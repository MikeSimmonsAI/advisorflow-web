"""Private Buyer Deal Room - investor accounts, invitations, listings, exclusive review.

ONE PLATFORM, TWO EXPERIENCES. Nothing here duplicates a system that exists:

    investor ACCOUNTS     a platform `users` row with role "investor" (the same
                          authentication, sessions and password rules) PLUS this
                          table, which ties that login to ONE buyer record in ONE
                          workspace. users.organization_id stays NULL, so an
                          investor never holds a Wholesale workspace membership.
    BUYING ENTITIES       the existing `wholesale_buyer_entities` (#413); this
                          module only records which representative may act for
                          which entity.
    LISTINGS              a deal's Deal Room state. The CONTENT an investor sees
                          is still built by `wholesale_publication` (whitelist) -
                          a listing only decides WHO sees it and WHEN.
    EXCLUSIVE REVIEW      a temporary visibility hold. It is not an option, a
                          contract or a purchase right, and every screen says so.
    MESSAGES              the existing `wholesale_buyer_conversations` (extended).
    FILES / SIGNATURES    the existing `wholesale_files` and EvoSys e-sign.

Tables are created by Base.metadata.create_all (registered in app/models/registry.py).
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text

from app.models.models import Base, gen_uuid

INVITE_PENDING, INVITE_ACCEPTED, INVITE_REVOKED, INVITE_EXPIRED = "pending", "accepted", "revoked", "expired"


class InvestorInvite(Base):
    __tablename__ = "investor_invites"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="SET NULL"), nullable=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    company = Column(String, nullable=True)
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=True)     # the staff member who owns it
    invited_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    token_hash = Column(String, nullable=True)                               # sha256; the token is never stored
    status = Column(String, nullable=False, default=INVITE_PENDING)
    expires_at = Column(DateTime, nullable=False)
    send_count = Column(Integer, nullable=False, default=0)
    last_sent_at = Column(DateTime, nullable=True)
    last_delivery = Column(String, nullable=True)                            # delivered | not_sent: <why>
    accepted_at = Column(DateTime, nullable=True)
    accepted_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_invinvite_org_status", "organization_id", "status"),
        Index("ix_invinvite_token", "token_hash", unique=True),
    )


class InvestorAccount(Base):
    __tablename__ = "investor_accounts"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="SET NULL"), nullable=True)
    invite_id = Column(String, ForeignKey("investor_invites.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default="active")               # active | disabled
    kind = Column(String, nullable=True)                                     # individual | business
    legal_name = Column(String, nullable=True)
    preferred_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    company_legal_name = Column(String, nullable=True)
    dba = Column(String, nullable=True)
    entity_type = Column(String, nullable=True)                              # llc, corporation, lp, trust, sole_prop
    business_address = Column(String, nullable=True)
    position = Column(String, nullable=True)
    channels = Column(Text, nullable=True)                                   # JSON: ["portal","email","phone","sms"]
    email_verified_at = Column(DateTime, nullable=True)                      # proven by accepting the emailed invite
    identity_completed_at = Column(DateTime, nullable=True)
    welcome_completed_at = Column(DateTime, nullable=True)
    assigned_user_id = Column(String, ForeignKey("users.id"), nullable=True)  # staff who handles them
    last_seen_at = Column(DateTime, nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_invacct_org_user"),
        Index("ix_invacct_org_buyer", "organization_id", "buyer_id"),
    )


class InvestorEntityRole(Base):
    """Which representative may act for which buying entity (many-to-many)."""

    __tablename__ = "investor_entity_roles"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    account_id = Column(String, ForeignKey("investor_accounts.id", ondelete="CASCADE"), nullable=False)
    entity_id = Column(String, ForeignKey("wholesale_buyer_entities.id", ondelete="CASCADE"), nullable=False)
    position = Column(String, nullable=True)                                 # Managing member, CFO ...
    authority = Column(String, nullable=False, default="representative")      # signer | representative | viewer
    stated_by = Column(String, nullable=False, default="investor")           # investor | staff (staff = checked)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("account_id", "entity_id", name="uq_inventrole"),)


LISTING_STATES = ("draft", "approved", "published", "exclusive_review", "expired", "withdrawn", "closed")
VISIBLE_STATES = ("published", "exclusive_review")


class DealListing(Base):
    __tablename__ = "deal_room_listings"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"), nullable=False)
    state = Column(String, nullable=False, default="draft")
    audience = Column(String, nullable=False, default="matched")            # matched | selected | all
    buyer_ids = Column(Text, nullable=True)                                  # JSON, for audience "selected"
    min_match_score = Column(Integer, nullable=False, default=60)
    publish_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    approved_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    published_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    published_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    end_reason = Column(String, nullable=True)
    notify_on_publish = Column(Boolean, nullable=False, default=True)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("deal_id", name="uq_drlisting_deal"),
        Index("ix_drlisting_org_state", "organization_id", "state"),
    )


HOLD_REQUESTED, HOLD_ACTIVE, HOLD_DECLINED, HOLD_ENDED = "requested", "active", "declined", "ended"


class ExclusiveHold(Base):
    """A TEMPORARY visibility hold - never a purchase right."""

    __tablename__ = "deal_room_holds"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    listing_id = Column(String, ForeignKey("deal_room_listings.id", ondelete="CASCADE"), nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"), nullable=False)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(String, ForeignKey("investor_accounts.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default=HOLD_REQUESTED)
    request_note = Column(Text, nullable=True)
    requested_at = Column(DateTime, default=datetime.utcnow)
    decided_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    end_reason = Column(String, nullable=True)       # expired | released | withdrawn | closed
    extended_count = Column(Integer, nullable=False, default=0)
    is_test = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_drhold_listing_status", "listing_id", "status"),
        # at most ONE active hold per listing, enforced by the database itself
        Index("uq_drhold_one_active", "listing_id", unique=True,
              postgresql_where=text("status = 'active'"), sqlite_where=text("status = 'active'")),
    )
