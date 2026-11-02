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
from .buyer import authenticated_identity, find_profile, router as buyer_router
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .database import get_db
from .models import UserAccount

app = FastAPI(title="RealIQ Authentication API")
app.include_router(buyer_router)

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


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = encoded.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def normalize_phone(value: str) -> str:
    phone = "".join(char for char in value if char.isdigit())
    if not 7 <= len(phone) <= 15:
        raise HTTPException(status_code=400, detail="Enter a valid phone number.")
    return phone


def set_session(request: Request, user: dict) -> None:
    request.session.clear()
    request.session["user"] = user
    request.session["authenticated_at"] = int(time.time())


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
    provider, subject = authenticated_identity(request)
    profile = find_profile(db, provider, subject)
    destination = "/buyer/dashboard" if profile else "/buyer/onboarding"
    return RedirectResponse(destination + _buyer_property_suffix(request), status_code=303)


@app.get("/buyer/onboarding")
def buyer_onboarding_page(request: Request):
    if not isinstance(request.session.get("user"), dict):
        return _buyer_login_redirect(request)
    authenticated_identity(request)
    return FileResponse(PROJECT_ROOT / "buyer-onboarding.html")


@app.get("/buyer/dashboard")
def buyer_dashboard_page(request: Request, db: Session = Depends(get_db)):
    if not isinstance(request.session.get("user"), dict):
        return _buyer_login_redirect(request)
    provider, subject = authenticated_identity(request)
    profile = find_profile(db, provider, subject)
    if not profile:
        return RedirectResponse(
            "/buyer/onboarding" + _buyer_property_suffix(request),
            status_code=303,
        )
    return FileResponse(PROJECT_ROOT / "buyer-dashboard.html")


@app.get("/inventory")
def inventory_page():
    return FileResponse(PROJECT_ROOT / "inventory.html")


@app.get("/dashboard")
def dashboard(request: Request):
    user = request.session.get("user")
    if not isinstance(user, dict):
        return RedirectResponse("/index.html#login", status_code=303)

    role = user.get("role")
    allowed_staff_roles = {"developer", "channel partner", "admin"}
    if not isinstance(role, str) or role.strip().casefold() not in allowed_staff_roles:
        raise HTTPException(
            status_code=403,
            detail="An approved staff role is required.",
        )

    return FileResponse(PROJECT_ROOT / "dashboard.html")


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
    db: Session = Depends(get_db),
):
    """Create a Buyer account without signing the user in."""
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
        db.commit()
        db.refresh(account)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

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
    """Authenticate an existing manually registered Buyer."""
    email = payload.identifier.strip().lower()
    account = db.scalar(
        select(UserAccount).where(UserAccount.email == email)
    )

    if (
        account is None
        or account.role != "buyer"
        or not verify_password(payload.password, account.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    user = {
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


@app.post("/api/auth/google")
def google_login(payload: GoogleLogin, request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google Sign-In is not configured.")

    try:
        claims = id_token.verify_oauth2_token(
            payload.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Google token is invalid or expired.")

    if not claims.get("email_verified"):
        raise HTTPException(status_code=401, detail="Google email is not verified.")

    email = claims.get("email")
    subject = claims.get("sub")
    if not email or not subject:
        raise HTTPException(status_code=401, detail="Google account details are incomplete.")

    # Google verifies identity. Buyer access is granted only when the
    # request explicitly originates from the Buyer authentication journey.
    role = "Buyer" if payload.purpose == "buyer" else "Unassigned"
    redirect = "/buyer" if role == "Buyer" else "/dashboard"

    user = {
        "provider": "google",
        "providerId": subject,
        "email": email,
        "fullName": claims.get("name") or email,
        "role": role,
    }
    set_session(request, user)
    return {"success": True, "user": user, "redirect": redirect}


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
def verify_otp(payload: OTPVerify, request: Request):
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
    user = {
        "provider": "local_otp",
        "phone": phone,
        "fullName": phone,
        "role": "Buyer" if purpose == "buyer" else "Unassigned",
    }
    set_session(request, user)
    return {"success": True, "user": user, "redirect": "/dashboard"}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"success": True}
