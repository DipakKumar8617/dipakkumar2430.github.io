"""Add idempotent reconciliation statement imports."""
from alembic import op
import sqlalchemy as sa

revision = "c8f4a2d91e60"
down_revision = "b7e2f4a91c60"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("payment_reconciliation_batches") as batch:
        batch.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("statement_hash", sa.String(length=64), nullable=True))
        batch.create_unique_constraint(
            "uq_payment_reconciliation_provider_idempotency",
            ["provider", "idempotency_key"],
        )


def downgrade():
    with op.batch_alter_table("payment_reconciliation_batches") as batch:
        batch.drop_constraint(
            "uq_payment_reconciliation_provider_idempotency",
            type_="unique",
        )
        batch.drop_column("statement_hash")
        batch.drop_column("idempotency_key")
