"""add transaction offers

Revision ID: a61f0c9e72bd
Revises: f78bbb2103d3
Create Date: 2026-11-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a61f0c9e72bd"
down_revision: Union[str, Sequence[str], None] = "f78bbb2103d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transaction_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("proposer_account_id", sa.Integer(), nullable=False),
        sa.Column("parent_offer_id", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default="INR",
        ),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.Column(
            "active_pending_transaction_id",
            sa.Integer(),
            sa.Computed(
                "CASE WHEN status = 'pending' "
                "THEN transaction_id ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_transaction_offer_amount_positive",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', "
            "'countered', 'withdrawn', 'expired')",
            name="ck_transaction_offer_status",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["real_estate_transactions.id"],
            name="fk_transaction_offer_transaction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["proposer_account_id"],
            ["user_accounts.id"],
            name="fk_transaction_offer_proposer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_offer_id"],
            ["transaction_offers.id"],
            name="fk_transaction_offer_parent",
            ondelete="RESTRICT",
        ),
    )

    op.create_index(
        "uq_transaction_offer_active_pending",
        "transaction_offers",
        ["active_pending_transaction_id"],
        unique=True,
    )
    op.create_index(
        "ix_transaction_offer_transaction_created",
        "transaction_offers",
        ["transaction_id", "created_at"],
    )
    op.create_index(
        "ix_transaction_offer_proposer_created",
        "transaction_offers",
        ["proposer_account_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transaction_offer_proposer_created",
        table_name="transaction_offers",
    )
    op.drop_index(
        "ix_transaction_offer_transaction_created",
        table_name="transaction_offers",
    )
    op.drop_index(
        "uq_transaction_offer_active_pending",
        table_name="transaction_offers",
    )
    op.drop_table("transaction_offers")
