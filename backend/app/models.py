from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
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
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
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
        CheckConstraint(
            "inventory_mode IN ('single', 'multi_unit')",
            name="ck_property_listing_inventory_mode",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    developer_organization_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "developer_organizations.id",
            name="fk_property_listings_developer_organization",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
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
    inventory_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="single",
        server_default="single",
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

class PropertyInventoryItem(Base):
    """One independently purchasable property or unit within a listing."""

    __tablename__ = "property_inventory_items"

    __table_args__ = (
        CheckConstraint(
            "item_type IN ('single', 'unit')",
            name="ck_property_inventory_item_type",
        ),
        CheckConstraint(
            "(item_type = 'single' AND unit_code IS NULL) OR "
            "(item_type = 'unit' AND unit_code IS NOT NULL)",
            name="ck_property_inventory_item_unit_code",
        ),
        CheckConstraint(
            "availability_status IN ('available', 'reserved', 'sold')",
            name="ck_property_inventory_item_availability",
        ),
        CheckConstraint(
            "price_inr IS NULL OR price_inr >= 0",
            name="ck_property_inventory_item_price",
        ),
        CheckConstraint(
            "bedrooms IS NULL OR bedrooms >= 0",
            name="ck_property_inventory_item_bedrooms",
        ),
        CheckConstraint(
            "area_sqft IS NULL OR area_sqft > 0",
            name="ck_property_inventory_item_area",
        ),
        Index(
            "uq_property_inventory_id_listing",
            "id",
            "property_listing_id",
            unique=True,
        ),
        UniqueConstraint(
            "property_listing_id",
            "unit_code",
            name="uq_property_inventory_listing_unit_code",
        ),
        Index(
            "uq_property_inventory_single_listing",
            "single_property_listing_id",
            unique=True,
        ),
        Index(
            "ix_property_inventory_listing",
            "property_listing_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    property_listing_id: Mapped[int] = mapped_column(
        ForeignKey(
            "property_listings.id",
            name="fk_property_inventory_item_listing",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    item_type: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    price_inr: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    area_sqft: Mapped[int | None] = mapped_column(Integer, nullable=True)

    availability_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="available",
        server_default="available",
    )

    single_property_listing_id: Mapped[int | None] = mapped_column(
        Integer,
        Computed(
            "CASE WHEN item_type = 'single' "
            "THEN property_listing_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class InventoryHold(Base):
    """Temporary exclusive hold on one independently purchasable item."""

    __tablename__ = "inventory_holds"

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'converted', 'released', 'expired')",
            name="ck_inventory_hold_status",
        ),
        Index(
            "uq_inventory_hold_active_item",
            "active_inventory_item_id",
            unique=True,
        ),
        Index(
            "ix_inventory_hold_item_expiry",
            "inventory_item_id",
            "status",
            "expires_at",
        ),
        Index(
            "ix_inventory_hold_buyer_created",
            "buyer_account_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey(
            "property_inventory_items.id",
            name="fk_inventory_hold_item",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    buyer_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_inventory_hold_buyer",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default="active",
    )

    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    active_inventory_item_id: Mapped[int | None] = mapped_column(
        Integer,
        Computed(
            "CASE WHEN status = 'active' "
            "THEN inventory_item_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
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

class DeveloperOrganization(Base):
    """Legal organization registered by a Developer account."""

    __tablename__ = "developer_organizations"

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('private_limited', 'public_limited', "
            "'llp', 'partnership', 'proprietorship', 'other')",
            name="ck_developer_organizations_entity_type",
        ),
        CheckConstraint(
            "verification_status IN "
            "('pending_verification', 'verified', 'rejected', 'suspended')",
            name="ck_developer_organizations_verification_status",
        ),
        UniqueConstraint(
            "country_code",
            "entity_type",
            "registration_number",
            name="uq_developer_organizations_registration",
        ),
        Index(
            "ix_developer_organizations_status_created_at",
            "verification_status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    registration_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)

    registered_address: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(20), nullable=False)

    contact_email: Mapped[str] = mapped_column(String(254), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)

    verification_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending_verification",
        server_default="pending_verification",
    )

    submitted_by_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_developer_organizations_submitter",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    reviewed_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_developer_organizations_reviewer",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DeveloperOrganizationMembership(Base):
    """Explicit account membership and permissions within an organization."""

    __tablename__ = "developer_organization_memberships"

    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'inventory_manager', 'viewer')",
            name="ck_developer_org_memberships_role",
        ),
        CheckConstraint(
            "status IN ('active', 'suspended', 'removed')",
            name="ck_developer_org_memberships_status",
        ),
        UniqueConstraint(
            "organization_id",
            "account_id",
            name="uq_developer_org_memberships_org_account",
        ),
        Index(
            "ix_developer_org_memberships_account_status",
            "account_id",
            "status",
        ),
        Index(
            "ix_developer_org_memberships_org_status",
            "organization_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        ForeignKey(
            "developer_organizations.id",
            name="fk_developer_org_memberships_organization",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_developer_org_memberships_account",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        server_default="active",
    )

    created_by_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_developer_org_memberships_creator",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class DeveloperOrganizationAuditLog(Base):
    """Audit history for organization registration and administration."""

    __tablename__ = "developer_organization_audit_logs"

    __table_args__ = (
        Index(
            "ix_developer_org_audit_org_created_at",
            "organization_id",
            "created_at",
        ),
        Index(
            "ix_developer_org_audit_actor_created_at",
            "actor_account_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        ForeignKey(
            "developer_organizations.id",
            name="fk_developer_org_audit_organization",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    actor_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_developer_org_audit_actor",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    target_account_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_developer_org_audit_target",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class PropertyChannelPartnerAssignment(Base):
    """Maintain the complete assignment history for a property."""

    __tablename__ = "property_channel_partner_assignments"

    __table_args__ = (
        Index(
            "uq_property_cp_assignment_id_partner",
            "id",
            "channel_partner_account_id",
            unique=True,
        ),
        Index(
            "uq_property_cp_assignment_active_property",
            "active_property_listing_id",
            unique=True,
        ),
        Index(
            "ix_property_cp_assignment_property",
            "property_listing_id",
        ),
        Index(
            "ix_property_cp_assignment_partner",
            "channel_partner_account_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    property_listing_id: Mapped[int] = mapped_column(
        ForeignKey(
            "property_listings.id",
            name="fk_property_cp_assignment_property",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    channel_partner_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_property_cp_assignment_partner",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    assigned_by_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_property_cp_assignment_admin",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    ended_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_property_cp_assignment_ended_by",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    active_property_listing_id: Mapped[int | None] = mapped_column(
        Integer,
        Computed(
            "CASE WHEN ended_at IS NULL "
            "THEN property_listing_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )

class RealEstateTransaction(Base):
    """A buyer's direct-booking or negotiated property transaction."""

    __tablename__ = "real_estate_transactions"

    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('direct_booking', 'negotiation')",
            name="ck_real_estate_transaction_type",
        ),
        CheckConstraint(
            "status IN ('initiated', 'under_review', 'negotiating', "
            "'terms_agreed', 'booking_pending', 'booked', 'completed', "
            "'cancelled', 'rejected', 'expired')",
            name="ck_real_estate_transaction_status",
        ),
        CheckConstraint(
            "(channel_partner_assignment_id IS NULL "
            "AND channel_partner_account_id IS NULL) OR "
            "(channel_partner_assignment_id IS NOT NULL "
            "AND channel_partner_account_id IS NOT NULL)",
            name="ck_real_estate_transaction_cp_snapshot",
        ),
        ForeignKeyConstraint(
            ["inventory_item_id", "property_listing_id"],
            ["property_inventory_items.id", "property_inventory_items.property_listing_id"],
            name="fk_real_estate_transaction_inventory_listing",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["channel_partner_assignment_id", "channel_partner_account_id"],
            ["property_channel_partner_assignments.id", "property_channel_partner_assignments.channel_partner_account_id"],
            name="fk_real_estate_transaction_cp_snapshot_assignment",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_real_estate_transaction_buyer_created",
            "buyer_account_id",
            "created_at",
        ),
        Index(
            "ix_real_estate_transaction_inventory_status",
            "inventory_item_id",
            "status",
        ),
        Index(
            "ix_real_estate_transaction_developer_status",
            "developer_organization_id",
            "status",
        ),
        Index(
            "ix_real_estate_transaction_cp_status",
            "channel_partner_account_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    buyer_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_real_estate_transaction_buyer",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    property_listing_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    inventory_item_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    developer_organization_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "developer_organizations.id",
            name="fk_real_estate_transaction_developer",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    # Historical attribution snapshot: never replace these values when
    # the property's active channel-partner assignment changes.
    channel_partner_assignment_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    channel_partner_account_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_real_estate_transaction_cp_account",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    transaction_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="initiated",
        server_default="initiated",
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


class TransactionEvent(Base):
    """Append-only audit history for a real-estate transaction."""

    __tablename__ = "transaction_events"

    __table_args__ = (
        CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('initiated', 'under_review', 'negotiating', 'terms_agreed', "
            "'booking_pending', 'booked', 'completed', 'cancelled', "
            "'rejected', 'expired')",
            name="ck_transaction_event_from_status",
        ),
        CheckConstraint(
            "to_status IS NULL OR to_status IN "
            "('initiated', 'under_review', 'negotiating', 'terms_agreed', "
            "'booking_pending', 'booked', 'completed', 'cancelled', "
            "'rejected', 'expired')",
            name="ck_transaction_event_to_status",
        ),
        Index(
            "ix_transaction_event_transaction_created",
            "transaction_id",
            "created_at",
        ),
        Index(
            "ix_transaction_event_actor_created",
            "actor_account_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey(
            "real_estate_transactions.id",
            name="fk_transaction_event_transaction",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    actor_account_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_transaction_event_actor",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    from_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    to_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    details: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

class TransactionOffer(Base):
    """An immutable negotiation proposal or counteroffer in a transaction."""

    __tablename__ = "transaction_offers"

    __table_args__ = (
        CheckConstraint(
            "amount > 0",
            name="ck_transaction_offer_amount_positive",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', "
            "'countered', 'withdrawn', 'expired')",
            name="ck_transaction_offer_status",
        ),
        Index(
            "uq_transaction_offer_active_pending",
            "active_pending_transaction_id",
            unique=True,
        ),
        Index(
            "ix_transaction_offer_transaction_created",
            "transaction_id",
            "created_at",
        ),
        Index(
            "ix_transaction_offer_proposer_created",
            "proposer_account_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey(
            "real_estate_transactions.id",
            name="fk_transaction_offer_transaction",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    proposer_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_transaction_offer_proposer",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    parent_offer_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "transaction_offers.id",
            name="fk_transaction_offer_parent",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="INR",
        server_default="INR",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    active_pending_transaction_id: Mapped[int | None] = mapped_column(
        Integer,
        Computed(
            "CASE WHEN status = 'pending' THEN transaction_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
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

class Booking(Base):
    """A booking created through the shared transaction engine."""

    __tablename__ = "bookings"

    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'awaiting_payment', 'confirmed', "
            "'cancelled', 'expired')",
            name="ck_booking_status",
        ),
        CheckConstraint(
            "agreed_amount IS NULL OR agreed_amount > 0",
            name="ck_booking_agreed_amount_positive",
        ),
        Index(
            "uq_booking_transaction",
            "transaction_id",
            unique=True,
        ),
        Index(
            "uq_booking_active_inventory_item",
            "active_inventory_item_id",
            unique=True,
        ),
        Index(
            "ix_booking_buyer_created",
            "buyer_account_id",
            "created_at",
        ),
        Index(
            "ix_booking_status_expiry",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey(
            "real_estate_transactions.id",
            name="fk_booking_transaction",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey(
            "property_inventory_items.id",
            name="fk_booking_inventory_item",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    inventory_hold_id: Mapped[int] = mapped_column(
        ForeignKey(
            "inventory_holds.id",
            name="fk_booking_inventory_hold",
            ondelete="RESTRICT",
        ),
        nullable=False,
        unique=True,
    )

    buyer_account_id: Mapped[int] = mapped_column(
        ForeignKey(
            "user_accounts.id",
            name="fk_booking_buyer",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    agreed_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2),
        nullable=True,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="INR",
        server_default="INR",
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="requested",
        server_default="requested",
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    active_inventory_item_id: Mapped[int | None] = mapped_column(
        Integer,
        Computed(
            "CASE WHEN status IN ('requested', 'awaiting_payment', "
            "'confirmed') THEN inventory_item_id ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
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

class PaymentAttempt(Base):
    """A provider-backed attempt to collect a booking payment."""

    __tablename__ = "payment_attempts"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_attempt_amount_positive"),
        CheckConstraint(
            "status IN ('created', 'pending', 'authorized', 'captured', "
            "'failed', 'cancelled', 'partially_refunded', 'refunded')",
            name="ck_payment_attempt_status",
        ),
        UniqueConstraint(
            "provider", "idempotency_key",
            name="uq_payment_attempt_provider_idempotency",
        ),
        UniqueConstraint(
            "provider", "provider_order_id",
            name="uq_payment_attempt_provider_order",
        ),
        UniqueConstraint(
            "provider", "provider_payment_id",
            name="uq_payment_attempt_provider_payment",
        ),
        Index("ix_payment_attempt_booking_created", "booking_id", "created_at"),
        Index("ix_payment_attempt_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", name="fk_payment_attempt_booking", ondelete="RESTRICT"),
        nullable=False,
    )
    buyer_account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", name="fk_payment_attempt_buyer", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="created", server_default="created"
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PaymentLedgerEntry(Base):
    """Append-only record of a provider-confirmed financial movement."""

    __tablename__ = "payment_ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "entry_type IN ('capture', 'refund', 'reversal', 'chargeback')",
            name="ck_payment_ledger_entry_type",
        ),
        CheckConstraint("amount > 0", name="ck_payment_ledger_amount_positive"),
        UniqueConstraint(
            "provider", "provider_transaction_id",
            name="uq_payment_ledger_provider_transaction",
        ),
        Index("ix_payment_ledger_payment_created", "payment_attempt_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_attempt_id: Mapped[int] = mapped_column(
        ForeignKey(
            "payment_attempts.id",
            name="fk_payment_ledger_payment_attempt",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class PaymentWebhookEvent(Base):
    """Deduplicated provider webhook receipt and processing state."""

    __tablename__ = "payment_webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_payment_webhook_provider_event"),
        CheckConstraint(
            "processing_status IN ('received', 'processing', 'processed', 'ignored', 'failed')",
            name="ck_payment_webhook_processing_status",
        ),
        Index("ix_payment_webhook_status_received", "processing_status", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="received", server_default="received"
    )
    event_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processing_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)


class PaymentReconciliationBatch(Base):
    """A reconciliation run against one provider and reporting period."""

    __tablename__ = "payment_reconciliation_batches"
    __table_args__ = (
        UniqueConstraint(
            "provider", "idempotency_key",
            name="uq_payment_reconciliation_provider_idempotency",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_payment_reconciliation_batch_status",
        ),
        Index("ix_payment_reconciliation_provider_created", "provider", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    statement_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default="running"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PaymentReconciliationItem(Base):
    """One provider statement line and its internal matching outcome."""

    __tablename__ = "payment_reconciliation_items"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('matched', 'missing_internal', 'missing_provider', "
            "'amount_mismatch', 'currency_mismatch')",
            name="ck_payment_reconciliation_item_outcome",
        ),
        Index("ix_payment_reconciliation_item_batch", "batch_id"),
        Index("ix_payment_reconciliation_item_payment", "payment_attempt_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey(
            "payment_reconciliation_batches.id",
            name="fk_payment_reconciliation_item_batch",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    payment_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "payment_attempts.id",
            name="fk_payment_reconciliation_item_payment",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    provider_transaction_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from datetime import datetime


class PaymentRefund(Base):
    __tablename__ = "payment_refunds"

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "idempotency_key",
            name="uq_payment_refund_provider_idempotency",
        ),
        UniqueConstraint(
            "provider",
            "provider_refund_id",
            name="uq_payment_refund_provider_refund_id",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_payment_refund_positive_amount",
        ),
        CheckConstraint(
            "status IN ('requested', 'submitted', 'pending', 'unknown', 'processed', 'failed')",
            name="ck_payment_refund_status",
        ),
        Index("ix_payment_refunds_payment_created", "payment_attempt_id", "created_at"),
        Index("ix_payment_refunds_status_created", "status", "created_at"),
    )

    id = Column(Integer, primary_key=True)
    payment_attempt_id = Column(
        Integer,
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider = Column(String(40), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    provider_refund_id = Column(String(128), nullable=True)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="INR")
    status = Column(String(20), nullable=False, default="requested")
    failure_code = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

class CommissionRule(Base):
    """Effective-dated commission rate. Rates are stored in basis points."""

    __tablename__ = "commission_rules"

    __table_args__ = (
        CheckConstraint(
            "rate_bps >= 0 AND rate_bps <= 10000",
            name="ck_commission_rules_rate_bps",
        ),
        CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="ck_commission_rules_effective_window",
        ),
        Index(
            "ix_commission_rules_effective",
            "is_active",
            "starts_at",
            "ends_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rate_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    created_by_account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class CommissionLedgerEntry(Base):
    """Append-only commission accrual/reversal with immutable rate snapshots."""

    __tablename__ = "commission_ledger_entries"

    __table_args__ = (
        CheckConstraint(
            "entry_type IN ('accrual', 'reversal')",
            name="ck_commission_ledger_entry_type",
        ),
        CheckConstraint(
            "amount > 0 AND basis_amount > 0",
            name="ck_commission_ledger_positive_amounts",
        ),
        CheckConstraint(
            "rate_bps >= 0 AND rate_bps <= 10000",
            name="ck_commission_ledger_rate_bps",
        ),
        CheckConstraint(
            "approval_status IN ('pending', 'approved', 'rejected')",
            name="ck_commission_ledger_approval_status",
        ),
        UniqueConstraint(
            "source_payment_ledger_entry_id",
            name="uq_commission_ledger_source_payment_entry",
        ),
        Index(
            "ix_commission_ledger_cp_created",
            "channel_partner_account_id",
            "created_at",
        ),
        Index(
            "ix_commission_ledger_approval_created",
            "approval_status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_partner_account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("real_estate_transactions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payment_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_payment_ledger_entry_id: Mapped[int] = mapped_column(
        ForeignKey("payment_ledger_entries.id", ondelete="RESTRICT"),
        nullable=False,
    )
    original_accrual_id: Mapped[int | None] = mapped_column(
        ForeignKey("commission_ledger_entries.id", ondelete="RESTRICT"),
        nullable=True,
    )
    commission_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("commission_rules.id", ondelete="RESTRICT"),
        nullable=True,
    )
    entry_type: Mapped[str] = mapped_column(String(20), nullable=False)
    basis_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    rate_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    approval_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    reviewed_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
