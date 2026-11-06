"""EVOSYS GOLD & JEWELRY PROGRAM - the only tables the vertical adds.

Canonical entities are REUSED, never copied:
  * a customer IS a Lead (leads.id). Gold & jewelry facts the Lead has no
    column for - segments, interests, follow-up status - live in
    Lead.custom_fields under the keys in app/services/jewelry/common.py;
    contact permissions are the Lead's own allow_sms / allow_email /
    sms_consent and opt-outs are Lead.status == "dnc" plus suppression_entries;
  * a staff member IS a User holding a customer_org Membership;
  * an appointment IS a BookingLink (the platform's appointment record);
  * notes are lead_notes, follow-ups are lead_tasks, the audit trail is
    audit_log_entries.

What does NOT exist anywhere else gets a table here: the store's business
profile and AI receptionist settings, recorded store transactions (there is no
POS integration - these are entered by staff and labelled that way), the
inventory / offers list, gold & jewelry promotions, appointment details the
BookingLink has no column for, and the AI receptionist's test conversations.

TENANT RULE: every row carries a NOT NULL organization_id and every query in
app/services/jewelry filters on it first. New tables only - created by
Base.metadata.create_all, so no auto_migrate entry is needed.
"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


def _org_col(unique: bool = False):
    return Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                  nullable=False, index=True, unique=unique)


class JewelryProfile(Base):
    """One per gold & jewelry business: identity, approved facts, AI settings."""
    __tablename__ = "jewelry_profiles"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col(unique=True)
    business_name = Column(String, nullable=True)          # NULL = the org's own name
    street_address = Column(String, nullable=True)
    city_state_zip = Column(String, nullable=True)
    business_phone = Column(String, nullable=True)
    business_email = Column(String, nullable=True)
    # JSON: {"mon": "10:00-18:00", ...}; a missing day = not stated (never "closed")
    business_hours = Column(Text, nullable=True)
    hours_confirmed = Column(Boolean, nullable=False, default=False)
    services = Column(Text, nullable=True)                 # JSON list of strings
    appointment_types = Column(Text, nullable=True)        # JSON list of strings
    appointment_minutes = Column(Integer, nullable=False, default=30)
    # JSON list of {"q": ..., "a": ...} the owner approved for the assistant
    approved_answers = Column(Text, nullable=True)
    # AI receptionist: "off" | "test". There is deliberately no live value yet:
    # nothing in this program answers a real customer on its own.
    ai_mode = Column(String(12), nullable=False, default="test")
    ai_paused = Column(Boolean, nullable=False, default=False)   # emergency stop
    ai_paused_by = Column(String, nullable=True)
    ai_paused_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class JewelryTransaction(Base):
    """A store transaction a staff member RECORDED (no POS feed exists)."""
    __tablename__ = "jewelry_transactions"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    # sale = customer bought from the store; purchase = store bought from the
    # customer (gold buying); trade = both. Amount is always positive.
    kind = Column(String(12), nullable=False, default="sale")
    occurred_on = Column(DateTime, nullable=False)
    description = Column(String(300), nullable=True)
    amount = Column(Float, nullable=False, default=0.0)
    campaign_id = Column(String, ForeignKey("jewelry_campaigns.id", ondelete="SET NULL"), nullable=True)
    item_id = Column(String, ForeignKey("jewelry_items.id", ondelete="SET NULL"), nullable=True)
    recorded_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source = Column(String(20), nullable=False, default="manual")   # manual (no POS integration)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (Index("ix_jewelry_txn_org_date", "organization_id", "occurred_on"),)


class JewelryItem(Base):
    """An inventory item or offer used for marketing - not a store catalog."""
    __tablename__ = "jewelry_items"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    name = Column(String(200), nullable=False)
    category = Column(String(30), nullable=False, default="jewelry")
    status = Column(String(20), nullable=False, default="available")  # available | sold | unavailable
    description = Column(Text, nullable=True)
    sku = Column(String(80), nullable=True)
    price_note = Column(String(120), nullable=True)   # free text the owner types; never computed
    featured = Column(Boolean, nullable=False, default=False)
    interest_tags = Column(Text, nullable=True)        # JSON list of interest keywords
    created_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_jewelry_items_org_status", "organization_id", "status"),)


class JewelryItemPhoto(Base):
    """One image of an item. Bytes go through the shared media store."""
    __tablename__ = "jewelry_item_photos"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    item_id = Column(String, ForeignKey("jewelry_items.id", ondelete="CASCADE"), nullable=False, index=True)
    content_type = Column(String(60), nullable=False)
    byte_size = Column(Integer, nullable=False, default=0)
    storage_backend = Column(String(20), nullable=True)
    storage_key = Column(String(300), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class JewelryItemInterest(Base):
    """A customer asked about / is interested in an item (an inquiry)."""
    __tablename__ = "jewelry_item_interests"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    item_id = Column(String, ForeignKey("jewelry_items.id", ondelete="CASCADE"), nullable=False, index=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    note = Column(String(300), nullable=True)
    recorded_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class JewelryCampaign(Base):
    """A gold & jewelry promotion. Sending is gated - see services/jewelry/campaigns."""
    __tablename__ = "jewelry_campaigns"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    name = Column(String(160), nullable=False)
    kind = Column(String(40), nullable=False, default="custom")
    segments = Column(Text, nullable=True)            # JSON list of segment keys
    channel = Column(String(10), nullable=False, default="sms")   # sms | email | both
    sms_body = Column(Text, nullable=True)
    email_subject = Column(String(200), nullable=True)
    email_body = Column(Text, nullable=True)
    # draft | scheduled | paused | cancelled | completed
    status = Column(String(12), nullable=False, default="draft")
    scheduled_for = Column(DateTime, nullable=True)
    item_id = Column(String, ForeignKey("jewelry_items.id", ondelete="SET NULL"), nullable=True)
    marketing_cost = Column(Float, nullable=True)     # what the owner says it cost; never estimated
    approved_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class JewelryAppointmentDetail(Base):
    """What a BookingLink has no column for: the store's own status and notes."""
    __tablename__ = "jewelry_appointment_details"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    booking_link_id = Column(String, ForeignKey("booking_links.id", ondelete="CASCADE"),
                             nullable=False, unique=True, index=True)
    # scheduled | confirmed | checked_in | completed | no_show | cancelled
    status = Column(String(16), nullable=False, default="scheduled")
    internal_notes = Column(Text, nullable=True)
    follow_up_task_id = Column(String, nullable=True)
    campaign_id = Column(String, ForeignKey("jewelry_campaigns.id", ondelete="SET NULL"), nullable=True)
    created_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class JewelryAiExchange(Base):
    """One question and answer in the AI receptionist (test mode only today)."""
    __tablename__ = "jewelry_ai_exchanges"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org_col()
    session_id = Column(String(40), nullable=False, index=True)
    mode = Column(String(12), nullable=False, default="test")
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    answered_by = Column(String(20), nullable=False, default="assistant")  # assistant | operator | blocked
    intent = Column(String(40), nullable=True)
    needs_human = Column(Boolean, nullable=False, default=False)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    actor_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (Index("ix_jewelry_ai_org_created", "organization_id", "created_at"),)
