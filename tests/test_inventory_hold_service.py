from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.database import SessionLocal
from backend.app.inventory_hold_service import (
    create_inventory_hold,
    expire_inventory_holds,
    release_inventory_hold,
)
from backend.app.models import (
    Booking,
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



def test_buyer_can_release_hold_and_replay_is_idempotent(client):
    with SessionLocal() as db:
        buyer, _, _, _, transaction = _seed_hold_records(db)
        db.commit()

        hold = create_inventory_hold(
            db, transaction_id=transaction.id, buyer_account_id=buyer.id
        )
        db.commit()

        released = release_inventory_hold(
            db,
            transaction_id=transaction.id,
            hold_id=hold.id,
            buyer_account_id=buyer.id,
        )
        db.commit()

        assert released.status == "released"

        replay = release_inventory_hold(
            db,
            transaction_id=transaction.id,
            hold_id=hold.id,
            buyer_account_id=buyer.id,
        )
        assert replay.id == hold.id
        assert replay.status == "released"

        events = db.scalars(
            select(TransactionEvent).where(
                TransactionEvent.transaction_id == transaction.id,
                TransactionEvent.event_type == "inventory_hold_released",
            )
        ).all()
        assert len(events) == 1


def test_non_owner_cannot_release_inventory_hold(client):
    with SessionLocal() as db:
        buyer, other, _, _, transaction = _seed_hold_records(
            db, second_buyer=True
        )
        db.commit()

        hold = create_inventory_hold(
            db, transaction_id=transaction.id, buyer_account_id=buyer.id
        )
        db.commit()

        with pytest.raises(PermissionError, match="transaction buyer"):
            release_inventory_hold(
                db,
                transaction_id=transaction.id,
                hold_id=hold.id,
                buyer_account_id=other.id,
            )


def test_release_rejects_hold_from_another_inventory(client):
    with SessionLocal() as db:
        buyer, _, _, inventory, transaction = _seed_hold_records(db)
        other_listing = PropertyListing(
            title="Different Hold Property",
            property_type="apartment",
            city="Test City",
            state="Test State",
            price_inr=125000,
            inventory_mode="single",
            availability_status="available",
            is_published=True,
        )
        db.add(other_listing)
        db.flush()
        other_inventory = PropertyInventoryItem(
            property_listing_id=other_listing.id,
            item_type="single",
            price_inr=125000,
            availability_status="available",
        )
        db.add(other_inventory)
        db.flush()
        unrelated_hold = InventoryHold(
            inventory_item_id=other_inventory.id,
            buyer_account_id=buyer.id,
            status="active",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(minutes=30),
        )
        db.add(unrelated_hold)
        db.commit()

        with pytest.raises(ValueError, match="does not belong"):
            release_inventory_hold(
                db,
                transaction_id=transaction.id,
                hold_id=unrelated_hold.id,
                buyer_account_id=buyer.id,
            )


def test_expiry_worker_expires_only_overdue_active_holds(client):
    with SessionLocal() as db:
        buyer, other, _, inventory, transaction = _seed_hold_records(
            db, second_buyer=True
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        overdue = InventoryHold(
            inventory_item_id=inventory.id,
            buyer_account_id=buyer.id,
            status="active",
            expires_at=now - timedelta(minutes=5),
        )
        still_active = InventoryHold(
            inventory_item_id=inventory.id + 100000,
            buyer_account_id=other.id,
            status="active",
            expires_at=now + timedelta(minutes=30),
        )
        db.add(overdue)
        db.flush()

        pending_booking = Booking(
            transaction_id=transaction.id,
            inventory_item_id=inventory.id,
            inventory_hold_id=overdue.id,
            buyer_account_id=buyer.id,
            agreed_amount=1000,
            currency="INR",
            status="awaiting_payment",
            expires_at=overdue.expires_at,
        )
        db.add(pending_booking)
        db.flush()

        # Use a separate valid inventory item for the future hold.
        listing = PropertyListing(
            title="Future Hold Property",
            property_type="apartment",
            city="Test City",
            state="Test State",
            price_inr=125000,
            inventory_mode="single",
            availability_status="available",
            is_published=True,
        )
        db.add(listing)
        db.flush()
        future_inventory = PropertyInventoryItem(
            property_listing_id=listing.id,
            item_type="single",
            price_inr=125000,
            availability_status="available",
        )
        db.add(future_inventory)
        db.flush()
        still_active.inventory_item_id = future_inventory.id
        db.add(still_active)
        db.commit()

        count = expire_inventory_holds(db, batch_size=10)
        db.commit()

        db.refresh(overdue)
        db.refresh(pending_booking)
        db.refresh(still_active)
        assert count == 1
        assert overdue.status == "expired"
        assert pending_booking.status == "expired"
        assert still_active.status == "active"

        assert expire_inventory_holds(db, batch_size=10) == 0


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_expiry_worker_rejects_invalid_batch_size(batch_size):
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="positive integer"):
            expire_inventory_holds(db, batch_size=batch_size)
