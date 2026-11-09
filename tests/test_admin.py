from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import engine
from backend.app.main import hash_password
from backend.app.models import AuthIdentity, StaffInvitation, UserAccount


TEST_PASSWORD = "StrongAdminPass123!"


def _create_account(role, email):
    with Session(engine) as db:
        account = UserAccount(
            full_name="Test Account",
            email=email,
            password_hash=hash_password(TEST_PASSWORD),
            role=role,
        )
        db.add(account)
        db.flush()

        db.add(
            AuthIdentity(
                account_id=account.id,
                provider="local_password",
                provider_subject=str(account.id),
            )
        )
        db.commit()
        return account.id


def _login_staff(client, role, email):
    response = client.post(
        "/api/login",
        json={
            "identifier": email,
            "password": TEST_PASSWORD,
            "role": role,
        },
    )
    assert response.status_code == 200
    return response


def _login_admin(client):
    _create_account("admin", "admin@example.test")
    return _login_staff(client, "Admin", "admin@example.test")


def _create_invitation(client, email="new-staff@example.test", role="developer"):
    response = client.post(
        "/api/admin/invitations",
        json={"email": email, "role": role},
    )
    assert response.status_code == 201

    data = response.json()
    token = parse_qs(urlsplit(data["invite_path"]).fragment)["token"][0]
    return data, token


def test_admin_api_requires_authentication(client):
    response = client.get("/api/admin/summary")
    assert response.status_code == 401


def test_developer_cannot_access_admin_api(client):
    _create_account("developer", "developer@example.test")
    _login_staff(client, "Developer", "developer@example.test")

    response = client.get("/api/admin/summary")
    assert response.status_code == 403

    response = client.post(
        "/api/admin/invitations",
        json={
            "email": "unauthorized@example.test",
            "role": "developer",
        },
    )
    assert response.status_code == 403


def test_buyer_cannot_access_admin_api(client):
    _create_account("buyer", "buyer@example.test")

    response = client.post(
        "/api/auth/password/login",
        json={
            "identifier": "buyer@example.test",
            "password": TEST_PASSWORD,
        },
    )
    assert response.status_code == 200

    response = client.get("/api/admin/summary")
    assert response.status_code == 403


def test_admin_summary_and_staff_directory_use_database(client):
    _login_admin(client)
    _create_account("developer", "developer@example.test")
    _create_account("channel partner", "partner@example.test")

    summary = client.get("/api/admin/summary")
    assert summary.status_code == 200
    assert summary.json() == {
        "developers": 1,
        "channel_partners": 1,
        "pending_invitations": 0,
    }

    staff = client.get("/api/admin/staff")
    assert staff.status_code == 200

    items = staff.json()["items"]
    assert len(items) == 2
    assert {item["email"] for item in items} == {
        "developer@example.test",
        "partner@example.test",
    }


def test_admin_creates_and_accepts_one_time_invitation(client):
    _login_admin(client)

    invitation, token = _create_invitation(client)

    assert invitation["email"] == "new-staff@example.test"
    assert invitation["role"] == "developer"
    assert invitation["expires_at"]

    token_hash = __import__("hashlib").sha256(
        token.encode("utf-8")
    ).hexdigest()

    with Session(engine) as db:
        stored = db.scalar(
            select(StaffInvitation).where(
                StaffInvitation.token_hash == token_hash
            )
        )
        assert stored is not None
        assert stored.token_hash != token

    inspection = client.post(
        "/api/staff-invitations/inspect",
        json={"token": token},
    )
    assert inspection.status_code == 200
    assert inspection.json()["email"] == "new-staff@example.test"
    assert inspection.json()["role"] == "developer"

    acceptance = client.post(
        "/api/staff-invitations/accept",
        json={
            "token": token,
            "full_name": "New Developer",
            "password": "StrongDeveloperPass123!",
        },
    )
    assert acceptance.status_code == 201
    assert acceptance.json()["success"] is True
    assert acceptance.json()["role"] == "developer"

    account_id = acceptance.json()["account_id"]

    with Session(engine) as db:
        account = db.get(UserAccount, account_id)
        assert account is not None
        assert account.email == "new-staff@example.test"
        assert account.role == "developer"
        assert account.password_hash

        identity = db.scalar(
            select(AuthIdentity).where(
                AuthIdentity.provider == "local_password",
                AuthIdentity.provider_subject == str(account_id),
            )
        )
        assert identity is not None
        assert identity.account_id == account_id

        stored_invitation = db.scalar(
            select(StaffInvitation).where(
                StaffInvitation.token_hash == token_hash
            )
        )
        assert stored_invitation.accepted_at is not None

    replay = client.post(
        "/api/staff-invitations/accept",
        json={
            "token": token,
            "full_name": "Another Person",
            "password": "StrongAnotherPass123!",
        },
    )
    assert replay.status_code == 410

    inspection = client.post(
        "/api/staff-invitations/inspect",
        json={"token": token},
    )
    assert inspection.status_code == 410

    staff = client.get("/api/admin/staff")
    assert staff.status_code == 200
    assert any(
        item["email"] == "new-staff@example.test"
        for item in staff.json()["items"]
    )


def test_admin_cannot_invite_existing_buyer(client):
    _login_admin(client)
    _create_account("buyer", "existing-buyer@example.test")

    response = client.post(
        "/api/admin/invitations",
        json={
            "email": "existing-buyer@example.test",
            "role": "developer",
        },
    )
    assert response.status_code == 409


def test_admin_cannot_create_admin_invitation(client):
    _login_admin(client)

    response = client.post(
        "/api/admin/invitations",
        json={
            "email": "another-admin@example.test",
            "role": "admin",
        },
    )
    assert response.status_code == 422


def test_duplicate_pending_invitation_is_rejected(client):
    _login_admin(client)

    _create_invitation(client)

    response = client.post(
        "/api/admin/invitations",
        json={
            "email": "new-staff@example.test",
            "role": "channel partner",
        },
    )
    assert response.status_code == 409


def test_admin_can_revoke_pending_invitation(client):
    _login_admin(client)

    invitation, token = _create_invitation(client)

    response = client.post(
        f"/api/admin/invitations/{invitation['id']}/revoke"
    )
    assert response.status_code == 200
    assert response.json()["revoked"] is True

    inspection = client.post(
        "/api/staff-invitations/inspect",
        json={"token": token},
    )
    assert inspection.status_code == 410

    invitations = client.get("/api/admin/invitations")
    assert invitations.status_code == 200
    assert invitations.json()["items"][0]["status"] == "revoked"


def test_expired_invitation_cannot_be_accepted(client):
    _login_admin(client)

    invitation, token = _create_invitation(client)

    with Session(engine) as db:
        stored = db.get(StaffInvitation, invitation["id"])
        stored.expires_at = stored.created_at.replace(year=2000)
        db.commit()

    response = client.post(
        "/api/staff-invitations/accept",
        json={
            "token": token,
            "full_name": "Expired Invite",
            "password": "StrongExpiredPass123!",
        },
    )
    assert response.status_code == 410


def test_channel_partner_invitation_creates_channel_partner(client):
    _login_admin(client)

    _, token = _create_invitation(
        client,
        email="partner-invite@example.test",
        role="channel partner",
    )

    response = client.post(
        "/api/staff-invitations/accept",
        json={
            "token": token,
            "full_name": "New Channel Partner",
            "password": "StrongPartnerPass123!",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "channel partner"

    with Session(engine) as db:
        account = db.get(UserAccount, response.json()["account_id"])
        assert account.role == "channel partner"

def test_admin_dashboard_route_serves_admin_control_center(client):
    _login_admin(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Command Centre" in response.text
    assert "Admin Control Center" in response.text
    assert "no-store" in response.headers.get("cache-control", "").lower()


def test_developer_dashboard_does_not_serve_admin_page(client):
    _create_account("developer", "developer-page@example.test")
    _login_staff(client, "Developer", "developer-page@example.test")

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "RealIQ Dashboard" in response.text
    assert "Admin Control Center" not in response.text


def test_invitation_page_security_headers(client):
    response = client.get("/staff-invitation")

    assert response.status_code == 200
    assert "Accept your invitation" in response.text
    assert response.headers.get("cache-control") == "no-store"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("x-robots-tag") == "noindex, nofollow"


def test_channel_partner_cannot_access_admin_api(client):
    _create_account("channel partner", "partner-page@example.test")
    _login_staff(
        client,
        "Channel Partner",
        "partner-page@example.test",
    )

    response = client.get("/api/admin/summary")
    assert response.status_code == 403

def test_staff_deactivation_revokes_existing_session_and_reactivation_restores_access(client):
    from fastapi.testclient import TestClient
    from backend.app.main import app

    _login_admin(client)
    staff_id = _create_account("developer", "lifecycle-dev@example.test")

    with TestClient(app) as staff_client:
        _login_staff(
            staff_client,
            "Developer",
            "lifecycle-dev@example.test",
        )

        assert staff_client.get("/dashboard").status_code == 200

        deactivated = client.post(
            f"/api/admin/staff/{staff_id}/deactivate"
        )
        assert deactivated.status_code == 200
        assert deactivated.json()["is_active"] is False
        assert deactivated.json()["changed"] is True

        # Existing session must immediately lose protected staff access.
        assert staff_client.get(
            "/dashboard",
            follow_redirects=False,
        ).status_code == 403

        staff_client.post("/api/logout")

        rejected_login = staff_client.post(
            "/api/login",
            json={
                "identifier": "lifecycle-dev@example.test",
                "password": TEST_PASSWORD,
                "role": "Developer",
            },
        )
        assert rejected_login.status_code == 401

        reactivated = client.post(
            f"/api/admin/staff/{staff_id}/activate"
        )
        assert reactivated.status_code == 200
        assert reactivated.json()["is_active"] is True
        assert reactivated.json()["changed"] is True

        restored_login = _login_staff(
            staff_client,
            "Developer",
            "lifecycle-dev@example.test",
        )
        assert restored_login.status_code == 200
        assert staff_client.get("/dashboard").status_code == 200


def test_admin_cannot_deactivate_buyers_or_admin_accounts(client):
    admin_login = _login_admin(client)
    admin_id = admin_login.json()["user"]["account_id"]
    buyer_id = _create_account("buyer", "protected-buyer@example.test")

    self_deactivation = client.post(
        f"/api/admin/staff/{admin_id}/deactivate"
    )
    assert self_deactivation.status_code == 404

    buyer_deactivation = client.post(
        f"/api/admin/staff/{buyer_id}/deactivate"
    )
    assert buyer_deactivation.status_code == 404


def test_staff_lifecycle_and_invitation_events_are_audited(client):
    admin_login = _login_admin(client)
    admin_id = admin_login.json()["user"]["account_id"]

    first_invitation, _ = _create_invitation(
        client,
        email="audit-revoked@example.test",
    )

    revoke = client.post(
        f"/api/admin/invitations/{first_invitation['id']}/revoke"
    )
    assert revoke.status_code == 200

    second_invitation, second_token = _create_invitation(
        client,
        email="audit-accepted@example.test",
    )

    acceptance = client.post(
        "/api/staff-invitations/accept",
        json={
            "token": second_token,
            "full_name": "Audit Test Developer",
            "password": "StrongAuditPass123!",
        },
    )
    assert acceptance.status_code == 201

    staff_id = acceptance.json()["account_id"]
    deactivate = client.post(
        f"/api/admin/staff/{staff_id}/deactivate"
    )
    assert deactivate.status_code == 200

    reactivate = client.post(
        f"/api/admin/staff/{staff_id}/activate"
    )
    assert reactivate.status_code == 200

    response = client.get("/api/admin/audit-logs")
    assert response.status_code == 200

    items = response.json()["items"]
    actions = {item["action"] for item in items}

    assert "staff.invitation_created" in actions
    assert "staff.invitation_revoked" in actions
    assert "staff.invitation_accepted" in actions
    assert "staff.deactivated" in actions
    assert "staff.activated" in actions

    admin_events = [
        item for item in items
        if item["action"] in {
            "staff.invitation_created",
            "staff.invitation_revoked",
            "staff.deactivated",
            "staff.activated",
        }
    ]
    assert all(item["actor_account_id"] == admin_id for item in admin_events)

    # Audit responses must never expose invitation tokens.
    assert second_token not in response.text


def test_deactivated_admin_cannot_use_admin_api(client):
    _create_account("admin", "inactive-admin@example.test")

    with Session(engine) as db:
        admin = db.scalar(
            select(UserAccount).where(
                UserAccount.email == "inactive-admin@example.test"
            )
        )
        admin.is_active = False
        db.commit()

    response = client.post(
        "/api/login",
        json={
            "identifier": "inactive-admin@example.test",
            "password": TEST_PASSWORD,
            "role": "Admin",
        },
    )
    assert response.status_code == 401
