"""/today (список задач на сегодня) и /taskN (карточка задачи).

`build_today_digest` переиспользуется планировщиком для утреннего дайджеста.
Карточка задачи позволяет отметить выполнение: «Отметить выполнение» →
«✅ Выполнено» / «❌ Не выполнено» → сообщение превращается в обновлённый
список /today.
"""

from __future__ import annotations

from datetime import datetime

import pytz
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger

from bot.constants import FREQ_LABELS, TEXTS
from bot.database.models import FrequencyType, Task, TaskStatus, User
from bot.database.repository import Repository
from bot.keyboards.builders import REMOVE_KB, task_done_skip_kb, task_mark_kb
from bot.services.streak import get_current_streak, get_max_streak
from bot.utils.validators import escape_md, format_days_short

router = Router(name="today")

# Разделитель между активными и закрытыми задачами в списке /today.
_SEPARATOR = "━━━━━━━━━━━━"


def _freq_line(task: Task) -> str:
    """Человекочитаемое описание частоты задачи для карточки/списка."""
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


async def build_today_digest(
    repo: Repository,
    user: User,
    header_key: str,
    empty_key: str,
) -> str:
    """Собрать список задач на сегодня (два блока) и создать pending-логи.

    Возвращает готовый к отправке MarkdownV2-текст.
    """
    today = datetime.now(_user_tz(user)).date()
    tasks = await repo.get_tasks_due_on(user.telegram_id, today)
    if not tasks:
        return escape_md(TEXTS[empty_key])

    active: list[str] = []
    closed: list[str] = []
    for task in tasks:
        log = await repo.get_or_create_log(task.id, user.telegram_id, today)
        line = f"{escape_md(task.name)} /task{task.id}"
        if log.status == TaskStatus.pending:
            active.append(line)
        else:
            closed.append(line)

    lines = [escape_md(TEXTS[header_key]), ""]
    lines.extend(active)
    if closed:
        if active:
            lines.append("")
        lines.append(_SEPARATOR)
        lines.extend(closed)
    return "\n".join(lines)


async def _task_card_text(repo: Repository, task: Task) -> str:
    """Собрать текст карточки задачи со стриками."""
    current = await get_current_streak(repo, task.id)
    best = await get_max_streak(repo, task.id)
    lines = [escape_md(task.name), "", f"📅 {escape_md(_freq_line(task))}"]
    if task.reminder_time is not None:
        lines.append(f"⏰ {escape_md(task.reminder_time.strftime('%H:%M'))}")
    lines.append(f"🔥 {escape_md(f'Текущий стрик: {current} дней')}")
    lines.append(f"🏆 {escape_md(f'Лучший стрик: {best} дней')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  /today
# --------------------------------------------------------------------------- #

@router.message(Command("today"))
async def cmd_today(message: Message, state: FSMContext, repo: Repository) -> None:
    """Показать список задач на сегодня."""
    await state.clear()
    user = await repo.get_user(message.from_user.id)
    text = await build_today_digest(repo, user, "today_header", "today_no_tasks")
    await message.answer(text, reply_markup=REMOVE_KB)


# --------------------------------------------------------------------------- #
#  /taskN — карточка задачи
# --------------------------------------------------------------------------- #

@router.message(F.text.regexp(r"^/task\d+$"))
async def show_task_card(message: Message, state: FSMContext, repo: Repository) -> None:
    """Открыть карточку задачи по команде /taskN."""
    await state.clear()
    task_id = int(message.text[len("/task"):])
    task = await repo.get_active_task(task_id, message.from_user.id)
    if task is None:
        await message.answer(escape_md(TEXTS["task_not_found"]))
        return
    text = await _task_card_text(repo, task)
    await message.answer(text, reply_markup=task_mark_kb(task.id))


@router.callback_query(F.data.startswith("mark:"))
async def mark_intent(callback: CallbackQuery, repo: Repository) -> None:
    """«Отметить выполнение» — показать кнопки Выполнено/Не выполнено."""
    task_id = int(callback.data.split(":", 1)[1])
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None or callback.message is None:
        await callback.answer()
        return
    text = await _task_card_text(repo, task)
    await callback.message.edit_text(text, reply_markup=task_done_skip_kb(task_id))
    await callback.answer()


async def _apply_status(
    callback: CallbackQuery,
    repo: Repository,
    task_id: int,
    status: TaskStatus,
) -> None:
    """Проставить статус задачи на сегодня и показать обновлённый список /today."""
    user = await repo.get_user(callback.from_user.id)
    task = await repo.get_active_task(task_id, callback.from_user.id)
    if task is None or callback.message is None:
        await callback.answer()
        return
    today = datetime.now(_user_tz(user)).date()
    log = await repo.get_or_create_log(task_id, user.telegram_id, today)
    await repo.set_log_status(log, status)
    logger.info("User {} marked task {} as {}", user.telegram_id, task_id, status.value)

    text = await build_today_digest(repo, user, "today_header", "today_no_tasks")
    await callback.message.edit_text(text, reply_markup=None)
    await callback.answer()


@router.callback_query(F.data.startswith("done:"))
async def mark_done(callback: CallbackQuery, repo: Repository) -> None:
    """Отметить задачу выполненной."""
    task_id = int(callback.data.split(":", 1)[1])
    await _apply_status(callback, repo, task_id, TaskStatus.done)


@router.callback_query(F.data.startswith("skip:"))
async def mark_skip(callback: CallbackQuery, repo: Repository) -> None:
    """Отметить задачу не выполненной."""
    task_id = int(callback.data.split(":", 1)[1])
    await _apply_status(callback, repo, task_id, TaskStatus.skipped)
