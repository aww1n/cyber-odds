"""Use one canonical live alert per event and strategy.

Revision ID: d7b4c8e219f0
Revises: c42f7a6e91bd
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d7b4c8e219f0"
down_revision: Union[str, Sequence[str], None] = "c42f7a6e91bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Previous builds keyed alerts by event + selection, so P1/X/P2 could all
    # become separate live calls for one mutually-exclusive 1X2 event. Clear
    # those keys first; NULL values do not conflict with the unique index.
    op.execute(sa.text("UPDATE signals SET alert_key = NULL WHERE decision = 'alert'"))

    # For alerts that were never delivered, keep only the strongest candidate
    # per event/strategy. Already delivered historical calls remain untouched
    # for truthful analytics, but only the canonical row receives the new key.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    s.id,
                    ROW_NUMBER() OVER (
                        PARTITION BY s.strategy, p.event_id
                        ORDER BY
                            CASE WHEN s.sent_at IS NOT NULL THEN 0 ELSE 1 END,
                            CASE WHEN s.sent_at IS NULL THEN p.value_percent END DESC NULLS LAST,
                            COALESCE(s.sent_at, s.created_at) ASC,
                            s.id ASC
                    ) AS rn
                FROM signals s
                JOIN model_predictions p ON p.id = s.prediction_id
                WHERE s.decision = 'alert'
            )
            UPDATE signals s
            SET
                decision = 'skip',
                filter_reasons = '["superseded_event_alert_migrated"]'::json
            FROM ranked r
            WHERE s.id = r.id
              AND r.rn > 1
              AND s.sent_at IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            WITH canonical AS (
                SELECT DISTINCT ON (s.strategy, p.event_id)
                    s.id,
                    s.strategy,
                    p.event_id
                FROM signals s
                JOIN model_predictions p ON p.id = s.prediction_id
                WHERE s.decision = 'alert'
                ORDER BY
                    s.strategy,
                    p.event_id,
                    CASE WHEN s.sent_at IS NOT NULL THEN 0 ELSE 1 END,
                    CASE WHEN s.sent_at IS NULL THEN p.value_percent END DESC NULLS LAST,
                    COALESCE(s.sent_at, s.created_at) ASC,
                    s.id ASC
            )
            UPDATE signals s
            SET alert_key = c.strategy || ':' || c.event_id::text
            FROM canonical c
            WHERE s.id = c.id
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE signals s
            SET alert_key = s.strategy || ':' || p.event_id::text || ':' || p.selection
            FROM model_predictions p
            WHERE s.prediction_id = p.id
              AND s.decision = 'alert'
              AND s.alert_key IS NOT NULL
            """
        )
    )
