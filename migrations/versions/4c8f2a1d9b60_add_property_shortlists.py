
"""Create buyer property shortlists."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4c8f2a1d9b60"
down_revision: Union[str, Sequence[str], None] = "7aae125dd628"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "property_shortlists",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("property_listing_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name="fk_property_shortlists_account_id_user_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["property_listing_id"],
            ["property_listings.id"],
            name="fk_property_shortlists_listing_id_property_listings",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "property_listing_id",
            name="uq_property_shortlist_account_listing",
        ),
    )

    op.create_index(
        "ix_property_shortlists_property_listing_id",
        "property_shortlists",
        ["property_listing_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_property_shortlists_property_listing_id",
        table_name="property_shortlists",
    )
    op.drop_table("property_shortlists")
