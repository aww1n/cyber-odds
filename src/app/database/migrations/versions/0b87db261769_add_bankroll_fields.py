"""add bankroll and alert window fields

Revision ID: 0b87db261769
Revises: e9c1b6d42a10
Create Date: 2026-08-19 23:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0b87db261769'
down_revision: Union[str, Sequence[str], None] = 'e9c1b6d42a10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add bankroll fields to signals table
    op.add_column('signals', sa.Column('bankroll_at_signal', sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column('signals', sa.Column('stake_percent', sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column('signals', sa.Column('stake_amount', sa.Numeric(precision=14, scale=2), nullable=True))


def downgrade() -> None:
    # Remove bankroll fields from signals
    op.drop_column('signals', 'stake_amount')
    op.drop_column('signals', 'stake_percent')
    op.drop_column('signals', 'bankroll_at_signal')
