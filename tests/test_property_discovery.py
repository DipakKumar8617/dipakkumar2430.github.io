
from backend.app.database import SessionLocal
from backend.app.models import PropertyListing


def add_listing(db, title, **overrides):
    values = {
        "title": title,
        "property_type": "apartment",
        "city": "Kolkata",
        "state": "West Bengal",
        "price_inr": 10_000_000,
        "bedrooms": 2,
        "area_sqft": 1000,
        "description": None,
        "image_url": None,
        "availability_status": "available",
        "is_published": True,
    }
    values.update(overrides)
    listing = PropertyListing(**values)
    db.add(listing)
    return listing


def test_inventory_search_applies_keyword_and_filters(client):
    with SessionLocal() as db:
        matching = add_listing(
            db,
            "Metro View Apartment",
            description="Close to metro station",
            price_inr=12_500_000,
            bedrooms=3,
            area_sqft=1400,
        )
        add_listing(
            db,
            "Metro Villa",
            property_type="villa",
            price_inr=20_000_000,
            bedrooms=3,
            area_sqft=1400,
        )
        add_listing(
            db,
            "Metro Apartment Outside City",
            city="Durgapur",
            price_inr=12_500_000,
            bedrooms=3,
            area_sqft=1400,
        )
        db.commit()
        matching_id = matching.id

    response = client.get(
        "/api/buyer/inventory/search",
        params={
            "q": "metro",
            "city": "KOLKATA",
            "state": "west bengal",
            "property_type": "apartment",
            "min_price": 10_000_000,
            "max_price": 15_000_000,
            "min_bedrooms": 2,
            "max_bedrooms": 3,
            "min_area": 1000,
            "max_area": 1500,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 1
    assert result["total_pages"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == matching_id


def test_inventory_search_sorts_and_paginates(client):
    with SessionLocal() as db:
        add_listing(db, "Low Price", price_inr=5_000_000)
        add_listing(db, "Mid Price", price_inr=10_000_000)
        add_listing(db, "High Price", price_inr=15_000_000)
        db.commit()

    first_page = client.get(
        "/api/buyer/inventory/search",
        params={"sort": "price_asc", "page": 1, "page_size": 2},
    )

    assert first_page.status_code == 200
    first = first_page.json()
    assert first["total"] == 3
    assert first["page"] == 1
    assert first["page_size"] == 2
    assert first["total_pages"] == 2
    assert [item["title"] for item in first["items"]] == [
        "Low Price",
        "Mid Price",
    ]

    second_page = client.get(
        "/api/buyer/inventory/search",
        params={"sort": "price_asc", "page": 2, "page_size": 2},
    )

    assert second_page.status_code == 200
    second = second_page.json()
    assert second["total"] == 3
    assert second["page"] == 2
    assert [item["title"] for item in second["items"]] == ["High Price"]


def test_inventory_search_excludes_unpublished_reserved_and_sold(client):
    with SessionLocal() as db:
        add_listing(db, "Harbour Available")
        add_listing(
            db,
            "Harbour Draft",
            is_published=False,
        )
        add_listing(
            db,
            "Harbour Reserved",
            availability_status="reserved",
        )
        add_listing(
            db,
            "Harbour Sold",
            availability_status="sold",
        )
        db.commit()

    response = client.get(
        "/api/buyer/inventory/search",
        params={"q": "Harbour"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 1
    assert [item["title"] for item in result["items"]] == [
        "Harbour Available"
    ]


def test_inventory_search_rejects_invalid_ranges(client):
    invalid_queries = [
        {"min_price": 20_000_000, "max_price": 10_000_000},
        {"min_bedrooms": 4, "max_bedrooms": 2},
        {"min_area": 2000, "max_area": 1000},
    ]

    for params in invalid_queries:
        response = client.get(
            "/api/buyer/inventory/search",
            params=params,
        )
        assert response.status_code == 422
