from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.transaction_service import (
    authorize_transaction_access,
    transition_transaction,
)


def make_account(account_id=1, role="buyer"):
    return SimpleNamespace(id=account_id, role=role)


def make_transaction(**overrides):
    values = {
        "id": 10,
        "buyer_account_id": 1,
        "channel_partner_account_id": 2,
        "developer_organization_id": None,
        "status": "initiated",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_buyer_can_access_own_transaction():
    db = MagicMock()
    transaction = make_transaction()

    authorize_transaction_access(db, transaction, make_account(), write=True)


def test_unrelated_account_cannot_access_transaction():
    db = MagicMock()
    db.scalar.return_value = None

    with pytest.raises(PermissionError):
        authorize_transaction_access(
            db, make_transaction(), make_account(99, "buyer")
        )


def test_buyer_can_cancel_own_transaction_and_event_is_recorded():
    db = MagicMock()
    transaction = make_transaction()
    db.scalar.return_value = transaction

    result = transition_transaction(
        db,
        make_account(),
        transaction_id=10,
        to_status="cancelled",
        details={"reason": "buyer_requested"},
    )

    assert result.status == "cancelled"
    event = db.add.call_args.args[0]
    assert event.transaction_id == 10
    assert event.actor_account_id == 1
    assert event.from_status == "initiated"
    assert event.to_status == "cancelled"
    assert event.details == {"reason": "buyer_requested"}
    db.flush.assert_called_once()


def test_invalid_transition_is_rejected():
    db = MagicMock()
    db.scalar.return_value = make_transaction(status="completed")

    with pytest.raises(ValueError, match="Invalid transaction transition"):
        transition_transaction(
            db,
            make_account(),
            transaction_id=10,
            to_status="negotiating",
        )

    db.add.assert_not_called()


def test_booking_transition_requires_booking_service():
    db = MagicMock()
    db.scalar.return_value = make_transaction(status="terms_agreed")

    with pytest.raises(ValueError, match="booking service"):
        transition_transaction(
            db,
            make_account(99, "admin"),
            transaction_id=10,
            to_status="booking_pending",
        )

    db.add.assert_not_called()



def test_terms_agreed_requires_offer_acceptance():
    db = MagicMock()
    db.scalar.return_value = make_transaction(status="negotiating")

    with pytest.raises(ValueError, match="offer acceptance"):
        transition_transaction(
            db, make_account(), transaction_id=10,
            to_status="terms_agreed",
        )

    db.add.assert_not_called()
    db.flush.assert_not_called()
