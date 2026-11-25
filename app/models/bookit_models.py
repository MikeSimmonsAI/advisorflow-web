"""Restland BookIT (owner directive v2.0, Nov 25 2026) - additive tables.

BookIT is the handoff sheet's work inside EvoSys, plus what the sheet never
had: structured sales with line items and attribution, Permission to Access /
Planning Guide status, Evo 6 Block family networks (parent batch + child
contacts), ADD Lead family-care cases, family service requests, the close-
ticket disposition, notifications and an audit trail.

Everything here is ADDITIVE. Appointments stay BookingLink, follow-ups stay
ProgramConversation, families stay Lead, the team stays ProgramTeamMember,
assignment stays HandoffSheetRow - BookIT points at those by id and never
copies them, so the sheet, the app and BookIT read the same records.

Money is integer cents, calculated on the server (services/bookit/sales.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)

from app.models.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


# ── vocabularies (strings, not DB enums: additive and SQLite-safe) ───────────

ACQUISITION_SOURCES = ("evosys_appointment", "evosys_follow_up", "evo6_block", "add_lead", "independent", "unknown")
SALE_STATUSES = ("closed", "pending", "void")
SALES_DISPOSITIONS = ("closed_sale", "no_sale", "pending_decision", "follow_up_needed")
DEFAULT_CATEGORIES = ("Cemetery property", "Funeral arrangements", "Mausoleum / niche", "Memorial / marker",
                      "Cremation arrangements", "Opening / closing", "Other products or services")
PTA_STATUSES = ("not_reviewed", "pending", "completed", "needs_update")
PPG_STATUSES = ("not_provided", "provided", "reviewed")
EVO6_COLLECTION = ("not_started", "partial", "complete", "declined")
CONTACT_VALIDATION = ("pending", "valid", "invalid", "duplicate")
ENROLLMENT_STATES = ("not_enrolled", "pending_approval", "enrolled", "paused", "stopped", "completed")
REFERRAL_KINDS = ("evo6", "add")
ADD_STATUSES = ("intake", "review", "contact", "assist", "waiting_family", "resolved")
SERVICE_STATUSES = ("new", "assigned", "in_progress", "waiting_info", "resolved", "closed")
PRIORITIES = ("normal", "high", "urgent")


class BookitSale(Base):
    """One contract (or a pending opportunity). Counted once however many
    appointments touch it. Lines hold the money; the header holds totals the
    server computed from them."""
    __tablename__ = "bookit_sales"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key", name="uq_bookit_sale_idem"),
                      Index("ix_bookit_sales_org_status", "organization_id", "status"))

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False, index=True)
    idempotency_key = Column(String, nullable=False)          # repeated submits -> one sale
    lead_id = Column(String, nullable=True, index=True)       # the family
    appointment_id = Column(String, nullable=True, index=True)   # BookingLink id
    follow_up_id = Column(String, nullable=True)              # FU-<conversation id> it came from
    acquisition_source = Column(String, nullable=False, default="unknown")
    booking_source = Column(String, nullable=True)            # ai | self_booked | follow_up | manual | public_link
    campaign_key = Column(String, nullable=True)
    referral_batch_id = Column(String, nullable=True)
    referral_contact_id = Column(String, nullable=True)
    add_case_id = Column(String, nullable=True)
    referring_advisor = Column(String, nullable=True)
    assigned_advisor = Column(String, nullable=True)
    closing_advisor = Column(String, nullable=True)
    contract_ref = Column(String, nullable=True)
    sale_date = Column(Date, nullable=True)
    gross_cents = Column(Integer, nullable=False, default=0)
    discount_cents = Column(Integer, nullable=False, default=0)   # line discounts + contract discount
    adjustment_cents = Column(Integer, nullable=False, default=0)
    net_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String, nullable=False, default="USD")
    status = Column(String, nullable=False, default="closed")
    amount_missing = Column(Boolean, nullable=False, default=False)  # legacy "Sold? Yes" with no amount
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    updated_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BookitSaleLine(Base):
    __tablename__ = "bookit_sale_lines"

    id = Column(String, primary_key=True, default=_uuid)
    sale_id = Column(String, ForeignKey("bookit_sales.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    category = Column(String, nullable=False)
    description = Column(String, nullable=True)
    quantity = Column(Integer, nullable=False, default=1)
    unit_cents = Column(Integer, nullable=False, default=0)
    gross_cents = Column(Integer, nullable=False, default=0)
    discount_cents = Column(Integer, nullable=False, default=0)
    net_cents = Column(Integer, nullable=False, default=0)


class BookitAudit(Base):
    """Who changed what, when and why - ids and values, never message bodies."""
    __tablename__ = "bookit_audit"
    __table_args__ = (Index("ix_bookit_audit_entity", "organization_id", "entity", "entity_id"),)

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    entity = Column(String, nullable=False)                   # sale | disposition | referral | add_case | ...
    entity_id = Column(String, nullable=False)
    action = Column(String, nullable=False)                   # create | update | void | reassign | ...
    field = Column(String, nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    user_id = Column(String, nullable=True)
    user_name = Column(String, nullable=True)
    at = Column(DateTime, default=datetime.utcnow)


class BookitPaperwork(Base):
    """Permission to Access and the Personal Planning Guide, per family.
    Separate from permission to send marketing (that stays on the lead)."""
    __tablename__ = "bookit_paperwork"
    __table_args__ = (UniqueConstraint("organization_id", "lead_id", name="uq_bookit_paperwork_lead"),)

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    lead_id = Column(String, nullable=False)
    pta_status = Column(String, nullable=False, default="not_reviewed")
    pta_completed_on = Column(Date, nullable=True)
    pta_advisor = Column(String, nullable=True)
    pta_appointment_id = Column(String, nullable=True)
    pta_document_ref = Column(String, nullable=True)
    pta_notes = Column(Text, nullable=True)
    ppg_status = Column(String, nullable=False, default="not_provided")
    ppg_on = Column(Date, nullable=True)
    updated_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BookitDisposition(Base):
    """Closing an appointment ticket. One per appointment; saved as a draft
    until closed. The outcome also goes to the sheet's Outcome column."""
    __tablename__ = "bookit_dispositions"
    __table_args__ = (UniqueConstraint("organization_id", "appointment_id", name="uq_bookit_disposition_appt"),)

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    appointment_id = Column(String, nullable=False)
    lead_id = Column(String, nullable=True)
    outcome = Column(String, nullable=True)                   # Showed | No-Show | Rescheduled | Cancelled
    sales_disposition = Column(String, nullable=True)
    pta_status = Column(String, nullable=True)
    ppg_status = Column(String, nullable=True)
    evo6_status = Column(String, nullable=True)
    evo6_reason = Column(Text, nullable=True)
    follow_up_on = Column(Date, nullable=True)
    final_notes = Column(Text, nullable=True)
    is_draft = Column(Boolean, nullable=False, default=True)
    closed_at = Column(DateTime, nullable=True)
    closed_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BookitReferralBatch(Base):
    """An Evo 6 Block (or an ADD Lead's family network): the parent that
    groups contacts for reporting and lineage. Each contact is worked on its
    own."""
    __tablename__ = "bookit_referral_batches"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key", name="uq_bookit_batch_idem"),)

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False, index=True)
    idempotency_key = Column(String, nullable=False)
    kind = Column(String, nullable=False, default="evo6")      # evo6 | add
    origin_lead_id = Column(String, nullable=True, index=True)
    source_appointment_id = Column(String, nullable=True)
    add_case_id = Column(String, nullable=True)
    submitting_advisor = Column(String, nullable=True)
    original_campaign = Column(String, nullable=True)
    collection_status = Column(String, nullable=False, default="not_started")
    explanation = Column(Text, nullable=True)                 # fewer than six: why
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BookitReferralContact(Base):
    __tablename__ = "bookit_referral_contacts"
    __table_args__ = (Index("ix_bookit_contact_batch", "batch_id"),
                      Index("ix_bookit_contact_norm", "organization_id", "norm_phone"))

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    batch_id = Column(String, ForeignKey("bookit_referral_batches.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    relationship = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    norm_phone = Column(String, nullable=True)                # digits, 1XXXXXXXXXX
    email = Column(String, nullable=True)
    norm_email = Column(String, nullable=True)
    street = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    validation_status = Column(String, nullable=False, default="pending")
    outreach_authorized = Column(Boolean, nullable=False, default=False)   # documented, by a person
    channels = Column(String, nullable=True)                  # "sms,email"
    suppressed = Column(Boolean, nullable=False, default=False)
    suppression_reason = Column(String, nullable=True)
    assigned_advisor = Column(String, nullable=True)
    lead_id = Column(String, nullable=True, index=True)       # the contact's own family record
    duplicate_of_lead_id = Column(String, nullable=True)
    enrollment_state = Column(String, nullable=False, default="not_enrolled")
    enrollment_step = Column(Integer, nullable=False, default=0)
    enrolled_at = Column(DateTime, nullable=True)
    follow_up_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BookitAddCase(Base):
    """An ADD Lead: a reported recent death and the family to help. Human-led;
    nothing is sent automatically."""
    __tablename__ = "bookit_add_cases"
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key", name="uq_bookit_add_idem"),)

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False, index=True)
    idempotency_key = Column(String, nullable=False)
    deceased_name = Column(String, nullable=False)
    deceased_lead_id = Column(String, nullable=True)
    date_of_passing = Column(Date, nullable=True)
    reported_at = Column(DateTime, default=datetime.utcnow)
    report_source = Column(String, nullable=True)
    verification_status = Column(String, nullable=False, default="unverified")
    arrangement_status = Column(String, nullable=True)
    contact_name = Column(String, nullable=True)
    contact_relationship = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    contact_lead_id = Column(String, nullable=True)
    preferred_channel = Column(String, nullable=True)
    preferred_time = Column(String, nullable=True)
    immediate_needs = Column(Text, nullable=True)
    assigned_advisor = Column(String, nullable=True)
    priority = Column(String, nullable=False, default="high")
    status = Column(String, nullable=False, default="intake")
    notes = Column(Text, nullable=True)
    resolution = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BookitServiceRequest(Base):
    __tablename__ = "bookit_service_requests"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False, index=True)
    lead_id = Column(String, nullable=True)
    family_name = Column(String, nullable=True)
    description = Column(Text, nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow)
    assigned_to = Column(String, nullable=True)
    priority = Column(String, nullable=False, default="normal")
    status = Column(String, nullable=False, default="new")
    notes = Column(Text, nullable=True)
    resolution = Column(Text, nullable=True)
    appointment_id = Column(String, nullable=True)
    created_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BookitNotification(Base):
    """For one advisor (recipient = their team name) or management
    (recipient = '*management*'). dedupe_key keeps one per event."""
    __tablename__ = "bookit_notifications"
    __table_args__ = (UniqueConstraint("organization_id", "dedupe_key", name="uq_bookit_notification"),
                      Index("ix_bookit_notifications_recipient", "organization_id", "recipient", "read_at"))

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    recipient = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)
    ref_type = Column(String, nullable=True)
    ref_id = Column(String, nullable=True)
    dedupe_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime, nullable=True)


class BookitCategory(Base):
    """Management-configurable product categories (defaults seeded once)."""
    __tablename__ = "bookit_categories"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_bookit_category"),)

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=True)
