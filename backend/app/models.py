from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
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
        UniqueConstraint(
            "account_id",
            name="uq_buyer_profile_account",
        ),
        CheckConstraint(
            "buyer_type IN ('indian_resident', 'nri_overseas')",
            name="ck_buyer_profile_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", name="fk_buyer_profiles_account_id_user_accounts", ondelete="RESTRICT"),
        nullable=False,
    )

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

class BuyerEnquiry(Base):
    """Persist a visitor's initial property enquiry before account registration."""

    __tablename__ = "buyer_enquiries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="new"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class PropertyListing(Base):
    """A property record that is visible publicly only when explicitly published."""

    __tablename__ = "property_listings"

    __table_args__ = (
        CheckConstraint(
            "property_type IN ('apartment', 'villa', 'plot', 'commercial', 'other')",
            name="ck_property_listing_type",
        ),
        CheckConstraint(
            "availability_status IN ('available', 'reserved', 'sold')",
            name="ck_property_listing_availability",
        ),
        CheckConstraint(
            "price_inr IS NULL OR price_inr >= 0",
            name="ck_property_listing_price",
        ),
        CheckConstraint(
            "area_sqft IS NULL OR area_sqft > 0",
            name="ck_property_listing_area",
        ),
        CheckConstraint(
            "bedrooms IS NULL OR bedrooms >= 0",
            name="ck_property_listing_bedrooms",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    property_type: Mapped[str] = mapped_column(String(30), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    price_inr: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    area_sqft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    availability_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="available"
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

class UserAccount(Base):
    """Account for manually registered RealIQ users."""

    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    email: Mapped[str | None] = mapped_column(
        String(254), nullable=True, unique=True, index=True
    )
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default="buyer")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class AuthIdentity(Base):
    """A verified sign-in identity linked to one canonical account."""

    __tablename__ = "auth_identities"

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_subject",
            name="uq_auth_identity_provider_subject",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", name="fk_auth_identities_account_id_user_accounts", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

class PropertyShortlist(Base):
    """A buyer's saved property, owned by their canonical account."""

    __tablename__ = "property_shortlists"

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "property_listing_id",
            name="uq_property_shortlist_account_listing",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_property_shortlists_account_id_user_accounts",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    property_listing_id: Mapped[int] = mapped_column(
        ForeignKey(
            "property_listings.id",
            name="fk_property_shortlists_listing_id_property_listings",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

class SavedPropertySearch(Base):
    """A named set of property discovery filters owned by a buyer."""

    __tablename__ = "saved_property_searches"

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "name",
            name="uq_saved_property_search_account_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_saved_property_searches_account_id_user_accounts",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    criteria: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
    )

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


class RecentlyViewedProperty(Base):
    """A buyer's most recent successful view of a published property."""

    __tablename__ = "recently_viewed_properties"

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "property_listing_id",
            name="uq_recently_viewed_account_listing",
        ),
        Index(
            "ix_recently_viewed_account_viewed_at",
            "account_id",
            "viewed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_recently_viewed_account_id_user_accounts",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    property_listing_id: Mapped[int] = mapped_column(
        ForeignKey(
            "property_listings.id",
            name="fk_recently_viewed_listing_id_property_listings",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    viewed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class BuyerSiteVisit(Base):
    """A site-visit request owned by an authenticated buyer."""

    __tablename__ = "buyer_site_visits"

    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'confirmed', 'completed', 'cancelled', 'rejected')",
            name="ck_buyer_site_visits_status",
        ),
        Index(
            "ix_buyer_site_visits_account_scheduled_at",
            "account_id",
            "scheduled_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_buyer_site_visits_account_id_user_accounts",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    property_listing_id: Mapped[int] = mapped_column(
        ForeignKey(
            "property_listings.id",
            name="fk_buyer_site_visits_listing_id_property_listings",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    message: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="requested",
        server_default="requested",
    )

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



class StaffInvitation(Base):
    """One-time invitation for a Developer or Channel Partner account."""

    __tablename__ = "staff_invitations"

    __table_args__ = (
        CheckConstraint(
            "role IN ('developer', 'channel partner')",
            name="ck_staff_invitations_role",
        ),
        CheckConstraint(
            "accepted_at IS NULL OR revoked_at IS NULL",
            name="ck_staff_invitations_terminal_state",
        ),
        UniqueConstraint(
            "token_hash",
            name="uq_staff_invitations_token_hash",
        ),
        Index(
            "ix_staff_invitations_email_created_at",
            "email",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    invited_by_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_staff_invitations_invited_by_user_accounts",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )



class StaffAuditLog(Base):
    """Append-only record of administrative staff-management actions."""

    __tablename__ = "staff_audit_logs"

    __table_args__ = (
        Index("ix_staff_audit_logs_created_at", "created_at"),
        Index(
            "ix_staff_audit_logs_target_created_at",
            "target_account_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    actor_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_staff_audit_logs_actor_user_accounts",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    target_account_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_staff_audit_logs_target_user_accounts",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    invitation_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "staff_invitations.id",
            name="fk_staff_audit_logs_invitation_staff_invitations",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
