
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.app.database import engine
from backend.app.models import (
    PropertyChannelPartnerAssignment,
    PropertyInventoryItem,
    RealEstateTransaction,
)
from tests.test_staff_site_visits import _login, _seed

BASE = "/api/channel-partners"


def test_channel_partner_endpoints_require_authentication(client):
    assert client.get(f"{BASE}/dashboard").status_code == 401
    assert client.get(f"{BASE}/inventory").status_code == 401
    assert client.get(f"{BASE}/transactions").status_code == 401
    assert client.get(f"{BASE}/site-visits").status_code == 401


def test_non_channel_partner_cannot_access_cp_os(client):
    data = _seed()
    _login(client, "developer", data["developer"][1])

    assert client.get(f"{BASE}/dashboard").status_code == 403
    assert client.get(f"{BASE}/inventory").status_code == 403
    assert client.get(f"{BASE}/transactions").status_code == 403


def test_dashboard_and_inventory_are_scoped_to_active_assignment(client):
    data = _seed()
    _login(client, "channel partner", data["cp"][1])

    dashboard = client.get(f"{BASE}/dashboard")
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.json()["metrics"]["active_assignments"] == 1
    assert dashboard.json()["metrics"]["site_visits"] == 1

    inventory = client.get(f"{BASE}/inventory")
    assert inventory.status_code == 200, inventory.text
    assert len(inventory.json()) == 1
    assert inventory.json()[0]["id"] == data["listing_id"]


def test_other_cp_cannot_see_another_partners_inventory_or_visits(client):
    data = _seed()
    _login(client, "channel partner", data["other_cp"][1])

    assert client.get(f"{BASE}/inventory").json() == []
    assert client.get(f"{BASE}/site-visits").json() == []
    assert client.get(
        f"{BASE}/site-visits/{data['visit_id']}"
    ).status_code == 404


def test_revoked_assignment_removes_inventory_and_visit_access(client):
    data = _seed()

    with Session(engine) as db:
        assignment = db.get(
            PropertyChannelPartnerAssignment,
            data["assignment_id"],
        )
        assignment.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        assignment.ended_by_account_id = data["admin"][0]
        db.commit()

    _login(client, "channel partner", data["cp"][1])

    assert client.get(f"{BASE}/inventory").json() == []
    assert client.get(f"{BASE}/site-visits").json() == []
    assert client.get(
        f"{BASE}/site-visits/{data['visit_id']}"
    ).status_code == 404


def test_historical_transaction_remains_visible_after_assignment_ends(client):
    data = _seed()

    with Session(engine) as db:
        inventory = PropertyInventoryItem(
            property_listing_id=data["listing_id"],
            item_type="single",
            unit_code=None,
            price_inr=12500000,
            availability_status="available",
        )
        db.add(inventory)
        db.flush()

        transaction = RealEstateTransaction(
            buyer_account_id=data["buyer"][0],
            property_listing_id=data["listing_id"],
            inventory_item_id=inventory.id,
            channel_partner_assignment_id=data["assignment_id"],
            channel_partner_account_id=data["cp"][0],
            transaction_type="direct_booking",
            status="initiated",
        )
        db.add(transaction)

        assignment = db.get(
            PropertyChannelPartnerAssignment,
            data["assignment_id"],
        )
        assignment.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        assignment.ended_by_account_id = data["admin"][0]
        db.commit()
        transaction_id = transaction.id

    _login(client, "channel partner", data["cp"][1])

    response = client.get(f"{BASE}/transactions")
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == transaction_id
    assert (
        response.json()[0]["channel_partner_assignment_id"]
        == data["assignment_id"]
    )
