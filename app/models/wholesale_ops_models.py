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


# ── FIELD APPOINTMENTS (build list #8-#10) ──────────────────────────────────
#
# A visit to a seller by someone on the Wholesale team. Booked from a property,
# from the Callback Center, or by hand; assigned to one rep; shows up in that
# rep's "today". Like the callback above it POINTS AT the seller (lead) and the
# property - it never copies a phone number or consent. The address is the
# place to drive to, kept so the rep's list still reads right if the property
# record is edited later.

APPOINTMENT_STATUSES = ("scheduled", "completed", "no_show", "cancelled")
APPOINTMENT_SOURCES = ("manual", "property", "callback", "call")


class WholesaleFieldAppointment(Base):
    __tablename__ = "wholesale_field_appointments"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    evosense_property_id = Column(String, nullable=True)     # wholesale/evosense/property/<id>
    property_id = Column(String, nullable=True)              # Wholesale Operations property
    deal_id = Column(String, nullable=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    assigned_to_id = Column(String, ForeignKey("users.id"), nullable=True)
    starts_at = Column(DateTime, nullable=False)              # UTC
    duration_minutes = Column(Integer, nullable=False, default=60)
    address = Column(String, nullable=True)
    title = Column(String, nullable=True)                     # e.g. "Walkthrough - 123 Main St"
    notes = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="scheduled")
    outcome = Column(String, nullable=True)                   # set by #16 (signed | follow_up | dead)
    outcome_note = Column(Text, nullable=True)
    # The phone's own id for its last change (build list #28). A change the
    # phone kept through a dead zone may arrive twice; the second is ignored.
    outcome_ref = Column(String, nullable=True)
    source = Column(String, nullable=False, default="manual")
    source_ref = Column(String, nullable=True)                # e.g. the callback it was booked from
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_ws_appt_org_starts", "organization_id", "starts_at"),
        Index("ix_ws_appt_org_rep_starts", "organization_id", "assigned_to_id", "starts_at"),
        Index("ix_ws_appt_org_esprop", "organization_id", "evosense_property_id"),
    )


# ── THE VISIT RECORD (build list #12 interview, #13 walkthrough) ────────────
#
# What the rep learned at the kitchen table and saw walking the house, kept
# with the appointment it belongs to. The answers are the rep's notes about
# the visit - they are not the seller's consent or contact details, which
# stay on the Lead.

class WholesaleFieldVisit(Base):
    __tablename__ = "wholesale_field_visits"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    appointment_id = Column(String, ForeignKey("wholesale_field_appointments.id", ondelete="CASCADE"),
                            nullable=False, unique=True)
    evosense_property_id = Column(String, nullable=True)
    interview = Column(Text, nullable=True)        # JSON: motivation, timeline, owed, price wanted ...
    repairs = Column(Text, nullable=True)          # JSON: condition + room-by-room lines
    updated_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_ws_visit_org_esprop", "organization_id", "evosense_property_id"),
    )


class WholesaleFieldVisitPhoto(Base):
    """A walkthrough photo. The bytes live wherever wholesale_files keeps
    files (the database when nothing else is set); `storage_key` never
    leaves the server - photos are served through an authenticated route."""
    __tablename__ = "wholesale_field_visit_photos"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    appointment_id = Column(String, ForeignKey("wholesale_field_appointments.id", ondelete="CASCADE"),
                            nullable=False)
    room = Column(String, nullable=True)
    caption = Column(String, nullable=True)
    storage_backend = Column(String, nullable=False)
    storage_key = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    byte_size = Column(Integer, nullable=False, default=0)
    checksum = Column(String, nullable=True)
    original_filename = Column(String, nullable=True)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_ws_visit_photo_org_appt", "organization_id", "appointment_id"),
    )


# ── FIELD OFFERS (build list #14 locked calculator, #20 approvals) ──────────
#
# A field login never sees the investor percentage or the fee. The server
# works out the most they may offer (the workspace's own MAO formula times the
# workspace's field limit) and records every offer they make: inside the limit
# it simply stands; above it, it waits for an admin to approve or decline.

OFFER_STATUSES = ("within", "pending", "approved", "declined")


class WholesaleFieldRules(Base):
    __tablename__ = "wholesale_field_rules"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, unique=True)
    offer_limit_pct = Column(Integer, nullable=False, default=100)   # % of the formula's max offer
    # How far a rep may raise their own opening offer on a visit without
    # asking (10 = up to 10% above it). 100 = no raise rule.
    raise_limit_pct = Column(Integer, nullable=True, default=10)
    updated_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WholesaleFieldOffer(Base):
    __tablename__ = "wholesale_field_offers"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    appointment_id = Column(String, ForeignKey("wholesale_field_appointments.id", ondelete="CASCADE"),
                            nullable=False)
    requested_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    arv = Column(Integer, nullable=False)
    repairs = Column(Integer, nullable=False, default=0)
    mao = Column(Integer, nullable=True)          # the formula's answer (never shown to a field login)
    offer_limit = Column(Integer, nullable=True)  # the most the rep could offer on their own
    offer = Column(Integer, nullable=False)
    note = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="within")
    decided_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_ws_offer_org_status", "organization_id", "status"),
        Index("ix_ws_offer_org_appt", "organization_id", "appointment_id"),
    )


# ── CANVASSING (build list #17 route by area, #18 door-knock form) ──────────
#
# A route is a list of doors in one ZIP picked from EvoSense's own findings
# (best first), given to a canvasser for a day. Each door keeps what the
# canvasser needs at the curb - the address, the owner of record, why it was
# flagged - so they never need access to the property records themselves.
# What they learn at the door is written on the door row; a hot lead becomes
# a call-back for the person who sends the field specialist.

CANVASS_RESULTS = ("not_home", "talked", "not_interested", "hot", "bad_address")


class WholesaleCanvassRoute(Base):
    __tablename__ = "wholesale_canvass_routes"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    name = Column(String, nullable=False)
    area = Column(String, nullable=True)              # the ZIP (or ZIPs) it was built from
    route_date = Column(DateTime, nullable=True)      # the day it is for (Central midnight, as UTC)
    assigned_to_id = Column(String, ForeignKey("users.id"), nullable=True)
    status = Column(String, nullable=False, default="open")    # open | done
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_ws_canvass_org_rep", "organization_id", "assigned_to_id"),
    )


class WholesaleCanvassDoor(Base):
    __tablename__ = "wholesale_canvass_doors"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    route_id = Column(String, ForeignKey("wholesale_canvass_routes.id", ondelete="CASCADE"), nullable=False)
    evosense_property_id = Column(String, nullable=True)
    sort = Column(Integer, nullable=False, default=0)
    address = Column(String, nullable=False)
    owner_of_record = Column(String, nullable=True)
    why = Column(String, nullable=True)
    # what happened at the door
    result = Column(String, nullable=True)            # CANVASS_RESULTS
    talked_to = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    permission = Column(String, nullable=True)        # yes | no | (blank)
    interest = Column(String, nullable=True)          # hot | warm | cold
    notes = Column(Text, nullable=True)
    knocked_at = Column(DateTime, nullable=True)
    knocked_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    handed_off_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_ws_canvass_door_org_route", "organization_id", "route_id"),
    )
