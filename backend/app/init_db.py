"""Explicitly create missing RealIQ tables without dropping existing tables."""

from . import models  # Registers BuyerProfile with the shared metadata.
from .database import Base, engine


def main():
    Base.metadata.create_all(bind=engine)
    print("Database schema initialization completed.")
    print("Existing tables were not dropped.")


if __name__ == "__main__":
    main()
