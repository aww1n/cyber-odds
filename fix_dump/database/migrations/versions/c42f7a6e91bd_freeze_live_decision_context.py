"""Freeze live mapping context and expire queued alerts.

Revision ID: c42f7a6e91bd
Revises: 8d31a9f4c2be
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c42f7a6e91bd"
down_revision: Union[str, Sequence[str], None] = "8d31a9f4c2be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_predictions",
        sa.Column("mapping_reversed_sides", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "signals",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # EventMatch.components can be recalculated after a signal is generated.
    # Snapshot the current orientation for existing live predictions so future
    # result settlement does not depend on mutable matching metadata.
    op.execute(
        sa.text(
            """
            UPDATE model_predictions p
            SET mapping_reversed_sides = CASE
                WHEN lower(COALESCE(em.components->>'reversed_sides', 'false')) = 'true'
                    THEN true
                ELSE false
            END
            FROM event_matches em
            WHERE p.event_match_id = em.id
              AND p.mapping_reversed_sides IS NULL
            """
        )
    )

    # Old queued alerts have no strategy-specific expiry persisted. Use the
    # project's historical two-minute freshness window as a conservative
    # migration default; new signals persist their exact strategy expiry.
    op.execute(
        sa.text(
            """
            UPDATE signals s
            SET expires_at = LEAST(e.started_at, s.created_at + interval '120 seconds')
            FROM model_predictions p
            JOIN events e ON e.id = p.event_id
            WHERE s.prediction_id = p.id
              AND s.expires_at IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("signals", "expires_at")
    op.drop_column("model_predictions", "mapping_reversed_sides")
