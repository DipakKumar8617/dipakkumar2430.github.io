from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from .models import RealEstateTransaction, TransactionEvent, TransactionOffer
from .transaction_service import authorize_transaction_access


OFFERABLE_STATUSES = {"initiated", "under_review", "negotiating"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(value):
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _amount(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Invalid offer amount.") from None
    if not result.is_finite() or result <= 0:
        raise ValueError("Offer amount must be greater than zero.")
    return result.quantize(Decimal("0.01"))


def _event(db, transaction, actor, event_type, details=None,
           from_status=None, to_status=None):
    db.add(TransactionEvent(
        transaction_id=transaction.id,
        actor_account_id=actor.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        details=details or {},
    ))


def _transaction(db, transaction_id):
    result = db.execute(
        select(RealEstateTransaction)
        .where(RealEstateTransaction.id == transaction_id)
        .with_for_update()
    ).scalar_one_or_none()
    if result is None:
        raise LookupError("Transaction not found.")
    return result


def _pending_offer(db, transaction, actor):
    offer = db.execute(
        select(TransactionOffer)
        .where(
            TransactionOffer.transaction_id == transaction.id,
            TransactionOffer.status == "pending",
        )
        .with_for_update()
    ).scalar_one_or_none()

    if offer and offer.expires_at and _naive(offer.expires_at) <= _now():
        offer.status = "expired"
        offer.responded_at = _now()
        _event(db, transaction, actor, "offer_expired", {"offer_id": offer.id})
        db.flush()
        return None

    return offer


def _validate_expiry(expires_at):
    if expires_at is not None and _naive(expires_at) <= _now():
        raise ValueError("Offer expiry must be in the future.")


def create_offer(
    db, transaction_id, actor, amount, currency="INR",
    expires_at=None, parent_offer_id=None,
):
    transaction = _transaction(db, transaction_id)
    authorize_transaction_access(db, transaction, actor, write=True)

    if transaction.transaction_type != "negotiation":
        raise ValueError("Offers require a negotiation transaction.")
    if transaction.status not in OFFERABLE_STATUSES:
        raise ValueError("Transaction is not open for negotiation.")

    currency = (currency or "INR").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Currency must be a three-letter code.")

    value = _amount(amount)
    _validate_expiry(expires_at)

    if _pending_offer(db, transaction, actor):
        raise ValueError("A pending offer already exists.")

    if parent_offer_id is not None:
        parent = db.execute(
            select(TransactionOffer)
            .where(
                TransactionOffer.id == parent_offer_id,
                TransactionOffer.transaction_id == transaction.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if parent is None:
            raise LookupError("Parent offer not found.")
        if parent.status != "countered":
            raise ValueError("Parent offer must have been countered.")

    offer = TransactionOffer(
        transaction_id=transaction.id,
        proposer_account_id=actor.id,
        parent_offer_id=parent_offer_id,
        amount=value,
        currency=currency,
        status="pending",
        expires_at=expires_at,
    )
    db.add(offer)

    if transaction.status in {"initiated", "under_review"}:
        previous = transaction.status
        transaction.status = "negotiating"
        _event(
            db, transaction, actor, "transaction_negotiating",
            {"reason": "offer_created"}, previous, "negotiating",
        )

    _event(db, transaction, actor, "offer_created", {
        "amount": str(value),
        "currency": currency,
        "parent_offer_id": parent_offer_id,
    })
    db.flush()
    return offer


def respond_to_offer(
    db, transaction_id, offer_id, actor, action,
    amount=None, currency=None, expires_at=None,
):
    transaction = _transaction(db, transaction_id)
    authorize_transaction_access(db, transaction, actor, write=True)

    offer = db.execute(
        select(TransactionOffer)
        .where(
            TransactionOffer.id == offer_id,
            TransactionOffer.transaction_id == transaction.id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if offer is None:
        raise LookupError("Offer not found.")
    if offer.status != "pending":
        raise ValueError("Offer is no longer pending.")
    if offer.proposer_account_id == actor.id:
        raise PermissionError("You cannot respond to your own offer.")

    if transaction.status not in OFFERABLE_STATUSES:
        raise ValueError("Transaction is no longer open for negotiation.")



    if offer.expires_at and _naive(offer.expires_at) <= _now():
        offer.status = "expired"
        offer.responded_at = _now()
        _event(db, transaction, actor, "offer_expired", {"offer_id": offer.id})
        db.flush()
        return offer

    action = (action or "").strip().lower()
    if action not in {"accept", "reject", "counter"}:
        raise ValueError("Action must be accept, reject, or counter.")

    if action == "counter":
        value = _amount(amount)
        next_currency = (currency or offer.currency).strip().upper()
        if len(next_currency) != 3 or not next_currency.isalpha():
            raise ValueError("Currency must be a three-letter code.")
        _validate_expiry(expires_at)

        offer.status = "countered"
        offer.responded_at = _now()

        counter = TransactionOffer(
            transaction_id=transaction.id,
            proposer_account_id=actor.id,
            parent_offer_id=offer.id,
            amount=value,
            currency=next_currency,
            status="pending",
            expires_at=expires_at,
        )
        db.add(counter)
        _event(db, transaction, actor, "offer_countered", {
            "offer_id": offer.id,
            "amount": str(value),
            "currency": next_currency,
        })
        db.flush()
        return counter

    offer.status = "accepted" if action == "accept" else "rejected"
    offer.responded_at = _now()

    if action == "accept":
        previous = transaction.status
        transaction.status = "terms_agreed"
        _event(
            db, transaction, actor, "offer_accepted",
            {"offer_id": offer.id, "amount": str(offer.amount),
             "currency": offer.currency},
            previous, "terms_agreed",
        )
    else:
        _event(db, transaction, actor, "offer_rejected",
               {"offer_id": offer.id})

    db.flush()
    return offer


def withdraw_offer(db, transaction_id, offer_id, actor):
    transaction = _transaction(db, transaction_id)
    authorize_transaction_access(db, transaction, actor, write=True)

    offer = db.execute(
        select(TransactionOffer)
        .where(
            TransactionOffer.id == offer_id,
            TransactionOffer.transaction_id == transaction.id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if offer is None:
        raise LookupError("Offer not found.")
    if offer.proposer_account_id != actor.id:
        raise PermissionError("Only the proposer can withdraw an offer.")
    if transaction.status not in OFFERABLE_STATUSES:
        raise ValueError("Transaction is no longer open for negotiation.")
    if offer.status != "pending":
        raise ValueError("Only pending offers can be withdrawn.")


    offer.status = "withdrawn"
    offer.responded_at = _now()
    _event(db, transaction, actor, "offer_withdrawn",
           {"offer_id": offer.id})
    db.flush()
    return offer


def list_offers(db, transaction_id, actor):
    transaction = db.get(RealEstateTransaction, transaction_id)
    if transaction is None:
        raise LookupError("Transaction not found.")
    authorize_transaction_access(db, transaction, actor, write=False)

    return db.execute(
        select(TransactionOffer)
        .where(TransactionOffer.transaction_id == transaction.id)
        .order_by(TransactionOffer.created_at, TransactionOffer.id)
    ).scalars().all()
