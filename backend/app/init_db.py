"""Explicitly create missing RealIQ tables without dropping existing tables."""

from . import models  # Registers BuyerProfile with the shared metadata.
from .config import APP_ENV
from .database import Base, engine


def main():
    if APP_ENV != "development":
        raise RuntimeError(
            "init_db is restricted to development. "
            "Use reviewed Alembic migrations for deployed environments."
        )
    Base.metadata.create_all(bind=engine)
    print("Database schema initialization completed.")
    print("Existing tables were not dropped.")


if __name__ == "__main__":
    main()
