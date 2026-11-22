from types import SimpleNamespace

import pytest

from backend.app import transactions


def make_transaction():
    return SimpleNamespace(
        id=10,
        buyer_account_id=1,
        property_listing_id=20,
        inventory_item_id=30,
        developer_organization_id=40,
        channel_partner_assignment_id=None,
        channel_partner_account_id=None,
        transaction_type="negotiation",
        status="initiated",
        created_at=None,
        updated_at=None,
    )


def test_transaction_endpoints_require_authentication(client):
    assert client.post(
        "/api/transactions",
        json={
            "property_listing_id": 20,
            "inventory_item_id": 30,
            "transaction_type": "negotiation",
        },
    ).status_code == 401

    assert client.get("/api/transactions/10").status_code == 401
    assert client.patch(
        "/api/transactions/10/status",
        json={"to_status": "cancelled"},
    ).status_code == 401


def test_create_transaction_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        transactions,
        "resolve_session_account",
        lambda request, db: SimpleNamespace(id=1, role="buyer"),
    )
    monkeypatch.setattr(
        transactions,
        "create_transaction",
        lambda *args, **kwargs: make_transaction(),
    )

    response = client.post(
        "/api/transactions",
        json={
            "property_listing_id": 20,
            "inventory_item_id": 30,
            "transaction_type": "negotiation",
        },
    )

    assert response.status_code == 201
    assert response.json()["id"] == 10
    assert response.json()["status"] == "initiated"


def test_get_transaction_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        transactions,
        "resolve_session_account",
        lambda request, db: SimpleNamespace(id=1, role="buyer"),
    )
    monkeypatch.setattr(
        transactions,
        "get_transaction",
        lambda db, account, transaction_id: make_transaction(),
    )

    response = client.get("/api/transactions/10")

    assert response.status_code == 200
    assert response.json()["id"] == 10
    assert response.json()["events"] == []


def test_update_transaction_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        transactions,
        "resolve_session_account",
        lambda request, db: SimpleNamespace(id=1, role="buyer"),
    )
    monkeypatch.setattr(
        transactions,
        "transition_transaction",
        lambda *args, **kwargs: make_transaction_with_status("cancelled"),
    )

    response = client.patch(
        "/api/transactions/10/status",
        json={
            "to_status": "cancelled",
            "details": {"reason": "buyer_requested"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def make_transaction_with_status(status):
    transaction = make_transaction()
    transaction.status = status
    return transaction



def make_offer(**overrides):
    values = {
        "id": 101,
        "transaction_id": 10,
        "proposer_account_id": 1,
        "parent_offer_id": None,
        "amount": "1250000.00",
        "currency": "INR",
        "status": "pending",
        "expires_at": None,
        "responded_at": None,
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def mock_offer_auth(monkeypatch):
    monkeypatch.setattr(
        transactions,
        "resolve_session_account",
        lambda request, db: SimpleNamespace(id=1, role="buyer"),
    )


def test_create_offer_endpoint(client, monkeypatch):
    mock_offer_auth(monkeypatch)
    monkeypatch.setattr(
        transactions, "create_offer",
        lambda *args, **kwargs: make_offer(),
    )

    response = client.post(
        "/api/transactions/10/offers",
        json={"amount": "1250000.00", "currency": "INR"},
    )

    assert response.status_code == 201
    assert response.json()["id"] == 101
    assert response.json()["amount"] == "1250000.00"
    assert response.json()["status"] == "pending"


def test_list_offers_endpoint(client, monkeypatch):
    mock_offer_auth(monkeypatch)
    monkeypatch.setattr(
        transactions, "list_offers",
        lambda *args: [make_offer()],
    )

    response = client.get("/api/transactions/10/offers")

    assert response.status_code == 200
    assert len(response.json()["offers"]) == 1
    assert response.json()["offers"][0]["id"] == 101


def test_respond_to_offer_endpoint(client, monkeypatch):
    mock_offer_auth(monkeypatch)
    monkeypatch.setattr(
        transactions, "respond_to_offer",
        lambda *args, **kwargs: make_offer(status="countered"),
    )

    response = client.post(
        "/api/transactions/10/offers/101/respond",
        json={"action": "counter", "amount": "1300000.00"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "countered"


def test_withdraw_offer_endpoint(client, monkeypatch):
    mock_offer_auth(monkeypatch)
    monkeypatch.setattr(
        transactions, "withdraw_offer",
        lambda *args: make_offer(status="withdrawn"),
    )

    response = client.post("/api/transactions/10/offers/101/withdraw")

    assert response.status_code == 200
    assert response.json()["status"] == "withdrawn"


def test_offer_endpoints_require_authentication(client):
    assert client.post(
        "/api/transactions/10/offers",
        json={"amount": "100.00"},
    ).status_code == 401
    assert client.get("/api/transactions/10/offers").status_code == 401
