"""Add payment ledger and reconciliation foundation.

Revision ID: c3a9d1e7f420
Revises: 9fae77931e47
"""
from alembic import op
import sqlalchemy as sa

revision = "c3a9d1e7f420"
down_revision = "9fae77931e47"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("buyer_account_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("provider_order_id", sa.String(128)),
        sa.Column("provider_payment_id", sa.String(128)),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="created"),
        sa.Column("failure_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount > 0", name="ck_payment_attempt_amount_positive"),
        sa.CheckConstraint(
            "status IN ('created', 'pending', 'authorized', 'captured', "
            "'failed', 'cancelled', 'partially_refunded', 'refunded')",
            name="ck_payment_attempt_status",
        ),
        sa.UniqueConstraint("provider", "idempotency_key", name="uq_payment_attempt_provider_idempotency"),
        sa.UniqueConstraint("provider", "provider_order_id", name="uq_payment_attempt_provider_order"),
        sa.UniqueConstraint("provider", "provider_payment_id", name="uq_payment_attempt_provider_payment"),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], name="fk_payment_attempt_booking", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["buyer_account_id"], ["user_accounts.id"], name="fk_payment_attempt_buyer", ondelete="RESTRICT"),
    )
    op.create_index("ix_payment_attempt_booking_created", "payment_attempts", ["booking_id", "created_at"])
    op.create_index("ix_payment_attempt_status_created", "payment_attempts", ["status", "created_at"])

    op.create_table(
        "payment_ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_attempt_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_transaction_id", sa.String(128), nullable=False),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "entry_type IN ('capture', 'refund', 'reversal', 'chargeback')",
            name="ck_payment_ledger_entry_type",
        ),
        sa.CheckConstraint("amount > 0", name="ck_payment_ledger_amount_positive"),
        sa.UniqueConstraint("provider", "provider_transaction_id", name="uq_payment_ledger_provider_transaction"),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"], ["payment_attempts.id"],
            name="fk_payment_ledger_payment_attempt", ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_payment_ledger_payment_created", "payment_ledger_entries", ["payment_attempt_id", "created_at"])

    op.create_table(
        "payment_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_event_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("processing_status", sa.String(20), nullable=False, server_default="received"),
        sa.Column("event_metadata", sa.JSON(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.CheckConstraint(
            "processing_status IN ('received', 'processed', 'ignored', 'failed')",
            name="ck_payment_webhook_processing_status",
        ),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_payment_webhook_provider_event"),
    )
    op.create_index("ix_payment_webhook_status_received", "payment_webhook_events", ["processing_status", "received_at"])

    op.create_table(
        "payment_reconciliation_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_payment_reconciliation_batch_status"),
    )
    op.create_index("ix_payment_reconciliation_provider_created", "payment_reconciliation_batches", ["provider", "created_at"])

    op.create_table(
        "payment_reconciliation_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("payment_attempt_id", sa.Integer(), nullable=True),
        sa.Column("provider_transaction_id", sa.String(128), nullable=True),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("expected_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("actual_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "outcome IN ('matched', 'missing_internal', 'missing_provider', "
            "'amount_mismatch', 'currency_mismatch')",
            name="ck_payment_reconciliation_item_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["payment_reconciliation_batches.id"],
            name="fk_payment_reconciliation_item_batch", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"], ["payment_attempts.id"],
            name="fk_payment_reconciliation_item_payment", ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_payment_reconciliation_item_batch", "payment_reconciliation_items", ["batch_id"])
    op.create_index("ix_payment_reconciliation_item_payment", "payment_reconciliation_items", ["payment_attempt_id"])


def downgrade():
    op.drop_index("ix_payment_reconciliation_item_payment", table_name="payment_reconciliation_items")
    op.drop_index("ix_payment_reconciliation_item_batch", table_name="payment_reconciliation_items")
    op.drop_table("payment_reconciliation_items")
    op.drop_index("ix_payment_reconciliation_provider_created", table_name="payment_reconciliation_batches")
    op.drop_table("payment_reconciliation_batches")
    op.drop_index("ix_payment_webhook_status_received", table_name="payment_webhook_events")
    op.drop_table("payment_webhook_events")
    op.drop_index("ix_payment_ledger_payment_created", table_name="payment_ledger_entries")
    op.drop_table("payment_ledger_entries")
    op.drop_index("ix_payment_attempt_status_created", table_name="payment_attempts")
    op.drop_index("ix_payment_attempt_booking_created", table_name="payment_attempts")
    op.drop_table("payment_attempts")
