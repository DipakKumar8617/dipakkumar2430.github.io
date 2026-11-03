import pytest
from sqlalchemy import func, select

from backend.app.database import SessionLocal
from backend.app.models import PropertyListing, PropertyShortlist
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
        "description": "Property shortlist API test fixture.",
        "image_url": None,
        "availability_status": "available",
        "is_published": True,
    }
    values.update(overrides)

    listing = PropertyListing(**values)
    db.add(listing)
    return listing


def create_and_save_listing(client, title):
    with SessionLocal() as db:
        listing = create_listing(db, title)
        db.commit()
        property_id = listing.id

    response = client.post(f"/api/buyer/shortlist/{property_id}")
    assert response.status_code == 201
    assert response.json()["saved"] is True
    assert response.json()["already_saved"] is False

    return property_id


def compare_properties(client, property_ids):
    params = [("property_ids", property_id) for property_id in property_ids]
    return client.get("/api/buyer/shortlist/compare", params=params)


def test_shortlist_endpoints_require_authentication(client):
    assert client.post("/api/buyer/shortlist/1").status_code == 401
    assert client.get("/api/buyer/shortlist").status_code == 401
    assert client.delete("/api/buyer/shortlist/1").status_code == 401

    comparison = client.get(
        "/api/buyer/shortlist/compare",
        params=[("property_ids", 1), ("property_ids", 2)],
    )
    assert comparison.status_code == 401


def test_buyer_can_save_property_and_duplicate_save_is_idempotent(client):
    login_with_otp(client, "+919876543231")

    with SessionLocal() as db:
        listing = create_listing(db, "Shortlist Test Apartment")
        db.commit()
        property_id = listing.id

    first = client.post(f"/api/buyer/shortlist/{property_id}")
    assert first.status_code == 201
    assert first.json() == {
        "saved": True,
        "already_saved": False,
        "property_id": property_id,
    }

    duplicate = client.post(f"/api/buyer/shortlist/{property_id}")
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "saved": True,
        "already_saved": True,
        "property_id": property_id,
    }

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(PropertyShortlist)
            .where(PropertyShortlist.property_listing_id == property_id)
        )
        assert count == 1


@pytest.mark.parametrize(
    ("availability_status", "is_published"),
    [
        ("reserved", True),
        ("sold", True),
        ("available", False),
    ],
)
def test_buyer_cannot_save_unavailable_or_unpublished_property(
    client, availability_status, is_published
):
    login_with_otp(client, "+919876543232")

    with SessionLocal() as db:
        listing = create_listing(
            db,
            "Unavailable Test Property",
            availability_status=availability_status,
            is_published=is_published,
        )
        db.commit()
        property_id = listing.id

    response = client.post(f"/api/buyer/shortlist/{property_id}")

    assert response.status_code == 404

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(PropertyShortlist)
            .where(PropertyShortlist.property_listing_id == property_id)
        )
        assert count == 0


def test_shortlist_lists_saved_properties_with_current_status(client):
    login_with_otp(client, "+919876543233")

    with SessionLocal() as db:
        reserved_listing = create_listing(
            db, "Originally Available Apartment"
        )
        sold_listing = create_listing(
            db, "Originally Available Villa"
        )
        hidden_listing = create_listing(
            db, "Property Later Unpublished"
        )
        db.commit()

        reserved_id = reserved_listing.id
        sold_id = sold_listing.id
        hidden_id = hidden_listing.id

    for property_id in (reserved_id, sold_id, hidden_id):
        response = client.post(f"/api/buyer/shortlist/{property_id}")
        assert response.status_code == 201

    # Simulate inventory changes after the buyer saved the properties.
    with SessionLocal() as db:
        reserved = db.get(PropertyListing, reserved_id)
        sold = db.get(PropertyListing, sold_id)
        hidden = db.get(PropertyListing, hidden_id)

        reserved.availability_status = "reserved"
        sold.availability_status = "sold"
        hidden.is_published = False

        db.commit()

    response = client.get("/api/buyer/shortlist")

    assert response.status_code == 200

    items = response.json()
    properties = {item["property"]["id"]: item["property"] for item in items}

    assert reserved_id in properties
    assert sold_id in properties
    assert hidden_id not in properties

    assert properties[reserved_id]["availability_status"] == "reserved"
    assert properties[sold_id]["availability_status"] == "sold"


def test_shortlists_are_isolated_between_buyer_accounts(client):
    login_with_otp(client, "+919876543234")

    with SessionLocal() as db:
        first = create_listing(db, "Buyer A Saved Apartment")
        second = create_listing(db, "Buyer A Saved Villa")
        db.commit()

        first_id = first.id
        second_id = second.id

    for property_id in (first_id, second_id):
        response = client.post(f"/api/buyer/shortlist/{property_id}")
        assert response.status_code == 201

    client.post("/api/logout")
    login_with_otp(client, "+919876543235")

    # Buyer B cannot see Buyer A's shortlist.
    response = client.get("/api/buyer/shortlist")
    assert response.status_code == 200
    assert response.json() == []

    # Buyer B cannot remove Buyer A's shortlist entries.
    response = client.delete(f"/api/buyer/shortlist/{first_id}")
    assert response.status_code == 200
    assert response.json()["removed"] is False

    # Buyer B cannot compare properties that Buyer B has not saved.
    response = compare_properties(client, [first_id, second_id])
    assert response.status_code == 404

    # The same property can be independently saved by Buyer B.
    response = client.post(f"/api/buyer/shortlist/{first_id}")
    assert response.status_code == 201

    response = client.get("/api/buyer/shortlist")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["property"]["id"] == first_id

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(PropertyShortlist)
            .where(PropertyShortlist.property_listing_id == first_id)
        )
        assert count == 2


def test_buyer_can_remove_own_shortlisted_property(client):
    login_with_otp(client, "+919876543236")

    property_id = create_and_save_listing(
        client, "Property To Remove"
    )

    response = client.delete(f"/api/buyer/shortlist/{property_id}")

    assert response.status_code == 200
    assert response.json() == {
        "removed": True,
        "property_id": property_id,
    }

    # Removing an already removed property is safe and idempotent.
    repeated = client.delete(f"/api/buyer/shortlist/{property_id}")

    assert repeated.status_code == 200
    assert repeated.json() == {
        "removed": False,
        "property_id": property_id,
    }

    assert client.get("/api/buyer/shortlist").json() == []


def test_buyer_can_compare_two_to_four_saved_properties_in_requested_order(
    client,
):
    login_with_otp(client, "+919876543237")

    property_ids = [
        create_and_save_listing(client, f"Comparison Property {number}")
        for number in range(1, 5)
    ]

    requested_order = [
        property_ids[2],
        property_ids[0],
        property_ids[3],
        property_ids[1],
    ]

    response = compare_properties(client, requested_order)

    assert response.status_code == 200

    data = response.json()
    assert data["count"] == 4
    assert [item["id"] for item in data["items"]] == requested_order


@pytest.mark.parametrize(
    "property_ids",
    [
        [1],
        [1, 2, 3, 4, 5],
        [1, 1],
        [0, 2],
        [-1, 2],
    ],
)
def test_comparison_rejects_invalid_property_selections(
    client, property_ids
):
    login_with_otp(client, "+919876543238")

    response = compare_properties(client, property_ids)

    assert response.status_code == 422


def test_comparison_rejects_properties_not_saved_by_current_buyer(client):
    login_with_otp(client, "+919876543239")

    first_id = create_and_save_listing(client, "Saved Comparison Property")

    with SessionLocal() as db:
        unsaved = create_listing(db, "Not Saved Comparison Property")
        db.commit()
        unsaved_id = unsaved.id

    response = compare_properties(client, [first_id, unsaved_id])

    assert response.status_code == 404


def test_reserved_and_sold_saved_properties_remain_comparable(client):
    login_with_otp(client, "+919876543240")

    first_id = create_and_save_listing(client, "Now Reserved Property")
    second_id = create_and_save_listing(client, "Now Sold Property")

    with SessionLocal() as db:
        db.get(PropertyListing, first_id).availability_status = "reserved"
        db.get(PropertyListing, second_id).availability_status = "sold"
        db.commit()

    response = compare_properties(client, [first_id, second_id])

    assert response.status_code == 200

    data = response.json()
    assert data["count"] == 2
    assert [item["availability_status"] for item in data["items"]] == [
        "reserved",
        "sold",
    ]


@pytest.mark.parametrize("new_status", ["reserved", "sold"])
def test_saving_already_shortlisted_property_remains_idempotent(
    client, new_status
):
    login_with_otp(client, "+919876543241")

    property_id = create_and_save_listing(
        client, "Previously Saved Property"
    )

    with SessionLocal() as db:
        listing = db.get(PropertyListing, property_id)
        listing.availability_status = new_status
        db.commit()

    response = client.post(f"/api/buyer/shortlist/{property_id}")

    assert response.status_code == 200
    assert response.json() == {
        "saved": True,
        "already_saved": True,
        "property_id": property_id,
    }

    with SessionLocal() as db:
        count = db.scalar(
            select(func.count())
            .select_from(PropertyShortlist)
            .where(
                PropertyShortlist.property_listing_id == property_id
            )
        )
        assert count == 1