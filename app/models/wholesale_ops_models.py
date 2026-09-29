"""Wholesale pilot operations: temperature overrides, callbacks, deal notes,
human takeover and pilot controls.

Owned by WS6/WS7 (overnight build 2026-09-28). NEW TABLES ONLY - nothing here
alters an existing table, so `Base.metadata.create_all` creates them and no
COLUMNS_TO_ADD entry is needed.

Every table carries `organization_id` NOT NULL and is indexed by it: the same
isolation guarantee as app/models/wholesale_models.py. The seller is still a
`Lead` - these rows point AT a lead / deal / property, they never copy contact
details, consent or DNC state (those stay on the Lead where every send gate
reads them).

HOT / WARM / COLD
-----------------
`Lead.engagement_temperature` is machine-set (engagement_service). A person's
choice is NOT written there - it would be overwritten by the next recompute,
which is exactly "AI silently overwriting a human-selected state". Instead
every human decision is an append-only row in `wholesale_temperature_overrides`.
The latest row for a subject says whether an override is in force:
action == "set" -> human_temperature wins; action == "clear" -> the AI value
is effective again. The AI's value and its reason at the moment of the
decision are frozen onto the row for the audit trail.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.models.models import Base, gen_uuid

TEMPERATURES = ("HOT", "WARM", "COLD")
OVERRIDE_ACTIONS = ("set", "clear")

CALLBACK_STATUSES = ("due", "completed", "cancelled")
CALLBACK_SOURCES = ("manual", "exception", "seller_request")

CONTROL_MODES = ("ai", "human")

PILOT_STATUSES = ("draft", "running", "paused", "stopped")


class WholesaleTemperatureOverride(Base):
    """Append-only audit of human HOT/WARM/COLD decisions on a seller."""

    __tablename__ = "wholesale_temperature_overrides"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    deal_id = Column(String, nullable=True)
    action = Column(String, nullable=False, default="set")        # set | clear
    ai_temperature = Column(String, nullable=True)                # HOT/WARM/COLD/UNKNOWN at decision time
    ai_reason = Column(Text, nullable=True)
    human_temperature = Column(String, nullable=True)             # HOT/WARM/COLD; NULL on clear
    reason = Column(Text, nullable=True)
    actor_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_ws_tempovr_org_lead", "organization_id", "lead_id", "created_at"),
    )


class WholesaleSellerCallback(Base):
    """A promised call back to a seller. Missed ones stay overdue until a
    person completes or cancels them - nothing ages them out."""

    __tablename__ = "wholesale_seller_callbacks"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=True)
    deal_id = Column(String, nullable=True)
    property_id = Column(String, nullable=True)
    due_at = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="due")        # due | completed | cancelled
    notes = Column(Text, nullable=True)
    outcome_note = Column(Text, nullable=True)
    assigned_to_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    completed_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    source = Column(String, nullable=False, default="manual")     # manual | exception | seller_request
    source_ref = Column(String, nullable=True)                    # e.g. wholesale_work_exceptions.id
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_ws_callback_org_status_due", "organization_id", "status", "due_at"),
        Index("ix_ws_callback_org_lead", "organization_id", "lead_id"),
        Index("ix_ws_callback_org_source_ref", "organization_id", "source_ref"),
    )


class WholesaleDealNote(Base):
    """A human note on a deal / property / seller. Author and time are kept;
    an edit keeps the original author and stamps `edited_at`."""

    __tablename__ = "wholesale_deal_notes"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    deal_id = Column(String, nullable=True)
    property_id = Column(String, nullable=True)
    lead_id = Column(String, nullable=True)
    author_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    edited_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)                  # soft delete: history is kept

    __table_args__ = (
        Index("ix_ws_note_org_deal", "organization_id", "deal_id", "created_at"),
        Index("ix_ws_note_org_property", "organization_id", "property_id"),
    )


class WholesaleConversationControl(Base):
    """Pause AI / Take Over / Resume AI for ONE seller conversation.

    One row per (organization, lead). Enforced server-side in
    wholesale_sms.enforce_for_lead (every SMS to a lead funnels through it via
    sms_service.send_sms) and in the voice router: while `paused_ai` is True or
    `mode == "human"`, only a MANUAL send by a person goes out."""

    __tablename__ = "wholesale_conversation_controls"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    mode = Column(String, nullable=False, default="ai")           # ai | human
    paused_ai = Column(Boolean, nullable=False, default=False)
    taken_over_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    reason = Column(Text, nullable=True)
    updated_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    changed_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_ws_convctl_org_lead", "organization_id", "lead_id", unique=True),
    )


class WholesaleConversationControlEvent(Base):
    """Audit of every Pause / Take over / Resume, append-only."""

    __tablename__ = "wholesale_conversation_control_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, nullable=False)
    action = Column(String, nullable=False)                       # pause | takeover | resume
    actor_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_ws_convctl_evt_org_lead", "organization_id", "lead_id", "created_at"),
    )


class WholesalePilotControl(Base):
    """The first controlled batch: one row per organization.

    `max_records` is a CAP (<= evosense.strategy.PILOT_HARD_CAP), never a
    target. Paid data is NOT switched on by anything here."""

    __tablename__ = "wholesale_pilot_controls"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, unique=True)
    status = Column(String, nullable=False, default="draft")      # draft | running | paused | stopped
    max_records = Column(Integer, nullable=False, default=250)
    source = Column(String, nullable=True)                        # free label: list / provider / file
    strategy_id = Column(String, nullable=True)                   # evosense_strategies.id (same org)
    skip_trace_budget_cents = Column(Integer, nullable=False, default=0)
    outreach_daily_limit = Column(Integer, nullable=False, default=0)
    notes = Column(Text, nullable=True)
    updated_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
