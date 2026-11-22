from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from backend.app.models import (
    Booking,
    CommissionLedgerEntry,
    PaymentAttempt,
    PaymentLedgerEntry,
    RealEstateTransaction,
)
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
        RealEstateTransaction.__table__,
        CommissionLedgerEntry.__table__,
        CommissionRule.__table__,
    ]
    Booking.metadata.create_all(engine, tables=tables)
    session = Session(engine)

    transaction = RealEstateTransaction(
        id=10,
        buyer_account_id=40,
        property_listing_id=1,
        inventory_item_id=20,
        transaction_type="direct_booking",
        status="booking_pending",
    )
    session.add(transaction)
    session.flush()

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


# Commission lifecycle coverage uses the existing isolated SQLite fixture.
from datetime import datetime
from decimal import Decimal

from backend.app.models import CommissionRule
from backend.app.commission_service import (
    accrue_verified_capture,
    reverse_verified_refund,
    review_commission,
)


def _commission_capture_setup(session, booking, suffix):
    transaction = session.get(RealEstateTransaction, booking.transaction_id)
    assert transaction is not None

    # Simulate the immutable CP attribution snapshot captured on the transaction.
    transaction.channel_partner_assignment_id = 1
    transaction.channel_partner_account_id = 50

    payment = make_attempt(session, booking, f"commission-{suffix}")
    payment.status = "captured"

    session.add(
        CommissionRule(
            rate_bps=500,
            currency="INR",
            starts_at=datetime(2020, 1, 1),
            is_active=True,
            created_by_account_id=1,
        )
    )
    session.flush()

    capture = PaymentLedgerEntry(
        payment_attempt_id=payment.id,
        provider="razorpay",
        provider_transaction_id=f"commission-capture-{suffix}",
        entry_type="capture",
        amount=Decimal("100.00"),
        currency="INR",
    )
    session.add(capture)
    session.flush()
    return payment, capture


def test_commission_capture_and_refund_lifecycle(db):
    session, booking = db
    payment, capture = _commission_capture_setup(session, booking, "lifecycle")

    accrual = accrue_verified_capture(session, capture.id)
    assert accrual is not None
    assert accrual.channel_partner_account_id == 50
    assert accrual.entry_type == "accrual"
    assert accrual.basis_amount == Decimal("100.00")
    assert accrual.amount == Decimal("5.00")
    assert accrual.rate_bps == 500
    assert accrual.approval_status == "pending"

    # Replaying the same source capture must return the existing ledger row.
    replay = accrue_verified_capture(session, capture.id)
    assert replay.id == accrual.id
    assert (
        session.query(CommissionLedgerEntry)
        .filter_by(source_payment_ledger_entry_id=capture.id)
        .count()
        == 1
    )

    first_refund = PaymentLedgerEntry(
        payment_attempt_id=payment.id,
        provider="razorpay",
        provider_transaction_id="commission-refund-lifecycle-1",
        entry_type="refund",
        amount=Decimal("50.00"),
        currency="INR",
    )
    session.add(first_refund)
    session.flush()

    first_reversal = reverse_verified_refund(session, first_refund.id)
    assert first_reversal is not None
    assert first_reversal.entry_type == "reversal"
    assert first_reversal.original_accrual_id == accrual.id
    assert first_reversal.basis_amount == Decimal("50.00")
    assert first_reversal.amount == Decimal("2.50")

    # A second partial refund reverses only the additional commission.
    second_refund = PaymentLedgerEntry(
        payment_attempt_id=payment.id,
        provider="razorpay",
        provider_transaction_id="commission-refund-lifecycle-2",
        entry_type="refund",
        amount=Decimal("50.00"),
        currency="INR",
    )
    session.add(second_refund)
    session.flush()

    second_reversal = reverse_verified_refund(session, second_refund.id)
    assert second_reversal is not None
    assert second_reversal.amount == Decimal("2.50")

    # Replaying the second refund source cannot create another reversal.
    replayed_reversal = reverse_verified_refund(session, second_refund.id)
    assert replayed_reversal.id == second_reversal.id

    reversals = (
        session.query(CommissionLedgerEntry)
        .filter_by(original_accrual_id=accrual.id, entry_type="reversal")
        .all()
    )
    assert sum((row.amount for row in reversals), Decimal("0.00")) == Decimal("5.00")

    reviewed = review_commission(
        session,
        entry_id=accrual.id,
        admin_account_id=1,
        decision="approved",
        note="Verified commission",
    )
    assert reviewed.approval_status == "approved"

    session.refresh(first_reversal)
    session.refresh(second_reversal)
    assert first_reversal.approval_status == "approved"
    assert second_reversal.approval_status == "approved"
    session.rollback()


def test_commission_capture_without_cp_attribution_does_not_accrue(db):
    session, booking = db
    transaction = session.get(RealEstateTransaction, booking.transaction_id)
    assert transaction is not None

    transaction.channel_partner_assignment_id = None
    transaction.channel_partner_account_id = None

    payment = make_attempt(session, booking, "commission-no-cp")
    payment.status = "captured"

    capture = PaymentLedgerEntry(
        payment_attempt_id=payment.id,
        provider="razorpay",
        provider_transaction_id="commission-capture-no-cp",
        entry_type="capture",
        amount=Decimal("100.00"),
        currency="INR",
    )
    session.add(capture)
    session.flush()

    assert accrue_verified_capture(session, capture.id) is None
    assert (
        session.query(CommissionLedgerEntry)
        .filter_by(source_payment_ledger_entry_id=capture.id)
        .count()
        == 0
    )
    session.rollback()
