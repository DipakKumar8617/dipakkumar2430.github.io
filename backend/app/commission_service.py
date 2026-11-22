"""Commission accrual and reversal services.

Callers own the transaction boundary. Functions flush but never commit.
Only verified payment-ledger events may be passed to these functions.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Booking,
    CommissionLedgerEntry,
    CommissionRule,
    PaymentAttempt,
    PaymentLedgerEntry,
    RealEstateTransaction,
)


CENT = Decimal("0.01")
BPS = Decimal("10000")


def _money(value) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid commission amount.") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("Commission amount must be positive.")
    return result


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _commission_for(basis: Decimal, rate_bps: int) -> Decimal:
    return (basis * Decimal(rate_bps) / BPS).quantize(
        CENT, rounding=ROUND_HALF_UP
    )


def accrue_verified_capture(
    db: Session,
    payment_ledger_entry_id: int,
) -> CommissionLedgerEntry | None:
    """Accrue once for a verified capture using immutable transaction attribution."""
    source = db.scalar(
        select(PaymentLedgerEntry)
        .where(PaymentLedgerEntry.id == payment_ledger_entry_id)
        .with_for_update()
    )
    if source is None:
        raise LookupError("Payment ledger entry not found.")
    if source.entry_type != "capture":
        raise ValueError("Commission accrual requires a capture ledger entry.")

    duplicate = db.scalar(
        select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.source_payment_ledger_entry_id == source.id
        )
    )
    if duplicate is not None:
        return duplicate

    payment = db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == source.payment_attempt_id)
        .with_for_update()
    )
    if payment is None:
        raise LookupError("Payment attempt not found.")

    booking = db.scalar(
        select(Booking).where(Booking.id == payment.booking_id)
    )
    if booking is None:
        raise LookupError("Booking not found.")

    transaction = db.scalar(
        select(RealEstateTransaction)
        .where(RealEstateTransaction.id == booking.transaction_id)
        .with_for_update()
    )
    if transaction is None:
        raise LookupError("Transaction not found.")

    # Direct bookings without a CP attribution do not earn CP commission.
    cp_account_id = transaction.channel_partner_account_id
    if cp_account_id is None:
        return None

    # Resolve the rule effective when the provider recorded the capture,
    # not when a delayed webhook happens to be processed.
    effective_at = source.created_at or _now()
    if effective_at.tzinfo is not None:
        effective_at = effective_at.astimezone(timezone.utc).replace(tzinfo=None)

    rule = db.scalar(
        select(CommissionRule)
        .where(
            CommissionRule.is_active.is_(True),
            CommissionRule.currency == source.currency,
            CommissionRule.starts_at <= effective_at,
            (
                CommissionRule.ends_at.is_(None)
                | (CommissionRule.ends_at > effective_at)
            ),
        )
        .order_by(CommissionRule.starts_at.desc(), CommissionRule.id.desc())
        .limit(1)
        .with_for_update()
    )
    if rule is None:
        return None

    basis = _money(source.amount)
    amount = _commission_for(basis, rule.rate_bps)
    if amount <= 0:
        return None

    entry = CommissionLedgerEntry(
        channel_partner_account_id=cp_account_id,
        transaction_id=transaction.id,
        payment_attempt_id=payment.id,
        source_payment_ledger_entry_id=source.id,
        commission_rule_id=rule.id,
        entry_type="accrual",
        basis_amount=basis,
        amount=amount,
        rate_bps=rule.rate_bps,
        currency=source.currency,
        approval_status="pending",
    )
    db.add(entry)
    db.flush()
    return entry


def reverse_verified_refund(
    db: Session,
    refund_ledger_entry_id: int,
) -> CommissionLedgerEntry | None:
    """Reverse the cumulative proportional commission for a verified refund."""
    source = db.scalar(
        select(PaymentLedgerEntry)
        .where(PaymentLedgerEntry.id == refund_ledger_entry_id)
        .with_for_update()
    )
    if source is None:
        raise LookupError("Payment ledger entry not found.")
    if source.entry_type != "refund":
        raise ValueError("Commission reversal requires a refund ledger entry.")

    duplicate = db.scalar(
        select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.source_payment_ledger_entry_id == source.id
        )
    )
    if duplicate is not None:
        return duplicate

    accrual = db.scalar(
        select(CommissionLedgerEntry)
        .where(
            CommissionLedgerEntry.payment_attempt_id == source.payment_attempt_id,
            CommissionLedgerEntry.entry_type == "accrual",
        )
        .with_for_update()
    )
    if accrual is None:
        return None

    # Serialize refund aggregation on the payment attempt. The verified
    # payment service already holds this lock during normal refund processing.
    payment = db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == source.payment_attempt_id)
        .with_for_update()
    )
    if payment is None:
        raise LookupError("Payment attempt not found.")

    # Include every verified refund. Do not infer chronology from row IDs.
    payment_entries = db.scalars(
        select(PaymentLedgerEntry).where(
            PaymentLedgerEntry.payment_attempt_id == source.payment_attempt_id,
            PaymentLedgerEntry.entry_type == "refund",
        )
    ).all()
    refunded_basis = sum(
        (Decimal(str(item.amount)) for item in payment_entries),
        Decimal("0.00"),
    )
    refunded_basis = min(refunded_basis, Decimal(str(accrual.basis_amount)))

    cumulative_target = _commission_for(refunded_basis, accrual.rate_bps)
    prior_reversals = db.scalars(
        select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.original_accrual_id == accrual.id,
            CommissionLedgerEntry.entry_type == "reversal",
        )
    ).all()
    already_reversed = sum(
        (Decimal(str(item.amount)) for item in prior_reversals),
        Decimal("0.00"),
    )
    reversal_amount = (cumulative_target - already_reversed).quantize(CENT)

    if reversal_amount <= 0:
        return None

    reversal = CommissionLedgerEntry(
        channel_partner_account_id=accrual.channel_partner_account_id,
        transaction_id=accrual.transaction_id,
        payment_attempt_id=accrual.payment_attempt_id,
        source_payment_ledger_entry_id=source.id,
        original_accrual_id=accrual.id,
        commission_rule_id=accrual.commission_rule_id,
        entry_type="reversal",
        basis_amount=_money(source.amount),
        amount=reversal_amount,
        rate_bps=accrual.rate_bps,
        currency=accrual.currency,
        # A reversal follows the approval state of its original accrual.
        approval_status=accrual.approval_status,
        reviewed_by_account_id=accrual.reviewed_by_account_id,
        reviewed_at=accrual.reviewed_at,
    )
    db.add(reversal)
    db.flush()
    return reversal


def review_commission(
    db: Session,
    *,
    entry_id: int,
    admin_account_id: int,
    decision: str,
    note: str | None = None,
) -> CommissionLedgerEntry:
    if decision not in {"approved", "rejected"}:
        raise ValueError("Decision must be approved or rejected.")

    entry = db.scalar(
        select(CommissionLedgerEntry)
        .where(CommissionLedgerEntry.id == entry_id)
        .with_for_update()
    )
    if entry is None:
        raise LookupError("Commission entry not found.")
    if entry.entry_type != "accrual":
        raise ValueError("Only accrual entries can be reviewed.")
    if entry.approval_status != "pending":
        raise ValueError("Commission entry has already been reviewed.")

    entry.approval_status = decision
    entry.reviewed_by_account_id = admin_account_id
    entry.reviewed_at = _now()
    entry.review_note = (note or "").strip()[:500] or None

    # Reversals already created for this accrual inherit the review decision.
    reversals = db.scalars(
        select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.original_accrual_id == entry.id,
            CommissionLedgerEntry.entry_type == "reversal",
        )
    ).all()
    for reversal in reversals:
        reversal.approval_status = decision
        reversal.reviewed_by_account_id = admin_account_id
        reversal.reviewed_at = entry.reviewed_at

    db.flush()
    return entry
