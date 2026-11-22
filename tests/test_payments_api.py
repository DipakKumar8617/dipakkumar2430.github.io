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


class RecoveryFakeDB:
    def __init__(self, results):
        self.results = iter(results)
        self.commits = 0
        self.rollbacks = 0
        self.refreshed = []

    def scalar(self, statement):
        return next(self.results)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, item):
        self.refreshed.append(item)


def recovery_refund_case(status="unknown", provider_refund_id=None):
    refund = SimpleNamespace(
        id=41, payment_attempt_id=7, amount=Decimal("25.00"),
        currency="INR", status=status,
        provider_refund_id=provider_refund_id,
    )
    payment = SimpleNamespace(
        id=7, provider="razorpay",
        provider_payment_id="pay_verified_123",
        status="captured", booking_id=12,
    )
    return refund, payment


def make_recovery_admin(monkeypatch):
    monkeypatch.setattr(
        payments, "resolve_session_account",
        lambda request, db: SimpleNamespace(role="admin"),
    )


def test_refund_recovery_does_not_resubmit_when_provider_match_missing(monkeypatch):
    from backend.app import refund_service

    refund, payment = recovery_refund_case()
    db = RecoveryFakeDB([refund, payment])
    make_recovery_admin(monkeypatch)
    calls = []

    monkeypatch.setattr(
        payments, "RazorpayGateway",
        lambda: SimpleNamespace(
            fetch_payment_refunds=lambda *args, **kwargs: []
        ),
    )
    def update(db, refund_id, **kwargs):
        calls.append((refund_id, kwargs))
        refund.status = kwargs["status"]

    monkeypatch.setattr(refund_service, "update_refund_submission", update)

    result = payments.recover_payment_refund(41, object(), db)

    assert result["status"] == "unknown"
    assert calls == [(
        41, {"status": "unknown", "failure_code": "provider_refund_not_found"}
    )]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_refund_recovery_rejects_multiple_provider_matches(monkeypatch):
    from backend.app import refund_service

    refund, payment = recovery_refund_case()
    db = RecoveryFakeDB([refund, payment])
    make_recovery_admin(monkeypatch)
    calls = []
    matches = [
        {"id": "rfnd_1", "receipt": "refund-41", "notes": {}},
        {"id": "rfnd_2", "receipt": "refund-41", "notes": {}},
    ]

    monkeypatch.setattr(
        payments, "RazorpayGateway",
        lambda: SimpleNamespace(
            fetch_payment_refunds=lambda *args, **kwargs: matches
        ),
    )
    monkeypatch.setattr(
        refund_service, "update_refund_submission",
        lambda db, refund_id, **kwargs: calls.append((refund_id, kwargs)),
    )

    with pytest.raises(HTTPException) as exc:
        payments.recover_payment_refund(41, object(), db)

    assert exc.value.status_code == 409
    assert calls == [(
        41, {
            "status": "unknown",
            "failure_code": "multiple_provider_refunds_found",
        }
    )]
    assert db.commits == 1
    assert db.rollbacks == 1


def test_refund_recovery_records_one_verified_provider_match(monkeypatch):
    from backend.app import refund_service

    refund, payment = recovery_refund_case()
    db = RecoveryFakeDB([refund, payment])
    make_recovery_admin(monkeypatch)
    verified = {
        "id": "rfnd_123",
        "payment_id": "pay_verified_123",
        "amount": Decimal("25.00"),
        "currency": "INR",
        "status": "processed",
    }
    gateway = SimpleNamespace(
        fetch_payment_refunds=lambda *args, **kwargs: [{
            "id": "rfnd_123",
            "receipt": "refund-41",
            "notes": {"internal_refund_id": "41"},
        }],
        verify_refund=lambda **kwargs: verified,
    )
    monkeypatch.setattr(payments, "RazorpayGateway", lambda: gateway)
    recorded = []

    def record(db, refund_id, **kwargs):
        recorded.append((refund_id, kwargs))
        refund.status = "processed"
        refund.provider_refund_id = kwargs["provider_refund_id"]

    monkeypatch.setattr(
        refund_service, "record_provider_confirmed_refund", record
    )

    result = payments.recover_payment_refund(41, object(), db)

    assert result["status"] == "processed"
    assert result["provider_refund_id"] == "rfnd_123"
    assert recorded == [(
        41, {
            "provider_refund_id": "rfnd_123",
            "provider_payment_id": "pay_verified_123",
            "amount": Decimal("25.00"),
            "currency": "INR",
        }
    )]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_refund_recovery_requires_admin(monkeypatch):
    monkeypatch.setattr(
        payments, "resolve_session_account",
        lambda request, db: SimpleNamespace(role="buyer"),
    )

    with pytest.raises(HTTPException) as exc:
        payments.recover_payment_refund(
            41, object(), RecoveryFakeDB([])
        )

    assert exc.value.status_code == 403


def test_captured_booking_recovery_retries_confirmation(monkeypatch):
    payment = SimpleNamespace(id=7, status="captured", booking_id=12)
    booking = SimpleNamespace(id=12, status="confirmed")
    db = RecoveryFakeDB([payment])
    make_recovery_admin(monkeypatch)
    calls = []

    monkeypatch.setattr(
        payments, "confirm_booking_after_capture",
        lambda db, **kwargs: (calls.append(kwargs), booking)[1],
    )

    result = payments.recover_captured_booking(7, object(), db)

    assert result["booking_id"] == 12
    assert result["booking_status"] == "confirmed"
    assert calls == [{"booking_id": 12, "payment_attempt_id": 7}]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_refund_recovery_does_not_assume_uniqueness_after_search_limit(monkeypatch):
    from backend.app import refund_service

    refund, payment = recovery_refund_case()
    db = RecoveryFakeDB([refund, payment])
    make_recovery_admin(monkeypatch)
    calls = []
    verification_calls = []

    def fetch(*args, **kwargs):
        calls.append(kwargs["skip"])
        return [
            {
                "id": f"rfnd_{kwargs['skip']}_{index}",
                "receipt": "refund-41",
                "notes": {},
            }
            for index in range(100)
        ]

    gateway = SimpleNamespace(
        fetch_payment_refunds=fetch,
        verify_refund=lambda **kwargs: verification_calls.append(kwargs),
    )
    monkeypatch.setattr(payments, "RazorpayGateway", lambda: gateway)

    def update(db, refund_id, **kwargs):
        refund.status = kwargs["status"]
        calls.append(kwargs["failure_code"])

    monkeypatch.setattr(
        refund_service, "update_refund_submission", update
    )

    result = payments.recover_payment_refund(41, object(), db)

    assert result["status"] == "unknown"
    assert calls[:10] == list(range(0, 1000, 100))
    assert calls[10] == "provider_refund_search_limit_reached"
    assert verification_calls == []
    assert db.commits == 1
