import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import engine
from backend.app.main import hash_password
from backend.app.models import (
    AuthIdentity,
    DeveloperOrganization,
    DeveloperOrganizationAuditLog,
    DeveloperOrganizationMembership,
    PropertyListing,
    UserAccount,
)


TEST_PASSWORD = "StrongAdminPass123!"
BASE_URL = "/api/developer-inventory"


def _create_account(email, role="developer"):
    with Session(engine) as db:
        account = UserAccount(
            full_name="Inventory Test Account",
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


def _login(client, email, role="Developer"):
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
        "legal_name": "Inventory Test Developers Private Limited",
        "display_name": "Inventory Test Developers",
        "entity_type": "private_limited",
        "registration_number": "INV2026TEST001",
        "country_code": "IN",
        "registered_address": "12 Test Marine Drive",
        "city": "Kolkata",
        "state": "West Bengal",
        "postal_code": "700001",
        "contact_email": "inventory@example.test",
        "contact_phone": "+919876543210",
        "website": "https://inventory.example.test",
    }
    payload.update(overrides)
    return payload


def _listing_payload(**overrides):
    payload = {
        "title": "Test Apartment in Kolkata",
        "property_type": "apartment",
        "city": "Kolkata",
        "state": "West Bengal",
        "price_inr": 8500000,
        "bedrooms": 3,
        "area_sqft": 1250,
        "description": "Test-only inventory fixture.",
        "image_url": None,
    }
    payload.update(overrides)
    return payload


def _create_organization(client, owner_email):
    _create_account(owner_email)
    _login(client, owner_email)

    unique_suffix = owner_email.split("@")[0].replace("-", "")[-12:]
    response = client.post(
        "/api/developer-organizations",
        json=_organization_payload(
            registration_number=f"INV2026{unique_suffix.upper()}",
            contact_email=f"{unique_suffix}@example.test",
        ),
    )
    assert response.status_code == 201
    return response.json()["id"]


def _set_organization_status(organization_id, status):
    with Session(engine) as db:
        organization = db.get(DeveloperOrganization, organization_id)
        assert organization is not None
        organization.verification_status = status
        db.commit()


def _add_member(organization_id, account_id, role, status="active"):
    with Session(engine) as db:
        db.add(
            DeveloperOrganizationMembership(
                organization_id=organization_id,
                account_id=account_id,
                role=role,
                status=status,
                created_by_account_id=account_id,
            )
        )
        db.commit()


def _listing_url(organization_id):
    return f"{BASE_URL}/organizations/{organization_id}/listings"


def test_inventory_endpoints_require_authentication(client):
    url = _listing_url(1)

    assert client.get(url).status_code == 401
    assert client.post(url, json=_listing_payload()).status_code == 401
    assert client.get(f"{url}/1").status_code == 401
    assert client.patch(
        f"{url}/1",
        json={"title": "Changed title"},
    ).status_code == 401


def test_non_developer_cannot_access_inventory(client):
    _create_account("inventory-partner@example.test", role="channel partner")
    _login(client, "inventory-partner@example.test", role="Channel Partner")

    assert client.get(_listing_url(1)).status_code == 403
    assert client.post(
        _listing_url(1),
        json=_listing_payload(),
    ).status_code == 403


def test_owner_can_create_and_list_draft_inventory_with_audit(client):
    owner_email = "inventory-owner@example.test"
    owner_id = _create_account(owner_email)
    _login(client, owner_email)

    organization_response = client.post(
        "/api/developer-organizations",
        json=_organization_payload(),
    )
    assert organization_response.status_code == 201
    organization_id = organization_response.json()["id"]

    response = client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    )

    assert response.status_code == 201
    data = response.json()

    assert data["developer_organization_id"] == organization_id
    assert data["title"] == "Test Apartment in Kolkata"
    assert data["availability_status"] == "available"
    assert data["is_published"] is False

    listing_id = data["id"]

    listed = client.get(_listing_url(organization_id))
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [listing_id]

    detail = client.get(f"{_listing_url(organization_id)}/{listing_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == listing_id

    with Session(engine) as db:
        audit = db.scalar(
            select(DeveloperOrganizationAuditLog).where(
                DeveloperOrganizationAuditLog.organization_id == organization_id,
                DeveloperOrganizationAuditLog.action == "inventory.listing_created",
            )
        )

        assert audit is not None
        assert audit.actor_account_id == owner_id
        assert audit.details == {
            "listing_id": listing_id,
            "initial_status": "draft",
        }


@pytest.mark.parametrize("role", ["owner", "admin", "inventory_manager"])
def test_inventory_write_roles_can_create_listings(client, role):
    owner_email = f"inventory-role-owner-{role}@example.test"
    organization_id = _create_organization(client, owner_email)

    member_email = f"inventory-member-{role}@example.test"
    member_id = _create_account(member_email)
    _add_member(organization_id, member_id, role)

    client.cookies.clear()
    _login(client, member_email)

    response = client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    )

    assert response.status_code == 201
    assert response.json()["is_published"] is False


def test_viewer_can_read_but_cannot_modify_inventory(client):
    owner_email = "viewer-test-owner@example.test"
    organization_id = _create_organization(client, owner_email)

    _set_organization_status(organization_id, "verified")

    client.cookies.clear()
    _login(client, owner_email)

    created = client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    )
    assert created.status_code == 201
    listing_id = created.json()["id"]

    viewer_email = "inventory-viewer@example.test"
    viewer_id = _create_account(viewer_email)
    _add_member(organization_id, viewer_id, "viewer")

    client.cookies.clear()
    _login(client, viewer_email)

    assert client.get(_listing_url(organization_id)).status_code == 200
    assert client.get(
        f"{_listing_url(organization_id)}/{listing_id}"
    ).status_code == 200

    assert client.post(
        _listing_url(organization_id),
        json=_listing_payload(title="Viewer listing"),
    ).status_code == 403

    assert client.patch(
        f"{_listing_url(organization_id)}/{listing_id}",
        json={"title": "Viewer changed title"},
    ).status_code == 403

    assert client.post(
        f"{_listing_url(organization_id)}/{listing_id}/publish"
    ).status_code == 403


def test_inactive_membership_cannot_access_inventory(client):
    owner_email = "inactive-inventory-owner@example.test"
    organization_id = _create_organization(client, owner_email)

    member_email = "inactive-inventory-member@example.test"
    member_id = _create_account(member_email)
    _add_member(organization_id, member_id, "inventory_manager", "suspended")

    client.cookies.clear()
    _login(client, member_email)

    assert client.get(_listing_url(organization_id)).status_code == 404
    assert client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    ).status_code == 404


def test_listing_access_is_scoped_to_organization(client):
    first_owner = "inventory-first-owner@example.test"
    first_org = _create_organization(client, first_owner)

    created = client.post(
        _listing_url(first_org),
        json=_listing_payload(),
    )
    assert created.status_code == 201
    listing_id = created.json()["id"]

    second_owner = "inventory-second-owner@example.test"
    second_org = _create_organization(client, second_owner)

    assert second_org != first_org

    assert client.get(
        f"{_listing_url(second_org)}/{listing_id}"
    ).status_code == 404

    assert client.patch(
        f"{_listing_url(second_org)}/{listing_id}",
        json={"title": "Unauthorized cross-organization edit"},
    ).status_code == 404

    assert client.post(
        f"{_listing_url(second_org)}/{listing_id}/publish"
    ).status_code == 404

    assert client.post(
        f"{_listing_url(second_org)}/{listing_id}/unpublish"
    ).status_code == 404

    assert client.patch(
        f"{_listing_url(second_org)}/{listing_id}/availability",
        json={"availability_status": "sold"},
    ).status_code == 404


def test_pending_organization_can_edit_drafts_but_cannot_publish(client):
    owner_email = "pending-inventory-owner@example.test"
    organization_id = _create_organization(client, owner_email)

    created = client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    )
    assert created.status_code == 201
    listing_id = created.json()["id"]

    edited = client.patch(
        f"{_listing_url(organization_id)}/{listing_id}",
        json={"title": "Updated pending organization draft"},
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "Updated pending organization draft"

    publish = client.post(
        f"{_listing_url(organization_id)}/{listing_id}/publish"
    )
    assert publish.status_code == 403

    assert client.get(
        f"{_listing_url(organization_id)}/{listing_id}"
    ).json()["is_published"] is False


def test_verified_organization_can_publish_and_unpublish(client):
    owner_email = "verified-inventory-owner@example.test"
    organization_id = _create_organization(client, owner_email)

    created = client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    )
    assert created.status_code == 201
    listing_id = created.json()["id"]

    assert client.get("/api/buyer/inventory").json() == []

    _set_organization_status(organization_id, "verified")

    published = client.post(
        f"{_listing_url(organization_id)}/{listing_id}/publish"
    )
    assert published.status_code == 200
    assert published.json()["is_published"] is True

    buyer_inventory = client.get("/api/buyer/inventory")
    assert buyer_inventory.status_code == 200
    assert listing_id in {item["id"] for item in buyer_inventory.json()}

    duplicate_publish = client.post(
        f"{_listing_url(organization_id)}/{listing_id}/publish"
    )
    assert duplicate_publish.status_code == 409

    unpublished = client.post(
        f"{_listing_url(organization_id)}/{listing_id}/unpublish"
    )
    assert unpublished.status_code == 200
    assert unpublished.json()["is_published"] is False

    buyer_ids = {
        item["id"] for item in client.get("/api/buyer/inventory").json()
    }
    assert listing_id not in buyer_ids

    duplicate_unpublish = client.post(
        f"{_listing_url(organization_id)}/{listing_id}/unpublish"
    )
    assert duplicate_unpublish.status_code == 409

    with Session(engine) as db:
        actions = list(
            db.scalars(
                select(DeveloperOrganizationAuditLog.action).where(
                    DeveloperOrganizationAuditLog.organization_id == organization_id,
                    DeveloperOrganizationAuditLog.action.in_(
                        [
                            "inventory.listing_published",
                            "inventory.listing_unpublished",
                        ]
                    ),
                )
            ).all()
        )

    assert sorted(actions) == [
        "inventory.listing_published",
        "inventory.listing_unpublished",
    ]


def test_availability_changes_are_audited_and_affect_public_inventory(client):
    owner_email = "availability-inventory-owner@example.test"
    organization_id = _create_organization(client, owner_email)
    _set_organization_status(organization_id, "verified")

    created = client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    )
    assert created.status_code == 201
    listing_id = created.json()["id"]

    published = client.post(
        f"{_listing_url(organization_id)}/{listing_id}/publish"
    )
    assert published.status_code == 200

    changed = client.patch(
        f"{_listing_url(organization_id)}/{listing_id}/availability",
        json={"availability_status": "reserved"},
    )
    assert changed.status_code == 200
    assert changed.json()["availability_status"] == "reserved"

    buyer_ids = {
        item["id"] for item in client.get("/api/buyer/inventory").json()
    }
    assert listing_id not in buyer_ids

    unchanged = client.patch(
        f"{_listing_url(organization_id)}/{listing_id}/availability",
        json={"availability_status": "reserved"},
    )
    assert unchanged.status_code == 409

    changed_again = client.patch(
        f"{_listing_url(organization_id)}/{listing_id}/availability",
        json={"availability_status": "available"},
    )
    assert changed_again.status_code == 200

    buyer_ids = {
        item["id"] for item in client.get("/api/buyer/inventory").json()
    }
    assert listing_id in buyer_ids

    with Session(engine) as db:
        audit = db.scalar(
            select(DeveloperOrganizationAuditLog).where(
                DeveloperOrganizationAuditLog.organization_id == organization_id,
                DeveloperOrganizationAuditLog.action == "inventory.availability_changed",
            )
        )

        assert audit is not None
        assert audit.details == {
            "listing_id": listing_id,
            "previous_status": "available",
            "new_status": "reserved",
        }


@pytest.mark.parametrize("status", ["rejected", "suspended"])
def test_rejected_or_suspended_organization_cannot_modify_inventory(
    client,
    status,
):
    owner_email = f"{status}-inventory-owner@example.test"
    organization_id = _create_organization(client, owner_email)
    _set_organization_status(organization_id, status)

    response = client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    )

    assert response.status_code == 403


@pytest.mark.parametrize("status", ["rejected", "suspended"])
def test_restricted_organization_can_unpublish_existing_listing(client, status):
    owner_email = f"restricted-takedown-{status}@example.test"
    organization_id = _create_organization(client, owner_email)
    _set_organization_status(organization_id, "verified")

    created = client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    )
    assert created.status_code == 201
    listing_id = created.json()["id"]

    published = client.post(
        f"{_listing_url(organization_id)}/{listing_id}/publish"
    )
    assert published.status_code == 200

    buyer_ids = {
        item["id"] for item in client.get("/api/buyer/inventory").json()
    }
    assert listing_id in buyer_ids

    _set_organization_status(organization_id, status)

    unpublished = client.post(
        f"{_listing_url(organization_id)}/{listing_id}/unpublish"
    )
    assert unpublished.status_code == 200
    assert unpublished.json()["is_published"] is False

    buyer_ids = {
        item["id"] for item in client.get("/api/buyer/inventory").json()
    }
    assert listing_id not in buyer_ids

    blocked_edit = client.patch(
        f"{_listing_url(organization_id)}/{listing_id}",
        json={"title": "Should remain blocked"},
    )
    assert blocked_edit.status_code == 403


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "   "},
        {"title": ""},
        {"property_type": "penthouse"},
        {"city": "   "},
        {"state": ""},
        {"price_inr": -1},
        {"bedrooms": -1},
        {"area_sqft": 0},
        {"unexpected_field": "not allowed"},
    ],
)
def test_invalid_listing_payload_is_rejected(client, overrides):
    owner_email = "invalid-inventory-owner@example.test"
    organization_id = _create_organization(client, owner_email)

    response = client.post(
        _listing_url(organization_id),
        json=_listing_payload(**overrides),
    )

    assert response.status_code == 422


def test_listing_update_rejects_empty_and_null_required_fields(client):
    owner_email = "update-validation-owner@example.test"
    organization_id = _create_organization(client, owner_email)

    created = client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    )
    assert created.status_code == 201
    listing_id = created.json()["id"]
    detail_url = f"{_listing_url(organization_id)}/{listing_id}"

    assert client.patch(detail_url, json={}).status_code == 400

    for field in ("title", "property_type", "city", "state"):
        response = client.patch(detail_url, json={field: None})
        assert response.status_code == 422


def test_listing_updates_record_changed_fields_and_previous_values(client):
    owner_email = "audit-inventory-owner@example.test"
    owner_id = _create_account(owner_email)
    _login(client, owner_email)

    organization_response = client.post(
        "/api/developer-organizations",
        json=_organization_payload(),
    )
    assert organization_response.status_code == 201
    organization_id = organization_response.json()["id"]

    created = client.post(
        _listing_url(organization_id),
        json=_listing_payload(),
    )
    assert created.status_code == 201
    listing_id = created.json()["id"]

    updated = client.patch(
        f"{_listing_url(organization_id)}/{listing_id}",
        json={
            "title": "Audited updated title",
            "price_inr": 9200000,
        },
    )

    assert updated.status_code == 200
    assert updated.json()["title"] == "Audited updated title"
    assert updated.json()["price_inr"] == 9200000

    with Session(engine) as db:
        audit = db.scalar(
            select(DeveloperOrganizationAuditLog).where(
                DeveloperOrganizationAuditLog.organization_id == organization_id,
                DeveloperOrganizationAuditLog.action == "inventory.listing_updated",
            )
        )

        assert audit is not None
        assert audit.actor_account_id == owner_id
        assert audit.details == {
            "listing_id": listing_id,
            "changed_fields": ["price_inr", "title"],
            "previous_values": {
                "title": "Test Apartment in Kolkata",
                "price_inr": 8500000,
            },
        }


def test_legacy_listings_without_organization_remain_public(client):
    with Session(engine) as db:
        legacy = PropertyListing(
            title="Legacy Public Listing",
            property_type="apartment",
            city="Kolkata",
            state="West Bengal",
            price_inr=5000000,
            bedrooms=2,
            area_sqft=900,
            availability_status="available",
            is_published=True,
        )
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id

    response = client.get("/api/buyer/inventory")

    assert response.status_code == 200
    assert legacy_id in {item["id"] for item in response.json()}
