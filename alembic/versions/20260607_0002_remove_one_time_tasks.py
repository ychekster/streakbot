"""Remove one-time tasks

Удаляет одноразовые задачи без следа: убирает накопленные данные (сами задачи и
их логи) и колонку `tasks.one_time_date`. Значение `one_time` enum'а
`frequency_type` хранится как VARCHAR без CHECK-ограничения (native_enum=False,
create_constraint=False по умолчанию в SQLAlchemy 2.0), поэтому отдельно менять
ограничение не нужно — достаточно убрать данные и колонку.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-07 00:00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Удаляем данные одноразовых задач без следа: сначала их логи (FK на tasks),
    #    затем сами задачи.
    op.execute(
        "DELETE FROM task_logs WHERE task_id IN "
        "(SELECT id FROM tasks WHERE frequency_type = 'one_time')"
    )
    op.execute("DELETE FROM tasks WHERE frequency_type = 'one_time'")

    # 2. Убираем колонку one_time_date. Batch-режим пересоздаёт таблицу в SQLite
    #    (на PostgreSQL выполняется обычный ALTER TABLE ... DROP COLUMN), сохраняя
    #    остальные колонки, индексы и внешние ключи.
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("one_time_date")


def downgrade() -> None:
    # Возвращаем колонку one_time_date (удалённые одноразовые задачи и их логи при
    # этом не восстанавливаются — данные были стёрты безвозвратно).
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("one_time_date", sa.Date(), nullable=True))
