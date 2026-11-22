"""Authenticated Razorpay payment API.

The browser never determines payment success. Checkout verification and
webhook processing both verify payment state directly with Razorpay.
"""
from datetime import datetime
import hashlib
import json
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .booking_service import confirm_booking_after_capture
from .auth_dependencies import resolve_session_account
from .database import get_db
from .models import Booking, PaymentAttempt, PaymentRefund, PaymentWebhookEvent
from .payment_service import (
    claim_webhook_processing,
    create_payment_attempt,
    mark_webhook_processed,
    record_verified_capture,
    record_verified_webhook,
    update_payment_status,
)
from .razorpay_gateway import (
    RazorpayError,
    RazorpayGateway,
    RazorpayVerificationError,
)
from .refund_service import (
    create_refund_request,
    record_provider_confirmed_refund,
    update_refund_submission,
)

router = APIRouter(prefix="/api", tags=["Payments"])


class CheckoutVerification(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=128)
    razorpay_payment_id: str = Field(min_length=1, max_length=128)
    razorpay_signature: str = Field(min_length=1, max_length=128)


class RefundCreatePayload(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)




def _payment_data(payment):
    return {
        "id": payment.id,
        "booking_id": payment.booking_id,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "provider": payment.provider,
        "provider_order_id": payment.provider_order_id,
        "provider_payment_id": payment.provider_payment_id,
        "status": payment.status,
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
    }


def _service_error(exc):
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _buyer_account(request, db):
    account = resolve_session_account(request, db)
    if str(account.role or "").strip().casefold() != "buyer":
        raise HTTPException(
            status_code=403,
            detail="Only the booking buyer can perform this payment action.",
        )
    return account




def _mark_booking_confirmation_failed(db: Session, payment_attempt_id: int):
    """Persist a reconciliation marker without undoing a verified capture."""
    db.rollback()
    payment = db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == payment_attempt_id)
        .with_for_update()
    )
    if payment is None or payment.status != "captured":
        db.rollback()
        raise RuntimeError("Captured payment could not be located for reconciliation.")

    payment.failure_code = "booking_confirmation_failed"
    db.commit()


@router.post("/bookings/{booking_id}/payments", status_code=201)
def create_booking_payment(
    booking_id: int,
    request: Request,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
    ),
    db: Session = Depends(get_db),
):
    account = _buyer_account(request, db)

    try:
        payment = create_payment_attempt(
            db,
            booking_id=booking_id,
            buyer_account_id=account.id,
            provider="razorpay",
            idempotency_key=idempotency_key,
        )
        db.commit()
        db.refresh(payment)

        # A retry with the same key reuses an already-created Razorpay order.
        if payment.provider_order_id:
            return {
                "payment": _payment_data(payment),
                "checkout": {
                    "key_id": RazorpayGateway().config.key_id,
                    "order_id": payment.provider_order_id,
                    "amount": int(payment.amount * 100),
                    "currency": payment.currency,
                },
            }

        gateway = RazorpayGateway()
        order = gateway.create_order(
            payment_attempt_id=payment.id,
            amount=payment.amount,
            currency=payment.currency,
        )

        payment.provider_order_id = order["id"]
        update_payment_status(db, payment.id, "pending")
        db.commit()
        db.refresh(payment)

        return {
            "payment": _payment_data(payment),
            "checkout": {
                "key_id": gateway.config.key_id,
                "order_id": order["id"],
                "amount": order["amount"],
                "currency": order["currency"],
            },
        }

    except (PermissionError, LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A conflicting payment attempt already exists. Retry with the same idempotency key.",
        ) from exc
    except RazorpayError as exc:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail="Payment gateway is temporarily unavailable. Retry using the same idempotency key.",
        ) from exc


@router.get("/payments/{payment_attempt_id}")
def get_payment(
    payment_attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    account = _buyer_account(request, db)
    payment = db.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.id == payment_attempt_id,
            PaymentAttempt.buyer_account_id == account.id,
        )
    )
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment attempt not found.")
    return _payment_data(payment)


@router.post("/payments/{payment_attempt_id}/refunds", status_code=202)
def create_payment_refund(
    payment_attempt_id: int,
    payload: RefundCreatePayload,
    request: Request,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
    ),
    db: Session = Depends(get_db),
):
    account = resolve_session_account(request, db)
    if str(account.role or "").strip().casefold() != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only administrators can initiate payment refunds.",
        )

    payment = db.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.id == payment_attempt_id
        )
    )
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment attempt not found.")

    if payment.provider != "razorpay" or not payment.provider_payment_id:
        raise HTTPException(
            status_code=409,
            detail="Payment has no verified Razorpay payment ID.",
        )

    # Never resubmit an existing ambiguous request: the provider may
    # already have accepted it even if our original request timed out.
    existing = db.scalar(
        select(PaymentRefund).where(
            PaymentRefund.provider == payment.provider,
            PaymentRefund.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        same_request = (
            existing.payment_attempt_id == payment.id
            and Decimal(str(existing.amount)) == Decimal(str(payload.amount))
            and existing.currency == payment.currency
        )
        if not same_request:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key was already used for a different refund request.",
            )

        return {
            "id": existing.id,
            "payment_attempt_id": existing.payment_attempt_id,
            "amount": str(existing.amount),
            "currency": existing.currency,
            "status": existing.status,
            "provider_refund_id": existing.provider_refund_id,
            "message": "Existing refund request returned; no duplicate provider request was made.",
        }

    try:
        refund = create_refund_request(
            db,
            payment_attempt_id=payment_attempt_id,
            amount=payload.amount,
            idempotency_key=idempotency_key,
        )
        db.commit()
        db.refresh(refund)

        # Atomically claim submission to prevent concurrent provider requests.
        claim = db.execute(
            update(PaymentRefund)
            .where(
                PaymentRefund.id == refund.id,
                PaymentRefund.status == "requested",
            )
            .values(status="submitted")
        )
        db.commit()

        if claim.rowcount != 1:
            db.refresh(refund)
            return {
                "id": refund.id,
                "payment_attempt_id": refund.payment_attempt_id,
                "amount": str(refund.amount),
                "currency": refund.currency,
                "status": refund.status,
                "provider_refund_id": refund.provider_refund_id,
                "message": "Refund is already being processed; no duplicate provider request was made.",
            }

        gateway = RazorpayGateway()

        provider_refund = gateway.create_refund(
            payment_id=payment.provider_payment_id,
            amount=refund.amount,
            currency=refund.currency,
            receipt=f"refund-{refund.id}",
            notes={"internal_refund_id": str(refund.id)},
        )

        update_refund_submission(
            db,
            refund.id,
            status="unknown",
            provider_refund_id=provider_refund["id"],
        )
        db.commit()

        verified = gateway.verify_refund(
            refund_id=provider_refund["id"],
            expected_payment_id=payment.provider_payment_id,
            expected_amount=refund.amount,
            expected_currency=refund.currency,
        )

        if verified["status"] == "processed":
            record_provider_confirmed_refund(
                db,
                refund.id,
                provider_refund_id=verified["id"],
                provider_payment_id=verified["payment_id"],
                amount=verified["amount"],
                currency=verified["currency"],
            )
        else:
            update_refund_submission(
                db,
                refund.id,
                status=verified["status"],
            )

        db.commit()
        db.refresh(refund)

        return {
            "id": refund.id,
            "payment_attempt_id": refund.payment_attempt_id,
            "amount": str(refund.amount),
            "currency": refund.currency,
            "status": refund.status,
            "provider_refund_id": refund.provider_refund_id,
        }

    except (PermissionError, LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Refund conflicts with an existing request or ledger entry.",
        ) from exc
    except RazorpayVerificationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail="Refund provider state could not be verified. The request is retained for reconciliation.",
        ) from exc
    except RazorpayError as exc:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail="Refund outcome is unknown. The request is retained; do not submit another refund.",
        ) from exc




@router.post("/payments/{payment_attempt_id}/verify")
def verify_checkout_payment(
    payment_attempt_id: int,
    payload: CheckoutVerification,
    request: Request,
    db: Session = Depends(get_db),
):
    account = _buyer_account(request, db)
    payment = db.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.id == payment_attempt_id,
            PaymentAttempt.buyer_account_id == account.id,
        )
    )
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment attempt not found.")

    if payment.provider != 'razorpay':
        raise HTTPException(
            status_code=409,
            detail="Payment has no verified Razorpay payment ID.",
        )

    if payment.provider != "razorpay" or not payment.provider_order_id:
        raise HTTPException(status_code=409, detail="Payment has no valid Razorpay order.")

    try:
        gateway = RazorpayGateway()

        if payload.razorpay_order_id != payment.provider_order_id:
            raise HTTPException(status_code=409, detail="Razorpay order does not match this payment.")

        if not gateway.verify_checkout_signature(
            order_id=payload.razorpay_order_id,
            payment_id=payload.razorpay_payment_id,
            signature=payload.razorpay_signature,
        ):
            raise HTTPException(status_code=400, detail="Payment signature is invalid.")

        verified = gateway.verify_captured_payment(
            payment_id=payload.razorpay_payment_id,
            expected_order_id=payment.provider_order_id,
            expected_amount=payment.amount,
            expected_currency=payment.currency,
        )

        record_verified_capture(
            db,
            payment.id,
            provider_transaction_id=verified["payment_id"],
            provider_payment_id=verified["payment_id"],
            amount=payment.amount,
            currency=payment.currency,
        )
        # Preserve the verified capture even if booking confirmation fails.
        db.commit()

        try:
            confirm_booking_after_capture(
                db,
                booking_id=payment.booking_id,
                payment_attempt_id=payment.id,
            )
            db.commit()
        except Exception as exc:
            _mark_booking_confirmation_failed(db, payment.id)
            raise HTTPException(
                status_code=409,
                detail=(
                    "Payment was captured, but booking confirmation failed. "
                    "Reconciliation is required; retry verification later."
                ),
            ) from exc

        db.refresh(payment)
        return _payment_data(payment)

    except HTTPException:
        db.rollback()
        raise
    except (PermissionError, LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    except RazorpayVerificationError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RazorpayError as exc:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail="Unable to verify payment with the gateway. Please retry.",
        ) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Payment verification conflicts with an existing ledger entry.",
        ) from exc


def _mark_webhook_failed_safely(db: Session, webhook_event_id: int | None, code: str):
    """Persist a retryable failure without masking the original webhook error."""
    if webhook_event_id is None:
        return
    try:
        db.rollback()
        mark_webhook_processed(
            db,
            webhook_event_id,
            error_code=code,
        )
        db.commit()
    except Exception:
        db.rollback()


@router.post("/payments/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db),
    signature: str | None = Header(default=None, alias="X-Razorpay-Signature"),
    event_id: str | None = Header(default=None, alias="X-Razorpay-Event-Id"),
):
    raw_body = await request.body()
    gateway = RazorpayGateway()

    if not signature or not gateway.verify_webhook_signature(
        raw_body=raw_body,
        signature=signature,
    ):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    if not event_id or len(event_id) > 160:
        raise HTTPException(status_code=400, detail="Missing or invalid webhook event ID.")

    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload.") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload.")

    event_type = str(payload.get("event") or "").strip()
    if not event_type or len(event_type) > 120:
        raise HTTPException(status_code=400, detail="Invalid webhook event type.")

    digest = hashlib.sha256(raw_body).hexdigest()

    webhook_event_id = None
    try:
        event = record_verified_webhook(
            db,
            provider="razorpay",
            provider_event_id=event_id,
            event_type=event_type,
            payload_hash=digest,
            signature_verified=True,
            metadata={"event": event_type},
        )
        webhook_event_id = event.id

        # Terminal deliveries are acknowledged without repeating financial writes.
        if event.processing_status in {"processed", "ignored"}:
            db.commit()
            return {"success": True, "duplicate": True}

        # Only one worker can acquire this lease. A crashed worker becomes
        # reclaimable after five minutes; every claim is committed before
        # network I/O or financial processing starts.
        if not claim_webhook_processing(db, webhook_event_id):
            db.commit()
            return {
                "success": True,
                "duplicate": True,
                "in_progress": True,
            }
        db.commit()

        if event_type == "payment.captured":
            entity = (
                payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
            )
            provider_payment_id = entity.get("id")
            provider_order_id = entity.get("order_id")

            if not provider_payment_id or not provider_order_id:
                raise ValueError("Captured payment webhook is missing payment identifiers.")

            payment = db.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.provider == "razorpay",
                    PaymentAttempt.provider_order_id == provider_order_id,
                )
            )
            if payment is None:
                raise LookupError("No internal payment matches this Razorpay order.")

            verified = gateway.verify_captured_payment(
                payment_id=provider_payment_id,
                expected_order_id=payment.provider_order_id,
                expected_amount=payment.amount,
                expected_currency=payment.currency,
            )

            record_verified_capture(
                db,
                payment.id,
                provider_transaction_id=verified["payment_id"],
                provider_payment_id=verified["payment_id"],
                amount=payment.amount,
                currency=payment.currency,
            )
            # Preserve the verified capture even if booking confirmation fails.
            db.commit()

            try:
                confirm_booking_after_capture(
                    db,
                    booking_id=payment.booking_id,
                    payment_attempt_id=payment.id,
                )
                db.commit()
            except Exception as exc:
                _mark_booking_confirmation_failed(db, payment.id)
                raise HTTPException(
                    status_code=500,
                    detail="Captured payment requires booking reconciliation.",
                ) from exc

        elif event_type == "payment.failed":
            entity = (
                payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
            )
            provider_payment_id = entity.get("id")
            provider_order_id = entity.get("order_id")

            if not provider_order_id:
                raise ValueError("Failed payment webhook is missing its order ID.")

            payment = db.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.provider == "razorpay",
                    PaymentAttempt.provider_order_id == provider_order_id,
                )
            )
            if payment is None:
                raise LookupError("No internal payment matches this Razorpay order.")

            provider_state = (
                gateway.fetch_payment(provider_payment_id)
                if provider_payment_id else {}
            )
            if (
                provider_state.get("order_id") != payment.provider_order_id
                or provider_state.get("status") != "failed"
            ):
                raise RazorpayVerificationError(
                    "Razorpay has not confirmed this failed payment."
                )

            update_payment_status(
                db,
                payment.id,
                "failed",
                failure_code="provider_payment_failed",
            )

        else:
            mark_webhook_processed(db, event.id, ignored=True)
            db.commit()
            return {"success": True, "ignored": True}

        mark_webhook_processed(db, event.id)
        db.commit()
        return {"success": True, "processed": True}

    except (PermissionError, LookupError, ValueError, RazorpayVerificationError) as exc:
        _mark_webhook_failed_safely(db, webhook_event_id, "webhook_validation_failed")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException as exc:
        _mark_webhook_failed_safely(db, webhook_event_id, "webhook_http_processing_failed")
        raise
    except IntegrityError as exc:
        _mark_webhook_failed_safely(db, webhook_event_id, "webhook_integrity_conflict")
        raise HTTPException(
            status_code=409,
            detail="Webhook conflicts with existing payment records.",
        ) from exc
    except RazorpayError as exc:
        _mark_webhook_failed_safely(db, webhook_event_id, "razorpay_verification_unavailable")
        raise HTTPException(
            status_code=502,
            detail="Unable to verify webhook payment with Razorpay. Provider will retry delivery.",
        ) from exc
    except Exception as exc:
        _mark_webhook_failed_safely(db, webhook_event_id, "webhook_processing_error")
        raise HTTPException(
            status_code=500,
            detail="Webhook processing failed. Provider should retry delivery.",
        ) from exc

@router.post("/payments/refunds/{refund_id}/recover")
def recover_payment_refund(
    refund_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Reconcile an ambiguous refund against authoritative Razorpay records."""
    from .models import PaymentRefund
    from .refund_service import (
        record_provider_confirmed_refund,
        update_refund_submission,
    )

    account = resolve_session_account(request, db)
    if str(account.role or "").strip().casefold() != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")

    refund = db.scalar(
        select(PaymentRefund).where(PaymentRefund.id == refund_id)
    )
    if refund is None:
        raise HTTPException(status_code=404, detail="Refund request not found.")

    payment = db.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.id == refund.payment_attempt_id
        )
    )
    if payment is None or payment.provider != "razorpay" or not payment.provider_payment_id:
        raise HTTPException(status_code=409, detail="Refund has no valid Razorpay payment.")

    if refund.status == "processed":
        return {
            "id": refund.id,
            "status": refund.status,
            "provider_refund_id": refund.provider_refund_id,
            "message": "Refund is already reconciled.",
        }

    gateway = RazorpayGateway()
    try:
        provider_refund_id = refund.provider_refund_id

        if provider_refund_id:
            verified = gateway.verify_refund(
                refund_id=provider_refund_id,
                expected_payment_id=payment.provider_payment_id,
                expected_amount=refund.amount,
                expected_currency=refund.currency,
            )
        else:
            # Search provider records. Never issue another refund request here.
            matches = {}
            for offset in range(0, 1000, 100):
                page = gateway.fetch_payment_refunds(
                    payment.provider_payment_id,
                    count=100,
                    skip=offset,
                )
                for item in page:
                    notes = item.get("notes")
                    notes = notes if isinstance(notes, dict) else {}
                    receipt_match = item.get("receipt") == f"refund-{refund.id}"
                    internal_id_match = str(
                        notes.get("internal_refund_id") or ""
                    ) == str(refund.id)
                    if receipt_match or internal_id_match:
                        item_id = item.get("id")
                        if item_id:
                            matches[str(item_id)] = item
                if len(page) < 100:
                    break

            # A full final page means the bounded search may have omitted
            # older provider records. Never treat a partial search as unique.
            if offset == 900 and len(page) == 100:
                update_refund_submission(
                    db,
                    refund.id,
                    status="unknown",
                    failure_code="provider_refund_search_limit_reached",
                )
                db.commit()
                return {
                    "id": refund.id,
                    "status": "unknown",
                    "message": (
                        "Provider refund search reached its safety limit. "
                        "No new refund was submitted; manual investigation is required."
                    ),
                }

            if len(matches) == 0:
                update_refund_submission(
                    db,
                    refund.id,
                    status="unknown",
                    failure_code="provider_refund_not_found",
                )
                db.commit()
                return {
                    "id": refund.id,
                    "status": "unknown",
                    "message": (
                        "No matching provider refund was found. No new refund was submitted; "
                        "manual investigation is required."
                    ),
                }

            if len(matches) != 1:
                update_refund_submission(
                    db,
                    refund.id,
                    status="unknown",
                    failure_code="multiple_provider_refunds_found",
                )
                db.commit()
                raise HTTPException(
                    status_code=409,
                    detail="Multiple provider refunds match this request; manual investigation is required.",
                )

            provider_refund_id = next(iter(matches))
            verified = gateway.verify_refund(
                refund_id=provider_refund_id,
                expected_payment_id=payment.provider_payment_id,
                expected_amount=refund.amount,
                expected_currency=refund.currency,
            )

        if verified["status"] == "processed":
            record_provider_confirmed_refund(
                db,
                refund.id,
                provider_refund_id=verified["id"],
                provider_payment_id=verified["payment_id"],
                amount=verified["amount"],
                currency=verified["currency"],
            )
        else:
            update_refund_submission(
                db,
                refund.id,
                status=verified["status"],
                provider_refund_id=verified["id"],
            )

        db.commit()
        db.refresh(refund)
        return {
            "id": refund.id,
            "status": refund.status,
            "provider_refund_id": refund.provider_refund_id,
        }

    except HTTPException:
        db.rollback()
        raise
    except (RazorpayError, RazorpayVerificationError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=502,
            detail="Unable to reconcile the refund with Razorpay. Its state remains unresolved.",
        ) from exc
    except (PermissionError, LookupError, ValueError, IntegrityError) as exc:
        db.rollback()
        _service_error(exc)


@router.post("/payments/{payment_attempt_id}/recover-booking")
def recover_captured_booking(
    payment_attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Retry booking confirmation for a previously captured payment."""
    account = resolve_session_account(request, db)
    if str(account.role or "").strip().casefold() != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")

    payment = db.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.id == payment_attempt_id
        )
    )
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment attempt not found.")

    if payment.status != "captured":
        raise HTTPException(
            status_code=409,
            detail="Only captured payments can enter booking recovery.",
        )

    if not payment.booking_id:
        raise HTTPException(
            status_code=409,
            detail="Captured payment is not linked to a booking.",
        )

    try:
        booking = confirm_booking_after_capture(
            db,
            booking_id=payment.booking_id,
            payment_attempt_id=payment.id,
        )
        db.commit()
        db.refresh(booking)
        db.refresh(payment)
        return {
            "booking_id": booking.id,
            "payment_attempt_id": payment.id,
            "booking_status": booking.status,
            "payment_status": payment.status,
            "message": "Booking confirmation recovery completed.",
        }
    except Exception as exc:
        db.rollback()
        _mark_booking_confirmation_failed(db, payment.id)
        raise HTTPException(
            status_code=409,
            detail=(
                "Booking recovery could not complete. The captured payment remains recorded; "
                "investigate the inventory hold and transaction state."
            ),
        ) from exc


class ReconciliationStatementLine(BaseModel):
    provider_transaction_id: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)


class ReconciliationImportPayload(BaseModel):
    period_start: datetime
    period_end: datetime
    statement_lines: list[ReconciliationStatementLine] = Field(max_length=10000)


def _require_payment_admin(request, db):
    account = resolve_session_account(request, db)
    if str(account.role or "").strip().casefold() != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return account


@router.post("/payments/reconciliation", status_code=201)
def import_payment_reconciliation(
    payload: ReconciliationImportPayload,
    request: Request,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=1, max_length=128
    ),
    db: Session = Depends(get_db),
):
    """Import a provider statement and persist its reconciliation results."""
    _require_payment_admin(request, db)
    try:
        batch = reconcile_provider_statement(
            db,
            provider="razorpay",
            period_start=payload.period_start,
            period_end=payload.period_end,
            statement_lines=[
                line.model_dump() for line in payload.statement_lines
            ],
            idempotency_key=idempotency_key,
        )
        db.commit()
        db.refresh(batch)
        return {
            "batch_id": batch.id,
            "provider": batch.provider,
            "status": batch.status,
            "period_start": batch.period_start,
            "period_end": batch.period_end,
            "statement_hash": batch.statement_hash,
            "idempotency_key": batch.idempotency_key,
        }
    except (PermissionError, LookupError, ValueError) as exc:
        db.rollback()
        _service_error(exc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A reconciliation import with this idempotency key is already being processed.",
        ) from exc


@router.get("/payments/reconciliation/{batch_id}")
def get_payment_reconciliation(
    batch_id: int,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Return admin-only reconciliation totals and a page of exception details."""
    _require_payment_admin(request, db)
    batch = db.scalar(
        select(PaymentReconciliationBatch).where(
            PaymentReconciliationBatch.id == batch_id
        )
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="Reconciliation batch not found.")

    totals = db.execute(
        select(
            PaymentReconciliationItem.outcome,
            func.count(PaymentReconciliationItem.id),
        )
        .where(PaymentReconciliationItem.batch_id == batch_id)
        .group_by(PaymentReconciliationItem.outcome)
    ).all()

    total_items = db.scalar(
        select(func.count(PaymentReconciliationItem.id)).where(
            PaymentReconciliationItem.batch_id == batch_id
        )
    ) or 0

    items = db.scalars(
        select(PaymentReconciliationItem)
        .where(PaymentReconciliationItem.batch_id == batch_id)
        .order_by(PaymentReconciliationItem.id)
        .limit(limit)
        .offset(offset)
    ).all()

    return {
        "batch": {
            "id": batch.id,
            "provider": batch.provider,
            "status": batch.status,
            "period_start": batch.period_start,
            "period_end": batch.period_end,
            "statement_hash": batch.statement_hash,
            "idempotency_key": batch.idempotency_key,
        },
        "total_items": total_items,
        "outcomes": {outcome: count for outcome, count in totals},
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": item.id,
                "payment_attempt_id": item.payment_attempt_id,
                "provider_transaction_id": item.provider_transaction_id,
                "outcome": item.outcome,
                "expected_amount": (
                    str(item.expected_amount) if item.expected_amount is not None else None
                ),
                "actual_amount": (
                    str(item.actual_amount) if item.actual_amount is not None else None
                ),
                "currency": item.currency,
                "details": item.details,
            }
            for item in items
        ],
    }
