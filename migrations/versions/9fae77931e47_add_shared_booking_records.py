"""add shared booking records

Revision ID: 9fae77931e47
Revises: a61f0c9e72bd
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9fae77931e47"
down_revision: Union[str, Sequence[str], None] = "a61f0c9e72bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("inventory_hold_id", sa.Integer(), nullable=False),
        sa.Column("buyer_account_id", sa.Integer(), nullable=False),
        sa.Column("agreed_amount", sa.Numeric(14, 2), nullable=True),
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
            server_default="requested",
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "active_inventory_item_id",
            sa.Integer(),
            sa.Computed(
                "CASE WHEN status IN "
                "('requested', 'awaiting_payment', 'confirmed') "
                "THEN inventory_item_id ELSE NULL END",
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
            "status IN ('requested', 'awaiting_payment', "
            "'confirmed', 'cancelled', 'expired')",
            name="ck_booking_status",
        ),
        sa.CheckConstraint(
            "agreed_amount IS NULL OR agreed_amount > 0",
            name="ck_booking_agreed_amount_positive",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["real_estate_transactions.id"],
            name="fk_booking_transaction",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["property_inventory_items.id"],
            name="fk_booking_inventory_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_hold_id"],
            ["inventory_holds.id"],
            name="fk_booking_inventory_hold",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_account_id"],
            ["user_accounts.id"],
            name="fk_booking_buyer",
            ondelete="RESTRICT",
        ),
sa.UniqueConstraint(
            "inventory_hold_id",
            name="uq_booking_inventory_hold",
        ),
    )

    op.create_index(
        "uq_booking_transaction",
        "bookings",
        ["transaction_id"],
        unique=True,
    )
    op.create_index(
        "uq_booking_active_inventory_item",
        "bookings",
        ["active_inventory_item_id"],
        unique=True,
    )
    op.create_index(
        "ix_booking_buyer_created",
        "bookings",
        ["buyer_account_id", "created_at"],
    )
    op.create_index(
        "ix_booking_status_expiry",
        "bookings",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_table("bookings")
