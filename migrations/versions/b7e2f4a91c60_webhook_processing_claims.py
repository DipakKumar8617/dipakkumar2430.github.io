"""add atomic webhook processing claims and retry leases

Revision ID: b7e2f4a91c60
Revises: d4e8f3a91c20, e31a7c9d5b42
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e2f4a91c60"
down_revision: Union[str, Sequence[str], None] = (
    "d4e8f3a91c20",
    "e31a7c9d5b42",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("payment_webhook_events") as batch:
        batch.drop_constraint(
            "ck_payment_webhook_processing_status",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_payment_webhook_processing_status",
            "processing_status IN ('received', 'processing', 'processed', 'ignored', 'failed')",
        )
        batch.add_column(
            sa.Column("processing_started_at", sa.DateTime(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "processing_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    active_claims = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM payment_webhook_events "
            "WHERE processing_status = 'processing'"
        )
    ).scalar_one()
    if active_claims:
        raise RuntimeError(
            "Cannot downgrade while webhook processing claims are active."
        )

    with op.batch_alter_table("payment_webhook_events") as batch:
        batch.drop_column("processing_attempts")
        batch.drop_column("processing_started_at")
        batch.drop_constraint(
            "ck_payment_webhook_processing_status",
            type_="check",
        )
        batch.create_check_constraint(
            "ck_payment_webhook_processing_status",
            "processing_status IN ('received', 'processed', 'ignored', 'failed')",
        )
