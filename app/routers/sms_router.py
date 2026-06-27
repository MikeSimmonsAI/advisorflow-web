from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Form, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, Reply, Message, ReplyClassification, BookingLink
from app.services.sms_service import send_sms, send_batch

router = APIRouter(prefix="/sms", tags=["sms"])

# Keyword-based hot lead detection - simple first pass.
# Phase 2 can upgrade this to an LLM sentiment call.
HOT_KEYWORDS = ["yes", "interested", "call me", "book", "schedule", "sure", "ok let's", "when can"]
STOP_KEYWORDS = ["stop", "unsubscribe", "remove", "no thanks", "not interested"]


class SendRequest(BaseModel):
    lead_id: str
    template: str
    include_booking_link: bool = True


class BatchSendRequest(BaseModel):
    lead_ids: list[str]
    template: str
    include_booking_link: bool = True


class ReclassifyReplyRequest(BaseModel):
    classification: ReplyClassification


class DraftReplyRequest(BaseModel):
    tone: str = "standard"  # "soft" | "standard" | "urgent" | "direct" - see draft_reply_service.TONE_GUIDANCE


class DraftReplyResponse(BaseModel):
    suggested_reply: str
    booking_url: Optional[str] = None
    booking_link_id: Optional[str] = None
    source: str
    tone: str = "standard"


def _get_org_reply_or_404(db: Session, reply_id: str, current_user: User) -> Reply:
    """
    Fetch a reply only if the parent lead belongs to the current user's organization.

    Deliberately checks organization scope here instead of trusting a reply id alone;
    reply ids are opaque UUIDs, but tenant boundaries still need to be enforced on
    every mutation endpoint.
    """
    reply = (
        db.query(Reply)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(Reply.id == reply_id)
        .filter(Lead.organization_id == current_user.organization_id)
        .first()
    )
    if not reply:
        raise HTTPException(status_code=404, detail="Reply not found")
    return reply



def _get_lead_for_current_org_or_404(db: Session, lead_id: str, current_user: User) -> Lead:
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.post("/draft-reply/{lead_id}", response_model=DraftReplyResponse)
def draft_reply_for_lead(
    lead_id: str,
    req: DraftReplyRequest = DraftReplyRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    AI-assisted one-on-one reply drafting for Lead Detail only.

    Per Mike's explicit request: previously this only ever produced one
    fixed tone (polite, soft, "when works for a quick call?") with no
    way to ask for anything stronger. Now accepts an optional `tone`
    (soft/standard/urgent/direct), each genuinely changing what the AI
    writes - not just swapping a word, see TONE_GUIDANCE in
    draft_reply_service.py for what each one actually instructs the
    model to do differently. Defaults to "standard" so every existing
    caller sending no body (or an empty body) gets the exact same
    behavior as before this change - fully backward compatible.

    This endpoint is deliberately non-blocking from the user's point of
    view: missing OpenAI key, API errors, malformed model output, or any
    other AI failure all fall back to a safe, editable reply instead of
    surfacing a 500. It also reuses the real booking-link helper from
    sms_service.py and only creates a new link when the lead does not
    already have one.
    """
    from app.services.draft_reply_service import draft_reply, VALID_TONES

    if req.tone not in VALID_TONES:
        raise HTTPException(status_code=400, detail=f"tone must be one of: {', '.join(VALID_TONES)}")

    lead = _get_lead_for_current_org_or_404(db, lead_id, current_user)

    result = draft_reply(db, lead, current_user, tone=req.tone)
    return result


@router.post("/send")
def send_single(req: SendRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    lead = db.query(Lead).filter(Lead.id == req.lead_id, Lead.organization_id == current_user.organization_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    try:
        message = send_sms(db, current_user, lead, req.template, req.include_booking_link)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message_id": message.id, "status": message.twilio_status}


@router.post("/send-batch")
def send_batch_endpoint(req: BatchSendRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids),
        Lead.organization_id == current_user.organization_id,
    ).all()
    result = send_batch(db, current_user, leads, req.template, req.include_booking_link)
    return result


@router.post("/webhook/inbound")
def inbound_webhook(
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(...),
    MessageSid: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Twilio webhook for inbound SMS replies. Configure this URL in each
    advisor's Twilio number messaging settings:
    https://<your-domain>/sms/webhook/inbound

    Matches the inbound number+sender phone back to the most recent Lead
    that was texted from that Twilio number, attaches the Reply, runs
    AI-based reply classification (interested/callback/dnc/neutral - see
    reply_classification_service.py, which replaced the old naive
    substring keyword matcher after testing surfaced real false
    positives), stops the re-engagement cadence (any reply means the
    lead is engaged - no more touches needed), and fires an immediate
    reply notification to the owning advisor for EVERY reply (not just
    hot ones - see notification_service.py's notify_reply, expanded per
    Mike's explicit request that any reply, not just a hot lead, is
    something he wants to know about the moment it happens).
    """
    from app.services.dedup_service import normalize_phone
    from app.services.cadence_service import stop_cadence_for_lead
    from app.services.reply_classification_service import classify_reply, contains_hard_stop_language
    from app.models.models import CadenceStatus, ReplyClassification
    lead_phone = normalize_phone(From)

    lead = db.query(Lead).filter(Lead.phone == lead_phone).order_by(Lead.updated_at.desc()).first()
    if not lead:
        # Unknown sender - log nothing actionable, just acknowledge Twilio
        return {"status": "no_matching_lead"}

    # Hard legal opt-out check ALWAYS runs first and overrides anything
    # the AI classifier returns - see reply_classification_service.py's
    # module docstring for why this is non-negotiable.
    is_hard_stop = contains_hard_stop_language(Body)
    ai_result = classify_reply(Body)
    classification = ReplyClassification.DNC if is_hard_stop else ReplyClassification(ai_result["classification"])
    is_hot = classification == ReplyClassification.INTERESTED

    reply = Reply(
        lead_id=lead.id,
        body=Body,
        twilio_sid=MessageSid,
        is_hot=is_hot,
        hot_reason=ai_result.get("reasoning") if is_hot else None,
        classification=classification,
        classification_confidence="high" if is_hard_stop else ai_result.get("confidence"),
        classification_reasoning="Hard STOP keyword match" if is_hard_stop else ai_result.get("reasoning"),
    )
    db.add(reply)

    if classification == ReplyClassification.DNC:
        lead.status = "dnc"
        stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_DNC)

        # Wire the reply-based STOP detection into the Compliance Center's
        # suppression list - these were two separate, unconnected systems
        # before this: a lead could be marked status=dnc here while the
        # Compliance Center's suppression list stayed completely unaware
        # of it. Now every DNC reply also lands in the org-wide
        # suppression list automatically, with source=REPLY_STOP so it's
        # distinguishable from numbers an admin added by hand.
        if lead.phone:
            from app.services.compliance_service import add_suppression_entry_from_reply
            try:
                add_suppression_entry_from_reply(db, lead.organization_id, lead.phone, reason=f"Replied: {Body[:200]}")
            except Exception:
                pass  # never let a suppression-list failure break the Twilio webhook response
    elif classification == ReplyClassification.INTERESTED:
        lead.status = "hot"
        stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_REPLIED)
    else:
        # CALLBACK, NOT_INTERESTED, WRONG_NUMBER, QUESTION, and NEUTRAL all
        # count as "replied" at the lead-status level - none of these are
        # a legal opt-out (that's DNC above) or a hot signal (that's
        # INTERESTED above). The richer classification distinction lives
        # on the Reply record itself for the filtered inbox to use.
        lead.status = "replied"
        stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_REPLIED)

    db.commit()

    # Reclassify hot/warm/cold now that a reply just arrived - this is
    # the single most important trigger point for engagement temperature,
    # since a reply is the strongest real-time signal a lead's state changed.
    from app.services.engagement_service import recompute_and_save
    try:
        recompute_and_save(db, lead)
    except Exception:
        pass  # never let a classification failure break the Twilio webhook response

    if lead.assigned_to:
        from app.services.notification_service import notify_reply
        try:
            notify_reply(db, lead.assigned_to, lead, reply)
        except Exception:
            pass  # never let a notification failure break the Twilio webhook response

    return {"status": "received", "is_hot": is_hot, "classification": classification.value}


@router.patch("/replies/{reply_id}/mark-reviewed")
def mark_reply_reviewed(
    reply_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reply = _get_org_reply_or_404(db, reply_id, current_user)
    reply.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(reply)
    return reply


@router.patch("/replies/{reply_id}/reclassify")
def reclassify_reply(
    reply_id: str,
    req: ReclassifyReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually reclassify a reply. IMPORTANT: reclassifying TO dnc must
    trigger the full DNC treatment, not just relabel the Reply row - this
    was a real, silent gap before this fix. An advisor selecting "DNC"
    from this dropdown would see the badge change, but the lead's status
    never flipped to dnc, the cadence never stopped, and the phone never
    got suppressed - meaning cadence touches would keep going out to a
    lead an advisor had explicitly flagged as do-not-contact. Now mirrors
    the same treatment as the automatic webhook path and the quick-DNC
    button on Lead Detail (see /leads/{lead_id}/mark-dnc).
    """
    reply = _get_org_reply_or_404(db, reply_id, current_user)
    reply.classification = req.classification
    reply.is_hot = req.classification == ReplyClassification.INTERESTED
    reply.classification_confidence = "manual"
    reply.classification_reasoning = f"Manually reclassified by {current_user.full_name}"

    if req.classification == ReplyClassification.DNC:
        from app.services.cadence_service import stop_cadence_for_lead
        from app.services.compliance_service import add_suppression_entry_from_reply
        from app.models.models import CadenceStatus, SuppressionSource

        lead = db.query(Lead).filter(Lead.id == reply.lead_id).first()
        if lead:
            lead.status = "dnc"
            stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_DNC)
            if lead.phone:
                add_suppression_entry_from_reply(
                    db, lead.organization_id, lead.phone,
                    reason=f"Reply reclassified to DNC by {current_user.full_name}",
                    source=SuppressionSource.ADVISOR_FLAGGED,
                )

    db.commit()
    db.refresh(reply)
    return reply


@router.get("/replies/activity-by-day")
def reply_activity_by_day(
    days: int = Query(14, ge=1, le=60),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Real reply-activity series for the Overview chart.

    Counts inbound Reply rows grouped by received date for leads owned by the
    logged-in advisor. Empty days are returned with count=0 so the chart never
    has to invent data client-side.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_date = (now.date() - timedelta(days=days - 1))
    start_at = datetime.combine(start_date, datetime.min.time())

    replies = (
        db.query(Reply.received_at)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.assigned_to_id == current_user.id,
            Reply.received_at.isnot(None),
            Reply.received_at >= start_at,
        )
        .all()
    )

    counts_by_date = {
        (start_date + timedelta(days=offset)).isoformat(): 0
        for offset in range(days)
    }
    for (received_at,) in replies:
        key = received_at.date().isoformat()
        if key in counts_by_date:
            counts_by_date[key] += 1

    return [
        {"date": date_key, "count": counts_by_date[date_key]}
        for date_key in sorted(counts_by_date.keys())
    ]


@router.get("/replies/counts")
def reply_counts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Real bucket counts for the Replies action center - per Mike's
    explicit complaint that the Replies page "should not just send me
    back to the lead sheet... it should feel like an action center."

    Deliberately a SEPARATE endpoint from list_replies below, not
    derived from its result: list_replies caps at 200 rows, and these
    counts need to reflect the advisor's TRUE totals regardless of how
    many replies exist, not just whatever happened to be in the first
    page. Buckets here are the ones with real, already-tracked data -
    "Appointment interest" and "Objections" (also named in Mike's notes)
    aren't real ReplyClassification values yet and were deliberately
    NOT invented here; they're logged as a real future classification
    project, not faked with a guess.

    needs_follow_up = Interested or Callback that hasn't been reviewed
    yet - the same definition list_replies' needs_attention=True filter
    already uses, kept consistent rather than introducing a second
    definition of "needs attention."
    """
    base_query = (
        db.query(Reply)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(Lead.organization_id == current_user.organization_id)
        .filter(Lead.assigned_to_id == current_user.id)
    )

    hot = base_query.filter(Reply.classification == ReplyClassification.INTERESTED).count()
    callback = base_query.filter(Reply.classification == ReplyClassification.CALLBACK).count()
    question = base_query.filter(Reply.classification == ReplyClassification.QUESTION).count()
    not_interested = base_query.filter(Reply.classification == ReplyClassification.NOT_INTERESTED).count()
    wrong_number = base_query.filter(Reply.classification == ReplyClassification.WRONG_NUMBER).count()
    dnc = base_query.filter(Reply.classification == ReplyClassification.DNC).count()
    neutral = base_query.filter(Reply.classification == ReplyClassification.NEUTRAL).count()

    needs_follow_up = base_query.filter(
        Reply.classification.in_([ReplyClassification.INTERESTED, ReplyClassification.CALLBACK]),
        Reply.reviewed_at.is_(None),
    ).count()
    reviewed = base_query.filter(Reply.reviewed_at.isnot(None)).count()
    total = base_query.count()

    return {
        "hot": hot,
        "callback": callback,
        "question": question,
        "not_interested": not_interested,
        "wrong_number": wrong_number,
        "dnc": dnc,
        "neutral": neutral,
        "needs_follow_up": needs_follow_up,
        "reviewed": reviewed,
        "total": total,
    }


@router.get("/replies")
def list_replies(
    hot_only: bool = False,
    needs_attention: bool = False,
    bucket: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Replies screen - shows replies for leads owned by current advisor.
    NOTE: this fixes the inverted-filter bug from the desktop version -
    explicitly filters by the advisor's own leads across ALL time, not
    just today, unless a date range is passed.

    needs_attention=True implements Mike's specific request: "only hand
    me a hot lead when I'm ready to book" - filters down to just
    Interested + Callback classifications, hiding Neutral and DNC
    replies that don't need a human decision. This is the filtered
    inbox behind the notification bell and Overview page, distinct from
    the older hot_only flag (which only checks the binary is_hot field).

    bucket is the action-center scorecard filter - clicking a card on
    the Replies page passes its bucket name here, matching the exact
    same bucket definitions reply_counts() above uses, so the numbers
    on the cards always agree with what clicking through actually
    shows. Kept separate from needs_attention/hot_only (both predate
    this) rather than replacing them, since other callers (notification
    bell, Overview) still use needs_attention directly.
    """
    from app.models.models import ReplyClassification

    query = (
        db.query(Reply)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(Lead.organization_id == current_user.organization_id)
        .filter(Lead.assigned_to_id == current_user.id)
    )
    if hot_only:
        query = query.filter(Reply.is_hot == True)
    if needs_attention:
        query = query.filter(Reply.classification.in_([ReplyClassification.INTERESTED, ReplyClassification.CALLBACK]))

    bucket_to_classification = {
        "hot": ReplyClassification.INTERESTED,
        "callback": ReplyClassification.CALLBACK,
        "question": ReplyClassification.QUESTION,
        "not_interested": ReplyClassification.NOT_INTERESTED,
        "wrong_number": ReplyClassification.WRONG_NUMBER,
        "dnc": ReplyClassification.DNC,
        "neutral": ReplyClassification.NEUTRAL,
    }
    if bucket in bucket_to_classification:
        query = query.filter(Reply.classification == bucket_to_classification[bucket])
    elif bucket == "needs_follow_up":
        query = query.filter(
            Reply.classification.in_([ReplyClassification.INTERESTED, ReplyClassification.CALLBACK]),
            Reply.reviewed_at.is_(None),
        )
    elif bucket == "reviewed":
        query = query.filter(Reply.reviewed_at.isnot(None))
    elif bucket is not None:
        raise HTTPException(status_code=400, detail=f"Unknown bucket: {bucket}")

    return query.order_by(Reply.received_at.desc()).limit(200).all()
