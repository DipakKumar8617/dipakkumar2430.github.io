"""add organization ownership to property listings

Revision ID: d4e8a7c1f209
Revises: b11f6d9954ae
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e8a7c1f209"
down_revision: Union[str, Sequence[str], None] = "b11f6d9954ae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "property_listings",
        sa.Column("developer_organization_id", sa.Integer(), nullable=True),
    )

    with op.batch_alter_table("property_listings") as batch:
        batch.create_foreign_key(
            "fk_property_listings_developer_organization",
            "developer_organizations",
            ["developer_organization_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_index(
        "ix_property_listings_developer_organization_id",
        "property_listings",
        ["developer_organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_property_listings_developer_organization_id",
        table_name="property_listings",
    )

    with op.batch_alter_table("property_listings") as batch:
        batch.drop_constraint(
            "fk_property_listings_developer_organization",
            type_="foreignkey",
        )

    op.drop_column("property_listings", "developer_organization_id")
