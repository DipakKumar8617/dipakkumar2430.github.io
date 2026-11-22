"""Provider-neutral payment, ledger, webhook, and reconciliation services.
import hashlib
import json

All functions flush but never commit. The API or job caller owns the
transaction boundary. Only trusted provider adapters should call functions
that record verified financial outcomes.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    Booking,
    PaymentAttempt,
    PaymentLedgerEntry,
    PaymentWebhookEvent,
    PaymentReconciliationBatch,
    PaymentReconciliationItem,
)


PAYMENT_STATUSES = {
    "created", "pending", "authorized", "captured",
    "failed", "cancelled", "partially_refunded", "refunded",
}

ALLOWED_TRANSITIONS = {
    "created": {"pending", "authorized", "failed", "cancelled"},
    "pending": {"authorized", "captured", "failed", "cancelled"},
    "authorized": {"captured", "failed", "cancelled"},
    "captured": {"partially_refunded", "refunded"},
    "partially_refunded": {"refunded"},
    "failed": set(),
    "cancelled": set(),
    "refunded": set(),
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid monetary amount.") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be a positive finite value.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Amount cannot have more than two decimal places.")
    return amount


def _currency(value):
    currency = str(value or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Currency must be a three-letter ISO code.")
    return currency


def _provider(value):
    provider = str(value or "").strip().lower()
    if not provider or len(provider) > 30:
        raise ValueError("A valid payment provider is required.")
    return provider


def _required_text(value, field, max_length):
    result = str(value or "").strip()
    if not result or len(result) > max_length:
        raise ValueError(f"{field} is required and must be at most {max_length} characters.")
    return result


def _locked_payment(db, payment_attempt_id):
    payment = db.execute(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == payment_attempt_id)
        .with_for_update()
    ).scalar_one_or_none()
    if payment is None:
        raise LookupError("Payment attempt not found.")
    return payment


def create_payment_attempt(
    db: Session,
    *,
    booking_id: int,
    buyer_account_id: int,
    provider: str,
    idempotency_key: str,
):
    """Create or return the same attempt for a repeated idempotency key."""
    provider = _provider(provider)
    key = _required_text(idempotency_key, "Idempotency key", 128)

    existing = db.execute(
        select(PaymentAttempt).where(
            PaymentAttempt.provider == provider,
            PaymentAttempt.idempotency_key == key,
        )
    ).scalar_one_or_none()

    if existing is not None:
        if (
            existing.booking_id != booking_id
            or existing.buyer_account_id != buyer_account_id
        ):
            raise ValueError("Idempotency key was already used for another payment.")
        return existing

    booking = db.execute(
        select(Booking)
        .where(Booking.id == booking_id)
        .with_for_update()
    ).scalar_one_or_none()

    if booking is None:
        raise LookupError("Booking not found.")
    if booking.buyer_account_id != buyer_account_id:
        raise PermissionError("Only the booking buyer can initiate its payment.")
    if booking.status != "awaiting_payment":
        raise ValueError("Booking is not awaiting payment.")
    if booking.expires_at <= _now():
        raise ValueError("Booking payment window has expired.")
    if booking.agreed_amount is None:
        raise ValueError("Booking has no agreed payment amount.")

    payment = PaymentAttempt(
        booking_id=booking.id,
        buyer_account_id=buyer_account_id,
        provider=provider,
        idempotency_key=key,
        amount=_money(booking.agreed_amount),
        currency=_currency(booking.currency),
        status="created",
    )
    db.add(payment)
    db.flush()
    return payment


def update_payment_status(
    db: Session,
    payment_attempt_id: int,
    new_status: str,
    *,
    failure_code: str | None = None,
):
    """Apply a permitted non-financial lifecycle transition."""
    payment = _locked_payment(db, payment_attempt_id)
    new_status = str(new_status or "").strip().lower()

    if new_status not in PAYMENT_STATUSES:
        raise ValueError("Unsupported payment status.")
    if new_status == payment.status:
        return payment
    if new_status not in ALLOWED_TRANSITIONS.get(payment.status, set()):
        raise ValueError(
            f"Payment cannot transition from {payment.status} to {new_status}."
        )

    if new_status in {"captured", "partially_refunded", "refunded"}:
        raise ValueError("Financial success states require verified provider ledger entries.")

    payment.status = new_status
    if failure_code is not None:
        payment.failure_code = _required_text(failure_code, "Failure code", 100)
    db.flush()
    return payment


def record_verified_capture(
    db: Session,
    payment_attempt_id: int,
    *,
    provider_transaction_id: str,
    amount,
    currency: str,
    provider_payment_id: str | None = None,
):
    """Record a full capture after authoritative provider verification."""
    payment = _locked_payment(db, payment_attempt_id)
    transaction_id = _required_text(provider_transaction_id, "Provider transaction ID", 128)
    amount = _money(amount)
    currency = _currency(currency)

    duplicate = db.execute(
        select(PaymentLedgerEntry).where(
            PaymentLedgerEntry.provider == payment.provider,
            PaymentLedgerEntry.provider_transaction_id == transaction_id,
        )
    ).scalar_one_or_none()

    if duplicate is not None:
        if (
            duplicate.payment_attempt_id != payment.id
            or duplicate.entry_type != "capture"
            or duplicate.amount != amount
            or duplicate.currency != currency
        ):
            raise ValueError("Provider transaction ID conflicts with an existing ledger entry.")
        return duplicate

    if payment.status not in {"created", "pending", "authorized"}:
        raise ValueError("Payment is not eligible for capture.")
    if amount != payment.amount or currency != payment.currency:
        raise ValueError("Capture amount or currency does not match the payment attempt.")

    entry = PaymentLedgerEntry(
        payment_attempt_id=payment.id,
        provider=payment.provider,
        provider_transaction_id=transaction_id,
        entry_type="capture",
        amount=amount,
        currency=currency,
    )
    db.add(entry)

    payment.status = "captured"
    if provider_payment_id:
        payment.provider_payment_id = _required_text(
            provider_payment_id, "Provider payment ID", 128
        )
    db.flush()
    return entry


def record_verified_refund(
    db: Session,
    payment_attempt_id: int,
    *,
    provider_transaction_id: str,
    amount,
    currency: str,
):
    """Append a provider-confirmed refund without modifying prior ledger entries."""
    payment = _locked_payment(db, payment_attempt_id)
    transaction_id = _required_text(provider_transaction_id, "Provider transaction ID", 128)
    amount = _money(amount)
    currency = _currency(currency)

    duplicate = db.execute(
        select(PaymentLedgerEntry).where(
            PaymentLedgerEntry.provider == payment.provider,
            PaymentLedgerEntry.provider_transaction_id == transaction_id,
        )
    ).scalar_one_or_none()

    if duplicate is not None:
        if (
            duplicate.payment_attempt_id != payment.id
            or duplicate.entry_type != "refund"
            or duplicate.amount != amount
            or duplicate.currency != currency
        ):
            raise ValueError("Provider transaction ID conflicts with an existing ledger entry.")
        return duplicate

    if payment.status not in {"captured", "partially_refunded"}:
        raise ValueError("Only captured payments can be refunded.")
    if currency != payment.currency:
        raise ValueError("Refund currency does not match the payment.")

    entries = db.execute(
        select(PaymentLedgerEntry).where(
            PaymentLedgerEntry.payment_attempt_id == payment.id
        )
    ).scalars().all()

    captured = sum(
        (entry.amount for entry in entries if entry.entry_type == "capture"),
        Decimal("0.00"),
    )
    deductions = sum(
        (
            entry.amount
            for entry in entries
            if entry.entry_type in {"refund", "reversal", "chargeback"}
        ),
        Decimal("0.00"),
    )
    remaining = captured - deductions

    if amount > remaining:
        raise ValueError("Refund exceeds the remaining captured balance.")

    entry = PaymentLedgerEntry(
        payment_attempt_id=payment.id,
        provider=payment.provider,
        provider_transaction_id=transaction_id,
        entry_type="refund",
        amount=amount,
        currency=currency,
    )
    db.add(entry)

    payment.status = (
        "refunded" if amount == remaining else "partially_refunded"
    )
    db.flush()
    return entry


def record_verified_webhook(
    db: Session,
    *,
    provider: str,
    provider_event_id: str,
    event_type: str,
    payload_hash: str,
    signature_verified: bool,
    metadata: dict | None = None,
):
    """Persist a verified webhook; tolerate concurrent duplicate inserts."""
    if signature_verified is not True:
        raise PermissionError("Unverified webhook events cannot enter the trusted event store.")

    provider = _provider(provider)
    event_id = _required_text(provider_event_id, "Provider event ID", 160)
    event_type = _required_text(event_type, "Event type", 120)
    digest = _required_text(payload_hash, "Payload hash", 64)

    def find_existing():
        return db.execute(
            select(PaymentWebhookEvent).where(
                PaymentWebhookEvent.provider == provider,
                PaymentWebhookEvent.provider_event_id == event_id,
            )
        ).scalar_one_or_none()

    existing = find_existing()
    if existing is not None:
        if existing.payload_hash != digest or existing.event_type != event_type:
            raise ValueError("Duplicate provider event ID has conflicting content.")
        return existing

    event = PaymentWebhookEvent(
        provider=provider,
        provider_event_id=event_id,
        event_type=event_type,
        payload_hash=digest,
        signature_verified=True,
        processing_status="received",
        event_metadata=metadata,
    )

    try:
        # The savepoint keeps the caller's outer transaction usable if another
        # worker wins the provider/event unique-key race.
        with db.begin_nested():
            db.add(event)
            db.flush()
        return event
    except IntegrityError:
        existing = find_existing()
        if existing is None:
            raise
        if existing.payload_hash != digest or existing.event_type != event_type:
            raise ValueError("Duplicate provider event ID has conflicting content.")
        return existing


def claim_webhook_processing(
    db: Session,
    webhook_event_id: int,
    *,
    stale_after_seconds: int = 300,
) -> bool:
    """Atomically claim a received/failed event or reclaim an expired lease."""
    if stale_after_seconds < 1:
        raise ValueError("Webhook lease duration must be positive.")

    now = _now()
    stale_before = now - timedelta(seconds=stale_after_seconds)

    eligible = or_(
        PaymentWebhookEvent.processing_status.in_(("received", "failed")),
        and_(
            PaymentWebhookEvent.processing_status == "processing",
            PaymentWebhookEvent.processing_started_at.is_not(None),
            PaymentWebhookEvent.processing_started_at <= stale_before,
        ),
    )

    result = db.execute(
        update(PaymentWebhookEvent)
        .where(
            PaymentWebhookEvent.id == webhook_event_id,
            PaymentWebhookEvent.signature_verified.is_(True),
            eligible,
        )
        .values(
            processing_status="processing",
            processing_started_at=now,
            processing_attempts=PaymentWebhookEvent.processing_attempts + 1,
            processed_at=None,
            error_code=None,
        )
        .execution_options(synchronize_session=False)
    )
    db.flush()
    return result.rowcount == 1


def mark_webhook_processed(
    db: Session,
    webhook_event_id: int,
    *,
    ignored: bool = False,
    error_code: str | None = None,
):
    event = db.execute(
        select(PaymentWebhookEvent)
        .where(PaymentWebhookEvent.id == webhook_event_id)
        .with_for_update()
    ).scalar_one_or_none()

    if event is None:
        raise LookupError("Webhook event not found.")
    if not event.signature_verified:
        raise PermissionError("Cannot process an unverified webhook.")
    if event.processing_status == "processed":
        return event
    if event.processing_status == "ignored":
        return event

    event.processing_status = "ignored" if ignored else (
        "failed" if error_code else "processed"
    )
    event.error_code = (
        _required_text(error_code, "Error code", 100) if error_code else None
    )
    event.processing_started_at = None
    event.processed_at = _now()
    db.flush()
    return event




def record_verified_payment_adjustment(
    db: Session,
    payment_attempt_id: int,
    *,
    provider_transaction_id: str,
    entry_type: str,
    amount,
    currency: str,
):
    """Append a provider-verified reversal or chargeback.

    Adjustments reduce the remaining captured exposure. They never rewrite
    prior ledger entries or automatically trigger another financial action.
    """
    payment = _locked_payment(db, payment_attempt_id)
    transaction_id = _required_text(
        provider_transaction_id, "Provider transaction ID", 128
    )
    entry_type = str(entry_type or "").strip().lower()
    if entry_type not in {"reversal", "chargeback"}:
        raise ValueError("Adjustment type must be reversal or chargeback.")

    amount = _money(amount)
    currency = _currency(currency)

    duplicate = db.execute(
        select(PaymentLedgerEntry).where(
            PaymentLedgerEntry.provider == payment.provider,
            PaymentLedgerEntry.provider_transaction_id == transaction_id,
        )
    ).scalar_one_or_none()

    if duplicate is not None:
        if (
            duplicate.payment_attempt_id != payment.id
            or duplicate.entry_type != entry_type
            or duplicate.amount != amount
            or duplicate.currency != currency
        ):
            raise ValueError(
                "Provider transaction ID conflicts with an existing ledger entry."
            )
        return duplicate

    if payment.status not in {"captured", "partially_refunded", "refunded"}:
        raise ValueError("Only captured payments can receive financial adjustments.")
    if currency != payment.currency:
        raise ValueError("Adjustment currency does not match the payment.")

    entries = db.execute(
        select(PaymentLedgerEntry).where(
            PaymentLedgerEntry.payment_attempt_id == payment.id
        )
    ).scalars().all()

    captured = sum(
        (entry.amount for entry in entries if entry.entry_type == "capture"),
        Decimal("0.00"),
    )
    deductions = sum(
        (
            entry.amount
            for entry in entries
            if entry.entry_type in {"refund", "reversal", "chargeback"}
        ),
        Decimal("0.00"),
    )
    if amount > captured - deductions:
        raise ValueError("Adjustment exceeds the remaining captured balance.")

    entry = PaymentLedgerEntry(
        payment_attempt_id=payment.id,
        provider=payment.provider,
        provider_transaction_id=transaction_id,
        entry_type=entry_type,
        amount=amount,
        currency=currency,
    )
    db.add(entry)
    db.flush()
    return entry


def reconcile_provider_statement(
    db: Session,
    *,
    provider: str,
    period_start: datetime,
    period_end: datetime,
    statement_lines: list[dict],
    idempotency_key: str,
):
    """Idempotently compare a complete provider statement with the ledger."""
    provider = _provider(provider)
    key = _required_text(idempotency_key, "Reconciliation idempotency key", 128)
    if period_end <= period_start:
        raise ValueError("Reconciliation period end must be after its start.")
    if not isinstance(statement_lines, list) or len(statement_lines) > 10000:
        raise ValueError("Statement must contain at most 10,000 transaction lines.")

    normalized = {}
    for line in statement_lines:
        if not isinstance(line, dict):
            raise ValueError("Every statement line must be an object.")
        transaction_id = _required_text(
            line.get("provider_transaction_id"), "Statement transaction ID", 128
        )
        if transaction_id in normalized:
            raise ValueError("Provider statement contains duplicate transaction IDs.")
        normalized[transaction_id] = {
            "amount": _money(line.get("amount")),
            "currency": _currency(line.get("currency")),
        }

    canonical = [
        {
            "provider_transaction_id": transaction_id,
            "amount": str(values["amount"]),
            "currency": values["currency"],
        }
        for transaction_id, values in sorted(normalized.items())
    ]
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    existing = db.scalar(
        select(PaymentReconciliationBatch).where(
            PaymentReconciliationBatch.provider == provider,
            PaymentReconciliationBatch.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.statement_hash != digest:
            raise ValueError(
                "Reconciliation idempotency key was reused with different statement content."
            )
        return existing

    batch = PaymentReconciliationBatch(
        provider=provider,
        idempotency_key=key,
        statement_hash=digest,
        period_start=period_start,
        period_end=period_end,
        status="running",
    )
    db.add(batch)
    db.flush()

    ledger_entries = db.execute(
        select(PaymentLedgerEntry).where(
            PaymentLedgerEntry.provider == provider,
            PaymentLedgerEntry.created_at >= period_start,
            PaymentLedgerEntry.created_at < period_end,
        )
    ).scalars().all()

    ledger_by_id = {
        entry.provider_transaction_id: entry for entry in ledger_entries
    }

    for transaction_id, statement in normalized.items():
        entry = ledger_by_id.get(transaction_id)
        if entry is None:
            outcome = "missing_internal"
            payment_id = None
            expected = None
            actual = statement["amount"]
            currency = statement["currency"]
        else:
            payment_id = entry.payment_attempt_id
            expected = entry.amount
            actual = statement["amount"]
            currency = statement["currency"]
            if entry.currency != currency:
                outcome = "currency_mismatch"
            elif entry.amount != actual:
                outcome = "amount_mismatch"
            else:
                outcome = "matched"

        db.add(PaymentReconciliationItem(
            batch_id=batch.id,
            payment_attempt_id=payment_id,
            provider_transaction_id=transaction_id,
            outcome=outcome,
            expected_amount=expected,
            actual_amount=actual,
            currency=currency,
        ))

    for transaction_id, entry in ledger_by_id.items():
        if transaction_id not in normalized:
            db.add(PaymentReconciliationItem(
                batch_id=batch.id,
                payment_attempt_id=entry.payment_attempt_id,
                provider_transaction_id=transaction_id,
                outcome="missing_provider",
                expected_amount=entry.amount,
                actual_amount=None,
                currency=entry.currency,
            ))

    batch.status = "completed"
    batch.completed_at = _now()
    db.flush()
    return batch
