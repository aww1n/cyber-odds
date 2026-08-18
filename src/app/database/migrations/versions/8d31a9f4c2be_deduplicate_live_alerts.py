"""Deduplicate live alerts and add a concurrency-safe alert key.

Revision ID: 8d31a9f4c2be
Revises: f34c2e10a9d7
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "8d31a9f4c2be"
down_revision: Union[str, Sequence[str], None] = "f34c2e10a9d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("alert_key", sa.String(length=255), nullable=True))

    # Keep the first historical alert as the canonical real bet. Older builds
    # could emit the same event/selection every odds cycle; demote only those
    # duplicate Signal rows while preserving every ModelPrediction for research.
    # Settlement rows are derived from alerts. Remove only settlement rows
    # attached to historical duplicate alerts so raw ROI is not inflated.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    s.id,
                    ROW_NUMBER() OVER (
                        PARTITION BY s.strategy, p.event_id, p.selection
                        ORDER BY CASE WHEN s.sent_at IS NOT NULL THEN 0 ELSE 1 END, COALESCE(s.sent_at, s.created_at) ASC, s.id ASC
                    ) AS rn
                FROM signals s
                JOIN model_predictions p ON p.id = s.prediction_id
                WHERE s.decision = 'alert'
            )
            DELETE FROM settlements st
            USING ranked r
            WHERE st.signal_id = r.id AND r.rn > 1
            """
        )
    )

    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    s.id,
                    s.strategy,
                    p.event_id,
                    p.selection,
                    ROW_NUMBER() OVER (
                        PARTITION BY s.strategy, p.event_id, p.selection
                        ORDER BY CASE WHEN s.sent_at IS NOT NULL THEN 0 ELSE 1 END, COALESCE(s.sent_at, s.created_at) ASC, s.id ASC
                    ) AS rn
                FROM signals s
                JOIN model_predictions p ON p.id = s.prediction_id
                WHERE s.decision = 'alert'
            )
            UPDATE signals s
            SET
                decision = 'skip',
                filter_reasons = '["duplicate_alert_migrated"]'::json
            FROM ranked r
            WHERE s.id = r.id AND r.rn > 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH canonical AS (
                SELECT
                    s.id,
                    s.strategy,
                    p.event_id,
                    p.selection
                FROM signals s
                JOIN model_predictions p ON p.id = s.prediction_id
                WHERE s.decision = 'alert'
            )
            UPDATE signals s
            SET alert_key = c.strategy || ':' || c.event_id::text || ':' || c.selection
            FROM canonical c
            WHERE s.id = c.id
            """
        )
    )
    op.create_index("ux_signals_alert_key", "signals", ["alert_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_signals_alert_key", table_name="signals")
    op.drop_column("signals", "alert_key")
