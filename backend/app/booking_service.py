from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import (
    Booking,
    InventoryHold,
    PropertyInventoryItem,
    RealEstateTransaction,
    TransactionEvent,
    TransactionOffer,
)


def _now() -> datetime:
    # Database DATETIME columns are stored without timezone information.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _amount(value) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Booking amount is invalid.")
    if not result.is_finite() or result <= 0:
        raise ValueError("Booking amount must be positive.")
    return result


def create_booking(
    db: Session,
    *,
    transaction_id: int,
    buyer_account_id: int,
    inventory_hold_id: int,
) -> Booking:
    """Create a payable booking from an agreed transaction and active hold.

    Caller owns commit/rollback.
    """
    now = _now()

    transaction = db.scalar(
        select(RealEstateTransaction)
        .where(RealEstateTransaction.id == transaction_id)
        .with_for_update()
    )
    if transaction is None:
        raise LookupError("Transaction not found.")

    if transaction.buyer_account_id != buyer_account_id:
        raise PermissionError("Only the transaction buyer can create its booking.")

    existing = db.scalar(
        select(Booking)
        .where(Booking.transaction_id == transaction_id)
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.buyer_account_id == buyer_account_id
            and existing.inventory_hold_id == inventory_hold_id
            and existing.status == "awaiting_payment"
            and _as_naive_utc(existing.expires_at) > now
        ):
            return existing
        raise ValueError("A booking already exists for this transaction.")

    if transaction.status != "terms_agreed":
        raise ValueError("Transaction must have agreed terms before booking.")

    hold = db.scalar(
        select(InventoryHold)
        .where(InventoryHold.id == inventory_hold_id)
        .with_for_update()
    )
    if hold is None:
        raise LookupError("Inventory hold not found.")

    if hold.buyer_account_id != buyer_account_id:
        raise PermissionError("Inventory hold belongs to another buyer.")

    if hold.inventory_item_id != transaction.inventory_item_id:
        raise ValueError("Inventory hold does not match the transaction inventory.")

    if hold.status != "active":
        raise ValueError("Inventory hold is not active.")

    hold_expiry = _as_naive_utc(hold.expires_at)
    if hold_expiry <= now:
        hold.status = "expired"
        raise ValueError("Inventory hold has expired.")

    inventory = db.scalar(
        select(PropertyInventoryItem)
        .where(PropertyInventoryItem.id == transaction.inventory_item_id)
        .with_for_update()
    )
    if inventory is None:
        raise LookupError("Inventory item not found.")

    if inventory.property_listing_id != transaction.property_listing_id:
        raise ValueError("Transaction inventory does not match its property.")

    if inventory.availability_status != "available":
        raise ValueError("Inventory item is no longer available.")

    if transaction.transaction_type == "negotiation":
        accepted_offer = db.scalar(
            select(TransactionOffer)
            .where(
                TransactionOffer.transaction_id == transaction.id,
                TransactionOffer.status == "accepted",
            )
            .order_by(TransactionOffer.id.desc())
            .limit(1)
            .with_for_update()
        )
        if accepted_offer is None:
            raise ValueError("An accepted offer is required for negotiated booking.")
        if accepted_offer.currency != "INR":
            raise ValueError("Only INR bookings are currently supported.")
        agreed_amount = _amount(accepted_offer.amount)
    elif transaction.transaction_type == "direct_booking":
        if inventory.price_inr is None:
            raise ValueError("Inventory price is not configured.")
        agreed_amount = _amount(inventory.price_inr)
    else:
        raise ValueError("Unsupported transaction type.")

    booking = Booking(
        transaction_id=transaction.id,
        inventory_item_id=inventory.id,
        inventory_hold_id=hold.id,
        buyer_account_id=buyer_account_id,
        agreed_amount=agreed_amount,
        currency="INR",
        status="awaiting_payment",
        expires_at=hold_expiry,
    )
    db.add(booking)

    previous_status = transaction.status
    transaction.status = "booking_pending"
    db.add(
        TransactionEvent(
            transaction_id=transaction.id,
            actor_account_id=buyer_account_id,
            event_type="booking_created",
            from_status=previous_status,
            to_status="booking_pending",
            details={
                "inventory_item_id": inventory.id,
                "inventory_hold_id": hold.id,
                "agreed_amount": str(agreed_amount),
                "currency": "INR",
            },
        )
    )

    db.flush()
    return booking


def confirm_booking_after_capture(
    db: Session,
    *,
    booking_id: int,
    payment_attempt_id: int,
) -> Booking:
    """Confirm booking after the caller has independently verified capture.

    Must be called in the same DB transaction as the payment ledger capture.
    Caller owns commit/rollback.
    """
    from .models import PaymentAttempt

    booking = db.scalar(
        select(Booking)
        .where(Booking.id == booking_id)
        .with_for_update()
    )
    if booking is None:
        raise LookupError("Booking not found.")

    if booking.status not in {"awaiting_payment", "confirmed"}:
        raise ValueError("Booking is not awaiting payment or already confirmed.")

    payment = db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == payment_attempt_id)
        .with_for_update()
    )
    if payment is None:
        raise LookupError("Payment attempt not found.")

    if payment.booking_id != booking.id:
        raise ValueError("Payment attempt does not belong to this booking.")

    if payment.status != "captured":
        raise ValueError("Payment must be captured before booking confirmation.")

    if payment.currency != booking.currency:
        raise ValueError("Payment currency does not match booking currency.")

    if _amount(payment.amount) != _amount(booking.agreed_amount):
        raise ValueError("Payment amount does not match the booking amount.")

    if booking.status == "confirmed":
        confirmation_event = db.scalar(
            select(TransactionEvent)
            .where(
                TransactionEvent.transaction_id == booking.transaction_id,
                TransactionEvent.event_type == "booking_payment_confirmed",
            )
            .order_by(TransactionEvent.id.desc())
            .limit(1)
        )
        details = (
            confirmation_event.details
            if confirmation_event is not None
            and isinstance(confirmation_event.details, dict)
            else {}
        )
        if str(details.get("payment_attempt_id") or "") != str(payment.id):
            raise ValueError(
                "Booking was already confirmed by a different payment attempt "
                "or its confirmation record cannot be verified."
            )
        payment.failure_code = None
        db.flush()
        return booking

    now = _now()
    if _as_naive_utc(booking.expires_at) <= now:
        raise ValueError("Booking has expired.")

    transaction = db.scalar(
        select(RealEstateTransaction)
        .where(RealEstateTransaction.id == booking.transaction_id)
        .with_for_update()
    )
    if transaction is None:
        raise LookupError("Booking transaction not found.")

    if transaction.status != "booking_pending":
        raise ValueError("Transaction is not awaiting booking confirmation.")

    hold = db.scalar(
        select(InventoryHold)
        .where(InventoryHold.id == booking.inventory_hold_id)
        .with_for_update()
    )
    if hold is None:
        raise LookupError("Booking inventory hold not found.")

    if (
        hold.status != "active"
        or hold.buyer_account_id != booking.buyer_account_id
        or hold.inventory_item_id != booking.inventory_item_id
        or _as_naive_utc(hold.expires_at) <= now
    ):
        raise ValueError("Booking inventory hold is no longer valid.")

    inventory = db.scalar(
        select(PropertyInventoryItem)
        .where(PropertyInventoryItem.id == booking.inventory_item_id)
        .with_for_update()
    )
    if inventory is None:
        raise LookupError("Booking inventory item not found.")

    if inventory.availability_status != "available":
        raise ValueError("Inventory item is no longer available.")

    previous_status = transaction.status

    hold.status = "converted"
    inventory.availability_status = "reserved"
    booking.status = "confirmed"
    transaction.status = "booked"
    payment.failure_code = None

    db.add(
        TransactionEvent(
            transaction_id=transaction.id,
            actor_account_id=None,
            event_type="booking_payment_confirmed",
            from_status=previous_status,
            to_status="booked",
            details={
                "booking_id": booking.id,
                "payment_attempt_id": payment.id,
                "inventory_item_id": inventory.id,
                "amount": str(booking.agreed_amount),
                "currency": booking.currency,
            },
        )
    )

    db.flush()
    return booking
