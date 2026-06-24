"""Единый формат ошибок API и его обработчики.

Все ошибки уходят клиенту в одинаковой форме:

    {"error": {"code": "<машинный_код>", "message": "<человекочитаемо>"}}

с осмысленным HTTP-статусом. Фронтенду достаточно прочитать `error.message` для
показа и `error.code` для логики, не разбирая разные форматы ответов.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger


class ApiError(Exception):
    """Прикладная ошибка с HTTP-статусом, машинным кодом и текстом для пользователя."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Собрать JSON-ответ в едином формате ошибки."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Подключить обработчики, приводящие любые ошибки к единому формату."""

    @app.exception_handler(ApiError)
    async def _on_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Невалидные параметры запроса — 422 с компактным описанием первой проблемы.
        first = exc.errors()[0] if exc.errors() else {}
        message = first.get("msg", "Некорректные параметры запроса")
        return _error_response(422, "validation_error", message)

    @app.exception_handler(Exception)
    async def _on_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        # Непредвиденная ошибка: логируем со стеком, наружу — обезличенное сообщение.
        logger.opt(exception=exc).error("Unhandled error while processing request: {}", exc)
        return _error_response(500, "internal_error", "Внутренняя ошибка сервера")
