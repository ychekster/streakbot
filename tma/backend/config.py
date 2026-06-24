"""Конфигурация API-сервера TMA.

Читается из того же `.env`, что и бот (на уровне корня репозитория), поэтому
`BOT_TOKEN` и `DATABASE_URL` совпадают с ботовскими — общая база, общий токен
для верификации `initData`. Никаких секретов и магических значений в коде.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Путь к общему `.env` в корне репозитория (на два уровня выше: tma/backend/ -> tma/ -> корень).
# Берём абсолютный путь, чтобы конфигурация читалась независимо от текущей рабочей директории.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Строго типизированные настройки API, читаются из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Токен бота от @BotFather — используется для проверки подписи initData (HMAC-SHA256).
    bot_token: str = Field(..., alias="BOT_TOKEN")

    # Та же строка подключения, что у бота (общая БД). Для SQLite путь относительный,
    # поэтому сервер нужно запускать из корня репозитория — см. tma/README.md.
    database_url: str = Field(
        default="sqlite+aiosqlite:///./streakbot.db",
        alias="DATABASE_URL",
    )

    # Хост и порт API-сервера.
    host: str = Field(default="0.0.0.0", alias="TMA_HOST")
    port: int = Field(default=8000, alias="TMA_PORT")

    # Разрешённые источники для CORS (фронтенд обычно на другом домене/порту).
    # Список через запятую; "*" — разрешить любой источник. Авторизация идёт через
    # заголовок (а не cookie), поэтому "*" здесь безопасен.
    allowed_origins: str = Field(default="*", alias="TMA_ALLOWED_ORIGINS")

    # Максимальный возраст initData в секундах (защита от воспроизведения старой
    # подписи). 0 — проверку возраста отключить (оставить только проверку подписи).
    auth_ttl_seconds: int = Field(default=86_400, alias="TMA_AUTH_TTL_SECONDS")

    @property
    def cors_origins(self) -> list[str]:
        """Список источников CORS из строки через запятую."""
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def load_settings() -> Settings:
    """Загрузить и закешировать настройки (`.env` читается один раз за процесс)."""
    return Settings()
