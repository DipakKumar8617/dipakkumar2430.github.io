from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.database import SessionLocal
from backend.app.models import BuyerSiteVisit, PropertyListing
from tests.test_buyer_profile import login_with_otp


def create_persisted_listing(title="Site visit test property", **overrides):
    values = {
        "title": title,
        "property_type": "apartment",
        "city": "Kolkata",
        "state": "West Bengal",
        "price_inr": 12500000,
        "bedrooms": 3,
        "area_sqft": 1420,
        "description": "Isolated site visit API test fixture.",
        "image_url": None,
        "availability_status": "available",
        "is_published": True,
    }
    values.update(overrides)

    with SessionLocal() as db:
        listing = PropertyListing(**values)
        db.add(listing)
        db.commit()
        db.refresh(listing)
        return listing.id


def future_visit_time():
    return (
        datetime.now(timezone.utc) + timedelta(days=2)
    ).replace(microsecond=0).isoformat()


def test_site_visit_requires_buyer_authentication(client):
    listing_id = create_persisted_listing()

    assert client.get("/api/buyer/site-visits").status_code == 401
    assert client.post(
        f"/api/buyer/site-visits/{listing_id}",
        json={"scheduled_at": future_visit_time()},
    ).status_code == 401
    assert client.patch(
        "/api/buyer/site-visits/1/cancel"
    ).status_code == 401


def test_buyer_can_request_list_and_cancel_site_visit(client):
    login_with_otp(client, "+919876500101")
    listing_id = create_persisted_listing()

    response = client.post(
        f"/api/buyer/site-visits/{listing_id}",
        json={
            "scheduled_at": future_visit_time(),
            "message": "Please confirm the available visit time.",
        },
    )

    assert response.status_code == 201
    created = response.json()
    assert created["property_listing_id"] == listing_id
    assert created["status"] == "requested"
    assert created["message"] == "Please confirm the available visit time."
    assert created["property"]["id"] == listing_id
    assert created["scheduled_at"].endswith("+00:00")

    with SessionLocal() as db:
        stored = db.scalar(
            select(BuyerSiteVisit).where(BuyerSiteVisit.id == created["id"])
        )
        assert stored is not None
        assert stored.status == "requested"

    listing_response = client.get("/api/buyer/site-visits")
    assert listing_response.status_code == 200
    visits = listing_response.json()
    assert len(visits) == 1
    assert visits[0]["id"] == created["id"]

    cancel_response = client.patch(
        f"/api/buyer/site-visits/{created['id']}/cancel"
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_published": False},
        {"availability_status": "reserved"},
        {"availability_status": "sold"},
    ],
)
def test_buyer_cannot_request_visit_for_unavailable_listing(client, overrides):
    login_with_otp(client, "+919876500102")
    listing_id = create_persisted_listing(**overrides)

    response = client.post(
        f"/api/buyer/site-visits/{listing_id}",
        json={"scheduled_at": future_visit_time()},
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"scheduled_at": "2020-01-01T10:00:00+05:30"},
        {"scheduled_at": "2030-01-01T10:00:00"},
        {
            "scheduled_at": "2030-01-01T10:00:00+05:30",
            "status": "confirmed",
        },
        {
            "scheduled_at": "2030-01-01T10:00:00+05:30",
            "message": "x" * 1001,
        },
    ],
)
def test_site_visit_rejects_invalid_input(client, payload):
    login_with_otp(client, "+919876500103")
    listing_id = create_persisted_listing()

    response = client.post(
        f"/api/buyer/site-visits/{listing_id}",
        json=payload,
    )

    assert response.status_code == 422


def test_buyer_cannot_view_or_cancel_another_buyers_visit(client):
    login_with_otp(client, "+919876500104")
    listing_id = create_persisted_listing()

    create_response = client.post(
        f"/api/buyer/site-visits/{listing_id}",
        json={"scheduled_at": future_visit_time()},
    )
    assert create_response.status_code == 201
    visit_id = create_response.json()["id"]

    login_with_otp(client, "+919876500105")

    list_response = client.get("/api/buyer/site-visits")
    assert list_response.status_code == 200
    assert list_response.json() == []

    cancel_response = client.patch(
        f"/api/buyer/site-visits/{visit_id}/cancel"
    )
    assert cancel_response.status_code == 404

    login_with_otp(client, "+919876500104")
    owner_response = client.get("/api/buyer/site-visits")
    assert owner_response.status_code == 200
    assert [visit["id"] for visit in owner_response.json()] == [visit_id]
