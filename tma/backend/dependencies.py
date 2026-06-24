"""Зависимости FastAPI (Dependency Injection).

Здесь собрано связывание запроса с инфраструктурой: настройки, сессия БД +
репозиторий (с авто-commit/rollback) и текущий пользователь Telegram, выведенный
из проверенной `initData`.
"""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import Header, Request

from bot.database.repository import Repository
from tma.backend.auth import InitDataError, TelegramUser, verify_init_data
from tma.backend.config import Settings
from tma.backend.constants import INIT_DATA_AUTH_SCHEME
from tma.backend.database import Database
from tma.backend.errors import ApiError


def get_settings(request: Request) -> Settings:
    """Настройки приложения (созданы при старте, лежат в `app.state`)."""
    return request.app.state.settings


def _get_database(request: Request) -> Database:
    """Объект подключения к БД из `app.state`."""
    return request.app.state.database


async def get_repository(request: Request) -> AsyncIterator[Repository]:
    """Репозиторий поверх сессии запроса с авто-commit при успехе и rollback при ошибке.

    Повторяет контракт ботовского `DatabaseMiddleware`: одна сессия на запрос,
    изменения фиксируются по завершении обработчика.
    """
    database = _get_database(request)
    async with database.session_factory() as session:
        repository = Repository(session)
        try:
            yield repository
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _extract_init_data(authorization: str | None) -> str:
    """Достать строку initData из заголовка `Authorization: tma <initData>`.

    Допускаем и схему `tma <initData>` (рекомендация Telegram), и «голую» строку
    initData — некоторые клиенты присылают её без префикса.
    """
    if not authorization:
        raise ApiError(401, "missing_init_data", "Отсутствует авторизация Telegram")
    prefix = f"{INIT_DATA_AUTH_SCHEME} "
    if authorization.lower().startswith(prefix):
        return authorization[len(prefix):]
    return authorization


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> TelegramUser:
    """Текущий пользователь Telegram из проверенной `initData` (иначе 401)."""
    settings = get_settings(request)
    init_data_raw = _extract_init_data(authorization)
    try:
        return verify_init_data(
            init_data_raw,
            bot_token=settings.bot_token,
            max_age_seconds=settings.auth_ttl_seconds,
        )
    except InitDataError as exc:
        raise ApiError(401, "invalid_init_data", "Не удалось подтвердить личность Telegram") from exc
