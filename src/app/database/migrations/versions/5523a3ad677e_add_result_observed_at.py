"""add result observed at

Revision ID: 5523a3ad677e
Revises: 19f9e81bf81c
Create Date: 2026-08-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5523a3ad677e"
down_revision: str | None = "19f9e81bf81c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("results") as batch_op:
        batch_op.add_column(sa.Column("observed_at", sa.DateTime(timezone=True)))
        batch_op.create_index("ix_results_observed_at", ["observed_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("results") as batch_op:
        batch_op.drop_index("ix_results_observed_at")
        batch_op.drop_column("observed_at")
