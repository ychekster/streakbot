"""Управление задачами APScheduler: дайджесты, напоминания, истечение задач.

Все задания планировщика переживают рестарт: при старте бота вызывается
`restore_jobs`, которая заново создаёт jobs для всех зарегистрированных
пользователей и их активных задач по данным из БД.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.models import FrequencyType, Task, User
from bot.database.repository import Repository
from bot.keyboards.builders import REMOVE_KB

# Идентификатор глобального задания истечения просроченных логов.
EXPIRE_JOB_ID = "global_expire_tasks"

# Окно активности: если пользователь взаимодействовал с ботом менее 5 минут
# назад, отправку дайджеста откладываем ровно на остаток до 5 минут и проверяем
# снова. Максимум 3 отложки — на 4-й итерации уведомление уходит безусловно.
_ACTIVITY_WINDOW = timedelta(minutes=5)
_MAX_POSTPONES = 3


class SchedulerService:
    """Обёртка над AsyncIOScheduler со всей логикой уведомлений StreakBot."""

    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        storage: BaseStorage,
        activity: dict[int, datetime],
        digest_sent: dict[tuple[int, str], "date"],
    ) -> None:
        self.scheduler = scheduler
        self.bot = bot
        self.session_factory = session_factory
        self.storage = storage          # для сброса FSM-состояния пользователя
        self.activity = activity         # user_id -> время последней активности (UTC)
        # (user_id, 'morning'|'evening') -> дата последнего отправленного дайджеста.
        # Если дайджест за сегодня уже отправлен, смена настроек его не переотправит.
        self.digest_sent = digest_sent

    # ------------------------------------------------------------------ #
    #  Жизненный цикл
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Запустить планировщик и зарегистрировать глобальные задания."""
        # Ежедневная проверка просроченных задач в 00:05 UTC.
        self.scheduler.add_job(
            self.check_and_expire_tasks,
            trigger=CronTrigger(hour=0, minute=5, timezone=pytz.utc),
            id=EXPIRE_JOB_ID,
            replace_existing=True,
        )
        self.scheduler.start()

    async def shutdown(self) -> None:
        """Корректно остановить планировщик."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    @staticmethod
    def _user_tz(user: User) -> pytz.BaseTzInfo:
        """Вернуть таймзону пользователя (UTC как безопасный фолбэк)."""
        try:
            return pytz.timezone(user.timezone) if user.timezone else pytz.utc
        except Exception:  # noqa: BLE001
            return pytz.utc

    # ------------------------------------------------------------------ #
    #  Регистрация jobs
    # ------------------------------------------------------------------ #

    def setup_user_jobs(self, user: User) -> None:
        """Создать/обновить cron-задания утреннего и вечернего дайджестов."""
        tz = self._user_tz(user)
        morning_id = f"morning_{user.telegram_id}"
        evening_id = f"evening_{user.telegram_id}"

        if user.morning_time is not None:
            self.scheduler.add_job(
                self.send_morning_digest,
                trigger=CronTrigger(
                    hour=user.morning_time.hour,
                    minute=user.morning_time.minute,
                    timezone=tz,
                ),
                id=morning_id,
                replace_existing=True,
                args=[user.telegram_id],
            )
        if user.evening_time is not None:
            self.scheduler.add_job(
                self.send_evening_digest,
                trigger=CronTrigger(
                    hour=user.evening_time.hour,
                    minute=user.evening_time.minute,
                    timezone=tz,
                ),
                id=evening_id,
                replace_existing=True,
                args=[user.telegram_id],
            )
        logger.info("Scheduler jobs set up for user {}", user.telegram_id)

    def add_task_reminder_job(self, user: User, task: Task) -> None:
        """Добавить job напоминания для задачи по её расписанию."""
        if task.reminder_time is None:
            return
        tz = self._user_tz(user)
        job_id = f"reminder_{task.id}"

        if task.frequency_type == FrequencyType.daily:
            trigger = CronTrigger(
                hour=task.reminder_time.hour,
                minute=task.reminder_time.minute,
                timezone=tz,
            )
        elif task.frequency_type == FrequencyType.specific_days:
            trigger = CronTrigger(
                day_of_week=task.days,  # коды 'mon,wed' совпадают с форматом APScheduler
                hour=task.reminder_time.hour,
                minute=task.reminder_time.minute,
                timezone=tz,
            )
        else:
            return

        self.scheduler.add_job(
            self.send_reminder,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            args=[task.id],
        )
        logger.info("Reminder job added for task {}", task.id)

    def remove_task_reminder_job(self, task_id: int) -> None:
        """Удалить job напоминания при мягком удалении задачи."""
        try:
            self.scheduler.remove_job(f"reminder_{task_id}")
            logger.info("Reminder job removed for task {}", task_id)
        except JobLookupError:
            pass

    async def restore_jobs(self) -> None:
        """Восстановить все jobs после рестарта по данным из БД."""
        async with self.session_factory() as session:
            repo = Repository(session)
            users = await repo.get_registered_users()
            for user in users:
                self.setup_user_jobs(user)
                tasks = await repo.get_active_tasks(user.telegram_id)
                for task in tasks:
                    if task.reminder_time is not None:
                        self.add_task_reminder_job(user, task)
        logger.info("Restored scheduler jobs for {} users", len(users))

    # ------------------------------------------------------------------ #
    #  Отправка уведомлений
    # ------------------------------------------------------------------ #

    def _maybe_postpone(self, func, user_id: int, prefix: str, attempt: int) -> bool:
        """Отложить дайджест, если пользователь активен (<5 мин). True — отложили.

        Задержка — ровно остаток до 5 минут от ПОСЛЕДНЕЙ активности (каждый раз
        пересчитывается заново, отложки не суммируются). Максимум _MAX_POSTPONES
        отложек; дальше уведомление уходит безусловно.
        """
        if attempt >= _MAX_POSTPONES:
            return False
        last = self.activity.get(user_id)
        if last is None:
            return False
        gap = datetime.now(timezone.utc) - last
        if gap >= _ACTIVITY_WINDOW:
            return False
        delay = _ACTIVITY_WINDOW - gap
        run_at = datetime.now(timezone.utc) + delay
        self.scheduler.add_job(
            func,
            trigger=DateTrigger(run_date=run_at),
            id=f"{prefix}_retry_{user_id}",
            replace_existing=True,
            args=[user_id, attempt + 1],
        )
        logger.info(
            "Digest '{}' for user {} postponed {:.0f}s (attempt {}/{})",
            prefix, user_id, delay.total_seconds(), attempt + 1, _MAX_POSTPONES,
        )
        return True

    async def _reset_user_state(self, user_id: int) -> None:
        """Сбросить FSM-состояние и данные пользователя (приватный чат)."""
        key = StorageKey(bot_id=self.bot.id, chat_id=user_id, user_id=user_id)
        await self.storage.set_state(key, None)
        await self.storage.set_data(key, {})

    async def _safe_send(self, user_id: int, text: str, reply_markup=None) -> None:
        """Отправить сообщение, обработав блокировку бота пользователем."""
        try:
            await self.bot.send_message(user_id, text, reply_markup=reply_markup)
        except TelegramForbiddenError:
            logger.info("User {} blocked the bot — marking inactive", user_id)
            async with self.session_factory() as session:
                repo = Repository(session)
                await repo.set_active(user_id, False)
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send message to {}: {}", user_id, exc)

    async def send_morning_digest(self, user_id: int, attempt: int = 0) -> None:
        """Утренний дайджест: задачи на сегодня + блок просроченных + pending-логи.

        Не отправляется повторно, если уже был отправлен сегодня (смена настроек
        не переотправляет). Откладывается при недавней активности (см.
        `_maybe_postpone`). Перед отправкой сбрасывает FSM-состояние и убирает
        reply-клавиатуру (или показывает inline-кнопку отметки просроченных).
        """
        # Ленивый импорт, чтобы избежать цикла импорта с хендлерами.
        from bot.handlers.today import build_morning_digest

        async with self.session_factory() as session:
            repo = Repository(session)
            user = await repo.get_user(user_id)
            if user is None or not user.is_active:
                return
            today = datetime.now(self._user_tz(user)).date()
            if self.digest_sent.get((user_id, "morning")) == today:
                return
            if self._maybe_postpone(self.send_morning_digest, user_id, "morning", attempt):
                return
            text, keyboard = await build_morning_digest(repo, user)
            await session.commit()

        await self._reset_user_state(user_id)
        await self._safe_send(user_id, text, reply_markup=keyboard or REMOVE_KB)
        self.digest_sent[(user_id, "morning")] = today
        logger.info("Morning digest sent to user {}", user_id)

    async def send_evening_digest(self, user_id: int, attempt: int = 0) -> None:
        """Вечерний итог + inline-кнопка отметки выполненных.

        Та же логика «не отправлять дважды за день», откладывания и сброса
        состояния, что и у утреннего дайджеста.
        """
        from bot.handlers.today import build_evening_digest

        async with self.session_factory() as session:
            repo = Repository(session)
            user = await repo.get_user(user_id)
            if user is None or not user.is_active:
                return
            today = datetime.now(self._user_tz(user)).date()
            if self.digest_sent.get((user_id, "evening")) == today:
                return
            if self._maybe_postpone(self.send_evening_digest, user_id, "evening", attempt):
                return
            text, keyboard = await build_evening_digest(repo, user)
            await session.commit()

        await self._reset_user_state(user_id)
        await self._safe_send(user_id, text, reply_markup=keyboard or REMOVE_KB)
        self.digest_sent[(user_id, "evening")] = today
        logger.info("Evening digest sent to user {}", user_id)

    async def send_reminder(self, task_id: int) -> None:
        """Отправить напоминание по задаче (с кнопкой-галочкой отметки, как в /today)."""
        # Ленивый импорт, чтобы избежать цикла импорта с хендлерами.
        from bot.handlers.today import build_reminder

        async with self.session_factory() as session:
            repo = Repository(session)
            task = await repo.get_task(task_id)
            if task is None or not task.is_active:
                return
            user = await repo.get_user(task.user_id)
            if user is None or not user.is_active:
                return
            target_date = datetime.now(self._user_tz(user)).date()
            text, keyboard = await build_reminder(repo, task, target_date)
            user_id = task.user_id
        await self._safe_send(user_id, text, reply_markup=keyboard)
        logger.info("Reminder sent for task {}", task_id)

    async def check_and_expire_tasks(self) -> None:
        """Перевести просроченные pending-логи в статус missed.

        Логи с датой раньше сегодняшней (UTC), оставшиеся в pending, считаются
        окончательно пропущенными и переводятся в missed.
        """
        today_utc = datetime.now(pytz.utc).date()
        async with self.session_factory() as session:
            repo = Repository(session)
            expired = await repo.get_expired_pending_logs(today_utc)
            if expired:
                await repo.mark_logs_missed(expired)
            await session.commit()
        logger.info("Expired {} pending logs", len(expired))
