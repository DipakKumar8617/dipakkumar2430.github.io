import re
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
    UserAccount,
)


router = APIRouter(
    prefix="/api/developer-organizations",
    tags=["developer organizations"],
)


EntityType = Literal[
    "private_limited",
    "public_limited",
    "llp",
    "partnership",
    "proprietorship",
    "other",
]


class DeveloperOrganizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    entity_type: EntityType
    registration_number: str | None = Field(default=None, max_length=100)
    country_code: str = Field(min_length=2, max_length=2)
    registered_address: str = Field(min_length=1, max_length=5000)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)
    contact_email: str = Field(min_length=5, max_length=254)
    contact_phone: str = Field(min_length=7, max_length=20)
    website: str | None = Field(default=None, max_length=255)

    @field_validator(
        "legal_name",
        "display_name",
        "registration_number",
        "registered_address",
        "city",
        "state",
        "postal_code",
        "contact_email",
        "contact_phone",
        "website",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("legal_name", "registered_address", "city", "state", "postal_code")
    @classmethod
    def required_text(cls, value):
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("contact_email")
    @classmethod
    def validate_contact_email(cls, value):
        if not value or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid contact email address.")
        return value.lower()

    @field_validator("contact_phone")
    @classmethod
    def validate_contact_phone(cls, value):
        if (
            not value
            or not re.fullmatch(r"\+?[0-9().\-\s]{7,20}", value)
            or sum(character.isdigit() for character in value) < 7
        ):
            raise ValueError("Enter a valid contact phone number.")
        return value

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country_code(cls, value):
        if isinstance(value, str):
            value = value.strip().upper()
        if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{2}", value):
            raise ValueError("Country code must be a two-letter code.")
        return value


class DeveloperOrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    legal_name: str
    display_name: str | None
    entity_type: str
    registration_number: str | None
    country_code: str
    registered_address: str
    city: str
    state: str
    postal_code: str
    contact_email: str
    contact_phone: str
    website: str | None
    verification_status: str
    created_at: datetime
    updated_at: datetime


def _require_active_membership(
    db: Session,
    organization_id: int,
    account_id: int,
) -> DeveloperOrganization:
    membership = db.scalar(
        select(DeveloperOrganizationMembership.id).where(
            DeveloperOrganizationMembership.organization_id == organization_id,
            DeveloperOrganizationMembership.account_id == account_id,
            DeveloperOrganizationMembership.status == "active",
        )
    )

    if membership is None:
        raise HTTPException(
            status_code=404,
            detail="Organization not found.",
        )

    organization = db.get(DeveloperOrganization, organization_id)

    if organization is None:
        raise HTTPException(
            status_code=404,
            detail="Organization not found.",
        )

    return organization


@router.post(
    "",
    response_model=DeveloperOrganizationResponse,
    status_code=201,
)
def register_developer_organization(
    payload: DeveloperOrganizationCreate,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    organization = DeveloperOrganization(
        **payload.model_dump(),
        submitted_by_account_id=account.id,
    )
    db.add(organization)

    try:
        db.flush()

        db.add(
            DeveloperOrganizationMembership(
                organization_id=organization.id,
                account_id=account.id,
                role="owner",
                status="active",
                created_by_account_id=account.id,
            )
        )

        db.add(
            DeveloperOrganizationAuditLog(
                organization_id=organization.id,
                actor_account_id=account.id,
                action="organization.registered",
                details={
                    "entity_type": organization.entity_type,
                    "country_code": organization.country_code,
                },
            )
        )

        db.commit()
        db.refresh(organization)

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Organization registration conflicts with existing data.",
        )
    except Exception:
        db.rollback()
        raise

    return organization


@router.get(
    "",
    response_model=list[DeveloperOrganizationResponse],
)
def list_developer_organizations(
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    statement = (
        select(DeveloperOrganization)
        .join(
            DeveloperOrganizationMembership,
            DeveloperOrganizationMembership.organization_id
            == DeveloperOrganization.id,
        )
        .where(
            DeveloperOrganizationMembership.account_id == account.id,
            DeveloperOrganizationMembership.status == "active",
        )
        .order_by(DeveloperOrganization.created_at.desc())
    )

    return list(db.scalars(statement).all())


@router.get(
    "/{organization_id}",
    response_model=DeveloperOrganizationResponse,
)
def get_developer_organization(
    organization_id: int,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    return _require_active_membership(
        db,
        organization_id,
        account.id,
    )
