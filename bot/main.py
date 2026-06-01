"""Точка входа StreakBot.

Запуск: ``python -m bot.main`` (после заполнения .env).

Последовательность: конфиг → логирование → БД и таблицы → планировщик →
Bot/Dispatcher → middlewares → роутеры → восстановление jobs → polling.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytz
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from bot.config import Config, load_config
from bot.constants import TEXTS
from bot.database.base import build_session_factory, create_tables, init_engine
from bot.handlers import (
    add_task,
    cancel,
    delete_task,
    onboarding,
    settings,
    start,
    stats,
    today,
)
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.registration import RegistrationMiddleware
from bot.services.scheduler import SchedulerService
from bot.utils.validators import escape_md


def setup_logging(config: Config) -> None:
    """Настроить loguru: вывод в stderr и ротация в файл."""
    logger.remove()
    logger.add(sys.stderr, level=config.log_level, enqueue=True)
    log_path = Path(config.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        config.log_file,
        level=config.log_level,
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
    )


def register_routers(dp: Dispatcher) -> None:
    """Подключить роутеры в порядке приоритета.

    `cancel` — первым (перехватывает /cancel в любом состоянии). `onboarding`
    идёт перед `start`, чтобы во время регистрации команды (кроме /start, который
    онбординг намеренно пропускает) перехватывались и приводили к повтору шага.
    """
    dp.include_router(cancel.router)
    dp.include_router(onboarding.router)
    dp.include_router(start.router)
    dp.include_router(add_task.router)
    dp.include_router(today.router)
    dp.include_router(delete_task.router)
    dp.include_router(stats.router)
    dp.include_router(settings.router)


def register_error_handler(dp: Dispatcher) -> None:
    """Глобальный обработчик ошибок: логировать и не показывать трейс пользователю."""

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        logger.opt(exception=event.exception).error(
            "Unhandled error while processing update: {}", event.exception
        )
        update = event.update
        try:
            if update.message is not None:
                await update.message.answer(escape_md(TEXTS["internal_error"]))
            elif update.callback_query is not None:
                await update.callback_query.answer(
                    TEXTS["internal_error"], show_alert=True
                )
        except Exception:  # noqa: BLE001 — уведомление не критично
            pass
        return True


async def set_bot_commands(bot: Bot) -> None:
    """Зарегистрировать меню команд бота в Telegram (не критично при сбое)."""
    commands = [
        BotCommand(command="today", description="Задачи на сегодня"),
        BotCommand(command="done", description="Выполненные за сегодня"),
        BotCommand(command="add", description="Добавить задачу"),
        BotCommand(command="delete", description="Удалить задачу"),
        BotCommand(command="stats", description="Статистика и стрики"),
        BotCommand(command="settings", description="Настройки"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as exc:  # noqa: BLE001 — меню команд не критично для работы
        logger.warning("Could not set bot commands: {}", exc)


async def main() -> None:
    """Инициализировать и запустить бота."""
    config = load_config()
    setup_logging(config)
    logger.info("Starting StreakBot...")

    # БД: движок, фабрика сессий, таблицы.
    engine = init_engine(config.database_url)
    session_factory = build_session_factory(engine)
    await create_tables(engine)

    # Bot, Dispatcher, планировщик.
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2),
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    scheduler = AsyncIOScheduler(timezone=pytz.utc)
    scheduler_service = SchedulerService(scheduler, bot, session_factory)

    # Проброс зависимостей в хендлеры через workflow_data.
    dp["config"] = config
    dp["scheduler"] = scheduler_service

    # Middlewares (outer на уровне update): сначала сессия, затем регистрация.
    dp.update.outer_middleware(DatabaseMiddleware(session_factory))
    dp.update.outer_middleware(RegistrationMiddleware())

    register_routers(dp)
    register_error_handler(dp)

    # Планировщик: старт и восстановление jobs для зарегистрированных юзеров.
    scheduler_service.start()
    await scheduler_service.restore_jobs()

    await set_bot_commands(bot)
    logger.info("StreakBot is up and polling")

    try:
        await dp.start_polling(bot)
    finally:
        await scheduler_service.shutdown()
        await bot.session.close()
        await engine.dispose()
        logger.info("StreakBot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested")
