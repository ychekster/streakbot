"""Конфигурация приложения.

Все настройки загружаются из `.env` через pydantic-settings.
Никаких секретов и магических значений в коде — только здесь.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Строго типизированная конфигурация бота, читается из переменных окружения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Токен бота от @BotFather. Единственная обязательная переменная.
    bot_token: str = Field(..., alias="BOT_TOKEN")

    # Строка подключения к БД (обязательно async-драйвер: aiosqlite / asyncpg).
    database_url: str = Field(
        default="sqlite+aiosqlite:///./streakbot.db",
        alias="DATABASE_URL",
    )

    # Параметры логирования.
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_file: str = Field(default="logs/bot.log", alias="LOG_FILE")

    # Публичный URL Telegram Mini App (фронтенд из tma/). Если задан — в главном
    # меню появляется кнопка для открытия мини-приложения; если пуст — меню как
    # раньше, без кнопки. URL должен быть HTTPS (требование Telegram WebApp).
    tma_url: str | None = Field(default=None, alias="TMA_URL")


@lru_cache
def load_config() -> Config:
    """Загрузить и закешировать конфигурацию.

    Используется кеш, чтобы `.env` читался один раз за время жизни процесса.
    """
    return Config()
