"""deduplicate model predictions

Revision ID: b9a6e46be0dc
Revises: 76a20ebc629a
Create Date: 2026-08-17 17:55:00

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b9a6e46be0dc"
down_revision: str | None = "76a20ebc629a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_predictions") as batch_op:
        batch_op.create_unique_constraint(
            "uq_prediction_snapshot_model_selection",
            ["odds_snapshot_id", "model_name", "model_version", "selection"],
        )


def downgrade() -> None:
    with op.batch_alter_table("model_predictions") as batch_op:
        batch_op.drop_constraint(
            "uq_prediction_snapshot_model_selection",
            type_="unique",
        )
