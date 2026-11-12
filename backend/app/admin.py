import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from .database import get_db
from .auth_dependencies import resolve_session_account
from .models import (
    AuthIdentity,
    DeveloperOrganization,
    DeveloperOrganizationAuditLog,
    PropertyChannelPartnerAssignment,
    PropertyListing,
    StaffAuditLog,
    StaffInvitation,
    UserAccount,
)
from .security import hash_password


admin_router = APIRouter(prefix="/api/admin", tags=["admin"])
invitation_router = APIRouter(
    prefix="/api/staff-invitations",
    tags=["staff invitations"],
)

INVITATION_TTL_HOURS = 24
STAFF_ROLES = {"developer", "channel partner"}


def utc_now_naive() -> datetime:
    """Return UTC as a naive datetime for the existing database convention."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class StaffInvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=5, max_length=254)
    role: Literal["developer", "channel partner"]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class StaffInvitationToken(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=128)


class StaffInvitationAccept(StaffInvitationToken):
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if len(value) < 2:
            raise ValueError("Enter your full name.")
        return value


class DeveloperOrganizationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_status: Literal["verified", "rejected"]
    review_notes: str | None = Field(default=None, max_length=2000)

    @field_validator("review_notes")
    @classmethod
    def normalize_review_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

class PropertyChannelPartnerAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_partner_account_id: int = Field(gt=0)


def require_admin(
    request: Request,
    db: Session = Depends(get_db),
) -> UserAccount:
    """Resolve the canonical account identity and require Admin."""

    account = resolve_session_account(request, db)

    if str(account.role or "").strip().casefold() != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")

    user = request.session["user"]
    user["account_id"] = account.id
    user["role"] = "Admin"
    request.session["user"] = user

    return account


def invitation_status(invitation: StaffInvitation, now: datetime) -> str:
    if invitation.accepted_at is not None:
        return "accepted"
    if invitation.revoked_at is not None:
        return "revoked"
    if invitation.expires_at <= now:
        return "expired"
    return "pending"


def invitation_is_usable(
    invitation: StaffInvitation,
    now: datetime,
) -> bool:
    return (
        invitation.accepted_at is None
        and invitation.revoked_at is None
        and invitation.expires_at > now
    )


@admin_router.get("/summary")
def admin_summary(
    _admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    now = utc_now_naive()

    developer_count = db.scalar(
        select(func.count(UserAccount.id)).where(
            func.lower(UserAccount.role) == "developer"
        )
    ) or 0

    partner_count = db.scalar(
        select(func.count(UserAccount.id)).where(
            func.lower(UserAccount.role) == "channel partner"
        )
    ) or 0

    pending_count = db.scalar(
        select(func.count(StaffInvitation.id)).where(
            StaffInvitation.accepted_at.is_(None),
            StaffInvitation.revoked_at.is_(None),
            StaffInvitation.expires_at > now,
        )
    ) or 0

    return {
        "developers": developer_count,
        "channel_partners": partner_count,
        "pending_invitations": pending_count,
    }


@admin_router.get("/staff")
def list_staff(
    _admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    accounts = db.scalars(
        select(UserAccount)
        .where(func.lower(UserAccount.role).in_(STAFF_ROLES))
        .order_by(UserAccount.created_at.desc(), UserAccount.id.desc())
    ).all()

    return {
        "items": [
            {
                "id": account.id,
                "full_name": account.full_name,
                "email": account.email,
                "role": account.role,
                "is_active": bool(account.is_active),
                "created_at": account.created_at,
            }
            for account in accounts
        ]
    }


@admin_router.get("/audit-logs")
def list_staff_audit_logs(
    _admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    actor = aliased(UserAccount)
    target = aliased(UserAccount)

    rows = db.execute(
        select(
            StaffAuditLog,
            actor.full_name,
            actor.email,
            target.full_name,
            target.email,
        )
        .join(actor, actor.id == StaffAuditLog.actor_account_id)
        .outerjoin(target, target.id == StaffAuditLog.target_account_id)
        .order_by(
            StaffAuditLog.created_at.desc(),
            StaffAuditLog.id.desc(),
        )
        .limit(200)
    ).all()

    return {
        "items": [
            {
                "id": log.id,
                "action": log.action,
                "actor_account_id": log.actor_account_id,
                "actor_name": actor_name,
                "actor_email": actor_email,
                "target_account_id": log.target_account_id,
                "target_name": target_name,
                "target_email": target_email,
                "invitation_id": log.invitation_id,
                "details": log.details,
                "created_at": log.created_at,
            }
            for (
                log,
                actor_name,
                actor_email,
                target_name,
                target_email,
            ) in rows
        ]
    }


@admin_router.get("/invitations")
def list_invitations(
    _admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    now = utc_now_naive()

    invitations = db.scalars(
        select(StaffInvitation)
        .order_by(StaffInvitation.created_at.desc(), StaffInvitation.id.desc())
        .limit(100)
    ).all()

    return {
        "items": [
            {
                "id": item.id,
                "email": item.email,
                "role": item.role,
                "status": invitation_status(item, now),
                "created_at": item.created_at,
                "expires_at": item.expires_at,
                "accepted_at": item.accepted_at,
                "revoked_at": item.revoked_at,
            }
            for item in invitations
        ]
    }


@admin_router.post("/invitations", status_code=201)
def create_invitation(
    payload: StaffInvitationCreate,
    request: Request,
    admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = payload.email
    now = utc_now_naive()

    existing_account = db.scalar(
        select(UserAccount.id).where(func.lower(UserAccount.email) == email)
    )
    if existing_account is not None:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    existing_invitation = db.scalar(
        select(StaffInvitation.id).where(
            func.lower(StaffInvitation.email) == email,
            StaffInvitation.accepted_at.is_(None),
            StaffInvitation.revoked_at.is_(None),
            StaffInvitation.expires_at > now,
        )
    )
    if existing_invitation is not None:
        raise HTTPException(
            status_code=409,
            detail="A pending invitation already exists for this email.",
        )

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = now + timedelta(hours=INVITATION_TTL_HOURS)

    invitation = StaffInvitation(
        email=email,
        role=payload.role,
        token_hash=token_hash,
        invited_by_account_id=admin.id,
        expires_at=expires_at,
    )
    db.add(invitation)

    try:
        db.flush()
        db.add(
            StaffAuditLog(
                actor_account_id=admin.id,
                invitation_id=invitation.id,
                action="staff.invitation_created",
                details={
                    "email": email,
                    "role": payload.role,
                },
            )
        )
        db.commit()
        db.refresh(invitation)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The invitation could not be created because the email or token conflicts with another record.",
        )

    return {
        "id": invitation.id,
        "email": invitation.email,
        "role": invitation.role,
        "expires_at": invitation.expires_at,
        "invite_path": f"/staff-invitation#token={raw_token}",
    }


@admin_router.post("/invitations/{invitation_id}/revoke")
def revoke_invitation(
    invitation_id: int,
    admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    invitation = db.scalar(
        select(StaffInvitation)
        .where(StaffInvitation.id == invitation_id)
        .with_for_update()
    )

    if invitation is None:
        raise HTTPException(status_code=404, detail="Invitation not found.")

    now = utc_now_naive()
    if not invitation_is_usable(invitation, now):
        raise HTTPException(
            status_code=409,
            detail="Only pending, unexpired invitations can be revoked.",
        )

    invitation.revoked_at = now
    db.add(
        StaffAuditLog(
            actor_account_id=admin.id,
            invitation_id=invitation.id,
            action="staff.invitation_revoked",
            details={
                "email": invitation.email,
                "role": invitation.role,
            },
        )
    )
    db.commit()

    return {"revoked": True, "invitation_id": invitation.id}


@invitation_router.post("/inspect")
def inspect_invitation(
    payload: StaffInvitationToken,
    db: Session = Depends(get_db),
):
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    invitation = db.scalar(
        select(StaffInvitation).where(
            StaffInvitation.token_hash == token_hash
        )
    )

    if invitation is None or not invitation_is_usable(
        invitation, utc_now_naive()
    ):
        raise HTTPException(
            status_code=410,
            detail="This invitation is invalid, expired, revoked, or already used.",
        )

    return {
        "email": invitation.email,
        "role": invitation.role,
        "expires_at": invitation.expires_at,
    }


@invitation_router.post("/accept", status_code=201)
def accept_invitation(
    payload: StaffInvitationAccept,
    db: Session = Depends(get_db),
):
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()

    invitation = db.scalar(
        select(StaffInvitation)
        .where(StaffInvitation.token_hash == token_hash)
        .with_for_update()
    )

    if invitation is None or not invitation_is_usable(
        invitation, utc_now_naive()
    ):
        raise HTTPException(
            status_code=410,
            detail="This invitation is invalid, expired, revoked, or already used.",
        )

    existing_account = db.scalar(
        select(UserAccount.id).where(
            func.lower(UserAccount.email) == invitation.email.lower()
        )
    )
    if existing_account is not None:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Contact the administrator.",
        )

    account = UserAccount(
        full_name=payload.full_name,
        email=invitation.email,
        password_hash=hash_password(payload.password),
        role=invitation.role,
    )
    db.add(account)

    try:
        db.flush()

        db.add(
            AuthIdentity(
                account_id=account.id,
                provider="local_password",
                provider_subject=str(account.id),
            )
        )

        invitation.accepted_at = utc_now_naive()
        db.add(
            StaffAuditLog(
                actor_account_id=account.id,
                target_account_id=account.id,
                invitation_id=invitation.id,
                action="staff.invitation_accepted",
                details={
                    "role": account.role,
                    "invited_by_account_id": invitation.invited_by_account_id,
                },
            )
        )
        db.commit()
        db.refresh(account)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The account could not be created because the email or sign-in identity already exists.",
        )

    return {
        "success": True,
        "message": "Your staff account has been created. You can now sign in.",
        "account_id": account.id,
        "role": account.role,
        "redirect": "/index.html#login",
    }



def _set_staff_active(
    account_id: int,
    active: bool,
    admin: UserAccount,
    db: Session,
):
    target = db.scalar(
        select(UserAccount)
        .where(UserAccount.id == account_id)
        .with_for_update()
    )

    if (
        target is None
        or str(target.role or "").strip().casefold() not in STAFF_ROLES
    ):
        raise HTTPException(status_code=404, detail="Staff account not found.")

    if bool(target.is_active) == active:
        return {
            "account_id": target.id,
            "is_active": active,
            "changed": False,
        }

    target.is_active = active

    db.add(
        StaffAuditLog(
            actor_account_id=admin.id,
            target_account_id=target.id,
            action="staff.activated" if active else "staff.deactivated",
            details={"role": target.role},
        )
    )

    db.commit()

    return {
        "account_id": target.id,
        "is_active": active,
        "changed": True,
    }


@admin_router.post("/staff/{account_id}/deactivate")
def deactivate_staff(
    account_id: int,
    admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _set_staff_active(account_id, False, admin, db)


@admin_router.post("/staff/{account_id}/activate")
def activate_staff(
    account_id: int,
    admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _set_staff_active(account_id, True, admin, db)


@admin_router.get("/organizations/pending")
def list_pending_organizations(
    _admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    organizations = db.scalars(
        select(DeveloperOrganization)
        .where(
            DeveloperOrganization.verification_status
            == "pending_verification"
        )
        .order_by(
            DeveloperOrganization.created_at.asc(),
            DeveloperOrganization.id.asc(),
        )
        .limit(100)
    ).all()

    return {
        "items": [
            {
                "id": organization.id,
                "legal_name": organization.legal_name,
                "display_name": organization.display_name,
                "entity_type": organization.entity_type,
                "registration_number": organization.registration_number,
                "country_code": organization.country_code,
                "registered_address": organization.registered_address,
                "city": organization.city,
                "state": organization.state,
                "postal_code": organization.postal_code,
                "contact_email": organization.contact_email,
                "contact_phone": organization.contact_phone,
                "website": organization.website,
                "verification_status": organization.verification_status,
                "submitted_by_account_id": organization.submitted_by_account_id,
                "created_at": organization.created_at,
            }
            for organization in organizations
        ]
    }


@admin_router.post("/organizations/{organization_id}/review")
def review_organization(
    organization_id: int,
    payload: DeveloperOrganizationReview,
    admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    reviewed_at = utc_now_naive()
    previous_status = "pending_verification"
    new_status = payload.verification_status

    try:
        result = db.execute(
            update(DeveloperOrganization)
            .where(
                DeveloperOrganization.id == organization_id,
                DeveloperOrganization.verification_status == previous_status,
            )
            .values(
                verification_status=new_status,
                reviewed_by_account_id=admin.id,
                reviewed_at=reviewed_at,
                review_notes=payload.review_notes,
            )
        )

        if result.rowcount != 1:
            existing = db.get(DeveloperOrganization, organization_id)
            db.rollback()

            if existing is None:
                raise HTTPException(
                    status_code=404,
                    detail="Organization not found.",
                )

            raise HTTPException(
                status_code=409,
                detail="Organization is no longer pending verification.",
            )

        organization = db.get(DeveloperOrganization, organization_id)

        db.add(
            DeveloperOrganizationAuditLog(
                organization_id=organization_id,
                actor_account_id=admin.id,
                target_account_id=organization.submitted_by_account_id,
                action=f"organization.{new_status}",
                details={
                    "previous_status": previous_status,
                    "new_status": new_status,
                },
            )
        )

        db.commit()
        db.refresh(organization)

        return {
            "id": organization.id,
            "legal_name": organization.legal_name,
            "display_name": organization.display_name,
            "entity_type": organization.entity_type,
            "registration_number": organization.registration_number,
            "country_code": organization.country_code,
            "registered_address": organization.registered_address,
            "city": organization.city,
            "state": organization.state,
            "postal_code": organization.postal_code,
            "contact_email": organization.contact_email,
            "contact_phone": organization.contact_phone,
            "website": organization.website,
            "verification_status": organization.verification_status,
            "submitted_by_account_id": organization.submitted_by_account_id,
            "reviewed_by_account_id": organization.reviewed_by_account_id,
            "reviewed_at": organization.reviewed_at,
            "created_at": organization.created_at,
        }

    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


@admin_router.post("/properties/{property_id}/channel-partner-assignment")
def assign_property_channel_partner(
    property_id: int,
    payload: PropertyChannelPartnerAssign,
    admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        property_listing = db.scalar(
            select(PropertyListing)
            .where(PropertyListing.id == property_id)
            .with_for_update()
        )

        if property_listing is None:
            raise HTTPException(
                status_code=404,
                detail="Property not found.",
            )

        channel_partner = db.get(
            UserAccount,
            payload.channel_partner_account_id,
        )

        if channel_partner is None:
            raise HTTPException(
                status_code=404,
                detail="Channel Partner account not found.",
            )

        if not channel_partner.is_active:
            raise HTTPException(
                status_code=409,
                detail="Inactive accounts cannot receive property assignments.",
            )

        if str(channel_partner.role or "").strip().casefold() != "channel partner":
            raise HTTPException(
                status_code=409,
                detail="The selected account is not a Channel Partner.",
            )

        existing_assignment = db.scalar(
            select(PropertyChannelPartnerAssignment)
            .where(
                PropertyChannelPartnerAssignment.property_listing_id
                == property_id
            )
            .with_for_update()
        )

        if existing_assignment is not None:
            if (
                existing_assignment.channel_partner_account_id
                == channel_partner.id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="This property is already assigned to this Channel Partner.",
                )

            raise HTTPException(
                status_code=409,
                detail="This property is assigned to another Channel Partner. Revoke the existing assignment first.",
            )

        assignment = PropertyChannelPartnerAssignment(
            property_listing_id=property_id,
            channel_partner_account_id=channel_partner.id,
            assigned_by_account_id=admin.id,
        )
        db.add(assignment)
        db.flush()

        db.add(
            StaffAuditLog(
                actor_account_id=admin.id,
                target_account_id=channel_partner.id,
                action="property.channel_partner.assigned",
                details={
                    "property_listing_id": property_id,
                    "channel_partner_account_id": channel_partner.id,
                },
            )
        )

        db.commit()
        db.refresh(assignment)

        return {
            "id": assignment.id,
            "property_listing_id": assignment.property_listing_id,
            "channel_partner_account_id": assignment.channel_partner_account_id,
            "assigned_by_account_id": assignment.assigned_by_account_id,
            "assigned_at": assignment.assigned_at,
        }

    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The property assignment conflicts with another update.",
        )
    except Exception:
        db.rollback()
        raise


@admin_router.delete("/properties/{property_id}/channel-partner-assignment")
def revoke_property_channel_partner(
    property_id: int,
    admin: UserAccount = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        property_listing = db.scalar(
            select(PropertyListing)
            .where(PropertyListing.id == property_id)
            .with_for_update()
        )

        if property_listing is None:
            raise HTTPException(
                status_code=404,
                detail="Property not found.",
            )

        assignment = db.scalar(
            select(PropertyChannelPartnerAssignment)
            .where(
                PropertyChannelPartnerAssignment.property_listing_id
                == property_id
            )
            .with_for_update()
        )

        if assignment is None:
            raise HTTPException(
                status_code=404,
                detail="No active Channel Partner assignment exists for this property.",
            )

        previous_channel_partner_id = assignment.channel_partner_account_id

        db.add(
            StaffAuditLog(
                actor_account_id=admin.id,
                target_account_id=previous_channel_partner_id,
                action="property.channel_partner.revoked",
                details={
                    "property_listing_id": property_id,
                    "previous_channel_partner_account_id": (
                        previous_channel_partner_id
                    ),
                },
            )
        )

        db.delete(assignment)
        db.commit()

        return {
            "message": "Channel Partner assignment revoked.",
            "property_listing_id": property_id,
            "previous_channel_partner_account_id": previous_channel_partner_id,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise