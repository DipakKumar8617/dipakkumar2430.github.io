from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.app.database import SessionLocal
from backend.app.models import (
    Booking,
    InventoryHold,
    PaymentAttempt,
    PaymentLedgerEntry,
    PaymentRefund,
    PropertyInventoryItem,
    PropertyListing,
    RealEstateTransaction,
    TransactionEvent,
    UserAccount,
)
from backend.app.booking_service import (
    create_booking,
    confirm_booking_after_capture,
)
from backend.app.payment_service import (
    create_payment_attempt,
    record_verified_capture,
)
from backend.app.refund_service import (
    create_refund_request,
    get_refundable_balance,
    record_provider_confirmed_refund,
)


def create_lifecycle_records(db):
    buyer = UserAccount(
        full_name="Lifecycle Test Buyer",
        email="lifecycle-buyer@example.test",
        role="buyer",
        is_active=True,
    )
    listing = PropertyListing(
        title="Lifecycle Test Property",
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
        availability_status="available",
    )
    db.add(inventory)
    db.flush()

    hold = InventoryHold(
        inventory_item_id=inventory.id,
        buyer_account_id=buyer.id,
        status="active",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    transaction = RealEstateTransaction(
        buyer_account_id=buyer.id,
        property_listing_id=listing.id,
        inventory_item_id=inventory.id,
        transaction_type="direct_booking",
        status="terms_agreed",
    )

    db.add_all([hold, transaction])
    db.flush()

    return buyer, listing, inventory, hold, transaction


def test_complete_transaction_payment_and_refund_lifecycle(client):
    db = SessionLocal()

    try:
        buyer, listing, inventory, hold, transaction = (
            create_lifecycle_records(db)
        )
        db.commit()

        # 1. Create a booking from the active inventory hold.
        booking = create_booking(
            db,
            transaction_id=transaction.id,
            buyer_account_id=buyer.id,
            inventory_hold_id=hold.id,
        )
        db.commit()

        assert booking.status == "awaiting_payment"
        assert booking.agreed_amount == Decimal("125000.00")
        assert db.get(RealEstateTransaction, transaction.id).status == (
            "booking_pending"
        )

        # Booking creation is idempotent for the same active hold.
        replayed_booking = create_booking(
            db,
            transaction_id=transaction.id,
            buyer_account_id=buyer.id,
            inventory_hold_id=hold.id,
        )
        assert replayed_booking.id == booking.id
        db.commit()

        # 2. Create the payment attempt.
        payment = create_payment_attempt(
            db,
            booking_id=booking.id,
            buyer_account_id=buyer.id,
            provider="razorpay",
            idempotency_key="lifecycle-payment-key",
        )
        db.commit()

        assert payment.status == "created"
        assert payment.amount == Decimal("125000.00")

        # A retry with a different client key must still reuse the attempt.
        replayed_payment = create_payment_attempt(
            db,
            booking_id=booking.id,
            buyer_account_id=buyer.id,
            provider="razorpay",
            idempotency_key="lifecycle-payment-retry-key",
        )
        assert replayed_payment.id == payment.id
        db.commit()

        # 3. Record an independently verified capture and confirm the booking
        # in the same database transaction.
        capture = record_verified_capture(
            db,
            payment.id,
            provider_transaction_id="lifecycle-capture-001",
            amount=Decimal("125000.00"),
            currency="INR",
            provider_payment_id="pay_lifecycle_001",
        )

        confirmed_booking = confirm_booking_after_capture(
            db,
            booking_id=booking.id,
            payment_attempt_id=payment.id,
        )
        db.commit()

        assert capture.entry_type == "capture"
        assert confirmed_booking.status == "confirmed"
        assert db.get(PaymentAttempt, payment.id).status == "captured"
        assert db.get(InventoryHold, hold.id).status == "converted"
        assert db.get(PropertyInventoryItem, inventory.id).availability_status == (
            "reserved"
        )
        assert db.get(RealEstateTransaction, transaction.id).status == "booked"

        # 4. Confirmation replay using the same payment is safe.
        replayed_confirmation = confirm_booking_after_capture(
            db,
            booking_id=booking.id,
            payment_attempt_id=payment.id,
        )
        assert replayed_confirmation.status == "confirmed"
        db.commit()

        confirmation_events = db.scalars(
            select(TransactionEvent).where(
                TransactionEvent.transaction_id == transaction.id,
                TransactionEvent.event_type == "booking_payment_confirmed",
            )
        ).all()
        assert len(confirmation_events) == 1

        # 5. Request a partial refund and verify the reserved balance.
        refund = create_refund_request(
            db,
            payment_attempt_id=payment.id,
            amount=Decimal("25000.00"),
            idempotency_key="lifecycle-refund-key",
        )
        db.commit()

        assert refund.status == "requested"
        assert get_refundable_balance(
            db, db.get(PaymentAttempt, payment.id)
        ) == Decimal("125000.00")

        # 6. Record the refund only after provider confirmation.
        processed_refund = record_provider_confirmed_refund(
            db,
            refund.id,
            provider_refund_id="rfnd_lifecycle_001",
            provider_payment_id="pay_lifecycle_001",
            amount=Decimal("25000.00"),
            currency="INR",
        )
        db.commit()

        assert processed_refund.status == "processed"
        assert db.get(PaymentAttempt, payment.id).status == "partially_refunded"
        assert get_refundable_balance(
            db, db.get(PaymentAttempt, payment.id)
        ) == Decimal("100000.00")

        ledger = db.scalars(
            select(PaymentLedgerEntry)
            .where(PaymentLedgerEntry.payment_attempt_id == payment.id)
            .order_by(PaymentLedgerEntry.id)
        ).all()

        assert [(entry.entry_type, entry.amount) for entry in ledger] == [
            ("capture", Decimal("125000.00")),
            ("refund", Decimal("25000.00")),
        ]

        assert db.scalar(
            select(PaymentRefund).where(PaymentRefund.id == refund.id)
        ).provider_refund_id == "rfnd_lifecycle_001"

    finally:
        db.close()


def test_booking_cannot_be_confirmed_before_verified_capture(client):
    db = SessionLocal()

    try:
        buyer, listing, inventory, hold, transaction = (
            create_lifecycle_records(db)
        )
        db.commit()

        booking = create_booking(
            db,
            transaction_id=transaction.id,
            buyer_account_id=buyer.id,
            inventory_hold_id=hold.id,
        )
        db.commit()

        payment = create_payment_attempt(
            db,
            booking_id=booking.id,
            buyer_account_id=buyer.id,
            provider="razorpay",
            idempotency_key="uncaptured-payment-key",
        )
        db.commit()

        with pytest.raises(ValueError, match="captured"):
            confirm_booking_after_capture(
                db,
                booking_id=booking.id,
                payment_attempt_id=payment.id,
            )

        db.rollback()

        assert db.get(Booking, booking.id).status == "awaiting_payment"
        assert db.get(PaymentAttempt, payment.id).status == "created"
        assert db.get(InventoryHold, hold.id).status == "active"
        assert db.get(PropertyInventoryItem, inventory.id).availability_status == (
            "available"
        )
        assert db.get(RealEstateTransaction, transaction.id).status == (
            "booking_pending"
        )

    finally:
        db.close()


def test_refund_cannot_exceed_remaining_captured_balance(client):
    db = SessionLocal()

    try:
        buyer, listing, inventory, hold, transaction = (
            create_lifecycle_records(db)
        )
        db.commit()

        booking = create_booking(
            db,
            transaction_id=transaction.id,
            buyer_account_id=buyer.id,
            inventory_hold_id=hold.id,
        )
        db.commit()

        payment = create_payment_attempt(
            db,
            booking_id=booking.id,
            buyer_account_id=buyer.id,
            provider="razorpay",
            idempotency_key="refund-limit-payment-key",
        )
        db.commit()

        record_verified_capture(
            db,
            payment.id,
            provider_transaction_id="refund-limit-capture",
            amount=Decimal("125000.00"),
            currency="INR",
            provider_payment_id="pay_refund_limit",
        )
        db.commit()

        refund = create_refund_request(
            db,
            payment_attempt_id=payment.id,
            amount=Decimal("100000.00"),
            idempotency_key="refund-limit-first",
        )
        db.commit()

        with pytest.raises(ValueError, match="unreserved"):
            create_refund_request(
                db,
                payment_attempt_id=payment.id,
                amount=Decimal("25000.01"),
                idempotency_key="refund-limit-excess",
            )

        db.rollback()

        assert db.get(PaymentRefund, refund.id).status == "requested"
        assert get_refundable_balance(
            db, db.get(PaymentAttempt, payment.id)
        ) == Decimal("125000.00")

    finally:
        db.close()
