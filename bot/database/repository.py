"""Репозиторий — единственная точка доступа к БД (Repository pattern).

Хендлеры и сервисы не пишут SQL напрямую: вся работа с данными идёт через
методы этого класса. Каждый экземпляр привязан к одной async-сессии.
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import WEEKDAYS
from bot.database.models import (
    FrequencyType,
    Task,
    TaskLog,
    TaskStatus,
    User,
)

# Индекс «код дня недели -> позиция Python (Monday == 0)».
_WEEKDAY_CODES: tuple[str, ...] = tuple(code for code, _, _ in WEEKDAYS)


class Repository:
    """CRUD-методы для User, Task и TaskLog поверх одной сессии."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    #  Users
    # ------------------------------------------------------------------ #

    async def get_user(self, telegram_id: int) -> User | None:
        """Вернуть пользователя по telegram_id или None."""
        return await self.session.get(User, telegram_id)

    async def create_user(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
    ) -> User:
        """Создать незарегистрированного пользователя."""
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            is_registered=False,
            is_active=True,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_or_create_user(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
    ) -> tuple[User, bool]:
        """Вернуть пользователя, создав его при отсутствии. Возвращает (user, created).

        Безопасно к гонке: при старте polling Telegram может прислать пачку
        накопившихся апдейтов, которые обрабатываются параллельно. Вставку
        оборачиваем в SAVEPOINT и при конфликте уникальности перечитываем
        уже созданную параллельным апдейтом запись.
        """
        user = await self.get_user(telegram_id)
        if user is not None:
            return user, False
        try:
            async with self.session.begin_nested():
                user = User(
                    telegram_id=telegram_id,
                    username=username,
                    first_name=first_name,
                    is_registered=False,
                    is_active=True,
                )
                self.session.add(user)
                await self.session.flush()
            return user, True
        except IntegrityError:
            # Запись успели создать в параллельном апдейте — перечитываем.
            user = await self.get_user(telegram_id)
            if user is None:  # крайне маловероятно
                raise
            return user, False

    async def reset_onboarding(self, user: User) -> None:
        """Сбросить данные онбординга (для повторного /start)."""
        user.morning_time = None
        user.evening_time = None
        user.timezone = None
        user.is_registered = False
        await self.session.flush()

    async def update_profile(
        self,
        user: User,
        username: str | None,
        first_name: str | None,
    ) -> None:
        """Обновить @username и имя (могут меняться между сессиями)."""
        user.username = username
        user.first_name = first_name
        await self.session.flush()

    async def set_morning_time(self, user: User, value: time) -> None:
        """Установить время утреннего дайджеста."""
        user.morning_time = value
        await self.session.flush()

    async def set_evening_time(self, user: User, value: time) -> None:
        """Установить время вечернего итога."""
        user.evening_time = value
        await self.session.flush()

    async def set_timezone(self, user: User, value: str) -> None:
        """Установить часовой пояс пользователя."""
        user.timezone = value
        await self.session.flush()

    async def complete_registration(self, user: User) -> None:
        """Отметить онбординг завершённым."""
        user.is_registered = True
        user.is_active = True
        await self.session.flush()

    async def set_active(self, telegram_id: int, value: bool) -> None:
        """Пометить пользователя активным/неактивным (например, заблокировал бота)."""
        await self.session.execute(
            update(User).where(User.telegram_id == telegram_id).values(is_active=value)
        )
        await self.session.flush()

    async def get_registered_users(self) -> list[User]:
        """Вернуть всех зарегистрированных пользователей (для восстановления jobs)."""
        result = await self.session.execute(
            select(User).where(User.is_registered.is_(True))
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------ #
    #  Tasks
    # ------------------------------------------------------------------ #

    async def create_task(
        self,
        user_id: int,
        name: str,
        frequency_type: FrequencyType,
        days: str | None = None,
        one_time_date: date | None = None,
        reminder_time: time | None = None,
    ) -> Task:
        """Создать активную задачу."""
        task = Task(
            user_id=user_id,
            name=name,
            frequency_type=frequency_type,
            days=days,
            one_time_date=one_time_date,
            reminder_time=reminder_time,
            is_active=True,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def task_name_exists(self, user_id: int, name: str) -> bool:
        """Есть ли у пользователя активная задача с таким именем (без учёта регистра).

        Сравнение делается в Python: SQLite `lower()` не приводит к нижнему
        регистру кириллицу, поэтому полагаться на него нельзя.
        """
        target = name.strip().lower()
        result = await self.session.execute(
            select(Task.name).where(
                Task.user_id == user_id, Task.is_active.is_(True)
            )
        )
        return any((n or "").strip().lower() == target for n in result.scalars().all())

    async def get_task(self, task_id: int) -> Task | None:
        """Вернуть задачу по id (без фильтра активности)."""
        return await self.session.get(Task, task_id)

    async def get_active_task(self, task_id: int, user_id: int) -> Task | None:
        """Вернуть активную задачу пользователя по id или None."""
        result = await self.session.execute(
            select(Task).where(
                Task.id == task_id,
                Task.user_id == user_id,
                Task.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_tasks(self, user_id: int) -> list[Task]:
        """Вернуть все активные задачи пользователя, отсортированные по id."""
        result = await self.session.execute(
            select(Task)
            .where(Task.user_id == user_id, Task.is_active.is_(True))
            .order_by(Task.id)
        )
        return list(result.scalars().all())

    async def get_tasks_due_on(self, user_id: int, target_date: date) -> list[Task]:
        """Вернуть активные задачи, запланированные на указанную дату.

        Фильтрация по типу частоты выполняется в Python:
        - daily         — всегда;
        - specific_days — если код дня недели присутствует в task.days;
        - one_time      — если one_time_date совпадает с целевой датой.
        """
        tasks = await self.get_active_tasks(user_id)
        weekday_code = _WEEKDAY_CODES[target_date.weekday()]
        due: list[Task] = []
        for task in tasks:
            if task.frequency_type == FrequencyType.daily:
                due.append(task)
            elif task.frequency_type == FrequencyType.specific_days:
                selected = (task.days or "").split(",")
                if weekday_code in selected:
                    due.append(task)
            elif task.frequency_type == FrequencyType.one_time:
                if task.one_time_date == target_date:
                    due.append(task)
        return due

    async def soft_delete_task(self, task: Task) -> None:
        """Мягкое удаление: пометить задачу неактивной."""
        task.is_active = False
        await self.session.flush()

    async def get_forgettable_one_time_tasks(self, cutoff_date: date) -> list[Task]:
        """Активные одноразовые задачи с датой раньше cutoff_date.

        Через сутки после истечения срока одноразовая задача «забывается»
        (деактивируется): передаётся cutoff = today - 1 день, под условие
        попадают задачи с one_time_date < cutoff (т.е. старше, чем вчера).
        """
        result = await self.session.execute(
            select(Task).where(
                Task.is_active.is_(True),
                Task.frequency_type == FrequencyType.one_time,
                Task.one_time_date < cutoff_date,
            )
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------ #
    #  TaskLogs
    # ------------------------------------------------------------------ #

    async def get_log(self, task_id: int, scheduled_date: date) -> TaskLog | None:
        """Вернуть запись лога задачи на дату или None."""
        result = await self.session.execute(
            select(TaskLog).where(
                TaskLog.task_id == task_id,
                TaskLog.scheduled_date == scheduled_date,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create_log(
        self,
        task_id: int,
        user_id: int,
        scheduled_date: date,
    ) -> TaskLog:
        """Вернуть лог на дату, создав его со статусом pending при отсутствии."""
        log = await self.get_log(task_id, scheduled_date)
        if log is not None:
            return log
        log = TaskLog(
            task_id=task_id,
            user_id=user_id,
            scheduled_date=scheduled_date,
            status=TaskStatus.pending,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def set_log_status(self, log: TaskLog, status: TaskStatus) -> None:
        """Установить статус лога и зафиксировать момент отметки."""
        log.status = status
        log.marked_at = datetime.utcnow()
        await self.session.flush()

    async def get_logs_for_task(self, task_id: int) -> list[TaskLog]:
        """Вернуть все логи задачи, отсортированные по дате (DESC)."""
        result = await self.session.execute(
            select(TaskLog)
            .where(TaskLog.task_id == task_id)
            .order_by(TaskLog.scheduled_date.desc())
        )
        return list(result.scalars().all())

    async def get_logs_for_date(self, user_id: int, scheduled_date: date) -> list[TaskLog]:
        """Вернуть все логи пользователя за конкретную дату."""
        result = await self.session.execute(
            select(TaskLog).where(
                TaskLog.user_id == user_id,
                TaskLog.scheduled_date == scheduled_date,
            )
        )
        return list(result.scalars().all())

    async def get_expired_pending_logs(self, before_date: date) -> list[TaskLog]:
        """Вернуть pending-логи с датой раньше указанной (кандидаты на missed)."""
        result = await self.session.execute(
            select(TaskLog).where(
                TaskLog.status == TaskStatus.pending,
                TaskLog.scheduled_date < before_date,
            )
        )
        return list(result.scalars().all())

    async def mark_logs_missed(self, logs: list[TaskLog]) -> None:
        """Перевести переданные логи в статус missed."""
        for log in logs:
            log.status = TaskStatus.missed
        await self.session.flush()
