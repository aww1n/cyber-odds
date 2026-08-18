"""add tournament matching metadata

Revision ID: c99215b408ef
Revises: 5523a3ad677e
Create Date: 2026-08-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c99215b408ef"
down_revision: str | None = "5523a3ad677e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tournaments") as batch_op:
        batch_op.add_column(sa.Column("family", sa.String(length=64)))
        batch_op.add_column(sa.Column("country_code", sa.String(length=8)))
        batch_op.create_index("ix_tournaments_family", ["family"], unique=False)
    with op.batch_alter_table("event_participants") as batch_op:
        batch_op.add_column(sa.Column("raw_team_name_alt", sa.String(length=255)))


def downgrade() -> None:
    with op.batch_alter_table("event_participants") as batch_op:
        batch_op.drop_column("raw_team_name_alt")
    with op.batch_alter_table("tournaments") as batch_op:
        batch_op.drop_index("ix_tournaments_family")
        batch_op.drop_column("country_code")
        batch_op.drop_column("family")
