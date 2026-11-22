from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.database import SessionLocal
from backend.app.inventory_hold_service import create_inventory_hold
from backend.app.models import (
    InventoryHold,
    PropertyInventoryItem,
    PropertyListing,
    RealEstateTransaction,
    TransactionEvent,
    UserAccount,
)


def _seed_hold_records(db, *, availability="available", second_buyer=False):
    buyer = UserAccount(
        full_name="Hold Test Buyer",
        email="hold-buyer@example.test",
        role="buyer",
        is_active=True,
    )
    listing = PropertyListing(
        title="Hold Test Property",
        property_type="apartment",
        city="Test City",
        state="Test State",
        price_inr=125000,
        inventory_mode="single",
        availability_status="available",
        is_published=True,
    )
    db.add_all([buyer, listing])
    db.flush()

    inventory = PropertyInventoryItem(
        property_listing_id=listing.id,
        item_type="single",
        price_inr=125000,
        availability_status=availability,
    )
    db.add(inventory)
    db.flush()

    transaction = RealEstateTransaction(
        buyer_account_id=buyer.id,
        property_listing_id=listing.id,
        inventory_item_id=inventory.id,
        transaction_type="direct_booking",
        status="initiated",
    )
    db.add(transaction)

    other = None
    if second_buyer:
        other = UserAccount(
            full_name="Second Hold Buyer",
            email="hold-buyer-2@example.test",
            role="buyer",
            is_active=True,
        )
        db.add(other)
        db.flush()
    db.flush()
    return buyer, other, listing, inventory, transaction


def test_hold_creation_and_same_buyer_replay_are_idempotent(client):
    with SessionLocal() as db:
        buyer, _, _, inventory, transaction = _seed_hold_records(db)
        db.commit()

        hold = create_inventory_hold(
            db, transaction_id=transaction.id, buyer_account_id=buyer.id
        )
        db.commit()
        first_id = hold.id

        replay = create_inventory_hold(
            db, transaction_id=transaction.id, buyer_account_id=buyer.id
        )
        assert replay.id == first_id
        assert replay.status == "active"
        assert replay.expires_at > datetime.now(timezone.utc).replace(tzinfo=None)

        events = db.scalars(
            select(TransactionEvent).where(
                TransactionEvent.transaction_id == transaction.id,
                TransactionEvent.event_type == "inventory_hold_created",
            )
        ).all()
        assert len(events) == 1


def test_active_hold_rejects_a_different_buyer(client):
    with SessionLocal() as db:
        buyer, other, _, _, transaction = _seed_hold_records(
            db, second_buyer=True
        )
        db.commit()

        create_inventory_hold(
            db, transaction_id=transaction.id, buyer_account_id=buyer.id
        )
        db.commit()

        second_transaction = RealEstateTransaction(
            buyer_account_id=other.id,
            property_listing_id=transaction.property_listing_id,
            inventory_item_id=transaction.inventory_item_id,
            transaction_type="direct_booking",
            status="initiated",
        )
        db.add(second_transaction)
        db.commit()

        with pytest.raises(ValueError, match="currently held"):
            create_inventory_hold(
                db,
                transaction_id=second_transaction.id,
                buyer_account_id=other.id,
            )


def test_expired_hold_is_released_before_new_acquisition(client):
    with SessionLocal() as db:
        buyer, other, _, inventory, transaction = _seed_hold_records(
            db, second_buyer=True
        )
        stale = InventoryHold(
            inventory_item_id=inventory.id,
            buyer_account_id=other.id,
            status="active",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=1),
        )
        db.add(stale)
        db.commit()
        stale_id = stale.id

        new_hold = create_inventory_hold(
            db, transaction_id=transaction.id, buyer_account_id=buyer.id
        )
        db.commit()

        db.refresh(stale)
        assert stale.id == stale_id
        assert stale.status == "expired"
        assert new_hold.status == "active"
        assert new_hold.buyer_account_id == buyer.id


def test_unavailable_inventory_cannot_be_held(client):
    with SessionLocal() as db:
        buyer, _, _, _, transaction = _seed_hold_records(
            db, availability="reserved"
        )
        db.commit()

        with pytest.raises(ValueError, match="no longer available"):
            create_inventory_hold(
                db, transaction_id=transaction.id, buyer_account_id=buyer.id
            )


def test_non_owner_cannot_acquire_transaction_inventory_hold(client):
    with SessionLocal() as db:
        buyer, other, _, _, transaction = _seed_hold_records(
            db, second_buyer=True
        )
        db.commit()

        with pytest.raises(PermissionError, match="Only the transaction buyer"):
            create_inventory_hold(
                db, transaction_id=transaction.id, buyer_account_id=other.id
            )
