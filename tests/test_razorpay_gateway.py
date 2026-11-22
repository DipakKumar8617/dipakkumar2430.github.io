import hashlib
import hmac
from decimal import Decimal

import pytest

from backend.app.razorpay_gateway import (
    RazorpayConfig,
    RazorpayGateway,
    RazorpayVerificationError,
    _minor_units,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.is_error = status_code >= 400

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


@pytest.fixture
def config():
    return RazorpayConfig(
        key_id="test_key",
        key_secret="test_secret",
        webhook_secret="test_webhook_secret",
    )


def test_inr_amount_conversion():
    assert _minor_units(Decimal("1250.50"), "INR") == 125050


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "1.001"])
def test_rejects_invalid_amounts(amount):
    with pytest.raises(ValueError):
        _minor_units(amount, "INR")


def test_rejects_non_inr_currency():
    with pytest.raises(ValueError):
        _minor_units("100.00", "USD")


def test_checkout_signature_verification(config):
    gateway = RazorpayGateway(config)
    message = b"order_123|pay_456"
    signature = hmac.new(
        config.key_secret.encode(),
        message,
        hashlib.sha256,
    ).hexdigest()

    assert gateway.verify_checkout_signature(
        order_id="order_123",
        payment_id="pay_456",
        signature=signature,
    )
    assert not gateway.verify_checkout_signature(
        order_id="order_123",
        payment_id="pay_456",
        signature="0" * 64,
    )


def test_webhook_signature_uses_exact_raw_body(config):
    gateway = RazorpayGateway(config)
    body = b'{"event":"payment.captured","contains":"exact bytes"}'
    signature = hmac.new(
        config.webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    assert gateway.verify_webhook_signature(
        raw_body=body,
        signature=signature,
    )
    assert not gateway.verify_webhook_signature(
        raw_body=body + b" ",
        signature=signature,
    )


def test_create_order_sends_paise_and_returns_order(config):
    client = FakeClient([
        FakeResponse({
            "id": "order_123",
            "amount": 125050,
            "currency": "INR",
            "receipt": "realiq-payment-42",
            "status": "created",
        })
    ])
    gateway = RazorpayGateway(config, client=client)

    result = gateway.create_order(
        payment_attempt_id=42,
        amount=Decimal("1250.50"),
    )

    assert result["id"] == "order_123"
    assert result["amount"] == 125050
    method, url, kwargs = client.calls[0]
    assert method == "POST"
    assert url.endswith("/orders")
    assert kwargs["json"]["amount"] == 125050
    assert kwargs["json"]["currency"] == "INR"
    assert kwargs["auth"] == ("test_key", "test_secret")


def test_create_order_rejects_provider_amount_mismatch(config):
    client = FakeClient([
        FakeResponse({
            "id": "order_123",
            "amount": 100,
            "currency": "INR",
        })
    ])
    gateway = RazorpayGateway(config, client=client)

    with pytest.raises(RazorpayVerificationError):
        gateway.create_order(
            payment_attempt_id=42,
            amount=Decimal("1250.50"),
        )


def test_captured_payment_requires_provider_confirmation(config):
    client = FakeClient([
        FakeResponse({
            "id": "pay_123",
            "order_id": "order_123",
            "amount": 125050,
            "currency": "INR",
            "status": "captured",
            "captured": True,
        })
    ])
    gateway = RazorpayGateway(config, client=client)

    result = gateway.verify_captured_payment(
        payment_id="pay_123",
        expected_order_id="order_123",
        expected_amount=Decimal("1250.50"),
    )

    assert result["status"] == "captured"
    assert result["amount"] == 125050


@pytest.mark.parametrize(
    "provider_payment",
    [
        {
            "order_id": "another_order",
            "amount": 125050,
            "currency": "INR",
            "status": "captured",
            "captured": True,
        },
        {
            "order_id": "order_123",
            "amount": 100,
            "currency": "INR",
            "status": "captured",
            "captured": True,
        },
        {
            "order_id": "order_123",
            "amount": 125050,
            "currency": "INR",
            "status": "authorized",
            "captured": False,
        },
    ],
)
def test_rejects_unverified_capture_details(config, provider_payment):
    client = FakeClient([FakeResponse(provider_payment)])
    gateway = RazorpayGateway(config, client=client)

    with pytest.raises(RazorpayVerificationError):
        gateway.verify_captured_payment(
            payment_id="pay_123",
            expected_order_id="order_123",
            expected_amount=Decimal("1250.50"),
        )


def test_fetch_payment_refunds_uses_paginated_endpoint(monkeypatch):
    gateway = RazorpayGateway.__new__(RazorpayGateway)
    calls = []
    monkeypatch.setattr(
        gateway, "_request",
        lambda method, path: (
            calls.append((method, path)),
            {"items": [{"id": "rfnd_1"}]},
        )[1],
    )

    result = gateway.fetch_payment_refunds(
        "pay_123", count=50, skip=100
    )

    assert result == [{"id": "rfnd_1"}]
    assert calls == [(
        "GET", "/payments/pay_123/refunds?count=50&skip=100"
    )]


@pytest.mark.parametrize(
    ("count", "skip"),
    [(0, 0), (101, 0), (True, 0), (10, -1), (10, True)],
)
def test_fetch_payment_refunds_rejects_invalid_pagination(
    monkeypatch, count, skip
):
    gateway = RazorpayGateway.__new__(RazorpayGateway)
    calls = []
    monkeypatch.setattr(
        gateway, "_request",
        lambda *args: calls.append(args),
    )

    with pytest.raises(ValueError):
        gateway.fetch_payment_refunds(
            "pay_123", count=count, skip=skip
        )

    assert calls == []


def test_fetch_payment_refunds_rejects_invalid_collection(monkeypatch):
    gateway = RazorpayGateway.__new__(RazorpayGateway)
    monkeypatch.setattr(
        gateway, "_request",
        lambda *args: {"items": "not-a-list"},
    )

    with pytest.raises(RazorpayVerificationError):
        gateway.fetch_payment_refunds("pay_123")
