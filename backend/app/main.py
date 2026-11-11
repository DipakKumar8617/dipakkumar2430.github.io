import hashlib
import hmac
import secrets
import re
import time
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .config import APP_ENV, GOOGLE_CLIENT_ID, PROJECT_ROOT
from .config import os
from .buyer import authenticated_account, find_profile, router as buyer_router
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .database import get_db
from .auth_dependencies import resolve_session_account
from .models import AuthIdentity, BuyerEnquiry, UserAccount
from .security import hash_password, verify_password
from .admin import admin_router, invitation_router
from .developer_organizations import router as developer_organizations_router
from .developer_inventory import router as developer_inventory_router

app = FastAPI(title="RealIQ Authentication API")
app.include_router(buyer_router)
app.include_router(admin_router)
app.include_router(invitation_router)
app.include_router(developer_organizations_router)
app.include_router(developer_inventory_router)

SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is missing from the project .env file.")

if APP_ENV != "development" and not GOOGLE_CLIENT_ID:
    raise RuntimeError("GOOGLE_CLIENT_ID must be configured outside development.")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="realiq_session",
    max_age=60 * 60 * 8,
    same_site="lax",
    https_only=APP_ENV != "development",
)

# Local development OTPs are held in memory; restarting the server clears them.
otp_records = {}
OTP_TTL_SECONDS = 300
OTP_RESEND_SECONDS = 30
OTP_MAX_ATTEMPTS = 5


class GoogleLogin(BaseModel):
    credential: str = Field(min_length=20, max_length=10000)
    purpose: Literal["buyer"] | None = None


class OTPRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=20)
    purpose: Literal["buyer"] | None = None


class OTPVerify(BaseModel):
    phone: str = Field(min_length=7, max_length=20)
    code: str = Field(pattern=r"^\d{6}$")


class ManualBuyerRegistration(BaseModel):
    fullName: str = Field(min_length=2, max_length=160)
    phone: str = Field(min_length=7, max_length=20)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    confirmPassword: str = Field(min_length=8, max_length=128)
    role: str = Field(default="buyer", max_length=30)
    property_id: int | None = Field(default=None, ge=1, le=9999999999)


class ManualBuyerLogin(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    property_id: int | None = Field(default=None, ge=1, le=9999999999)

class StaffLogin(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    role: Literal["Channel Partner", "Developer", "Admin"]

def normalize_phone(value: str) -> str:
    phone = "".join(char for char in value if char.isdigit())
    if not 7 <= len(phone) <= 15:
        raise HTTPException(status_code=400, detail="Enter a valid phone number.")
    return phone


def set_session(request: Request, user: dict) -> None:
    request.session.clear()
    request.session["user"] = user
    request.session["authenticated_at"] = int(time.time())


def authenticated_staff_account(
    request: Request,
    db: Session,
) -> UserAccount:
    """Authenticate staff using the canonical database identity."""

    user = request.session.get("user")

    if not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="Login required.")

    if str(user.get("role", "")).strip().casefold() == "unassigned":
        raise HTTPException(
            status_code=403,
            detail="An approved staff role is required.",
        )

    account = resolve_session_account(request, db)

    database_role = str(account.role or "").strip().casefold()
    role_names = {
        "developer": "Developer",
        "channel partner": "Channel Partner",
        "admin": "Admin",
    }

    if database_role not in role_names:
        raise HTTPException(
            status_code=403,
            detail="An approved staff role is required.",
        )

    user["account_id"] = account.id
    user["role"] = role_names[database_role]
    request.session["user"] = user

    return account


@app.get("/")
def home():
    return FileResponse(PROJECT_ROOT / "index.html")


def _selected_property_id(request: Request) -> str | None:
    value = request.query_params.get("property_id", "")
    if value.isascii() and value.isdigit() and len(value) <= 10 and int(value) > 0:
        return str(int(value))
    return None


def _buyer_property_suffix(request: Request) -> str:
    property_id = _selected_property_id(request)
    return f"?property_id={property_id}" if property_id else ""


def _buyer_login_redirect(request: Request):
    property_id = _selected_property_id(request)
    suffix = f"&property_id={property_id}" if property_id else ""
    return RedirectResponse(
        f"/index.html?next=/buyer{suffix}#login",
        status_code=303,
    )


@app.get("/buyer")
def buyer_entry(request: Request, db: Session = Depends(get_db)):
    if not isinstance(request.session.get("user"), dict):
        return _buyer_login_redirect(request)
    account, _provider, _subject = authenticated_account(request, db)
    profile = find_profile(db, account.id)
    destination = "/buyer/dashboard" if profile else "/buyer/onboarding"
    return RedirectResponse(destination + _buyer_property_suffix(request), status_code=303)


@app.get("/buyer/onboarding")
def buyer_onboarding_page(request: Request, db: Session = Depends(get_db)):
    if not isinstance(request.session.get("user"), dict):
        return _buyer_login_redirect(request)
    authenticated_account(request, db)
    return FileResponse(PROJECT_ROOT / "buyer-onboarding.html")


@app.get("/buyer/dashboard")
def buyer_dashboard_page(request: Request, db: Session = Depends(get_db)):
    if not isinstance(request.session.get("user"), dict):
        return _buyer_login_redirect(request)
    account, _provider, _subject = authenticated_account(request, db)
    profile = find_profile(db, account.id)
    if not profile:
        return RedirectResponse(
            "/buyer/onboarding" + _buyer_property_suffix(request),
            status_code=303,
        )
    return FileResponse(PROJECT_ROOT / "buyer-dashboard.html")


@app.get("/inventory")
def inventory_page():
    return FileResponse(PROJECT_ROOT / "inventory.html")


@app.get("/property/{property_id}")
def property_detail_page(property_id: int):
    if property_id < 1:
        raise HTTPException(
            status_code=404,
            detail="Property not found.",
        )

    return FileResponse(PROJECT_ROOT / "property-detail.html")

@app.get("/dashboard")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    user = request.session.get("user")

    if not isinstance(user, dict):
        return RedirectResponse("/index.html#login", status_code=303)

    account = authenticated_staff_account(request, db)

    if str(account.role or "").strip().casefold() == "admin":
        return FileResponse(
            PROJECT_ROOT / "admin-dashboard.html",
            headers={"Cache-Control": "no-store"},
        )

    return FileResponse(PROJECT_ROOT / "dashboard.html")


@app.get("/staff-invitation")
def staff_invitation_page():
    return FileResponse(
        PROJECT_ROOT / "staff-invitation.html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@app.get("/index.html")
def index_page():
    return FileResponse(PROJECT_ROOT / "index.html")


@app.get("/otp-login.html")
def otp_login_page():
    return FileResponse(PROJECT_ROOT / "otp-login.html")


@app.get("/register.html")
def register_page():
    return FileResponse(PROJECT_ROOT / "register.html")


@app.get("/forgot-password.html")
def forgot_password_page():
    return FileResponse(PROJECT_ROOT / "forgot-password.html")


@app.get("/buyer-enquiry.html")
def buyer_enquiry_page():
    return FileResponse(PROJECT_ROOT / "buyer-enquiry.html")


app.mount("/images", StaticFiles(directory=PROJECT_ROOT / "images"), name="images")


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    return RedirectResponse("/favicon.svg", status_code=307)

@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    return FileResponse(PROJECT_ROOT / "favicon.svg", media_type="image/svg+xml")

@app.get("/styles.css")
def stylesheet():
    return FileResponse(PROJECT_ROOT / "styles.css", media_type="text/css")


@app.get("/api/auth/config")
def auth_config():
    return {"google_client_id": GOOGLE_CLIENT_ID}

@app.get("/api/session")
def get_session(request: Request):
    user = request.session.get("user")
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user": user}



@app.post("/api/register", status_code=201)
def register_buyer(
    payload: ManualBuyerRegistration,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a Buyer account and link a matching visitor enquiry without signing in."""
    if payload.role.strip().lower() != "buyer":
        raise HTTPException(
            status_code=403,
            detail="Manual public registration is currently available for Buyers only.",
        )

    full_name = " ".join(payload.fullName.split())
    email = payload.email.strip().lower()
    phone = normalize_phone(payload.phone)

    if len(full_name) < 2:
        raise HTTPException(status_code=422, detail="Enter your full name.")

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")

    if payload.password != payload.confirmPassword:
        raise HTTPException(status_code=422, detail="Passwords do not match.")

    if db.scalar(select(UserAccount.id).where(UserAccount.email == email)):
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    account = UserAccount(
        full_name=full_name,
        email=email,
        phone=phone,
        password_hash=hash_password(payload.password),
        role="buyer",
    )
    db.add(account)

    try:
        # Obtain the canonical account ID before linking the enquiry.
        db.flush()

        db.add(
            AuthIdentity(
                account_id=account.id,
                provider="local_password",
                provider_subject=str(account.id),
            )
        )

        # Link only the enquiry captured in this browser session.
        enquiry_id = request.session.get("buyer_enquiry_id")

        if isinstance(enquiry_id, int) and not isinstance(enquiry_id, bool):
            enquiry = db.scalar(
                select(BuyerEnquiry).where(
                    BuyerEnquiry.id == enquiry_id,
                    BuyerEnquiry.email == email,
                    BuyerEnquiry.account_id.is_(None),
                )
            )

            if enquiry is not None:
                enquiry.account_id = account.id

        # Account, identity and enquiry association commit atomically.
        db.commit()
        db.refresh(account)

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An account with this email or sign-in identity already exists.",
        )

    # Clear the pending enquiry reference after successful registration.
    # A stale or mismatched enquiry does not prevent account creation.
    request.session.pop("buyer_enquiry_id", None)

    redirect = "/index.html?next=%2Fbuyer&registered=1"

    if payload.property_id is not None:
        redirect += f"&property_id={payload.property_id}"

    redirect += "#login"

    return {
        "success": True,
        "message": "Account created. Please sign in to continue.",
        "redirect": redirect,
    }



@app.post("/api/auth/password/login")
def buyer_password_login(
    payload: ManualBuyerLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    """Authenticate a password account through its canonical identity."""
    email = payload.identifier.strip().lower()
    account = db.scalar(select(UserAccount).where(UserAccount.email == email))
    if (
        account is None
        or account.role != "buyer"
        or not account.password_hash
        or not verify_password(payload.password, account.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == "local_password",
            AuthIdentity.provider_subject == str(account.id),
        )
    )
    if identity is None:
        db.add(AuthIdentity(
            account_id=account.id,
            provider="local_password",
            provider_subject=str(account.id),
        ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            identity = db.scalar(
                select(AuthIdentity).where(
                    AuthIdentity.provider == "local_password",
                    AuthIdentity.provider_subject == str(account.id),
                )
            )
            if identity is None or identity.account_id != account.id:
                raise HTTPException(status_code=409, detail="Password identity is linked to another account.")

    user = {
        "account_id": account.id,
        "provider": "local_password",
        "providerId": str(account.id),
        "email": account.email,
        "phone": account.phone,
        "fullName": account.full_name,
        "role": "Buyer",
    }
    set_session(request, user)

    redirect = "/buyer"
    if payload.property_id is not None:
        redirect += f"?property_id={payload.property_id}"
    return {"success": True, "user": user, "redirect": redirect}

@app.post("/api/login")
def staff_password_login(
    payload: StaffLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    """Authenticate an existing, provisioned staff account."""

    email = payload.identifier.strip().lower()
    account = db.scalar(
        select(UserAccount).where(UserAccount.email == email)
    )

    if (
        account is None
        or str(account.role or "").strip().casefold()
        not in {"admin", "developer", "channel partner"}
        or not account.is_active
        or not account.password_hash
        or not verify_password(payload.password, account.password_hash)
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
        )

    role_names = {
        "admin": "Admin",
        "developer": "Developer",
        "channel partner": "Channel Partner",
    }
    database_role = str(account.role).strip().casefold()
    canonical_role = role_names[database_role]

    if payload.role != canonical_role:
        raise HTTPException(
            status_code=403,
            detail="Selected role does not match this account.",
        )

    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == "local_password",
            AuthIdentity.provider_subject == str(account.id),
        )
    )

    if identity is None or identity.account_id != account.id:
        raise HTTPException(
            status_code=401,
            detail="Staff account is not provisioned for password login.",
        )

    user = {
        "account_id": account.id,
        "provider": "local_password",
        "providerId": str(account.id),
        "email": account.email,
        "phone": account.phone,
        "fullName": account.full_name,
        "role": canonical_role,
    }
    set_session(request, user)

    return {
        "success": True,
        "user": user,
        "redirect": "/dashboard",
    }

@app.post("/api/auth/google")
def google_login(
    payload: GoogleLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google Sign-In is not configured.")
    try:
        claims = id_token.verify_oauth2_token(
            payload.credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Google token is invalid or expired.")

    if not claims.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google email is not verified.")
    email = str(claims.get("email") or "").strip().lower()
    subject = str(claims.get("sub") or "").strip()
    if not email or not subject:
        raise HTTPException(status_code=401, detail="Google account details are incomplete.")

    # A generic Google sign-in is not sufficient to create or grant a Buyer account.
    if payload.purpose != "buyer":
        user = {
            "provider": "google",
            "providerId": subject,
            "email": email,
            "fullName": claims.get("name") or email,
            "role": "Unassigned",
            "account_id": None,
        }
        set_session(request, user)
        return {"success": True, "user": user, "redirect": "/dashboard"}

    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == "google",
            AuthIdentity.provider_subject == subject,
        )
    )
    account = db.get(UserAccount, identity.account_id) if identity else None

    if identity is None:
        # Never merge accounts based only on a matching email address.
        email_account = db.scalar(select(UserAccount).where(UserAccount.email == email))
        if email_account is not None:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists. Sign in using that account before linking Google.",
            )

        account = UserAccount(
            full_name=claims.get("name") or None,
            email=email,
            phone=None,
            password_hash=None,
            role="buyer",
        )
        db.add(account)
        try:
            db.flush()
            db.add(AuthIdentity(
                account_id=account.id,
                provider="google",
                provider_subject=subject,
            ))
            db.commit()
            db.refresh(account)
        except IntegrityError:
            db.rollback()
            identity = db.scalar(
                select(AuthIdentity).where(
                    AuthIdentity.provider == "google",
                    AuthIdentity.provider_subject == subject,
                )
            )
            if identity is None:
                raise HTTPException(status_code=409, detail="An account with this email already exists.")
            account = db.get(UserAccount, identity.account_id)

    if account is None or account.role != "buyer":
        raise HTTPException(status_code=403, detail="This Google identity is not linked to a Buyer account.")

    user = {
        "account_id": account.id,
        "provider": "google",
        "providerId": subject,
        "email": email,
        "fullName": account.full_name or claims.get("name") or email,
        "role": "Buyer",
    }
    set_session(request, user)
    return {"success": True, "user": user, "redirect": "/buyer"}


@app.post("/api/auth/otp/request")
def request_otp(payload: OTPRequest):
    if APP_ENV != "development":
        raise HTTPException(
            status_code=403,
            detail="Terminal OTP is available only in local development.",
        )

    phone = normalize_phone(payload.phone)
    now = time.time()
    previous = otp_records.get(phone)

    if previous and now - previous["created_at"] < OTP_RESEND_SECONDS:
        raise HTTPException(status_code=429, detail="Wait before requesting another OTP.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    otp_records[phone] = {
        "digest": hashlib.sha256(code.encode()).hexdigest(),
        "created_at": now,
        "expires_at": now + OTP_TTL_SECONDS,
        "attempts": 0,
        "purpose": payload.purpose,
    }

    # Intentionally printed only in the local development terminal.
    print(f"\n[RealIQ LOCAL OTP] Phone: {phone} | Code: {code} | Expires in 5 minutes\n")
    return {"success": True, "message": "Demo OTP generated.", "demo_code": code}


@app.post("/api/auth/otp/verify")
def verify_otp(
    payload: OTPVerify,
    request: Request,
    db: Session = Depends(get_db),
):
    if APP_ENV != "development":
        raise HTTPException(status_code=403, detail="Terminal OTP is disabled.")

    phone = normalize_phone(payload.phone)
    record = otp_records.get(phone)
    if not record:
        raise HTTPException(status_code=400, detail="Request an OTP first.")
    if time.time() > record["expires_at"]:
        otp_records.pop(phone, None)
        raise HTTPException(status_code=400, detail="OTP expired. Request a new one.")
    if record["attempts"] >= OTP_MAX_ATTEMPTS:
        otp_records.pop(phone, None)
        raise HTTPException(status_code=429, detail="Too many attempts. Request a new OTP.")

    record["attempts"] += 1
    candidate = hashlib.sha256(payload.code.encode()).hexdigest()
    if not hmac.compare_digest(candidate, record["digest"]):
        if record["attempts"] >= OTP_MAX_ATTEMPTS:
            otp_records.pop(phone, None)
            raise HTTPException(status_code=429, detail="Too many attempts. Request a new OTP.")
        raise HTTPException(status_code=400, detail="Incorrect OTP.")

    purpose = record.get("purpose")
    otp_records.pop(phone, None)

    if purpose != "buyer":
        user = {
            "provider": "local_otp",
            "phone": phone,
            "fullName": phone,
            "role": "Unassigned",
            "account_id": None,
        }
        set_session(request, user)
        return {"success": True, "user": user, "redirect": "/dashboard"}

    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == "local_otp",
            AuthIdentity.provider_subject == phone,
        )
    )
    account = db.get(UserAccount, identity.account_id) if identity else None

    if identity is None:
        # A phone match alone does not prove ownership of an existing password account.
        phone_account = db.scalar(select(UserAccount).where(UserAccount.phone == phone))
        if phone_account is not None:
            raise HTTPException(
                status_code=409,
                detail="This phone is already on an account. Sign in to that account before linking OTP sign-in.",
            )

        account = UserAccount(
            full_name=None,
            email=None,
            phone=phone,
            password_hash=None,
            role="buyer",
        )
        db.add(account)
        try:
            db.flush()
            db.add(AuthIdentity(
                account_id=account.id,
                provider="local_otp",
                provider_subject=phone,
            ))
            db.commit()
            db.refresh(account)
        except IntegrityError:
            db.rollback()
            identity = db.scalar(
                select(AuthIdentity).where(
                    AuthIdentity.provider == "local_otp",
                    AuthIdentity.provider_subject == phone,
                )
            )
            if identity is None:
                raise HTTPException(status_code=409, detail="Could not create the Buyer identity. Please retry sign-in.")
            account = db.get(UserAccount, identity.account_id)

    if account is None or account.role != "buyer":
        raise HTTPException(status_code=403, detail="This phone identity is not linked to a Buyer account.")

    user = {
        "account_id": account.id,
        "provider": "local_otp",
        "phone": phone,
        "fullName": account.full_name or phone,
        "email": account.email,
        "role": "Buyer",
    }
    set_session(request, user)
    return {"success": True, "user": user, "redirect": "/buyer"}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"success": True}
