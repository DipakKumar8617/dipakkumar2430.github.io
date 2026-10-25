import os

# Isolated, non-production configuration for automated tests.
os.environ["APP_ENV"] = "development"
os.environ["SESSION_SECRET"] = "ci-only-test-secret-never-use-in-production"
os.environ["GOOGLE_CLIENT_ID"] = ""

import pytest
from fastapi.testclient import TestClient
from backend.app import main


@pytest.fixture
def client():
    main.otp_records.clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.otp_records.clear()
