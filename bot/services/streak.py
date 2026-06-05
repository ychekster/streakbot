"""Подсчёт стриков задачи.

Стрик нигде не хранится — он каждый раз вычисляется по записям TaskLog.
Вся работа с БД идёт через Repository.
"""

from __future__ import annotations

from datetime import date, timedelta

from bot.constants import STATS_GRID_DAYS
from bot.database.models import TaskStatus
from bot.database.repository import Repository


async def get_current_streak(repo: Repository, task_id: int) -> int:
    """Вернуть текущий стрик — число подряд идущих `done` с самого недавнего дня.

    Алгоритм:
    1. Берём логи задачи, отсортированные по дате по убыванию.
    2. Идём от самого свежего, пока статус == done.
    3. Первый не-done (missed / skipped / pending) обрывает стрик.
    """
    logs = await repo.get_logs_for_task(task_id)  # отсортированы по дате DESC
    streak = 0
    for log in logs:
        if log.status == TaskStatus.done:
            streak += 1
        else:
            break
    return streak


async def get_max_streak(repo: Repository, task_id: int) -> int:
    """Вернуть максимальный стрик — самую длинную серию подряд идущих `done`."""
    logs = await repo.get_logs_for_task(task_id)
    # Идём в хронологическом порядке (логи приходят DESC — переворачиваем).
    best = 0
    current = 0
    for log in reversed(logs):
        if log.status == TaskStatus.done:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


async def get_last_30_days(repo: Repository, task_id: int, today: date) -> list[bool]:
    """Статусы выполнения задачи за последние 30 календарных дней (старое → сегодня).

    Возвращает список из `STATS_GRID_DAYS` (30) булевых значений: True, если в этот
    день есть лог со статусом `done`, иначе False. Индекс 0 — самый старый день
    (today − 29), индекс 29 — сегодня. Используется для сетки в картинке /stats:
    True → синяя ячейка, False → светлая.
    """
    logs = await repo.get_logs_for_task(task_id)
    done_dates = {log.scheduled_date for log in logs if log.status == TaskStatus.done}
    start = today - timedelta(days=STATS_GRID_DAYS - 1)
    return [(start + timedelta(days=offset)) in done_dates for offset in range(STATS_GRID_DAYS)]
