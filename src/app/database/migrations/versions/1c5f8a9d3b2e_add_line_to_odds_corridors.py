"""add line to odds_corridors for totals support

Revision ID: 1c5f8a9d3b2e
Revises: 0b87db261769
Create Date: 2026-08-19 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1c5f8a9d3b2e'
down_revision: Union[str, Sequence[str], None] = '0b87db261769'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add line column to odds_corridors
    op.add_column('odds_corridors', sa.Column('line', sa.Numeric(precision=10, scale=3), nullable=True))
    
    # Drop old unique constraint
    op.drop_constraint('uq_odds_corridor_bucket', 'odds_corridors', type_='unique')
    
    # Create new unique constraint with line
    op.create_unique_constraint(
        'uq_odds_corridor_bucket',
        'odds_corridors',
        [
            'bookmaker_source_id',
            'sport',
            'game_key',
            'scope_type',
            'scope_value',
            'market_code',
            'selection',
            'line',
            'odds_min',
            'odds_max'
        ]
    )
    
    # Drop old lookup index
    op.drop_index('ix_odds_corridor_lookup', table_name='odds_corridors')
    
    # Create new lookup index with line
    op.create_index(
        'ix_odds_corridor_lookup',
        'odds_corridors',
        [
            'bookmaker_source_id',
            'sport',
            'game_key',
            'market_code',
            'selection',
            'line',
            'odds_min',
            'odds_max'
        ],
        unique=False
    )
    
    # Removed active lookup index - is_active column doesn't exist


def downgrade() -> None:
    # Drop new indexes (skip active lookup - doesn't exist)
    op.drop_index('ix_odds_corridor_lookup', table_name='odds_corridors')
    
    # Drop new unique constraint
    op.drop_constraint('uq_odds_corridor_bucket', 'odds_corridors', type_='unique')
    
    # Recreate old lookup index without line
    op.create_index(
        'ix_odds_corridor_lookup',
        'odds_corridors',
        ['bookmaker_source_id', 'sport', 'game_key', 'market_code', 'selection', 'odds_min', 'odds_max'],
        unique=False
    )
    
    # Recreate old unique constraint without line
    op.create_unique_constraint(
        'uq_odds_corridor_bucket',
        'odds_corridors',
        [
            'bookmaker_source_id',
            'sport',
            'game_key',
            'scope_type',
            'scope_value',
            'market_code',
            'selection',
            'odds_min',
            'odds_max'
        ]
    )
    
    # Remove line column
    op.drop_column('odds_corridors', 'line')
