from decimal import Decimal

import pytest

from backend.app.transaction_offer_service import _amount

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from backend.app.database import Base, SessionLocal, engine
from backend.app.models import (
    UserAccount,
    PropertyListing,
    PropertyInventoryItem,
    RealEstateTransaction,
    TransactionOffer,
)
from backend.app import transaction_offer_service as offer_service


def make_offer_test_data(db):
    buyer = UserAccount()
    responder = UserAccount()
    db.add_all([buyer, responder])
    db.flush()

    listing = PropertyListing(
        title="Offer Test Property",
        property_type="apartment",
        city="Pune",
        state="Maharashtra",
    )
    db.add(listing)
    db.flush()

    item = PropertyInventoryItem(
        property_listing_id=listing.id,
        item_type="single",
    )
    db.add(item)
    db.flush()

    transaction = RealEstateTransaction(
        buyer_account_id=buyer.id,
        property_listing_id=listing.id,
        inventory_item_id=item.id,
        transaction_type="negotiation",
        status="negotiating",
    )
    db.add(transaction)
    db.commit()

    return transaction, buyer, responder


def test_offer_lifecycle_persists_in_database(client, monkeypatch):
    monkeypatch.setattr(
        offer_service,
        "authorize_transaction_access",
        lambda *args, **kwargs: None,
    )

    db = SessionLocal()
    try:
        transaction, buyer, responder = make_offer_test_data(db)

        offer = offer_service.create_offer(
            db,
            transaction.id,
            buyer,
            "125000",
        )
        db.commit()

        saved_offer = db.get(TransactionOffer, offer.id)
        assert saved_offer.status == "pending"

        with pytest.raises(ValueError, match="pending"):
            offer_service.create_offer(
                db,
                transaction.id,
                buyer,
                "130000",
            )
        db.rollback()

        accepted = offer_service.respond_to_offer(
            db,
            transaction.id,
            offer.id,
            responder,
            "accept",
        )
        db.commit()

        assert db.get(TransactionOffer, offer.id).status == "accepted"
        assert db.get(RealEstateTransaction, transaction.id).status == "terms_agreed"

    finally:
        db.close()


def test_counteroffer_preserves_parent_child_history(client, monkeypatch):
    monkeypatch.setattr(
        offer_service,
        "authorize_transaction_access",
        lambda *args, **kwargs: None,
    )

    db = SessionLocal()
    try:
        transaction, buyer, responder = make_offer_test_data(db)

        original = offer_service.create_offer(
            db, transaction.id, buyer, "125000"
        )
        db.commit()

        counter = offer_service.respond_to_offer(
            db,
            transaction.id,
            original.id,
            responder,
            "counter",
            amount="135000",
        )
        db.commit()

        assert db.get(TransactionOffer, original.id).status == "countered"
        assert counter.parent_offer_id == original.id
        assert counter.status == "pending"

    finally:
        db.close()


def test_withdraw_offer_persists(client, monkeypatch):
    monkeypatch.setattr(
        offer_service,
        "authorize_transaction_access",
        lambda *args, **kwargs: None,
    )

    db = SessionLocal()
    try:
        transaction, buyer, _ = make_offer_test_data(db)

        offer = offer_service.create_offer(
            db, transaction.id, buyer, "125000"
        )
        db.commit()

        offer_service.withdraw_offer(
            db, transaction.id, offer.id, buyer
        )
        db.commit()

        assert db.get(TransactionOffer, offer.id).status == "withdrawn"

    finally:
        db.close()


def test_expired_offer_state_persists(client, monkeypatch):
    monkeypatch.setattr(
        offer_service,
        "authorize_transaction_access",
        lambda *args, **kwargs: None,
    )

    db = SessionLocal()
    try:
        transaction, buyer, responder = make_offer_test_data(db)

        offer = offer_service.create_offer(
            db, transaction.id, buyer, "125000"
        )
        offer.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()

        result = offer_service.respond_to_offer(
            db,
            transaction.id,
            offer.id,
            responder,
            "accept",
        )
        db.commit()

        assert result.status == "expired"
        assert db.get(TransactionOffer, offer.id).status == "expired"

    finally:
        db.close()


def test_database_rejects_second_pending_offer(client):
    db = SessionLocal()
    try:
        transaction, buyer, _ = make_offer_test_data(db)

        first = TransactionOffer(
            transaction_id=transaction.id,
            proposer_account_id=buyer.id,
            amount=Decimal("125000.00"),
        )
        db.add(first)
        db.commit()

        second = TransactionOffer(
            transaction_id=transaction.id,
            proposer_account_id=buyer.id,
            amount=Decimal("130000.00"),
        )
        db.add(second)

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()

    finally:
        db.close()


@pytest.mark.parametrize(
    "value",
    [0, -1, "0", "-12.50", "NaN", "Infinity", "not-a-number", None],
)
def test_offer_amount_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        _amount(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("100", Decimal("100.00")),
        ("1250.5", Decimal("1250.50")),
        (Decimal("999.99"), Decimal("999.99")),
    ],
)
def test_offer_amount_uses_two_decimal_places(value, expected):
    assert _amount(value) == expected
