"""/edit — редактирование существующей задачи (название, частота, напоминание).

Список выбора задачи идентичен /delete. После выбора показывается карточка
задачи (редактированием одного сообщения) с пунктами «Название», «Частота»,
«Напоминание» и «‹ Назад» (к списку).

- Название и время напоминания вводятся текстом — бот присылает НОВОЕ сообщение
  с запросом; после успешного ввода — сообщение с кнопкой «‹ Вернуться к задаче»,
  которая редактируется обратно в карточку.
- Частота и меню уже существующего напоминания меняются редактированием
  карточки; на каждом таком экране есть «‹ Назад» к предыдущему состоянию.

Частота при редактировании может быть только `daily` или `specific_days`.
Стрик и логи задачи при редактировании не трогаются.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot.constants import COMMANDS, DELETE_PAGE_SIZE, TEXTS, WEEKDAYS
from bot.database.models import FrequencyType, Task
from bot.database.repository import Repository
from bot.keyboards.builders import (
    REMOVE_KB,
    edit_card_kb,
    edit_days_kb,
    edit_freq_kb,
    edit_list_kb,
    edit_reminder_menu_kb,
    edit_return_kb,
)
from bot.services.scheduler import SchedulerService
from bot.utils.validators import escape_md, format_days_short, parse_time

router = Router(name="edit_task")

# Канонический порядок кодов дней недели (пн → вс).
_DAY_ORDER: tuple[str, ...] = tuple(code for code, _, _ in WEEKDAYS)

_NAME_MAX_LEN = 100


class EditTaskStates(StatesGroup):
    """Состояния FSM редактирования задачи."""

    select = State()         # выбор задачи из списка
    card = State()           # карточка задачи (меню полей)
    name = State()           # ожидание нового названия (текст)
    freq_choice = State()    # выбор новой частоты
    freq_days = State()      # выбор дней недели
    reminder_menu = State()  # меню существующего напоминания (Изменить/Убрать)
    reminder_time = State()  # ожидание нового времени напоминания (текст)


# --------------------------------------------------------------------------- #
#  Вспомогательные функции
# --------------------------------------------------------------------------- #

def _ordered_codes(codes: list[str]) -> list[str]:
    """Упорядочить коды дней недели в каноническом порядке (пн→вс)."""
    chosen = set(codes)
    return [code for code in _DAY_ORDER if code in chosen]


def _paginate(tasks: list[Task], page: int) -> tuple[list[tuple[int, str]], int]:
    """Вернуть ((id, name) текущей страницы, всего страниц) — как в /delete."""
    total_pages = max(1, (len(tasks) + DELETE_PAGE_SIZE - 1) // DELETE_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = tasks[page * DELETE_PAGE_SIZE : (page + 1) * DELETE_PAGE_SIZE]
    return [(task.id, task.name) for task in chunk], total_pages


def _freq_str(task: Task) -> str:
    """Человекочитаемая частота задачи для карточки."""
    if task.frequency_type == FrequencyType.daily:
        return "Каждый день"
    if task.frequency_type == FrequencyType.specific_days:
        return format_days_short(task.days or "")
    return "Одноразовая · " + task.one_time_date.strftime("%d.%m.%Y")


def _card_text(task: Task) -> str:
    """Текст карточки задачи: название, частота, напоминание + вопрос «что изменить»."""
    lines = [escape_md(task.name), "", f"📅 {escape_md(_freq_str(task))}"]
    if task.reminder_time is not None:
        lines.append(f"⏰ {escape_md(task.reminder_time.strftime('%H:%M'))}")
    else:
        lines.append(f"⏰ {escape_md(TEXTS['edit_reminder_none'])}")
    lines.append("")
    lines.append(escape_md(TEXTS["edit_card_prompt"]))
    return "\n".join(lines)


async def _get_edit_task(
    state: FSMContext, repo: Repository, user_id: int
) -> Task | None:
    """Вернуть редактируемую задачу по id из FSM-данных (или None, если исчезла)."""
    data = await state.get_data()
    task_id = data.get("edit_task_id")
    if task_id is None:
        return None
    return await repo.get_active_task(task_id, user_id)


async def _render_card(
    callback: CallbackQuery, state: FSMContext, repo: Repository
) -> None:
    """Отрисовать карточку задачи в текущем сообщении (редактированием)."""
    task = await _get_edit_task(state, repo, callback.from_user.id)
    if task is None:
        await state.clear()
        await callback.message.edit_text(escape_md(TEXTS["task_not_found"]))
        return
    await state.set_state(EditTaskStates.card)
    await callback.message.edit_text(_card_text(task), reply_markup=edit_card_kb())


# --------------------------------------------------------------------------- #
#  Вход: /edit и выбор задачи
# --------------------------------------------------------------------------- #

@router.message(Command("edit"))
async def cmd_edit(message: Message, state: FSMContext, repo: Repository) -> None:
    """Показать список задач для редактирования."""
    await state.clear()
    tasks = await repo.get_active_tasks(message.from_user.id)
    if not tasks:
        await message.answer(escape_md(TEXTS["no_tasks_yet"]), reply_markup=REMOVE_KB)
        return
    await state.set_state(EditTaskStates.select)
    await state.update_data(page=0)
    page_tasks, total_pages = _paginate(tasks, 0)
    await message.answer(
        escape_md(TEXTS["edit_select"]),
        reply_markup=edit_list_kb(page_tasks, 0, total_pages),
    )


@router.callback_query(EditTaskStates.select, F.data.startswith("edit_page:"))
async def edit_paginate(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Перелистнуть страницу списка задач."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    tasks = await repo.get_active_tasks(callback.from_user.id)
    if not tasks:
        await callback.message.edit_text(escape_md(TEXTS["no_tasks_yet"]))
        await state.clear()
        await callback.answer()
        return
    total_pages = max(1, (len(tasks) + DELETE_PAGE_SIZE - 1) // DELETE_PAGE_SIZE)
    if page < 0:
        await callback.answer(TEXTS["pagination_first"], show_alert=True)
        return
    if page >= total_pages:
        await callback.answer(TEXTS["pagination_last"], show_alert=True)
        return
    page_tasks, total_pages = _paginate(tasks, page)
    await state.update_data(page=page)
    await callback.message.edit_reply_markup(
        reply_markup=edit_list_kb(page_tasks, page, total_pages)
    )
    await callback.answer()


@router.callback_query(EditTaskStates.select, F.data.startswith("edit_select:"))
async def edit_pick(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Выбрать задачу — показать карточку редактирования."""
    if callback.message is None:
        await callback.answer()
        return
    task_id = int(callback.data.split(":", 1)[1])
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    await state.update_data(edit_task_id=task_id)
    await state.set_state(EditTaskStates.card)
    await callback.message.edit_text(_card_text(task), reply_markup=edit_card_kb())
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Навигация карточки: назад к задаче / назад к списку
# --------------------------------------------------------------------------- #

@router.callback_query(F.data == "edit_to_card")
async def edit_to_card(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«‹ Вернуться к задаче» / «‹ Назад» — отрисовать карточку в текущем сообщении."""
    if callback.message is None:
        await callback.answer()
        return
    await _render_card(callback, state, repo)
    await callback.answer()


@router.callback_query(EditTaskStates.card, F.data == "edit_back_list")
async def edit_back_list(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«‹ Назад» из карточки — вернуть список выбора задач."""
    if callback.message is None:
        await callback.answer()
        return
    tasks = await repo.get_active_tasks(callback.from_user.id)
    if not tasks:
        await state.clear()
        await callback.message.edit_text(escape_md(TEXTS["no_tasks_yet"]))
        await callback.answer()
        return
    data = await state.get_data()
    page = data.get("page", 0)
    page_tasks, total_pages = _paginate(tasks, page)
    await state.set_state(EditTaskStates.select)
    await callback.message.edit_text(
        escape_md(TEXTS["edit_select"]),
        reply_markup=edit_list_kb(page_tasks, page, total_pages),
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Редактирование названия (текстовый ввод → новое сообщение)
# --------------------------------------------------------------------------- #

@router.callback_query(EditTaskStates.card, F.data == "edit_field:name")
async def edit_name_start(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Название» — прислать новое сообщение с запросом названия."""
    if callback.message is None:
        await callback.answer()
        return
    task = await _get_edit_task(state, repo, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    await state.set_state(EditTaskStates.name)
    await callback.message.answer(escape_md(TEXTS["edit_name_prompt"]))
    await callback.answer()


@router.message(EditTaskStates.name, ~Command(*COMMANDS))
async def edit_name_input(message: Message, state: FSMContext, repo: Repository) -> None:
    """Принять новое название: проверить и сохранить либо сообщить об ошибке."""
    if not message.text or not message.text.strip():
        await message.answer(escape_md(TEXTS["add_task_name_invalid"]))
        return
    name = message.text.strip()
    if len(name) > _NAME_MAX_LEN:
        await message.answer(escape_md(TEXTS["add_task_name_too_long"]))
        return
    task = await _get_edit_task(state, repo, message.from_user.id)
    if task is None:
        await state.clear()
        await message.answer(escape_md(TEXTS["task_not_found"]), reply_markup=REMOVE_KB)
        return
    if await repo.task_name_exists(message.from_user.id, name, exclude_task_id=task.id):
        await message.answer(escape_md(TEXTS["add_task_name_duplicate"]))
        return
    await repo.update_task_name(task, name)
    logger.info("User {} renamed task {} to '{}'", message.from_user.id, task.id, name)
    await state.set_state(EditTaskStates.card)
    await message.answer(escape_md(TEXTS["edit_name_done"]), reply_markup=edit_return_kb())


# --------------------------------------------------------------------------- #
#  Редактирование частоты (редактирование карточки)
# --------------------------------------------------------------------------- #

@router.callback_query(EditTaskStates.card, F.data == "edit_field:freq")
async def edit_freq_start(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Частота» — показать выбор частоты (Каждый день / В конкретные дни / Назад)."""
    if callback.message is None:
        await callback.answer()
        return
    task = await _get_edit_task(state, repo, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    await state.set_state(EditTaskStates.freq_choice)
    await callback.message.edit_text(
        escape_md(TEXTS["add_task_frequency"]), reply_markup=edit_freq_kb()
    )
    await callback.answer()


@router.callback_query(EditTaskStates.freq_choice, F.data == "edit_freq_set:daily")
async def edit_freq_daily(
    callback: CallbackQuery,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """«Каждый день» — применить и вернуться к карточке."""
    if callback.message is None:
        await callback.answer()
        return
    task = await _get_edit_task(state, repo, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    await repo.update_task_frequency(task, FrequencyType.daily, None)
    if task.reminder_time is not None:
        user = await repo.get_user(callback.from_user.id)
        scheduler.add_task_reminder_job(user, task)
    logger.info("User {} set task {} frequency=daily", callback.from_user.id, task.id)
    await _render_card(callback, state, repo)
    await callback.answer()


@router.callback_query(EditTaskStates.freq_choice, F.data == "edit_freq_set:specific")
async def edit_freq_specific(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«В конкретные дни» — показать выбор дней недели (с текущими отмеченными)."""
    if callback.message is None:
        await callback.answer()
        return
    task = await _get_edit_task(state, repo, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    if task.frequency_type == FrequencyType.specific_days:
        current = {code for code in (task.days or "").split(",") if code}
    else:
        current = set()
    await state.update_data(edit_days=list(current))
    await state.set_state(EditTaskStates.freq_days)
    await callback.message.edit_text(
        escape_md(TEXTS["add_task_days"]), reply_markup=edit_days_kb(current)
    )
    await callback.answer()


@router.callback_query(EditTaskStates.freq_days, F.data.startswith("eday:"))
async def edit_toggle_day(callback: CallbackQuery, state: FSMContext) -> None:
    """Переключить выбор дня недели (галочка)."""
    if callback.message is None:
        await callback.answer()
        return
    code = callback.data.split(":", 1)[1]
    data = await state.get_data()
    days = set(data.get("edit_days", []))
    if code in days:
        days.discard(code)
    else:
        days.add(code)
    await state.update_data(edit_days=list(days))
    await callback.message.edit_reply_markup(reply_markup=edit_days_kb(days))
    await callback.answer()


@router.callback_query(EditTaskStates.freq_days, F.data == "edit_freq_back")
async def edit_days_back(callback: CallbackQuery, state: FSMContext) -> None:
    """«‹ Назад» с выбора дней — вернуть выбор частоты."""
    if callback.message is None:
        await callback.answer()
        return
    await state.set_state(EditTaskStates.freq_choice)
    await callback.message.edit_text(
        escape_md(TEXTS["add_task_frequency"]), reply_markup=edit_freq_kb()
    )
    await callback.answer()


@router.callback_query(EditTaskStates.freq_days, F.data == "edays_done")
async def edit_days_done(
    callback: CallbackQuery,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """«Готово» — применить выбранные дни и вернуться к карточке (без выбора — alert)."""
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    days = list(data.get("edit_days", []))
    if not days:
        await callback.answer(TEXTS["days_none_selected"], show_alert=True)
        return
    task = await _get_edit_task(state, repo, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    days_str = ",".join(_ordered_codes(days))
    await repo.update_task_frequency(task, FrequencyType.specific_days, days_str)
    if task.reminder_time is not None:
        user = await repo.get_user(callback.from_user.id)
        scheduler.add_task_reminder_job(user, task)
    logger.info(
        "User {} set task {} frequency=specific_days ({})",
        callback.from_user.id, task.id, days_str,
    )
    await _render_card(callback, state, repo)
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Редактирование напоминания
# --------------------------------------------------------------------------- #

@router.callback_query(EditTaskStates.card, F.data == "edit_field:rem")
async def edit_reminder_start(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Напоминание»: если его нет — запросить время новым сообщением; иначе — меню."""
    if callback.message is None:
        await callback.answer()
        return
    task = await _get_edit_task(state, repo, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    if task.reminder_time is None:
        await state.set_state(EditTaskStates.reminder_time)
        await callback.message.answer(escape_md(TEXTS["edit_reminder_prompt"]))
    else:
        await state.set_state(EditTaskStates.reminder_menu)
        text = escape_md(
            TEXTS["edit_reminder_menu"].format(time=task.reminder_time.strftime("%H:%M"))
        )
        await callback.message.edit_text(text, reply_markup=edit_reminder_menu_kb())
    await callback.answer()


@router.callback_query(EditTaskStates.reminder_menu, F.data == "edit_rem_change")
async def edit_reminder_change(callback: CallbackQuery, state: FSMContext) -> None:
    """«Изменить» — запросить новое время напоминания новым сообщением."""
    if callback.message is None:
        await callback.answer()
        return
    await state.set_state(EditTaskStates.reminder_time)
    await callback.message.answer(escape_md(TEXTS["edit_reminder_prompt"]))
    await callback.answer()


@router.callback_query(EditTaskStates.reminder_menu, F.data == "edit_rem_remove")
async def edit_reminder_remove(
    callback: CallbackQuery,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """«Убрать» — снять напоминание и job, вернуться к карточке."""
    if callback.message is None:
        await callback.answer()
        return
    task = await _get_edit_task(state, repo, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    await repo.update_task_reminder(task, None)
    scheduler.remove_task_reminder_job(task.id)
    logger.info("User {} removed reminder from task {}", callback.from_user.id, task.id)
    await _render_card(callback, state, repo)
    await callback.answer(TEXTS["edit_reminder_removed"])


@router.message(EditTaskStates.reminder_time, ~Command(*COMMANDS))
async def edit_reminder_input(
    message: Message,
    state: FSMContext,
    repo: Repository,
    scheduler: SchedulerService,
) -> None:
    """Принять новое время напоминания: проверить, сохранить и пересоздать job."""
    parsed = parse_time(message.text or "")
    if parsed is None:
        await message.answer(escape_md(TEXTS["invalid_time_format"]))
        return
    task = await _get_edit_task(state, repo, message.from_user.id)
    if task is None:
        await state.clear()
        await message.answer(escape_md(TEXTS["task_not_found"]), reply_markup=REMOVE_KB)
        return
    await repo.update_task_reminder(task, parsed)
    user = await repo.get_user(message.from_user.id)
    scheduler.add_task_reminder_job(user, task)
    logger.info("User {} set reminder {} on task {}", message.from_user.id, parsed, task.id)
    await state.set_state(EditTaskStates.card)
    await message.answer(
        escape_md(TEXTS["edit_reminder_done"]), reply_markup=edit_return_kb()
    )
