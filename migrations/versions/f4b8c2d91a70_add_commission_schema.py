"""Add commission rules and commission ledger entries.

Revision ID: f4b8c2d91a70
Revises: c8f4a2d91e60
"""
from alembic import op
import sqlalchemy as sa

revision = "f4b8c2d91a70"
down_revision = "c8f4a2d91e60"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "commission_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("rate_bps", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_by_account_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "rate_bps >= 0 AND rate_bps <= 10000",
            name="ck_commission_rules_rate_bps",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_commission_rules_effective_window",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_account_id"], ["user_accounts.id"],
            name="fk_commission_rules_created_by_account",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_commission_rules_effective",
        "commission_rules",
        ["is_active", "starts_at", "ends_at"],
    )

    op.create_table(
        "commission_ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_partner_account_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("payment_attempt_id", sa.Integer(), nullable=False),
        sa.Column("source_payment_ledger_entry_id", sa.Integer(), nullable=False),
        sa.Column("original_accrual_id", sa.Integer(), nullable=True),
        sa.Column("commission_rule_id", sa.Integer(), nullable=True),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("basis_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("rate_bps", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("approval_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewed_by_account_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "entry_type IN ('accrual', 'reversal')",
            name="ck_commission_ledger_entry_type",
        ),
        sa.CheckConstraint(
            "amount > 0 AND basis_amount > 0",
            name="ck_commission_ledger_positive_amounts",
        ),
        sa.CheckConstraint(
            "rate_bps >= 0 AND rate_bps <= 10000",
            name="ck_commission_ledger_rate_bps",
        ),
        sa.CheckConstraint(
            "approval_status IN ('pending', 'approved', 'rejected')",
            name="ck_commission_ledger_approval_status",
        ),
        sa.UniqueConstraint(
            "source_payment_ledger_entry_id",
            name="uq_commission_ledger_source_payment_entry",
        ),
        sa.ForeignKeyConstraint(
            ["channel_partner_account_id"], ["user_accounts.id"],
            name="fk_commission_ledger_channel_partner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["real_estate_transactions.id"],
            name="fk_commission_ledger_transaction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"], ["payment_attempts.id"],
            name="fk_commission_ledger_payment_attempt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_payment_ledger_entry_id"], ["payment_ledger_entries.id"],
            name="fk_commission_ledger_source_payment_entry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["original_accrual_id"], ["commission_ledger_entries.id"],
            name="fk_commission_ledger_original_accrual",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["commission_rule_id"], ["commission_rules.id"],
            name="fk_commission_ledger_rule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_account_id"], ["user_accounts.id"],
            name="fk_commission_ledger_reviewer",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_commission_ledger_cp_created",
        "commission_ledger_entries",
        ["channel_partner_account_id", "created_at"],
    )
    op.create_index(
        "ix_commission_ledger_approval_created",
        "commission_ledger_entries",
        ["approval_status", "created_at"],
    )


def downgrade():
    op.drop_index(
        "ix_commission_ledger_approval_created",
        table_name="commission_ledger_entries",
    )
    op.drop_index(
        "ix_commission_ledger_cp_created",
        table_name="commission_ledger_entries",
    )
    op.drop_table("commission_ledger_entries")
    op.drop_index(
        "ix_commission_rules_effective",
        table_name="commission_rules",
    )
    op.drop_table("commission_rules")
