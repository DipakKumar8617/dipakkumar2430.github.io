from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import config
from .models import (
    Booking,
    InventoryHold,
    PropertyInventoryItem,
    RealEstateTransaction,
    TransactionEvent,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_inventory_hold(
    db: Session,
    *,
    transaction_id: int,
    buyer_account_id: int,
) -> InventoryHold:
    """Acquire an exclusive temporary hold for a transaction.

    Caller owns commit/rollback. The inventory row is the serialization
    point for competing acquisitions of the same inventory item.
    """
    now = _now()

    transaction = db.scalar(
        select(RealEstateTransaction)
        .where(RealEstateTransaction.id == transaction_id)
        .with_for_update()
    )
    if transaction is None:
        raise LookupError("Transaction not found.")

    if transaction.buyer_account_id != buyer_account_id:
        raise PermissionError("Only the transaction buyer can hold this inventory.")

    inventory = db.scalar(
        select(PropertyInventoryItem)
        .where(PropertyInventoryItem.id == transaction.inventory_item_id)
        .with_for_update()
    )
    if inventory is None:
        raise LookupError("Inventory item not found.")

    if inventory.property_listing_id != transaction.property_listing_id:
        raise ValueError("Transaction inventory does not match its property.")

    if inventory.availability_status != "available":
        raise ValueError("Inventory item is no longer available.")

    if transaction.status not in {
        "initiated", "under_review", "negotiating", "terms_agreed"
    }:
        raise ValueError("Transaction is not eligible for an inventory hold.")

    # The inventory row lock serializes this cleanup with other acquisitions
    # for the same item on databases that support SELECT FOR UPDATE.
    db.execute(
        update(InventoryHold)
        .where(
            InventoryHold.inventory_item_id == inventory.id,
            InventoryHold.status == "active",
            InventoryHold.expires_at <= now,
        )
        .values(status="expired")
        .execution_options(synchronize_session=False)
    )

    existing = db.scalar(
        select(InventoryHold)
        .where(
            InventoryHold.inventory_item_id == inventory.id,
            InventoryHold.status == "active",
        )
        .limit(1)
    )
    if existing is not None:
        if (
            existing.buyer_account_id == buyer_account_id
            and existing.expires_at > now
        ):
            return existing
        raise ValueError("Inventory item is currently held by another buyer.")

    hold = InventoryHold(
        inventory_item_id=inventory.id,
        buyer_account_id=buyer_account_id,
        status="active",
        expires_at=now + timedelta(
            minutes=config.INVENTORY_HOLD_DURATION_MINUTES
        ),
    )
    db.add(hold)
    db.flush()

    db.add(
        TransactionEvent(
            transaction_id=transaction.id,
            actor_account_id=buyer_account_id,
            event_type="inventory_hold_created",
            from_status=transaction.status,
            to_status=transaction.status,
            details={
                "inventory_item_id": inventory.id,
                "inventory_hold_id": hold.id,
                "expires_at": hold.expires_at.isoformat(),
            },
        )
    )
    db.flush()
    return hold



def release_inventory_hold(
    db: Session,
    *,
    transaction_id: int,
    hold_id: int,
    buyer_account_id: int,
) -> InventoryHold:
    """Release a buyer's hold, recording the action against its transaction."""
    now = _now()

    transaction = db.scalar(
        select(RealEstateTransaction)
        .where(RealEstateTransaction.id == transaction_id)
        .with_for_update()
    )
    if transaction is None:
        raise LookupError("Transaction not found.")

    if transaction.buyer_account_id != buyer_account_id:
        raise PermissionError("Only the transaction buyer can release this hold.")

    hold = db.scalar(
        select(InventoryHold)
        .where(InventoryHold.id == hold_id)
        .with_for_update()
    )
    if hold is None:
        raise LookupError("Inventory hold not found.")

    if hold.inventory_item_id != transaction.inventory_item_id:
        raise ValueError("Inventory hold does not belong to this transaction.")

    if hold.buyer_account_id != buyer_account_id:
        raise PermissionError("Inventory hold belongs to another buyer.")

    if hold.status in {"released", "expired"}:
        return hold

    if hold.status != "active":
        raise ValueError("Only active inventory holds can be released.")

    previous_status = hold.status
    expired = hold.expires_at <= now
    hold.status = "expired" if expired else "released"

    db.add(
        TransactionEvent(
            transaction_id=transaction.id,
            actor_account_id=buyer_account_id,
            event_type=(
                "inventory_hold_expired" if expired
                else "inventory_hold_released"
            ),
            from_status=transaction.status,
            to_status=transaction.status,
            details={
                "inventory_item_id": hold.inventory_item_id,
                "inventory_hold_id": hold.id,
                "previous_hold_status": previous_status,
                "hold_status": hold.status,
            },
        )
    )
    db.flush()
    return hold


def expire_inventory_holds(
    db: Session,
    *,
    batch_size: int = 100,
) -> int:
    """Expire overdue active holds in a bounded, repeatable batch.

    Caller owns commit/rollback. The conditional status check makes repeated
    runs safe and ensures a concurrently released or converted hold is not
    overwritten. This function deliberately does not attach transaction
    events because a hold is not associated with a transaction in the schema.
    """
    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ValueError("Batch size must be a positive integer.")
    if batch_size <= 0:
        raise ValueError("Batch size must be a positive integer.")

    now = _now()
    hold_ids = db.scalars(
        select(InventoryHold.id)
        .where(
            InventoryHold.status == "active",
            InventoryHold.expires_at <= now,
        )
        .order_by(InventoryHold.expires_at.asc(), InventoryHold.id.asc())
        .limit(batch_size)
    ).all()

    expired_count = 0
    for hold_id in hold_ids:
        # Match confirmation's booking -> hold lock order.
        booking = db.scalar(
            select(Booking)
            .where(
                Booking.inventory_hold_id == hold_id,
                Booking.status == "awaiting_payment",
            )
            .with_for_update()
        )

        hold = db.scalar(
            select(InventoryHold)
            .where(InventoryHold.id == hold_id)
            .with_for_update()
        )

        if (
            hold is not None
            and hold.status == "active"
            and hold.expires_at <= now
        ):
            hold.status = "expired"
            if (
                booking is not None
                and booking.status == "awaiting_payment"
                and booking.expires_at <= now
            ):
                booking.status = "expired"
            expired_count += 1

    db.flush()
    return expired_count
