from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app import payments


class FakeDB:
    def __init__(self, payment, existing_refund):
        self.results = iter([payment, existing_refund])

    def scalar(self, statement):
        return next(self.results)


def make_case(existing_payment_id=7, existing_amount="25.00",
              payment_currency="INR", existing_currency="INR"):
    payment = SimpleNamespace(
        id=7,
        provider="razorpay",
        provider_payment_id="pay_verified_123",
        currency=payment_currency,
    )
    existing = SimpleNamespace(
        id=41,
        payment_attempt_id=existing_payment_id,
        amount=Decimal(existing_amount),
        currency=existing_currency,
        status="processed",
        provider_refund_id="rfnd_123",
    )
    return payment, existing


def invoke_handler(monkeypatch, payment, existing, amount="25.00"):
    monkeypatch.setattr(
        payments,
        "resolve_session_account",
        lambda request, db: SimpleNamespace(role="admin"),
    )
    db = FakeDB(payment, existing)

    return payments.create_payment_refund(
        payment_attempt_id=7,
        payload=SimpleNamespace(amount=Decimal(amount)),
        request=object(),
        idempotency_key="refund-test-key",
        db=db,
    )


def test_refund_api_returns_existing_identical_idempotent_request(monkeypatch):
    payment, existing = make_case()

    result = invoke_handler(monkeypatch, payment, existing)

    assert result["id"] == 41
    assert result["status"] == "processed"
    assert "no duplicate provider request" in result["message"]


@pytest.mark.parametrize(
    "case",
    [
        {"existing_payment_id": 8},
        {"existing_amount": "30.00"},
        {"payment_currency": "USD", "existing_currency": "INR"},
    ],
    ids=["different-payment", "different-amount", "different-currency"],
)
def test_refund_api_rejects_conflicting_idempotency_key(monkeypatch, case):
    payment, existing = make_case(**case)

    with pytest.raises(HTTPException) as exc:
        invoke_handler(monkeypatch, payment, existing)

    assert exc.value.status_code == 409
    assert "Idempotency key" in exc.value.detail
