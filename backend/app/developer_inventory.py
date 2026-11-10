from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth_dependencies import require_developer
from .database import get_db
from .models import (
    DeveloperOrganization,
    DeveloperOrganizationAuditLog,
    DeveloperOrganizationMembership,
    PropertyListing,
    UserAccount,
)


router = APIRouter(
    prefix="/api/developer-inventory",
    tags=["developer inventory"],
)

PropertyType = Literal["apartment", "villa", "plot", "commercial", "other"]
AvailabilityStatus = Literal["available", "reserved", "sold"]

WRITE_ROLES = {"owner", "admin", "inventory_manager"}


class ListingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=180)
    property_type: PropertyType
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    price_inr: int | None = Field(default=None, ge=0)
    bedrooms: int | None = Field(default=None, ge=0)
    area_sqft: int | None = Field(default=None, ge=1)
    description: str | None = Field(default=None, max_length=10000)
    image_url: str | None = Field(default=None, max_length=500)

    @field_validator("title", "city", "state")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("description", "image_url")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ListingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=180)
    property_type: PropertyType | None = None
    city: str | None = Field(default=None, min_length=1, max_length=100)
    state: str | None = Field(default=None, min_length=1, max_length=100)
    price_inr: int | None = Field(default=None, ge=0)
    bedrooms: int | None = Field(default=None, ge=0)
    area_sqft: int | None = Field(default=None, ge=1)
    description: str | None = Field(default=None, max_length=10000)
    image_url: str | None = Field(default=None, max_length=500)

    @field_validator("title", "city", "state")
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("description", "image_url")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ListingAvailabilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    availability_status: AvailabilityStatus


class DeveloperListingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    developer_organization_id: int
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
    is_published: bool
    created_at: datetime


def _require_inventory_access(
    db: Session,
    organization_id: int,
    account_id: int,
):
    organization = db.get(DeveloperOrganization, organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    membership = db.scalar(
        select(DeveloperOrganizationMembership).where(
            DeveloperOrganizationMembership.organization_id == organization_id,
            DeveloperOrganizationMembership.account_id == account_id,
            DeveloperOrganizationMembership.status == "active",
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    return organization, membership


def _require_inventory_write(
    organization: DeveloperOrganization,
    membership: DeveloperOrganizationMembership,
    *,
    allow_restricted: bool = False,
) -> None:
    if membership.role not in WRITE_ROLES:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to manage organization inventory.",
        )

    if (
        not allow_restricted
        and organization.verification_status in {"rejected", "suspended"}
    ):
        raise HTTPException(
            status_code=403,
            detail="This organization cannot modify inventory in its current status.",
        )


def _get_organization_listing(
    db: Session,
    organization_id: int,
    listing_id: int,
) -> PropertyListing:
    listing = db.scalar(
        select(PropertyListing).where(
            PropertyListing.id == listing_id,
            PropertyListing.developer_organization_id == organization_id,
        )
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="Listing not found.")
    return listing


def _record_inventory_audit(
    db: Session,
    organization_id: int,
    actor_account_id: int,
    action: str,
    details: dict[str, object],
) -> None:
    db.add(
        DeveloperOrganizationAuditLog(
            organization_id=organization_id,
            actor_account_id=actor_account_id,
            action=action,
            details=details,
        )
    )


def _commit_inventory_change(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The inventory change conflicts with existing data.",
        )
    except Exception:
        db.rollback()
        raise


@router.get(
    "/organizations/{organization_id}/listings",
    response_model=list[DeveloperListingResponse],
)
def list_organization_listings(
    organization_id: int,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    _organization, _membership = _require_inventory_access(
        db, organization_id, account.id
    )

    return db.scalars(
        select(PropertyListing)
        .where(PropertyListing.developer_organization_id == organization_id)
        .order_by(PropertyListing.created_at.desc(), PropertyListing.id.desc())
    ).all()


@router.post(
    "/organizations/{organization_id}/listings",
    response_model=DeveloperListingResponse,
    status_code=201,
)
def create_organization_listing(
    organization_id: int,
    payload: ListingCreate,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    organization, membership = _require_inventory_access(
        db, organization_id, account.id
    )
    _require_inventory_write(organization, membership)

    listing = PropertyListing(
        developer_organization_id=organization_id,
        **payload.model_dump(),
        availability_status="available",
        is_published=False,
    )
    db.add(listing)

    try:
        db.flush()
        _record_inventory_audit(
            db,
            organization_id,
            account.id,
            "inventory.listing_created",
            {
                "listing_id": listing.id,
                "initial_status": "draft",
            },
        )
        db.commit()
        db.refresh(listing)
        return listing
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The listing conflicts with existing data.",
        )
    except Exception:
        db.rollback()
        raise


@router.get(
    "/organizations/{organization_id}/listings/{listing_id}",
    response_model=DeveloperListingResponse,
)
def get_organization_listing(
    organization_id: int,
    listing_id: int,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    _require_inventory_access(db, organization_id, account.id)
    return _get_organization_listing(db, organization_id, listing_id)


@router.patch(
    "/organizations/{organization_id}/listings/{listing_id}",
    response_model=DeveloperListingResponse,
)
def update_organization_listing(
    organization_id: int,
    listing_id: int,
    payload: ListingUpdate,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    organization, membership = _require_inventory_access(
        db, organization_id, account.id
    )
    _require_inventory_write(organization, membership)

    listing = _get_organization_listing(db, organization_id, listing_id)
    changes = payload.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(status_code=400, detail="No listing changes supplied.")

    for field in ("title", "property_type", "city", "state"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null.",
            )

    previous_values = {
        field: getattr(listing, field)
        for field in changes
    }

    for field, value in changes.items():
        setattr(listing, field, value)

    _record_inventory_audit(
        db,
        organization_id,
        account.id,
        "inventory.listing_updated",
        {
            "listing_id": listing.id,
            "changed_fields": sorted(changes.keys()),
            "previous_values": previous_values,
        },
    )

    _commit_inventory_change(db)
    db.refresh(listing)
    return listing


@router.post(
    "/organizations/{organization_id}/listings/{listing_id}/publish",
    response_model=DeveloperListingResponse,
)
def publish_organization_listing(
    organization_id: int,
    listing_id: int,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    organization, membership = _require_inventory_access(
        db, organization_id, account.id
    )
    _require_inventory_write(organization, membership)

    listing = _get_organization_listing(db, organization_id, listing_id)

    if organization.verification_status != "verified":
        raise HTTPException(
            status_code=403,
            detail="Organization verification is required before publishing listings.",
        )

    if listing.is_published:
        raise HTTPException(status_code=409, detail="Listing is already published.")

    listing.is_published = True
    _record_inventory_audit(
        db,
        organization_id,
        account.id,
        "inventory.listing_published",
        {"listing_id": listing.id},
    )

    _commit_inventory_change(db)
    db.refresh(listing)
    return listing


@router.post(
    "/organizations/{organization_id}/listings/{listing_id}/unpublish",
    response_model=DeveloperListingResponse,
)
def unpublish_organization_listing(
    organization_id: int,
    listing_id: int,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    organization, membership = _require_inventory_access(
        db, organization_id, account.id
    )
    _require_inventory_write(
        organization,
        membership,
        allow_restricted=True,
    )

    listing = _get_organization_listing(db, organization_id, listing_id)

    if not listing.is_published:
        raise HTTPException(status_code=409, detail="Listing is already unpublished.")

    listing.is_published = False
    _record_inventory_audit(
        db,
        organization_id,
        account.id,
        "inventory.listing_unpublished",
        {"listing_id": listing.id},
    )

    _commit_inventory_change(db)
    db.refresh(listing)
    return listing


@router.patch(
    "/organizations/{organization_id}/listings/{listing_id}/availability",
    response_model=DeveloperListingResponse,
)
def update_organization_listing_availability(
    organization_id: int,
    listing_id: int,
    payload: ListingAvailabilityUpdate,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    organization, membership = _require_inventory_access(
        db, organization_id, account.id
    )
    _require_inventory_write(organization, membership)

    listing = _get_organization_listing(db, organization_id, listing_id)

    if listing.availability_status == payload.availability_status:
        raise HTTPException(
            status_code=409,
            detail="Listing already has this availability status.",
        )

    previous_status = listing.availability_status
    listing.availability_status = payload.availability_status

    _record_inventory_audit(
        db,
        organization_id,
        account.id,
        "inventory.availability_changed",
        {
            "listing_id": listing.id,
            "previous_status": previous_status,
            "new_status": payload.availability_status,
        },
    )

    _commit_inventory_change(db)
    db.refresh(listing)
    return listing
