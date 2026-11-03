"""Add canonical account identities and preserve legacy Buyer profiles."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7aae125dd628"
down_revision: Union[str, Sequence[str], None] = "1ef93a206d06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _preflight(connection):
    """Validate legacy identities before making any schema changes."""

    profiles = connection.execute(
        sa.text(
            """
            SELECT id, auth_provider, auth_subject, full_name
            FROM buyer_profiles
            ORDER BY id
            """
        )
    ).mappings().all()

    account_ids = {
        int(value)
        for value in connection.execute(
            sa.text("SELECT id FROM user_accounts")
        ).scalars()
    }

    password_profile_accounts = []

    for profile in profiles:
        provider = profile["auth_provider"]
        subject = profile["auth_subject"]

        if not isinstance(subject, str) or not subject.strip():
            raise RuntimeError(
                f"Buyer profile {profile['id']} has an invalid identity subject."
            )

        if provider == "local_password":
            if not subject.isascii() or not subject.isdigit():
                raise RuntimeError(
                    f"Buyer profile {profile['id']} has an invalid password identity."
                )

            account_id = int(subject)

            if account_id not in account_ids:
                raise RuntimeError(
                    f"Buyer profile {profile['id']} references a missing account."
                )

            password_profile_accounts.append(account_id)

        elif provider not in {"google", "local_otp"}:
            raise RuntimeError(
                f"Unsupported legacy provider in Buyer profile {profile['id']}: "
                f"{provider!r}"
            )

    if len(password_profile_accounts) != len(set(password_profile_accounts)):
        raise RuntimeError(
            "Multiple legacy Buyer profiles resolve to the same password account."
        )

    return profiles, account_ids


def _insert_identity(connection, account_id, provider, subject):
    connection.execute(
        sa.text(
            """
            INSERT INTO auth_identities
                (account_id, provider, provider_subject)
            VALUES
                (:account_id, :provider, :subject)
            """
        ),
        {
            "account_id": account_id,
            "provider": provider,
            "subject": subject,
        },
    )


def _create_buyer_account(connection, full_name, phone=None):
    result = connection.execute(
        sa.text(
            """
            INSERT INTO user_accounts
                (full_name, email, phone, password_hash, role)
            VALUES
                (:full_name, NULL, :phone, NULL, 'buyer')
            """
        ),
        {
            "full_name": full_name,
            "phone": phone,
        },
    )

    account_id = result.lastrowid

    if account_id is None:
        raise RuntimeError(
            "Database did not return the newly created canonical account ID."
        )

    return int(account_id)


def upgrade() -> None:
    connection = op.get_bind()

    # Validate all legacy profiles before performing DDL.
    profiles, existing_account_ids = _preflight(connection)

    # Google-first and OTP-first accounts must support missing credentials.
    with op.batch_alter_table("user_accounts") as batch:
        batch.alter_column(
            "full_name",
            existing_type=sa.String(160),
            existing_nullable=False,
            nullable=True,
        )
        batch.alter_column(
            "email",
            existing_type=sa.String(254),
            existing_nullable=False,
            nullable=True,
        )
        batch.alter_column(
            "phone",
            existing_type=sa.String(20),
            existing_nullable=False,
            nullable=True,
        )
        batch.alter_column(
            "password_hash",
            existing_type=sa.String(255),
            existing_nullable=False,
            nullable=True,
        )

    # Add the profile relationship temporarily as nullable.
    op.add_column(
        "buyer_profiles",
        sa.Column("account_id", sa.Integer(), nullable=True),
    )

    # Create the identity registry.
    op.create_table(
        "auth_identities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name="fk_auth_identities_account_id_user_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "provider_subject",
            name="uq_auth_identity_provider_subject",
        ),
    )

    op.create_index(
        "ix_auth_identities_account_id",
        "auth_identities",
        ["account_id"],
        unique=False,
    )

    # Every pre-existing password account retains its original account ID.
    for account_id in sorted(existing_account_ids):
        _insert_identity(
            connection,
            account_id,
            "local_password",
            str(account_id),
        )

    # Backfill legacy Buyer profiles without merging by email or phone.
    for profile in profiles:
        profile_id = profile["id"]
        provider = profile["auth_provider"]
        subject = profile["auth_subject"]

        if provider == "local_password":
            account_id = int(subject)

        elif provider == "google":
            # The old profile does not store the Google email.
            # Do not invent it or link by email.
            account_id = _create_buyer_account(
                connection,
                full_name=profile["full_name"],
            )

            _insert_identity(
                connection,
                account_id,
                "google",
                subject,
            )

        elif provider == "local_otp":
            account_id = _create_buyer_account(
                connection,
                full_name=profile["full_name"],
                phone=subject,
            )

            _insert_identity(
                connection,
                account_id,
                "local_otp",
                subject,
            )

        else:
            raise RuntimeError(
                f"Unsupported provider while backfilling profile {profile_id}."
            )

        updated = connection.execute(
            sa.text(
                """
                UPDATE buyer_profiles
                SET account_id = :account_id
                WHERE id = :profile_id
                """
            ),
            {
                "account_id": account_id,
                "profile_id": profile_id,
            },
        )

        if updated.rowcount != 1:
            raise RuntimeError(
                f"Could not link Buyer profile {profile_id}."
            )

    # Verify every existing profile was linked successfully.
    remaining = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM buyer_profiles
            WHERE account_id IS NULL
            """
        )
    ).scalar_one()

    if remaining:
        raise RuntimeError(
            f"Canonical account backfill incomplete: {remaining} unlinked profiles."
        )

    duplicates = connection.execute(
        sa.text(
            """
            SELECT account_id
            FROM buyer_profiles
            GROUP BY account_id
            HAVING COUNT(*) > 1
            """
        )
    ).all()

    if duplicates:
        raise RuntimeError(
            "Multiple Buyer profiles resolve to the same canonical account."
        )

    # Enforce one Buyer profile per account and a mandatory relationship.
    with op.batch_alter_table("buyer_profiles") as batch:
        batch.create_unique_constraint(
            "uq_buyer_profile_account",
            ["account_id"],
        )
        batch.create_foreign_key(
            "fk_buyer_profiles_account_id_user_accounts",
            "user_accounts",
            ["account_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.alter_column(
            "account_id",
            existing_type=sa.Integer(),
            existing_nullable=True,
            nullable=False,
        )


def downgrade() -> None:
    raise RuntimeError(
        "This migration is intentionally irreversible because it creates "
        "canonical accounts and identity mappings. Restore a verified backup "
        "to reverse this migration safely."
    )
