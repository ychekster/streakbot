"""Pydantic-схемы ответов API (контракт с фронтендом).

Схемы намеренно отделены от ORM-моделей бота: модели описывают хранение, схемы —
форму ответа. Так контракт API не зависит от внутренней структуры таблиц.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from tma.backend.constants import GRID_DAYS


class Habit(BaseModel):
    """Привычка пользователя: история выполнения и признак расписания на сегодня."""

    id: int = Field(..., description="Идентификатор задачи")
    name: str = Field(..., description="Название привычки")
    done_today: bool = Field(..., description="Отмечена ли задача выполненной сегодня")
    scheduled_today: bool = Field(
        ...,
        description=(
            "Запланирована ли задача на сегодня (по частоте/дням недели). "
            "True — задачу можно отмечать; False — только просмотр прогресса."
        ),
    )
    history: list[bool] = Field(
        ...,
        description=(
            f"Выполнение за последние {GRID_DAYS} дней (старое → сегодня). "
            "True — день выполнен (статус done), False — пропущен или нет данных. "
            "Индекс 0 — самый старый день, последний — сегодня."
        ),
    )


class HabitsResponse(BaseModel):
    """Ответ `GET /tasks` — список привычек пользователя."""

    habits: list[Habit]


class ToggleResponse(BaseModel):
    """Ответ `POST /tasks/{task_id}/toggle` — обновлённое состояние привычки."""

    habit: Habit
