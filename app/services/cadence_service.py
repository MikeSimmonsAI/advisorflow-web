"""
Re-engagement Cadence Engine
Implements Mike's 9-touch cadence over 60 days: Day 1, 3, 7, 10, 14, 21,
30, 45, 60 since the lead entered the cadence (matches the original LAP
spec carried over from the desktop app).

How it works:
  - When a lead is newly imported (status=new, not DNC, not duplicate,
    not needs_tier_review), start_cadence() creates a CadenceState row
    and schedules touch #1 for "now" (Day 1 = immediately, or next business
    day - see SEND_IMMEDIATELY_ON_DAY_1 below).
  - A scheduled job (run daily, e.g. via Render cron or a simple loop with
    a sleep) calls run_due_cadences() which finds every CadenceState where
    next_touch_due_at <= now and status=active, sends that lead's next
    touch, advances current_touch_number, and reschedules next_touch_due_at.
  - The cadence auto-stops the moment a lead replies, books, or is
    flagged DNC - see stop_cadence_for_lead(), which should be called from
    the inbound SMS webhook and the booking confirmation handler.
  - Each touch uses the lead's message_track to pick variation/tone, but
    Phase 2 ships one template set per track with touch-number variable
    substitution rather than 9 fully bespoke messages per track - refine
    later once real reply data shows what's working.
"""

from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models.models import (
    Lead, LeadStatus, CadenceState, CadenceStatus, MessageTrack, User
)
from app.services.sms_service import send_sms

# Day offsets since cadence start - the actual 9-touch spec.
CADENCE_SCHEDULE_DAYS = [1, 3, 7, 10, 14, 21, 30, 45, 60]
TOTAL_TOUCHES = len(CADENCE_SCHEDULE_DAYS)

# Day 1 touch fires immediately rather than waiting 24 hours, since "Day 1"
# means "the day contact starts," not "wait a full day first."
SEND_IMMEDIATELY_ON_DAY_1 = True

# One template per track, with {touch_number} driven tone variation handled
# via TOUCH_TONE_VARIANTS below rather than 9 separate hardcoded strings -
# keeps this maintainable and matches "rotating message variations to avoid
# carrier flagging" from Mike's original AHK-era requirement.
TRACK_BASE_TEMPLATES = {
    MessageTrack.PRE_NEED_LOCK_PRICE: (
        "Hi {first_name}, this is {advisor_name} with Restland. {tone_phrase} "
        "Lock in today's pricing before it changes - here's my booking link: {booking_link}"
    ),
    MessageTrack.AT_NEED_SUPPORT: (
        "Hi {first_name}, this is {advisor_name} with Restland. {tone_phrase} "
        "I'm here to help with any arrangements you need. Reach out anytime: {advisor_cell} "
        "or book a time here: {booking_link}"
    ),
    MessageTrack.IMMINENT_SUPPORT: (
        "Hi {first_name}, this is {advisor_name} with Restland. {tone_phrase} "
        "Please call me directly at {advisor_cell} - I want to make sure you have support right now."
    ),
    MessageTrack.UPSELL_EXISTING_CUSTOMER: (
        "Hi {first_name}, this is {advisor_name} with Restland. {tone_phrase} "
        "We have options for memorials, markers, and additional services for your family. "
        "Let's chat: {booking_link}"
    ),
    MessageTrack.NEW_INQUIRY_INTRO: (
        "Hi {first_name}, this is {advisor_name} with Restland. {tone_phrase} "
        "I help families with cemetery and funeral planning in the area - happy to "
        "answer any questions, no pressure at all. You can reach me at {advisor_cell} "
        "or grab a time here: {booking_link}"
    ),
}

# Light tone rotation per touch number so the message doesn't read identical
# every time (also helps avoid carrier spam flagging on repeated identical text).
TOUCH_TONE_VARIANTS = {
    1: "Wanted to reach out and introduce myself.",
    2: "Following up in case you missed my last message.",
    3: "Just checking in - no pressure, just want to make sure you have the info you need.",
    4: "Still here whenever you're ready to chat.",
    5: "Wanted to check back in with you.",
    6: "No rush at all - just keeping the door open.",
    7: "Reaching out one more time in case now's a better time.",
    8: "Last few times we've connected - wanted to try again.",
    9: "This will be my final check-in for now, but I'm always here if you need me.",
}


def start_cadence(db: Session, lead: Lead) -> CadenceState | None:
    """
    Begins the 9-touch cadence for a lead. Skips leads that shouldn't be
    in any active outreach cadence (DNC, duplicate, needs tier review,
    email-only - email has its own nurture flow, not this SMS cadence).
    """
    if lead.status in (LeadStatus.DNC, LeadStatus.NEEDS_TIER_REVIEW):
        return None
    if lead.is_duplicate:
        return None
    if lead.contact_channel == "email_only":
        return None  # email-only leads use the email nurture flow, not SMS cadence

    existing = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    if existing:
        return existing  # already in a cadence, don't restart

    now = datetime.now(timezone.utc)
    first_due = now if SEND_IMMEDIATELY_ON_DAY_1 else now + timedelta(days=CADENCE_SCHEDULE_DAYS[0])

    state = CadenceState(
        lead_id=lead.id,
        status=CadenceStatus.ACTIVE,
        current_touch_number=0,
        cadence_started_at=now,
        next_touch_due_at=first_due,
    )
    db.add(state)
    db.commit()

    # A lead actively entering the cadence is WARM by definition (alive,
    # being worked, no hot signal yet) - classify immediately rather than
    # waiting for the next periodic recompute job.
    from app.services.engagement_service import recompute_and_save
    try:
        recompute_and_save(db, lead)
    except Exception:
        pass

    return state


def stop_cadence_for_lead(db: Session, lead_id: str, reason: CadenceStatus) -> None:
    """
    Stops the cadence for a lead - call this from the inbound SMS webhook
    on any reply, from the booking confirmation handler, or when a lead
    is flagged DNC for any reason.
    """
    state = db.query(CadenceState).filter(CadenceState.lead_id == lead_id).first()
    if not state or state.status != CadenceStatus.ACTIVE:
        return
    state.status = reason
    state.completed_at = datetime.now(timezone.utc)
    db.commit()


def render_cadence_message(db: Session, lead: Lead, advisor: User, touch_number: int, booking_url: str) -> str:
    """
    Checks for an org-customized template first (see template_service.py);
    falls back to the hardcoded default if the org hasn't customized this
    track. This is what makes the template editor in Settings actually
    take effect on real sends, not just store text nobody reads.

    Exported (no longer a private _-prefixed helper) since the import
    review screen (admin_router.py's preview-message endpoint) needs the
    exact same resolution logic for touch 1 - reusing this function
    instead of duplicating the override/fallback logic guarantees the
    preview a user sees before confirming an import batch is genuinely
    the same text that would actually go out, not an approximation.
    """
    from app.services.template_service import get_sms_template
    custom_template = get_sms_template(db, lead.organization_id, lead.message_track)
    template = custom_template or TRACK_BASE_TEMPLATES.get(lead.message_track, TRACK_BASE_TEMPLATES[MessageTrack.PRE_NEED_LOCK_PRICE])
    tone_phrase = TOUCH_TONE_VARIANTS.get(touch_number, TOUCH_TONE_VARIANTS[9])
    return (
        template
        .replace("{first_name}", lead.first_name or "there")
        .replace("{advisor_name}", advisor.full_name)
        .replace("{tone_phrase}", tone_phrase)
        .replace("{booking_link}", booking_url)
        .replace("{advisor_cell}", advisor.twilio_phone_number or "")
    )


def run_due_cadences(db: Session, organization_id: str = None) -> dict:
    """
    Finds every active CadenceState whose next touch is due now, sends it,
    advances the state. Intended to be called once daily (e.g. via a
    Render cron job or background scheduler loop) - NOT per-request.

    Returns a summary of what was sent / skipped / completed / errored,
    for logging or an admin-facing "last cadence run" view.
    """
    now = datetime.now(timezone.utc)
    query = db.query(CadenceState).filter(
        CadenceState.status == CadenceStatus.ACTIVE,
        CadenceState.next_touch_due_at <= now,
    )

    due_states = query.all()
    sent_count = 0
    completed_count = 0
    error_count = 0
    errors = []

    for state in due_states:
        lead = state.lead
        if organization_id and lead.organization_id != organization_id:
            continue

        # Defensive re-check - a lead could have replied/been flagged DNC
        # between when it entered the queue and when this job runs.
        if lead.status in (LeadStatus.DNC, LeadStatus.HOT, LeadStatus.REPLIED, LeadStatus.BOOKED):
            stop_cadence_for_lead(
                db, lead.id,
                CadenceStatus.STOPPED_DNC if lead.status == LeadStatus.DNC else CadenceStatus.STOPPED_REPLIED,
            )
            continue

        advisor = lead.assigned_to
        if not advisor or not advisor.twilio_phone_number:
            error_count += 1
            errors.append(f"Lead {lead.id}: advisor has no Twilio number configured")
            continue

        touch_number = state.current_touch_number + 1

        try:
            from app.services.sms_service import create_booking_link
            booking = create_booking_link(db, lead, advisor)
            import os
            booking_url = f"{os.environ.get('BOOKING_BASE_URL', '')}/book/{booking.token}"
            body = render_cadence_message(db, lead, advisor, touch_number, booking_url)

            from app.services.sms_service import get_twilio_client
            client = get_twilio_client(advisor)
            twilio_msg = client.messages.create(body=body, from_=advisor.twilio_phone_number, to=lead.phone)

            from app.models.models import Message
            message = Message(
                lead_id=lead.id, sender_id=advisor.id, body=body,
                twilio_sid=twilio_msg.sid, twilio_status=twilio_msg.status,
                booking_link_id=booking.id,
            )
            db.add(message)

            state.current_touch_number = touch_number
            state.last_touch_sent_at = now

            if touch_number >= TOTAL_TOUCHES:
                state.status = CadenceStatus.COMPLETED
                state.completed_at = now
                completed_count += 1
            else:
                next_day_offset = CADENCE_SCHEDULE_DAYS[touch_number]  # next touch's day offset
                state.next_touch_due_at = state.cadence_started_at + timedelta(days=next_day_offset)

            lead.status = LeadStatus.SENT
            db.commit()
            sent_count += 1

        except Exception as e:
            error_count += 1
            errors.append(f"Lead {lead.id}: {str(e)}")
            db.rollback()

    return {
        "evaluated": len(due_states),
        "sent": sent_count,
        "completed": completed_count,
        "errors": error_count,
        "error_details": errors,
    }


def get_cadence_summary(db: Session, organization_id: str) -> dict:
    """Quick stats for an admin dashboard panel on cadence health."""
    from sqlalchemy import func as sa_func
    from app.models.models import Lead as LeadModel

    counts = (
        db.query(CadenceState.status, sa_func.count(CadenceState.id))
        .join(LeadModel, CadenceState.lead_id == LeadModel.id)
        .filter(LeadModel.organization_id == organization_id)
        .group_by(CadenceState.status)
        .all()
    )
    return {status.value: count for status, count in counts}
