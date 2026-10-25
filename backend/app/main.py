import hashlib
import hmac
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .config import APP_ENV, GOOGLE_CLIENT_ID, PROJECT_ROOT
from .config import os

app = FastAPI(title="RealIQ Authentication API")

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


class OTPRequest(BaseModel):
    phone: str = Field(min_length=7, max_length=20)


class OTPVerify(BaseModel):
    phone: str = Field(min_length=7, max_length=20)
    code: str = Field(pattern=r"^\d{6}$")


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


@app.get("/dashboard")
def dashboard(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/index.html#login", status_code=303)
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

    # Authentication only: this does not grant a RealIQ business role.
    user = {
        "provider": "google",
        "providerId": subject,
        "email": email,
        "fullName": claims.get("name") or email,
        "role": "Unassigned",
    }
    set_session(request, user)
    return {"success": True, "user": user, "redirect": "/dashboard"}


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

    otp_records.pop(phone, None)
    user = {
        "provider": "local_otp",
        "phone": phone,
        "fullName": phone,
        "role": "Unassigned",
    }
    set_session(request, user)
    return {"success": True, "user": user, "redirect": "/dashboard"}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"success": True}
