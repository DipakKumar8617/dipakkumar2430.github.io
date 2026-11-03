from tests.test_buyer_profile import login_with_otp
from backend.app.database import SessionLocal
from backend.app.models import BuyerEnquiry, PropertyListing


def test_buyer_pages_redirect_unauthenticated_users(client):
    for path in ("/buyer", "/buyer/onboarding", "/buyer/dashboard"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/index.html?next=/buyer#login"


def test_new_buyer_is_sent_to_onboarding(client):
    login_with_otp(client, "+919876543221")

    response = client.get("/buyer", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/buyer/onboarding"

    response = client.get("/buyer/onboarding")
    assert response.status_code == 200
    assert "Indian Resident" in response.text
    assert "NRI / Overseas" in response.text

    response = client.get("/buyer/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/buyer/onboarding"


def test_profiled_buyer_can_open_buyer_dashboard(client):
    login_with_otp(client, "+919876543222")

    save_response = client.put(
        "/api/buyer/profile",
        json={
            "buyer_type": "indian_resident",
            "full_name": "Buyer Page Test",
            "preferred_city": "Kolkata",
        },
    )
    assert save_response.status_code == 200

    response = client.get("/buyer", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/buyer/dashboard"

    response = client.get("/buyer/dashboard")
    assert response.status_code == 200
    assert "Buyer OS" in response.text
    assert "buyerName" in response.text
    assert "Next phase" in response.text


def test_buyer_cannot_open_staff_dashboard(client):
    login_with_otp(client, "+919876543223")

    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 403

def test_login_page_preserves_destination_without_buyer_role_option(client):
    response = client.get("/index.html?next=/buyer#login")
    assert response.status_code == 200
    assert 'data-role="Buyer">Buyer</button>' not in response.text
    assert "buyer-entry-note" not in response.text
    assert "function getPostLoginRedirect" in response.text
    assert "const initialRole = 'Developer';" in response.text
    assert "params.toString()" in response.text
    assert "property_id" in response.text

    otp_response = client.get("/otp-login.html?next=/buyer")
    assert otp_response.status_code == 200
    assert "function getPostLoginRedirect" in otp_response.text
    assert "getPostLoginRedirect(data.redirect || '/dashboard')" in otp_response.text

def test_buyer_enquiry_is_saved_before_registration(client):
    response = client.post(
        "/api/buyer/enquiries",
        json={
            "full_name": "  Rounit Kumar  ",
            "email": " Rounit@example.com ",
            "phone": "+91 98765 43210",
            "city": " Kolkata ",
            "state": " West Bengal ",
        },
    )

    assert response.status_code == 201
    result = response.json()
    assert result["success"] is True
    assert result["redirect"] == "/inventory"
    assert isinstance(result["enquiry_id"], int)

    with SessionLocal() as db:
        enquiry = db.get(BuyerEnquiry, result["enquiry_id"])
        assert enquiry is not None
        assert enquiry.full_name == "Rounit Kumar"
        assert enquiry.email == "rounit@example.com"
        assert enquiry.phone == "+91 98765 43210"
        assert enquiry.city == "Kolkata"
        assert enquiry.state == "West Bengal"
        assert enquiry.status == "new"


def test_buyer_enquiry_rejects_invalid_email(client):
    response = client.post(
        "/api/buyer/enquiries",
        json={
            "full_name": "Test Buyer",
            "email": "not-an-email",
            "phone": "+91 98765 43210",
            "city": "Kolkata",
            "state": "West Bengal",
        },
    )
    assert response.status_code == 422

def test_inventory_page_shows_database_backed_empty_state(client):
    response = client.get("/inventory")
    assert response.status_code == 200
    assert "/api/buyer/inventory" in response.text
    assert "No published properties are currently available." in response.text

    api_response = client.get("/api/buyer/inventory")
    assert api_response.status_code == 200
    assert api_response.json() == []


def test_inventory_only_returns_published_available_listings(client):
    with SessionLocal() as db:
        published = PropertyListing(
            title="Published Test Apartment",
            property_type="apartment",
            city="Kolkata",
            state="West Bengal",
            price_inr=12500000,
            bedrooms=3,
            area_sqft=1420,
            description="Test fixture listing, not production inventory.",
            availability_status="available",
            is_published=True,
        )
        draft = PropertyListing(
            title="Unpublished Draft",
            property_type="villa",
            city="Kolkata",
            state="West Bengal",
            availability_status="available",
            is_published=False,
        )
        sold = PropertyListing(
            title="Already Sold Property",
            property_type="plot",
            city="Kolkata",
            state="West Bengal",
            availability_status="sold",
            is_published=True,
        )
        db.add_all([published, draft, sold])
        db.commit()
        published_id = published.id

    response = client.get("/api/buyer/inventory")
    assert response.status_code == 200
    result = response.json()

    assert len(result) == 1
    assert result[0]["id"] == published_id
    assert result[0]["title"] == "Published Test Apartment"
    assert result[0]["price_inr"] == 12500000
    assert result[0]["availability_status"] == "available"

def test_selected_property_is_preserved_through_buyer_login(client):
    response = client.get("/buyer?property_id=42", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == (
        "/index.html?next=/buyer&property_id=42#login"
    )

    invalid = client.get("/buyer?property_id=../bad", follow_redirects=False)
    assert invalid.status_code == 303
    assert invalid.headers["location"] == "/index.html?next=/buyer#login"


def test_selected_property_survives_new_buyer_onboarding_redirect(client):
    login_with_otp(client, "+919876540999")

    response = client.get("/buyer?property_id=42", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/buyer/onboarding?property_id=42"

    onboarding = client.get(response.headers["location"])
    assert onboarding.status_code == 200
    assert 'id="selectedPropertyContext"' in onboarding.text
    assert "loadSelectedPropertyContext" in onboarding.text
    assert "selectedPropertyId" in onboarding.text
