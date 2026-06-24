"""Точка входа API-сервера TMA (FastAPI).

Запуск из корня репозитория:

    python -m tma.backend.main
    # или с автоперезагрузкой при разработке:
    uvicorn tma.backend.main:app --reload --port 8000

Последовательность старта: настройки → подключение к общей БД → CORS →
обработчики ошибок → роутеры.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from tma.backend.config import Settings, load_settings
from tma.backend.database import Database
from tma.backend.errors import register_error_handlers
from tma.backend.routers import tasks


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Жизненный цикл приложения: поднять подключение к БД и закрыть его при остановке."""
    settings: Settings = load_settings()
    app.state.settings = settings
    app.state.database = Database(settings.database_url)
    logger.info("TMA API started (database connected)")
    try:
        yield
    finally:
        await app.state.database.dispose()
        logger.info("TMA API stopped (database disposed)")


def create_app() -> FastAPI:
    """Собрать и сконфигурировать FastAPI-приложение."""
    settings = load_settings()
    app = FastAPI(title="StreakBot Mini App API", version="1.0.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(tasks.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Проверка живости сервиса (для мониторинга/проксей)."""
        return {"status": "ok"}

    return app


app = create_app()


def run() -> None:
    """Запустить uvicorn с хостом и портом из настроек."""
    import uvicorn

    settings = load_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
