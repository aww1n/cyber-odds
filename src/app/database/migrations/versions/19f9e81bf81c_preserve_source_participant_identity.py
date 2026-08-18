"""preserve source participant identity

Revision ID: 19f9e81bf81c
Revises: eb4b123ae501
Create Date: 2026-08-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "19f9e81bf81c"
down_revision: str | None = "eb4b123ae501"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("event_participants") as batch_op:
        batch_op.add_column(sa.Column("raw_player_name", sa.String(length=255)))
        batch_op.add_column(sa.Column("raw_team_name", sa.String(length=255)))
        batch_op.add_column(sa.Column("external_player_key", sa.String(length=255)))
        batch_op.add_column(sa.Column("external_participant_id", sa.String(length=128)))
        batch_op.add_column(sa.Column("external_team_id", sa.String(length=128)))

    with op.batch_alter_table("results") as batch_op:
        batch_op.alter_column(
            "settled_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("results") as batch_op:
        batch_op.alter_column(
            "settled_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

    with op.batch_alter_table("event_participants") as batch_op:
        batch_op.drop_column("external_team_id")
        batch_op.drop_column("external_participant_id")
        batch_op.drop_column("external_player_key")
        batch_op.drop_column("raw_team_name")
        batch_op.drop_column("raw_player_name")
