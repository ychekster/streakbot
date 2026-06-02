"""/today и /done — задачи на сегодня с инлайн-навигацией.

`/today` присылает инлайн-список НЕвыполненных задач (по кнопке на задачу,
сетка по 2 в ряд, пагинация при >8). В тексте сообщения отмеченные задачи
остаются отдельным зачёркнутым блоком, но кнопок для них нет. Нажатие на задачу
открывает карточку с кнопками «✅ Выполнено» и «‹ Назад»; отметка возвращает
сообщение к списку.

`/done` аналогичен `/today`, но показывает только выполненные сегодня задачи
(не зачёркнуты). В карточке — «Отменить выполнение» (возвращает задачу в
активные) и «‹ Назад».

`build_morning_digest` переиспользуется планировщиком для утреннего дайджеста
(список на сегодня + блок просроченных задач).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytz
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from loguru import logger

from bot.constants import FREQ_LABELS, OVERDUE_PAGE_SIZE, TEXTS
from bot.database.models import FrequencyType, Task, TaskLog, TaskStatus, User
from bot.database.repository import Repository
from bot.keyboards.builders import (
    REMOVE_KB,
    done_card_kb,
    done_list_kb,
    evening_mark_kb,
    morning_overdue_kb,
    overdue_expired_kb,
    select_confirm_kb,
    task_select_kb,
    today_card_kb,
    today_list_kb,
)
from bot.services.streak import get_current_streak, get_max_streak
from bot.utils.validators import escape_md, format_days_short

router = Router(name="today")


def _freq_line(task: Task) -> str:
    """Человекочитаемое описание частоты задачи для карточки."""
    if task.frequency_type == FrequencyType.daily:
        return FREQ_LABELS["daily"].capitalize()
    if task.frequency_type == FrequencyType.specific_days:
        return format_days_short(task.days or "")
    return "Одноразовая · " + task.one_time_date.strftime("%d.%m.%Y")


def _user_tz(user: User) -> pytz.BaseTzInfo:
    """Таймзона пользователя (UTC как фолбэк)."""
    try:
        return pytz.timezone(user.timezone) if user.timezone else pytz.utc
    except Exception:  # noqa: BLE001
        return pytz.utc


async def _today_tasks(
    repo: Repository,
    user: User,
    today,
) -> tuple[list[tuple[Task, TaskLog]], list[tuple[Task, TaskLog]]]:
    """Вернуть (активные, отмеченные) задачи на сегодня, создав pending-логи.

    Активные — со статусом pending, отмеченные — done/skipped.
    """
    tasks = await repo.get_tasks_due_on(user.telegram_id, today)
    active: list[tuple[Task, TaskLog]] = []
    marked: list[tuple[Task, TaskLog]] = []
    for task in tasks:
        log = await repo.get_or_create_log(task.id, user.telegram_id, today)
        if log.status == TaskStatus.pending:
            active.append((task, log))
        else:
            marked.append((task, log))
    return active, marked


def _render_task_lines(
    active: list[tuple[Task, TaskLog]],
    marked: list[tuple[Task, TaskLog]],
) -> list[str]:
    """Строки списка /today: активные, пустая строка, отмеченные (зачёркнуты).

    Нумерация сквозная и совпадает с нумерацией кнопок (кнопки — только активные).
    """
    lines: list[str] = []
    number = 1
    for task, _ in active:
        lines.append(f"{number}\\. {escape_md(task.name)}")
        number += 1
    if marked:
        if active:
            lines.append("")  # пропуск строки между блоками
        for task, _ in marked:
            lines.append(f"~{number}\\. {escape_md(task.name)}~")  # зачёркнуто
            number += 1
    return lines


async def _today_list_message(
    repo: Repository,
    user: User,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Собрать сообщение списка /today (текст + клавиатура только из активных задач)."""
    today = datetime.now(_user_tz(user)).date()
    active, marked = await _today_tasks(repo, user, today)
    if not active and not marked:
        return escape_md(TEXTS["today_no_tasks"]), None
    lines = [escape_md(TEXTS["today_header"]), ""]
    lines.extend(_render_task_lines(active, marked))
    keyboard = (
        today_list_kb([(task.id, task.name) for task, _ in active]) if active else None
    )
    return "\n".join(lines), keyboard


async def _done_list_message(
    repo: Repository,
    user: User,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Собрать сообщение списка /done (только выполненные сегодня задачи)."""
    today = datetime.now(_user_tz(user)).date()
    _, marked = await _today_tasks(repo, user, today)
    completed = [task for task, log in marked if log.status == TaskStatus.done]
    if not completed:
        return escape_md(TEXTS["done_no_tasks"]), None
    lines = [escape_md(TEXTS["done_header"]), ""]
    lines.extend(
        f"{i + 1}\\. {escape_md(task.name)}" for i, task in enumerate(completed)
    )
    keyboard = done_list_kb([(task.id, task.name) for task in completed])
    return "\n".join(lines), keyboard


async def _tasks_for_date_not_done(
    repo: Repository,
    user: User,
    target_date,
) -> list[Task]:
    """Активные задачи с логом за указанную дату и статусом не `done`.

    Включает все типы задач. Используется для просроченных (вчера) в утреннем
    дайджесте и для невыполненных (сегодня) в вечернем.
    """
    logs = await repo.get_logs_for_date(user.telegram_id, target_date)
    tasks: list[Task] = []
    for log in logs:
        if log.status == TaskStatus.done:
            continue
        task = await repo.get_task(log.task_id)
        if task is not None and task.is_active:
            tasks.append(task)
    tasks.sort(key=lambda t: t.id)
    return tasks


async def _overdue_tasks(repo: Repository, user: User, today) -> list[Task]:
    """Просроченные = вчерашние невыполненные задачи (любого типа)."""
    return await _tasks_for_date_not_done(repo, user, today - timedelta(days=1))


async def _evening_digest_text(repo: Repository, user: User, today) -> str:
    """Текст вечернего итога: выполненные против оставшихся за сегодня."""
    logs = await repo.get_logs_for_date(user.telegram_id, today)
    if not logs:
        return escape_md(TEXTS["digest_evening_no_tasks"])

    done, remaining = [], []
    for log in logs:
        task = await repo.get_task(log.task_id)
        if task is None:
            continue
        (done if log.status == TaskStatus.done else remaining).append(task.name)

    if not remaining:
        header = escape_md(TEXTS["digest_evening_header"])
        return f"{header}\n\n{escape_md(TEXTS['digest_evening_all_done'])}"

    parts = [escape_md(TEXTS["digest_evening_header"]), ""]
    if done:
        parts.append(escape_md(TEXTS["digest_evening_done"]))
        parts.extend(f"• {escape_md(name)}" for name in done)
        parts.append("")
    parts.append(escape_md(TEXTS["digest_evening_pending"]))
    parts.extend(f"• {escape_md(name)}" for name in remaining)
    return "\n".join(parts)


async def build_evening_digest(
    repo: Repository,
    user: User,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Вечерний дайджест: текст итогов + кнопка «Отметить выполненные».

    Кнопка показывается только если есть невыполненные задачи. (текст, клавиатура)
    """
    today = datetime.now(_user_tz(user)).date()
    text = await _evening_digest_text(repo, user, today)
    remaining = await _tasks_for_date_not_done(repo, user, today)
    keyboard = evening_mark_kb() if remaining else None
    return text, keyboard


async def build_morning_digest(
    repo: Repository,
    user: User,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Утренний дайджест: блок задач на сегодня + блок просроченных.

    Блок просроченных и кнопка «Отметить вчерашние задачи» — только если такие
    задачи есть. Возвращает (текст, клавиатуру).
    """
    today = datetime.now(_user_tz(user)).date()
    active, marked = await _today_tasks(repo, user, today)
    overdue = await _overdue_tasks(repo, user, today)

    if not active and not marked and not overdue:
        return escape_md(TEXTS["digest_morning_no_tasks"]), None

    blocks: list[str] = []
    if active or marked:
        lines = [escape_md(TEXTS["digest_morning_header"]), ""]
        lines.extend(_render_task_lines(active, marked))
        blocks.append("\n".join(lines))
    else:
        blocks.append(escape_md(TEXTS["digest_morning_greeting"]))

    if overdue:
        olines = [escape_md(TEXTS["digest_overdue_header"])]
        olines.extend(f"• {escape_md(task.name)}" for task in overdue)
        blocks.append("\n".join(olines))

    text = "\n\n".join(blocks)
    keyboard = morning_overdue_kb() if overdue else None
    return text, keyboard


async def _task_card_text(repo: Repository, task: Task) -> str:
    """Собрать текст карточки задачи. Для одноразовых задач стрик не показывается."""
    lines = [escape_md(task.name), "", f"📅 {escape_md(_freq_line(task))}"]
    if task.reminder_time is not None:
        lines.append(f"⏰ {escape_md(task.reminder_time.strftime('%H:%M'))}")
    # Одноразовые задачи не участвуют в стриках.
    if task.frequency_type != FrequencyType.one_time:
        current = await get_current_streak(repo, task.id)
        best = await get_max_streak(repo, task.id)
        lines.append(f"🔥 {escape_md(f'Текущий стрик: {current} дней')}")
        lines.append(f"🏆 {escape_md(f'Лучший стрик: {best} дней')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  /today
# --------------------------------------------------------------------------- #

@router.message(Command("today"))
async def cmd_today(message: Message, state: FSMContext, repo: Repository) -> None:
    """Показать список невыполненных задач на сегодня."""
    await state.clear()
    user = await repo.get_user(message.from_user.id)
    text, keyboard = await _today_list_message(repo, user)
    await message.answer(text, reply_markup=keyboard if keyboard else REMOVE_KB)


@router.callback_query(F.data.startswith("today_open:"))
async def today_open(callback: CallbackQuery, repo: Repository) -> None:
    """Открыть карточку выбранной задачи (редактируем список → карточка)."""
    task_id = int(callback.data.split(":", 1)[1])
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None or callback.message is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    text = await _task_card_text(repo, task)
    await callback.message.edit_text(text, reply_markup=today_card_kb(task_id))
    await callback.answer()


@router.callback_query(F.data.startswith("today_done:"))
async def today_done(callback: CallbackQuery, repo: Repository) -> None:
    """Отметить задачу выполненной и вернуть сообщение к списку."""
    task_id = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None or callback.message is None:
        await callback.answer()
        return
    today = datetime.now(_user_tz(user)).date()
    log = await repo.get_or_create_log(task_id, user.telegram_id, today)
    await repo.set_log_status(log, TaskStatus.done)
    logger.info("User {} marked task {} as done", user.telegram_id, task_id)
    text, keyboard = await _today_list_message(repo, user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "today_back_list")
async def today_back_list(callback: CallbackQuery, repo: Repository) -> None:
    """«‹ Назад» с карточки — вернуть список /today."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    text, keyboard = await _today_list_message(repo, user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("today_page:"))
async def today_page(callback: CallbackQuery, repo: Repository) -> None:
    """Перелистнуть страницу списка /today (меняем только клавиатуру)."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    active, _ = await _today_tasks(repo, user, today)
    if not active:
        await callback.answer()
        return
    await callback.message.edit_reply_markup(
        reply_markup=today_list_kb([(task.id, task.name) for task, _ in active], page)
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
#  /done
# --------------------------------------------------------------------------- #

@router.message(Command("done"))
async def cmd_done(message: Message, state: FSMContext, repo: Repository) -> None:
    """Показать выполненные сегодня задачи."""
    await state.clear()
    user = await repo.get_user(message.from_user.id)
    text, keyboard = await _done_list_message(repo, user)
    await message.answer(text, reply_markup=keyboard if keyboard else REMOVE_KB)


@router.callback_query(F.data.startswith("done_open:"))
async def done_open(callback: CallbackQuery, repo: Repository) -> None:
    """Открыть карточку выполненной задачи."""
    task_id = int(callback.data.split(":", 1)[1])
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None or callback.message is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        return
    text = await _task_card_text(repo, task)
    await callback.message.edit_text(text, reply_markup=done_card_kb(task_id))
    await callback.answer()


@router.callback_query(F.data.startswith("done_undo:"))
async def done_undo(callback: CallbackQuery, repo: Repository) -> None:
    """«Отменить выполнение» — вернуть задачу в активные и показать список /done."""
    task_id = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None or callback.message is None:
        await callback.answer()
        return
    today = datetime.now(_user_tz(user)).date()
    log = await repo.get_or_create_log(task_id, user.telegram_id, today)
    await repo.set_log_status(log, TaskStatus.pending)
    logger.info("User {} undid completion of task {}", user.telegram_id, task_id)
    text, keyboard = await _done_list_message(repo, user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "done_back_list")
async def done_back_list(callback: CallbackQuery, repo: Repository) -> None:
    """«‹ Назад» с карточки — вернуть список /done."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    text, keyboard = await _done_list_message(repo, user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("done_page:"))
async def done_page(callback: CallbackQuery, repo: Repository) -> None:
    """Перелистнуть страницу списка /done (меняем только клавиатуру)."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    _, marked = await _today_tasks(repo, user, today)
    completed = [task for task, log in marked if log.status == TaskStatus.done]
    if not completed:
        await callback.answer()
        return
    await callback.message.edit_reply_markup(
        reply_markup=done_list_kb([(task.id, task.name) for task in completed], page)
    )
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Интерактивный утренний дайджест: отметка вчерашних (просроченных) задач
#
#  Весь флоу идёт через редактирование одного сообщения. Выбор (галочки)
#  хранится в FSM-данных (md_selected, md_page); просроченные задачи каждый раз
#  пересчитываются из БД. «Подтвердить» активно до 12:00 по времени пользователя.
# --------------------------------------------------------------------------- #

def _overdue_select_text(overdue: list[Task]) -> str:
    """Экран выбора: заголовок + нумерованный список просроченных задач."""
    lines = [escape_md(TEXTS["overdue_select_header"]), ""]
    lines.extend(
        f"{i + 1}\\. {escape_md(task.name)}" for i, task in enumerate(overdue)
    )
    return "\n".join(lines)


def _overdue_confirm_text(overdue: list[Task], selected: set[int]) -> str:
    """Экран подтверждения: отмеченные / неотмеченные + предупреждение."""
    marked = [task for task in overdue if task.id in selected]
    unmarked = [task for task in overdue if task.id not in selected]
    parts = [escape_md(TEXTS["overdue_confirm_header"]), ""]
    if marked:
        parts.append(escape_md(TEXTS["overdue_confirm_marked"]))
        parts.extend(f"• {escape_md(task.name)}" for task in marked)
        parts.append("")
    if unmarked:
        parts.append(escape_md(TEXTS["overdue_confirm_unmarked"]))
        parts.extend(f"• {escape_md(task.name)}" for task in unmarked)
        parts.append("")
    if not marked:
        parts.append(escape_md(TEXTS["overdue_confirm_none_marked"]))
        parts.append("")
    parts.append(escape_md(TEXTS["overdue_confirm_warning"]))
    return "\n".join(parts)


async def _morning_final_text(repo: Repository, user: User) -> str:
    """Финальный вид: утренний дайджест без просроченных + строка про /help."""
    today = datetime.now(_user_tz(user)).date()
    active, marked = await _today_tasks(repo, user, today)
    if active or marked:
        lines = [escape_md(TEXTS["digest_morning_header"]), ""]
        lines.extend(_render_task_lines(active, marked))
        block = "\n".join(lines)
    else:
        block = escape_md(TEXTS["digest_morning_greeting"])
    return f"{block}\n\n{escape_md(TEXTS['digest_morning_help'])}"


@router.callback_query(F.data == "md_mark")
async def md_mark(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Отметить вчерашние задачи» — открыть экран выбора."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    if not overdue:
        # Просроченных уже нет — обновляем дайджест (кнопка исчезает).
        await callback.answer(TEXTS["overdue_none"], show_alert=True)
        text, keyboard = await build_morning_digest(repo, user)
        await callback.message.edit_text(text, reply_markup=keyboard)
        return
    await state.update_data(md_selected=[], md_page=0)
    await callback.message.edit_text(
        _overdue_select_text(overdue),
        reply_markup=task_select_kb([(t.id, t.name) for t in overdue], set(), 0, "md"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("md_toggle:"))
async def md_toggle(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Переключить галочку на задаче (меняем только клавиатуру)."""
    if callback.message is None:
        await callback.answer()
        return
    task_id = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    overdue_ids = {task.id for task in overdue}
    data = await state.get_data()
    selected = set(data.get("md_selected", [])) & overdue_ids
    page = data.get("md_page", 0)
    if task_id in overdue_ids:
        selected.symmetric_difference_update({task_id})
    await state.update_data(md_selected=list(selected))
    await callback.message.edit_reply_markup(
        reply_markup=task_select_kb(
            [(t.id, t.name) for t in overdue], selected, page, "md"
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("md_page:"))
async def md_page(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Пагинация списка просроченных задач (alert на краях)."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    total_pages = max(1, (len(overdue) + OVERDUE_PAGE_SIZE - 1) // OVERDUE_PAGE_SIZE)
    if page < 0:
        await callback.answer(TEXTS["pagination_first"], show_alert=True)
        return
    if page >= total_pages:
        await callback.answer(TEXTS["pagination_last"], show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("md_selected", []))
    await state.update_data(md_page=page)
    await callback.message.edit_reply_markup(
        reply_markup=task_select_kb(
            [(t.id, t.name) for t in overdue], selected, page, "md"
        )
    )
    await callback.answer()


@router.callback_query(F.data == "md_done")
async def md_done(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Готово» — показать экран подтверждения."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("md_selected", [])) & {task.id for task in overdue}
    await callback.message.edit_text(
        _overdue_confirm_text(overdue, selected), reply_markup=select_confirm_kb("md")
    )
    await callback.answer()


@router.callback_query(F.data == "md_back_digest")
async def md_back_digest(callback: CallbackQuery, repo: Repository) -> None:
    """«‹ Назад» (с выбора) — вернуть исходный утренний дайджест."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    text, keyboard = await build_morning_digest(repo, user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "md_back_select")
async def md_back_select(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«‹ Назад» (с подтверждения) — вернуть экран выбора (галочки сохраняются)."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    overdue = await _overdue_tasks(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("md_selected", [])) & {task.id for task in overdue}
    page = data.get("md_page", 0)
    await callback.message.edit_text(
        _overdue_select_text(overdue),
        reply_markup=task_select_kb([(t.id, t.name) for t in overdue], selected, page, "md"),
    )
    await callback.answer()


@router.callback_query(F.data == "md_confirm")
async def md_confirm(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Подтвердить»: до 12:00 — зафиксировать отметки, иначе экран «время вышло»."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    tz = _user_tz(user)
    now = datetime.now(tz)
    today = now.date()
    deadline = tz.localize(datetime.combine(today, time(12, 0)))
    if now >= deadline:
        await callback.message.edit_text(
            escape_md(TEXTS["overdue_expired"]), reply_markup=overdue_expired_kb()
        )
        await callback.answer()
        return

    overdue = await _overdue_tasks(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("md_selected", []))
    yesterday = today - timedelta(days=1)
    done_count = 0
    for task in overdue:
        log = await repo.get_or_create_log(task.id, user.telegram_id, yesterday)
        if task.id in selected:
            await repo.set_log_status(log, TaskStatus.done)
            done_count += 1
        else:
            await repo.set_log_status(log, TaskStatus.missed)
    await state.update_data(md_selected=[], md_page=0)
    logger.info(
        "User {} confirmed overdue marking ({}/{} done)",
        user.telegram_id, done_count, len(overdue),
    )
    text = await _morning_final_text(repo, user)
    await callback.message.edit_text(text, reply_markup=None)
    await callback.answer()


@router.callback_query(F.data == "md_expired_ok")
async def md_expired_ok(callback: CallbackQuery, repo: Repository) -> None:
    """Подтверждение на экране «время вышло» — показать финальный вид без изменений."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    text = await _morning_final_text(repo, user)
    await callback.message.edit_text(text, reply_markup=None)
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Интерактивный вечерний дайджест: отметка выполненных сегодня задач
#
#  Аналог утреннего флоу (префикс callback-data "ed"), но: дедлайна нет,
#  предупреждения о сгорании нет, «Подтвердить» помечает выбранные как
#  выполненные (невыбранные не трогает) и возвращает к дайджесту с сохранённой
#  кнопкой — процесс можно повторить.
# --------------------------------------------------------------------------- #

def _evening_confirm_text(remaining: list[Task], selected: set[int]) -> str:
    """Экран подтверждения вечернего дайджеста (без предупреждения о сгорании)."""
    marked = [task for task in remaining if task.id in selected]
    unmarked = [task for task in remaining if task.id not in selected]
    parts = [escape_md(TEXTS["evening_confirm_header"]), ""]
    if marked:
        parts.append(escape_md(TEXTS["evening_confirm_marked"]))
        parts.extend(f"• {escape_md(task.name)}" for task in marked)
        parts.append("")
    if unmarked:
        parts.append(escape_md(TEXTS["evening_confirm_unmarked"]))
        parts.extend(f"• {escape_md(task.name)}" for task in unmarked)
        parts.append("")
    if not marked:
        parts.append(escape_md(TEXTS["evening_confirm_none_marked"]))
    return "\n".join(parts).rstrip()


@router.callback_query(F.data == "ed_mark")
async def ed_mark(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Отметить выполненные» — добавить абзац и показать экран выбора."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    remaining = await _tasks_for_date_not_done(repo, user, today)
    if not remaining:
        await callback.answer(TEXTS["evening_nothing"], show_alert=True)
        text, keyboard = await build_evening_digest(repo, user)
        await callback.message.edit_text(text, reply_markup=keyboard)
        return
    await state.update_data(ed_selected=[], ed_page=0)
    digest_text = await _evening_digest_text(repo, user, today)
    text = f"{digest_text}\n\n{escape_md(TEXTS['evening_select_prompt'])}"
    await callback.message.edit_text(
        text,
        reply_markup=task_select_kb([(t.id, t.name) for t in remaining], set(), 0, "ed"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ed_toggle:"))
async def ed_toggle(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Переключить галочку на задаче (меняем только клавиатуру)."""
    if callback.message is None:
        await callback.answer()
        return
    task_id = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    remaining = await _tasks_for_date_not_done(repo, user, today)
    remaining_ids = {task.id for task in remaining}
    data = await state.get_data()
    selected = set(data.get("ed_selected", [])) & remaining_ids
    page = data.get("ed_page", 0)
    if task_id in remaining_ids:
        selected.symmetric_difference_update({task_id})
    await state.update_data(ed_selected=list(selected))
    await callback.message.edit_reply_markup(
        reply_markup=task_select_kb(
            [(t.id, t.name) for t in remaining], selected, page, "ed"
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ed_page:"))
async def ed_page(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """Пагинация списка невыполненных задач (alert на краях)."""
    if callback.message is None:
        await callback.answer()
        return
    page = int(callback.data.split(":", 1)[1])
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    remaining = await _tasks_for_date_not_done(repo, user, today)
    total_pages = max(1, (len(remaining) + OVERDUE_PAGE_SIZE - 1) // OVERDUE_PAGE_SIZE)
    if page < 0:
        await callback.answer(TEXTS["pagination_first"], show_alert=True)
        return
    if page >= total_pages:
        await callback.answer(TEXTS["pagination_last"], show_alert=True)
        return
    data = await state.get_data()
    selected = set(data.get("ed_selected", []))
    await state.update_data(ed_page=page)
    await callback.message.edit_reply_markup(
        reply_markup=task_select_kb(
            [(t.id, t.name) for t in remaining], selected, page, "ed"
        )
    )
    await callback.answer()


@router.callback_query(F.data == "ed_done")
async def ed_done(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Готово» — показать экран подтверждения (без предупреждения о сгорании)."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    remaining = await _tasks_for_date_not_done(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("ed_selected", [])) & {task.id for task in remaining}
    await callback.message.edit_text(
        _evening_confirm_text(remaining, selected), reply_markup=select_confirm_kb("ed")
    )
    await callback.answer()


@router.callback_query(F.data == "ed_back_digest")
async def ed_back_digest(callback: CallbackQuery, repo: Repository) -> None:
    """«‹ Назад» (с выбора) — вернуть вечерний дайджест."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    text, keyboard = await build_evening_digest(repo, user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "ed_back_select")
async def ed_back_select(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«‹ Назад» (с подтверждения) — вернуть экран выбора (галочки сохраняются)."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    remaining = await _tasks_for_date_not_done(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("ed_selected", [])) & {task.id for task in remaining}
    page = data.get("ed_page", 0)
    digest_text = await _evening_digest_text(repo, user, today)
    text = f"{digest_text}\n\n{escape_md(TEXTS['evening_select_prompt'])}"
    await callback.message.edit_text(
        text,
        reply_markup=task_select_kb([(t.id, t.name) for t in remaining], selected, page, "ed"),
    )
    await callback.answer()


@router.callback_query(F.data == "ed_confirm")
async def ed_confirm(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    """«Подтвердить» — пометить выбранные выполненными и вернуть дайджест (кнопка остаётся)."""
    if callback.message is None:
        await callback.answer()
        return
    user = await repo.get_user(callback.from_user.id)
    today = datetime.now(_user_tz(user)).date()
    remaining = await _tasks_for_date_not_done(repo, user, today)
    data = await state.get_data()
    selected = set(data.get("ed_selected", [])) & {task.id for task in remaining}
    for task in remaining:
        if task.id in selected:
            log = await repo.get_or_create_log(task.id, user.telegram_id, today)
            await repo.set_log_status(log, TaskStatus.done)
    await state.update_data(ed_selected=[], ed_page=0)
    logger.info("User {} marked {} tasks done via evening digest", user.telegram_id, len(selected))
    text, keyboard = await build_evening_digest(repo, user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Кнопка «Выполнена» на напоминании о задаче
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("rem_done:"))
async def reminder_done(callback: CallbackQuery, repo: Repository) -> None:
    """Отметить задачу выполненной из напоминания (активно до 12:00 следующего дня)."""
    if callback.message is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    task_id = int(parts[1])
    target_date = date.fromisoformat(parts[2])
    user = await repo.get_user(callback.from_user.id)
    tz = _user_tz(user)
    now = datetime.now(tz)
    deadline = tz.localize(datetime.combine(target_date + timedelta(days=1), time(12, 0)))
    if now >= deadline:
        await callback.message.edit_text(escape_md(TEXTS["reminder_expired"]))
        await callback.answer()
        return

    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None:
        await callback.message.edit_text(escape_md(TEXTS["reminder_gone"]))
        await callback.answer()
        return

    log = await repo.get_or_create_log(task_id, user.telegram_id, target_date)
    if log.status == TaskStatus.done:
        await callback.message.edit_text(
            escape_md(TEXTS["reminder_already_done"].format(name=task.name))
        )
    else:
        await repo.set_log_status(log, TaskStatus.done)
        await callback.message.edit_text(
            escape_md(TEXTS["reminder_marked_done"].format(name=task.name))
        )
        logger.info("User {} marked task {} done via reminder", user.telegram_id, task_id)
    await callback.answer()
