from sqlalchemy import select
from backend.app import main
from backend.app.database import SessionLocal
from backend.app.models import UserAccount


def configure_google(monkeypatch, email, subject):
    monkeypatch.setattr(main, "GOOGLE_CLIENT_ID", "test-google-client-id")

    claims = {
        "email": email,
        "sub": subject,
        "name": "Canonical Buyer",
        "email_verified": True,
    }

    def fake_verify(credential, request, audience):
        assert credential == "valid-test-google-credential"
        return claims

    monkeypatch.setattr(main.id_token, "verify_oauth2_token", fake_verify)


def login_with_google(client, monkeypatch, email, subject):
    configure_google(monkeypatch, email, subject)

    return client.post(
        "/api/auth/google",
        json={
            "credential": "valid-test-google-credential",
            "purpose": "buyer",
        },
    )


def login_with_buyer_otp(client, phone):
    response = client.post(
        "/api/auth/otp/request",
        json={"phone": phone, "purpose": "buyer"},
    )
    assert response.status_code == 200

    code = response.json()["demo_code"]

    response = client.post(
        "/api/auth/otp/verify",
        json={"phone": phone, "code": code},
    )
    assert response.status_code == 200

    return response


def test_google_identity_resolves_same_canonical_account(client, monkeypatch):
    first = login_with_google(
        client,
        monkeypatch,
        "canonical.google@example.com",
        "google-canonical-subject-001",
    )

    assert first.status_code == 200
    first_id = first.json()["user"].get("account_id")
    assert isinstance(first_id, int)

    client.post("/api/logout")

    second = login_with_google(
        client,
        monkeypatch,
        "canonical.google@example.com",
        "google-canonical-subject-001",
    )

    assert second.status_code == 200
    assert second.json()["user"]["account_id"] == first_id

    with SessionLocal() as db:
        accounts = db.scalars(
            select(UserAccount).where(UserAccount.id == first_id)
        ).all()
        assert len(accounts) == 1


def test_google_email_match_does_not_automatically_merge_accounts(
    client, monkeypatch
):
    with SessionLocal() as db:
        existing = UserAccount(
            full_name="Password Buyer",
            email="existing.buyer@example.com",
            phone="919876543210",
            password_hash=main.hash_password("ExistingPassword123!"),
            role="buyer",
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)
        existing_id = existing.id

    response = login_with_google(
        client,
        monkeypatch,
        "existing.buyer@example.com",
        "different-google-subject-002",
    )

    assert response.status_code == 409
    assert client.get("/api/session").json()["authenticated"] is False

    with SessionLocal() as db:
        accounts = db.scalars(select(UserAccount)).all()
        assert len(accounts) == 1
        assert accounts[0].id == existing_id


def test_buyer_otp_resolves_same_canonical_account(client):
    phone = "+919876543220"

    first = login_with_buyer_otp(client, phone)
    first_id = first.json()["user"].get("account_id")

    assert isinstance(first_id, int)

    client.post("/api/logout")

    # Clear the development-only resend cooldown for this test.
    main.otp_records.pop("919876543220", None)

    second = login_with_buyer_otp(client, phone)

    assert second.status_code == 200
    assert second.json()["user"]["account_id"] == first_id


def test_unassigned_google_login_does_not_create_buyer_account(
    client, monkeypatch
):
    configure_google(
        monkeypatch,
        "unassigned@example.com",
        "google-unassigned-subject-003",
    )

    response = client.post(
        "/api/auth/google",
        json={"credential": "valid-test-google-credential"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "Unassigned"
    assert response.json()["user"].get("account_id") is None
    assert client.get("/api/buyer/profile").status_code == 403
