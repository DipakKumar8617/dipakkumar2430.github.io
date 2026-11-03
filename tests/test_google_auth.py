from backend.app import main


def configure_google_verifier(monkeypatch, claims):
    monkeypatch.setattr(main, "GOOGLE_CLIENT_ID", "test-google-client-id")

    def fake_verify(credential, request, audience):
        assert credential == "valid-test-google-credential"
        assert audience == "test-google-client-id"
        return claims

    monkeypatch.setattr(main.id_token, "verify_oauth2_token", fake_verify)


def google_claims():
    return {
        "email": "google.buyer@example.com",
        "sub": "google-test-subject-001",
        "name": "Google Buyer",
        "email_verified": True,
    }


def test_google_login_can_create_buyer_session(client, monkeypatch):
    configure_google_verifier(monkeypatch, google_claims())

    response = client.post(
        "/api/auth/google",
        json={
            "credential": "valid-test-google-credential",
            "purpose": "buyer",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["user"]["role"] == "Buyer"
    assert response.json()["redirect"] == "/buyer"

    profile = client.get("/api/buyer/profile")
    assert profile.status_code == 200
    assert profile.json()["profile"] is None

    buyer_page = client.get("/buyer", follow_redirects=False)
    assert buyer_page.status_code == 303
    assert buyer_page.headers["location"] == "/buyer/onboarding"


def test_google_login_without_buyer_purpose_remains_unassigned(
    client, monkeypatch
):
    configure_google_verifier(monkeypatch, google_claims())

    response = client.post(
        "/api/auth/google",
        json={"credential": "valid-test-google-credential"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "Unassigned"
    assert client.get("/api/buyer/profile").status_code == 403
    assert client.get("/dashboard", follow_redirects=False).status_code == 403


def test_google_login_rejects_unverified_email(client, monkeypatch):
    claims = google_claims()
    claims["email_verified"] = False
    configure_google_verifier(monkeypatch, claims)

    response = client.post(
        "/api/auth/google",
        json={
            "credential": "valid-test-google-credential",
            "purpose": "buyer",
        },
    )

    assert response.status_code == 401
    assert client.get("/api/session").json()["authenticated"] is False
