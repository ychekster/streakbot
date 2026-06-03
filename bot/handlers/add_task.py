"""FSM добавления задачи (/add).

Три ветки частоты: каждый день (A), конкретные дни (B), одноразовая (C).
Текстовый ввод исключает команды (`~Command(*COMMANDS)`), поэтому любая команда
во время добавления сбрасывает состояние и выполняется — как требует основной
сценарий. Нажатия inline-кнопок редактируют текущее сообщение, текстовый ввод
порождает новое сообщение.
"""

from __future__ import annotations

from datetime import date, datetime

import pytz
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot.constants import COMMANDS, MONTHS, TEXTS, WEEKDAYS
from bot.database.models import FrequencyType
from bot.database.repository import Repository
from bot.keyboards.builders import (
    REMOVE_KB,
    current_years,
    day_kb,
    days_kb,
    frequency_kb,
    has_reminder_kb,
    month_kb,
    reminder_time_back_kb,
    year_kb,
)
from bot.services.scheduler import SchedulerService
from bot.utils.validators import escape_md, parse_time

router = Router(name="add_task")

# Короткие подписи дней по коду и канонический порядок.
_DAY_SHORT: dict[str, str] = {code: short for code, short, _ in WEEKDAYS}
_DAY_ORDER: tuple[str, ...] = tuple(code for code, _, _ in WEEKDAYS)

_NAME_MAX_LEN = 100


class AddTaskStates(StatesGroup):
    """Состояния FSM добавления задачи."""

    name = State()
    frequency = State()
    days_select = State()
    year_select = State()
    month_select = State()
    day_select = State()
    has_reminder = State()
    reminder_time = State()


# --------------------------------------------------------------------------- #
#  Вспомогательные функции
# --------------------------------------------------------------------------- #

async def start_add_task(message: Message, state: FSMContext) -> None:
    """Запустить добавление задачи с Шага 1 (название). Точка входа из всех мест."""
    await state.clear()
    await state.set_state(AddTaskStates.name)
    await message.answer(escape_md(TEXTS["add_task_name"]), reply_markup=REMOVE_KB)


def _ordered_codes(codes: list[str]) -> list[str]:
    """Упорядочить коды дней недели в каноническом порядке (пн→вс)."""
    chosen = set(codes)
    return [code for code in _DAY_ORDER if code in chosen]


def _days_ru(codes: list[str]) -> str:
    """Собрать строку выбранных дней: 'ПН, СР, ПТ'."""
    return ", ".join(_DAY_SHORT[code] for code in _ordered_codes(codes))


def _build_date(data: dict) -> date:
    """Построить дату одноразовой задачи из сохранённых year/month/day."""
    return date(data["year"], data["month"], data["day"])


def _freq_summary(data: dict, reminder_str: str | None = None) -> str:
    """Собрать строку-сводку частоты для заголовков и подтверждения."""
    freq = data["frequency"]
    if freq == "daily":
        base = "Каждый день"
    elif freq == "specific_days":
        base = _days_ru(data.get("days", []))
    else:
        base = "Одноразовая · " + _build_date(data).strftime("%d.%m.%Y")
    if reminder_str:
        base += f" · Напоминание в {reminder_str}"
    return base


async def _user_today(repo: Repository, user_id: int) -> date:
    """Текущая дата в часовом поясе пользователя."""
    user = await repo.get_user(user_id)
    try:
        tz = pytz.timezone(user.timezone) if user and user.timezone else pytz.utc
    except Exception:  # noqa: BLE001
        tz = pytz.utc
    return datetime.now(tz).date()


def _is_one_time_today(data: dict, today: date) -> bool:
    """Является ли выбранная дата одноразовой задачи сегодняшней."""
    return data["frequency"] == "one_time" and _build_date(data) == today


# --------------------------------------------------------------------------- #
#  Точка входа: /add
# --------------------------------------------------------------------------- #

@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    """Команда /add — начать добавление задачи."""
    await start_add_task(message, state)


# --------------------------------------------------------------------------- #
#  Шаг 1 — название
# --------------------------------------------------------------------------- #

@router.message(AddTaskStates.name, ~Command(*COMMANDS))
async def add_name(message: Message, state: FSMContext, repo: Repository) -> None:
    """Принять название задачи и перейти к выбору частоты."""
    if not message.text:
        await message.answer(escape_md(TEXTS["add_task_name_invalid"]))
        return
    name = message.text.strip()
    if not name:
        await message.answer(escape_md(TEXTS["add_task_name_invalid"]))
        return
    if len(name) > _NAME_MAX_LEN:
        await message.answer(escape_md(TEXTS["add_task_name_too_long"]))
        return
    # Запрет дубликатов: задача с таким именем (без учёта регистра) уже есть.
    if await repo.task_name_exists(message.from_user.id, name):
        await message.answer(escape_md(TEXTS["add_task_name_duplicate"]))
        return

    await state.update_data(name=name)
    await state.set_state(AddTaskStates.frequency)
    text = f"{escape_md(name)}\n\n{escape_md(TEXTS['add_task_frequency'])}"
    await message.answer(text, reply_markup=frequency_kb())


# --------------------------------------------------------------------------- #
#  Шаг 2 — частота
# --------------------------------------------------------------------------- #

@router.callback_query(AddTaskStates.frequency, F.data.startswith("freq:"))
async def choose_frequency(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Развилка по выбранной частоте."""
    if callback.message is None:
        await callback.answer()
        return
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()
    name = data["name"]

    if choice == "daily":
        await state.update_data(frequency="daily")
        await state.set_state(AddTaskStates.has_reminder)
        text = (
            f"{escape_md(name)}\n{escape_md('Каждый день')}\n\n"
            f"{escape_md(TEXTS['add_task_has_reminder'])}"
        )
        await callback.message.edit_text(text, reply_markup=has_reminder_kb())
    elif choice == "specific":
        await state.update_data(frequency="specific_days", days=[])
        await state.set_state(AddTaskStates.days_select)
        text = f"{escape_md(name)}\n\n{escape_md(TEXTS['add_task_days'])}"
        await callback.message.edit_text(text, reply_markup=days_kb(set()))
    elif choice == "onetime":
        await state.update_data(frequency="one_time")
        await state.set_state(AddTaskStates.year_select)
        text = (
            f"{escape_md(name)}\n{escape_md('Одноразовая')}\n\n"
            f"{escape_md(TEXTS['add_task_year'])}"
        )
        await callback.message.edit_text(text, reply_markup=year_kb(current_years()))
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Ветка B — конкретные дни недели
# --------------------------------------------------------------------------- #

@router.callback_query(AddTaskStates.days_select, F.data.startswith("day:"))
async def toggle_day(callback: CallbackQuery, state: FSMContext) -> None:
    """Переключить выбор дня недели (галочка) и обновить клавиатуру."""
    if callback.message is None:
        await callback.answer()
        return
    code = callback.data.split(":", 1)[1]
    data = await state.get_data()
    days = set(data.get("days", []))
    if code in days:
        days.discard(code)
    else:
        days.add(code)
    await state.update_data(days=list(days))
    await callback.message.edit_reply_markup(reply_markup=days_kb(days))
    await callback.answer()


@router.callback_query(AddTaskStates.days_select, F.data == "days_done")
async def days_done(callback: CallbackQuery, state: FSMContext) -> None:
    """Подтвердить выбор дней; без выбранных дней — alert."""
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    days = list(data.get("days", []))
    if not days:
        await callback.answer(TEXTS["days_none_selected"], show_alert=True)
        return
    await state.set_state(AddTaskStates.has_reminder)
    text = (
        f"{escape_md(data['name'])}\n{escape_md(_days_ru(days))}\n\n"
        f"{escape_md(TEXTS['add_task_has_reminder'])}"
    )
    await callback.message.edit_text(text, reply_markup=has_reminder_kb())
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Ветка C — одноразовая (год → месяц → день)
# --------------------------------------------------------------------------- #

@router.callback_query(AddTaskStates.year_select, F.data.startswith("year:"))
async def choose_year(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Выбор года → переход к выбору месяца (прошедшие месяцы скрыты)."""
    if callback.message is None:
        await callback.answer()
        return
    year = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    today = await _user_today(repo, callback.from_user.id)
    min_month = today.month if year == today.year else 1
    await state.update_data(year=year)
    await state.set_state(AddTaskStates.month_select)
    text = (
        f"{escape_md(data['name'])}\n"
        f"{escape_md(f'Одноразовая · {year}')}\n\n{escape_md(TEXTS['add_task_month'])}"
    )
    await callback.message.edit_text(text, reply_markup=month_kb(year, min_month))
    await callback.answer()


@router.callback_query(AddTaskStates.month_select, F.data.startswith("month:"))
async def choose_month(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Выбор месяца → переход к выбору дня (прошедшие дни скрыты)."""
    if callback.message is None:
        await callback.answer()
        return
    month = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    year = data["year"]
    today = await _user_today(repo, callback.from_user.id)
    min_day = today.day if (year == today.year and month == today.month) else 1
    await state.update_data(month=month)
    await state.set_state(AddTaskStates.day_select)
    text = (
        f"{escape_md(data['name'])}\n"
        f"{escape_md(f'Одноразовая · {MONTHS[month]} {year}')}\n\n"
        f"{escape_md(TEXTS['add_task_day'])}"
    )
    await callback.message.edit_text(text, reply_markup=day_kb(year, month, min_day))
    await callback.answer()


@router.callback_query(AddTaskStates.day_select, F.data.startswith("dateday:"))
async def choose_day(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор дня → переход к вопросу о напоминании."""
    if callback.message is None:
        await callback.answer()
        return
    day = int(callback.data.split(":", 1)[1])
    await state.update_data(day=day)
    data = await state.get_data()
    await state.set_state(AddTaskStates.has_reminder)
    date_str = _build_date(data).strftime("%d.%m.%Y")
    text = (
        f"{escape_md(data['name'])}\n{escape_md(f'Одноразовая · {date_str}')}\n\n"
        f"{escape_md(TEXTS['add_task_has_reminder'])}"
    )
    await callback.message.edit_text(text, reply_markup=has_reminder_kb())
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Вопрос о напоминании
# --------------------------------------------------------------------------- #

@router.callback_query(AddTaskStates.has_reminder, F.data == "rem:no")
async def reminder_no(
    callback: CallbackQuery,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """Без напоминания — сохранить задачу и показать подтверждение, редактируя сообщение."""
    if callback.message is None:
        await callback.answer()
        return
    confirm = await _create_task(state, repo, scheduler, callback.from_user.id, None)
    # Редактируем сообщение на месте: текст подтверждения, инлайн-клавиатуру убираем.
    await callback.message.edit_text(confirm, reply_markup=None)
    await callback.answer()


@router.callback_query(AddTaskStates.has_reminder, F.data == "rem:yes")
async def reminder_yes(
    callback: CallbackQuery,
    state: FSMContext,
    repo: Repository,
) -> None:
    """С напоминанием — запросить время (убираем inline-кнопки)."""
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    today = await _user_today(repo, callback.from_user.id)
    key = (
        "add_task_reminder_time_today"
        if _is_one_time_today(data, today)
        else "add_task_reminder_time"
    )
    text = (
        f"{escape_md(data['name'])}\n{escape_md(_freq_summary(data))}\n\n"
        f"{escape_md(TEXTS[key])}"
    )
    await state.set_state(AddTaskStates.reminder_time)
    await callback.message.edit_text(text, reply_markup=reminder_time_back_kb())
    await callback.answer()


@router.message(AddTaskStates.reminder_time, ~Command(*COMMANDS))
async def reminder_time_input(
    message: Message,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """Принять время напоминания (текстом → новое сообщение-подтверждение)."""
    parsed = parse_time(message.text or "")
    if parsed is None:
        await message.answer(escape_md(TEXTS["invalid_time_format"]))
        return

    data = await state.get_data()
    today = await _user_today(repo, message.from_user.id)
    if _is_one_time_today(data, today):
        # Для сегодняшней одноразовой задачи время должно быть в будущем.
        tz = pytz.timezone((await repo.get_user(message.from_user.id)).timezone)
        now = datetime.now(tz)
        target = tz.localize(datetime.combine(today, parsed))
        if target <= now:
            await message.answer(escape_md(TEXTS["time_in_past"]))
            return

    confirm = await _create_task(state, repo, scheduler, message.from_user.id, parsed)
    await message.answer(confirm, reply_markup=REMOVE_KB)


# --------------------------------------------------------------------------- #
#  Навигация «‹ Назад» по шагам (доступна на каждом шаге после ввода названия,
#  кроме первого — выбора частоты). Нажатие редактирует сообщение и возвращает
#  к предыдущему шагу, позволяя изменить прошлый выбор. Выбранные ранее значения
#  (например, дни недели) сохраняются.
# --------------------------------------------------------------------------- #

@router.callback_query(
    StateFilter(AddTaskStates.days_select, AddTaskStates.year_select),
    F.data == "add_back_freq",
)
async def add_back_freq(callback: CallbackQuery, state: FSMContext) -> None:
    """«‹ Назад» с выбора дней/года — вернуть к выбору частоты."""
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    await state.set_state(AddTaskStates.frequency)
    text = f"{escape_md(data['name'])}\n\n{escape_md(TEXTS['add_task_frequency'])}"
    await callback.message.edit_text(text, reply_markup=frequency_kb())
    await callback.answer()


@router.callback_query(AddTaskStates.month_select, F.data == "add_back_to_year")
async def add_back_to_year(callback: CallbackQuery, state: FSMContext) -> None:
    """«‹ Назад» с выбора месяца — вернуть к выбору года."""
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    await state.set_state(AddTaskStates.year_select)
    text = (
        f"{escape_md(data['name'])}\n{escape_md('Одноразовая')}\n\n"
        f"{escape_md(TEXTS['add_task_year'])}"
    )
    await callback.message.edit_text(text, reply_markup=year_kb(current_years()))
    await callback.answer()


@router.callback_query(AddTaskStates.day_select, F.data == "add_back_to_month")
async def add_back_to_month(
    callback: CallbackQuery, state: FSMContext, repo: Repository
) -> None:
    """«‹ Назад» с выбора дня — вернуть к выбору месяца."""
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    year = data["year"]
    today = await _user_today(repo, callback.from_user.id)
    min_month = today.month if year == today.year else 1
    await state.set_state(AddTaskStates.month_select)
    text = (
        f"{escape_md(data['name'])}\n"
        f"{escape_md(f'Одноразовая · {year}')}\n\n{escape_md(TEXTS['add_task_month'])}"
    )
    await callback.message.edit_text(text, reply_markup=month_kb(year, min_month))
    await callback.answer()


@router.callback_query(AddTaskStates.has_reminder, F.data == "add_back_from_reminder")
async def add_back_from_reminder(
    callback: CallbackQuery, state: FSMContext, repo: Repository
) -> None:
    """«‹ Назад» с вопроса о напоминании — вернуть к предыдущему шагу по ветке частоты.

    daily → выбор частоты, specific_days → выбор дней (с сохранёнными галочками),
    one_time → выбор дня.
    """
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    name = data["name"]
    freq = data["frequency"]
    if freq == "daily":
        await state.set_state(AddTaskStates.frequency)
        text = f"{escape_md(name)}\n\n{escape_md(TEXTS['add_task_frequency'])}"
        await callback.message.edit_text(text, reply_markup=frequency_kb())
    elif freq == "specific_days":
        await state.set_state(AddTaskStates.days_select)
        days = set(data.get("days", []))
        text = f"{escape_md(name)}\n\n{escape_md(TEXTS['add_task_days'])}"
        await callback.message.edit_text(text, reply_markup=days_kb(days))
    else:  # one_time
        await state.set_state(AddTaskStates.day_select)
        year, month = data["year"], data["month"]
        today = await _user_today(repo, callback.from_user.id)
        min_day = today.day if (year == today.year and month == today.month) else 1
        text = (
            f"{escape_md(name)}\n"
            f"{escape_md(f'Одноразовая · {MONTHS[month]} {year}')}\n\n"
            f"{escape_md(TEXTS['add_task_day'])}"
        )
        await callback.message.edit_text(text, reply_markup=day_kb(year, month, min_day))
    await callback.answer()


@router.callback_query(AddTaskStates.reminder_time, F.data == "add_back_to_reminder")
async def add_back_to_reminder(callback: CallbackQuery, state: FSMContext) -> None:
    """«‹ Назад» с ввода времени напоминания — вернуть к вопросу о напоминании."""
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    await state.set_state(AddTaskStates.has_reminder)
    text = (
        f"{escape_md(data['name'])}\n{escape_md(_freq_summary(data))}\n\n"
        f"{escape_md(TEXTS['add_task_has_reminder'])}"
    )
    await callback.message.edit_text(text, reply_markup=has_reminder_kb())
    await callback.answer()


async def _create_task(
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
    user_id: int,
    reminder_time,
) -> str:
    """Создать задачу в БД, поставить job напоминания и вернуть текст подтверждения."""
    data = await state.get_data()
    freq = FrequencyType(data["frequency"])
    days_str = (
        ",".join(_ordered_codes(data.get("days", [])))
        if data["frequency"] == "specific_days"
        else None
    )
    one_time_date = _build_date(data) if data["frequency"] == "one_time" else None

    task = await repo.create_task(
        user_id=user_id,
        name=data["name"],
        frequency_type=freq,
        days=days_str,
        one_time_date=one_time_date,
        reminder_time=reminder_time,
    )
    if reminder_time is not None:
        user = await repo.get_user(user_id)
        scheduler.add_task_reminder_job(user, task)
    logger.info("User {} created task '{}' (id={})", user_id, task.name, task.id)

    reminder_str = reminder_time.strftime("%H:%M") if reminder_time else None
    summary = _freq_summary(data, reminder_str)
    await state.clear()
    return (
        f"{escape_md(data['name'])}\n{escape_md(summary)}\n\n"
        f"{escape_md(TEXTS['task_added'])}"
    )


