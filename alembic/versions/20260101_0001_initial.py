"""Initial schema: users, tasks, task_logs

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Значения enum'ов (хранятся как VARCHAR, native_enum=False в моделях).
_FREQUENCY = ("daily", "specific_days", "one_time")
_STATUS = ("pending", "done", "skipped", "missed")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("morning_time", sa.Time(), nullable=True),
        sa.Column("evening_time", sa.Time(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("is_registered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "frequency_type",
            sa.Enum(*_FREQUENCY, native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("days", sa.String(length=64), nullable=True),
        sa.Column("one_time_date", sa.Date(), nullable=True),
        sa.Column("reminder_time", sa.Time(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"]),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])

    op.create_table(
        "task_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*_STATUS, native_enum=False, length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("marked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"]),
        sa.UniqueConstraint("task_id", "scheduled_date", name="uq_tasklog_task_date"),
    )
    op.create_index("ix_task_logs_task_id", "task_logs", ["task_id"])
    op.create_index("ix_task_logs_user_id", "task_logs", ["user_id"])
    op.create_index("ix_task_logs_scheduled_date", "task_logs", ["scheduled_date"])


def downgrade() -> None:
    op.drop_index("ix_task_logs_scheduled_date", table_name="task_logs")
    op.drop_index("ix_task_logs_user_id", table_name="task_logs")
    op.drop_index("ix_task_logs_task_id", table_name="task_logs")
    op.drop_table("task_logs")
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("users")
