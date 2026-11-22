from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .booking_service import create_booking
from .auth_dependencies import resolve_session_account
from .database import get_db
from .models import TransactionEvent
from .transaction_offer_service import (
    create_offer,
    list_offers,
    respond_to_offer,
    withdraw_offer,
)
from .transaction_service import (
    create_transaction,
    get_transaction,
    transition_transaction,
)



class BookingCreatePayload(BaseModel):
    inventory_hold_id: int = Field(gt=0)

router = APIRouter(prefix="/api/transactions", tags=["Transactions"])


class TransactionCreate(BaseModel):
    property_listing_id: int
    inventory_item_id: int
    transaction_type: str


class TransactionStatusUpdate(BaseModel):
    to_status: str
    details: dict = Field(default_factory=dict)


class OfferCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    expires_at: datetime | None = None


class OfferResponse(BaseModel):
    action: str
    amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    expires_at: datetime | None = None


def _transaction_data(transaction):
    return {
        "id": transaction.id,
        "buyer_account_id": transaction.buyer_account_id,
        "property_listing_id": transaction.property_listing_id,
        "inventory_item_id": transaction.inventory_item_id,
        "developer_organization_id": transaction.developer_organization_id,
        "channel_partner_assignment_id": transaction.channel_partner_assignment_id,
        "channel_partner_account_id": transaction.channel_partner_account_id,
        "transaction_type": transaction.transaction_type,
        "status": transaction.status,
        "created_at": transaction.created_at,
        "updated_at": transaction.updated_at,
    }


def _offer_data(offer):
    return {
        "id": offer.id,
        "transaction_id": offer.transaction_id,
        "proposer_account_id": offer.proposer_account_id,
        "parent_offer_id": offer.parent_offer_id,
        "amount": str(offer.amount),
        "currency": offer.currency,
        "status": offer.status,
        "expires_at": offer.expires_at,
        "responded_at": offer.responded_at,
        "created_at": offer.created_at,
        "updated_at": offer.updated_at,
    }


def _raise_service_error(exc):
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.post("", status_code=201)
def create_transaction_endpoint(
    payload: TransactionCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    account = resolve_session_account(request, db)
    try:
        transaction = create_transaction(
            db,
            account,
            property_listing_id=payload.property_listing_id,
            inventory_item_id=payload.inventory_item_id,
            transaction_type=payload.transaction_type,
        )
        db.commit()
        return _transaction_data(transaction)
    except (PermissionError, LookupError, ValueError) as exc:
        db.rollback()
        _raise_service_error(exc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The transaction could not be created because the requested data conflicts with current records.",
        ) from exc


@router.get("/{transaction_id}")
def get_transaction_endpoint(
    transaction_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    account = resolve_session_account(request, db)
    try:
        transaction = get_transaction(db, account, transaction_id)
        events = db.scalars(
            select(TransactionEvent)
            .where(TransactionEvent.transaction_id == transaction.id)
            .order_by(TransactionEvent.created_at.asc(), TransactionEvent.id.asc())
        ).all()

        result = _transaction_data(transaction)
        result["events"] = [
            {
                "id": event.id,
                "actor_account_id": event.actor_account_id,
                "event_type": event.event_type,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "details": event.details,
                "created_at": event.created_at,
            }
            for event in events
        ]
        return result
    except (PermissionError, LookupError, ValueError) as exc:
        _raise_service_error(exc)


@router.patch("/{transaction_id}/status")
def update_transaction_status_endpoint(
    transaction_id: int,
    payload: TransactionStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    account = resolve_session_account(request, db)
    try:
        transaction = transition_transaction(
            db,
            account,
            transaction_id=transaction_id,
            to_status=payload.to_status,
            details=payload.details,
        )
        db.commit()
        return _transaction_data(transaction)
    except (PermissionError, LookupError, ValueError) as exc:
        db.rollback()
        _raise_service_error(exc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="The transaction update conflicts with current records.",
        ) from exc



@router.post("/{transaction_id}/offers", status_code=201)
def create_offer_endpoint(
    transaction_id: int,
    payload: OfferCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    account = resolve_session_account(request, db)
    try:
        offer = create_offer(
            db,
            transaction_id,
            account,
            payload.amount,
            currency=payload.currency,
            expires_at=payload.expires_at,
        )
        db.commit()
        return _offer_data(offer)
    except (PermissionError, LookupError, ValueError) as exc:
        db.rollback()
        _raise_service_error(exc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Offer conflicts with an existing pending offer.") from exc


@router.get("/{transaction_id}/offers")
def list_offers_endpoint(
    transaction_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    account = resolve_session_account(request, db)
    try:
        offers = list_offers(db, transaction_id, account)
        return {"offers": [_offer_data(offer) for offer in offers]}
    except (PermissionError, LookupError, ValueError) as exc:
        _raise_service_error(exc)


@router.post("/{transaction_id}/offers/{offer_id}/respond")
def respond_to_offer_endpoint(
    transaction_id: int,
    offer_id: int,
    payload: OfferResponse,
    request: Request,
    db: Session = Depends(get_db),
):
    account = resolve_session_account(request, db)
    try:
        offer = respond_to_offer(
            db,
            transaction_id,
            offer_id,
            account,
            payload.action,
            amount=payload.amount,
            currency=payload.currency,
            expires_at=payload.expires_at,
        )
        db.commit()
        if offer.status == "expired":
            raise HTTPException(status_code=409, detail="Offer has expired.")
        return _offer_data(offer)
    except (PermissionError, LookupError, ValueError) as exc:
        db.rollback()
        _raise_service_error(exc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Offer response conflicts with current records.") from exc


@router.post("/{transaction_id}/offers/{offer_id}/withdraw")
def withdraw_offer_endpoint(
    transaction_id: int,
    offer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    account = resolve_session_account(request, db)
    try:
        offer = withdraw_offer(db, transaction_id, offer_id, account)
        db.commit()
        return _offer_data(offer)
    except (PermissionError, LookupError, ValueError) as exc:
        db.rollback()
        _raise_service_error(exc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Offer withdrawal conflicts with current records.") from exc


@router.post("/{transaction_id}/booking")
def create_booking_endpoint(
    transaction_id: int,
    payload: BookingCreatePayload,
    request: Request,
    db: Session = Depends(get_db),
):
    account = resolve_session_account(request, db)

    if str(account.role or "").strip().casefold() != "buyer":
        raise HTTPException(
            status_code=403,
            detail="Only the transaction buyer can create a booking.",
        )

    try:
        booking = create_booking(
            db,
            transaction_id=transaction_id,
            buyer_account_id=account.id,
            inventory_hold_id=payload.inventory_hold_id,
        )
        db.commit()
        db.refresh(booking)
        return {
            "id": booking.id,
            "transaction_id": booking.transaction_id,
            "inventory_item_id": booking.inventory_item_id,
            "inventory_hold_id": booking.inventory_hold_id,
            "buyer_account_id": booking.buyer_account_id,
            "agreed_amount": str(booking.agreed_amount),
            "currency": booking.currency,
            "status": booking.status,
            "expires_at": booking.expires_at.isoformat(),
        }
    except (PermissionError, LookupError, ValueError) as exc:
        db.rollback()
        _raise_service_error(exc)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Booking conflicts with an existing booking or inventory hold.",
        ) from exc
    except Exception:
        db.rollback()
        raise
