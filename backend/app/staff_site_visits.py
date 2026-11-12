from datetime import timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth_dependencies import resolve_session_account
from .database import get_db
from .models import (
    BuyerSiteVisit,
    DeveloperOrganizationMembership,
    PropertyChannelPartnerAssignment,
    PropertyListing,
    StaffAuditLog,
    UserAccount,
)


router = APIRouter(prefix="/api/staff/site-visits", tags=["staff site visits"])

PRIVILEGED_DEVELOPER_ROLES = {"owner", "admin", "inventory_manager"}
ALLOWED_TRANSITIONS = {
    "requested": {"confirmed", "rejected"},
    "confirmed": {"completed"},
}


class SiteVisitStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "rejected", "completed"]


def _staff_account(request: Request, db: Session) -> UserAccount:
    account = resolve_session_account(request, db)
    role = account.role.strip().casefold()

    if role not in {"admin", "developer", "channel partner"}:
        raise HTTPException(status_code=403, detail="Staff access required.")

    return account


def _visit_query(account: UserAccount):
    role = account.role.strip().casefold()

    statement = (
        select(
            BuyerSiteVisit,
            PropertyListing,
            UserAccount,
            DeveloperOrganizationMembership.role,
        )
        .join(
            PropertyListing,
            PropertyListing.id == BuyerSiteVisit.property_listing_id,
        )
        .join(UserAccount, UserAccount.id == BuyerSiteVisit.account_id)
        .outerjoin(
            DeveloperOrganizationMembership,
            (
                DeveloperOrganizationMembership.organization_id
                == PropertyListing.developer_organization_id
            )
            & (DeveloperOrganizationMembership.account_id == account.id)
            & (DeveloperOrganizationMembership.status == "active"),
        )
    )

    if role == "developer":
        statement = statement.where(
            DeveloperOrganizationMembership.id.is_not(None)
        )
    elif role == "channel partner":
        assignment_exists = select(PropertyChannelPartnerAssignment.id).where(
            PropertyChannelPartnerAssignment.property_listing_id
            == BuyerSiteVisit.property_listing_id,
            PropertyChannelPartnerAssignment.channel_partner_account_id
            == account.id,
        ).exists()
        statement = statement.where(assignment_exists)

    return statement


def _visit_data(row, account: UserAccount) -> dict:
    visit, listing, buyer, membership_role = row
    role = account.role.strip().casefold()

    can_view_contact = (
        role in {"admin", "channel partner"}
        or (
            role == "developer"
            and membership_role in PRIVILEGED_DEVELOPER_ROLES
        )
    )

    scheduled_at = visit.scheduled_at
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    created_at = visit.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    updated_at = visit.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    data = {
        "id": visit.id,
        "property_listing_id": listing.id,
        "property": {
            "id": listing.id,
            "title": listing.title,
            "property_type": listing.property_type,
            "city": listing.city,
            "state": listing.state,
            "price_inr": listing.price_inr,
            "availability_status": listing.availability_status,
        },
        "scheduled_at": scheduled_at.astimezone(timezone.utc).isoformat(),
        "message": visit.message,
        "status": visit.status,
        "created_at": created_at.astimezone(timezone.utc).isoformat(),
        "updated_at": updated_at.astimezone(timezone.utc).isoformat(),
    }

    if can_view_contact:
        data["buyer"] = {
            "account_id": buyer.id,
            "full_name": buyer.full_name,
            "email": buyer.email,
            "phone": buyer.phone,
        }

    return data


@router.get("")
def list_staff_site_visits(
    request: Request,
    db: Session = Depends(get_db),
):
    account = _staff_account(request, db)

    rows = db.execute(
        _visit_query(account).order_by(
            BuyerSiteVisit.scheduled_at.desc(),
            BuyerSiteVisit.id.desc(),
        )
    ).all()

    return [_visit_data(row, account) for row in rows]


@router.get("/{visit_id}")
def get_staff_site_visit(
    visit_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    account = _staff_account(request, db)

    row = db.execute(
        _visit_query(account).where(BuyerSiteVisit.id == visit_id)
    ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="Site visit not found.")

    return _visit_data(row, account)


@router.patch("/{visit_id}/status")
def update_staff_site_visit_status(
    visit_id: int,
    payload: SiteVisitStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    account = _staff_account(request, db)
    role = account.role.strip().casefold()

    row = db.execute(
        _visit_query(account)
        .where(BuyerSiteVisit.id == visit_id)
        .with_for_update(of=BuyerSiteVisit)
    ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="Site visit not found.")

    visit, _listing, buyer, membership_role = row

    can_manage = (
        role in {"admin", "channel partner"}
        or (
            role == "developer"
            and membership_role in PRIVILEGED_DEVELOPER_ROLES
        )
    )

    if not can_manage:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to update this site visit.",
        )

    allowed = ALLOWED_TRANSITIONS.get(visit.status, set())
    if payload.status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot change site visit status from '{visit.status}' to '{payload.status}'.",
        )

    previous_status = visit.status
    visit.status = payload.status

    try:
        db.add(
            StaffAuditLog(
                actor_account_id=account.id,
                target_account_id=buyer.id,
                action="site_visit.status_changed",
                details={
                    "site_visit_id": visit.id,
                    "property_listing_id": visit.property_listing_id,
                    "previous_status": previous_status,
                    "new_status": payload.status,
                },
            )
        )
        db.commit()
        db.refresh(visit)
    except Exception:
        db.rollback()
        raise

    updated_row = db.execute(
        _visit_query(account).where(BuyerSiteVisit.id == visit.id)
    ).first()

    if updated_row is None:
        raise HTTPException(status_code=404, detail="Site visit not found.")

    return _visit_data(updated_row, account)
