from decimal import Decimal

import pytest

from backend.app.razorpay_gateway import (
    RazorpayGateway,
    RazorpayVerificationError,
)


def make_gateway(monkeypatch, payload):
    gateway = RazorpayGateway.__new__(RazorpayGateway)
    monkeypatch.setattr(
        gateway,
        "fetch_refund",
        lambda refund_id: payload,
    )
    return gateway


@pytest.mark.parametrize("status", ["pending", "processed", "failed"])
def test_verify_refund_accepts_valid_provider_states(monkeypatch, status):
    payload = {
        "id": "rfnd_123",
        "payment_id": "pay_123",
        "amount": 12550,
        "currency": "INR",
        "status": status,
    }
    gateway = make_gateway(monkeypatch, payload)

    result = gateway.verify_refund(
        refund_id="rfnd_123",
        expected_payment_id="pay_123",
        expected_amount=Decimal("125.50"),
        expected_currency="INR",
    )

    assert result == {
        "id": "rfnd_123",
        "payment_id": "pay_123",
        "amount": Decimal("125.50"),
        "currency": "INR",
        "status": status,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "rfnd_wrong"),
        ("payment_id", "pay_wrong"),
        ("amount", 12549),
        ("currency", "USD"),
        ("status", "unknown"),
    ],
)
def test_verify_refund_rejects_mismatched_provider_data(
    monkeypatch, field, value
):
    payload = {
        "id": "rfnd_123",
        "payment_id": "pay_123",
        "amount": 12550,
        "currency": "INR",
        "status": "processed",
    }
    payload[field] = value
    gateway = make_gateway(monkeypatch, payload)

    with pytest.raises(RazorpayVerificationError):
        gateway.verify_refund(
            refund_id="rfnd_123",
            expected_payment_id="pay_123",
            expected_amount=Decimal("125.50"),
            expected_currency="INR",
        )


def test_verify_refund_rejects_missing_currency(monkeypatch):
    payload = {
        "id": "rfnd_123",
        "payment_id": "pay_123",
        "amount": 12550,
        "status": "processed",
    }
    gateway = make_gateway(monkeypatch, payload)

    with pytest.raises(RazorpayVerificationError):
        gateway.verify_refund(
            refund_id="rfnd_123",
            expected_payment_id="pay_123",
            expected_amount=Decimal("125.50"),
        )


def test_verify_refund_uses_fetched_refund_id(monkeypatch):
    payload = {
        "id": "rfnd_other",
        "payment_id": "pay_123",
        "amount": 12550,
        "currency": "INR",
        "status": "processed",
    }
    gateway = make_gateway(monkeypatch, payload)

    with pytest.raises(RazorpayVerificationError):
        gateway.verify_refund(
            refund_id="rfnd_123",
            expected_payment_id="pay_123",
            expected_amount=Decimal("125.50"),
        )
