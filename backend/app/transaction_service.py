from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    DeveloperOrganizationMembership,
    InventoryHold,
    PropertyChannelPartnerAssignment,
    PropertyInventoryItem,
    PropertyListing,
    RealEstateTransaction,
    TransactionEvent,
    UserAccount,
)


TRANSACTION_TRANSITIONS = {
    "initiated": {"under_review", "negotiating", "cancelled", "rejected", "expired"},
    "under_review": {"negotiating", "terms_agreed", "cancelled", "rejected", "expired"},
    "negotiating": {"terms_agreed", "cancelled", "rejected", "expired"},
    "terms_agreed": {"negotiating", "booking_pending", "cancelled", "expired"},
    "booking_pending": {"booked", "cancelled", "expired"},
    "booked": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
    "rejected": set(),
    "expired": set(),
}

BUYER_TRANSITIONS = {"negotiating", "terms_agreed", "cancelled"}
PARTNER_TRANSITIONS = {
    "under_review", "negotiating", "terms_agreed", "rejected", "cancelled"
}
DEVELOPER_WRITE_ROLES = {"owner", "admin", "inventory_manager"}


def _role(account: UserAccount) -> str:
    return str(account.role or "").strip().casefold().replace(" ", "_")


def _is_admin(account: UserAccount) -> bool:
    return _role(account) in {"admin", "administrator", "platform_admin"}


def _developer_membership(
    db: Session,
    transaction: RealEstateTransaction,
    account: UserAccount,
) -> DeveloperOrganizationMembership | None:
    if transaction.developer_organization_id is None:
        return None

    return db.scalar(
        select(DeveloperOrganizationMembership).where(
            DeveloperOrganizationMembership.organization_id
            == transaction.developer_organization_id,
            DeveloperOrganizationMembership.account_id == account.id,
            DeveloperOrganizationMembership.status == "active",
        )
    )



def _active_transaction_partner_assignment(db, transaction, account):
    if not transaction.channel_partner_assignment_id:
        return None

    return db.scalar(
        select(PropertyChannelPartnerAssignment).where(
            PropertyChannelPartnerAssignment.id
            == transaction.channel_partner_assignment_id,
            PropertyChannelPartnerAssignment.channel_partner_account_id
            == account.id,
            PropertyChannelPartnerAssignment.ended_at.is_(None),
        )
    )


def authorize_transaction_access(
    db: Session,
    transaction: RealEstateTransaction,
    account: UserAccount,
    *,
    write: bool = False,
) -> None:
    """Enforce transaction access using current account and assignment data."""

    if _is_admin(account):
        return

    role = _role(account)

    if transaction.buyer_account_id == account.id:
        if not write or role == "buyer":
            return

    if role in {"channel_partner", "channelpartner", "cp"}:
        if _active_transaction_partner_assignment(db, transaction, account):
            return

    membership = _developer_membership(db, transaction, account)
    if membership is not None:
        if not write or str(membership.role).casefold() in DEVELOPER_WRITE_ROLES:
            return

    raise PermissionError("You do not have access to this transaction.")


def create_transaction(
    db: Session,
    account: UserAccount,
    *,
    property_listing_id: int,
    inventory_item_id: int,
    transaction_type: str,
) -> RealEstateTransaction:
    """Create a transaction and its initial event; caller owns the DB commit."""

    if _role(account) != "buyer":
        raise PermissionError("Only buyer accounts can initiate transactions.")

    if transaction_type not in {"direct_booking", "negotiation"}:
        raise ValueError("Unsupported transaction type.")

    listing = db.scalar(
        select(PropertyListing)
        .where(PropertyListing.id == property_listing_id)
        .with_for_update()
    )
    if listing is None or not listing.is_published:
        raise ValueError("Property listing is unavailable.")

    inventory_item = db.scalar(
        select(PropertyInventoryItem)
        .where(
            PropertyInventoryItem.id == inventory_item_id,
            PropertyInventoryItem.property_listing_id == property_listing_id,
        )
        .with_for_update()
    )
    if inventory_item is None:
        raise ValueError("Inventory item does not belong to this listing.")

    if str(inventory_item.availability_status).casefold() != "available":
        raise ValueError("Inventory item is not available.")

    assignment = db.scalar(
        select(PropertyChannelPartnerAssignment).where(
            PropertyChannelPartnerAssignment.property_listing_id
            == property_listing_id,
            PropertyChannelPartnerAssignment.ended_at.is_(None),
        )
    )

    transaction = RealEstateTransaction(
        buyer_account_id=account.id,
        property_listing_id=property_listing_id,
        inventory_item_id=inventory_item_id,
        developer_organization_id=listing.developer_organization_id,
        channel_partner_assignment_id=assignment.id if assignment else None,
        channel_partner_account_id=(
            assignment.channel_partner_account_id if assignment else None
        ),
        transaction_type=transaction_type,
        status="initiated",
    )
    db.add(transaction)
    db.flush()

    db.add(
        TransactionEvent(
            transaction_id=transaction.id,
            actor_account_id=account.id,
            event_type="transaction_created",
            from_status=None,
            to_status="initiated",
            details={"transaction_type": transaction_type},
        )
    )
    db.flush()

    return transaction


def transition_transaction(
    db: Session,
    account: UserAccount,
    *,
    transaction_id: int,
    to_status: str,
    event_type: str = "status_changed",
    details: dict | None = None,
) -> RealEstateTransaction:
    """Validate and record one transaction state transition."""

    transaction = db.scalar(
        select(RealEstateTransaction)
        .where(RealEstateTransaction.id == transaction_id)
        .with_for_update()
    )
    if transaction is None:
        raise LookupError("Transaction not found.")

    authorize_transaction_access(db, transaction, account, write=True)

    current_status = transaction.status
    if to_status not in TRANSACTION_TRANSITIONS.get(current_status, set()):
        raise ValueError(
            f"Invalid transaction transition: {current_status} -> {to_status}."
        )

    if to_status == "terms_agreed":
        raise ValueError("Terms can only be agreed through offer acceptance.")

    # Booking transitions require the dedicated atomic hold/booking workflow.
    if to_status in {"booking_pending", "booked", "completed"}:
        raise ValueError(
            "Booking-related transitions must use the booking service."
        )

    role = _role(account)
    is_buyer = transaction.buyer_account_id == account.id
    is_partner = (
        role in {"channel_partner", "channelpartner", "cp"}
        and _active_transaction_partner_assignment(db, transaction, account)
        is not None
    )
    membership = _developer_membership(db, transaction, account)
    is_developer_writer = (
        membership is not None
        and str(membership.role).casefold() in DEVELOPER_WRITE_ROLES
    )

    if not _is_admin(account):
        if is_buyer and to_status not in BUYER_TRANSITIONS:
            raise PermissionError("Buyer cannot perform this transition.")
        if not is_buyer and (is_partner or is_developer_writer):
            if to_status not in PARTNER_TRANSITIONS:
                raise PermissionError("This account cannot perform this transition.")
        elif not is_buyer:
            raise PermissionError("Transaction update permission required.")

    transaction.status = to_status

    db.add(
        TransactionEvent(
            transaction_id=transaction.id,
            actor_account_id=account.id,
            event_type=event_type,
            from_status=current_status,
            to_status=to_status,
            details=details or {},
        )
    )
    db.flush()

    return transaction


def get_transaction(
    db: Session,
    account: UserAccount,
    transaction_id: int,
) -> RealEstateTransaction:
    transaction = db.get(RealEstateTransaction, transaction_id)
    if transaction is None:
        raise LookupError("Transaction not found.")

    authorize_transaction_access(db, transaction, account)
    return transaction
