from backend.app import main


def test_public_pages_and_api_routes(client):
    paths = [
        "/",
        "/index.html",
        "/otp-login.html",
        "/register.html",
        "/forgot-password.html",
        "/buyer-enquiry.html",
        "/styles.css",
        "/api/auth/config",
        "/api/session",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, (
            f"{path} returned {response.status_code}"
        )


def test_favicon_redirect_and_svg(client):
    response = client.get("/favicon.ico", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/favicon.svg"

    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text


def test_dashboard_requires_authentication(client):
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/index.html#login"


def test_otp_login_dashboard_and_logout(client):
    phone = "+919876543211"

    response = client.post(
        "/api/auth/otp/request",
        json={"phone": phone},
    )
    assert response.status_code == 200
    code = response.json().get("demo_code")
    assert code and len(code) == 6 and code.isdigit()

    response = client.post(
        "/api/auth/otp/verify",
        json={"phone": phone, "code": code},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    session = client.get("/api/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True

    dashboard = client.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code == 200

    logout = client.post("/api/logout")
    assert logout.status_code == 200
    assert logout.json()["success"] is True

    session = client.get("/api/session")
    assert session.json()["authenticated"] is False

    dashboard = client.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code == 303
    assert dashboard.headers["location"] == "/index.html#login"


def test_otp_is_disabled_outside_development(client, monkeypatch):
    monkeypatch.setattr(main, "APP_ENV", "production")

    response = client.post(
        "/api/auth/otp/request",
        json={"phone": "+919876543211"},
    )
    assert response.status_code == 403
