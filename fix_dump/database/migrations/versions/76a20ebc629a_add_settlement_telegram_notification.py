"""add settlement telegram notification

Revision ID: 76a20ebc629a
Revises: a80a96915f67
Create Date: 2026-08-17 17:20:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "76a20ebc629a"
down_revision: str | None = "a80a96915f67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("settlements") as batch_op:
        batch_op.add_column(
            sa.Column("telegram_notified_at", sa.DateTime(timezone=True))
        )


def downgrade() -> None:
    with op.batch_alter_table("settlements") as batch_op:
        batch_op.drop_column("telegram_notified_at")
