from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fee0d9aa9c3f"
down_revision: Union[str, Sequence[str], None] = "c8d4e2a91f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing listings remain single-item listings by default.
    with op.batch_alter_table("property_listings") as batch_op:
        batch_op.add_column(
            sa.Column(
                "inventory_mode",
                sa.String(length=20),
                nullable=False,
                server_default="single",
            )
        )
        batch_op.create_check_constraint(
            "ck_property_listing_inventory_mode",
            "inventory_mode IN ('single', 'multi_unit')",
        )

    op.create_table(
        "property_inventory_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_listing_id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=20), nullable=False),
        sa.Column("unit_code", sa.String(length=50), nullable=True),
        sa.Column("price_inr", sa.BigInteger(), nullable=True),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("area_sqft", sa.Integer(), nullable=True),
        sa.Column(
            "availability_status",
            sa.String(length=20),
            nullable=False,
            server_default="available",
        ),
        sa.Column(
            "single_property_listing_id",
            sa.Integer(),
            sa.Computed(
                "CASE WHEN item_type = 'single' "
                "THEN property_listing_id ELSE NULL END",
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
            "item_type IN ('single', 'unit')",
            name="ck_property_inventory_item_type",
        ),
        sa.CheckConstraint(
            "(item_type = 'single' AND unit_code IS NULL) OR "
            "(item_type = 'unit' AND unit_code IS NOT NULL)",
            name="ck_property_inventory_item_unit_code",
        ),
        sa.CheckConstraint(
            "availability_status IN ('available', 'reserved', 'sold')",
            name="ck_property_inventory_item_availability",
        ),
        sa.CheckConstraint(
            "price_inr IS NULL OR price_inr >= 0",
            name="ck_property_inventory_item_price",
        ),
        sa.CheckConstraint(
            "bedrooms IS NULL OR bedrooms >= 0",
            name="ck_property_inventory_item_bedrooms",
        ),
        sa.CheckConstraint(
            "area_sqft IS NULL OR area_sqft > 0",
            name="ck_property_inventory_item_area",
        ),
        sa.UniqueConstraint(
            "property_listing_id",
            "unit_code",
            name="uq_property_inventory_listing_unit_code",
        ),
        sa.ForeignKeyConstraint(
            ["property_listing_id"],
            ["property_listings.id"],
            name="fk_property_inventory_item_listing",
            ondelete="RESTRICT",
        ),
    )

    op.create_index(
        "uq_property_inventory_single_listing",
        "property_inventory_items",
        ["single_property_listing_id"],
        unique=True,
    )
    op.create_index(
        "ix_property_inventory_listing",
        "property_inventory_items",
        ["property_listing_id"],
    )

    # Preserve existing listing details and availability in the canonical
    # inventory record. This is a one-time migration backfill.
    op.execute(
        sa.text(
            """
            INSERT INTO property_inventory_items (
                property_listing_id,
                item_type,
                unit_code,
                price_inr,
                bedrooms,
                area_sqft,
                availability_status,
                created_at,
                updated_at
            )
            SELECT
                id,
                'single',
                NULL,
                price_inr,
                bedrooms,
                area_sqft,
                availability_status,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM property_listings
            """
        )
    )

    op.create_table(
        "inventory_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("buyer_account_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "active_inventory_item_id",
            sa.Integer(),
            sa.Computed(
                "CASE WHEN status = 'active' "
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
            "status IN ('active', 'converted', 'released', 'expired')",
            name="ck_inventory_hold_status",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["property_inventory_items.id"],
            name="fk_inventory_hold_item",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_account_id"],
            ["user_accounts.id"],
            name="fk_inventory_hold_buyer",
            ondelete="RESTRICT",
        ),
    )

    op.create_index(
        "uq_inventory_hold_active_item",
        "inventory_holds",
        ["active_inventory_item_id"],
        unique=True,
    )
    op.create_index(
        "ix_inventory_hold_item_expiry",
        "inventory_holds",
        ["inventory_item_id", "status", "expires_at"],
    )
    op.create_index(
        "ix_inventory_hold_buyer_created",
        "inventory_holds",
        ["buyer_account_id", "created_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()

    # Never silently discard holds or multi-unit inventory.
    hold_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM inventory_holds")
    ).scalar_one()

    multi_unit_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM property_listings "
            "WHERE inventory_mode <> 'single'"
        )
    ).scalar_one()

    unit_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM property_inventory_items "
            "WHERE item_type <> 'single'"
        )
    ).scalar_one()

    missing_item_count = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM property_listings AS p
            WHERE (
                SELECT COUNT(*)
                FROM property_inventory_items AS i
                WHERE i.property_listing_id = p.id
                  AND i.item_type = 'single'
            ) <> 1
            """
        )
    ).scalar_one()

    changed_item_count = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM property_inventory_items AS i
            JOIN property_listings AS p
              ON p.id = i.property_listing_id
            WHERE
                (i.price_inr <> p.price_inr)
                OR (i.price_inr IS NULL AND p.price_inr IS NOT NULL)
                OR (i.price_inr IS NOT NULL AND p.price_inr IS NULL)
                OR (i.bedrooms <> p.bedrooms)
                OR (i.bedrooms IS NULL AND p.bedrooms IS NOT NULL)
                OR (i.bedrooms IS NOT NULL AND p.bedrooms IS NULL)
                OR (i.area_sqft <> p.area_sqft)
                OR (i.area_sqft IS NULL AND p.area_sqft IS NOT NULL)
                OR (i.area_sqft IS NOT NULL AND p.area_sqft IS NULL)
                OR (i.availability_status <> p.availability_status)
            """
        )
    ).scalar_one()

    if any(
        (
            hold_count,
            multi_unit_count,
            unit_count,
            missing_item_count,
            changed_item_count,
        )
    ):
        raise RuntimeError(
            "Downgrade blocked: inventory holds, multi-unit records, "
            "missing inventory items, or inventory changes exist. "
            "Preserve the data and resolve these records before downgrading."
        )

    # Dropping each table also removes its indexes and foreign keys.
    op.drop_table("inventory_holds")
    op.drop_table("property_inventory_items")

    with op.batch_alter_table("property_listings") as batch_op:
        batch_op.drop_constraint(
            "ck_property_listing_inventory_mode",
            type_="check",
        )
        batch_op.drop_column("inventory_mode")
