import getpass
import re
import sys

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.app.database import SessionLocal, engine
from backend.app.models import AuthIdentity, UserAccount
from backend.app.security import hash_password


LOCK_NAME = "realiq_admin_bootstrap"
LOCK_TIMEOUT_SECONDS = 10


def prompt_details():
    full_name = input("Admin full name: ").strip()
    if not 2 <= len(full_name) <= 160:
        raise ValueError("Full name must contain between 2 and 160 characters.")

    email = input("Admin email: ").strip().lower()
    if (
        len(email) > 254
        or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
    ):
        raise ValueError("Enter a valid email address.")

    password = getpass.getpass("Admin password (12-128 characters): ")
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain between 12 and 128 characters.")

    confirmation = getpass.getpass("Confirm admin password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match.")

    return full_name, email, password


def bootstrap_admin(full_name: str, email: str, password: str) -> int:
    if engine.dialect.name != "mysql":
        raise RuntimeError(
            "Admin bootstrap supports MySQL only. No account was created."
        )

    lock_connection = engine.connect()
    lock_acquired = False

    try:
        lock_result = lock_connection.execute(
            text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
            {
                "lock_name": LOCK_NAME,
                "timeout_seconds": LOCK_TIMEOUT_SECONDS,
            },
        ).scalar_one()

        if lock_result == 1:
            lock_acquired = True

        lock_connection.commit()

        if lock_result != 1:
            raise RuntimeError(
                "Could not acquire the admin bootstrap lock. "
                "Another bootstrap may be running."
            )

        lock_acquired = True

        with SessionLocal.begin() as db:
            existing_admin = db.scalar(
                select(UserAccount.id)
                .where(func.lower(UserAccount.role) == "admin")
                .limit(1)
            )
            if existing_admin is not None:
                raise RuntimeError(
                    "An Admin account already exists. Bootstrap is refused."
                )

            existing_email = db.scalar(
                select(UserAccount.id)
                .where(func.lower(UserAccount.email) == email)
                .limit(1)
            )
            if existing_email is not None:
                raise RuntimeError(
                    "This email already belongs to an account. "
                    "Bootstrap will not promote or modify existing accounts."
                )

            account = UserAccount(
                full_name=full_name,
                email=email,
                password_hash=hash_password(password),
                role="admin",
            )
            db.add(account)
            db.flush()

            db.add(
                AuthIdentity(
                    account_id=account.id,
                    provider="local_password",
                    provider_subject=str(account.id),
                )
            )
            db.flush()

            account_id = account.id

        return account_id

    except IntegrityError as exc:
        raise RuntimeError(
            "The database rejected the account creation. "
            "No partial account was committed."
        ) from exc
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "A database error prevented admin bootstrap."
        ) from exc
    finally:
        if lock_acquired:
            try:
                released = lock_connection.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": LOCK_NAME},
                ).scalar_one()
                lock_connection.commit()

                if released != 1:
                    raise RuntimeError(
                        "Admin bootstrap finished, but the MySQL lock "
                        "could not be confirmed as released. The connection "
                        "will be invalidated for safety."
                    )
            except Exception:
                lock_connection.invalidate()
                raise
            finally:
                lock_connection.close()
        else:
            lock_connection.close()


def main():
    try:
        full_name, email, password = prompt_details()
        account_id = bootstrap_admin(full_name, email, password)
    except (ValueError, RuntimeError) as exc:
        print(f"\nAdmin bootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nAdmin bootstrap cancelled.", file=sys.stderr)
        raise SystemExit(1)

    print("\nAdmin account created successfully.")
    print(f"Account ID: {account_id}")
    print("You can now sign in using the Admin role.")


if __name__ == "__main__":
    main()
