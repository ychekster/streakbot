"""Подсчёт стриков задачи.

Стрик нигде не хранится — он каждый раз вычисляется по записям TaskLog.
Вся работа с БД идёт через Repository.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytz

from bot.constants import RECOVERY_DEADLINE_HOUR, STATS_GRID_DAYS
from bot.database.models import TaskStatus
from bot.database.repository import Repository


def _recovery_deadline(scheduled_date: date, tz: pytz.BaseTzInfo) -> datetime:
    """Дедлайн восстановления дня — 12:00 следующего дня в часовом поясе пользователя.

    До этого момента невыполненный день ещё можно отметить (утренний дайджест,
    напоминание), поэтому он не считается окончательно пропущенным.
    """
    deadline_naive = datetime.combine(
        scheduled_date + timedelta(days=1), time(RECOVERY_DEADLINE_HOUR, 0)
    )
    return tz.localize(deadline_naive)


async def get_current_streak(repo: Repository, task_id: int, tz: pytz.BaseTzInfo) -> int:
    """Вернуть текущий стрик по последнему ОКОНЧАТЕЛЬНО ЗАКРЫТОМУ дню.

    Считаем серию подряд идущих `done`, начиная с самого свежего лога, но с учётом
    окна восстановления: ещё не закрытый день (его дедлайн восстановления — 12:00
    следующего дня по поясу пользователя — не наступил) не считается провалом.

    Алгоритм идёт от самого свежего лога:
    - `done` — увеличивает стрик;
    - не-`done` ВЕДУЩИЙ день (стрик ещё не начали считать), который можно
      восстановить, — пропускается, не обрывая стрик. Это сегодняшний день (дедлайн
      завтра в 12:00) и вчерашний до 12:00 (дедлайн сегодня в 12:00): они «в
      процессе», их ещё можно отметить;
    - любой другой не-`done` день закрыт окончательно (срок восстановления прошёл
      либо стрик уже считается) — он обрывает стрик.

    Поэтому сегодняшний невыполненный день и вчерашний до 12:00 стрик не обнуляют, а
    вчерашний после 12:00, так и оставшийся невыполненным, — обнуляет. Ведущие
    «в процессе» дни пропускаются только до первого зачтённого `done`, поэтому
    текущий стрик всегда не превышает рекорд (`get_max_streak`).
    """
    logs = await repo.get_logs_for_task(task_id)  # отсортированы по дате DESC
    now = datetime.now(tz)
    streak = 0
    counting = False  # начали ли мы уже считать серию done
    for log in logs:
        if log.status == TaskStatus.done:
            streak += 1
            counting = True
            continue
        # День не выполнен. Пока стрик не начат, ведущий «ещё не закрытый» день
        # (сегодня / вчера до 12:00) пропускаем — его можно восстановить.
        if not counting and now < _recovery_deadline(log.scheduled_date, tz):
            continue
        # Иначе день закрыт окончательно как пропущенный — стрик прерывается.
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
