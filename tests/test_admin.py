from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.database import engine
from backend.app.main import hash_password
from backend.app.models import (
    AuthIdentity,
    DeveloperOrganization,
    DeveloperOrganizationAuditLog,
    PropertyChannelPartnerAssignment,
    PropertyListing,
    StaffAuditLog,
    StaffInvitation,
    UserAccount,
)
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError


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



def _create_organization_for_review(
    email="review-developer@example.test",
    verification_status="pending_verification",
):
    developer_id = _create_account("developer", email)

    with Session(engine) as db:
        organization = DeveloperOrganization(
            legal_name="Marine Review Test Private Limited",
            display_name="Marine Review Test",
            entity_type="private_limited",
            registration_number=f"REVIEW-{developer_id}",
            country_code="IN",
            registered_address="12 Marine Drive",
            city="Mumbai",
            state="Maharashtra",
            postal_code="400001",
            contact_email="contact@marine-review.example",
            contact_phone="+919876543210",
            verification_status=verification_status,
            submitted_by_account_id=developer_id,
        )
        db.add(organization)
        db.commit()
        db.refresh(organization)
        return organization.id


def test_organization_verification_queue_requires_authentication(client):
    response = client.get("/api/admin/organizations/pending")

    assert response.status_code == 401


def test_developer_cannot_access_organization_verification_queue(client):
    _create_account("developer", "review-queue-developer@example.test")
    _login_staff(client, "Developer", "review-queue-developer@example.test")

    response = client.get("/api/admin/organizations/pending")

    assert response.status_code == 403


def test_channel_partner_cannot_access_organization_verification_queue(client):
    _create_account("channel partner", "review-queue-partner@example.test")
    _login_staff(client, "Channel Partner", "review-queue-partner@example.test")

    response = client.get("/api/admin/organizations/pending")

    assert response.status_code == 403


def test_admin_verification_queue_contains_only_pending_organizations(client):
    pending_id = _create_organization_for_review(
        email="pending-review@example.test",
    )
    _create_organization_for_review(
        email="verified-review@example.test",
        verification_status="verified",
    )
    _create_organization_for_review(
        email="rejected-review@example.test",
        verification_status="rejected",
    )

    _login_admin(client)
    response = client.get("/api/admin/organizations/pending")

    assert response.status_code == 200
    data = response.json()
    assert "items" in data

    returned_ids = {item["id"] for item in data["items"]}
    assert returned_ids == {pending_id}
    assert all(
        item["verification_status"] == "pending_verification"
        for item in data["items"]
    )



def test_admin_can_approve_pending_organization_and_audit_decision(client):
    organization_id = _create_organization_for_review()
    admin_login = _login_admin(client)
    admin_id = admin_login.json()["user"]["account_id"]

    response = client.post(
        f"/api/admin/organizations/{organization_id}/review",
        json={"verification_status": "verified"},
    )

    assert response.status_code == 200
    assert response.json()["verification_status"] == "verified"
    assert response.json()["reviewed_by_account_id"] == admin_id
    assert response.json()["reviewed_at"] is not None

    with Session(engine) as db:
        organization = db.get(DeveloperOrganization, organization_id)
        assert organization.verification_status == "verified"
        assert organization.reviewed_by_account_id == admin_id
        assert organization.reviewed_at is not None

        audit = db.scalar(
            select(DeveloperOrganizationAuditLog).where(
                DeveloperOrganizationAuditLog.organization_id == organization_id
            )
        )
        assert audit is not None
        assert audit.actor_account_id == admin_id
        assert audit.action == "organization.verified"
        assert audit.details == {
            "previous_status": "pending_verification",
            "new_status": "verified",
        }


def test_admin_can_reject_pending_organization_with_review_notes(client):
    organization_id = _create_organization_for_review()
    admin_login = _login_admin(client)
    admin_id = admin_login.json()["user"]["account_id"]

    response = client.post(
        f"/api/admin/organizations/{organization_id}/review",
        json={
            "verification_status": "rejected",
            "review_notes": "  Registration document could not be verified.  ",
        },
    )

    assert response.status_code == 200
    assert response.json()["verification_status"] == "rejected"
    assert response.json()["reviewed_by_account_id"] == admin_id
    assert response.json()["reviewed_at"] is not None

    with Session(engine) as db:
        organization = db.get(DeveloperOrganization, organization_id)
        assert organization.verification_status == "rejected"
        assert organization.review_notes == (
            "Registration document could not be verified."
        )

        audit = db.scalar(
            select(DeveloperOrganizationAuditLog).where(
                DeveloperOrganizationAuditLog.organization_id == organization_id
            )
        )
        assert audit.action == "organization.rejected"
        assert audit.details == {
            "previous_status": "pending_verification",
            "new_status": "rejected",
        }
        assert "Registration document" not in str(audit.details)


def test_admin_cannot_review_organization_more_than_once(client):
    organization_id = _create_organization_for_review()
    _login_admin(client)

    first = client.post(
        f"/api/admin/organizations/{organization_id}/review",
        json={"verification_status": "verified"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/admin/organizations/{organization_id}/review",
        json={"verification_status": "rejected"},
    )
    assert second.status_code == 409

    with Session(engine) as db:
        organization = db.get(DeveloperOrganization, organization_id)
        assert organization.verification_status == "verified"

        audit_count = db.scalar(
            select(func.count(DeveloperOrganizationAuditLog.id)).where(
                DeveloperOrganizationAuditLog.organization_id == organization_id
            )
        )
        assert audit_count == 1


def test_reviewing_nonexistent_organization_returns_404(client):
    _login_admin(client)

    response = client.post(
        "/api/admin/organizations/999999/review",
        json={"verification_status": "verified"},
    )

    assert response.status_code == 404


def test_organization_review_requires_admin(client):
    organization_id = _create_organization_for_review()

    response = client.post(
        f"/api/admin/organizations/{organization_id}/review",
        json={"verification_status": "verified"},
    )
    assert response.status_code == 401

    _create_account("developer", "review-decision-developer@example.test")
    _login_staff(client, "Developer", "review-decision-developer@example.test")

    forbidden = client.post(
        f"/api/admin/organizations/{organization_id}/review",
        json={"verification_status": "verified"},
    )
    assert forbidden.status_code == 403


def test_organization_review_rejects_invalid_status_and_extra_fields(client):
    organization_id = _create_organization_for_review()
    _login_admin(client)

    invalid_status = client.post(
        f"/api/admin/organizations/{organization_id}/review",
        json={"verification_status": "suspended"},
    )
    assert invalid_status.status_code == 422

    extra_field = client.post(
        f"/api/admin/organizations/{organization_id}/review",
        json={
            "verification_status": "verified",
            "reviewed_by_account_id": 1,
        },
    )
    assert extra_field.status_code == 422


def test_organization_review_rejects_oversized_notes(client):
    organization_id = _create_organization_for_review()
    _login_admin(client)

    response = client.post(
        f"/api/admin/organizations/{organization_id}/review",
        json={
            "verification_status": "rejected",
            "review_notes": "x" * 2001,
        },
    )

    assert response.status_code == 422



def _create_assignment_test_property():
    with Session(engine) as db:
        listing = PropertyListing(
            title="Assignment Test Apartment",
            property_type="apartment",
            city="Kolkata",
            state="West Bengal",
            price_inr=12500000,
            bedrooms=3,
            area_sqft=1420,
            description="Assignment API test fixture.",
            availability_status="available",
            is_published=True,
        )
        db.add(listing)
        db.commit()
        return listing.id


def test_admin_can_assign_property_to_channel_partner(client):
    admin_id = _create_account("admin", "assignment-admin@example.test")
    cp_id = _create_account("channel partner", "assignment-cp@example.test")
    property_id = _create_assignment_test_property()
    _login_staff(client, "Admin", "assignment-admin@example.test")

    response = client.post(
        f"/api/admin/properties/{property_id}/channel-partner-assignment",
        json={"channel_partner_account_id": cp_id},
    )

    assert response.status_code == 200
    assert response.json()["property_listing_id"] == property_id
    assert response.json()["channel_partner_account_id"] == cp_id
    assert response.json()["assigned_by_account_id"] == admin_id

    with Session(engine) as db:
        assignment = db.scalar(
            select(PropertyChannelPartnerAssignment).where(
                PropertyChannelPartnerAssignment.property_listing_id == property_id
            )
        )
        audit = db.scalar(
            select(StaffAuditLog).where(
                StaffAuditLog.action == "property.channel_partner.assigned",
                StaffAuditLog.details["property_listing_id"].as_integer() == property_id,
            )
        )

        assert assignment is not None
        assert assignment.channel_partner_account_id == cp_id
        assert assignment.assigned_by_account_id == admin_id
        assert audit is not None
        assert audit.actor_account_id == admin_id
        assert audit.target_account_id == cp_id


def test_property_assignment_is_exclusive_across_channel_partners(client):
    _create_account("admin", "exclusive-admin@example.test")
    first_cp_id = _create_account("channel partner", "exclusive-cp1@example.test")
    second_cp_id = _create_account("channel partner", "exclusive-cp2@example.test")
    property_id = _create_assignment_test_property()
    _login_staff(client, "Admin", "exclusive-admin@example.test")

    endpoint = f"/api/admin/properties/{property_id}/channel-partner-assignment"

    first = client.post(
        endpoint,
        json={"channel_partner_account_id": first_cp_id},
    )
    duplicate = client.post(
        endpoint,
        json={"channel_partner_account_id": first_cp_id},
    )
    competing = client.post(
        endpoint,
        json={"channel_partner_account_id": second_cp_id},
    )

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert competing.status_code == 409

    with Session(engine) as db:
        assignments = db.scalars(
            select(PropertyChannelPartnerAssignment).where(
                PropertyChannelPartnerAssignment.property_listing_id == property_id
            )
        ).all()
        assert len(assignments) == 1
        assert assignments[0].channel_partner_account_id == first_cp_id


def test_database_enforces_one_active_assignment_and_preserves_history(client):
    admin_id = _create_account("admin", "db-constraint-admin@example.test")
    cp_id = _create_account("channel partner", "db-constraint-cp@example.test")
    other_cp_id = _create_account(
        "channel partner",
        "db-constraint-other-cp@example.test",
    )
    property_id = _create_assignment_test_property()
    ended_at = datetime.now(timezone.utc).replace(tzinfo=None)

    with Session(engine) as db:
        # Multiple ended assignments for the same property are valid history.
        db.add_all(
            [
                PropertyChannelPartnerAssignment(
                    property_listing_id=property_id,
                    channel_partner_account_id=cp_id,
                    assigned_by_account_id=admin_id,
                    ended_at=ended_at,
                    ended_by_account_id=admin_id,
                ),
                PropertyChannelPartnerAssignment(
                    property_listing_id=property_id,
                    channel_partner_account_id=other_cp_id,
                    assigned_by_account_id=admin_id,
                    ended_at=ended_at,
                    ended_by_account_id=admin_id,
                ),
                PropertyChannelPartnerAssignment(
                    property_listing_id=property_id,
                    channel_partner_account_id=cp_id,
                    assigned_by_account_id=admin_id,
                ),
            ]
        )
        db.commit()

    # A second active assignment must fail at the database constraint.
    with Session(engine) as db:
        db.add(
            PropertyChannelPartnerAssignment(
                property_listing_id=property_id,
                channel_partner_account_id=other_cp_id,
                assigned_by_account_id=admin_id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()

    with Session(engine) as db:
        assignments = db.scalars(
            select(PropertyChannelPartnerAssignment).where(
                PropertyChannelPartnerAssignment.property_listing_id == property_id
            )
        ).all()

        assert len(assignments) == 3
        assert sum(item.ended_at is None for item in assignments) == 1


def test_admin_can_revoke_and_reassign_property(client):
    admin_id = _create_account("admin", "reassign-admin@example.test")
    first_cp_id = _create_account("channel partner", "reassign-cp1@example.test")
    second_cp_id = _create_account("channel partner", "reassign-cp2@example.test")
    property_id = _create_assignment_test_property()
    _login_staff(client, "Admin", "reassign-admin@example.test")

    endpoint = f"/api/admin/properties/{property_id}/channel-partner-assignment"

    assigned = client.post(
        endpoint,
        json={"channel_partner_account_id": first_cp_id},
    )
    assert assigned.status_code == 200

    revoked = client.delete(endpoint)
    assert revoked.status_code == 200
    assert revoked.json()["previous_channel_partner_account_id"] == first_cp_id
    assert revoked.json()["ended_at"] is not None
    assert revoked.json()["ended_by_account_id"] == admin_id

    with Session(engine) as db:
        assignments = db.scalars(
            select(PropertyChannelPartnerAssignment)
            .where(
                PropertyChannelPartnerAssignment.property_listing_id == property_id
            )
            .order_by(PropertyChannelPartnerAssignment.id)
        ).all()

        assert len(assignments) == 1
        assert assignments[0].channel_partner_account_id == first_cp_id
        assert assignments[0].ended_at is not None
        assert assignments[0].ended_by_account_id == admin_id

    reassigned = client.post(
        endpoint,
        json={"channel_partner_account_id": second_cp_id},
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["channel_partner_account_id"] == second_cp_id

    with Session(engine) as db:
        assignments = db.scalars(
            select(PropertyChannelPartnerAssignment)
            .where(
                PropertyChannelPartnerAssignment.property_listing_id == property_id
            )
            .order_by(PropertyChannelPartnerAssignment.id)
        ).all()

        actions = db.scalars(
            select(StaffAuditLog.action).where(
                StaffAuditLog.details["property_listing_id"].as_integer() == property_id
            )
        ).all()

        assert len(assignments) == 2

        # First CP's historical assignment remains preserved.
        assert assignments[0].channel_partner_account_id == first_cp_id
        assert assignments[0].ended_at is not None
        assert assignments[0].ended_by_account_id == admin_id

        # Second CP is the only active assignment.
        assert assignments[1].channel_partner_account_id == second_cp_id
        assert assignments[1].ended_at is None
        assert assignments[1].ended_by_account_id is None

        assert actions.count("property.channel_partner.assigned") == 2
        assert actions.count("property.channel_partner.revoked") == 1


def test_admin_assignment_rejects_invalid_channel_partner_accounts(client):
    _create_account("admin", "invalid-cp-admin@example.test")
    buyer_id = _create_account("buyer", "invalid-cp-buyer@example.test")
    developer_id = _create_account("developer", "invalid-cp-developer@example.test")
    inactive_cp_id = _create_account("channel partner", "inactive-cp@example.test")
    property_id = _create_assignment_test_property()

    with Session(engine) as db:
        inactive_cp = db.get(UserAccount, inactive_cp_id)
        inactive_cp.is_active = False
        db.commit()

    _login_staff(client, "Admin", "invalid-cp-admin@example.test")
    endpoint = f"/api/admin/properties/{property_id}/channel-partner-assignment"

    assert client.post(
        endpoint,
        json={"channel_partner_account_id": buyer_id},
    ).status_code == 409

    assert client.post(
        endpoint,
        json={"channel_partner_account_id": developer_id},
    ).status_code == 409

    assert client.post(
        endpoint,
        json={"channel_partner_account_id": inactive_cp_id},
    ).status_code == 409

    assert client.post(
        endpoint,
        json={"channel_partner_account_id": 999999},
    ).status_code == 404

    assert client.post(
        f"/api/admin/properties/999999/channel-partner-assignment",
        json={"channel_partner_account_id": inactive_cp_id},
    ).status_code == 404


def test_property_assignment_endpoints_require_admin(client):
    property_id = _create_assignment_test_property()

    endpoint = f"/api/admin/properties/{property_id}/channel-partner-assignment"

    assert client.post(
        endpoint,
        json={"channel_partner_account_id": 1},
    ).status_code == 401

    _create_account("developer", "assignment-forbidden@example.test")
    _login_staff(client, "Developer", "assignment-forbidden@example.test")

    assert client.post(
        endpoint,
        json={"channel_partner_account_id": 1},
    ).status_code == 403

    assert client.delete(endpoint).status_code == 403
