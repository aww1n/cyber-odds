"""link predictions to event matches

Revision ID: f34c2e10a9d7
Revises: b9a6e46be0dc
Create Date: 2026-08-17 18:10:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f34c2e10a9d7"
down_revision: str | None = "b9a6e46be0dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_predictions") as batch_op:
        batch_op.add_column(sa.Column("event_match_id", sa.BigInteger(), nullable=True))
        batch_op.create_foreign_key(
            "fk_model_predictions_event_match_id_event_matches",
            "event_matches",
            ["event_match_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_model_predictions_event_match_id",
            ["event_match_id"],
            unique=False,
        )
    op.execute(
        sa.text(
            """
            UPDATE model_predictions
            SET event_match_id = (
                SELECT MIN(event_matches.id)
                FROM event_matches
                WHERE event_matches.bookmaker_event_id = model_predictions.event_id
                  AND event_matches.status = 'matched'
            )
            WHERE (
                SELECT COUNT(*)
                FROM event_matches
                WHERE event_matches.bookmaker_event_id = model_predictions.event_id
                  AND event_matches.status = 'matched'
            ) = 1
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("model_predictions") as batch_op:
        batch_op.drop_index("ix_model_predictions_event_match_id")
        batch_op.drop_constraint(
            "fk_model_predictions_event_match_id_event_matches",
            type_="foreignkey",
        )
        batch_op.drop_column("event_match_id")
