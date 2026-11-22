from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import config
from .models import (
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
