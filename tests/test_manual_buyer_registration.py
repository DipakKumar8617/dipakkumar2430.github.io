from sqlalchemy import select

from backend.app.database import SessionLocal
from backend.app.models import BuyerEnquiry, UserAccount


def registration_payload(**overrides):
    payload = {
        "fullName": "Test Buyer",
        "phone": "+919876543210",
        "email": "buyer.test@example.com",
        "password": "TestPassword123!",
        "confirmPassword": "TestPassword123!",
        "role": "buyer",
        "property_id": 2,
    }
    payload.update(overrides)
    return payload


def test_manual_buyer_registration_requires_login(client):
    response = client.post(
        "/api/register",
        json=registration_payload(),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["redirect"] == (
        "/index.html?next=%2Fbuyer&registered=1&property_id=2#login"
    )
    assert "user" not in data

    # Registration must not authenticate the new account.
    session = client.get("/api/session").json()
    assert session["authenticated"] is False

    with SessionLocal() as db:
        account = db.scalar(
            select(UserAccount).where(
                UserAccount.email == "buyer.test@example.com"
            )
        )
        assert account is not None
        assert account.full_name == "Test Buyer"
        assert account.password_hash != "TestPassword123!"
        assert account.password_hash.startswith("scrypt$")

    # Only a successful password login should create an authenticated session.
    login = client.post(
        "/api/auth/password/login",
        json={
            "identifier": "buyer.test@example.com",
            "password": "TestPassword123!",
            "property_id": 2,
        },
    )
    assert login.status_code == 200
    assert login.json()["redirect"] == "/buyer?property_id=2"

    session = client.get("/api/session").json()
    assert session["authenticated"] is True
    assert session["user"]["provider"] == "local_password"


def test_manual_registration_rejects_duplicate_email(client):
    first = client.post("/api/register", json=registration_payload())
    second = client.post(
        "/api/register",
        json=registration_payload(phone="+919876543219"),
    )

    assert first.status_code == 201
    assert second.status_code == 409


def test_manual_registration_rejects_password_mismatch(client):
    response = client.post(
        "/api/register",
        json=registration_payload(confirmPassword="DifferentPassword123!"),
    )

    assert response.status_code == 422
    assert "Passwords do not match" in response.json()["detail"]


def test_manual_registration_rejects_non_buyer_roles(client):
    response = client.post(
        "/api/register",
        json=registration_payload(role="admin"),
    )

    assert response.status_code == 403


def test_manual_registration_rejects_invalid_email(client):
    response = client.post(
        "/api/register",
        json=registration_payload(email="not-an-email"),
    )

    assert response.status_code == 422


def test_registration_links_matching_visitor_enquiry_without_signing_in(client):
    enquiry_response = client.post(
        "/api/buyer/enquiries",
        json={
            "full_name": "Test Buyer",
            "email": " BUYER.TEST@EXAMPLE.COM ",
            "phone": "+91 98765 43210",
            "city": "Kolkata",
            "state": "West Bengal",
        },
    )
    assert enquiry_response.status_code == 201
    enquiry_id = enquiry_response.json()["enquiry_id"]

    registration_response = client.post(
        "/api/register",
        json=registration_payload(email="buyer.test@example.com"),
    )
    assert registration_response.status_code == 201

    # Linking an enquiry must not authenticate the newly registered buyer.
    session = client.get("/api/session").json()
    assert session["authenticated"] is False

    with SessionLocal() as db:
        enquiry = db.get(BuyerEnquiry, enquiry_id)
        account = db.scalar(
            select(UserAccount).where(
                UserAccount.email == "buyer.test@example.com"
            )
        )

        assert enquiry is not None
        assert account is not None
        assert enquiry.account_id == account.id


def test_registration_does_not_link_enquiry_with_different_email(client):
    enquiry_response = client.post(
        "/api/buyer/enquiries",
        json={
            "full_name": "Visitor Buyer",
            "email": "visitor@example.com",
            "phone": "+91 98765 43210",
            "city": "Kolkata",
            "state": "West Bengal",
        },
    )
    assert enquiry_response.status_code == 201
    enquiry_id = enquiry_response.json()["enquiry_id"]

    registration_response = client.post(
        "/api/register",
        json=registration_payload(email="buyer.test@example.com"),
    )
    assert registration_response.status_code == 201

    with SessionLocal() as db:
        enquiry = db.get(BuyerEnquiry, enquiry_id)
        assert enquiry is not None
        assert enquiry.account_id is None

    session = client.get("/api/session").json()
    assert session["authenticated"] is False