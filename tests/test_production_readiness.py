import pytest
from sqlalchemy.exc import OperationalError

from backend.app import config, init_db, main
from backend.app.database import get_db


def test_liveness_does_not_require_database(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_succeeds_with_database(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_returns_503_when_database_query_fails(client):
    class UnavailableDatabase:
        def execute(self, statement):
            raise OperationalError(
                "SELECT 1", {}, Exception("database unavailable")
            )

    def unavailable_database():
        return UnavailableDatabase()

    main.app.dependency_overrides[get_db] = unavailable_database
    try:
        response = client.get("/health/ready")
    finally:
        main.app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert "database unavailable" not in response.text


def test_production_requires_strong_session_secret(monkeypatch):
    monkeypatch.setattr(config, "RAZORPAY_KEY_ID", "")
    monkeypatch.setattr(config, "RAZORPAY_KEY_SECRET", "")
    monkeypatch.setattr(config, "RAZORPAY_WEBHOOK_SECRET", "")
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        config.validate_runtime_configuration("too-short", "production")


def test_development_allows_short_nonempty_session_secret(monkeypatch):
    monkeypatch.setattr(config, "RAZORPAY_KEY_ID", "")
    monkeypatch.setattr(config, "RAZORPAY_KEY_SECRET", "")
    monkeypatch.setattr(config, "RAZORPAY_WEBHOOK_SECRET", "")
    config.validate_runtime_configuration("local-secret", "development")


def test_session_secret_must_never_be_empty():
    with pytest.raises(RuntimeError, match="SESSION_SECRET is missing"):
        config.validate_runtime_configuration("", "development")


@pytest.mark.parametrize(
    "key_id,key_secret,webhook_secret",
    [
        ("rzp_test_key", "", ""),
        ("", "secret", ""),
        ("", "", "webhook"),
        ("rzp_test_key", "secret", ""),
        ("rzp_test_key", "", "webhook"),
        ("", "secret", "webhook"),
    ],
)
def test_partial_razorpay_configuration_is_rejected(
    monkeypatch, key_id, key_secret, webhook_secret
):
    monkeypatch.setattr(config, "RAZORPAY_KEY_ID", key_id)
    monkeypatch.setattr(config, "RAZORPAY_KEY_SECRET", key_secret)
    monkeypatch.setattr(config, "RAZORPAY_WEBHOOK_SECRET", webhook_secret)
    with pytest.raises(RuntimeError, match="Configure RAZORPAY"):
        config.validate_runtime_configuration("x" * 48, "production")


def test_unconfigured_razorpay_is_allowed(monkeypatch):
    monkeypatch.setattr(config, "RAZORPAY_KEY_ID", "")
    monkeypatch.setattr(config, "RAZORPAY_KEY_SECRET", "")
    monkeypatch.setattr(config, "RAZORPAY_WEBHOOK_SECRET", "")
    config.validate_runtime_configuration("x" * 48, "production")


def test_configured_razorpay_requires_https(monkeypatch):
    monkeypatch.setattr(config, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(config, "RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setattr(config, "RAZORPAY_WEBHOOK_SECRET", "webhook")
    monkeypatch.setattr(config, "RAZORPAY_BASE_URL", "http://gateway.example")
    with pytest.raises(RuntimeError, match="must use HTTPS"):
        config.validate_runtime_configuration("x" * 48, "production")


def test_init_db_refuses_non_development_environment(monkeypatch):
    monkeypatch.setattr(init_db, "APP_ENV", "production")
    with pytest.raises(RuntimeError, match="restricted to development"):
        init_db.main()
