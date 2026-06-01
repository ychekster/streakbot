"""FSM-машина регистрации (онбординг).

Поведение команд во время онбординга отличается от основного режима: любая
команда, кроме /start и /cancel, считается некорректным вводом и приводит к
повторной отправке текущего шага. Поэтому обработчики онбординга перехватывают
всё, кроме `/start` (через `~CommandStart()`); `/cancel` уже перехвачен роутером
cancel, зарегистрированным раньше.

Часть функций (валидация времени, разбор часового пояса) вынесена сюда как
переиспользуемая логика — её применяет и /settings.
"""

from __future__ import annotations

from datetime import time

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from loguru import logger

from bot.constants import (
    BTN_ADD_TASK,
    BTN_NO,
    BTN_SKIP,
    BTN_START,
    BTN_YES,
    TEXTS,
    TIMEZONE_PRESETS,
)
from bot.database.repository import Repository
from bot.keyboards.builders import (
    REMOVE_KB,
    confirm_city_kb,
    evening_time_kb,
    morning_time_kb,
    onboarding_done_kb,
    start_kb,
    timezone_kb,
)
from bot.services.geo import find_timezone
from bot.services.scheduler import SchedulerService
from bot.utils.validators import (
    escape_md,
    is_evening_time_valid,
    is_morning_time_valid,
    looks_like_utc,
    parse_time,
    parse_utc_offset,
    utc_label,
)

router = Router(name="onboarding")

# Быстрый поиск UTC-строки по подписи кнопки-пресета.
_PRESET_LOOKUP: dict[str, str] = {label: utc for label, utc in TIMEZONE_PRESETS}


class OnboardingStates(StatesGroup):
    """Состояния FSM регистрации."""

    waiting_start = State()      # показано приветствие, ждём «🚀 Поехали»
    morning_time = State()       # ждём утреннее время (04:00–12:00)
    evening_time = State()       # ждём вечернее время (16:00–00:00)
    timezone_select = State()    # ждём пресет/город/UTC
    timezone_city = State()      # ждём текстовый ввод города/UTC
    timezone_confirm = State()   # ждём подтверждение найденного города


# --------------------------------------------------------------------------- #
#  Тексты шагов (с жирным заголовком) — переиспользуются настройками
# --------------------------------------------------------------------------- #

def step1_text() -> str:
    """Сообщение Шага 1 (утреннее время)."""
    return f"☀️ *{escape_md(TEXTS['step1_header'])}*\n\n{escape_md(TEXTS['step1_morning'])}"


def step2_text() -> str:
    """Сообщение Шага 2 (вечернее время)."""
    return f"🌙 *{escape_md(TEXTS['step2_header'])}*\n\n{escape_md(TEXTS['step2_evening'])}"


def step3_text() -> str:
    """Сообщение Шага 3 (часовой пояс)."""
    return f"🌍 *{escape_md(TEXTS['step3_header'])}*\n\n{escape_md(TEXTS['step3_timezone'])}"


# --------------------------------------------------------------------------- #
#  Переиспользуемая валидация (онбординг + настройки)
# --------------------------------------------------------------------------- #

def validate_morning(text: str) -> tuple[time | None, str | None]:
    """Разобрать и проверить утреннее время. Вернуть (time, None) или (None, ключ_ошибки)."""
    t = parse_time(text)
    if t is None:
        return None, "step1_invalid"
    if not is_morning_time_valid(t):
        return None, "step1_out_of_range"
    return t, None


def validate_evening(text: str) -> tuple[time | None, str | None]:
    """Разобрать и проверить вечернее время. Вернуть (time, None) или (None, ключ_ошибки)."""
    t = parse_time(text)
    if t is None:
        return None, "step2_invalid"
    if not is_evening_time_valid(t):
        return None, "step2_out_of_range"
    return t, None


async def resolve_timezone(text: str) -> tuple[str, str | None, str | None, str | None]:
    """Определить часовой пояс по вводу.

    Возвращает (kind, tz, city, utc):
        kind == 'utc'          — готовый пояс (пресет или UTC±N), tz заполнен;
        kind == 'utc_invalid'  — формат UTC верный, но вне диапазона;
        kind == 'city_found'   — город найден, нужно подтверждение (tz, city, utc);
        kind == 'city_not_found' — город не найден.
    """
    value = text.strip()

    # Пресет-кнопка ("Москва UTC+3").
    if value in _PRESET_LOOKUP:
        tz = parse_utc_offset(_PRESET_LOOKUP[value])
        return "utc", tz, None, None

    # Явный ввод в формате UTC±N.
    if looks_like_utc(value):
        tz = parse_utc_offset(value)
        if tz is None:
            return "utc_invalid", None, None, None
        return "utc", tz, None, None

    # Иначе — считаем вводом города.
    tz, display = await find_timezone(value)
    if tz is None:
        return "city_not_found", None, None, None
    city = display.split(" (")[0] if display else value
    return "city_found", tz, city, utc_label(tz)


async def finalize_registration(
    message: Message,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
    tz: str,
) -> None:
    """Завершить онбординг: сохранить пояс, включить jobs, показать финал."""
    user = await repo.get_user(message.from_user.id)
    await repo.set_timezone(user, tz)
    await repo.complete_registration(user)
    scheduler.setup_user_jobs(user)
    await state.clear()
    await message.answer(escape_md(TEXTS["onboarding_done"]), reply_markup=onboarding_done_kb())
    logger.info("User {} completed onboarding (timezone={})", user.telegram_id, tz)


# --------------------------------------------------------------------------- #
#  Шаг 0 → 1 : ожидание «Поехали»
# --------------------------------------------------------------------------- #

@router.message(OnboardingStates.waiting_start, ~CommandStart())
async def onb_waiting_start(message: Message, state: FSMContext) -> None:
    """Ждём нажатие «🚀 Поехали»; любой другой ввод — напоминание."""
    if message.text == BTN_START:
        await state.set_state(OnboardingStates.morning_time)
        await message.answer(step1_text(), reply_markup=morning_time_kb())
    else:
        await message.answer(
            escape_md(TEXTS["waiting_start_reminder"]), reply_markup=start_kb()
        )


# --------------------------------------------------------------------------- #
#  Шаг 1 : утреннее время
# --------------------------------------------------------------------------- #

@router.message(OnboardingStates.morning_time, ~CommandStart())
async def onb_morning(message: Message, state: FSMContext, repo: Repository) -> None:
    """Принять утреннее время и перейти к вечернему."""
    t, error = validate_morning(message.text or "")
    if error:
        await message.answer(escape_md(TEXTS[error]), reply_markup=morning_time_kb())
        return
    user = await repo.get_user(message.from_user.id)
    await repo.set_morning_time(user, t)
    await state.set_state(OnboardingStates.evening_time)
    await message.answer(step2_text(), reply_markup=evening_time_kb())


# --------------------------------------------------------------------------- #
#  Шаг 2 : вечернее время
# --------------------------------------------------------------------------- #

@router.message(OnboardingStates.evening_time, ~CommandStart())
async def onb_evening(message: Message, state: FSMContext, repo: Repository) -> None:
    """Принять вечернее время и перейти к выбору часового пояса."""
    t, error = validate_evening(message.text or "")
    if error:
        await message.answer(escape_md(TEXTS[error]), reply_markup=evening_time_kb())
        return
    user = await repo.get_user(message.from_user.id)
    await repo.set_evening_time(user, t)
    await state.set_state(OnboardingStates.timezone_select)
    await message.answer(step3_text(), reply_markup=timezone_kb())


# --------------------------------------------------------------------------- #
#  Шаг 3 : часовой пояс (выбор/город/UTC)
# --------------------------------------------------------------------------- #

@router.message(
    StateFilter(OnboardingStates.timezone_select, OnboardingStates.timezone_city),
    ~CommandStart(),
)
async def onb_timezone(
    message: Message,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """Обработать ввод часового пояса: пресет, UTC или город."""
    text = (message.text or "").strip()
    if not text:
        await message.answer(
            escape_md(TEXTS["step3_invalid_input"]), reply_markup=timezone_kb()
        )
        return

    kind, tz, city, utc = await resolve_timezone(text)
    if kind == "utc":
        await finalize_registration(message, state, repo, scheduler, tz)
    elif kind == "utc_invalid":
        await message.answer(
            escape_md(TEXTS["step3_invalid_utc"]), reply_markup=timezone_kb()
        )
    elif kind == "city_found":
        await state.update_data(pending_tz=tz, pending_city=city, pending_utc=utc)
        await state.set_state(OnboardingStates.timezone_confirm)
        await message.answer(
            escape_md(TEXTS["step3_confirm"].format(city=city, utc=utc)),
            reply_markup=confirm_city_kb(),
        )
    else:  # city_not_found
        await message.answer(
            escape_md(TEXTS["step3_city_not_found"]), reply_markup=timezone_kb()
        )


@router.message(OnboardingStates.timezone_confirm, ~CommandStart())
async def onb_timezone_confirm(
    message: Message,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """Подтверждение найденного города: Да — завершаем, Нет — назад к выбору."""
    if message.text == BTN_YES:
        data = await state.get_data()
        tz = data.get("pending_tz")
        if not tz:
            await state.set_state(OnboardingStates.timezone_select)
            await message.answer(step3_text(), reply_markup=timezone_kb())
            return
        await finalize_registration(message, state, repo, scheduler, tz)
    elif message.text == BTN_NO:
        await state.set_state(OnboardingStates.timezone_select)
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
#  Финал онбординга: выбор «Добавить задачу / Пропустить»
#  (состояние уже очищено, пользователь зарегистрирован)
# --------------------------------------------------------------------------- #

@router.message(StateFilter(None), F.text == BTN_ADD_TASK)
async def onb_finish_add_task(message: Message, state: FSMContext) -> None:
    """«+ Добавить задачу» после регистрации — запустить добавление."""
    from bot.handlers.add_task import start_add_task

    await start_add_task(message, state)


@router.message(StateFilter(None), F.text == BTN_SKIP)
async def onb_finish_skip(message: Message) -> None:
    """«Пропустить» после регистрации — показать главное меню."""
    await message.answer(escape_md(TEXTS["main_menu"]), reply_markup=REMOVE_KB)
