from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import AuthIdentity, UserAccount


def resolve_session_account(
    request: Request,
    db: Session,
) -> UserAccount:
    """Resolve a session to its active canonical database account."""

    user = request.session.get("user")

    if not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="Login required.")

    provider = user.get("provider")

    if provider == "google":
        subject = user.get("providerId")
    elif provider == "local_otp":
        subject = user.get("phone")
    elif provider == "local_password":
        subject = user.get("providerId")
    else:
        subject = None

    if not isinstance(subject, str) or not subject.strip():
        raise HTTPException(
            status_code=401,
            detail="Valid account identity required.",
        )

    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == provider,
            AuthIdentity.provider_subject == subject,
        )
    )

    if identity is None:
        raise HTTPException(
            status_code=401,
            detail="Account identity is not linked. Please sign in again.",
        )

    session_account_id = user.get("account_id")

    if (
        session_account_id is None
        or str(session_account_id) != str(identity.account_id)
    ):
        raise HTTPException(
            status_code=401,
            detail="Session identity does not match the account.",
        )

    account = db.get(UserAccount, identity.account_id)

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="Account not found. Please sign in again.",
        )

    if not account.is_active:
        raise HTTPException(
            status_code=403,
            detail="This account is deactivated. Contact an administrator.",
        )

    user["account_id"] = account.id
    request.session["user"] = user

    return account



def require_developer(
    request: Request,
    db: Session = Depends(get_db),
) -> UserAccount:
    """Require an active account whose canonical database role is developer."""

    account = resolve_session_account(request, db)

    if str(account.role or "").strip().casefold() != "developer":
        raise HTTPException(
            status_code=403,
            detail="Developer access required.",
        )

    user = request.session["user"]
    user["account_id"] = account.id
    user["role"] = "Developer"
    request.session["user"] = user

    return account
