"""/tasks — просмотр и управление задачами в рамках одного сообщения.

Иерархия (всё через редактирование одного сообщения; контекст — раздел и
страница — кодируется прямо в callback-data, поэтому навигация переживает сброс
FSM, и на каждом шаге «‹ Назад» возвращает к предыдущему состоянию):

    меню → список раздела → карточка задачи → подтверждение удаления

- Меню: «Все задачи» (раздел "a") и «Задачи на сегодня» (раздел "t").
- Список: жирный заголовок, по каждой задаче — название и стрик (для одноразовых
  стрика нет), жирная подсказка; кнопки задач (по 2 в ряд) + стрелки + «‹ Назад».
  «Все задачи» — активные повторяющиеся задачи (как /stats), «Задачи на сегодня» —
  задачи, запланированные на сегодня (любого типа).
- Карточка: название, параметры (частота, напоминание), стрик, подсказка. Кнопки:
  «Выполнено» с галочкой (только если задача на сегодня) — переключает статус
  сегодняшнего лога; удаление (с подтверждением); «‹ Назад» к списку.
"""

from __future__ import annotations

from datetime import date, datetime

import pytz
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from loguru import logger

from bot.constants import STATS_PAGE_SIZE, TEXTS
from bot.database.models import FrequencyType, Task, TaskStatus
from bot.database.repository import Repository
from bot.handlers.stats import stats_tasks
from bot.keyboards.builders import (
    task_card_kb,
    task_delete_confirm_kb,
    tasks_list_kb,
    tasks_menu_kb,
)
from bot.services.scheduler import SchedulerService
from bot.services.streak import get_current_streak, get_max_streak
from bot.utils.validators import escape_md, format_days_short

router = Router(name="tasks")

# Коды разделов в callback-data.
_SEC_ALL = "a"      # все задачи (как /stats)
_SEC_TODAY = "t"    # задачи на сегодня


# --------------------------------------------------------------------------- #
#  Вспомогательные функции
# --------------------------------------------------------------------------- #

async def _user_today(repo: Repository, user_id: int) -> date:
    """Текущая дата в часовом поясе пользователя (UTC как фолбэк)."""
    user = await repo.get_user(user_id)
    try:
        tz = pytz.timezone(user.timezone) if user and user.timezone else pytz.utc
    except Exception:  # noqa: BLE001
        tz = pytz.utc
    return datetime.now(tz).date()


def _freq_str(task: Task) -> str:
    """Человекочитаемая частота задачи для карточки (как в /edit)."""
    if task.frequency_type == FrequencyType.daily:
        return "Каждый день"
    if task.frequency_type == FrequencyType.specific_days:
        return format_days_short(task.days or "")
    return "Одноразовая · " + task.one_time_date.strftime("%d.%m.%Y")


async def _section_data(
    repo: Repository, user_id: int, section: str
) -> tuple[list[Task], str]:
    """Вернуть (задачи раздела, заголовок).

    «Все задачи» — активные повторяющиеся (как /stats, без одноразовых).
    «Задачи на сегодня» — задачи, запланированные на сегодня (любого типа).
    """
    if section == _SEC_TODAY:
        today = await _user_today(repo, user_id)
        tasks = await repo.get_tasks_due_on(user_id, today)
        return tasks, TEXTS["tasks_today_header"]
    tasks = await stats_tasks(repo, user_id)
    return tasks, TEXTS["tasks_all_header"]


async def _task_streak_lines(repo: Repository, task: Task) -> list[str]:
    """Строки со стриком задачи (для одноразовых — пусто: они не участвуют в стриках)."""
    if task.frequency_type == FrequencyType.one_time:
        return []
    current = await get_current_streak(repo, task.id)
    best = await get_max_streak(repo, task.id)
    return [
        f"🔥 {escape_md(f'Текущий стрик: {current} дней')}",
        f"🏆 {escape_md(f'Лучший стрик: {best} дней')}",
    ]


async def _list_text(repo: Repository, tasks: list[Task], header: str) -> str:
    """Текст списка: жирный заголовок + по задаче (название + стрик) + жирная подсказка."""
    lines = [f"*{escape_md(header)}*", ""]
    for task in tasks:
        lines.append(escape_md(task.name))
        lines.extend(await _task_streak_lines(repo, task))
        lines.append("")
    lines.append(f"*{escape_md(TEXTS['tasks_list_prompt'])}*")
    return "\n".join(lines)


async def _card_text(repo: Repository, task: Task) -> str:
    """Текст карточки: название (жирное), параметры (частота, напоминание), стрик, подсказка."""
    lines = [
        f"*{escape_md(task.name)}*",
        "",
        f"📅 {escape_md(_freq_str(task))}",
    ]
    if task.reminder_time is not None:
        lines.append(f"⏰ {escape_md(task.reminder_time.strftime('%H:%M'))}")
    else:
        lines.append(f"⏰ {escape_md(TEXTS['edit_reminder_none'])}")
    streak = await _task_streak_lines(repo, task)
    if streak:
        lines.append("")
        lines.extend(streak)
    lines.append("")
    lines.append(f"*{escape_md(TEXTS['task_card_prompt'])}*")
    return "\n".join(lines)


async def _list_view(
    repo: Repository, user_id: int, section: str, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Собрать вид списка раздела (страница клампится).

    Пустой раздел — текст-заглушка и клавиатура с одной кнопкой «‹ Назад».
    """
    tasks, header = await _section_data(repo, user_id, section)
    if not tasks:
        empty = TEXTS["tasks_today_empty"] if section == _SEC_TODAY else TEXTS["no_tasks_yet"]
        return escape_md(empty), tasks_list_kb([], section, 0, 1)
    total_pages = max(1, (len(tasks) + STATS_PAGE_SIZE - 1) // STATS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    page_tasks = tasks[page * STATS_PAGE_SIZE : (page + 1) * STATS_PAGE_SIZE]
    text = await _list_text(repo, page_tasks, header)
    keyboard = tasks_list_kb(
        [(t.id, t.name) for t in page_tasks], section, page, total_pages
    )
    return text, keyboard


def _parse_ctx(data: str) -> tuple[str, int, int | None]:
    """Разобрать callback-data `prefix:{section}:{page}[:{task_id}]`.

    Возвращает (section, page, task_id|None).
    """
    parts = data.split(":")
    section = parts[1]
    page = int(parts[2])
    task_id = int(parts[3]) if len(parts) > 3 else None
    return section, page, task_id


async def _is_due_today(repo: Repository, user_id: int, task_id: int, today: date) -> bool:
    """Запланирована ли задача на сегодня (для показа кнопки «Выполнено»)."""
    due_today = await repo.get_tasks_due_on(user_id, today)
    return any(t.id == task_id for t in due_today)


# --------------------------------------------------------------------------- #
#  Меню и списки
# --------------------------------------------------------------------------- #

@router.message(Command("tasks"))
async def cmd_tasks(message: Message, state: FSMContext) -> None:
    """/tasks — показать меню: «Все задачи» / «Задачи на сегодня»."""
    await state.clear()
    await message.answer(
        escape_md(TEXTS["tasks_menu_prompt"]), reply_markup=tasks_menu_kb()
    )


@router.callback_query(F.data == "tasks_menu")
async def tasks_back_to_menu(callback: CallbackQuery) -> None:
    """«‹ Назад» из списка — вернуть меню с двумя кнопками."""
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.edit_text(
        escape_md(TEXTS["tasks_menu_prompt"]), reply_markup=tasks_menu_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tk_list:"))
async def tk_list(callback: CallbackQuery, repo: Repository) -> None:
    """Показать страницу списка раздела (стрелки — alert на краях)."""
    if callback.message is None:
        await callback.answer()
        return
    section, page, _ = _parse_ctx(callback.data)
    tasks, _header = await _section_data(repo, callback.from_user.id, section)
    if tasks:
        # Стрелки могут увести за границы — на краях показываем alert (как в /stats).
        total_pages = max(1, (len(tasks) + STATS_PAGE_SIZE - 1) // STATS_PAGE_SIZE)
        if page < 0:
            await callback.answer(TEXTS["pagination_first"], show_alert=True)
            return
        if page >= total_pages:
            await callback.answer(TEXTS["pagination_last"], show_alert=True)
            return
    text, keyboard = await _list_view(repo, callback.from_user.id, section, page)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# --------------------------------------------------------------------------- #
#  Карточка задачи: просмотр, отметка «Выполнено», удаление
# --------------------------------------------------------------------------- #

async def _show_card(
    callback: CallbackQuery, repo: Repository, section: str, page: int, task: Task
) -> None:
    """Отрисовать карточку задачи (с учётом «есть ли сегодня» и текущей отметки)."""
    today = await _user_today(repo, callback.from_user.id)
    is_today = await _is_due_today(repo, callback.from_user.id, task.id, today)
    is_done = False
    if is_today:
        log = await repo.get_log(task.id, today)
        is_done = log is not None and log.status == TaskStatus.done
    text = await _card_text(repo, task)
    await callback.message.edit_text(
        text, reply_markup=task_card_kb(section, page, task.id, is_today, is_done)
    )


async def _back_to_list(
    callback: CallbackQuery, repo: Repository, section: str, page: int
) -> None:
    """Вернуть сообщение к списку раздела (в т.ч. если задача исчезла или удалена)."""
    text, keyboard = await _list_view(repo, callback.from_user.id, section, page)
    await callback.message.edit_text(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("tk_card:"))
async def tk_card(callback: CallbackQuery, repo: Repository) -> None:
    """Открыть карточку выбранной задачи."""
    if callback.message is None:
        await callback.answer()
        return
    section, page, task_id = _parse_ctx(callback.data)
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        await _back_to_list(callback, repo, section, page)
        return
    await _show_card(callback, repo, section, page, task)
    await callback.answer()


@router.callback_query(F.data.startswith("tk_done:"))
async def tk_done(callback: CallbackQuery, repo: Repository) -> None:
    """Кнопка «Выполнено» — переключить статус сегодняшнего лога и обновить карточку."""
    if callback.message is None:
        await callback.answer()
        return
    section, page, task_id = _parse_ctx(callback.data)
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        await _back_to_list(callback, repo, section, page)
        return
    today = await _user_today(repo, callback.from_user.id)
    if not await _is_due_today(repo, callback.from_user.id, task_id, today):
        # Кнопка показывается только для сегодняшних задач — защитная ветка.
        await callback.answer()
        await _show_card(callback, repo, section, page, task)
        return
    log = await repo.get_or_create_log(task_id, callback.from_user.id, today)
    new_status = (
        TaskStatus.pending if log.status == TaskStatus.done else TaskStatus.done
    )
    await repo.set_log_status(log, new_status)
    logger.info(
        "User {} toggled task {} -> {} via /tasks card",
        callback.from_user.id, task_id, new_status.value,
    )
    await _show_card(callback, repo, section, page, task)
    await callback.answer()


@router.callback_query(F.data.startswith("tk_del:"))
async def tk_del(callback: CallbackQuery, repo: Repository) -> None:
    """Кнопка удаления — запросить подтверждение (редактируя карточку)."""
    if callback.message is None:
        await callback.answer()
        return
    section, page, task_id = _parse_ctx(callback.data)
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None:
        await callback.answer(TEXTS["task_not_found"], show_alert=True)
        await _back_to_list(callback, repo, section, page)
        return
    await callback.message.edit_text(
        escape_md(TEXTS["delete_confirm"].format(name=task.name)),
        reply_markup=task_delete_confirm_kb(section, page, task_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tk_dok:"))
async def tk_delete(
    callback: CallbackQuery, repo: Repository, scheduler: SchedulerService
) -> None:
    """«Подтвердить» — мягко удалить задачу, снять напоминание и вернуться к списку раздела."""
    if callback.message is None:
        await callback.answer()
        return
    section, page, task_id = _parse_ctx(callback.data)
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is not None:
        had_reminder = task.reminder_time is not None
        await repo.soft_delete_task(task)
        if had_reminder:
            scheduler.remove_task_reminder_job(task_id)
        logger.info("User {} deleted task {} via /tasks", callback.from_user.id, task_id)
    # Возврат к списку раздела (страница клампится — задач стало меньше).
    await _back_to_list(callback, repo, section, page)
    await callback.answer(TEXTS["delete_done"])
