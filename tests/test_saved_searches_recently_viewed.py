
from datetime import datetime

import pytest
from sqlalchemy import func, select

from backend.app.database import SessionLocal
from backend.app.models import (
    PropertyListing,
    RecentlyViewedProperty,
    SavedPropertySearch,
)
from tests.test_buyer_profile import login_with_otp


def create_listing(db, title, **overrides):
    values = {
        "title": title,
        "property_type": "apartment",
        "city": "Kolkata",
        "state": "West Bengal",
        "price_inr": 12500000,
        "bedrooms": 3,
        "area_sqft": 1420,
        "description": "Saved search and recent-view test fixture.",
        "image_url": None,
        "availability_status": "available",
        "is_published": True,
    }
    values.update(overrides)

    listing = PropertyListing(**values)
    db.add(listing)
    return listing


def create_persisted_listing(title, **overrides):
    with SessionLocal() as db:
        listing = create_listing(db, title, **overrides)
        db.commit()
        return listing.id


def saved_search_payload(name="Kolkata Apartments"):
    return {
        "name": name,
        "criteria": {
            "city": "Kolkata",
            "min_price": 5000000,
            "max_price": 15000000,
            "property_type": "apartment",
            "sort": "price_asc",
        },
    }


def save_search(client, name="Kolkata Apartments"):
    response = client.post(
        "/api/buyer/saved-searches",
        json=saved_search_payload(name),
    )
    return response


def record_view(client, property_id):
    return client.post(
        f"/api/buyer/recently-viewed/{property_id}"
    )


def test_saved_search_and_recent_view_endpoints_require_authentication(client):
    payload = saved_search_payload()

    assert client.post(
        "/api/buyer/saved-searches",
        json=payload,
    ).status_code == 401

    assert client.get("/api/buyer/saved-searches").status_code == 401

    assert client.delete(
        "/api/buyer/saved-searches/1"
    ).status_code == 401

    assert client.post(
        "/api/buyer/recently-viewed/1"
    ).status_code == 401

    assert client.get(
        "/api/buyer/recently-viewed"
    ).status_code == 401


def test_buyer_can_create_and_retrieve_saved_search(client):
    login_with_otp(client, "+919876543251")

    response = save_search(client, "  Kolkata Apartments  ")

    assert response.status_code == 201

    created = response.json()

    assert created["name"] == "Kolkata Apartments"
    assert created["criteria"] == {
        "city": "Kolkata",
        "min_price": 5000000,
        "max_price": 15000000,
        "property_type": "apartment",
        "sort": "price_asc",
    }
    assert created["id"] > 0
    assert created["created_at"]
    assert created["updated_at"]

    listing_response = client.get("/api/buyer/saved-searches")

    assert listing_response.status_code == 200
    assert len(listing_response.json()) == 1
    assert listing_response.json()[0]["id"] == created["id"]


def test_duplicate_saved_search_name_is_rejected(client):
    login_with_otp(client, "+919876543252")

    first = save_search(client, "Kolkata Apartments")
    duplicate = save_search(client, "Kolkata Apartments")

    assert first.status_code == 201
    assert duplicate.status_code == 409

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(SavedPropertySearch)
        )
        assert count == 1


@pytest.mark.parametrize(
    "payload",
    [
        {
            "name": "Invalid Price",
            "criteria": {
                "min_price": 15000000,
                "max_price": 5000000,
            },
        },
        {
            "name": "Unsupported Pagination",
            "criteria": {
                "city": "Kolkata",
                "page": 2,
            },
        },
        {
            "name": "Unsupported Filter",
            "criteria": {
                "developer": "Unknown Developer",
            },
        },
        {
            "name": "Invalid Property Type",
            "criteria": {
                "property_type": "warehouse_only",
            },
        },
        {
            "name": "   ",
            "criteria": {
                "city": "Kolkata",
            },
        },
    ],
)
def test_saved_search_rejects_invalid_criteria_and_names(client, payload):
    login_with_otp(client, "+919876543253")

    response = client.post(
        "/api/buyer/saved-searches",
        json=payload,
    )

    assert response.status_code == 422


def test_saved_search_can_reuse_existing_inventory_search_api(client):
    login_with_otp(client, "+919876543254")

    with SessionLocal() as db:
        matching = create_listing(
            db,
            "Kolkata Saved Search Match",
            price_inr=10000000,
            property_type="apartment",
        )
        create_listing(
            db,
            "Kolkata Expensive Apartment",
            price_inr=30000000,
        )
        db.commit()
        matching_id = matching.id

    saved = save_search(client)

    assert saved.status_code == 201

    criteria = saved.json()["criteria"]

    response = client.get(
        "/api/buyer/inventory/search",
        params=criteria,
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == matching_id


def test_saved_searches_are_isolated_between_buyer_accounts(client):
    login_with_otp(client, "+919876543255")

    first = save_search(client, "Kolkata Apartments")
    assert first.status_code == 201
    first_id = first.json()["id"]

    client.post("/api/logout")
    login_with_otp(client, "+919876543256")

    response = client.get("/api/buyer/saved-searches")

    assert response.status_code == 200
    assert response.json() == []

    response = client.delete(
        f"/api/buyer/saved-searches/{first_id}"
    )

    assert response.status_code == 200
    assert response.json() == {
        "removed": False,
        "search_id": first_id,
    }

    # Different buyers can independently use the same search name.
    second = save_search(client, "Kolkata Apartments")

    assert second.status_code == 201
    assert second.json()["id"] != first_id

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(SavedPropertySearch)
            .where(
                SavedPropertySearch.name == "Kolkata Apartments"
            )
        )
        assert count == 2


def test_buyer_can_delete_own_saved_search(client):
    login_with_otp(client, "+919876543257")

    created = save_search(client)
    assert created.status_code == 201

    search_id = created.json()["id"]

    response = client.delete(
        f"/api/buyer/saved-searches/{search_id}"
    )

    assert response.status_code == 200
    assert response.json() == {
        "removed": True,
        "search_id": search_id,
    }

    repeated = client.delete(
        f"/api/buyer/saved-searches/{search_id}"
    )

    assert repeated.status_code == 200
    assert repeated.json() == {
        "removed": False,
        "search_id": search_id,
    }

    assert client.get("/api/buyer/saved-searches").json() == []


def test_buyer_can_record_and_repeat_a_property_view(client):
    login_with_otp(client, "+919876543258")

    property_id = create_persisted_listing(
        "Recently Viewed Apartment"
    )

    first = record_view(client, property_id)

    assert first.status_code == 201
    assert first.json() == {
        "tracked": True,
        "already_viewed": False,
        "property_id": property_id,
    }

    old_timestamp = datetime(2000, 1, 1)

    with SessionLocal() as db:
        record = db.scalar(
            select(RecentlyViewedProperty).where(
                RecentlyViewedProperty.property_listing_id
                == property_id
            )
        )
        assert record is not None
        record.viewed_at = old_timestamp
        db.commit()

    repeated = record_view(client, property_id)

    assert repeated.status_code == 200
    assert repeated.json() == {
        "tracked": True,
        "already_viewed": True,
        "property_id": property_id,
    }

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(RecentlyViewedProperty)
            .where(
                RecentlyViewedProperty.property_listing_id
                == property_id
            )
        )

        record = db.scalar(
            select(RecentlyViewedProperty).where(
                RecentlyViewedProperty.property_listing_id
                == property_id
            )
        )

        assert count == 1
        assert record.viewed_at > old_timestamp


@pytest.mark.parametrize(
    ("availability_status", "is_published"),
    [
        ("reserved", True),
        ("sold", True),
        ("available", False),
    ],
)
def test_recent_view_tracking_rejects_unavailable_properties(
    client, availability_status, is_published
):
    login_with_otp(client, "+919876543259")

    property_id = create_persisted_listing(
        "Unavailable Recent View Property",
        availability_status=availability_status,
        is_published=is_published,
    )

    response = record_view(client, property_id)

    assert response.status_code == 404

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(RecentlyViewedProperty)
            .where(
                RecentlyViewedProperty.property_listing_id
                == property_id
            )
        )
        assert count == 0


def test_recently_viewed_is_ordered_and_excludes_unpublished_properties(client):
    login_with_otp(client, "+919876543260")

    first_id = create_persisted_listing("First Viewed Property")
    second_id = create_persisted_listing("Second Viewed Property")
    hidden_id = create_persisted_listing("Later Unpublished Property")

    for property_id in (first_id, second_id, hidden_id):
        response = record_view(client, property_id)
        assert response.status_code == 201

    with SessionLocal() as db:
        first = db.scalar(
            select(RecentlyViewedProperty).where(
                RecentlyViewedProperty.property_listing_id == first_id
            )
        )
        second = db.scalar(
            select(RecentlyViewedProperty).where(
                RecentlyViewedProperty.property_listing_id == second_id
            )
        )
        hidden = db.get(PropertyListing, hidden_id)

        first.viewed_at = datetime(2000, 1, 1)
        second.viewed_at = datetime(2001, 1, 1)
        hidden.is_published = False

        db.commit()

    # Revisit the first property so it becomes the most recent.
    repeated = record_view(client, first_id)
    assert repeated.status_code == 200

    response = client.get("/api/buyer/recently-viewed")

    assert response.status_code == 200

    properties = [
        item["property"]
        for item in response.json()
    ]

    assert [item["id"] for item in properties] == [
        first_id,
        second_id,
    ]

    assert hidden_id not in [
        item["id"] for item in properties
    ]


def test_recently_viewed_is_isolated_between_buyer_accounts(client):
    login_with_otp(client, "+919876543261")

    property_id = create_persisted_listing(
        "Shared Property Across Buyer Accounts"
    )

    first = record_view(client, property_id)
    assert first.status_code == 201

    client.post("/api/logout")
    login_with_otp(client, "+919876543262")

    response = client.get("/api/buyer/recently-viewed")

    assert response.status_code == 200
    assert response.json() == []

    second = record_view(client, property_id)

    assert second.status_code == 201
    assert second.json()["already_viewed"] is False

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(RecentlyViewedProperty)
            .where(
                RecentlyViewedProperty.property_listing_id
                == property_id
            )
        )
        assert count == 2


def test_recently_viewed_limit_is_validated(client):
    login_with_otp(client, "+919876543263")

    assert client.get(
        "/api/buyer/recently-viewed?limit=1"
    ).status_code == 200

    assert client.get(
        "/api/buyer/recently-viewed?limit=50"
    ).status_code == 200

    assert client.get(
        "/api/buyer/recently-viewed?limit=0"
    ).status_code == 422

    assert client.get(
        "/api/buyer/recently-viewed?limit=51"
    ).status_code == 422
