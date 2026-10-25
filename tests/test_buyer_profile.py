def login_with_otp(client, phone):
    response = client.post(
        "/api/auth/otp/request",
        json={"phone": phone},
    )
    assert response.status_code == 200
    code = response.json()["demo_code"]

    response = client.post(
        "/api/auth/otp/verify",
        json={"phone": phone, "code": code},
    )
    assert response.status_code == 200


def test_buyer_profile_requires_login(client):
    assert client.get("/api/buyer/profile").status_code == 401

    response = client.put(
        "/api/buyer/profile",
        json={"buyer_type": "indian_resident"},
    )
    assert response.status_code == 401


def test_buyer_can_create_and_update_own_profile(client):
    login_with_otp(client, "+919876543211")

    response = client.get("/api/buyer/profile")
    assert response.status_code == 200
    assert response.json()["profile"] is None

    response = client.put(
        "/api/buyer/profile",
        json={
            "buyer_type": "indian_resident",
            "full_name": "Test Buyer",
            "preferred_city": "Kolkata",
            "purchase_purpose": "self_use",
        },
    )
    assert response.status_code == 200
    assert response.json()["buyer_type"] == "indian_resident"
    assert response.json()["preferred_city"] == "Kolkata"
    profile_id = response.json()["id"]

    response = client.put(
        "/api/buyer/profile",
        json={
            "buyer_type": "indian_resident",
            "preferred_city": "Mumbai",
        },
    )
    assert response.status_code == 200
    assert response.json()["id"] == profile_id
    assert response.json()["preferred_city"] == "Mumbai"
    assert response.json()["full_name"] == "Test Buyer"


def test_nri_profile_can_be_saved(client):
    login_with_otp(client, "+919876543212")

    response = client.put(
        "/api/buyer/profile",
        json={
            "buyer_type": "nri_overseas",
            "country_of_residence": "United Arab Emirates",
            "timezone": "Asia/Dubai",
            "purchase_purpose": "investment",
        },
    )
    assert response.status_code == 200
    assert response.json()["buyer_type"] == "nri_overseas"
    assert response.json()["country_of_residence"] == "United Arab Emirates"


def test_invalid_buyer_type_is_rejected(client):
    login_with_otp(client, "+919876543213")

    response = client.put(
        "/api/buyer/profile",
        json={"buyer_type": "developer"},
    )
    assert response.status_code == 422


def test_buyer_profiles_are_isolated_by_account(client):
    login_with_otp(client, "+919876543214")

    response = client.put(
        "/api/buyer/profile",
        json={"buyer_type": "indian_resident"},
    )
    assert response.status_code == 200

    client.post("/api/logout")
    login_with_otp(client, "+919876543215")

    response = client.get("/api/buyer/profile")
    assert response.status_code == 200
    assert response.json()["profile"] is None
