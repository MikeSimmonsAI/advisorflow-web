"""
Sales scheduling — availability, meeting types, appointments, participants.

CHECKPOINT 2. Registers on the SAME Base as models.py and sales_models.py.

THE PROBLEM THIS SOLVES
-----------------------
A Discovery + Demo may need Blake (owner), Michael (manager) and Mike (product
specialist) in the same room. Nobody should be comparing three calendars by
hand. The engine returns only the times when EVERY required participant is
free — an intersection, not a union.

Grok's `computeSlots` is a union across interchangeable reps ("any one of N is
free"), and its `appointments` table has a single `staff_id`. That answers a
different question and cannot be extended into this one, which is why
participants are a real table here.

TENANCY — the rule this file must not break
-------------------------------------------
A sales appointment belongs to the BRAND SALES domain. It has a
`brand_sales_org_id` and it has NO customer `organization_id`. An EvoSys Pro
discovery call is not a Greenland Cemetery appointment, and the existing
`booking_links` / customer scheduling surface is a separate thing that this
module neither reads nor writes.

TIME STORAGE
------------
Every instant column is naive UTC, matching `datetime.utcnow()` used throughout
this codebase. Local wall-clock intent lives in `timezone` (an IANA name) plus
minutes-from-midnight integers on the recurring rows, which is what makes DST
come out right: 9:00am local stays 9:00am local across a transition, and only
its UTC offset moves.
"""

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Text, Integer,
    UniqueConstraint, Index,
)
from datetime import datetime
import uuid

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# ── vocabularies ─────────────────────────────────────────────────────────────
# Plain strings, per the codebase convention (never call .value on these).

# Role slots. A meeting type asks for ROLES; the resolver turns them into people
# for the specific opportunity and brand. Hardcoding "Blake" into a meeting type
# would make the type unusable for the next brand.
SLOT_OPPORTUNITY_OWNER  = "opportunity_owner"
SLOT_SALES_MANAGER      = "sales_manager"
SLOT_PRODUCT_SPECIALIST = "product_specialist"
SLOT_ANY_REP            = "any_rep"
ROLE_SLOTS = (SLOT_OPPORTUNITY_OWNER, SLOT_SALES_MANAGER,
              SLOT_PRODUCT_SPECIALIST, SLOT_ANY_REP)

SLOT_LABELS = {
    SLOT_OPPORTUNITY_OWNER:  "Opportunity Owner",
    SLOT_SALES_MANAGER:      "Sales Manager",
    SLOT_PRODUCT_SPECIALIST: "Product Specialist",
    SLOT_ANY_REP:            "Any Representative",
}

APPT_SCHEDULED = "scheduled"
APPT_COMPLETED = "completed"
APPT_CANCELLED = "cancelled"
APPT_NO_SHOW   = "no_show"
APPOINTMENT_STATUSES = (APPT_SCHEDULED, APPT_COMPLETED, APPT_CANCELLED, APPT_NO_SHOW)

# An appointment only occupies someone's calendar while it is scheduled. A
# cancelled meeting must stop blocking, or a rep's week silently fills with
# ghosts.
BLOCKING_STATUSES = (APPT_SCHEDULED,)

CONF_PENDING   = "pending"
CONF_SENT      = "sent"
CONF_CONFIRMED = "confirmed"
CONF_DECLINED  = "declined"
CONF_CANCELLED = "cancelled"
CONF_NO_SHOW   = "no_show"
CONFIRMATION_STATUSES = (CONF_PENDING, CONF_SENT, CONF_CONFIRMED,
                         CONF_DECLINED, CONF_CANCELLED, CONF_NO_SHOW)

# Where a confirmation came from. Recorded because "confirmed" means something
# different when the prospect clicked a link than when a rep ticked a box.
CONF_SRC_PROSPECT_LINK = "prospect_link"
CONF_SRC_STAFF_MANUAL  = "staff_manual"
CONF_SRC_EMAIL_REPLY   = "email_reply"
CONF_SRC_PROVIDER      = "provider"
CONFIRMATION_SOURCES = (CONF_SRC_PROSPECT_LINK, CONF_SRC_STAFF_MANUAL,
                        CONF_SRC_EMAIL_REPLY, CONF_SRC_PROVIDER)

ATTEND_UNKNOWN  = "unknown"
ATTEND_ACCEPTED = "accepted"
ATTEND_DECLINED = "declined"
ATTEND_ATTENDED = "attended"
ATTEND_NO_SHOW  = "no_show"

BLOCK_RECURRING = "recurring"   # weekly, e.g. lunch
BLOCK_TIME_OFF  = "time_off"    # a dated absence

DEFAULT_TIMEZONE = "America/Chicago"


class AvailabilityProfile(Base):
    """One person's scheduling rules. The inputs to the engine.

    Deliberately per-USER, not per-user-per-brand: a human has one working day.
    If Mike sells EvoSys Pro in the morning and BookaBoost in the afternoon, he
    is still one person who cannot be in two meetings at once.
    """
    __tablename__ = "availability_profiles"

    id      = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, unique=True)

    # IANA name. NOT hardcoded anywhere — the team default is applied when a
    # profile is first created and can be changed per person from then on.
    timezone = Column(String, default=DEFAULT_TIMEZONE, nullable=False)

    # Padding either side of a meeting. Applied to THIS person's calendar, so a
    # rep who needs 15 minutes to write notes gets it without imposing that on
    # everyone else in the room.
    buffer_before_minutes = Column(Integer, default=0, nullable=False)
    buffer_after_minutes  = Column(Integer, default=0, nullable=False)

    # How soon someone may be booked, and how far ahead.
    min_notice_minutes    = Column(Integer, default=120, nullable=False)
    booking_horizon_days  = Column(Integer, default=60, nullable=False)

    # Off entirely — on leave, not selling this quarter. Distinct from having no
    # working hours, which would silently look the same to the engine.
    accepts_bookings = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AvailabilityWindow(Base):
    """A recurring weekly working period, in LOCAL minutes from midnight.

    Storing 9:00am as 540 rather than as a UTC instant is what makes DST work:
    the number never changes, and the engine resolves it against the user's
    timezone on each specific date. Several rows per day allow split shifts.
    """
    __tablename__ = "availability_windows"

    id         = Column(String, primary_key=True, default=gen_uuid)
    profile_id = Column(String, ForeignKey("availability_profiles.id", ondelete="CASCADE"),
                        nullable=False)
    # 0 = Monday … 6 = Sunday (Python's date.weekday()).
    day_of_week  = Column(Integer, nullable=False)
    start_minute = Column(Integer, nullable=False)   # 540 = 09:00 local
    end_minute   = Column(Integer, nullable=False)   # 1020 = 17:00 local

    __table_args__ = (
        Index("ix_avail_windows_profile", "profile_id", "day_of_week"),
    )


class AvailabilityBlock(Base):
    """Time carved OUT of the working day. Two kinds in one table.

    `recurring` — weekly, local minutes (lunch, a standing internal meeting).
    `time_off`  — a dated absence, stored as naive UTC instants.

    One table because both answer the same question ("is this person free?") and
    the engine subtracts them identically; splitting them would duplicate the
    expansion logic twice over.
    """
    __tablename__ = "availability_blocks"

    id         = Column(String, primary_key=True, default=gen_uuid)
    profile_id = Column(String, ForeignKey("availability_profiles.id", ondelete="CASCADE"),
                        nullable=False)
    kind  = Column(String, nullable=False)          # BLOCK_RECURRING | BLOCK_TIME_OFF
    label = Column(String, nullable=True)           # "Lunch", "PTO", "Conference"

    # kind = recurring
    day_of_week  = Column(Integer, nullable=True)
    start_minute = Column(Integer, nullable=True)
    end_minute   = Column(Integer, nullable=True)

    # kind = time_off (naive UTC)
    starts_at = Column(DateTime, nullable=True)
    ends_at   = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_avail_blocks_profile", "profile_id", "kind"),
        Index("ix_avail_blocks_range", "starts_at", "ends_at"),
    )


class MeetingType(Base):
    """What kind of meeting, how long, and WHICH ROLES must attend.

    `required_slots` holds role slugs, never user ids. For EvoSys Pro a
    Discovery + Demo resolves to Blake + Michael + Mike; for the next brand the
    same meeting type resolves to entirely different people with no change here.
    """
    __tablename__ = "sales_meeting_types"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=False)
    key         = Column(String, nullable=False)     # discovery | discovery_demo | demo | …
    name        = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    duration_minutes = Column(Integer, default=30, nullable=False)

    # Comma-separated role slugs. Plain text rather than JSON because the
    # codebase has no JSON column convention and the list is tiny and ordered.
    required_slots = Column(String, nullable=True)   # "opportunity_owner,sales_manager"
    optional_slots = Column(String, nullable=True)

    is_internal = Column(Boolean, default=False, nullable=False)   # no prospect attends
    is_active   = Column(Boolean, default=True, nullable=False)
    sort_order  = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("brand_sales_org_id", "key", name="uq_meeting_type_org_key"),
        Index("ix_meeting_types_org", "brand_sales_org_id", "is_active"),
    )

    def required_slot_list(self):
        return [s for s in (self.required_slots or "").split(",") if s]

    def optional_slot_list(self):
        return [s for s in (self.optional_slots or "").split(",") if s]


class SalesAppointment(Base):
    """The central sales appointment. AdvisorFlow is the source of truth.

    NOTE THE ABSENCE: there is no `organization_id` on this table, and that is
    the point. This is a brand-sales meeting. Provider calendar events (Outlook,
    Google) will later point BACK at this row via participant event ids; they
    never become the record of truth.
    """
    __tablename__ = "sales_appointments"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=False)
    # Nullable so an internal team meeting can exist without a deal attached.
    opportunity_id  = Column(String, ForeignKey("opportunities.id", ondelete="SET NULL"),
                             nullable=True)
    meeting_type_id = Column(String, ForeignKey("sales_meeting_types.id"), nullable=True)

    title = Column(String, nullable=False)

    # Naive UTC. `timezone` is the wall-clock the meeting was agreed in and is
    # what gets displayed and put on the invite.
    starts_at = Column(DateTime, nullable=False)
    ends_at   = Column(DateTime, nullable=False)
    timezone  = Column(String, default=DEFAULT_TIMEZONE, nullable=False)

    status = Column(String, default=APPT_SCHEDULED, nullable=False)

    # Prospect identity captured AT BOOKING. Denormalised on purpose: the
    # opportunity's contact may change later, and the invite that went out said
    # what it said.
    prospect_name    = Column(String, nullable=True)
    prospect_company = Column(String, nullable=True)
    prospect_email   = Column(String, nullable=True)
    prospect_phone   = Column(String, nullable=True)
    prospect_timezone = Column(String, nullable=True)

    # Confirmation is operational state, not a booking side effect.
    confirmation_status = Column(String, default=CONF_PENDING, nullable=False)
    confirmation_source = Column(String, nullable=True)
    confirmation_sent_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by = Column(String, ForeignKey("users.id"), nullable=True)

    meeting_provider = Column(String, nullable=True)   # zoom | meet | teams | phone | in_person
    meeting_url      = Column(String, nullable=True)
    location         = Column(String, nullable=True)
    notes            = Column(Text, nullable=True)

    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancelled_at   = Column(DateTime, nullable=True)
    cancel_reason  = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_sales_appt_org_time", "brand_sales_org_id", "starts_at"),
        Index("ix_sales_appt_opportunity", "opportunity_id"),
        Index("ix_sales_appt_status_time", "status", "starts_at"),
    )


class AppointmentParticipant(Base):
    """One internal person on one appointment.

    `busy_start_at` / `busy_end_at` are the appointment window EXPANDED BY THIS
    PERSON'S OWN BUFFERS, frozen at booking time. Two reasons:

      1. Double-booking is a property of a person, not of a meeting, so the
         database-level exclusion constraint belongs here — one row per person
         per meeting, with the range it actually occupies.
      2. Buffers differ per person. Storing the resolved window means a later
         change to someone's buffer preference cannot retroactively invalidate
         meetings that were legitimately booked under the old one.

    The Postgres exclusion constraint that makes a race impossible is added in
    auto_migrate.py (it needs btree_gist and cannot be expressed portably here).
    The service layer ALSO checks inside the transaction so SQLite and every
    non-racing conflict are caught identically.
    """
    __tablename__ = "sales_appointment_participants"

    id = Column(String, primary_key=True, default=gen_uuid)
    appointment_id = Column(String, ForeignKey("sales_appointments.id", ondelete="CASCADE"),
                            nullable=False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    role_slot   = Column(String, nullable=True)      # ROLE_SLOTS
    is_required = Column(Boolean, default=True, nullable=False)
    attendance_status = Column(String, default=ATTEND_UNKNOWN, nullable=False)

    # Denormalised from the parent so the conflict check is a single-table read
    # and the exclusion constraint has everything it needs on one row.
    busy_start_at = Column(DateTime, nullable=False)
    busy_end_at   = Column(DateTime, nullable=False)
    is_blocking   = Column(Boolean, default=True, nullable=False)

    # Checkpoint 3 (Microsoft, then Google). Present now so the boundary is
    # visible and nothing has to be migrated to add it later. NOT populated —
    # no calendar sync exists yet and none is faked.
    external_calendar_provider = Column(String, nullable=True)
    external_event_id          = Column(String, nullable=True)
    external_synced_at         = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("appointment_id", "user_id", name="uq_participant_appt_user"),
        Index("ix_appt_participant_user_time", "user_id", "busy_start_at", "busy_end_at"),
        Index("ix_appt_participant_appt", "appointment_id"),
    )
