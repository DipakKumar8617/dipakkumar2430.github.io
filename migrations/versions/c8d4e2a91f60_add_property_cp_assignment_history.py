"""Retain property Channel Partner assignment history."""

from alembic import op
import sqlalchemy as sa


revision = "c8d4e2a91f60"
down_revision = "a7c3e91f5b20"
branch_labels = None
depends_on = None


TABLE = "property_channel_partner_assignments"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("ended_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("ended_by_account_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_property_cp_assignment_ended_by",
        TABLE,
        "user_accounts",
        ["ended_by_account_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "uq_property_cp_assignment_property",
        TABLE,
        type_="unique",
    )

    op.add_column(
        TABLE,
        sa.Column(
            "active_property_listing_id",
            sa.Integer(),
            sa.Computed(
                "CASE WHEN ended_at IS NULL "
                "THEN property_listing_id ELSE NULL END",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_property_cp_assignment_active_property",
        TABLE,
        ["active_property_listing_id"],
        unique=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    duplicate_history = connection.execute(
        sa.text(
            f"""
            SELECT property_listing_id
            FROM {TABLE}
            GROUP BY property_listing_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()

    if duplicate_history is not None:
        raise RuntimeError(
            "Cannot downgrade property CP assignment history: "
            "at least one property has multiple assignment records. "
            "Downgrading would require deleting historical assignments."
        )

    op.drop_index(
        "uq_property_cp_assignment_active_property",
        table_name=TABLE,
    )
    op.drop_column(TABLE, "active_property_listing_id")
    op.drop_constraint(
        "fk_property_cp_assignment_ended_by",
        TABLE,
        type_="foreignkey",
    )
    op.drop_column(TABLE, "ended_by_account_id")
    op.drop_column(TABLE, "ended_at")
    op.create_unique_constraint(
        "uq_property_cp_assignment_property",
        TABLE,
        ["property_listing_id"],
    )
