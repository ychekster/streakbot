"""Базовая инфраструктура SQLAlchemy: DeclarativeBase, async-движок, фабрика сессий.

Движок создаётся лениво (через `init_engine`), а не на уровне модуля — чтобы
конфигурация (DATABASE_URL) была прочитана до его создания и чтобы импорт пакета
не имел побочных эффектов.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Общий декларативный базовый класс для всех ORM-моделей."""


def init_engine(database_url: str, echo: bool = False) -> AsyncEngine:
    """Создать async-движок SQLAlchemy для указанной строки подключения."""
    return create_async_engine(database_url, echo=echo, future=True)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Построить фабрику async-сессий, привязанную к движку."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def create_tables(engine: AsyncEngine) -> None:
    """Создать все таблицы по метаданным моделей (idempotent, checkfirst=True).

    Импортируем модели здесь, чтобы они зарегистрировались в `Base.metadata`
    до вызова `create_all`.
    """
    from bot.database import models  # noqa: F401  (важен сам факт импорта)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
