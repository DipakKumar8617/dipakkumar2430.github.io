from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from backend.app.models import Base, PaymentAttempt, PaymentLedgerEntry
from backend.app.payment_service import record_verified_refund


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            PaymentAttempt.__table__,
            PaymentLedgerEntry.__table__,
        ],
    )

    session = Session(engine)
    payment = PaymentAttempt(
        booking_id=1,
        buyer_account_id=1,
        provider="razorpay",
        idempotency_key="capture-test-key",
        amount=Decimal("100.00"),
        currency="INR",
        status="captured",
        provider_payment_id="pay_test_123",
    )
    session.add(payment)
    session.flush()

    session.add(
        PaymentLedgerEntry(
            payment_attempt_id=payment.id,
            provider="razorpay",
            provider_transaction_id="capture_test_123",
            entry_type="capture",
            amount=Decimal("100.00"),
            currency="INR",
        )
    )
    session.commit()

    yield session, payment

    session.close()
    engine.dispose()


def refund(db, payment, provider_id, amount):
    return record_verified_refund(
        db,
        payment.id,
        provider_transaction_id=provider_id,
        amount=Decimal(amount),
        currency="INR",
    )


def test_verified_refund_creates_ledger_entry(db):
    session, payment = db

    entry = refund(session, payment, "rfnd_test_1", "30.00")

    assert entry.entry_type == "refund"
    assert entry.amount == Decimal("30.00")
    assert entry.currency == "INR"

    count = session.scalar(
        select(func.count(PaymentLedgerEntry.id)).where(
            PaymentLedgerEntry.payment_attempt_id == payment.id,
            PaymentLedgerEntry.entry_type == "refund",
        )
    )
    assert count == 1
    assert payment.status == "partially_refunded"


def test_duplicate_provider_refund_id_does_not_duplicate_ledger(db):
    session, payment = db

    first = refund(session, payment, "rfnd_duplicate_1", "25.00")
    second = refund(session, payment, "rfnd_duplicate_1", "25.00")

    assert first.id == second.id

    count = session.scalar(
        select(func.count(PaymentLedgerEntry.id)).where(
            PaymentLedgerEntry.payment_attempt_id == payment.id,
            PaymentLedgerEntry.entry_type == "refund",
        )
    )
    assert count == 1


def test_cumulative_refunds_cannot_exceed_capture(db):
    session, payment = db

    refund(session, payment, "rfnd_limit_1", "70.00")
    session.commit()

    with pytest.raises(ValueError):
        refund(session, payment, "rfnd_limit_2", "30.01")

    session.rollback()

    total_refunds = session.scalar(
        select(func.coalesce(func.sum(PaymentLedgerEntry.amount), 0)).where(
            PaymentLedgerEntry.payment_attempt_id == payment.id,
            PaymentLedgerEntry.entry_type == "refund",
        )
    )
    assert total_refunds == Decimal("70.00")


def test_exact_remaining_balance_can_be_refunded(db):
    session, payment = db

    refund(session, payment, "rfnd_exact_1", "60.00")
    refund(session, payment, "rfnd_exact_2", "40.00")

    assert payment.status == "refunded"

    total_refunds = session.scalar(
        select(func.sum(PaymentLedgerEntry.amount)).where(
            PaymentLedgerEntry.payment_attempt_id == payment.id,
            PaymentLedgerEntry.entry_type == "refund",
        )
    )
    assert total_refunds == Decimal("100.00")
