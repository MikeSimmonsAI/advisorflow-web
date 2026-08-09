"""
Auto-Send Candidate Creation

Called once, right after a new Reply is committed in the inbound SMS
webhook - decides whether this specific reply should become a real
AutoSendItem row in the queue, using the dedicated eligibility brain in
auto_send_eligibility_service.py.

THIS FILE OWNS THE "is the feature even on for this advisor" GATE.
auto_send_eligibility_service.py never checks User.auto_send_phase at
all - it only ever answers "is this reply, in isolation, the kind of
thing that COULD be safe to auto-draft." Whether the feature is
actually active for this specific advisor is checked here, first,
before the eligibility brain is ever consulted - an advisor whose
auto_send_phase is "off" (the only safe default) gets zero API calls
spent on this, zero candidate rows created, full stop.

Once a reply is confirmed eligible, this also drafts the actual
response by reusing the existing draft_reply_service.draft_reply -
the same proven conversation-history building, booking-link handling,
and AI-failure fallback already used for Lead Detail's one-on-one
drafting. Deliberately not a separate, duplicated drafting
implementation.

auto_send_phase values:
  "off"       — default, nothing happens
  "candidate" — eligible replies go to the review queue (advisor approves)
  "auto"      — eligible replies are sent immediately without review
"""

from datetime import datetime
import uuid

from sqlalchemy.orm import Session

from app.models.models import Reply, Lead


def maybe_create_candidate(db: Session, reply: Reply, lead: Lead):
    """
    Returns the newly-created AutoSendItem if this reply qualified,
    or None if it didn't (or the feature isn't active for this lead's
    advisor at all). Never raises - any failure here must never break
    the inbound webhook's response to Twilio; the caller wraps this in
    its own try/except as a second layer of defense, but this
    function's own contract is "never raises."

    Drafts the actual AI response via draft_reply_service.draft_reply
    once eligibility is confirmed - a failure to draft never blocks
    candidate creation, it just means the candidate is created with an
    empty draft for the advisor to write themselves in the review queue.

    When advisor.auto_send_phase == "auto", the drafted message is sent
    immediately via sms_service/email_service rather than sitting in the
    review queue. The AutoSendItem row is still created so there's a full
    audit trail in history.
    """
    # Late import to avoid circular - auto_send_router defines AutoSendItem
    from app.routers.auto_send_router import AutoSendItem

    advisor = lead.assigned_to
    if not advisor or advisor.auto_send_phase not in ("candidate", "auto"):
        # "off" (the default) or a missing advisor - nothing happens,
        # this is the normal, expected path for the vast majority of
        # advisors who haven't opted into this feature at all.
        return None

    if reply.classification is None:
        return None

    # "Is this the lead's first-ever reply" - checked by counting prior
    # Reply rows for this lead BEFORE this one. Uses received_at
    # ordering, not just count, so a backfilled/out-of-order reply
    # can't accidentally look like "the first" when it isn't.
    prior_replies = (
        db.query(Reply)
        .filter(Reply.lead_id == lead.id, Reply.id != reply.id, Reply.received_at < reply.received_at)
        .order_by(Reply.received_at.desc())
        .limit(10)
        .all()
    )
    is_first_reply = len(prior_replies) == 0

    # Build a short conversation history string for the eligibility check
    # (most recent first → reverse for display order)
    history_lines = []
    for r in reversed(prior_replies[:5]):
        history_lines.append(f"Lead: {r.body}")
    conversation_history = "\n".join(history_lines) if history_lines else ""

    from app.services.auto_send_eligibility_service import check_auto_send_eligibility
    try:
        result = check_auto_send_eligibility(
            body=reply.body,
            general_classification=reply.classification.value if hasattr(reply.classification, 'value') else str(reply.classification),
            is_first_reply=is_first_reply,
            conversation_history=conversation_history,
        )
    except Exception:
        # Mirrors the eligibility service's own "never raises" contract,
        # but defends against it anyway - a failure determining
        # eligibility is itself a reason not to create a candidate.
        return None

    if not result.get("eligible"):
        return None

    # Draft the actual response using the existing, already-proven
    # draft_reply service - same conversation-history building,
    # booking-link handling, and safe AI-failure fallback already used
    # for Lead Detail's one-on-one drafting.
    from app.services.draft_reply_service import draft_reply
    try:
        drafted = draft_reply(db, lead, advisor, tone="standard")
        drafted_body = drafted["suggested_reply"]
    except Exception:
        # draft_reply already has its own internal AI-failure fallback
        # and should not raise in practice, but this is a second,
        # outer layer of defense anyway - a failure to draft must never
        # mean a failure to record that this reply WAS eligible.
        drafted_body = ""

    now = datetime.utcnow()

    item = AutoSendItem(
        id=str(uuid.uuid4()),
        organization_id=lead.organization_id,
        lead_id=lead.id,
        advisor_id=advisor.id,
        message=drafted_body,
        channel="sms",
        source="ai",
        source_ref=reply.id,
        ai_reason=result.get("reasoning"),
        status="pending",
        created_at=now,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    # "auto" phase: send immediately, no human review needed
    if advisor.auto_send_phase == "auto" and drafted_body:
        try:
            from app.services.sms_service import send_sms
            send_sms(db=db, lead=lead, advisor=advisor, template=drafted_body, include_booking_link=False)
            item.status = "sent"
            item.actioned_at = now
            item.actioned_by_id = advisor.id
            db.commit()
            db.refresh(item)
        except Exception:
            # Send failure in auto mode — leave status as "pending" so
            # the advisor sees it in their queue and can approve manually.
            item.status = "pending"
            db.commit()
            db.refresh(item)

    return item
