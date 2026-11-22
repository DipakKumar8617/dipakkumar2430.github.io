from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from backend.app.models import (
    PaymentAttempt, PaymentLedgerEntry,
    PaymentReconciliationBatch, PaymentReconciliationItem,
)
from backend.app.payment_service import (
    reconcile_provider_statement,
    record_verified_payment_adjustment,
)
from backend.app.refund_service import get_refundable_balance


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    tables = [
        PaymentAttempt.__table__,
        PaymentLedgerEntry.__table__,
        PaymentReconciliationBatch.__table__,
        PaymentReconciliationItem.__table__,
    ]
    PaymentAttempt.metadata.create_all(engine, tables=tables)
    session = Session(engine)
    payment = PaymentAttempt(
        booking_id=1, buyer_account_id=1, provider="razorpay",
        idempotency_key="recon-payment-key", amount=Decimal("100.00"),
        currency="INR", status="captured", provider_payment_id="pay_recon_1",
    )
    session.add(payment)
    session.flush()
    session.add(PaymentLedgerEntry(
        payment_attempt_id=payment.id, provider="razorpay",
        provider_transaction_id="capture_recon_1", entry_type="capture",
        amount=Decimal("100.00"), currency="INR",
    ))
    session.commit()
    yield session, payment
    session.close()
    engine.dispose()


def run_reconciliation(db, key, lines):
    session, _ = db
    return reconcile_provider_statement(
        session, provider="razorpay",
        period_start=datetime(2000, 1, 1),
        period_end=datetime(2100, 1, 1),
        statement_lines=lines, idempotency_key=key,
    )


def test_reconciliation_is_idempotent_for_same_key_and_content(db):
    lines = [{
        "provider_transaction_id": "capture_recon_1",
        "amount": "100.00", "currency": "INR",
    }]
    first = run_reconciliation(db, "statement-001", lines)
    db[0].commit()
    second = run_reconciliation(db, "statement-001", lines)
    assert first.id == second.id
    assert db[0].scalar(
        select(func.count(PaymentReconciliationBatch.id))
    ) == 1


def test_reconciliation_rejects_same_key_with_changed_content(db):
    run_reconciliation(db, "statement-002", [{
        "provider_transaction_id": "capture_recon_1",
        "amount": "100.00", "currency": "INR",
    }])
    db[0].commit()
    with pytest.raises(ValueError, match="different statement content"):
        run_reconciliation(db, "statement-002", [{
            "provider_transaction_id": "capture_recon_1",
            "amount": "99.00", "currency": "INR",
        }])


def test_reconciliation_records_all_mismatch_categories(db):
    session, payment = db

    # Provider reports this transaction in USD; internal ledger records INR.
    session.add(PaymentLedgerEntry(
        payment_attempt_id=payment.id,
        provider="razorpay",
        provider_transaction_id="currency_bad",
        entry_type="capture",
        amount=Decimal("10.00"),
        currency="INR",
    ))

    # Internal ledger has this transaction, but the provider statement omits it.
    session.add(PaymentLedgerEntry(
        payment_attempt_id=payment.id,
        provider="razorpay",
        provider_transaction_id="internal_only",
        entry_type="capture",
        amount=Decimal("15.00"),
        currency="INR",
    ))
    session.flush()

    batch = run_reconciliation(db, "statement-003", [
        {"provider_transaction_id": "capture_recon_1",
         "amount": "99.00", "currency": "INR"},
        {"provider_transaction_id": "provider_only",
         "amount": "20.00", "currency": "INR"},
        {"provider_transaction_id": "currency_bad",
         "amount": "10.00", "currency": "USD"},
    ])
    session.commit()

    outcomes = session.scalars(
        select(PaymentReconciliationItem.outcome).where(
            PaymentReconciliationItem.batch_id == batch.id
        )
    ).all()

    assert sorted(outcomes) == sorted([
        "amount_mismatch",
        "currency_mismatch",
        "missing_internal",
        "missing_provider",
    ])


def test_duplicate_statement_transaction_ids_are_rejected(db):
    line = {"provider_transaction_id": "duplicate",
            "amount": "1.00", "currency": "INR"}
    with pytest.raises(ValueError, match="duplicate transaction IDs"):
        run_reconciliation(db, "statement-004", [line, line])


def test_adjustment_is_idempotent_and_reduces_refundable_balance(db):
    session, payment = db
    first = record_verified_payment_adjustment(
        session, payment.id, provider_transaction_id="rev_001",
        entry_type="reversal", amount="30.00", currency="INR",
    )
    second = record_verified_payment_adjustment(
        session, payment.id, provider_transaction_id="rev_001",
        entry_type="reversal", amount="30.00", currency="INR",
    )
    assert first.id == second.id
    assert get_refundable_balance(session, payment) == Decimal("70.00")


def test_adjustment_cannot_exceed_remaining_captured_balance(db):
    session, payment = db
    record_verified_payment_adjustment(
        session, payment.id, provider_transaction_id="chargeback_001",
        entry_type="chargeback", amount="80.00", currency="INR",
    )
    session.commit()
    with pytest.raises(ValueError, match="remaining captured balance"):
        record_verified_payment_adjustment(
            session, payment.id, provider_transaction_id="rev_002",
            entry_type="reversal", amount="20.01", currency="INR",
        )


def test_adjustment_rejects_provider_id_conflict(db):
    session, payment = db
    record_verified_payment_adjustment(
        session, payment.id, provider_transaction_id="same_provider_id",
        entry_type="reversal", amount="10.00", currency="INR",
    )
    with pytest.raises(ValueError, match="conflicts"):
        record_verified_payment_adjustment(
            session, payment.id, provider_transaction_id="same_provider_id",
            entry_type="chargeback", amount="10.00", currency="INR",
        )
