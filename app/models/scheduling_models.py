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


# ── LEADERSHIP POLICY ────────────────────────────────────────────────────────
#
# WHY THE SLOTS ABOVE ARE NOT ENOUGH, STATED PLAINLY.
#
# `required_slots = "opportunity_owner,sales_manager"` means "the owner AND a
# sales manager must both be free". Two things about that are wrong for an
# inbound booking:
#
#   1. `sales_manager` resolves to EVERY manager holding a membership in the
#      brand (see meeting_roles.resolve_slot). A rep in Kentucky would have
#      their public availability computed against managers in Texas who have
#      never met them. As the brand grows, that is not a slowly-worsening
#      inconvenience; it is a wrong answer that gets wronger.
#
#   2. It is an AND over a fixed list. The real rule is a QUORUM: the rep, plus
#      AT LEAST ONE leader from that rep's own reporting line. Requiring every
#      named manager means a slot is lost whenever any one of them is busy,
#      which on a three-person chain is most of the week.
#
# So the policy below is a small, closed vocabulary rather than a rules
# language. It answers exactly five questions - must the owner attend, where do
# the leaders come from, how many are needed, how far up to look, and do extra
# available leaders get invited - and nothing else. A general expression
# language here would be a system nobody can reason about in exchange for
# flexibility nobody has asked for.
#
# NULL is the fifth value and it is the important one: it means "this meeting
# type predates leadership policy, behave exactly as before". Every existing row
# in every brand is NULL, so nothing changes for anything already configured.

# Leaders come from the opportunity owner's own reporting chain, walked upward
# through brand-sales Membership.reports_to_user_id inside ONE brand sales org.
LEADERSHIP_REPORTING_CHAIN = "reporting_chain"
LEADERSHIP_POLICIES = (LEADERSHIP_REPORTING_CHAIN,)

# THE DELIBERATE ABSENCE. There is no "any manager in the brand" policy value,
# and adding one would be a decision to reintroduce the defect above, not a
# feature. If a brand ever genuinely wants that, it needs its own name, its own
# justification and its own tests - it must not arrive as a fallback that a
# misconfigured chain quietly lands on.

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
ATTENDANCE_STATUSES = (ATTEND_UNKNOWN, ATTEND_ACCEPTED, ATTEND_DECLINED,
                       ATTEND_ATTENDED, ATTEND_NO_SHOW)


# ── OUTCOME — what actually happened, recorded by a human ────────────────────
#
# THE GAP THIS CLOSES. T9 Intelligence reported appointment completion as
# UNKNOWN, and it was right to: `status` above only ever said what the calendar
# intended, never what took place. "scheduled" on a meeting whose time has
# passed is not evidence the meeting happened, and inferring completion from a
# clock is how a pipeline quietly fills with deals nobody actually spoke to.
#
# So completion is recorded AT THE CALENDAR SOURCE by the person who was in the
# room, and everything downstream reads that fact rather than guessing at it.
# `status` stays the lifecycle; `outcome` is the verdict. They are separate
# because "completed" and "completed, prospect wants a proposal" are different
# pieces of information and collapsing them would lose the one that matters.

OUTCOME_COMPLETED       = "completed"
OUTCOME_NO_SHOW         = "no_show"
OUTCOME_CANCELLED       = "cancelled"
OUTCOME_RESCHEDULED     = "rescheduled"
OUTCOME_FOLLOW_UP       = "follow_up_required"
OUTCOME_PROPOSAL_NEEDED = "proposal_needed"
OUTCOME_PROPOSAL_SENT   = "proposal_sent"
OUTCOME_WON             = "won"
OUTCOME_LOST            = "lost"
APPOINTMENT_OUTCOMES = (
    OUTCOME_COMPLETED, OUTCOME_NO_SHOW, OUTCOME_CANCELLED, OUTCOME_RESCHEDULED,
    OUTCOME_FOLLOW_UP, OUTCOME_PROPOSAL_NEEDED, OUTCOME_PROPOSAL_SENT,
    OUTCOME_WON, OUTCOME_LOST,
)

OUTCOME_LABELS = {
    OUTCOME_COMPLETED:       "Completed",
    OUTCOME_NO_SHOW:         "No-show",
    OUTCOME_CANCELLED:       "Cancelled",
    OUTCOME_RESCHEDULED:     "Rescheduled",
    OUTCOME_FOLLOW_UP:       "Follow-up required",
    OUTCOME_PROPOSAL_NEEDED: "Proposal needed",
    OUTCOME_PROPOSAL_SENT:   "Proposal sent",
    OUTCOME_WON:             "Won",
    OUTCOME_LOST:            "Lost",
}

# Which outcomes mean the meeting TOOK PLACE. This is the single definition of
# "it happened" that T9/T10 read, rather than each report inventing its own by
# comparing a timestamp to now(). A rescheduled meeting is deliberately absent:
# the meeting at THAT time did not occur.
OUTCOMES_OCCURRED = (OUTCOME_COMPLETED, OUTCOME_FOLLOW_UP, OUTCOME_PROPOSAL_NEEDED,
                     OUTCOME_PROPOSAL_SENT, OUTCOME_WON, OUTCOME_LOST)

# Which outcomes mean it definitively did NOT take place.
OUTCOMES_NOT_OCCURRED = (OUTCOME_NO_SHOW, OUTCOME_CANCELLED, OUTCOME_RESCHEDULED)

# The appointment lifecycle status each outcome settles the row into, so the
# two fields can never contradict each other. Recording "no_show" and leaving
# `status` at "scheduled" would leave the meeting blocking calendars forever.
OUTCOME_TO_STATUS = {
    OUTCOME_COMPLETED:       APPT_COMPLETED,
    OUTCOME_FOLLOW_UP:       APPT_COMPLETED,
    OUTCOME_PROPOSAL_NEEDED: APPT_COMPLETED,
    OUTCOME_PROPOSAL_SENT:   APPT_COMPLETED,
    OUTCOME_WON:             APPT_COMPLETED,
    OUTCOME_LOST:            APPT_COMPLETED,
    OUTCOME_NO_SHOW:         APPT_NO_SHOW,
    OUTCOME_CANCELLED:       APPT_CANCELLED,
    OUTCOME_RESCHEDULED:     APPT_CANCELLED,
}

# Outcomes that only make sense when a deal is attached. Offering "Won" on an
# internal pipeline review is how a vocabulary stops being trusted.
OUTCOMES_REQUIRING_OPPORTUNITY = (OUTCOME_PROPOSAL_NEEDED, OUTCOME_PROPOSAL_SENT,
                                  OUTCOME_WON, OUTCOME_LOST)

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

    # ── Video meetings (Checkpoint 4) ───────────────────────────────────────
    # Whether booking this type should provision a video meeting. Per TYPE, not
    # global: an internal pipeline review does not need a Zoom room, and
    # creating one anyway burns the account's concurrent-meeting limit and fills
    # the host's Zoom dashboard with rooms nobody joins.
    #
    # Defaults FALSE so an existing meeting type never silently starts creating
    # Zoom meetings when this ships. The seed turns it on for the customer-facing
    # types explicitly.
    requires_video = Column(Boolean, default=False, nullable=False)
    # Which provider, when the brand has more than one configured. NULL means
    # "whatever the brand's default is" — the usual case.
    video_provider = Column(String, nullable=True)

    # ── Leadership quorum (inbound Discovery / Demo) ────────────────────────
    # See LEADERSHIP_POLICY above for why these exist and why NULL is the
    # default. Every column here is additive and inert until a policy is set.
    leadership_policy = Column(String, nullable=True)   # LEADERSHIP_POLICIES

    # Must the opportunity owner personally attend? Almost always yes - the
    # prospect booked with THEM - but it is stated rather than assumed so a
    # brand can run a pooled meeting type later without a schema change.
    owner_required = Column(Boolean, default=True, nullable=False)

    # How many leaders from the chain must be free for a slot to be offered.
    # 0 with a policy set means "leaders are welcome but never required", which
    # is a legitimate configuration and not the same as having no policy.
    leadership_minimum = Column(Integer, default=0, nullable=False)

    # How far up the chain to look. 2 means the rep's manager and that
    # manager's manager. Bounded on purpose: an unbounded walk up a corporate
    # org chart ends at somebody who has never heard of the prospect.
    leadership_depth = Column(Integer, default=0, nullable=False)

    # When more leaders than the minimum are free at the chosen time, invite
    # them all. FALSE books only the nearest `leadership_minimum`.
    include_additional_leaders = Column(Boolean, default=False, nullable=False)

    # ── Public bookability ──────────────────────────────────────────────────
    # WHETHER AN UNAUTHENTICATED VISITOR MAY BOOK THIS TYPE AT ALL.
    #
    # Defaults FALSE, and that default is a security control rather than a
    # convenience. Without it, the public endpoint's meeting-type parameter
    # would be a way for anyone on the internet to book "Internal Sales
    # Meeting" onto a management team's calendar. A brand turns this on for the
    # one or two types its website actually offers.
    public_bookable = Column(Boolean, default=False, nullable=False)

    # ── WHO LAST WROTE TO THIS ROW, SYSTEM OR PERSON ────────────────────────
    #
    # `updated_at` alone cannot answer that, and the difference decides whether
    # a system default may be applied. `ensure_meeting_types` runs more than one
    # backfill over these rows; each one moves `updated_at`, so the FIRST
    # backfill made every row look hand-edited to every backfill after it, and
    # the later one skipped those rows permanently. That is what left a brand
    # with a correctly seeded Discovery + Demo that its own website could never
    # book.
    #
    # This column records when the SYSTEM last wrote its defaults here. Anything
    # that moves `updated_at` past it is a person, and a person's choice is
    # final. NULL means the system has never stamped this row.
    system_defaults_at = Column(DateTime, nullable=True)

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

    # ── HOW THIS BOOKING ARRIVED ────────────────────────────────────────────
    # NULL means a salesperson booked it from inside the product, which is
    # every row that exists today. A public website booking is a materially
    # different thing - nobody authenticated, the participants were chosen by a
    # policy rather than by a human - and reporting that cannot tell the two
    # apart cannot answer "is the website working".
    booking_source = Column(String, nullable=True)   # BOOKING_SOURCES

    # ── THE RETRY GUARD ─────────────────────────────────────────────────────
    #
    # A browser posts a booking, the connection dies, the visitor presses the
    # button again. Without this they now have two meetings, two Zoom rooms, two
    # confirmation emails and two entries on three people's calendars - and the
    # second one is indistinguishable from a genuine second booking.
    #
    # The key is generated by the page and carried through every side effect, so
    # the retry finds this row and returns the ORIGINAL result. The uniqueness
    # is a database constraint rather than a lookup, because "check then insert"
    # is two statements and two simultaneous submissions both read "no" in the
    # gap between them. See app/services/ai_operations/idempotency.py, which
    # states the same rule for the same reason.
    #
    # Scoped per brand: two brands' websites generating the same random key is
    # vanishingly unlikely and would be a confusing failure rather than a safe
    # one.
    booking_idempotency_key = Column(String, nullable=True)

    # Confirmation is operational state, not a booking side effect.
    confirmation_status = Column(String, default=CONF_PENDING, nullable=False)
    confirmation_source = Column(String, nullable=True)
    confirmation_sent_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by = Column(String, ForeignKey("users.id"), nullable=True)

    meeting_provider = Column(String, nullable=True)   # zoom | meet | teams | phone | in_person
    meeting_url      = Column(String, nullable=True)
    location         = Column(String, nullable=True)
    # INTERNAL. Never sent to the prospect and never written into a provider
    # event body the prospect can read.
    notes            = Column(Text, nullable=True)

    # ── Prospect-facing invitation (Checkpoint 3) ───────────────────────────
    # THE DEMO CONFIRMATION, tracked like the invitation beside it. `count`
    # is what distinguishes a resend from a first send - a resend is the same
    # call again, so without a counter the two are indistinguishable in the
    # log.
    demo_confirmation_sent_at = Column(DateTime, nullable=True)
    demo_confirmation_error = Column(Text, nullable=True)
    demo_confirmation_count = Column(Integer, default=0, nullable=True)

    prospect_invite_sent_at = Column(DateTime, nullable=True)
    prospect_invite_error   = Column(Text, nullable=True)

    # ── Reschedule history ──────────────────────────────────────────────────
    # A reschedule MOVES this row rather than cancelling and recreating, so the
    # opportunity timeline, the confirmation token and the provider event ids
    # all survive. These record that it happened.
    rescheduled_count     = Column(Integer, default=0, nullable=False)
    rescheduled_at        = Column(DateTime, nullable=True)
    previous_starts_at    = Column(DateTime, nullable=True)
    reschedule_reason     = Column(Text, nullable=True)

    # ── OUTCOME — the authoritative record of what happened ─────────────────
    #
    # Written only by a human recording it, never by a clock. NULL is a real
    # and important state: "this meeting's time has passed and nobody has said
    # what happened yet", which is what the pending-outcome queue is built on.
    # A NULL here must never be read as "completed" or as "no-show"; the whole
    # point of these columns is that T9 no longer has to choose between two
    # wrong inferences.
    outcome             = Column(String, nullable=True)   # APPOINTMENT_OUTCOMES
    outcome_notes       = Column(Text, nullable=True)
    outcome_recorded_at = Column(DateTime, nullable=True)
    outcome_recorded_by = Column(String, ForeignKey("users.id"), nullable=True)

    # Denormalised from `outcome` through OUTCOMES_OCCURRED at the moment it is
    # recorded. Exists so "did this meeting happen?" is one indexed boolean
    # rather than a nine-value string every downstream report has to know how to
    # interpret — and so adding a tenth outcome later cannot silently change
    # what an existing report counted.
    occurred     = Column(Boolean, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Set when an outcome produced a follow-up meeting, so the chain is
    # traversable in both directions rather than inferred from timestamps.
    followup_appointment_id = Column(String, nullable=True)

    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    cancelled_at   = Column(DateTime, nullable=True)
    cancel_reason  = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_sales_appt_org_time", "brand_sales_org_id", "starts_at"),
        Index("ix_sales_appt_opportunity", "opportunity_id"),
        Index("ix_sales_appt_status_time", "status", "starts_at"),
        # The pending-outcome queue: meetings in the past with no verdict yet.
        # Indexed on the two columns that query actually filters on, because it
        # runs on every load of the manager's calendar.
        Index("ix_sales_appt_outcome_pending", "brand_sales_org_id", "outcome",
              "starts_at"),
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

    # ── External calendar sync (Checkpoint 3) ───────────────────────────────
    # Per PARTICIPANT, not per appointment: each attendee gets their own event
    # on their own calendar, under their own OAuth grant. One person's provider
    # failing must never affect anybody else's copy or the appointment itself.
    external_calendar_provider = Column(String, nullable=True)   # microsoft | google | ics
    external_event_id          = Column(String, nullable=True)   # the id retry keys on
    external_synced_at         = Column(DateTime, nullable=True) # last SUCCESS

    # sync_status is the single field the UI reads. `not_connected` is a normal
    # resting state, not an error — it routes to the .ics fallback.
    sync_status        = Column(String, default="not_connected", nullable=False)
    sync_attempts      = Column(Integer, default=0, nullable=False)
    sync_last_attempt  = Column(DateTime, nullable=True)
    sync_error         = Column(Text, nullable=True)   # message only, never a token
    ics_sent_at        = Column(DateTime, nullable=True)

    # ── DRIFT DETECTION — what we last pushed, and what we last saw ─────────
    #
    # Without these, a provider-side edit is invisible. The old behaviour was
    # asymmetric in a way that quietly lost information: a DELETED provider
    # event was recreated on the next update (fine — EvoSys is authoritative),
    # but a MOVED one was never noticed at all, so Outlook said Thursday, EvoSys
    # said Tuesday, and both were confident.
    #
    # `pushed_*` is the state WE last wrote. A later read that disagrees with it
    # is a genuine external edit rather than our own write echoing back, which
    # is the distinction that makes reconciliation possible at all — comparing a
    # provider event against the appointment's CURRENT time would flag our own
    # in-flight reschedule as a conflict.
    pushed_starts_at   = Column(DateTime, nullable=True)
    pushed_ends_at     = Column(DateTime, nullable=True)
    pushed_at          = Column(DateTime, nullable=True)
    # Provider's own version marker (Graph changeKey / Google etag), so an
    # unchanged event can be recognised without comparing every field.
    external_etag      = Column(String, nullable=True)
    external_last_seen_at = Column(DateTime, nullable=True)

    # A conflict the system refuses to resolve on its own. Set when a provider
    # event was changed outside EvoSys in a way that is not deterministically
    # reconcilable, and cleared only when somebody decides. Deliberately NOT
    # auto-healed: silently overwriting means whoever moved the meeting in
    # Outlook had their change destroyed without being told.
    sync_conflict        = Column(Boolean, default=False, nullable=False)
    sync_conflict_kind   = Column(String, nullable=True)   # CONFLICT_KINDS
    sync_conflict_detail = Column(Text, nullable=True)
    sync_conflict_at     = Column(DateTime, nullable=True)
    # What the provider said, kept so the review screen can show both sides
    # rather than asking a human to go and look.
    conflict_provider_starts_at = Column(DateTime, nullable=True)
    conflict_provider_ends_at   = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("appointment_id", "user_id", name="uq_participant_appt_user"),
        Index("ix_appt_participant_user_time", "user_id", "busy_start_at", "busy_end_at"),
        Index("ix_appt_participant_appt", "appointment_id"),
        # The conflict review queue. Partial-index semantics are not portable,
        # so this is a plain composite — the column is false for almost every
        # row, which keeps it small in practice.
        Index("ix_appt_participant_conflict", "sync_conflict", "sync_conflict_at"),
    )


# ── HOW A BOOKING ARRIVED ────────────────────────────────────────────────────
# NULL for every row booked from inside the product. See
# SalesAppointment.booking_source.
BOOKING_SOURCE_INTERNAL   = "internal"
BOOKING_SOURCE_PUBLIC_WEB = "public_web"
BOOKING_SOURCES = (BOOKING_SOURCE_INTERNAL, BOOKING_SOURCE_PUBLIC_WEB)


# ── Reminder lifecycle ───────────────────────────────────────────────────────
#
# WHY A TABLE AND NOT TWO BOOLEAN COLUMNS.
#
# `BookingLink` on the customer-tenant side does it with booleans -
# `reminder_24hr_sent`, `reminder_1hr_sent` - and that shape cannot answer the
# questions this needs to answer. It cannot say whether a reminder was
# deliberately skipped or simply has not fired yet; it cannot record WHY one
# failed; it cannot survive a reschedule, because a reminder sent for the old
# time has to be distinguishable from one owed for the new one; and adding a
# third reminder means another column on a table that is already wide.
#
# Most importantly, a boolean is set by the sender AFTER it sends. Two overlapping
# job runs both read False and both send. The unique constraint below makes the
# claim the atomic act: whichever run inserts the row owns that reminder, and the
# other one's INSERT fails at the database. That is the same guarantee, for the
# same reason, that ai_operations/idempotency.py is built on.

REMINDER_CONFIRMATION = "confirmation"   # sent once, at booking
REMINDER_24H          = "reminder_24h"
REMINDER_1H           = "reminder_1h"
REMINDER_KINDS = (REMINDER_CONFIRMATION, REMINDER_24H, REMINDER_1H)

# How far ahead of the meeting each one is owed. The confirmation is not on this
# clock - it belongs to the booking transaction, not to the scheduler.
REMINDER_LEAD_MINUTES = {
    REMINDER_24H: 24 * 60,
    REMINDER_1H:  60,
}

REMINDER_PENDING = "pending"   # claimed, not yet attempted
REMINDER_SENT    = "sent"
REMINDER_FAILED  = "failed"
# Deliberately not owed. A meeting booked ninety minutes out never had a
# 24-hour reminder to send, and recording that as "skipped" rather than leaving
# a gap is what stops a later reader concluding the job missed one.
REMINDER_SKIPPED   = "skipped"
REMINDER_SUPPRESSED = "suppressed"   # cancelled meeting, or too close to another message
REMINDER_STATUSES = (REMINDER_PENDING, REMINDER_SENT, REMINDER_FAILED,
                     REMINDER_SKIPPED, REMINDER_SUPPRESSED)

# Statuses that mean "this reminder is finished with"; a job must not retry one.
REMINDER_SETTLED = (REMINDER_SENT, REMINDER_SKIPPED, REMINDER_SUPPRESSED)


class AppointmentReminder(Base):
    """One row per (appointment, reminder kind). The row IS the claim.

    Created by the scheduler when a reminder becomes due, or at booking time for
    the ones already known to be unnecessary. Never deleted - a reschedule
    settles the old rows and the new time gets new ones, so the history reads as
    what actually happened rather than as the current plan.
    """
    __tablename__ = "sales_appointment_reminders"

    id = Column(String, primary_key=True, default=gen_uuid)
    appointment_id = Column(String, ForeignKey("sales_appointments.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    kind   = Column(String, nullable=False)   # REMINDER_KINDS
    status = Column(String, default=REMINDER_PENDING, nullable=False)

    # The meeting time this reminder was claimed AGAINST. A reschedule makes the
    # old row's target stale, which is how `due_reminders` knows to issue a new
    # one instead of treating the meeting as already reminded.
    target_starts_at = Column(DateTime, nullable=True)

    scheduled_for = Column(DateTime, nullable=True)   # when it was/is owed, UTC
    attempted_at  = Column(DateTime, nullable=True)
    sent_at       = Column(DateTime, nullable=True)
    recipient     = Column(String, nullable=True)     # prospect email at send time
    detail        = Column(Text, nullable=True)       # why skipped, or the error
    attempts      = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # THE GUARANTEE. Not a convenience index - this is what makes a second
        # job run lose the race instead of sending a second email.
        UniqueConstraint("appointment_id", "kind", "target_starts_at",
                         name="uq_appointment_reminder_kind_target"),
        Index("ix_appointment_reminder_due", "status", "scheduled_for"),
    )
