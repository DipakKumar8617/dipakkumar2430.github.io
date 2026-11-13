from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import engine
from backend.app.main import hash_password
from tests.test_buyer_profile import login_with_otp
from backend.app.models import (
    AuthIdentity,
    BuyerSiteVisit,
    DeveloperOrganization,
    DeveloperOrganizationMembership,
    PropertyChannelPartnerAssignment,
    PropertyListing,
    StaffAuditLog,
    UserAccount,
)

TEST_PASSWORD = "StrongAdminPass123!"
BASE_URL = "/api/staff/site-visits"


def _create_account(db, role, email, name):
    account = UserAccount(
        full_name=name,
        email=email,
        password_hash=hash_password(TEST_PASSWORD),
        role=role,
        phone={
            "admin@staff.test": "+919876543210",
            "developer@staff.test": "+919876543211",
            "viewer@staff.test": "+919876543212",
            "cp@staff.test": "+919876543213",
            "othercp@staff.test": "+919876543214",
            "buyer@staff.test": "+919876543215",
        }[email],
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
    db.flush()
    return account


def _login(client, role, email):
    response = client.post(
        "/api/login",
        json={
            "identifier": email,
            "password": TEST_PASSWORD,
            "role": {
                "admin": "Admin",
                "developer": "Developer",
                "channel partner": "Channel Partner",
            }[role],
        },
    )
    assert response.status_code == 200, response.text
    return response


def _seed():
    with Session(engine) as db:
        admin = _create_account(db, "admin", "admin@staff.test", "Admin")
        developer = _create_account(
            db, "developer", "developer@staff.test", "Developer"
        )
        viewer = _create_account(
            db, "developer", "viewer@staff.test", "Viewer"
        )
        cp = _create_account(
            db, "channel partner", "cp@staff.test", "Assigned CP"
        )
        other_cp = _create_account(
            db, "channel partner", "othercp@staff.test", "Other CP"
        )
        buyer = _create_account(
            db, "buyer", "buyer@staff.test", "Private Buyer"
        )

        organization = DeveloperOrganization(
            legal_name="Test Developer Private Limited",
            display_name="Test Developer",
            entity_type="private_limited",
            country_code="IN",
            registered_address="12 Test Road",
            city="Kolkata",
            state="West Bengal",
            postal_code="700001",
            contact_email="org@staff.test",
            contact_phone="+919876543216",
            submitted_by_account_id=developer.id,
        )
        db.add(organization)
        db.flush()

        db.add_all([
            DeveloperOrganizationMembership(
                organization_id=organization.id,
                account_id=developer.id,
                role="owner",
                status="active",
                created_by_account_id=developer.id,
            ),
            DeveloperOrganizationMembership(
                organization_id=organization.id,
                account_id=viewer.id,
                role="viewer",
                status="active",
                created_by_account_id=developer.id,
            ),
        ])

        listing = PropertyListing(
            developer_organization_id=organization.id,
            title="Staff Visit Test Property",
            property_type="apartment",
            city="Kolkata",
            state="West Bengal",
            price_inr=12500000,
            bedrooms=3,
            area_sqft=1420,
            description="Test property",
            image_url=None,
            availability_status="available",
            is_published=True,
        )
        db.add(listing)
        db.flush()

        visit = BuyerSiteVisit(
            account_id=buyer.id,
            property_listing_id=listing.id,
            scheduled_at=(
                datetime.now(timezone.utc) + timedelta(days=2)
            ).replace(tzinfo=None),
            message="Please call before visiting.",
        )
        db.add(visit)
        db.flush()

        assignment = PropertyChannelPartnerAssignment(
            property_listing_id=listing.id,
            channel_partner_account_id=cp.id,
            assigned_by_account_id=admin.id,
        )
        db.add(assignment)
        db.commit()

        return {
            "admin": (admin.id, admin.email),
            "developer": (developer.id, developer.email),
            "viewer": (viewer.id, viewer.email),
            "cp": (cp.id, cp.email),
            "other_cp": (other_cp.id, other_cp.email),
            "buyer": (buyer.id, buyer.email),
            "listing_id": listing.id,
            "visit_id": visit.id,
            "assignment_id": assignment.id,
        }


def test_staff_site_visits_require_staff_authentication(client):
    response = client.get(BASE_URL)
    assert response.status_code == 401

    data = _seed()
    login_with_otp(client, "+919876543215")

    response = client.get(BASE_URL)
    assert response.status_code == 403


def test_developer_owner_can_view_buyer_contact(client):
    data = _seed()
    _login(client, "developer", data["developer"][1])

    response = client.get(BASE_URL)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1

    buyer = response.json()[0]["buyer"]
    assert buyer["full_name"] == "Private Buyer"
    assert buyer["email"] == "buyer@staff.test"
    assert buyer["phone"] == "+919876543215"


def test_viewer_sees_visit_without_buyer_contact_and_cannot_update(client):
    data = _seed()
    _login(client, "developer", data["viewer"][1])

    response = client.get(BASE_URL)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1

    visit = response.json()[0]
    assert "buyer" not in visit
    assert "Private Buyer" not in str(visit)
    assert "buyer@staff.test" not in str(visit)
    assert "+919876543215" not in str(visit)

    response = client.patch(
        f"{BASE_URL}/{data['visit_id']}/status",
        json={"status": "confirmed"},
    )
    assert response.status_code == 403


def test_channel_partner_access_is_assignment_scoped(client):
    data = _seed()

    _login(client, "channel partner", data["cp"][1])
    response = client.get(BASE_URL)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["buyer"]["email"] == "buyer@staff.test"

    client.cookies.clear()
    _login(client, "channel partner", data["other_cp"][1])

    assert client.get(BASE_URL).json() == []

    response = client.get(f"{BASE_URL}/{data['visit_id']}")
    assert response.status_code == 404

    response = client.patch(
        f"{BASE_URL}/{data['visit_id']}/status",
        json={"status": "confirmed"},
    )
    assert response.status_code == 404



def test_revoked_channel_partner_loses_access_immediately(client):
    data = _seed()

    ended_at = datetime.now(timezone.utc).replace(tzinfo=None)

    with Session(engine) as db:
        assignment = db.get(
            PropertyChannelPartnerAssignment,
            data["assignment_id"],
        )

        assignment.ended_at = ended_at
        assignment.ended_by_account_id = data["admin"][0]

        db.add(
            PropertyChannelPartnerAssignment(
                property_listing_id=data["listing_id"],
                channel_partner_account_id=data["other_cp"][0],
                assigned_by_account_id=data["admin"][0],
            )
        )
        db.commit()

        # Verify the original assignment remains in history.
        historical_assignment = db.get(
            PropertyChannelPartnerAssignment,
            data["assignment_id"],
        )
        assert historical_assignment is not None
        assert historical_assignment.ended_at == ended_at
        assert historical_assignment.ended_by_account_id == data["admin"][0]

    # Former CP immediately loses access.
    _login(client, "channel partner", data["cp"][1])

    assert client.get(BASE_URL).json() == []
    assert client.get(
        f"{BASE_URL}/{data['visit_id']}"
    ).status_code == 404

    assert client.patch(
        f"{BASE_URL}/{data['visit_id']}/status",
        json={"status": "confirmed"},
    ).status_code == 404

    # Replacement CP receives access.
    client.cookies.clear()
    _login(client, "channel partner", data["other_cp"][1])

    response = client.get(f"{BASE_URL}/{data['visit_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["buyer"]["email"] == "buyer@staff.test"



def test_admin_can_update_status_and_audit_action_is_recorded(client):
    data = _seed()
    _login(client, "admin", data["admin"][1])

    response = client.patch(
        f"{BASE_URL}/{data['visit_id']}/status",
        json={"status": "confirmed"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "confirmed"

    with Session(engine) as db:
        audit = db.scalar(
            select(StaffAuditLog).where(
                StaffAuditLog.action == "site_visit.status_changed"
            )
        )
        assert audit is not None
        assert audit.actor_account_id == data["admin"][0]
        assert audit.target_account_id == data["buyer"][0]
        assert audit.details["previous_status"] == "requested"
        assert audit.details["new_status"] == "confirmed"


def test_invalid_status_transition_is_rejected(client):
    data = _seed()
    _login(client, "admin", data["admin"][1])

    response = client.patch(
        f"{BASE_URL}/{data['visit_id']}/status",
        json={"status": "completed"},
    )
    assert response.status_code == 409
