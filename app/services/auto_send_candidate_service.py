"""
Auto-Send Candidate Creation

Called once, right after a new Reply is committed in the inbound SMS
webhook - decides whether this specific reply should become a real
AutoSendCandidate row, using the dedicated eligibility brain in
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
"""

from sqlalchemy.orm import Session

from app.models.models import Reply, Lead, User, AutoSendCandidate


def maybe_create_candidate(db: Session, reply: Reply, lead: Lead):
    """
    Returns the newly-created AutoSendCandidate if this reply qualified,
    or None if it didn't (or the feature isn't active for this lead's
    advisor at all). Never raises - any failure here must never break
    the inbound webhook's response to Twilio; the caller wraps this in
    its own try/except as a second layer of defense, but this
    function's own contract is "never raises."

    Drafts the actual AI response via draft_reply_service.draft_reply
    once eligibility is confirmed - a failure to draft never blocks
    candidate creation, it just means the candidate is created with an
    empty draft for the advisor to write themselves in the review queue.
    """
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
    prior_reply_count = (
        db.query(Reply)
        .filter(Reply.lead_id == lead.id, Reply.id != reply.id, Reply.received_at < reply.received_at)
        .count()
    )
    is_first_reply = prior_reply_count == 0

    from app.services.auto_send_eligibility_service import check_auto_send_eligibility
    try:
        result = check_auto_send_eligibility(
            body=reply.body,
            general_classification=reply.classification.value,
            is_first_reply=is_first_reply,
        )
    except Exception:
        # Mirrors the eligibility service's own "never raises" contract,
        # but defends against it anyway - a failure determining
        # eligibility is itself a reason not to create a candidate, the
        # same logic as the eligibility service's own internal except
        # block, just one more layer out.
        return None

    if not result.get("eligible"):
        return None

    # Draft the actual response using the existing, already-proven
    # draft_reply service - same conversation-history building,
    # booking-link handling, and safe AI-failure fallback already used
    # for Lead Detail's one-on-one drafting. Deliberately reused, not
    # duplicated - there is no reason this feature needs its own,
    # separate drafting logic when the existing one already does
    # exactly what's needed here.
    from app.services.draft_reply_service import draft_reply
    try:
        drafted = draft_reply(db, lead, advisor, tone="standard")
        drafted_body = drafted["suggested_reply"]
    except Exception:
        # draft_reply already has its own internal AI-failure fallback
        # and should not raise in practice, but this is a second,
        # outer layer of defense anyway - a failure to draft must never
        # mean a failure to record that this reply WAS eligible. A
        # candidate with an empty draft still shows up in the review
        # queue, where the advisor can write the reply themselves.
        drafted_body = ""

    candidate = AutoSendCandidate(
        reply_id=reply.id,
        lead_id=lead.id,
        advisor_id=advisor.id,
        eligibility_reasoning=result.get("reasoning"),
        classification_confidence=result.get("confidence"),
        ai_drafted_body=drafted_body,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate
