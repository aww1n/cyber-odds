"""Complete totals, corridor, and matching production schema.

Revision ID: 4e6f2b8a91c0
Revises: 1c5f8a9d3b2e
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4e6f2b8a91c0"
down_revision: Union[str, Sequence[str], None] = "1c5f8a9d3b2e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "odds_corridors",
        sa.Column("returns", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "odds_corridors",
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
    )
    op.add_column(
        "odds_corridors",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("uq_odds_corridor_bucket", "odds_corridors", type_="unique")
    op.create_index(
        "ux_odds_corridor_bucket",
        "odds_corridors",
        [
            "bookmaker_source_id",
            "sport",
            "game_key",
            "scope_type",
            "scope_value",
            "market_code",
            "selection",
            sa.text("COALESCE(line, -999999)"),
            "odds_min",
            "odds_max",
        ],
        unique=True,
    )
    op.create_index(
        "ix_odds_corridor_active_lookup",
        "odds_corridors",
        [
            "is_active",
            "bookmaker_source_id",
            "sport",
            "game_key",
            "market_code",
            "selection",
            "line",
        ],
        unique=False,
    )

    op.create_table(
        "corridor_observations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_match_id", sa.BigInteger(), nullable=False),
        sa.Column("odds_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("market_id", sa.BigInteger(), nullable=False),
        sa.Column("result_id", sa.BigInteger(), nullable=False),
        sa.Column("bookmaker_source_id", sa.BigInteger(), nullable=False),
        sa.Column("sport", sa.String(length=64), nullable=False),
        sa.Column("game_key", sa.String(length=64), server_default="", nullable=False),
        sa.Column(
            "tournament_family",
            sa.String(length=64),
            server_default="",
            nullable=False,
        ),
        sa.Column("market_code", sa.String(length=64), nullable=False),
        sa.Column("selection", sa.String(length=64), nullable=False),
        sa.Column("line", sa.Numeric(10, 3), nullable=True),
        sa.Column("odds", sa.Numeric(12, 5), nullable=False),
        sa.Column("implied_probability", sa.Numeric(12, 10), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column(
            "mapping_reversed_sides",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("result_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("odds > 1", name="odds_positive"),
        sa.CheckConstraint(
            "implied_probability > 0 AND implied_probability < 1",
            name="implied_probability_range",
        ),
        sa.CheckConstraint("outcome IN ('win', 'loss', 'return')", name="outcome"),
        sa.ForeignKeyConstraint(["bookmaker_source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["event_match_id"], ["event_matches.id"]),
        sa.ForeignKeyConstraint(["market_id"], ["markets.id"]),
        sa.ForeignKeyConstraint(["odds_snapshot_id"], ["odds_snapshots.id"]),
        sa.ForeignKeyConstraint(["result_id"], ["results.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_corridor_observation_contract",
        "corridor_observations",
        [
            "event_match_id",
            "market_id",
            "selection",
            sa.text("COALESCE(line, -999999)"),
        ],
        unique=True,
    )
    op.create_index(
        "ix_corridor_observations_event_match_id",
        "corridor_observations",
        ["event_match_id"],
    )
    op.create_index(
        "ix_corridor_observations_result_id",
        "corridor_observations",
        ["result_id"],
    )
    op.create_index(
        "ix_corridor_observations_snapshot_id",
        "corridor_observations",
        ["odds_snapshot_id"],
    )
    op.create_index(
        "ix_corridor_observations_available_at",
        "corridor_observations",
        ["result_available_at"],
    )
    op.create_index(
        "ix_corridor_observations_active_lookup",
        "corridor_observations",
        [
            "is_active",
            "bookmaker_source_id",
            "sport",
            "game_key",
            "market_code",
            "selection",
        ],
    )

    # Preserve every relation while making the active mapping one-to-one.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY source_event_id
                    ORDER BY confidence DESC, matched_at ASC, id ASC
                ) AS rn
                FROM event_matches
                WHERE status = 'matched'
            )
            UPDATE event_matches em
            SET status = 'ambiguous'
            FROM ranked r
            WHERE em.id = r.id AND r.rn > 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY bookmaker_event_id
                    ORDER BY confidence DESC, matched_at ASC, id ASC
                ) AS rn
                FROM event_matches
                WHERE status = 'matched'
            )
            UPDATE event_matches em
            SET status = 'ambiguous'
            FROM ranked r
            WHERE em.id = r.id AND r.rn > 1
            """
        )
    )
    op.create_index(
        "ix_event_matches_source_status",
        "event_matches",
        ["source_event_id", "status"],
    )
    op.create_index(
        "ix_event_matches_bookmaker_status",
        "event_matches",
        ["bookmaker_event_id", "status"],
    )
    op.create_index(
        "ux_event_matches_matched_source",
        "event_matches",
        ["source_event_id"],
        unique=True,
        postgresql_where=sa.text("status = 'matched'"),
    )
    op.create_index(
        "ux_event_matches_matched_bookmaker",
        "event_matches",
        ["bookmaker_event_id"],
        unique=True,
        postgresql_where=sa.text("status = 'matched'"),
    )
    op.create_index("ix_results_settled_at", "results", ["settled_at"])
    op.create_index(
        "ix_odds_contract_received",
        "odds_snapshots",
        ["event_id", "market_id", "selection", "line", "received_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_odds_contract_received", table_name="odds_snapshots")
    op.drop_index("ix_results_settled_at", table_name="results")
    op.drop_index("ux_event_matches_matched_bookmaker", table_name="event_matches")
    op.drop_index("ux_event_matches_matched_source", table_name="event_matches")
    op.drop_index("ix_event_matches_bookmaker_status", table_name="event_matches")
    op.drop_index("ix_event_matches_source_status", table_name="event_matches")

    op.drop_index(
        "ix_corridor_observations_active_lookup",
        table_name="corridor_observations",
    )
    op.drop_index(
        "ix_corridor_observations_available_at",
        table_name="corridor_observations",
    )
    op.drop_index(
        "ix_corridor_observations_snapshot_id",
        table_name="corridor_observations",
    )
    op.drop_index("ix_corridor_observations_result_id", table_name="corridor_observations")
    op.drop_index(
        "ix_corridor_observations_event_match_id",
        table_name="corridor_observations",
    )
    op.drop_index(
        "ux_corridor_observation_contract",
        table_name="corridor_observations",
    )
    op.drop_table("corridor_observations")

    op.drop_index("ix_odds_corridor_active_lookup", table_name="odds_corridors")
    op.drop_index("ux_odds_corridor_bucket", table_name="odds_corridors")
    op.create_unique_constraint(
        "uq_odds_corridor_bucket",
        "odds_corridors",
        [
            "bookmaker_source_id",
            "sport",
            "game_key",
            "scope_type",
            "scope_value",
            "market_code",
            "selection",
            "line",
            "odds_min",
            "odds_max",
        ],
    )
    op.drop_column("odds_corridors", "retired_at")
    op.drop_column("odds_corridors", "is_active")
    op.drop_column("odds_corridors", "returns")
