"""Эндпоинты задач (привычек).

    GET  /tasks                  — список привычек с историей выполнения за год
    POST /tasks/{task_id}/toggle — отметить/снять отметку выполнения за сегодня

Оба требуют валидную `initData` (через зависимость `get_current_user`). Доступ к
данным — только через репозиторий бота.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path

from bot.database.repository import Repository
from tma.backend.auth import TelegramUser
from tma.backend.dependencies import get_current_user, get_repository
from tma.backend.errors import ApiError
from tma.backend.schemas import HabitsResponse, ToggleResponse
from tma.backend.services import list_habits, toggle_today

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=HabitsResponse)
async def get_tasks(
    user: TelegramUser = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> HabitsResponse:
    """Список активных привычек пользователя с историей выполнения за год.

    Если пользователь ещё не зарегистрирован в боте или у него нет задач —
    возвращается пустой список (не ошибка).
    """
    db_user = await repo.get_user(user.id)
    if db_user is None:
        return HabitsResponse(habits=[])
    habits = await list_habits(repo, db_user)
    return HabitsResponse(habits=habits)


@router.post("/{task_id}/toggle", response_model=ToggleResponse)
async def toggle_task(
    task_id: int = Path(..., ge=1, description="Идентификатор задачи"),
    user: TelegramUser = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> ToggleResponse:
    """Переключить отметку выполнения задачи за сегодня и вернуть её новое состояние."""
    db_user = await repo.get_user(user.id)
    if db_user is None:
        raise ApiError(404, "user_not_found", "Пользователь не найден")
    task = await repo.get_active_task(task_id, db_user.telegram_id)
    if task is None:
        raise ApiError(404, "task_not_found", "Задача не найдена")
    habit = await toggle_today(repo, db_user, task)
    return ToggleResponse(habit=habit)
