"""Middleware проверки регистрации. Применяется ко всем апдейтам.

Логика (по спецификации):
1. `/start` (с любым payload) или `/cancel` — пропускаем, хендлер разберётся сам.
2. Пользователь не найден в БД — отправляем `not_registered`, блокируем апдейт.
3. Пользователь найден, is_registered=False:
   - есть активное состояние онбординга — пропускаем (онбординг-роутер обработает);
   - состояния нет (например, потеряно после рестарта) — сбрасываем и
     перезапускаем онбординг с приветствия.
4. Пользователь зарегистрирован — пропускаем.

FSMContext (`state`) и `event_from_user` уже внедрены встроенными middleware
aiogram (UserContextMiddleware и FSMContextMiddleware), которые зарегистрированы
как update-outer middleware раньше наших.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, Update

from bot.constants import TEXTS
from bot.database.repository import Repository
from bot.utils.validators import escape_md

# Префикс имени состояний онбординга в строке state.
_ONBOARDING_PREFIX = "OnboardingStates"


class RegistrationMiddleware(BaseMiddleware):
    """Гейт регистрации: не пускает незарегистрированных дальше онбординга."""

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        message: Message | None = event.message
        callback: CallbackQuery | None = event.callback_query
        tg_user = data.get("event_from_user")
        repo: Repository = data["repo"]

        # Апдейты без пользователя (служебные) — пропускаем как есть.
        if tg_user is None:
            return await handler(event, data)

        # (1) /start и /cancel всегда проходят к своим хендлерам.
        text = message.text if message else None
        if text and (text.startswith("/start") or text.startswith("/cancel")):
            return await handler(event, data)

        db_user = await repo.get_user(tg_user.id)

        # (2) Пользователя нет в БД и это не /start — просим начать с /start.
        if db_user is None:
            await self._reply(message, callback, TEXTS["not_registered"])
            return None

        # (4) Зарегистрированный пользователь — без ограничений.
        if db_user.is_registered:
            return await handler(event, data)

        # (3) Незавершённый онбординг.
        state: FSMContext = data["state"]
        current = await state.get_state()
        if current and current.startswith(_ONBOARDING_PREFIX):
            return await handler(event, data)

        # Состояние потеряно — перезапускаем онбординг с приветствия.
        if message is not None:
            from bot.handlers.start import begin_onboarding

            await state.clear()
            await begin_onboarding(message, state, repo, db_user)
        elif callback is not None:
            await callback.answer()
        return None

    @staticmethod
    async def _reply(
        message: Message | None,
        callback: CallbackQuery | None,
        text: str,
    ) -> None:
        """Ответить пользователю и в message, и в callback-сценарии."""
        if message is not None:
            await message.answer(escape_md(text))
        elif callback is not None:
            await callback.answer(text, show_alert=True)
