"""Normalize legacy total selections without deleting historical snapshots.

Revision ID: 7a2c91d4e6b8
Revises: 4e6f2b8a91c0
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7a2c91d4e6b8"
down_revision: Union[str, Sequence[str], None] = "4e6f2b8a91c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Older parser versions embedded the line in selection (TB/TM), although
    # the same rows already had a populated line column. Preserve all snapshot
    # identities and values; normalize only rows whose market contract and line
    # make the conversion unambiguous.
    op.execute(
        sa.text(
            """
            UPDATE odds_snapshots AS os
            SET selection = CASE
                WHEN UPPER(REPLACE(os.selection, ' ', '')) LIKE 'TB%'
                  OR UPPER(REPLACE(os.selection, ' ', '')) LIKE 'ТБ%'
                    THEN 'over'
                WHEN UPPER(REPLACE(os.selection, ' ', '')) LIKE 'TM%'
                  OR UPPER(REPLACE(os.selection, ' ', '')) LIKE 'ТМ%'
                    THEN 'under'
                ELSE os.selection
            END
            FROM markets AS m
            WHERE m.id = os.market_id
              AND m.code = 'total'
              AND os.line IS NOT NULL
              AND (
                    UPPER(REPLACE(os.selection, ' ', '')) LIKE 'TB%'
                 OR UPPER(REPLACE(os.selection, ' ', '')) LIKE 'ТБ%'
                 OR UPPER(REPLACE(os.selection, ' ', '')) LIKE 'TM%'
                 OR UPPER(REPLACE(os.selection, ' ', '')) LIKE 'ТМ%'
              )
            """
        )
    )


def downgrade() -> None:
    # Canonical over/under does not retain whether a row was written by a legacy
    # or current parser, so reconstructing TB/TM would corrupt current rows.
    # Keeping the normalized values is the safe, data-preserving downgrade.
    pass
