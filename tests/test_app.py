from backend.app import main
from backend.app.database import engine
from backend.app.models import AuthIdentity, UserAccount
from sqlalchemy.orm import Session
from starlette.requests import Request


def test_public_pages_and_api_routes(client):
    paths = [
        "/",
        "/index.html",
        "/otp-login.html",
        "/register.html",
        "/forgot-password.html",
        "/buyer-enquiry.html",
        "/styles.css",
        "/api/auth/config",
        "/api/session",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, (
            f"{path} returned {response.status_code}"
        )


def test_favicon_redirect_and_svg(client):
    response = client.get("/favicon.ico", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/favicon.svg"

    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text


def test_dashboard_requires_authentication(client):
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/index.html#login"


def test_otp_login_dashboard_and_logout(client):
    phone = "+919876543211"

    response = client.post(
        "/api/auth/otp/request",
        json={"phone": phone},
    )
    assert response.status_code == 200
    code = response.json().get("demo_code")
    assert code and len(code) == 6 and code.isdigit()

    response = client.post(
        "/api/auth/otp/verify",
        json={"phone": phone, "code": code},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    session = client.get("/api/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.json()["user"]["role"] == "Unassigned"

    dashboard = client.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code == 403

    logout = client.post("/api/logout")
    assert logout.status_code == 200
    assert logout.json()["success"] is True

    session = client.get("/api/session")
    assert session.json()["authenticated"] is False

    dashboard = client.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code == 303
    assert dashboard.headers["location"] == "/index.html#login"


def test_otp_is_disabled_outside_development(client, monkeypatch):
    monkeypatch.setattr(main, "APP_ENV", "production")

    response = client.post(
        "/api/auth/otp/request",
        json={"phone": "+919876543211"},
    )
    assert response.status_code == 403


def _make_staff_test_request(account_id, role, phone):
    return Request(
        {
            "type": "http",
            "headers": [],
            "session": {
                "user": {
                    "account_id": account_id,
                    "provider": "local_otp",
                    "phone": phone,
                    "role": role,
                }
            },
        }
    )


def test_staff_authorization_uses_database_role_not_session_role(client):
    with Session(engine) as db:
        account = UserAccount(
            full_name="Test Developer",
            email="test-developer@example.test",
            phone="919876543210",
            password_hash=None,
            role="developer",
        )
        db.add(account)
        db.flush()

        db.add(
            AuthIdentity(
                account_id=account.id,
                provider="local_otp",
                provider_subject="919876543210",
            )
        )
        db.commit()

        request = _make_staff_test_request(
            account.id,
            "Buyer",
            "919876543210",
        )

        authenticated = main.authenticated_staff_account(request, db)

        assert authenticated.id == account.id
        assert request.session["user"]["role"] == "Developer"


def test_staff_authorization_rejects_buyer_even_if_session_claims_developer(client):
    with Session(engine) as db:
        account = UserAccount(
            full_name="Test Buyer",
            email="test-buyer@example.test",
            phone="919876543212",
            password_hash=None,
            role="buyer",
        )
        db.add(account)
        db.flush()

        db.add(
            AuthIdentity(
                account_id=account.id,
                provider="local_otp",
                provider_subject="919876543212",
            )
        )
        db.commit()

        request = _make_staff_test_request(
            account.id,
            "Developer",
            "919876543212",
        )

        from fastapi import HTTPException

        try:
            main.authenticated_staff_account(request, db)
            assert False, "Buyer account must not receive staff access."
        except HTTPException as exc:
            assert exc.status_code == 403


def test_staff_authorization_rejects_mismatched_session_account_id(client):
    with Session(engine) as db:
        account = UserAccount(
            full_name="Test Admin",
            email="test-admin@example.test",
            phone="919876543213",
            password_hash=None,
            role="admin",
        )
        db.add(account)
        db.flush()

        db.add(
            AuthIdentity(
                account_id=account.id,
                provider="local_otp",
                provider_subject="919876543213",
            )
        )
        db.commit()

        request = _make_staff_test_request(
            account.id + 100,
            "Admin",
            "919876543213",
        )

        from fastapi import HTTPException

        try:
            main.authenticated_staff_account(request, db)
            assert False, "Mismatched account ID must not receive access."
        except HTTPException as exc:
            assert exc.status_code == 401
