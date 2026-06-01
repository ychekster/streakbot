"""Управление задачами APScheduler: дайджесты, напоминания, истечение задач.

Все задания планировщика переживают рестарт: при старте бота вызывается
`restore_jobs`, которая заново создаёт jobs для всех зарегистрированных
пользователей и их активных задач по данным из БД.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytz
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.constants import TEXTS
from bot.database.models import (
    FrequencyType,
    Task,
    TaskStatus,
    User,
)
from bot.database.repository import Repository
from bot.utils.validators import escape_md

# Идентификатор глобального задания истечения просроченных логов.
EXPIRE_JOB_ID = "global_expire_tasks"


class SchedulerService:
    """Обёртка над AsyncIOScheduler со всей логикой уведомлений StreakBot."""

    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.scheduler = scheduler
        self.bot = bot
        self.session_factory = session_factory

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
        elif task.frequency_type == FrequencyType.one_time and task.one_time_date:
            run_dt = tz.localize(
                datetime.combine(task.one_time_date, task.reminder_time)
            )
            # Если момент уже в прошлом — напоминание не имеет смысла.
            if run_dt <= datetime.now(tz):
                return
            trigger = DateTrigger(run_date=run_dt)
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

    async def _safe_send(self, user_id: int, text: str) -> None:
        """Отправить сообщение, обработав блокировку бота пользователем."""
        try:
            await self.bot.send_message(user_id, text)
        except TelegramForbiddenError:
            logger.info("User {} blocked the bot — marking inactive", user_id)
            async with self.session_factory() as session:
                repo = Repository(session)
                await repo.set_active(user_id, False)
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send message to {}: {}", user_id, exc)

    async def send_morning_digest(self, user_id: int) -> None:
        """Утренний дайджест: задачи на сегодня + блок просроченных + pending-логи."""
        # Ленивый импорт, чтобы избежать цикла импорта с хендлерами.
        from bot.handlers.today import build_morning_digest

        async with self.session_factory() as session:
            repo = Repository(session)
            user = await repo.get_user(user_id)
            if user is None or not user.is_active:
                return
            text = await build_morning_digest(repo, user)
            await session.commit()
        await self._safe_send(user_id, text)
        logger.info("Morning digest sent to user {}", user_id)

    async def send_evening_digest(self, user_id: int) -> None:
        """Вечерний итог: выполненные против оставшихся."""
        async with self.session_factory() as session:
            repo = Repository(session)
            user = await repo.get_user(user_id)
            if user is None or not user.is_active:
                return
            tz = self._user_tz(user)
            today = datetime.now(tz).date()
            logs = await repo.get_logs_for_date(user_id, today)
            text = await self._render_evening(repo, logs)
            await session.commit()
        await self._safe_send(user_id, text)
        logger.info("Evening digest sent to user {}", user_id)

    async def _render_evening(self, repo: Repository, logs: list) -> str:
        """Собрать текст вечернего итога из логов за день."""
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

    async def send_reminder(self, task_id: int) -> None:
        """Отправить напоминание по конкретной задаче."""
        async with self.session_factory() as session:
            repo = Repository(session)
            task = await repo.get_task(task_id)
            if task is None or not task.is_active:
                return
            user = await repo.get_user(task.user_id)
            if user is None or not user.is_active:
                return
            text = escape_md(TEXTS["reminder_text"].format(name=task.name))
            user_id = task.user_id
        await self._safe_send(user_id, text)
        logger.info("Reminder sent for task {}", task_id)

    async def check_and_expire_tasks(self) -> None:
        """Перевести просроченные pending-логи в missed и «забыть» старые one_time.

        Одноразовая задача забывается (деактивируется) через сутки после
        истечения срока: на дату D она ещё видна в дайджесте дня D+1 как
        просроченная, а начиная с D+2 — деактивируется и её напоминание снимается.
        """
        today_utc = datetime.now(pytz.utc).date()
        forget_cutoff = today_utc - timedelta(days=1)
        async with self.session_factory() as session:
            repo = Repository(session)
            expired = await repo.get_expired_pending_logs(today_utc)
            if expired:
                await repo.mark_logs_missed(expired)

            forgettable = await repo.get_forgettable_one_time_tasks(forget_cutoff)
            for task in forgettable:
                await repo.soft_delete_task(task)
                self.remove_task_reminder_job(task.id)

            await session.commit()
        logger.info(
            "Expired {} pending logs, forgot {} one-time tasks",
            len(expired), len(forgettable),
        )
