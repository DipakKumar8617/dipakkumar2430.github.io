from datetime import datetime
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import BuyerEnquiry, BuyerProfile, PropertyListing

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


class BuyerEnquiryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=2, max_length=150)
    email: str = Field(min_length=5, max_length=254)
    phone: str = Field(min_length=7, max_length=20)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=100)


class PropertyListingData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    property_type: str
    city: str
    state: str
    price_inr: int | None
    bedrooms: int | None
    area_sqft: int | None
    description: str | None
    image_url: str | None
    availability_status: str


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

    role = user.get("role")
    if not isinstance(role, str) or role.strip().casefold() != "buyer":
        raise HTTPException(
            status_code=403,
            detail="Buyer access is required.",
        )

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
        raise HTTPException(status_code=401, detail="Valid account identity required.")

    return provider, subject


def find_profile(db: Session, provider: str, subject: str):
    return db.scalar(
        select(BuyerProfile).where(
            BuyerProfile.auth_provider == provider,
            BuyerProfile.auth_subject == subject,
        )
    )


@router.get("/inventory", response_model=list[PropertyListingData])
def get_public_inventory(db: Session = Depends(get_db)):
    """Return only listings explicitly published and marked available."""
    return list(
        db.scalars(
            select(PropertyListing)
            .where(
                PropertyListing.is_published.is_(True),
                PropertyListing.availability_status == "available",
            )
            .order_by(PropertyListing.created_at.desc(), PropertyListing.id.desc())
        ).all()
    )


@router.post("/enquiries", status_code=201)
def create_buyer_enquiry(
    payload: BuyerEnquiryInput,
    request: Request,
    db: Session = Depends(get_db),
):
    """Save a visitor enquiry before requiring account registration."""
    full_name = " ".join(payload.full_name.split())
    email = payload.email.strip().lower()
    phone = payload.phone.strip()
    city = " ".join(payload.city.split())
    state = " ".join(payload.state.split())

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")

    if not 7 <= len(re.sub(r"\D", "", phone)) <= 15:
        raise HTTPException(status_code=422, detail="Enter a valid phone number.")

    if not all((full_name, city, state)):
        raise HTTPException(
            status_code=422,
            detail="Name, city, and state cannot be blank.",
        )

    enquiry = BuyerEnquiry(
        full_name=full_name,
        email=email,
        phone=phone,
        city=city,
        state=state,
    )
    db.add(enquiry)
    db.commit()
    db.refresh(enquiry)

    # Keep this enquiry attached to the browser session until the next stage
    # links it to a registered identity.
    request.session["buyer_enquiry_id"] = enquiry.id

    return {
        "success": True,
        "enquiry_id": enquiry.id,
        "redirect": "/inventory",
    }


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
