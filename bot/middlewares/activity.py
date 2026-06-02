"""Middleware учёта времени последней активности пользователя.

Записывает в общий словарь момент последнего апдейта от каждого пользователя.
Планировщик использует это, чтобы откладывать утренний/вечерний дайджест, если
пользователь был активен менее 5 минут назад. Данные хранятся в памяти и
теряются при рестарте (после рестарта активность считается отсутствующей).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Update


class ActivityMiddleware(BaseMiddleware):
    """Фиксирует время последнего взаимодействия пользователя с ботом."""

    def __init__(self, activity: dict[int, datetime]) -> None:
        self.activity = activity

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is not None:
            self.activity[tg_user.id] = datetime.now(timezone.utc)
        return await handler(event, data)
