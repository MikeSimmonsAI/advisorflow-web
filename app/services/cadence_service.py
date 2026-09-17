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

import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
import os

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.models.models import (
    Lead, CadenceState, User, Reply, CadenceTouchLog
)

logger = logging.getLogger(__name__)

# Day offsets since cadence start - the actual 9-touch spec.
CADENCE_SCHEDULE_DAYS = [1, 3, 7, 10, 14, 21, 30, 45, 60]
TOTAL_TOUCHES = len(CADENCE_SCHEDULE_DAYS)

# Day 1 touch fires immediately rather than waiting 24 hours, since "Day 1"
# means "the day contact starts," not "wait a full day first."
from app.services.sms_content_policy import (
    enforce_sms_content_policy, LINKS_ALLOWED as SMS_LINKS_ALLOWED,
)

SEND_IMMEDIATELY_ON_DAY_1 = True

# One template per track, with {touch_number} driven tone variation handled
# via TOUCH_TONE_VARIANTS below rather than 9 separate hardcoded strings -
# keeps this maintainable and matches "rotating message variations to avoid
# carrier flagging" from Mike's original AHK-era requirement.
# CTIA-required opt-out. Every A2P sample message a campaign is approved on
# carries one; NOT ONE of these templates did, and a message that omits it
# reads to a carrier's content filter as unsolicited traffic - especially the
# ones carrying a link on a domain with no sending reputation. This is the
# minimum change the 30007 isolation pointed at: it does not touch the sender,
# the send path, the branding, the booking link or any Twilio/A2P setting.
OPT_OUT_SUFFIX = " Reply STOP to opt out."

# OPERATING RULE FOR THE CURRENT SAMPLE:
#   SMS   = conversation and follow-up. No URL, no phone number.
#   EMAIL = the scheduling link.
#
# These templates no longer reference {booking_link} or {advisor_cell} at all.
# Campaign CO3YNIF is registered has_embedded_links=false and
# has_embedded_phone=false, so a message containing either contradicts the
# registration and the carrier filters it as 30007. Instead of carrying the
# link, the SMS points the family at the email that does.
#
# Removing the placeholders here is only half of it - an org's custom template,
# a cadence touch's own message_template, an AI draft or a pasted composer
# message could each still introduce one. sms_content_policy.enforce_sms_content_policy
# is the guarantee; this is the copy that reads well without needing it.
TRACK_BASE_TEMPLATES = {
    "pre_need_lock_price": (
        "Hi {first_name}, this is {advisor_name} with {org_name}. {tone_phrase} "
        "I just emailed you about locking in today's pricing, along with times "
        "we could talk. Please take a look when you have a moment."
        + OPT_OUT_SUFFIX
    ),
    "at_need_support": (
        "Hi {first_name}, this is {advisor_name} with {org_name}. {tone_phrase} "
        "I'm here to help with any arrangements you need. I've sent you an email "
        "with scheduling options whenever you're ready."
        + OPT_OUT_SUFFIX
    ),
    "imminent_support": (
        "Hi {first_name}, this is {advisor_name} with {org_name}. {tone_phrase} "
        "I want to make sure you have support right now. Please reply here and "
        "I'll call you straight back, or check your email for a time to talk."
        + OPT_OUT_SUFFIX
    ),
    "upsell_existing": (
        "Hi {first_name}, this is {advisor_name} with {org_name}. {tone_phrase} "
        "We have options available for your family. I've emailed you the details "
        "and some times we could chat."
        + OPT_OUT_SUFFIX
    ),
    "new_inquiry_intro": (
        "Hi {first_name}, this is {advisor_name} with {org_name}. {tone_phrase} "
        "I'd love to help answer any questions, no pressure at all. Check your "
        "email for scheduling options, or just reply here."
        + OPT_OUT_SUFFIX
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
    if lead.status in ("dnc", "needs_tier_review", "replied", "booked", "hot"):
        return None
    if lead.is_duplicate:
        return None
    # PLAN CAPACITY HOLD. A cadence is a standing commitment to spend on this
    # lead nine more times; enrolling a held prospect would turn one webhook
    # arrival the plan does not cover into nine paid messages.
    from app.services.lead_capacity import is_held
    if is_held(lead):
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
        status="active",
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


def stop_cadence_for_lead(db: Session, lead_id: str, reason) -> None:
    """
    Stops the cadence for a lead.
    reason can be a CadenceStatus enum or a plain string — both handled safely.
    """
    state = db.query(CadenceState).filter(CadenceState.lead_id == lead_id).first()
    if not state or state.status != "active":
        return
    # CadenceState.status is plain VARCHAR — always store as string
    state.status = reason.value if hasattr(reason, "value") else str(reason)
    state.completed_at = datetime.now(timezone.utc)
    db.commit()


def _get_org_cadence_schedule(db, organization_id: str) -> list[dict]:
    """
    Returns the cadence schedule for an org.
    If the org has a default CadenceTemplate, use its touches.
    Otherwise fall back to the hardcoded CADENCE_SCHEDULE_DAYS.
    Each item: {day_offset, send_hour, channel, message_template, touch_number}
    """
    from app.models.models import CadenceTemplate
    template = db.query(CadenceTemplate).filter(
        CadenceTemplate.organization_id == organization_id,
        CadenceTemplate.is_default == True,
        CadenceTemplate.is_active == True,
    ).first()

    if template and template.touches:
        return [
            {
                "touch_number": t.touch_number,
                "day_offset": t.day_offset,
                "send_hour": t.send_hour,
                "channel": t.channel,
                "message_template": t.message_template,
            }
            for t in sorted(template.touches, key=lambda x: x.touch_number)
            if t.is_active
        ]

    # Fallback to hardcoded schedule
    return [
        {"touch_number": i + 1, "day_offset": day, "send_hour": 10, "channel": "sms", "message_template": None}
        for i, day in enumerate(CADENCE_SCHEDULE_DAYS)
    ]


def render_cadence_message(db: Session, lead: Lead, advisor: User, touch_number: int, booking_url: str, message_template: str = None) -> str:
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
    # If a cadence template touch provides a message, use it directly
    if message_template:
        org = db.query(__import__('app.models.models', fromlist=['Organization']).Organization).filter_by(id=lead.organization_id).first()
        org_name = org.name if org else "our organization"
        # A cadence touch supplies its own text, so it can carry a link the
        # base templates no longer do. The policy is the backstop.
        return enforce_sms_content_policy(
            message_template
            .replace("{first_name}", lead.first_name or "there")
            .replace("{advisor_name}", advisor.full_name or "your advisor")
            .replace("{org_name}", org_name)
            .replace("{booking_url}", booking_url)
            .replace("{booking_link}", booking_url)
        )

    from app.services.template_service import get_sms_template
    from app.models.models import Organization
    org = db.query(Organization).filter_by(id=lead.organization_id).first()
    org_name = (org.brand_name or org.name) if org else "our team"

    custom_template = get_sms_template(db, lead.organization_id, lead.message_track)
    template = custom_template or TRACK_BASE_TEMPLATES.get(lead.message_track, TRACK_BASE_TEMPLATES["pre_need_lock_price"])
    tone_phrase = TOUCH_TONE_VARIANTS.get(touch_number, TOUCH_TONE_VARIANTS[9])
    # `custom_template` comes from the org's template editor and may still
    # contain {booking_link} or {advisor_cell}, so the substitutions stay and
    # the policy removes whatever the campaign is not registered to send.
    return enforce_sms_content_policy(
        template
        .replace("{first_name}", lead.first_name or "there")
        .replace("{advisor_name}", advisor.full_name)
        .replace("{org_name}", org_name)
        .replace("{tone_phrase}", tone_phrase)
        .replace("{booking_link}", booking_url)
        .replace("{booking_url}", booking_url)
        .replace("{advisor_cell}", advisor.twilio_phone_number or "")
    )


# ── OUTCOMES ────────────────────────────────────────────────────────────────
#
# What happened to one attempt at one touch. Written to
# CadenceTouchLog.outcome, and deliberately a small closed set: an operator
# asking "why did this family not hear from us" needs an answer that is always
# one of these, never a blank.

OUTCOME_SENT = "sent"                # handed to the provider, Message row written
OUTCOME_FAILED = "failed"            # provider refused or raised; its words are kept
OUTCOME_BLOCKED = "blocked"          # compliance refused before any provider call
OUTCOME_SUPPRESSED = "suppressed"    # demo tenant, or a guard inside the sender
OUTCOME_SKIPPED = "skipped"          # temporary: capacity hold, no advisor, disabled
OUTCOME_STOPPED = "stopped"          # the cadence ended here: reply, DNC, booking
OUTCOME_ABANDONED = "abandoned"      # retries exhausted; the cadence moves on

CADENCE_OUTCOMES = (
    OUTCOME_SENT, OUTCOME_FAILED, OUTCOME_BLOCKED, OUTCOME_SUPPRESSED,
    OUTCOME_SKIPPED, OUTCOME_STOPPED, OUTCOME_ABANDONED,
)

# A touch that fails is retried on a backoff rather than silently swallowed or
# retried forever. Three is enough to ride out a provider blip and few enough
# that a permanently bad number does not occupy the runner every hour until the
# heat death of the universe.
MAX_ATTEMPTS_PER_TOUCH = 3
RETRY_BACKOFF = timedelta(hours=1)


def _sending_enabled() -> bool:
    """MAY THIS DEPLOYMENT PLACE A CADENCE SMS AT ALL?

    Default OFF, and that default is load-bearing rather than cautious.

    Until this rewrite the runner called `get_twilio_client(advisor, db)` and
    unpacked THREE values from it. That function returns a single Client, so
    every touch raised TypeError - after the counter had already been advanced
    and committed. The cadence has therefore never sent one message: it has
    walked leads from touch 1 to touch 9, marked them `sent`, written no
    `messages` row, and reported an error count nobody read.

    Repairing that makes the highest-volume sender in the product start working
    on the next deploy, against a population of leads whose due dates are
    months in the past. Turning that on is a decision about contacting real
    families, and it belongs to the owner, not to a bug fix. So the repair
    ships switched off and the switch is explicit.

    Per-customer control is the `cadences` feature entitlement that already
    exists and already gates the cadence router - checked below per lead, so
    enabling this deployment-wide still sends nothing for a customer who is not
    entitled to cadences.
    """
    raw = os.environ.get("CADENCE_SMS_SENDING")
    if raw is None:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _log_touch(db: Session, state, lead, *, touch_number, attempt_seq, outcome,
               reason=None, channel="sms", scheduled_for=None, attempted_at=None,
               message=None, body_preview=None, claim=False):
    """Write one CadenceTouchLog row.

    `claim=True` inserts inside a savepoint and returns None if the unique
    index refuses it, which is how a second runner learns it lost the race
    without a SELECT-then-INSERT gap to lose in. A failed INSERT poisons the
    surrounding transaction on Postgres, so the savepoint is not optional -
    same reasoning as app/services/ai_operations/idempotency.py.
    """
    row = CadenceTouchLog(
        cadence_state_id=state.id,
        lead_id=lead.id,
        organization_id=getattr(lead, "organization_id", None),
        touch_number=touch_number,
        attempt_seq=attempt_seq,
        channel=channel,
        outcome=outcome,
        reason=(str(reason)[:500] if reason else None),
        scheduled_for=scheduled_for,
        attempted_at=attempted_at,
        body_preview=(body_preview[:240] if body_preview else None),
    )
    if message is not None:
        row.message_id = getattr(message, "id", None)
        row.provider = "twilio"
        row.provider_message_id = getattr(message, "twilio_sid", None)
        row.provider_error_code = getattr(message, "error_code", None)
        row.provider_error_message = getattr(message, "error_message", None)
    if not claim:
        db.add(row)
        db.flush()
        return row
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        logger.info(
            "cadence: touch %s attempt %s for lead %s was already claimed by "
            "another runner", touch_number, attempt_seq, lead.id)
        return None


def _already_sent(db: Session, state, touch_number: int) -> bool:
    """Has this touch ALREADY gone out?

    The unique index stops two runners claiming the same ATTEMPT; it does not
    by itself stop a second runner claiming attempt 2 of a touch that attempt 1
    already delivered. This is the guard that makes the touch, not the attempt,
    the thing that happens once.

    It is checked before the claim and the claim is still inserted under the
    unique index, so two runners that both read "not sent" a microsecond apart
    still produce one insert and one send - the read is the fast path, the
    index is the guarantee.
    """
    return db.query(CadenceTouchLog.id).filter(
        CadenceTouchLog.cadence_state_id == state.id,
        CadenceTouchLog.touch_number == touch_number,
        CadenceTouchLog.outcome == OUTCOME_SENT,
    ).first() is not None


def _next_attempt_seq(db: Session, state, touch_number: int) -> int:
    """Attempts are counted from FAILURES only.

    A delivered touch is never retried - `_already_sent` refuses it outright -
    so the sequence exists to number the retries of a touch that has not landed
    yet, and MAX_ATTEMPTS_PER_TOUCH is a budget for provider failures.
    """
    used = (db.query(func.count(CadenceTouchLog.id))
            .filter(CadenceTouchLog.cadence_state_id == state.id,
                    CadenceTouchLog.touch_number == touch_number,
                    CadenceTouchLog.outcome == OUTCOME_FAILED)
            .scalar()) or 0
    return used + 1


def run_due_cadences(db: Session, organization_id: str = None) -> dict:
    """Send every touch that is due, and record what happened to each one.

    WHAT CHANGED, AND WHY EACH PART OF IT HAD TO

    The counter no longer moves before the send. It moved first, and committed,
    so a failure left `current_touch_number` claiming a message that does not
    exist - which was every single touch, because the provider call raised
    TypeError before it ever reached Twilio. Success state now follows a
    confirmed send, and a failure leaves the cadence where it was.

    The provider call goes through `sms_service.send_sms` instead of a
    hand-rolled Twilio client. That was the bug's home and it is also where
    four guards live that this path never had: the demo boundary, the
    suppression list, correct credential resolution, and a `messages` row
    carrying delivery state, the provider's error code and message,
    send_source and the status callback. The cadence had none of them.

    The compliance preflight runs before it, so a family on the suppression
    list whose Lead.status was never flipped to DNC is refused rather than
    texted nine times.

    Every step writes a CadenceTouchLog row - sent, failed, blocked,
    suppressed, skipped, stopped or abandoned. A skip used to be a bare
    `continue`, so a lead could be re-evaluated and re-skipped hourly forever
    with nothing recorded anywhere.

    The claim row is the concurrency lock. `with_for_update(skip_locked=True)`
    was documented as preventing double-sends and does not: its locks are
    released by the first commit inside the loop, and there are three commits
    per iteration and three concurrent triggers. A unique index on
    (cadence_state, touch, attempt) does hold.

    A failed touch is retried on a backoff up to MAX_ATTEMPTS_PER_TOUCH and
    then abandoned so the cadence continues rather than wedging.

    NOTHING SENDS unless this deployment has CADENCE_SMS_SENDING on AND the
    customer holds the `cadences` feature. See `_sending_enabled`.
    """
    from app.services.lead_capacity import is_held
    from app.services.compliance_service import check_compliance_preflight
    from app.services import entitlements
    from app.services import send_source as _src
    from app.services.sms_service import send_sms
    from app.models.models import Organization

    # Naive UTC, matching the columns. cadence_router already does this at its
    # one comparison; the runner used an aware value and wrote it into naive
    # DateTime columns, which Postgres silently truncates.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sending_on = _sending_enabled()

    query = (
        db.query(CadenceState)
        .filter(
            CadenceState.status == "active",
            CadenceState.next_touch_due_at <= now,
        )
    )
    if organization_id:
        query = query.join(Lead).filter(Lead.organization_id == organization_id)
    due_states = query.all()

    sent_count = 0
    completed_count = 0
    error_count = 0
    blocked_count = 0
    skipped_count = 0
    errors = []
    org_cache = {}

    for state in due_states:
        lead = state.lead
        if lead is None:
            continue

        scheduled_for = state.next_touch_due_at
        touch_number = state.current_touch_number + 1

        # ── STOPS. The cadence ends here, and the reason is recorded. ───────
        if lead.status in ("dnc", "hot", "replied", "booked"):
            reason = "stopped_dnc" if lead.status == "dnc" else "stopped_replied"
            _log_touch(db, state, lead, touch_number=touch_number, attempt_seq=1,
                       outcome=OUTCOME_STOPPED,
                       reason=f"lead status is {lead.status}",
                       scheduled_for=scheduled_for)
            stop_cadence_for_lead(db, lead.id, reason)
            continue

        has_reply = db.query(Reply).filter(Reply.lead_id == lead.id).first()
        if has_reply:
            _log_touch(db, state, lead, touch_number=touch_number, attempt_seq=1,
                       outcome=OUTCOME_STOPPED,
                       reason="the family replied", scheduled_for=scheduled_for)
            stop_cadence_for_lead(db, lead.id, "stopped_replied")
            continue

        # ── TEMPORARY SKIPS. Recorded, and the cadence resumes later. ───────
        #
        # A capacity hold is temporary, so the cadence is skipped rather than
        # torn down - but the skip is now visible instead of being a bare
        # `continue` that repeated silently every hour.
        if is_held(lead):
            _log_touch(db, state, lead, touch_number=touch_number,
                       attempt_seq=_next_attempt_seq(db, state, touch_number),
                       outcome=OUTCOME_SKIPPED,
                       reason="lead is held over plan capacity",
                       scheduled_for=scheduled_for)
            state.next_touch_due_at = now + RETRY_BACKOFF
            skipped_count += 1
            db.commit()
            continue

        advisor = lead.assigned_to
        if not advisor:
            _log_touch(db, state, lead, touch_number=touch_number,
                       attempt_seq=_next_attempt_seq(db, state, touch_number),
                       outcome=OUTCOME_SKIPPED, reason="no advisor assigned",
                       scheduled_for=scheduled_for)
            state.next_touch_due_at = now + RETRY_BACKOFF
            skipped_count += 1
            error_count += 1
            errors.append(f"Lead {lead.id}: no advisor assigned")
            db.commit()
            continue

        schedule = _get_org_cadence_schedule(db, lead.organization_id)
        total_touches = len(schedule)
        touch_def = next((t for t in schedule if t["touch_number"] == touch_number), None)
        if not touch_def:
            state.status = "completed"
            state.completed_at = now
            completed_count += 1
            db.commit()
            continue

        # ── ENTITLEMENT AND THE DEPLOYMENT SWITCH ──────────────────────────
        org = org_cache.get(lead.organization_id)
        if org is None and lead.organization_id:
            org = (db.query(Organization)
                   .filter(Organization.id == lead.organization_id).first())
            org_cache[lead.organization_id] = org

        if not sending_on or not entitlements.org_has_feature(org, "cadences"):
            reason = ("cadence SMS sending is disabled for this deployment"
                      if not sending_on
                      else "the organization is not entitled to cadences")
            _log_touch(db, state, lead, touch_number=touch_number,
                       attempt_seq=_next_attempt_seq(db, state, touch_number),
                       outcome=OUTCOME_SKIPPED, reason=reason,
                       scheduled_for=scheduled_for)
            state.next_touch_due_at = now + RETRY_BACKOFF
            skipped_count += 1
            db.commit()
            continue

        # ── COMPLIANCE, BEFORE ANY PROVIDER IS RESOLVED ────────────────────
        try:
            check_compliance_preflight(db, lead, channel="sms")
        except ValueError as blocked:
            _log_touch(db, state, lead, touch_number=touch_number,
                       attempt_seq=_next_attempt_seq(db, state, touch_number),
                       outcome=OUTCOME_BLOCKED, reason=str(blocked),
                       scheduled_for=scheduled_for, attempted_at=now)
            state.next_touch_due_at = now + RETRY_BACKOFF
            blocked_count += 1
            db.commit()
            continue

        # ── THE CLAIM. One row, one send. ──────────────────────────────────
        #
        # A touch that already delivered is never re-sent, whatever the state
        # counter says. That covers the crash-between-send-and-commit case as
        # well as the concurrent-runner one: the evidence that a family was
        # contacted is the log row, not the integer.
        if _already_sent(db, state, touch_number):
            logger.info(
                "cadence: touch %s for lead %s has already been delivered; "
                "leaving it to the runner that sent it", touch_number, lead.id)
            continue

        attempt_seq = _next_attempt_seq(db, state, touch_number)
        if attempt_seq > MAX_ATTEMPTS_PER_TOUCH:
            _log_touch(db, state, lead, touch_number=touch_number,
                       attempt_seq=attempt_seq, outcome=OUTCOME_ABANDONED,
                       reason=f"{MAX_ATTEMPTS_PER_TOUCH} attempts failed",
                       scheduled_for=scheduled_for)
            state.current_touch_number = touch_number
            _advance_schedule(state, schedule, touch_number, total_touches, now)
            if state.status == "completed":
                completed_count += 1
            db.commit()
            continue

        body = render_cadence_message(
            db, lead, advisor, touch_number, "",
            touch_def.get("message_template"))

        claim = _log_touch(db, state, lead, touch_number=touch_number,
                           attempt_seq=attempt_seq, outcome=OUTCOME_SENT,
                           scheduled_for=scheduled_for, attempted_at=now,
                           body_preview=body, claim=True)
        if claim is None:
            # Another runner owns this touch. Not an error.
            continue
        db.commit()

        try:
            message = send_sms(
                db=db, advisor=advisor, lead=lead, template=body,
                include_booking_link=False,
                send_source=_src.CADENCE,
                # A cron. No authenticated human, and NULL says so honestly.
                sent_by_user_id=None,
            )
        except Exception as exc:                                 # noqa: BLE001
            db.rollback()
            claim = db.query(CadenceTouchLog).filter(
                CadenceTouchLog.id == claim.id).first()
            if claim is not None:
                is_guard = exc.__class__.__name__ == "DemoBoundaryViolation"
                claim.outcome = OUTCOME_SUPPRESSED if is_guard else OUTCOME_FAILED
                claim.reason = str(exc)[:500]
            state.next_touch_due_at = now + RETRY_BACKOFF
            error_count += 1
            errors.append(f"Lead {lead.id}: {exc}")
            db.commit()
            continue

        # ── SUCCESS. Only now does anything advance. ───────────────────────
        claim.message_id = getattr(message, "id", None)
        claim.provider = "twilio"
        claim.provider_message_id = getattr(message, "twilio_sid", None)
        claim.provider_error_code = getattr(message, "error_code", None)
        claim.provider_error_message = getattr(message, "error_message", None)

        state.current_touch_number = touch_number
        state.last_touch_sent_at = now
        _advance_schedule(state, schedule, touch_number, total_touches, now)
        if state.status == "completed":
            completed_count += 1
        sent_count += 1
        db.commit()

    return {
        "evaluated": len(due_states),
        "sent": sent_count,
        "completed": completed_count,
        "errors": error_count,
        "blocked": blocked_count,
        "skipped": skipped_count,
        "sending_enabled": sending_on,
        "error_details": errors,
    }


def _advance_schedule(state, schedule, touch_number, total_touches, now):
    """Move the state to the next touch, or complete it. Success path only."""
    if touch_number >= total_touches:
        state.status = "completed"
        state.completed_at = now
        return
    next_def = next((t for t in schedule if t["touch_number"] == touch_number + 1), None)
    if not next_def:
        state.status = "completed"
        state.completed_at = now
        return
    base = state.cadence_started_at or now
    if getattr(base, "tzinfo", None) is not None:
        base = base.replace(tzinfo=None)
    state.next_touch_due_at = base + timedelta(days=next_def["day_offset"])


def get_cadence_history(db: Session, lead_id: str) -> list:
    """Every attempt at every touch for one lead, oldest first.

    This is the answer to "which steps completed, on what channel, when, and
    what happened to the rest" - a question that had no answer at all while the
    only state was one integer.
    """
    rows = (db.query(CadenceTouchLog)
            .filter(CadenceTouchLog.lead_id == lead_id)
            .order_by(CadenceTouchLog.touch_number.asc(),
                      CadenceTouchLog.attempt_seq.asc())
            .all())
    return [
        {
            "touch_number": r.touch_number,
            "attempt": r.attempt_seq,
            "channel": r.channel,
            "outcome": r.outcome,
            "reason": r.reason,
            "scheduled_for": r.scheduled_for,
            "attempted_at": r.attempted_at,
            "provider_message_id": r.provider_message_id,
            "provider_error_code": r.provider_error_code,
            "provider_error_message": r.provider_error_message,
            "message_id": r.message_id,
            "body_preview": r.body_preview,
        }
        for r in rows
    ]


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
    return {str(status): count for status, count in counts}
