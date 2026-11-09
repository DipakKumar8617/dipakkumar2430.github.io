
"""Add buyer saved searches and recently viewed properties."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9d7a4c2e6b31"
down_revision: Union[str, Sequence[str], None] = "4c8f2a1d9b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_property_searches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name="fk_saved_property_searches_account_id_user_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "name",
            name="uq_saved_property_search_account_name",
        ),
    )

    op.create_table(
        "recently_viewed_properties",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("property_listing_id", sa.Integer(), nullable=False),
        sa.Column(
            "viewed_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name="fk_recently_viewed_account_id_user_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["property_listing_id"],
            ["property_listings.id"],
            name="fk_recently_viewed_listing_id_property_listings",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "property_listing_id",
            name="uq_recently_viewed_account_listing",
        ),
    )

    op.create_index(
        "ix_recently_viewed_account_viewed_at",
        "recently_viewed_properties",
        ["account_id", "viewed_at"],
        unique=False,
    )

    op.create_index(
        "ix_recently_viewed_properties_property_listing_id",
        "recently_viewed_properties",
        ["property_listing_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recently_viewed_properties_property_listing_id",
        table_name="recently_viewed_properties",
    )
    op.drop_index(
        "ix_recently_viewed_account_viewed_at",
        table_name="recently_viewed_properties",
    )
    op.drop_table("recently_viewed_properties")
    op.drop_table("saved_property_searches")
