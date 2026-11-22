from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth_dependencies import resolve_session_account
from .database import get_db
from .models import (
    BuyerSiteVisit,
    CommissionLedgerEntry,
    PropertyChannelPartnerAssignment,
    PropertyInventoryItem,
    PropertyListing,
    RealEstateTransaction,
    UserAccount,
)

router = APIRouter(
    prefix="/api/channel-partners",
    tags=["Channel Partners"],
)


def _require_channel_partner(request: Request, db: Session) -> UserAccount:
    account = resolve_session_account(request, db)
    if str(account.role or "").strip().casefold() != "channel partner":
        raise HTTPException(
            status_code=403,
            detail="Channel Partner access required.",
        )
    return account




def _listing_data(listing: PropertyListing, items: list[PropertyInventoryItem]):
    return {
        "id": listing.id,
        "title": listing.title,
        "property_type": listing.property_type,
        "city": listing.city,
        "state": listing.state,
        "price_inr": listing.price_inr,
        "bedrooms": listing.bedrooms,
        "area_sqft": listing.area_sqft,
        "image_url": listing.image_url,
        "availability_status": listing.availability_status,
        "is_published": listing.is_published,
        "inventory": [
            {
                "id": item.id,
                "item_type": item.item_type,
                "unit_code": item.unit_code,
                "price_inr": item.price_inr,
                "bedrooms": item.bedrooms,
                "area_sqft": item.area_sqft,
                "availability_status": item.availability_status,
            }
            for item in items
        ],
    }


@router.get("/dashboard")
def channel_partner_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    account = _require_channel_partner(request, db)

    active_assignments = db.scalar(
        select(func.count())
        .select_from(PropertyChannelPartnerAssignment)
        .where(
            PropertyChannelPartnerAssignment.channel_partner_account_id
            == account.id,
            PropertyChannelPartnerAssignment.ended_at.is_(None),
        )
    ) or 0

    attributed_transactions = db.scalar(
        select(func.count())
        .select_from(RealEstateTransaction)
        .where(
            RealEstateTransaction.channel_partner_account_id == account.id
        )
    ) or 0

    assigned_listing_ids = select(
        PropertyChannelPartnerAssignment.property_listing_id
    ).where(
        PropertyChannelPartnerAssignment.channel_partner_account_id
        == account.id,
        PropertyChannelPartnerAssignment.ended_at.is_(None),
    )

    assigned_visits = db.scalar(
        select(func.count())
        .select_from(BuyerSiteVisit)
        .where(BuyerSiteVisit.property_listing_id.in_(assigned_listing_ids))
    ) or 0

    return {
        "account": {
            "id": account.id,
            "full_name": account.full_name,
            "email": account.email,
        },
        "metrics": {
            "active_assignments": active_assignments,
            "attributed_transactions": attributed_transactions,
            "site_visits": assigned_visits,
        },
    }


@router.get("/inventory")
def list_assigned_inventory(
    request: Request,
    db: Session = Depends(get_db),
):
    account = _require_channel_partner(request, db)

    rows = db.execute(
        select(PropertyListing)
        .join(
            PropertyChannelPartnerAssignment,
            PropertyChannelPartnerAssignment.property_listing_id
            == PropertyListing.id,
        )
        .where(
            PropertyChannelPartnerAssignment.channel_partner_account_id
            == account.id,
            PropertyChannelPartnerAssignment.ended_at.is_(None),
        )
        .order_by(PropertyListing.created_at.desc(), PropertyListing.id.desc())
    ).scalars().all()

    result = []
    for listing in rows:
        items = db.scalars(
            select(PropertyInventoryItem)
            .where(PropertyInventoryItem.property_listing_id == listing.id)
            .order_by(PropertyInventoryItem.id.asc())
        ).all()
        result.append(_listing_data(listing, items))

    return result


@router.get("/transactions")
def list_attributed_transactions(
    request: Request,
    db: Session = Depends(get_db),
):
    account = _require_channel_partner(request, db)

    rows = db.execute(
        select(RealEstateTransaction, PropertyListing)
        .join(
            PropertyListing,
            PropertyListing.id == RealEstateTransaction.property_listing_id,
        )
        .where(
            RealEstateTransaction.channel_partner_account_id == account.id
        )
        .order_by(
            RealEstateTransaction.created_at.desc(),
            RealEstateTransaction.id.desc(),
        )
    ).all()

    return [
        {
            "id": transaction.id,
            "buyer_account_id": transaction.buyer_account_id,
            "property_listing_id": transaction.property_listing_id,
            "property_title": listing.title,
            "inventory_item_id": transaction.inventory_item_id,
            "developer_organization_id": transaction.developer_organization_id,
            "channel_partner_assignment_id": transaction.channel_partner_assignment_id,
            "transaction_type": transaction.transaction_type,
            "status": transaction.status,
            "created_at": transaction.created_at,
            "updated_at": transaction.updated_at,
        }
        for transaction, listing in rows
    ]


@router.get("/site-visits")
def list_assigned_site_visits(
    request: Request,
    db: Session = Depends(get_db),
):
    account = _require_channel_partner(request, db)

    rows = db.execute(
        select(BuyerSiteVisit, PropertyListing)
        .join(
            PropertyListing,
            PropertyListing.id == BuyerSiteVisit.property_listing_id,
        )
        .where(
            BuyerSiteVisit.property_listing_id.in_(
                select(PropertyChannelPartnerAssignment.property_listing_id)
                .where(
                    PropertyChannelPartnerAssignment.channel_partner_account_id
                    == account.id,
                    PropertyChannelPartnerAssignment.ended_at.is_(None),
                )
            )
        )
        .order_by(
            BuyerSiteVisit.scheduled_at.desc(),
            BuyerSiteVisit.id.desc(),
        )
    ).all()

    return [
        {
            "id": visit.id,
            "property_listing_id": listing.id,
            "property_title": listing.title,
            "scheduled_at": visit.scheduled_at,
            "message": visit.message,
            "status": visit.status,
            "created_at": visit.created_at,
            "updated_at": visit.updated_at,
        }
        for visit, listing in rows
    ]


@router.get("/site-visits/{visit_id}")
def get_assigned_site_visit(
    visit_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    account = _require_channel_partner(request, db)

    row = db.execute(
        select(BuyerSiteVisit, PropertyListing)
        .join(
            PropertyListing,
            PropertyListing.id == BuyerSiteVisit.property_listing_id,
        )
        .join(
            PropertyChannelPartnerAssignment,
            PropertyChannelPartnerAssignment.property_listing_id
            == BuyerSiteVisit.property_listing_id,
        )
        .where(
            BuyerSiteVisit.id == visit_id,
            PropertyChannelPartnerAssignment.channel_partner_account_id
            == account.id,
            PropertyChannelPartnerAssignment.ended_at.is_(None),
        )
    ).first()

    if row is None:
        raise HTTPException(status_code=404, detail="Site visit not found.")

    visit, listing = row
    return {
        "id": visit.id,
        "property_listing_id": listing.id,
        "property_title": listing.title,
        "scheduled_at": visit.scheduled_at,
        "message": visit.message,
        "status": visit.status,
        "created_at": visit.created_at,
        "updated_at": visit.updated_at,
    }


@router.get("/earnings")
def channel_partner_earnings(
    request: Request,
    db: Session = Depends(get_db),
):
    """Return commission ledger and totals for the authenticated CP only."""
    account = _require_channel_partner(request, db)

    all_entries = db.scalars(
        select(CommissionLedgerEntry)
        .where(
            CommissionLedgerEntry.channel_partner_account_id == account.id
        )
        .order_by(
            CommissionLedgerEntry.created_at.desc(),
            CommissionLedgerEntry.id.desc(),
        )
    ).all()

    totals = {}
    for entry in all_entries:
        currency = entry.currency
        bucket = totals.setdefault(
            currency,
            {
                "pending": Decimal("0.00"),
                "approved": Decimal("0.00"),
                "rejected": Decimal("0.00"),
                "gross_accrued": Decimal("0.00"),
                "total_reversed": Decimal("0.00"),
                "net_earnings": Decimal("0.00"),
            },
        )
        amount = Decimal(str(entry.amount))
        signed = amount if entry.entry_type == "accrual" else -amount

        bucket[entry.approval_status] += signed
        if entry.entry_type == "accrual":
            bucket["gross_accrued"] += amount
        else:
            bucket["total_reversed"] += amount
        bucket["net_earnings"] += signed

    return {
        "totals_by_currency": {
            currency: {
                key: str(value.quantize(Decimal("0.01")))
                for key, value in bucket.items()
            }
            for currency, bucket in totals.items()
        },
        "items": [
            {
                "id": entry.id,
                "transaction_id": entry.transaction_id,
                "entry_type": entry.entry_type,
                "original_accrual_id": entry.original_accrual_id,
                "basis_amount": str(entry.basis_amount),
                "amount": str(entry.amount),
                "rate_bps": entry.rate_bps,
                "currency": entry.currency,
                "approval_status": entry.approval_status,
                "reviewed_at": entry.reviewed_at,
                "review_note": entry.review_note,
                "created_at": entry.created_at,
            }
            for entry in all_entries[:200]
        ],
    }
