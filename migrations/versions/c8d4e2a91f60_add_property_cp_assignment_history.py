"""Retain property Channel Partner assignment history."""

from alembic import op
import sqlalchemy as sa


revision = "c8d4e2a91f60"
down_revision = "a7c3e91f5b20"
branch_labels = None
depends_on = None


TABLE = "property_channel_partner_assignments"
PROPERTY_FK_INDEX = "ix_property_cp_assignment_property"


def upgrade() -> None:
    # Batch mode supports SQLite table recreation and normal MySQL ALTERs.
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.add_column(
            sa.Column("ended_at", sa.DateTime(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("ended_by_account_id", sa.Integer(), nullable=True),
        )
        batch_op.create_foreign_key(
            "fk_property_cp_assignment_ended_by",
            "user_accounts",
            ["ended_by_account_id"],
            ["id"],
            ondelete="RESTRICT",
        )

        # Preserve the property FK support index before dropping the
        # original unique constraint.
        batch_op.create_index(
            PROPERTY_FK_INDEX,
            ["property_listing_id"],
            unique=False,
        )
        batch_op.drop_constraint(
            "uq_property_cp_assignment_property",
            type_="unique",
        )
        batch_op.add_column(
            sa.Column(
                "active_property_listing_id",
                sa.Integer(),
                sa.Computed(
                    "CASE WHEN ended_at IS NULL "
                    "THEN property_listing_id ELSE NULL END",
                    persisted=True,
                ),
                nullable=True,
            )
        )
        batch_op.create_index(
            "uq_property_cp_assignment_active_property",
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

    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.drop_index(
            "uq_property_cp_assignment_active_property",
        )
        batch_op.drop_column("active_property_listing_id")
        batch_op.drop_constraint(
            "fk_property_cp_assignment_ended_by",
            type_="foreignkey",
        )
        batch_op.drop_column("ended_by_account_id")
        batch_op.drop_column("ended_at")
        batch_op.create_unique_constraint(
            "uq_property_cp_assignment_property",
            ["property_listing_id"],
        )
        batch_op.drop_index(PROPERTY_FK_INDEX)
