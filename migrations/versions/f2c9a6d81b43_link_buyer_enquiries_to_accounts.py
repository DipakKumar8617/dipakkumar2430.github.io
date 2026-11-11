"""link visitor enquiries to canonical buyer accounts

Revision ID: f2c9a6d81b43
Revises: d4e8a7c1f209
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2c9a6d81b43"
down_revision: Union[str, Sequence[str], None] = "d4e8a7c1f209"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("buyer_enquiries") as batch:
        batch.add_column(
            sa.Column("account_id", sa.Integer(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_buyer_enquiries_account",
            "user_accounts",
            ["account_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.create_index(
        "ix_buyer_enquiries_account_id",
        "buyer_enquiries",
        ["account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_buyer_enquiries_account_id",
        table_name="buyer_enquiries",
    )

    with op.batch_alter_table("buyer_enquiries") as batch:
        batch.drop_constraint(
            "fk_buyer_enquiries_account",
            type_="foreignkey",
        )
        batch.drop_column("account_id")
