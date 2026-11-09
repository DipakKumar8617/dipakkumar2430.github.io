"""Add buyer site visits.

Revision ID: b84c7e2a91d3
Revises: 9d7a4c2e6b31
"""

from alembic import op
import sqlalchemy as sa


revision = "b84c7e2a91d3"
down_revision = "9d7a4c2e6b31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "buyer_site_visits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("property_listing_id", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'requested'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'confirmed', 'completed', 'cancelled', 'rejected')",
            name="ck_buyer_site_visits_status",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name="fk_buyer_site_visits_account_id_user_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["property_listing_id"],
            ["property_listings.id"],
            name="fk_buyer_site_visits_listing_id_property_listings",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_buyer_site_visits_account_scheduled_at",
        "buyer_site_visits",
        ["account_id", "scheduled_at"],
        unique=False,
    )
    op.create_index(
        "ix_buyer_site_visits_property_listing_id",
        "buyer_site_visits",
        ["property_listing_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_buyer_site_visits_property_listing_id",
        table_name="buyer_site_visits",
    )
    op.drop_index(
        "ix_buyer_site_visits_account_scheduled_at",
        table_name="buyer_site_visits",
    )
    op.drop_table("buyer_site_visits")
