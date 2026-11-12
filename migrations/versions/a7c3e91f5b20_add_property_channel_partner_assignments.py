"""Add exclusive property-to-Channel-Partner assignments."""

from alembic import op
import sqlalchemy as sa


revision = "a7c3e91f5b20"
down_revision = "f2c9a6d81b43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "property_channel_partner_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("property_listing_id", sa.Integer(), nullable=False),
        sa.Column("channel_partner_account_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by_account_id", sa.Integer(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["property_listing_id"],
            ["property_listings.id"],
            name="fk_property_cp_assignment_property",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["channel_partner_account_id"],
            ["user_accounts.id"],
            name="fk_property_cp_assignment_partner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_account_id"],
            ["user_accounts.id"],
            name="fk_property_cp_assignment_admin",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "property_listing_id",
            name="uq_property_cp_assignment_property",
        ),
    )
    op.create_index(
        "ix_property_cp_assignment_partner",
        "property_channel_partner_assignments",
        ["channel_partner_account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_property_cp_assignment_partner",
        table_name="property_channel_partner_assignments",
    )
    op.drop_table("property_channel_partner_assignments")
