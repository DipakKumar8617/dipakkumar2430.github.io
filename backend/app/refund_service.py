from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PaymentAttempt, PaymentLedgerEntry, PaymentRefund
from .payment_service import record_verified_refund


ACTIVE_REFUND_STATUSES = {"requested", "submitted", "pending", "unknown"}


def _money(value) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Refund amount is invalid.") from exc

    if not amount.is_finite() or amount <= 0:
        raise ValueError("Refund amount must be positive.")
    return amount


def _required_text(value, name: str, limit: int = 128) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise ValueError(f"{name} is invalid.")
    return result


def get_refundable_balance(db: Session, payment: PaymentAttempt) -> Decimal:
    entries = db.execute(
        select(PaymentLedgerEntry).where(
            PaymentLedgerEntry.payment_attempt_id == payment.id
        )
    ).scalars().all()

    captured = sum(
        (entry.amount for entry in entries if entry.entry_type == "capture"),
        Decimal("0.00"),
    )
    refunded = sum(
        (entry.amount for entry in entries if entry.entry_type == "refund"),
        Decimal("0.00"),
    )
    return captured - refunded


def create_refund_request(
    db: Session,
    *,
    payment_attempt_id: int,
    amount,
    idempotency_key: str,
) -> PaymentRefund:
    """Reserve refundable balance. Caller owns commit/rollback."""
    key = _required_text(idempotency_key, "Idempotency key")
    payment = db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == payment_attempt_id)
        .with_for_update()
    )
    if payment is None:
        raise LookupError("Payment attempt not found.")

    existing = db.scalar(
        select(PaymentRefund).where(
            PaymentRefund.provider == payment.provider,
            PaymentRefund.idempotency_key == key,
        )
    )
    amount = _money(amount)

    if existing is not None:
        if (
            existing.payment_attempt_id != payment.id
            or existing.amount != amount
            or existing.currency != payment.currency
        ):
            raise ValueError("Idempotency key was already used for a different refund.")
        return existing

    if payment.status not in {"captured", "partially_refunded"}:
        raise ValueError("Only captured payments can be refunded.")

    balance = get_refundable_balance(db, payment)

    active_refunds = db.execute(
        select(PaymentRefund).where(
            PaymentRefund.payment_attempt_id == payment.id,
            PaymentRefund.status.in_(ACTIVE_REFUND_STATUSES),
        )
    ).scalars().all()

    reserved = sum(
        (refund.amount for refund in active_refunds),
        Decimal("0.00"),
    )

    if amount > balance - reserved:
        raise ValueError("Refund exceeds the unreserved captured balance.")

    refund = PaymentRefund(
        payment_attempt_id=payment.id,
        provider=payment.provider,
        idempotency_key=key,
        amount=amount,
        currency=payment.currency,
        status="requested",
    )
    db.add(refund)
    db.flush()
    return refund


def update_refund_submission(
    db: Session,
    refund_id: int,
    *,
    status: str,
    provider_refund_id: str | None = None,
    failure_code: str | None = None,
) -> PaymentRefund:
    """Persist provider submission state; never writes a financial ledger entry."""
    if status not in {"submitted", "pending", "unknown", "failed"}:
        raise ValueError("Invalid refund submission status.")

    refund = db.scalar(
        select(PaymentRefund)
        .where(PaymentRefund.id == refund_id)
        .with_for_update()
    )
    if refund is None:
        raise LookupError("Refund request not found.")

    if refund.status == "processed":
        return refund

    if provider_refund_id is not None:
        provider_refund_id = _required_text(
            provider_refund_id,
            "Provider refund ID",
        )
        if (
            refund.provider_refund_id is not None
            and refund.provider_refund_id != provider_refund_id
        ):
            raise ValueError("Refund request has a conflicting provider refund ID.")
        refund.provider_refund_id = provider_refund_id

    refund.status = status
    refund.failure_code = (
        _required_text(failure_code, "Failure code", 100)
        if failure_code else None
    )
    db.flush()
    return refund


def record_provider_confirmed_refund(
    db: Session,
    refund_id: int,
    *,
    provider_refund_id: str,
    provider_payment_id: str,
    amount,
    currency: str,
) -> PaymentRefund:
    """Record only after independently verifying the provider reports processed."""
    refund = db.scalar(
        select(PaymentRefund)
        .where(PaymentRefund.id == refund_id)
        .with_for_update()
    )
    if refund is None:
        raise LookupError("Refund request not found.")

    provider_refund_id = _required_text(provider_refund_id, "Provider refund ID")
    provider_payment_id = _required_text(provider_payment_id, "Provider payment ID")
    amount = _money(amount)
    currency = _required_text(currency, "Currency", 3).upper()

    payment = db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == refund.payment_attempt_id)
        .with_for_update()
    )
    if payment is None:
        raise LookupError("Payment attempt not found.")

    if payment.provider_payment_id != provider_payment_id:
        raise ValueError("Refund belongs to a different provider payment.")

    if amount != refund.amount or currency != refund.currency:
        raise ValueError("Provider refund amount or currency does not match the request.")

    if refund.provider_refund_id not in {None, provider_refund_id}:
        raise ValueError("Provider refund ID does not match the request.")

    if refund.status == "processed":
        if refund.provider_refund_id != provider_refund_id:
            raise ValueError("Processed refund has a conflicting provider ID.")
        return refund

    record_verified_refund(
        db,
        payment.id,
        provider_transaction_id=provider_refund_id,
        amount=amount,
        currency=currency,
    )

    refund.provider_refund_id = provider_refund_id
    refund.status = "processed"
    refund.failure_code = None
    db.flush()
    return refund
