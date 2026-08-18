"""add result source updated at

Revision ID: a80a96915f67
Revises: c99215b408ef
Create Date: 2026-08-17 15:55:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a80a96915f67"
down_revision: str | None = "c99215b408ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("results") as batch_op:
        batch_op.add_column(sa.Column("source_updated_at", sa.DateTime(timezone=True)))
        batch_op.create_index(
            "ix_results_source_updated_at",
            ["source_updated_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("results") as batch_op:
        batch_op.drop_index("ix_results_source_updated_at")
        batch_op.drop_column("source_updated_at")
