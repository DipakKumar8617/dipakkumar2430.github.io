
from backend.app.database import SessionLocal
from backend.app.models import PropertyListing


def create_listing(db, title, **overrides):
    values = {
        "title": title,
        "property_type": "apartment",
        "city": "Kolkata",
        "state": "West Bengal",
        "price_inr": 12500000,
        "bedrooms": 3,
        "area_sqft": 1420,
        "description": "Property detail API test fixture.",
        "image_url": None,
        "availability_status": "available",
        "is_published": True,
    }
    values.update(overrides)

    listing = PropertyListing(**values)
    db.add(listing)
    return listing


def test_property_detail_returns_published_available_listing(client):
    with SessionLocal() as db:
        listing = create_listing(db, "Published Detail Apartment")
        db.commit()
        listing_id = listing.id

    response = client.get(f"/api/buyer/inventory/{listing_id}")

    assert response.status_code == 200

    result = response.json()
    assert result["id"] == listing_id
    assert result["title"] == "Published Detail Apartment"
    assert result["property_type"] == "apartment"
    assert result["city"] == "Kolkata"
    assert result["state"] == "West Bengal"
    assert result["price_inr"] == 12500000
    assert result["bedrooms"] == 3
    assert result["area_sqft"] == 1420
    assert result["availability_status"] == "available"


def test_property_detail_hides_unpublished_listing(client):
    with SessionLocal() as db:
        listing = create_listing(
            db,
            "Unpublished Detail Draft",
            is_published=False,
        )
        db.commit()
        listing_id = listing.id

    response = client.get(f"/api/buyer/inventory/{listing_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Property not found."


def test_property_detail_hides_reserved_listing(client):
    with SessionLocal() as db:
        listing = create_listing(
            db,
            "Reserved Detail Apartment",
            availability_status="reserved",
        )
        db.commit()
        listing_id = listing.id

    response = client.get(f"/api/buyer/inventory/{listing_id}")

    assert response.status_code == 404


def test_property_detail_hides_sold_listing(client):
    with SessionLocal() as db:
        listing = create_listing(
            db,
            "Sold Detail Apartment",
            availability_status="sold",
        )
        db.commit()
        listing_id = listing.id

    response = client.get(f"/api/buyer/inventory/{listing_id}")

    assert response.status_code == 404


def test_property_detail_returns_404_for_missing_listing(client):
    response = client.get("/api/buyer/inventory/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Property not found."


def test_property_detail_rejects_invalid_listing_id(client):
    response = client.get("/api/buyer/inventory/0")

    assert response.status_code == 404


def test_property_detail_page_is_served(client):
    response = client.get("/property/42")

    assert response.status_code == 200
    assert "Property Details | RealIQ" in response.text
    assert "/api/buyer/inventory/" in response.text
    assert "Continue with this property" in response.text
    assert "/inventory" in response.text


def test_property_detail_page_rejects_zero_id(client):
    response = client.get("/property/0")

    assert response.status_code == 404
    assert response.json()["detail"] == "Property not found."