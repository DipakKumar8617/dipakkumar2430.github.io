from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import BuyerProfile

router = APIRouter(prefix="/api/buyer", tags=["buyer"])


class BuyerProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buyer_type: Literal["indian_resident", "nri_overseas"]
    full_name: str | None = Field(default=None, max_length=160)
    country_of_residence: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=80)
    preferred_city: str | None = Field(default=None, max_length=100)
    purchase_purpose: Literal[
        "self_use", "investment", "commercial", "land"
    ] | None = None


class BuyerProfileData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    buyer_type: str
    full_name: str | None
    country_of_residence: str | None
    timezone: str | None
    preferred_city: str | None
    purchase_purpose: str | None
    created_at: datetime
    updated_at: datetime


def authenticated_identity(request: Request) -> tuple[str, str]:
    user = request.session.get("user")
    if not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="Login required.")

    provider = user.get("provider")
    if provider == "google":
        subject = user.get("providerId")
    elif provider == "local_otp":
        subject = user.get("phone")
    else:
        subject = None

    if not isinstance(subject, str) or not subject.strip():
        raise HTTPException(status_code=401, detail="Valid account identity required.")

    return provider, subject


def find_profile(db: Session, provider: str, subject: str):
    return db.scalar(
        select(BuyerProfile).where(
            BuyerProfile.auth_provider == provider,
            BuyerProfile.auth_subject == subject,
        )
    )


@router.get("/profile")
def get_buyer_profile(
    request: Request,
    db: Session = Depends(get_db),
):
    provider, subject = authenticated_identity(request)
    profile = find_profile(db, provider, subject)
    return {
        "profile": BuyerProfileData.model_validate(profile) if profile else None
    }


@router.put("/profile", response_model=BuyerProfileData)
def save_buyer_profile(
    payload: BuyerProfileInput,
    request: Request,
    db: Session = Depends(get_db),
):
    provider, subject = authenticated_identity(request)
    values = payload.model_dump(exclude_unset=True)
    profile = find_profile(db, provider, subject)

    if profile is None:
        profile = BuyerProfile(
            auth_provider=provider,
            auth_subject=subject,
            buyer_type=payload.buyer_type,
        )
        db.add(profile)

    for field in (
        "buyer_type",
        "full_name",
        "country_of_residence",
        "timezone",
        "preferred_city",
        "purchase_purpose",
    ):
        if field in values:
            setattr(profile, field, values[field])

    db.commit()
    db.refresh(profile)
    return profile
