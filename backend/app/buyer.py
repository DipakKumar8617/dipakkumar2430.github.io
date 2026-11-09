from datetime import datetime
import re
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .database import get_db
from .models import (
    AuthIdentity,
    BuyerEnquiry,
    BuyerProfile,
    PropertyListing,
    PropertyShortlist,
    UserAccount,
    RecentlyViewedProperty,
    SavedPropertySearch,
)

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


class PropertyShortlistData(BaseModel):
    shortlist_id: int
    created_at: datetime
    property: PropertyListingData


class PropertyShortlistSaveResponse(BaseModel):
    saved: bool
    already_saved: bool
    property_id: int


class PropertyShortlistDeleteResponse(BaseModel):
    removed: bool
    property_id: int


class PropertyComparisonResponse(BaseModel):
    items: list[PropertyListingData]
    count: int


class PropertySearchCriteria(BaseModel):
    """Validated filters supported by the property discovery API."""
    model_config = ConfigDict(extra="forbid")
    q: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    property_type: Literal[
        "apartment", "villa", "plot", "commercial", "other"
    ] | None = None
    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)
    min_bedrooms: int | None = Field(default=None, ge=0)
    max_bedrooms: int | None = Field(default=None, ge=0)
    min_area: int | None = Field(default=None, ge=1)
    max_area: int | None = Field(default=None, ge=1)
    sort: Literal[
        "newest", "price_asc", "price_desc", "area_asc", "area_desc"
    ] = "newest"
    @field_validator("q", "city", "state")
    @classmethod
    def normalize_search_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
    @model_validator(mode="after")
    def validate_ranges(self):
        ranges = (
            ("min_price", "max_price"),
            ("min_bedrooms", "max_bedrooms"),
            ("min_area", "max_area"),
        )
        for minimum, maximum in ranges:
            min_value = getattr(self, minimum)
            max_value = getattr(self, maximum)
            if (
                min_value is not None
                and max_value is not None
                and min_value > max_value
            ):
                raise ValueError(
                    f"{minimum} cannot exceed {maximum}"
                )
        return self


class SavedPropertySearchInput(BaseModel):
    """Request to save a named property search."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    criteria: PropertySearchCriteria
    @field_validator("name")
    @classmethod
    def normalize_search_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Search name cannot be empty.")
        return normalized


class SavedPropertySearchData(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    criteria: PropertySearchCriteria
    created_at: datetime
    updated_at: datetime


class SavedPropertySearchDeleteResponse(BaseModel):
    removed: bool
    search_id: int


class RecentlyViewedPropertyData(BaseModel):
    viewed_at: datetime
    property: PropertyListingData


class RecentlyViewedRecordResponse(BaseModel):
    tracked: bool
    already_viewed: bool
    property_id: int


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


@router.get(
    "/inventory/{listing_id}",
    response_model=PropertyListingData,
)
def get_public_property_detail(
    listing_id: int,
    db: Session = Depends(get_db),
):
    """Return a property only when it is published and available."""
    if listing_id < 1:
        raise HTTPException(
            status_code=404,
            detail="Property not found.",
        )
    listing = db.scalar(
        select(PropertyListing).where(
            PropertyListing.id == listing_id,
            PropertyListing.is_published.is_(True),
            PropertyListing.availability_status == "available",
        )
    )
    if listing is None:
        raise HTTPException(
            status_code=404,
            detail="Property not found.",
        )
    return listing
# --------------------------------------------------
# BUYER PROPERTY SHORTLIST
# --------------------------------------------------


@router.post(
    "/shortlist/{property_id}",
    response_model=PropertyShortlistSaveResponse,
    status_code=201,
)
def save_shortlisted_property(
    property_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Save an available published property for the authenticated buyer."""
    account, _provider, _subject = authenticated_account(request, db)
    listing = db.scalar(
        select(PropertyListing).where(
            PropertyListing.id == property_id,
            PropertyListing.is_published.is_(True),
        )
    )
    if listing is None:
        raise HTTPException(
            status_code=404,
            detail="Property not found.",
        )
    existing = db.scalar(
        select(PropertyShortlist).where(
            PropertyShortlist.account_id == account.id,
            PropertyShortlist.property_listing_id == property_id,
        )
    )
    if existing is not None:
        response.status_code = 200
        return PropertyShortlistSaveResponse(
            saved=True,
            already_saved=True,
            property_id=property_id,
        )
    if listing.availability_status != "available":
        raise HTTPException(
            status_code=404,
            detail="Property is unavailable.",
        )
    shortlist = PropertyShortlist(
        account_id=account.id,
        property_listing_id=property_id,
    )
    db.add(shortlist)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(PropertyShortlist).where(
                PropertyShortlist.account_id == account.id,
                PropertyShortlist.property_listing_id == property_id,
            )
        )
        if existing is not None:
            response.status_code = 200
            return PropertyShortlistSaveResponse(
                saved=True,
                already_saved=True,
                property_id=property_id,
            )
        raise HTTPException(
            status_code=409,
            detail="Property could not be saved. Please try again.",
        )
    return PropertyShortlistSaveResponse(
        saved=True,
        already_saved=False,
        property_id=property_id,
    )


@router.get(
    "/shortlist",
    response_model=list[PropertyShortlistData],
)
def get_buyer_shortlist(
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the authenticated buyer's published saved properties."""
    account, _provider, _subject = authenticated_account(request, db)
    rows = db.execute(
        select(PropertyShortlist, PropertyListing)
        .join(
            PropertyListing,
            PropertyListing.id == PropertyShortlist.property_listing_id,
        )
        .where(
            PropertyShortlist.account_id == account.id,
            PropertyListing.is_published.is_(True),
        )
        .order_by(
            PropertyShortlist.created_at.desc(),
            PropertyShortlist.id.desc(),
        )
    ).all()
    return [
        PropertyShortlistData(
            shortlist_id=shortlist.id,
            created_at=shortlist.created_at,
            property=listing,
        )
        for shortlist, listing in rows
    ]


@router.delete(
    "/shortlist/{property_id}",
    response_model=PropertyShortlistDeleteResponse,
)
def remove_shortlisted_property(
    property_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Remove a property only from the authenticated buyer's shortlist."""
    account, _provider, _subject = authenticated_account(request, db)
    shortlist = db.scalar(
        select(PropertyShortlist).where(
            PropertyShortlist.account_id == account.id,
            PropertyShortlist.property_listing_id == property_id,
        )
    )
    if shortlist is None:
        return PropertyShortlistDeleteResponse(
            removed=False,
            property_id=property_id,
        )
    db.delete(shortlist)
    db.commit()
    return PropertyShortlistDeleteResponse(
        removed=True,
        property_id=property_id,
    )
# --------------------------------------------------
# BUYER PROPERTY COMPARISON
# --------------------------------------------------


@router.get(
    "/shortlist/compare",
    response_model=PropertyComparisonResponse,
)
def compare_shortlisted_properties(
    request: Request,
    property_ids: list[int] = Query(...),
    db: Session = Depends(get_db),
):
    """Compare two to four properties saved by the authenticated buyer."""
    if not 2 <= len(property_ids) <= 4:
        raise HTTPException(
            status_code=422,
            detail="Select between two and four properties to compare.",
        )
    if any(property_id <= 0 for property_id in property_ids):
        raise HTTPException(
            status_code=422,
            detail="Property IDs must be positive integers.",
        )
    if len(set(property_ids)) != len(property_ids):
        raise HTTPException(
            status_code=422,
            detail="Duplicate property IDs are not allowed.",
        )
    account, _provider, _subject = authenticated_account(request, db)
    listings = db.scalars(
        select(PropertyListing)
        .join(
            PropertyShortlist,
            PropertyShortlist.property_listing_id == PropertyListing.id,
        )
        .where(
            PropertyShortlist.account_id == account.id,
            PropertyListing.id.in_(property_ids),
            PropertyListing.is_published.is_(True),
        )
    ).all()
    listings_by_id = {
        listing.id: listing
        for listing in listings
    }
    if len(listings_by_id) != len(property_ids):
        raise HTTPException(
            status_code=404,
            detail="One or more properties are not in your shortlist.",
        )
    # Preserve the order selected by the buyer.
    ordered_listings = [
        listings_by_id[property_id]
        for property_id in property_ids
    ]
    return PropertyComparisonResponse(
        items=ordered_listings,
        count=len(ordered_listings),
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
# --------------------------------------------------
# BUYER SAVED SEARCHES
# --------------------------------------------------


@router.post(
    "/saved-searches",
    response_model=SavedPropertySearchData,
    response_model_exclude_none=True,
    status_code=201,
)
def create_saved_property_search(
    payload: SavedPropertySearchInput,
    request: Request,
    db: Session = Depends(get_db),
):
    """Save validated property discovery criteria for the buyer."""
    account, _provider, _subject = authenticated_account(request, db)
    criteria = payload.criteria.model_dump(exclude_none=True)
    existing = db.scalar(
        select(SavedPropertySearch).where(
            SavedPropertySearch.account_id == account.id,
            SavedPropertySearch.name == payload.name,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="A saved search with this name already exists.",
        )
    saved_search = SavedPropertySearch(
        account_id=account.id,
        name=payload.name,
        criteria=criteria,
    )
    db.add(saved_search)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A saved search with this name already exists.",
        )
    db.refresh(saved_search)
    return saved_search


@router.get(
    "/saved-searches",
    response_model=list[SavedPropertySearchData],
    response_model_exclude_none=True,
)
def get_saved_property_searches(
    request: Request,
    db: Session = Depends(get_db),
):
    """List saved searches belonging to the authenticated buyer."""
    account, _provider, _subject = authenticated_account(request, db)
    return db.scalars(
        select(SavedPropertySearch)
        .where(SavedPropertySearch.account_id == account.id)
        .order_by(
            SavedPropertySearch.updated_at.desc(),
            SavedPropertySearch.id.desc(),
        )
    ).all()


@router.delete(
    "/saved-searches/{search_id}",
    response_model=SavedPropertySearchDeleteResponse,
)
def delete_saved_property_search(
    search_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a saved search only when owned by the authenticated buyer."""
    account, _provider, _subject = authenticated_account(request, db)
    saved_search = db.scalar(
        select(SavedPropertySearch).where(
            SavedPropertySearch.id == search_id,
            SavedPropertySearch.account_id == account.id,
        )
    )
    if saved_search is None:
        return SavedPropertySearchDeleteResponse(
            removed=False,
            search_id=search_id,
        )
    db.delete(saved_search)
    db.commit()
    return SavedPropertySearchDeleteResponse(
        removed=True,
        search_id=search_id,
    )
# --------------------------------------------------
# BUYER RECENTLY VIEWED PROPERTIES
# --------------------------------------------------


@router.post(
    "/recently-viewed/{property_id}",
    response_model=RecentlyViewedRecordResponse,
    status_code=201,
)
def record_recently_viewed_property(
    property_id: int,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Record a successful view of an available published property."""
    account, _provider, _subject = authenticated_account(request, db)
    listing = db.scalar(
        select(PropertyListing).where(
            PropertyListing.id == property_id,
            PropertyListing.is_published.is_(True),
            PropertyListing.availability_status == "available",
        )
    )
    if listing is None:
        raise HTTPException(
            status_code=404,
            detail="Property not found or unavailable.",
        )
    existing = db.scalar(
        select(RecentlyViewedProperty).where(
            RecentlyViewedProperty.account_id == account.id,
            RecentlyViewedProperty.property_listing_id == property_id,
        )
    )
    if existing is not None:
        existing.viewed_at = func.now()
        db.commit()
        response.status_code = 200
        return RecentlyViewedRecordResponse(
            tracked=True,
            already_viewed=True,
            property_id=property_id,
        )
    record = RecentlyViewedProperty(
        account_id=account.id,
        property_listing_id=property_id,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        # Another request may have recorded the same view concurrently.
        db.rollback()
        existing = db.scalar(
            select(RecentlyViewedProperty).where(
                RecentlyViewedProperty.account_id == account.id,
                RecentlyViewedProperty.property_listing_id == property_id,
            )
        )
        if existing is None:
            raise HTTPException(
                status_code=409,
                detail="Property view could not be recorded.",
            )
        existing.viewed_at = func.now()
        db.commit()
        response.status_code = 200
        return RecentlyViewedRecordResponse(
            tracked=True,
            already_viewed=True,
            property_id=property_id,
        )
    return RecentlyViewedRecordResponse(
        tracked=True,
        already_viewed=False,
        property_id=property_id,
    )


@router.get(
    "/recently-viewed",
    response_model=list[RecentlyViewedPropertyData],
)
def get_recently_viewed_properties(
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Return the buyer's latest views of currently published properties."""
    account, _provider, _subject = authenticated_account(request, db)
    rows = db.execute(
        select(RecentlyViewedProperty, PropertyListing)
        .join(
            PropertyListing,
            PropertyListing.id
            == RecentlyViewedProperty.property_listing_id,
        )
        .where(
            RecentlyViewedProperty.account_id == account.id,
            PropertyListing.is_published.is_(True),
        )
        .order_by(
            RecentlyViewedProperty.viewed_at.desc(),
            RecentlyViewedProperty.id.desc(),
        )
        .limit(limit)
    ).all()
    return [
        RecentlyViewedPropertyData(
            viewed_at=record.viewed_at,
            property=listing,
        )
        for record, listing in rows
    ]


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
