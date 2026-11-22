import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
APP_ENV = os.getenv("APP_ENV", "production").strip().lower()

try:
    INVENTORY_HOLD_DURATION_MINUTES = int(
        os.getenv("INVENTORY_HOLD_DURATION_MINUTES", "120").strip()
    )
except ValueError as exc:
    raise RuntimeError(
        "INVENTORY_HOLD_DURATION_MINUTES must be a positive integer."
    ) from exc

if INVENTORY_HOLD_DURATION_MINUTES <= 0:
    raise RuntimeError(
        "INVENTORY_HOLD_DURATION_MINUTES must be greater than zero."
    )
