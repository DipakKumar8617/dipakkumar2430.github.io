import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
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

MembershipRole = Literal["admin", "inventory_manager", "viewer"]
MembershipStatus = Literal["suspended", "removed"]


class DeveloperOrganizationMemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=5, max_length=254)
    role: MembershipRole = "viewer"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class DeveloperOrganizationMemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MembershipRole


class DeveloperOrganizationMemberStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MembershipStatus


class DeveloperOrganizationMemberResponse(BaseModel):
    account_id: int
    full_name: str | None
    email: str | None
    role: str
    status: str
    joined_at: datetime


def _require_membership_manager(
    db: Session,
    organization_id: int,
    account_id: int,
):
    organization = db.get(DeveloperOrganization, organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    actor_membership = db.scalar(
        select(DeveloperOrganizationMembership).where(
            DeveloperOrganizationMembership.organization_id == organization_id,
            DeveloperOrganizationMembership.account_id == account_id,
            DeveloperOrganizationMembership.status == "active",
        )
    )
    if actor_membership is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    if actor_membership.role not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to manage organization members.",
        )

    return organization, actor_membership


def _require_managed_member(
    db: Session,
    organization_id: int,
    target_account_id: int,
    actor_membership: DeveloperOrganizationMembership,
):
    target = db.scalar(
        select(DeveloperOrganizationMembership)
        .where(
            DeveloperOrganizationMembership.organization_id == organization_id,
            DeveloperOrganizationMembership.account_id == target_account_id,
        )
        .with_for_update()
    )

    if target is None:
        raise HTTPException(status_code=404, detail="Member not found.")

    if target.role == "owner":
        raise HTTPException(
            status_code=409,
            detail="Owner membership cannot be changed through member management.",
        )

    if actor_membership.role == "admin" and target.role not in {
        "inventory_manager",
        "viewer",
    }:
        raise HTTPException(
            status_code=403,
            detail="Admins can manage inventory managers and viewers only.",
        )

    return target


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


@router.get(
    "/{organization_id}/members",
    response_model=list[DeveloperOrganizationMemberResponse],
)
def list_developer_organization_members(
    organization_id: int,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    _organization, _actor = _require_membership_manager(
        db, organization_id, account.id
    )
    rows = db.execute(
        select(DeveloperOrganizationMembership, UserAccount)
        .join(
            UserAccount,
            UserAccount.id == DeveloperOrganizationMembership.account_id,
        )
        .where(
            DeveloperOrganizationMembership.organization_id == organization_id
        )
        .order_by(
            DeveloperOrganizationMembership.status,
            DeveloperOrganizationMembership.role,
            UserAccount.id,
        )
    ).all()

    return [
        DeveloperOrganizationMemberResponse(
            account_id=membership.account_id,
            full_name=member_account.full_name,
            email=member_account.email,
            role=membership.role,
            status=membership.status,
            joined_at=membership.joined_at,
        )
        for membership, member_account in rows
    ]


@router.post(
    "/{organization_id}/members",
    response_model=DeveloperOrganizationMemberResponse,
    status_code=201,
)
def add_developer_organization_member(
    organization_id: int,
    payload: DeveloperOrganizationMemberAdd,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    _organization, actor = _require_membership_manager(
        db, organization_id, account.id
    )

    if actor.role == "admin" and payload.role == "admin":
        raise HTTPException(
            status_code=403,
            detail="Only the organization owner can assign the admin role.",
        )

    target_account = db.scalar(
        select(UserAccount).where(
            func.lower(UserAccount.email) == payload.email,
            UserAccount.is_active.is_(True),
            func.lower(UserAccount.role) == "developer",
        )
    )
    if target_account is None:
        raise HTTPException(
            status_code=404,
            detail="No active developer account was found for that email.",
        )

    try:
        membership = db.scalar(
            select(DeveloperOrganizationMembership)
            .where(
                DeveloperOrganizationMembership.organization_id == organization_id,
                DeveloperOrganizationMembership.account_id == target_account.id,
            )
            .with_for_update()
        )

        if membership is not None:
            if membership.role == "owner":
                raise HTTPException(
                    status_code=409,
                    detail="The organization owner cannot be re-added.",
                )
            if actor.role == "admin" and membership.role == "admin":
                raise HTTPException(
                    status_code=403,
                    detail="Admins cannot manage other admin memberships.",
                )
            if membership.status == "active":
                raise HTTPException(
                    status_code=409,
                    detail="This account is already an active organization member.",
                )

            previous_status = membership.status
            previous_role = membership.role
            membership.status = "active"
            membership.role = payload.role
            action = "membership.reactivated"
            details = {
                "previous_status": previous_status,
                "previous_role": previous_role,
                "new_status": "active",
                "new_role": payload.role,
            }
        else:
            membership = DeveloperOrganizationMembership(
                organization_id=organization_id,
                account_id=target_account.id,
                role=payload.role,
                status="active",
                created_by_account_id=account.id,
            )
            db.add(membership)
            action = "membership.added"
            details = {"new_status": "active", "new_role": payload.role}

        db.add(
            DeveloperOrganizationAuditLog(
                organization_id=organization_id,
                actor_account_id=account.id,
                target_account_id=target_account.id,
                action=action,
                details=details,
            )
        )
        db.flush()
        db.refresh(membership)

        response = DeveloperOrganizationMemberResponse(
            account_id=membership.account_id,
            full_name=target_account.full_name,
            email=target_account.email,
            role=membership.role,
            status=membership.status,
            joined_at=membership.joined_at,
        )
        db.commit()
        return response

    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Membership conflicts with existing organization data.",
        )
    except Exception:
        db.rollback()
        raise


@router.patch(
    "/{organization_id}/members/{target_account_id}/role",
    response_model=DeveloperOrganizationMemberResponse,
)
def update_developer_organization_member_role(
    organization_id: int,
    target_account_id: int,
    payload: DeveloperOrganizationMemberRoleUpdate,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    _organization, actor = _require_membership_manager(
        db, organization_id, account.id
    )

    if actor.role == "admin" and payload.role == "admin":
        raise HTTPException(
            status_code=403,
            detail="Only the organization owner can assign the admin role.",
        )

    try:
        target = _require_managed_member(
            db, organization_id, target_account_id, actor
        )

        if target.status != "active":
            raise HTTPException(
                status_code=409,
                detail="Only active memberships can have their role changed.",
            )
        if target.role == payload.role:
            raise HTTPException(
                status_code=409,
                detail="The member already has this role.",
            )

        previous_role = target.role
        target.role = payload.role

        db.add(
            DeveloperOrganizationAuditLog(
                organization_id=organization_id,
                actor_account_id=account.id,
                target_account_id=target_account_id,
                action="membership.role_changed",
                details={
                    "previous_role": previous_role,
                    "new_role": payload.role,
                },
            )
        )

        target_account = db.get(UserAccount, target_account_id)
        db.flush()
        response = DeveloperOrganizationMemberResponse(
            account_id=target.account_id,
            full_name=target_account.full_name,
            email=target_account.email,
            role=target.role,
            status=target.status,
            joined_at=target.joined_at,
        )
        db.commit()
        return response

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.patch(
    "/{organization_id}/members/{target_account_id}/status",
    response_model=DeveloperOrganizationMemberResponse,
)
def update_developer_organization_member_status(
    organization_id: int,
    target_account_id: int,
    payload: DeveloperOrganizationMemberStatusUpdate,
    account: UserAccount = Depends(require_developer),
    db: Session = Depends(get_db),
):
    _organization, actor = _require_membership_manager(
        db, organization_id, account.id
    )

    try:
        target = _require_managed_member(
            db, organization_id, target_account_id, actor
        )

        if target.status == payload.status:
            raise HTTPException(
                status_code=409,
                detail="The member already has this status.",
            )

        previous_status = target.status
        target.status = payload.status

        db.add(
            DeveloperOrganizationAuditLog(
                organization_id=organization_id,
                actor_account_id=account.id,
                target_account_id=target_account_id,
                action="membership.status_changed",
                details={
                    "previous_status": previous_status,
                    "new_status": payload.status,
                },
            )
        )

        target_account = db.get(UserAccount, target_account_id)
        db.flush()
        response = DeveloperOrganizationMemberResponse(
            account_id=target.account_id,
            full_name=target_account.full_name,
            email=target_account.email,
            role=target.role,
            status=target.status,
            joined_at=target.joined_at,
        )
        db.commit()
        return response

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
