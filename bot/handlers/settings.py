"""/settings — просмотр и изменение настроек.

Логика изменения времени и часового пояса переиспользует функции онбординга
(validate_morning / validate_evening / resolve_timezone, тексты шагов), чтобы не
дублировать код. Этот роутер регистрируется последним, поэтому здесь же лежит
fallback-обработчик «пустых»/устаревших callback'ов.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot.constants import (
    BTN_NO,
    BTN_SETTINGS_EVENING,
    BTN_SETTINGS_MORNING,
    BTN_SETTINGS_TIMEZONE,
    BTN_YES,
    COMMANDS,
    TEXTS,
)
from bot.database.models import User
from bot.database.repository import Repository
from bot.handlers.onboarding import (
    resolve_timezone,
    step1_text,
    step2_text,
    step3_text,
    validate_evening,
    validate_morning,
)
from bot.keyboards.builders import (
    confirm_city_kb,
    evening_time_kb,
    morning_time_kb,
    settings_kb,
    timezone_kb,
)
from bot.services.scheduler import SchedulerService
from bot.utils.validators import escape_md, format_timezone_display

router = Router(name="settings")


class SettingsStates(StatesGroup):
    """Состояния FSM настроек (зеркалят шаги онбординга)."""

    idle = State()
    morning_time = State()
    evening_time = State()
    timezone_select = State()
    timezone_city = State()
    timezone_confirm = State()


def _settings_card(user: User) -> str:
    """Собрать текст карточки настроек."""
    morning = user.morning_time.strftime("%H:%M") if user.morning_time else "—"
    evening = user.evening_time.strftime("%H:%M") if user.evening_time else "—"
    timezone = format_timezone_display(user.timezone) if user.timezone else "—"
    return escape_md(
        TEXTS["settings_card"].format(morning=morning, evening=evening, timezone=timezone)
    )


async def _show_card(message: Message, state: FSMContext, repo: Repository) -> None:
    """Перевести в idle и показать актуальную карточку настроек."""
    user = await repo.get_user(message.from_user.id)
    await state.set_state(SettingsStates.idle)
    await message.answer(_settings_card(user), reply_markup=settings_kb())


# --------------------------------------------------------------------------- #
#  Вход: /settings
# --------------------------------------------------------------------------- #

@router.message(Command("settings"))
async def cmd_settings(message: Message, state: FSMContext, repo: Repository) -> None:
    """Показать карточку настроек с reply-клавиатурой."""
    await state.clear()
    await _show_card(message, state, repo)


# --------------------------------------------------------------------------- #
#  Выбор пункта настроек (state idle)
# --------------------------------------------------------------------------- #

@router.message(SettingsStates.idle, F.text == BTN_SETTINGS_MORNING)
async def settings_pick_morning(message: Message, state: FSMContext) -> None:
    """«Изменить утреннее» — запросить новое время."""
    await state.set_state(SettingsStates.morning_time)
    await message.answer(step1_text(), reply_markup=morning_time_kb())


@router.message(SettingsStates.idle, F.text == BTN_SETTINGS_EVENING)
async def settings_pick_evening(message: Message, state: FSMContext) -> None:
    """«Изменить вечернее» — запросить новое время."""
    await state.set_state(SettingsStates.evening_time)
    await message.answer(step2_text(), reply_markup=evening_time_kb())


@router.message(SettingsStates.idle, F.text == BTN_SETTINGS_TIMEZONE)
async def settings_pick_timezone(message: Message, state: FSMContext) -> None:
    """«Изменить пояс» — запросить новый часовой пояс."""
    await state.set_state(SettingsStates.timezone_select)
    await message.answer(step3_text(), reply_markup=timezone_kb())


# --------------------------------------------------------------------------- #
#  Изменение утреннего времени
# --------------------------------------------------------------------------- #

@router.message(SettingsStates.morning_time, ~Command(*COMMANDS))
async def settings_morning(
    message: Message,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """Сохранить новое утреннее время и пересоздать jobs."""
    t, error = validate_morning(message.text or "")
    if error:
        await message.answer(escape_md(TEXTS[error]), reply_markup=morning_time_kb())
        return
    user = await repo.get_user(message.from_user.id)
    await repo.set_morning_time(user, t)
    scheduler.setup_user_jobs(user)
    logger.info("User {} changed morning time to {}", user.telegram_id, t)
    await _show_card(message, state, repo)


# --------------------------------------------------------------------------- #
#  Изменение вечернего времени
# --------------------------------------------------------------------------- #

@router.message(SettingsStates.evening_time, ~Command(*COMMANDS))
async def settings_evening(
    message: Message,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """Сохранить новое вечернее время и пересоздать jobs."""
    t, error = validate_evening(message.text or "")
    if error:
        await message.answer(escape_md(TEXTS[error]), reply_markup=evening_time_kb())
        return
    user = await repo.get_user(message.from_user.id)
    await repo.set_evening_time(user, t)
    scheduler.setup_user_jobs(user)
    logger.info("User {} changed evening time to {}", user.telegram_id, t)
    await _show_card(message, state, repo)


# --------------------------------------------------------------------------- #
#  Изменение часового пояса
# --------------------------------------------------------------------------- #

async def _apply_timezone(
    message: Message,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
    tz: str,
) -> None:
    """Сохранить новый пояс, пересоздать jobs и показать карточку."""
    user = await repo.get_user(message.from_user.id)
    await repo.set_timezone(user, tz)
    scheduler.setup_user_jobs(user)
    logger.info("User {} changed timezone to {}", user.telegram_id, tz)
    await _show_card(message, state, repo)


@router.message(
    SettingsStates.timezone_select,
    SettingsStates.timezone_city,
    ~Command(*COMMANDS),
)
async def settings_timezone(
    message: Message,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """Обработать ввод нового часового пояса (пресет/UTC/город)."""
    text = (message.text or "").strip()
    if not text:
        await message.answer(
            escape_md(TEXTS["step3_invalid_input"]), reply_markup=timezone_kb()
        )
        return

    kind, tz, city, utc = await resolve_timezone(text)
    if kind == "utc":
        await _apply_timezone(message, state, repo, scheduler, tz)
    elif kind == "utc_invalid":
        await message.answer(
            escape_md(TEXTS["step3_invalid_utc"]), reply_markup=timezone_kb()
        )
    elif kind == "city_found":
        await state.update_data(pending_tz=tz, pending_city=city, pending_utc=utc)
        await state.set_state(SettingsStates.timezone_confirm)
        await message.answer(
            escape_md(TEXTS["step3_confirm"].format(city=city, utc=utc)),
            reply_markup=confirm_city_kb(),
        )
    else:  # city_not_found
        await message.answer(
            escape_md(TEXTS["step3_city_not_found"]), reply_markup=timezone_kb()
        )


@router.message(SettingsStates.timezone_confirm, ~Command(*COMMANDS))
async def settings_timezone_confirm(
    message: Message,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """Подтверждение найденного города при смене пояса."""
    if message.text == BTN_YES:
        data = await state.get_data()
        tz = data.get("pending_tz")
        if not tz:
            await state.set_state(SettingsStates.timezone_select)
            await message.answer(step3_text(), reply_markup=timezone_kb())
            return
        await _apply_timezone(message, state, repo, scheduler, tz)
    elif message.text == BTN_NO:
        await state.set_state(SettingsStates.timezone_select)
        await message.answer(step3_text(), reply_markup=timezone_kb())
    else:
        data = await state.get_data()
        city = data.get("pending_city", "")
        utc = data.get("pending_utc", "")
        await message.answer(
            escape_md(TEXTS["step3_confirm"].format(city=city, utc=utc)),
            reply_markup=confirm_city_kb(),
        )


# --------------------------------------------------------------------------- #
#  Fallback для устаревших/пустых callback'ов (последний роутер)
# --------------------------------------------------------------------------- #

@router.callback_query()
async def fallback_callback(callback: CallbackQuery) -> None:
    """Тихо подтвердить нераспознанный callback (например, метку пагинации)."""
    await callback.answer()
