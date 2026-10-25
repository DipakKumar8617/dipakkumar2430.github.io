from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class BuyerProfile(Base):
    __tablename__ = "buyer_profiles"

    __table_args__ = (
        UniqueConstraint(
            "auth_provider",
            "auth_subject",
            name="uq_buyer_profile_auth_identity",
        ),
        CheckConstraint(
            "buyer_type IN ('indian_resident', 'nri_overseas')",
            name="ck_buyer_profile_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Identity is derived from the authenticated session, not request input.
    auth_provider: Mapped[str] = mapped_column(String(30), nullable=False)
    auth_subject: Mapped[str] = mapped_column(String(255), nullable=False)

    buyer_type: Mapped[str] = mapped_column(String(30), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    country_of_residence: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    timezone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    preferred_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    purchase_purpose: Mapped[str | None] = mapped_column(String(30), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
