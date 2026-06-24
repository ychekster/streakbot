"""Верификация Telegram `initData` (HMAC-SHA256).

Telegram передаёт фронтенду строку `initData` с подписью. Любой запрос к API
обязан её прислать, и при каждом запросе подпись проверяется заново. Алгоритм —
из официальной документации Telegram Web Apps:

    secret_key  = HMAC_SHA256(key="WebAppData", message=bot_token)
    data_check  = "\\n".join(f"{k}={v}" for k, v in sorted(pairs) if k != "hash")
    expected    = HMAC_SHA256(key=secret_key, message=data_check)  # hex
    valid       = (expected == hash)

Здесь нет ни обращений к БД, ни зависимостей от FastAPI — только чистая проверка,
поэтому модуль легко тестировать и переиспользовать.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from pydantic import BaseModel

# Ключ HMAC для вывода секрета из токена бота (константа протокола Telegram Web Apps).
_WEB_APP_DATA_KEY = b"WebAppData"


class InitDataError(Exception):
    """initData отсутствует, повреждена, просрочена или подпись неверна."""


class TelegramUser(BaseModel):
    """Пользователь Telegram, извлечённый из проверенной `initData`."""

    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


def _compute_secret_key(bot_token: str) -> bytes:
    """Секретный ключ для проверки подписи: HMAC_SHA256(key='WebAppData', msg=token)."""
    return hmac.new(_WEB_APP_DATA_KEY, bot_token.encode("utf-8"), hashlib.sha256).digest()


def _build_data_check_string(pairs: list[tuple[str, str]]) -> str:
    """Строка для проверки подписи: пары 'key=value' (кроме hash), сортированные, через \\n."""
    relevant = sorted((key, value) for key, value in pairs if key != "hash")
    return "\n".join(f"{key}={value}" for key, value in relevant)


def verify_init_data(
    init_data_raw: str,
    bot_token: str,
    max_age_seconds: int = 0,
) -> TelegramUser:
    """Проверить подпись `initData` и вернуть пользователя Telegram.

    `max_age_seconds > 0` дополнительно отвергает слишком старую подпись (по полю
    `auth_date`). Любая проблема (нет подписи, неверная подпись, просрочено,
    отсутствует пользователь) приводит к `InitDataError`.
    """
    if not init_data_raw:
        raise InitDataError("initData is empty")

    pairs = parse_qsl(init_data_raw, keep_blank_values=True)
    fields = dict(pairs)

    received_hash = fields.get("hash")
    if not received_hash:
        raise InitDataError("initData has no hash")

    data_check_string = _build_data_check_string(pairs)
    secret_key = _compute_secret_key(bot_token)
    expected_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    # Сравнение в постоянное время — защита от тайминг-атак.
    if not hmac.compare_digest(expected_hash, received_hash):
        raise InitDataError("initData signature mismatch")

    if max_age_seconds > 0:
        auth_date_raw = fields.get("auth_date")
        if not auth_date_raw or not auth_date_raw.isdigit():
            raise InitDataError("initData has no valid auth_date")
        if time.time() - int(auth_date_raw) > max_age_seconds:
            raise InitDataError("initData is expired")

    user_raw = fields.get("user")
    if not user_raw:
        raise InitDataError("initData has no user")
    try:
        user_data = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise InitDataError("initData user is not valid JSON") from exc

    try:
        return TelegramUser.model_validate(user_data)
    except Exception as exc:  # noqa: BLE001 — любую ошибку схемы трактуем как невалидные данные
        raise InitDataError("initData user has unexpected shape") from exc
