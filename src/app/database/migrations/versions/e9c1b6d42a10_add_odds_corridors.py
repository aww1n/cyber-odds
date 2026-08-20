"""Add leakage-safe bookmaker odds corridor aggregates.

Revision ID: e9c1b6d42a10
Revises: d7b4c8e219f0
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9c1b6d42a10"
down_revision: Union[str, Sequence[str], None] = "d7b4c8e219f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "odds_corridors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("bookmaker_source_id", sa.BigInteger(), nullable=False),
        sa.Column("sport", sa.String(length=64), nullable=False),
        sa.Column("game_key", sa.String(length=64), server_default="", nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_value", sa.String(length=255), server_default="", nullable=False),
        sa.Column("market_code", sa.String(length=64), nullable=False),
        sa.Column("selection", sa.String(length=64), nullable=False),
        sa.Column("odds_min", sa.Numeric(12, 5), nullable=False),
        sa.Column("odds_max", sa.Numeric(12, 5), nullable=False),
        sa.Column("bucket_width", sa.Numeric(12, 5), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False),
        sa.Column("losses", sa.Integer(), nullable=False),
        sa.Column("win_rate", sa.Numeric(12, 10), nullable=False),
        sa.Column("average_odds", sa.Numeric(12, 5), nullable=False),
        sa.Column("average_implied_probability", sa.Numeric(12, 10), nullable=False),
        sa.Column("roi_percent", sa.Numeric(12, 5), nullable=False),
        sa.Column("edge_percent", sa.Numeric(12, 5), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope_type IN ('global', 'tournament_family')", name="scope_type"),
        sa.CheckConstraint("odds_min > 1 AND odds_max > odds_min", name="odds_corridor_bounds"),
        sa.CheckConstraint("sample_size > 0", name="odds_corridor_sample_size"),
        sa.ForeignKeyConstraint(["bookmaker_source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bookmaker_source_id",
            "sport",
            "game_key",
            "scope_type",
            "scope_value",
            "market_code",
            "selection",
            "odds_min",
            "odds_max",
            name="uq_odds_corridor_bucket",
        ),
    )
    op.create_index("ix_odds_corridors_as_of", "odds_corridors", ["as_of"], unique=False)
    op.create_index(
        "ix_odds_corridor_lookup",
        "odds_corridors",
        [
            "bookmaker_source_id",
            "sport",
            "game_key",
            "market_code",
            "selection",
            "odds_min",
            "odds_max",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_odds_corridor_lookup", table_name="odds_corridors")
    op.drop_index("ix_odds_corridors_as_of", table_name="odds_corridors")
    op.drop_table("odds_corridors")
