"""Окружение Alembic (async).

URL подключения берётся из .env через `bot.config.load_config`, метаданные —
из `bot.database.base.Base`. Поддерживаются offline- и online-режимы.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# Импортируем модели, чтобы они зарегистрировались в Base.metadata.
from bot.config import load_config
from bot.database import models  # noqa: F401
from bot.database.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Реальный URL из .env.
DB_URL = load_config().database_url
config.set_main_option("sqlalchemy.url", DB_URL)


def run_migrations_offline() -> None:
    """Запуск миграций в offline-режиме (генерация SQL без подключения)."""
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Выполнить миграции на переданном соединении."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # batch-режим нужен для ALTER в SQLite
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Запуск миграций в online-режиме через async-движок."""
    connectable = create_async_engine(DB_URL)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
