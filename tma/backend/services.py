"""Прикладная логика API поверх репозитория бота.

Доступ к данным идёт только через `Repository` — SQL здесь не пишется, логика
бота не дублируется. Этот слой лишь собирает из задач и их логов форму ответа
(`Habit`) и применяет переключение отметки за сегодня.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytz

from bot.database.models import Task, TaskStatus, User
from bot.database.repository import Repository
from tma.backend.constants import YEAR_GRID_DAYS
from tma.backend.schemas import Habit


def resolve_timezone(timezone_name: str | None) -> pytz.BaseTzInfo:
    """Часовой пояс пользователя (UTC как фолбэк при пустом/неизвестном значении).

    Повторяет поведение бота: «сегодня» считается в личном поясе пользователя.
    """
    try:
        return pytz.timezone(timezone_name) if timezone_name else pytz.utc
    except Exception:  # noqa: BLE001 — неизвестная зона не должна ронять запрос
        return pytz.utc


def user_today(user: User) -> date:
    """Текущая дата в часовом поясе пользователя (как у бота)."""
    return datetime.now(resolve_timezone(user.timezone)).date()


async def build_history(repo: Repository, task_id: int, today: date) -> list[bool]:
    """История выполнения задачи за последние `YEAR_GRID_DAYS` дней (старое → сегодня).

    True — в этот день есть лог со статусом `done`, иначе False. Та же логика
    «закрашен = done», что и в PNG-баннерах бота, но окно — год вместо 30 дней.
    """
    logs = await repo.get_logs_for_task(task_id)
    done_dates = {log.scheduled_date for log in logs if log.status == TaskStatus.done}
    start = today - timedelta(days=YEAR_GRID_DAYS - 1)
    return [(start + timedelta(days=offset)) in done_dates for offset in range(YEAR_GRID_DAYS)]


async def build_habit(repo: Repository, task: Task, today: date) -> Habit:
    """Собрать схему `Habit` по задаче: название, отметка за сегодня и годовая история."""
    history = await build_history(repo, task.id, today)
    # Последний элемент истории — сегодняшний день, поэтому он же определяет done_today.
    return Habit(id=task.id, name=task.name, done_today=history[-1], history=history)


async def list_habits(repo: Repository, user: User) -> list[Habit]:
    """Все активные привычки пользователя с историей выполнения (для `GET /tasks`)."""
    today = user_today(user)
    tasks = await repo.get_active_tasks(user.telegram_id)
    return [await build_habit(repo, task, today) for task in tasks]


async def toggle_today(repo: Repository, user: User, task: Task) -> Habit:
    """Переключить отметку выполнения задачи за сегодня и вернуть обновлённую привычку.

    Отметка применяется относительно статуса в БД: `done` ↔ `pending`. Лог за
    сегодня создаётся при необходимости.
    """
    today = user_today(user)
    log = await repo.get_or_create_log(task.id, user.telegram_id, today)
    target = TaskStatus.pending if log.status == TaskStatus.done else TaskStatus.done
    await repo.set_log_status(log, target)
    return await build_habit(repo, task, today)
