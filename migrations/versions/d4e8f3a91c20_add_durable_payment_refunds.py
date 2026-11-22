"""Add durable payment refund requests."""

from alembic import op
import sqlalchemy as sa


revision = "d4e8f3a91c20"
down_revision = "c3a9d1e7f420"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "payment_attempt_id",
            sa.Integer(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("provider_refund_id", sa.String(length=128), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_payment_refund_positive_amount",
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'submitted', 'pending', 'unknown', 'processed', 'failed')",
            name="ck_payment_refund_status",
        ),
        sa.UniqueConstraint(
            "provider",
            "idempotency_key",
            name="uq_payment_refund_provider_idempotency",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_refund_id",
            name="uq_payment_refund_provider_refund_id",
        ),
    )

    op.create_index(
        "ix_payment_refunds_payment_created",
        "payment_refunds",
        ["payment_attempt_id", "created_at"],
    )
    op.create_index(
        "ix_payment_refunds_status_created",
        "payment_refunds",
        ["status", "created_at"],
    )


def downgrade():
    op.drop_index(
        "ix_payment_refunds_status_created",
        table_name="payment_refunds",
    )
    op.drop_index(
        "ix_payment_refunds_payment_created",
        table_name="payment_refunds",
    )
    op.drop_table("payment_refunds")
