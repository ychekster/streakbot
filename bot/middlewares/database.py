"""DI-middleware: на каждый апдейт открывает async-сессию и репозиторий.

Сессия и репозиторий пробрасываются в хендлеры через `data` (kwargs `session`
и `repo`). По завершении обработки изменения коммитятся, при исключении —
откатываются. Хендлеры не создают сессии вручную.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.database.repository import Repository


class DatabaseMiddleware(BaseMiddleware):
    """Открывает сессию БД на время обработки апдейта и внедряет repo/session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.session_factory() as session:
            data["session"] = session
            data["repo"] = Repository(session)
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
