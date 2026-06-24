"""Подключение к общей базе данных бота.

Движок и фабрика сессий строятся теми же функциями, что и у бота
(`bot.database.base`), поверх того же `DATABASE_URL`. Таблицы здесь НЕ создаются:
схемой владеет бот (через `create_tables`/alembic), API только читает и пишет.
"""

from __future__ import annotations

from bot.database.base import build_session_factory, init_engine


class Database:
    """Async-движок и фабрика сессий к общей с ботом базе."""

    def __init__(self, database_url: str) -> None:
        self._engine = init_engine(database_url)
        self.session_factory = build_session_factory(self._engine)

    async def dispose(self) -> None:
        """Закрыть пул соединений при остановке приложения."""
        await self._engine.dispose()
