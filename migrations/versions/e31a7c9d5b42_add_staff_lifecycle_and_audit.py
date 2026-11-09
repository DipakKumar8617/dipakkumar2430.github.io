"""Add staff activation and administrative audit history."""

from alembic import op
import sqlalchemy as sa


revision = "e31a7c9d5b42"
down_revision = "c6a92f1d4e70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_accounts",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )

    op.create_table(
        "staff_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_account_id", sa.Integer(), nullable=False),
        sa.Column("target_account_id", sa.Integer(), nullable=True),
        sa.Column("invitation_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_account_id"],
            ["user_accounts.id"],
            name="fk_staff_audit_logs_actor_user_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_account_id"],
            ["user_accounts.id"],
            name="fk_staff_audit_logs_target_user_accounts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["staff_invitations.id"],
            name="fk_staff_audit_logs_invitation_staff_invitations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_staff_audit_logs_created_at",
        "staff_audit_logs",
        ["created_at"],
    )
    op.create_index(
        "ix_staff_audit_logs_target_created_at",
        "staff_audit_logs",
        ["target_account_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_audit_logs_target_created_at",
        table_name="staff_audit_logs",
    )
    op.drop_index(
        "ix_staff_audit_logs_created_at",
        table_name="staff_audit_logs",
    )
    op.drop_table("staff_audit_logs")
    op.drop_column("user_accounts", "is_active")
