"""ORM-модели: User, Task, TaskLog.

Стрик нигде не хранится как поле — он вычисляется динамически в
`services/streak.py` по записям TaskLog. Это исключает рассинхронизацию данных.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.database.base import Base


class FrequencyType(str, enum.Enum):
    """Тип расписания задачи."""

    daily = "daily"                  # каждый день
    specific_days = "specific_days"  # конкретные дни недели


class TaskStatus(str, enum.Enum):
    """Статус выполнения задачи на конкретную дату."""

    pending = "pending"   # создана, ещё не отмечена
    done = "done"         # пользователь отметил выполнение
    skipped = "skipped"   # пользователь отметил «не выполнено»
    missed = "missed"     # не отмечена по истечении grace period


class User(Base):
    """Пользователь Telegram и его настройки уведомлений."""

    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    morning_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    evening_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    is_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tasks: Mapped[list["Task"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Task(Base):
    """Задача (привычка) пользователя."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    frequency_type: Mapped[FrequencyType] = mapped_column(
        Enum(FrequencyType, native_enum=False, length=20), nullable=False
    )
    # Для specific_days: строка вида "mon,wed,fri".
    days: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Опциональное время отдельного напоминания.
    reminder_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="tasks")
    logs: Mapped[list["TaskLog"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskLog(Base):
    """Запись о статусе задачи на конкретную дату.

    Уникальность (task_id, scheduled_date) гарантирует одну запись на день.
    """

    __tablename__ = "task_logs"
    __table_args__ = (
        UniqueConstraint("task_id", "scheduled_date", name="uq_tasklog_task_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tasks.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True
    )
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, length=20),
        default=TaskStatus.pending,
        nullable=False,
    )
    marked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    task: Mapped["Task"] = relationship(back_populates="logs")
