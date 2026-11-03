from datetime import datetime
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from .database import get_db
from .models import AuthIdentity, BuyerEnquiry, BuyerProfile, PropertyListing, UserAccount

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

class InventorySearchResponse(BaseModel):
    items: list[PropertyListingData]
    page: int
    page_size: int
    total: int
    total_pages: int


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


def authenticated_account(
    request: Request, db: Session
) -> tuple[UserAccount, str, str]:
    """Resolve the signed-in buyer through the canonical account identity."""
    user = request.session.get("user")
    if not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="Login required.")

    if str(user.get("role", "")).strip().casefold() == "unassigned":
        raise HTTPException(status_code=403, detail="Buyer access is required.")

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

    identity = db.scalar(
        select(AuthIdentity).where(
            AuthIdentity.provider == provider,
            AuthIdentity.provider_subject == subject,
        )
    )
    if identity is None:
        raise HTTPException(status_code=401, detail="Account identity is not linked. Please sign in again.")

    session_account_id = user.get("account_id")
    if session_account_id is not None and str(session_account_id) != str(identity.account_id):
        raise HTTPException(status_code=401, detail="Session identity does not match the account.")

    account = db.get(UserAccount, identity.account_id)
    if account is None:
        raise HTTPException(status_code=401, detail="Account not found. Please sign in again.")
    if str(account.role).strip().casefold() != "buyer":
        raise HTTPException(status_code=403, detail="Buyer access is required.")

    # Refresh the account id and role from the database, not from browser input.
    user["account_id"] = account.id
    user["role"] = "Buyer"
    request.session["user"] = user
    return account, provider, subject


def find_profile(db: Session, account_id: int):
    return db.scalar(
        select(BuyerProfile).where(BuyerProfile.account_id == account_id)
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



@router.get("/inventory/search", response_model=InventorySearchResponse)
def search_public_inventory(
    q: str | None = Query(default=None, max_length=120),
    city: str | None = Query(default=None, max_length=100),
    state: str | None = Query(default=None, max_length=100),
    property_type: Literal[
        "apartment", "villa", "plot", "commercial", "other"
    ] | None = None,
    min_price: int | None = Query(default=None, ge=0),
    max_price: int | None = Query(default=None, ge=0),
    min_bedrooms: int | None = Query(default=None, ge=0),
    max_bedrooms: int | None = Query(default=None, ge=0),
    min_area: int | None = Query(default=None, ge=1),
    max_area: int | None = Query(default=None, ge=1),
    sort: Literal[
        "newest", "price_asc", "price_desc", "area_asc", "area_desc"
    ] = "newest",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=48),
    db: Session = Depends(get_db),
):
    """Search publicly available properties with filters and pagination."""

    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status_code=422, detail="min_price cannot exceed max_price")

    if (
        min_bedrooms is not None
        and max_bedrooms is not None
        and min_bedrooms > max_bedrooms
    ):
        raise HTTPException(
            status_code=422,
            detail="min_bedrooms cannot exceed max_bedrooms",
        )

    if min_area is not None and max_area is not None and min_area > max_area:
        raise HTTPException(status_code=422, detail="min_area cannot exceed max_area")

    filters = [
        PropertyListing.is_published.is_(True),
        PropertyListing.availability_status == "available",
    ]

    if q and q.strip():
        keyword = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{keyword}%"
        filters.append(
            or_(
                PropertyListing.title.ilike(pattern, escape="\\"),
                PropertyListing.description.ilike(pattern, escape="\\"),
                PropertyListing.property_type.ilike(pattern, escape="\\"),
                PropertyListing.city.ilike(pattern, escape="\\"),
                PropertyListing.state.ilike(pattern, escape="\\"),
            )
        )

    if city and city.strip():
        filters.append(func.lower(PropertyListing.city) == city.strip().lower())

    if state and state.strip():
        filters.append(func.lower(PropertyListing.state) == state.strip().lower())

    if property_type is not None:
        filters.append(PropertyListing.property_type == property_type)

    if min_price is not None:
        filters.append(PropertyListing.price_inr >= min_price)

    if max_price is not None:
        filters.append(PropertyListing.price_inr <= max_price)

    if min_bedrooms is not None:
        filters.append(PropertyListing.bedrooms >= min_bedrooms)

    if max_bedrooms is not None:
        filters.append(PropertyListing.bedrooms <= max_bedrooms)

    if min_area is not None:
        filters.append(PropertyListing.area_sqft >= min_area)

    if max_area is not None:
        filters.append(PropertyListing.area_sqft <= max_area)

    total = db.scalar(
        select(func.count())
        .select_from(PropertyListing)
        .where(*filters)
    ) or 0

    statement = select(PropertyListing).where(*filters)

    if sort == "price_asc":
        statement = statement.order_by(
            case((PropertyListing.price_inr.is_(None), 1), else_=0),
            PropertyListing.price_inr.asc(),
            PropertyListing.id.desc(),
        )
    elif sort == "price_desc":
        statement = statement.order_by(
            case((PropertyListing.price_inr.is_(None), 1), else_=0),
            PropertyListing.price_inr.desc(),
            PropertyListing.id.desc(),
        )
    elif sort == "area_asc":
        statement = statement.order_by(
            case((PropertyListing.area_sqft.is_(None), 1), else_=0),
            PropertyListing.area_sqft.asc(),
            PropertyListing.id.desc(),
        )
    elif sort == "area_desc":
        statement = statement.order_by(
            case((PropertyListing.area_sqft.is_(None), 1), else_=0),
            PropertyListing.area_sqft.desc(),
            PropertyListing.id.desc(),
        )
    else:
        statement = statement.order_by(
            PropertyListing.created_at.desc(),
            PropertyListing.id.desc(),
        )

    items = db.scalars(
        statement.offset((page - 1) * page_size).limit(page_size)
    ).all()

    return InventorySearchResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=(total + page_size - 1) // page_size,
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
    account, _provider, _subject = authenticated_account(request, db)
    profile = find_profile(db, account.id)
    return {
        "profile": BuyerProfileData.model_validate(profile) if profile else None
    }


@router.put("/profile", response_model=BuyerProfileData)
def save_buyer_profile(
    payload: BuyerProfileInput,
    request: Request,
    db: Session = Depends(get_db),
):
    account, provider, subject = authenticated_account(request, db)
    values = payload.model_dump(exclude_unset=True)
    profile = find_profile(db, account.id)

    if profile is None:
        profile = BuyerProfile(
            account_id=account.id,
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
