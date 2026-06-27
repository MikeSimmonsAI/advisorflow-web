"""
Certified Appointment Pipeline

Per Mike's explicit, direct definition - not a vague "hot lead" score,
a concrete, auditable sequence of real events that must each genuinely
have happened, in order:

    Solicited -> Contacted -> Booked -> Confirmed -> Waiting

His own words, verbatim: "certified means that we've already
solicited. We had to contact them. They booked the appointment. We
confirmed. Now we're just waiting for them to come in."

DESIGN PRINCIPLE - this is verification, not scoring. There is no AI
judgment call, no "seems serious" heuristic, no weighted score. Each
step is a real, checkable fact: did a message get sent, did a reply
come back, does a real booked appointment exist, was confirmation
explicitly recorded. "Certified" is binary and earned strictly in
order - skipping a step (e.g. a booking with no prior reply on record)
does not count, since the whole point of certification is that it can
be trusted and audited, not just inferred from a vibe.

INDUSTRY-AGNOSTIC BY DESIGN: this 5-step pipeline is the universal
layer Mike wants this app to be built around, independent of whatever
industry-specific vocabulary sits on top of it (Pre-Need vs. a roofing
quote stage, etc.) - none of the five steps below reference funeral-
specific language at all, deliberately.

QUALIFICATION - NOT BUILT, DESIGNED FOR: Mike was explicit that some
industries (e.g. land sales) need an extra "is this buyer qualified"
gate that funeral/most simple businesses don't. Rather than build an
unused, speculative toggle system now, this module exposes exactly one
deliberate seam for that to slot into later without reworking
anything else - see the `is_qualification_required` parameter on
get_certification_status below, which today always defaults to "not
required" and is never wired up to anything real. When a real
qualification feature is built, it plugs in here.
"""

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.models import Lead, Message, EmailMessage, Reply, BookingLink


STEP_SOLICITED = "solicited"
STEP_CONTACTED = "contacted"
STEP_BOOKED = "booked"
STEP_CONFIRMED = "confirmed"
STEP_WAITING = "waiting"

ALL_STEPS = [STEP_SOLICITED, STEP_CONTACTED, STEP_BOOKED, STEP_CONFIRMED, STEP_WAITING]


def get_certification_status(db: Session, lead: Lead, is_qualification_required: bool = False) -> dict:
    """
    Returns the lead's current position in the certified-appointment
    pipeline, checking each real, underlying fact directly - never
    inferred from LeadStatus alone, since LeadStatus drives other,
    unrelated existing logic (cadence eligibility, the work queue) and
    deliberately was not touched or repurposed for this feature (see
    BookingLink.confirmed_at's comment in models.py for why).

    Returns:
        {
            "current_step": one of ALL_STEPS or None (nothing solicited yet),
            "is_certified": bool - True only once current_step == "waiting",
            "steps_completed": {step_name: bool, ...} - every step's real status,
            "booking_link_id": str | None - the relevant booking, if one exists,
        }

    is_qualification_required is accepted but not yet acted on - see
    module docstring. Always pass False until a real qualification
    feature exists; this parameter exists so callers don't need to
    change their call signature when that feature is eventually built.
    """
    has_solicited = (
        db.query(Message).filter(Message.lead_id == lead.id).first() is not None
        or db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).first() is not None
    )

    has_contacted = db.query(Reply).filter(Reply.lead_id == lead.id).first() is not None

    booking = (
        db.query(BookingLink)
        .filter(BookingLink.lead_id == lead.id, BookingLink.status == "booked")
        .order_by(BookingLink.booked_time.desc())
        .first()
    )
    has_booked = booking is not None

    has_confirmed = bool(booking and booking.confirmed_at is not None)

    steps_completed = {
        STEP_SOLICITED: has_solicited,
        STEP_CONTACTED: has_contacted,
        STEP_BOOKED: has_booked,
        STEP_CONFIRMED: has_confirmed,
    }

    if not has_solicited:
        current_step = None
    elif not has_contacted:
        current_step = STEP_SOLICITED
    elif not has_booked:
        current_step = STEP_CONTACTED
    elif not has_confirmed:
        current_step = STEP_BOOKED
    else:
        current_step = STEP_WAITING

    is_certified = current_step == STEP_WAITING

    return {
        "current_step": current_step,
        "is_certified": is_certified,
        "steps_completed": steps_completed,
        "booking_link_id": booking.id if booking else None,
    }


def confirm_appointment(db: Session, booking_link: BookingLink) -> BookingLink:
    """
    Marks a booked appointment as confirmed - the deliberate, separate
    action Mike described: "we confirm: if they say yes, I'm still
    good, that's confirmed." Not automatic, not inferred from booking
    alone - this function is the explicit act of recording that a real
    confirmation happened.

    Idempotent - confirming an already-confirmed booking just leaves
    confirmed_at as its original timestamp rather than overwriting it,
    so the record reflects when confirmation FIRST happened, not the
    last time someone clicked confirm again.
    """
    if booking_link.confirmed_at is None:
        booking_link.confirmed_at = datetime.now(timezone.utc)
        db.commit()
    return booking_link


def get_certification_status_batch(db: Session, lead_ids: list[str]) -> dict[str, dict]:
    """
    Same per-step facts as get_certification_status, but for MANY leads
    in a small, fixed number of queries instead of one
    get_certification_status() call per lead.

    Built specifically for the Replies action center: a page of 200
    replies might reference only 30-50 distinct leads (several replies
    often belong to the same lead), and naively calling
    get_certification_status() once per REPLY would mean up to 600
    queries (3 per lead x 200 replies) on a single page load, much of
    it duplicate work re-checking the same lead repeatedly. This
    function takes the deduplicated list of lead_ids actually needed
    and runs exactly 3 queries total, regardless of how many leads or
    replies are involved.

    Returns {lead_id: same dict shape as get_certification_status, ...}
    for every lead_id passed in - leads with no activity at all still
    get a real entry (current_step=None), not a missing key.
    """
    if not lead_ids:
        return {}

    solicited_lead_ids = {
        row[0] for row in db.query(Message.lead_id).filter(Message.lead_id.in_(lead_ids)).distinct().all()
    } | {
        row[0] for row in db.query(EmailMessage.lead_id).filter(EmailMessage.lead_id.in_(lead_ids)).distinct().all()
    }

    contacted_lead_ids = {
        row[0] for row in db.query(Reply.lead_id).filter(Reply.lead_id.in_(lead_ids)).distinct().all()
    }

    # Most recent BOOKED link per lead, if any - same "most recent wins"
    # rule as the single-lead version, just computed for every lead at
    # once instead of one query per lead.
    bookings_by_lead: dict[str, BookingLink] = {}
    all_bookings = (
        db.query(BookingLink)
        .filter(BookingLink.lead_id.in_(lead_ids), BookingLink.status == "booked")
        .order_by(BookingLink.booked_time.desc())
        .all()
    )
    for booking in all_bookings:
        if booking.lead_id not in bookings_by_lead:
            bookings_by_lead[booking.lead_id] = booking

    results = {}
    for lead_id in lead_ids:
        has_solicited = lead_id in solicited_lead_ids
        has_contacted = lead_id in contacted_lead_ids
        booking = bookings_by_lead.get(lead_id)
        has_booked = booking is not None
        has_confirmed = bool(booking and booking.confirmed_at is not None)

        if not has_solicited:
            current_step = None
        elif not has_contacted:
            current_step = STEP_SOLICITED
        elif not has_booked:
            current_step = STEP_CONTACTED
        elif not has_confirmed:
            current_step = STEP_BOOKED
        else:
            current_step = STEP_WAITING

        results[lead_id] = {
            "current_step": current_step,
            "is_certified": current_step == STEP_WAITING,
            "steps_completed": {
                STEP_SOLICITED: has_solicited,
                STEP_CONTACTED: has_contacted,
                STEP_BOOKED: has_booked,
                STEP_CONFIRMED: has_confirmed,
            },
            "booking_link_id": booking.id if booking else None,
        }

    return results
