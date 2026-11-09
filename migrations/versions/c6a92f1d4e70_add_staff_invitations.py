"""Create staff invitations."""

from alembic import op
import sqlalchemy as sa


revision = "c6a92f1d4e70"
down_revision = "b84c7e2a91d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_invitations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by_account_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('developer', 'channel partner')",
            name="ck_staff_invitations_role",
        ),
        sa.CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL",
            name="ck_staff_invitations_terminal_state",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_account_id"],
            ["user_accounts.id"],
            name="fk_staff_invitations_invited_by_user_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_staff_invitations_token_hash",
        ),
    )

    op.create_index(
        "ix_staff_invitations_email_created_at",
        "staff_invitations",
        ["email", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_invitations_email_created_at",
        table_name="staff_invitations",
    )
    op.drop_table("staff_invitations")
