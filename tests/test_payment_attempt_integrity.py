from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from backend.app.models import Booking, PaymentAttempt, PaymentLedgerEntry
from backend.app.payment_service import (
    create_payment_attempt,
    record_verified_capture,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    tables = [
        Booking.__table__,
        PaymentAttempt.__table__,
        PaymentLedgerEntry.__table__,
    ]
    Booking.metadata.create_all(engine, tables=tables)
    session = Session(engine)

    booking = Booking(
        transaction_id=10,
        inventory_item_id=20,
        inventory_hold_id=30,
        buyer_account_id=40,
        agreed_amount=Decimal("100.00"),
        currency="INR",
        status="awaiting_payment",
        expires_at=(
            datetime.now(timezone.utc).replace(tzinfo=None)
            + timedelta(minutes=30)
        ),
    )
    session.add(booking)
    session.commit()

    yield session, booking

    session.close()
    engine.dispose()


def make_attempt(session, booking, key):
    return create_payment_attempt(
        session,
        booking_id=booking.id,
        buyer_account_id=booking.buyer_account_id,
        provider="razorpay",
        idempotency_key=key,
    )


def test_different_request_keys_reuse_one_attempt_for_booking(db):
    session, booking = db

    first = make_attempt(session, booking, "initial-request-key")
    session.commit()

    retry = make_attempt(session, booking, "new-client-retry-key")

    assert retry.id == first.id
    assert retry.idempotency_key == "initial-request-key"
    assert session.scalar(
        select(func.count(PaymentAttempt.id)).where(
            PaymentAttempt.booking_id == booking.id
        )
    ) == 1


def test_failed_attempt_is_reused_instead_of_creating_another_order(db):
    session, booking = db

    first = make_attempt(session, booking, "initial-request-key")
    first.status = "failed"
    session.commit()

    retry = make_attempt(session, booking, "retry-after-failure")

    assert retry.id == first.id
    assert retry.status == "failed"
    assert session.scalar(
        select(func.count(PaymentAttempt.id)).where(
            PaymentAttempt.booking_id == booking.id
        )
    ) == 1


def test_authoritatively_verified_capture_can_recover_failed_attempt(db):
    session, booking = db

    payment = make_attempt(session, booking, "initial-request-key")
    payment.status = "failed"
    session.commit()

    entry = record_verified_capture(
        session,
        payment.id,
        provider_transaction_id="pay_retry_success",
        provider_payment_id="pay_retry_success",
        amount=Decimal("100.00"),
        currency="INR",
    )
    session.commit()

    assert entry.entry_type == "capture"
    assert payment.status == "captured"
    assert payment.provider_payment_id == "pay_retry_success"


def test_second_distinct_capture_cannot_be_added_to_same_attempt(db):
    session, booking = db

    payment = make_attempt(session, booking, "initial-request-key")
    record_verified_capture(
        session,
        payment.id,
        provider_transaction_id="pay_first_success",
        provider_payment_id="pay_first_success",
        amount=Decimal("100.00"),
        currency="INR",
    )
    session.commit()

    with pytest.raises(ValueError, match="not eligible for capture"):
        record_verified_capture(
            session,
            payment.id,
            provider_transaction_id="pay_second_success",
            provider_payment_id="pay_second_success",
            amount=Decimal("100.00"),
            currency="INR",
        )
