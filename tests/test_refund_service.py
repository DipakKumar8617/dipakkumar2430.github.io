from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app import refund_service


class FakeDB:
    def __init__(self, scalar_results, rows=None):
        self.scalar_results = list(scalar_results)
        self.rows = rows or []
        self.added = []
        self.flush_count = 0

    def scalar(self, statement):
        return self.scalar_results.pop(0)

    def scalars(self, statement):
        return SimpleNamespace(all=lambda: self.rows)

    def execute(self, statement):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: self.rows)
        )

    def add(self, item):
        self.added.append(item)

    def flush(self):
        self.flush_count += 1


def payment():
    return SimpleNamespace(
        id=17,
        provider="razorpay",
        currency="INR",
        status="captured",
    )


def test_refund_request_rejects_amount_above_remaining_balance(monkeypatch):
    monkeypatch.setattr(
        refund_service,
        "get_refundable_balance",
        lambda db, payment: Decimal("100.00"),
    )
    db = FakeDB([payment(), None])

    with pytest.raises(ValueError):
        refund_service.create_refund_request(
            db,
            payment_attempt_id=17,
            amount=Decimal("100.01"),
            idempotency_key="refund-over-balance",
        )

    assert db.added == []
    assert db.flush_count == 0


def test_refund_request_reuses_existing_idempotent_request():
    existing = SimpleNamespace(
        id=91,
        payment_attempt_id=17,
        amount=Decimal("25.00"),
        currency="INR",
        provider="razorpay",
        idempotency_key="refund-retry-1",
        status="unknown",
    )
    db = FakeDB([payment(), existing])

    result = refund_service.create_refund_request(
        db,
        payment_attempt_id=17,
        amount=Decimal("25.00"),
        idempotency_key="refund-retry-1",
    )

    assert result is existing
    assert db.added == []
    assert db.flush_count == 0


def test_refund_request_rejects_idempotency_key_reused_for_different_amount():
    existing = SimpleNamespace(
        id=91,
        payment_attempt_id=17,
        amount=Decimal("25.00"),
        currency="INR",
        provider="razorpay",
        idempotency_key="refund-retry-1",
        status="unknown",
    )
    db = FakeDB([payment(), existing])

    with pytest.raises(ValueError):
        refund_service.create_refund_request(
            db,
            payment_attempt_id=17,
            amount=Decimal("30.00"),
            idempotency_key="refund-retry-1",
        )

    assert db.added == []
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app import refund_service


def refund_record(status="pending", provider_refund_id=None):
    return SimpleNamespace(
        id=91,
        payment_attempt_id=17,
        provider="razorpay",
        provider_refund_id=provider_refund_id,
        amount=Decimal("25.00"),
        currency="INR",
        status=status,
        failure_code="previous_error",
    )


def captured_payment():
    return SimpleNamespace(
        id=17,
        provider="razorpay",
        provider_payment_id="pay_123",
        currency="INR",
    )


def test_confirmed_refund_records_ledger_and_marks_processed(monkeypatch):
    refund = refund_record()
    calls = []

    def fake_record_verified_refund(db, payment_attempt_id, **kwargs):
        calls.append({"payment_attempt_id": payment_attempt_id, **kwargs})
        return SimpleNamespace(id=501)

    monkeypatch.setattr(
        refund_service,
        "record_verified_refund",
        fake_record_verified_refund,
    )
    db = FakeDB([refund.payment_attempt_id, captured_payment(), refund])

    result = refund_service.record_provider_confirmed_refund(
        db,
        91,
        provider_refund_id="rfnd_123",
        provider_payment_id="pay_123",
        amount=Decimal("25.00"),
        currency="INR",
    )

    assert result is refund
    assert refund.status == "processed"
    assert refund.provider_refund_id == "rfnd_123"
    assert refund.failure_code is None
    assert len(calls) == 1
    assert calls[0]["provider_transaction_id"] == "rfnd_123"
    assert calls[0]["amount"] == Decimal("25.00")


def test_processed_refund_is_idempotent(monkeypatch):
    refund = refund_record(
        status="processed",
        provider_refund_id="rfnd_123",
    )
    calls = []

    monkeypatch.setattr(
        refund_service,
        "record_verified_refund",
        lambda *args, **kwargs: calls.append(kwargs),
    )
    db = FakeDB([refund.payment_attempt_id, captured_payment(), refund])

    result = refund_service.record_provider_confirmed_refund(
        db,
        91,
        provider_refund_id="rfnd_123",
        provider_payment_id="pay_123",
        amount=Decimal("25.00"),
        currency="INR",
    )

    assert result is refund
    assert calls == []


@pytest.mark.parametrize(
    ("provider_payment_id", "amount", "currency"),
    [
        ("pay_wrong", Decimal("25.00"), "INR"),
        ("pay_123", Decimal("25.01"), "INR"),
        ("pay_123", Decimal("25.00"), "USD"),
    ],
)
def test_confirmed_refund_rejects_mismatched_provider_details(
    monkeypatch, provider_payment_id, amount, currency
):
    refund = refund_record()
    calls = []

    monkeypatch.setattr(
        refund_service,
        "record_verified_refund",
        lambda *args, **kwargs: calls.append(kwargs),
    )
    db = FakeDB([refund.payment_attempt_id, captured_payment(), refund])

    with pytest.raises(ValueError):
        refund_service.record_provider_confirmed_refund(
            db,
            91,
            provider_refund_id="rfnd_123",
            provider_payment_id=provider_payment_id,
            amount=amount,
            currency=currency,
        )

    assert calls == []
    assert refund.status == "pending"
