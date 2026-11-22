from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.booking_service import (
    create_booking,
    confirm_booking_after_capture,
)


def future():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30)


def make_transaction(**overrides):
    values = {
        "id": 10,
        "buyer_account_id": 1,
        "inventory_item_id": 20,
        "property_listing_id": 30,
        "transaction_type": "direct_booking",
        "status": "terms_agreed",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_hold(**overrides):
    values = {
        "id": 40,
        "buyer_account_id": 1,
        "inventory_item_id": 20,
        "status": "active",
        "expires_at": future(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_inventory(**overrides):
    values = {
        "id": 20,
        "property_listing_id": 30,
        "availability_status": "available",
        "price_inr": 125000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_create_direct_booking_from_valid_hold():
    db = MagicMock()
    transaction = make_transaction()
    hold = make_hold()
    inventory = make_inventory()

    db.scalar.side_effect = [transaction, None, hold, inventory]

    booking = create_booking(
        db,
        transaction_id=10,
        buyer_account_id=1,
        inventory_hold_id=40,
    )

    assert booking.transaction_id == 10
    assert booking.inventory_item_id == 20
    assert booking.agreed_amount == Decimal("125000.00")
    assert booking.currency == "INR"
    assert booking.status == "awaiting_payment"
    assert transaction.status == "booking_pending"
    assert db.flush.called


def test_create_booking_rejects_another_buyers_transaction():
    db = MagicMock()
    db.scalar.return_value = make_transaction(buyer_account_id=99)

    with pytest.raises(PermissionError, match="transaction buyer"):
        create_booking(
            db,
            transaction_id=10,
            buyer_account_id=1,
            inventory_hold_id=40,
        )

    db.add.assert_not_called()


def test_create_booking_rejects_expired_hold():
    db = MagicMock()
    db.scalar.side_effect = [
        make_transaction(),
        None,
        make_hold(expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)),
    ]

    with pytest.raises(ValueError, match="expired"):
        create_booking(
            db,
            transaction_id=10,
            buyer_account_id=1,
            inventory_hold_id=40,
        )

    db.add.assert_not_called()


def test_create_booking_rejects_inventory_mismatch():
    db = MagicMock()
    db.scalar.side_effect = [
        make_transaction(),
        None,
        make_hold(inventory_item_id=999),
    ]

    with pytest.raises(ValueError, match="does not match"):
        create_booking(
            db,
            transaction_id=10,
            buyer_account_id=1,
            inventory_hold_id=40,
        )

    db.add.assert_not_called()


def test_confirm_booking_after_verified_capture():
    db = MagicMock()
    booking = SimpleNamespace(
        id=50,
        transaction_id=10,
        inventory_item_id=20,
        inventory_hold_id=40,
        buyer_account_id=1,
        agreed_amount=Decimal("125000.00"),
        currency="INR",
        status="awaiting_payment",
        expires_at=future(),
    )
    payment = SimpleNamespace(
        id=60,
        booking_id=50,
        status="captured",
        amount=Decimal("125000.00"),
        currency="INR",
    )
    transaction = make_transaction(status="booking_pending")
    hold = make_hold()
    inventory = make_inventory()

    db.scalar.side_effect = [booking, payment, transaction, hold, inventory]

    result = confirm_booking_after_capture(
        db,
        booking_id=50,
        payment_attempt_id=60,
    )

    assert result.status == "confirmed"
    assert transaction.status == "booked"
    assert hold.status == "converted"
    assert inventory.availability_status == "reserved"
    assert db.flush.called


def test_confirmation_rejects_uncaptured_payment():
    db = MagicMock()
    booking = SimpleNamespace(
        id=50,
        status="awaiting_payment",
        expires_at=future(),
        agreed_amount=Decimal("125000.00"),
        currency="INR",
    )
    payment = SimpleNamespace(
        id=60,
        booking_id=50,
        status="pending",
        amount=Decimal("125000.00"),
        currency="INR",
    )
    db.scalar.side_effect = [booking, payment]

    with pytest.raises(ValueError, match="captured"):
        confirm_booking_after_capture(
            db,
            booking_id=50,
            payment_attempt_id=60,
        )

    db.flush.assert_not_called()


def test_confirmed_booking_replay_requires_same_payment_attempt():
    db = MagicMock()
    booking = SimpleNamespace(
        id=50,
        transaction_id=10,
        status="confirmed",
        agreed_amount=Decimal("125000.00"),
        currency="INR",
    )
    payment = SimpleNamespace(
        id=60,
        booking_id=50,
        status="captured",
        amount=Decimal("125000.00"),
        currency="INR",
        failure_code="booking_confirmation_failed",
    )
    event = SimpleNamespace(details={"payment_attempt_id": 60})
    db.scalar.side_effect = [booking, payment, event]

    result = confirm_booking_after_capture(
        db,
        booking_id=50,
        payment_attempt_id=60,
    )

    assert result is booking
    assert payment.failure_code is None
    db.add.assert_not_called()


def test_confirmed_booking_rejects_a_different_payment_attempt():
    db = MagicMock()
    booking = SimpleNamespace(
        id=50,
        transaction_id=10,
        status="confirmed",
        agreed_amount=Decimal("125000.00"),
        currency="INR",
    )
    payment = SimpleNamespace(
        id=61,
        booking_id=50,
        status="captured",
        amount=Decimal("125000.00"),
        currency="INR",
        failure_code=None,
    )
    event = SimpleNamespace(details={"payment_attempt_id": 60})
    db.scalar.side_effect = [booking, payment, event]

    with pytest.raises(ValueError, match="different payment attempt"):
        confirm_booking_after_capture(
            db,
            booking_id=50,
            payment_attempt_id=61,
        )

    db.add.assert_not_called()
