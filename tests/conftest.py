import os

# Isolated, non-production configuration for automated tests.
os.environ["APP_ENV"] = "development"
os.environ["SESSION_SECRET"] = "ci-only-test-secret-never-use-in-production"
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

import pytest
from fastapi.testclient import TestClient

from backend.app import main, models
from backend.app.database import Base, engine

if engine.dialect.name != "sqlite":
    raise RuntimeError("Tests must use the isolated SQLite database.")



@pytest.fixture
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    main.otp_records.clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.otp_records.clear()
    Base.metadata.drop_all(bind=engine)
