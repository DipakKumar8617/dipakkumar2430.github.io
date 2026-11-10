import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.database import engine
from backend.app.main import hash_password
from backend.app.models import (
    AuthIdentity,
    DeveloperOrganization,
    DeveloperOrganizationAuditLog,
    DeveloperOrganizationMembership,
    UserAccount,
)


TEST_PASSWORD = "StrongAdminPass123!"
BASE_URL = "/api/developer-organizations"


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


def _login(client, role, email):
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


def _organization_payload(**overrides):
    payload = {
        "legal_name": "Oceanic Marine Developers Private Limited",
        "display_name": "Oceanic Marine",
        "entity_type": "private_limited",
        "registration_number": "U12345MH2026PTC123456",
        "country_code": "IN",
        "registered_address": "12 Marine Drive",
        "city": "Mumbai",
        "state": "Maharashtra",
        "postal_code": "400001",
        "contact_email": "contact@oceanic.example",
        "contact_phone": "+919876543210",
        "website": "https://oceanic.example",
    }
    payload.update(overrides)
    return payload


def test_developer_organization_endpoints_require_authentication(client):
    assert client.get(BASE_URL).status_code == 401
    assert client.get(f"{BASE_URL}/1").status_code == 401
    assert client.post(
        BASE_URL,
        json=_organization_payload(),
    ).status_code == 401


def test_developer_can_register_organization_with_owner_membership_and_audit(client):
    account_id = _create_account("developer", "developer@example.test")
    _login(client, "Developer", "developer@example.test")

    response = client.post(BASE_URL, json=_organization_payload())

    assert response.status_code == 201
    data = response.json()

    assert data["legal_name"] == "Oceanic Marine Developers Private Limited"
    assert data["display_name"] == "Oceanic Marine"
    assert data["entity_type"] == "private_limited"
    assert data["registration_number"] == "U12345MH2026PTC123456"
    assert data["country_code"] == "IN"
    assert data["verification_status"] == "pending_verification"
    assert "review_notes" not in data
    assert "reviewed_by_account_id" not in data

    organization_id = data["id"]

    with Session(engine) as db:
        organization = db.get(DeveloperOrganization, organization_id)
        assert organization is not None
        assert organization.submitted_by_account_id == account_id

        membership = db.scalar(
            select(DeveloperOrganizationMembership).where(
                DeveloperOrganizationMembership.organization_id == organization_id,
                DeveloperOrganizationMembership.account_id == account_id,
            )
        )
        assert membership is not None
        assert membership.role == "owner"
        assert membership.status == "active"
        assert membership.created_by_account_id == account_id

        audit = db.scalar(
            select(DeveloperOrganizationAuditLog).where(
                DeveloperOrganizationAuditLog.organization_id == organization_id
            )
        )
        assert audit is not None
        assert audit.actor_account_id == account_id
        assert audit.action == "organization.registered"
        assert audit.details == {
            "entity_type": "private_limited",
            "country_code": "IN",
        }

        assert "contact_email" not in audit.details
        assert "contact_phone" not in audit.details
        assert "registered_address" not in audit.details


def test_developer_can_list_and_retrieve_own_organization(client):
    _create_account("developer", "owner@example.test")
    _login(client, "Developer", "owner@example.test")

    created = client.post(BASE_URL, json=_organization_payload())
    assert created.status_code == 201
    organization_id = created.json()["id"]

    listing = client.get(BASE_URL)
    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()] == [organization_id]

    detail = client.get(f"{BASE_URL}/{organization_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == organization_id


def test_non_developer_staff_cannot_access_developer_organizations(client):
    _create_account("channel partner", "partner@example.test")
    _login(client, "Channel Partner", "partner@example.test")

    assert client.get(BASE_URL).status_code == 403
    assert client.get(f"{BASE_URL}/1").status_code == 403
    assert client.post(
        BASE_URL,
        json=_organization_payload(),
    ).status_code == 403


def test_developer_cannot_access_another_developers_organization(client):
    _create_account("developer", "first@example.test")
    _login(client, "Developer", "first@example.test")

    created = client.post(BASE_URL, json=_organization_payload())
    assert created.status_code == 201
    organization_id = created.json()["id"]

    # Clear the first developer's session and authenticate as another developer.
    client.cookies.clear()
    _create_account("developer", "second@example.test")
    _login(client, "Developer", "second@example.test")

    detail = client.get(f"{BASE_URL}/{organization_id}")
    assert detail.status_code == 404

    listing = client.get(BASE_URL)
    assert listing.status_code == 200
    assert listing.json() == []


def test_duplicate_organization_registration_is_rejected_atomically(client):
    _create_account("developer", "duplicate@example.test")
    _login(client, "Developer", "duplicate@example.test")

    payload = _organization_payload()

    first = client.post(BASE_URL, json=payload)
    assert first.status_code == 201

    duplicate = client.post(
        BASE_URL,
        json={
            **payload,
            "legal_name": "A Different Legal Name",
        },
    )
    assert duplicate.status_code == 409

    with Session(engine) as db:
        organization_count = db.scalar(
            select(func.count()).select_from(DeveloperOrganization)
        )
        membership_count = db.scalar(
            select(func.count()).select_from(DeveloperOrganizationMembership)
        )
        audit_count = db.scalar(
            select(func.count()).select_from(DeveloperOrganizationAuditLog)
        )

    assert organization_count == 1
    assert membership_count == 1
    assert audit_count == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"entity_type": "unregistered_entity_type"},
        {"country_code": "IND"},
        {"contact_email": "not-an-email"},
        {"contact_phone": "invalid-phone"},
        {"contact_phone": "-------"},
        {"legal_name": "   "},
        {"registered_address": "   "},
        {"unexpected_field": "must be rejected"},
    ],
)


def test_invalid_organization_payload_is_rejected(client, overrides):
    _create_account("developer", "validation@example.test")
    _login(client, "Developer", "validation@example.test")

    response = client.post(
        BASE_URL,
        json=_organization_payload(**overrides),
    )

    assert response.status_code == 422



def test_country_code_is_normalized_before_length_validation(client):
    _create_account("developer", "country-code@example.test")
    _login(client, "Developer", "country-code@example.test")

    response = client.post(
        BASE_URL,
        json=_organization_payload(country_code=" in "),
    )

    assert response.status_code == 201
    assert response.json()["country_code"] == "IN"


def test_deactivated_developer_loses_organization_access(client):
    account_id = _create_account("developer", "inactive-developer@example.test")
    _login(client, "Developer", "inactive-developer@example.test")

    with Session(engine) as db:
        account = db.get(UserAccount, account_id)
        account.is_active = False
        db.commit()

    assert client.get(BASE_URL).status_code == 403
    assert client.post(
        BASE_URL,
        json=_organization_payload(),
    ).status_code == 403


def test_suspended_membership_hides_organization(client):
    account_id = _create_account("developer", "suspended-member@example.test")
    _login(client, "Developer", "suspended-member@example.test")

    created = client.post(BASE_URL, json=_organization_payload())
    assert created.status_code == 201
    organization_id = created.json()["id"]

    with Session(engine) as db:
        membership = db.scalar(
            select(DeveloperOrganizationMembership).where(
                DeveloperOrganizationMembership.organization_id == organization_id,
                DeveloperOrganizationMembership.account_id == account_id,
            )
        )
        assert membership is not None
        membership.status = "suspended"
        db.commit()

    assert client.get(BASE_URL).status_code == 200
    assert client.get(BASE_URL).json() == []
    assert client.get(f"{BASE_URL}/{organization_id}").status_code == 404


def test_owner_can_list_members_and_add_developer_with_audit(client):
    owner_id = _create_account("developer", "membership-owner@example.test")
    member_id = _create_account("developer", "new-member@example.test")
    _login(client, "Developer", "membership-owner@example.test")

    created = client.post(BASE_URL, json=_organization_payload())
    assert created.status_code == 201
    organization_id = created.json()["id"]

    added = client.post(
        f"{BASE_URL}/{organization_id}/members",
        json={"email": " NEW-MEMBER@example.test ", "role": "viewer"},
    )
    assert added.status_code == 201
    assert added.json()["account_id"] == member_id
    assert added.json()["role"] == "viewer"
    assert added.json()["status"] == "active"

    roster = client.get(f"{BASE_URL}/{organization_id}/members")
    assert roster.status_code == 200
    assert {item["account_id"] for item in roster.json()} == {owner_id, member_id}

    with Session(engine) as db:
        membership = db.scalar(
            select(DeveloperOrganizationMembership).where(
                DeveloperOrganizationMembership.organization_id == organization_id,
                DeveloperOrganizationMembership.account_id == member_id,
            )
        )
        assert membership is not None
        assert membership.created_by_account_id == owner_id

        audit = db.scalar(
            select(DeveloperOrganizationAuditLog).where(
                DeveloperOrganizationAuditLog.organization_id == organization_id,
                DeveloperOrganizationAuditLog.target_account_id == member_id,
            )
        )
        assert audit is not None
        assert audit.actor_account_id == owner_id
        assert audit.action == "membership.added"
        assert audit.details == {"new_status": "active", "new_role": "viewer"}


def test_viewer_cannot_list_or_manage_organization_members(client):
    _create_account("developer", "viewer-owner@example.test")
    viewer_id = _create_account("developer", "viewer-member@example.test")
    _login(client, "Developer", "viewer-owner@example.test")

    created = client.post(BASE_URL, json=_organization_payload())
    assert created.status_code == 201
    organization_id = created.json()["id"]

    with Session(engine) as db:
        db.add(
            DeveloperOrganizationMembership(
                organization_id=organization_id,
                account_id=viewer_id,
                role="viewer",
                status="active",
                created_by_account_id=viewer_id,
            )
        )
        db.commit()

    client.cookies.clear()
    _login(client, "Developer", "viewer-member@example.test")

    assert client.get(f"{BASE_URL}/{organization_id}/members").status_code == 403
    assert client.post(
        f"{BASE_URL}/{organization_id}/members",
        json={"email": "viewer-owner@example.test", "role": "viewer"},
    ).status_code == 403
    assert client.patch(
        f"{BASE_URL}/{organization_id}/members/{viewer_id}/role",
        json={"role": "inventory_manager"},
    ).status_code == 403


def test_owner_can_change_member_role_and_suspend_with_audit(client):
    owner_id = _create_account("developer", "role-owner@example.test")
    member_id = _create_account("developer", "role-member@example.test")
    _login(client, "Developer", "role-owner@example.test")

    created = client.post(BASE_URL, json=_organization_payload())
    assert created.status_code == 201
    organization_id = created.json()["id"]

    added = client.post(
        f"{BASE_URL}/{organization_id}/members",
        json={"email": "role-member@example.test", "role": "viewer"},
    )
    assert added.status_code == 201

    changed_role = client.patch(
        f"{BASE_URL}/{organization_id}/members/{member_id}/role",
        json={"role": "inventory_manager"},
    )
    assert changed_role.status_code == 200
    assert changed_role.json()["role"] == "inventory_manager"

    suspended = client.patch(
        f"{BASE_URL}/{organization_id}/members/{member_id}/status",
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    with Session(engine) as db:
        audits = db.scalars(
            select(DeveloperOrganizationAuditLog)
            .where(
                DeveloperOrganizationAuditLog.organization_id == organization_id,
                DeveloperOrganizationAuditLog.target_account_id == member_id,
            )
            .order_by(DeveloperOrganizationAuditLog.id)
        ).all()

        assert [item.action for item in audits] == [
            "membership.added",
            "membership.role_changed",
            "membership.status_changed",
        ]
        assert audits[1].actor_account_id == owner_id
        assert audits[1].details == {
            "previous_role": "viewer",
            "new_role": "inventory_manager",
        }
        assert audits[2].details == {
            "previous_status": "active",
            "new_status": "suspended",
        }


def test_admin_cannot_assign_or_reactivate_another_admin(client):
    owner_id = _create_account("developer", "admin-rules-owner@example.test")
    admin_one_id = _create_account("developer", "admin-one@example.test")
    admin_two_id = _create_account("developer", "admin-two@example.test")
    _login(client, "Developer", "admin-rules-owner@example.test")

    created = client.post(BASE_URL, json=_organization_payload())
    assert created.status_code == 201
    organization_id = created.json()["id"]

    for email in ("admin-one@example.test", "admin-two@example.test"):
        response = client.post(
            f"{BASE_URL}/{organization_id}/members",
            json={"email": email, "role": "admin"},
        )
        assert response.status_code == 201

    with Session(engine) as db:
        target = db.scalar(
            select(DeveloperOrganizationMembership).where(
                DeveloperOrganizationMembership.organization_id == organization_id,
                DeveloperOrganizationMembership.account_id == admin_two_id,
            )
        )
        target.status = "suspended"
        db.commit()

    client.cookies.clear()
    _login(client, "Developer", "admin-one@example.test")

    assert client.post(
        f"{BASE_URL}/{organization_id}/members",
        json={"email": "new-admin@example.test", "role": "admin"},
    ).status_code == 403

    assert client.patch(
        f"{BASE_URL}/{organization_id}/members/{admin_two_id}/role",
        json={"role": "viewer"},
    ).status_code == 403

    assert client.post(
        f"{BASE_URL}/{organization_id}/members",
        json={"email": "admin-two@example.test", "role": "viewer"},
    ).status_code == 403


def test_owner_membership_cannot_be_changed_or_removed(client):
    owner_id = _create_account("developer", "immutable-owner@example.test")
    _login(client, "Developer", "immutable-owner@example.test")

    created = client.post(BASE_URL, json=_organization_payload())
    assert created.status_code == 201
    organization_id = created.json()["id"]

    role_change = client.patch(
        f"{BASE_URL}/{organization_id}/members/{owner_id}/role",
        json={"role": "viewer"},
    )
    status_change = client.patch(
        f"{BASE_URL}/{organization_id}/members/{owner_id}/status",
        json={"status": "removed"},
    )

    assert role_change.status_code == 409
    assert status_change.status_code == 409

    with Session(engine) as db:
        owner = db.scalar(
            select(DeveloperOrganizationMembership).where(
                DeveloperOrganizationMembership.organization_id == organization_id,
                DeveloperOrganizationMembership.account_id == owner_id,
            )
        )
        assert owner.role == "owner"
        assert owner.status == "active"


def test_owner_can_reactivate_member_without_replacing_membership(client):
    owner_id = _create_account("developer", "reactivation-owner@example.test")
    member_id = _create_account("developer", "reactivation-member@example.test")
    _login(client, "Developer", "reactivation-owner@example.test")

    created = client.post(BASE_URL, json=_organization_payload())
    assert created.status_code == 201
    organization_id = created.json()["id"]

    added = client.post(
        f"{BASE_URL}/{organization_id}/members",
        json={"email": "reactivation-member@example.test", "role": "viewer"},
    )
    assert added.status_code == 201

    with Session(engine) as db:
        original = db.scalar(
            select(DeveloperOrganizationMembership).where(
                DeveloperOrganizationMembership.organization_id == organization_id,
                DeveloperOrganizationMembership.account_id == member_id,
            )
        )
        original_id = original.id
        original_joined_at = original.joined_at
        original_creator_id = original.created_by_account_id
        original.status = "suspended"
        db.commit()

    reactivated = client.post(
        f"{BASE_URL}/{organization_id}/members",
        json={"email": "reactivation-member@example.test", "role": "inventory_manager"},
    )
    assert reactivated.status_code == 201
    assert reactivated.json()["status"] == "active"
    assert reactivated.json()["role"] == "inventory_manager"

    with Session(engine) as db:
        membership = db.scalar(
            select(DeveloperOrganizationMembership).where(
                DeveloperOrganizationMembership.organization_id == organization_id,
                DeveloperOrganizationMembership.account_id == member_id,
            )
        )
        assert membership.id == original_id
        assert membership.joined_at == original_joined_at
        assert membership.created_by_account_id == original_creator_id == owner_id

        audit = db.scalar(
            select(DeveloperOrganizationAuditLog).where(
                DeveloperOrganizationAuditLog.organization_id == organization_id,
                DeveloperOrganizationAuditLog.target_account_id == member_id,
                DeveloperOrganizationAuditLog.action == "membership.reactivated",
            )
        )
        assert audit is not None
        assert audit.actor_account_id == owner_id
        assert audit.details == {
            "previous_status": "suspended",
            "previous_role": "viewer",
            "new_status": "active",
            "new_role": "inventory_manager",
        }


def test_member_add_rejects_unknown_duplicate_and_invalid_accounts(client):
    _create_account("developer", "add-validation-owner@example.test")
    _create_account("buyer", "buyer-account@example.test")
    _create_account("developer", "inactive-account@example.test")
    _login(client, "Developer", "add-validation-owner@example.test")

    created = client.post(BASE_URL, json=_organization_payload())
    assert created.status_code == 201
    organization_id = created.json()["id"]
    members_url = f"{BASE_URL}/{organization_id}/members"

    assert client.post(
        members_url,
        json={"email": "missing@example.test", "role": "viewer"},
    ).status_code == 404

    assert client.post(
        members_url,
        json={"email": "buyer-account@example.test", "role": "viewer"},
    ).status_code == 404

    with Session(engine) as db:
        inactive = db.scalar(
            select(UserAccount).where(
                UserAccount.email == "inactive-account@example.test"
            )
        )
        inactive.is_active = False
        db.commit()

    assert client.post(
        members_url,
        json={"email": "inactive-account@example.test", "role": "viewer"},
    ).status_code == 404

    added = client.post(
        members_url,
        json={"email": "add-validation-owner@example.test", "role": "viewer"},
    )
    assert added.status_code == 409

    for payload in (
        {"email": "not-an-email", "role": "viewer"},
        {"email": "missing@example.test", "role": "owner"},
        {"email": "missing@example.test", "role": "unknown"},
        {"email": "missing@example.test", "role": "viewer", "unexpected": True},
    ):
        assert client.post(members_url, json=payload).status_code == 422
