import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Form, Query, Request, UploadFile, File
from fastapi.responses import Response
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.deps import get_db, get_current_user, require_tenant_user
from app.models.models import User, Lead, Reply, ReplyClassification
from app.services.sms_service import send_sms, send_batch, send_mms
from app.routers.compose_router import acting_advisor
from app.utils.twilio_webhook_guard import guard_inbound, guard_status_callback
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)

router = APIRouter(prefix="/sms", tags=["sms"])
logger = logging.getLogger(__name__)

# ── what Twilio is allowed to receive back ───────────────────────────────────
#
# Twilio fetches a webhook and expects TwiML — `text/xml`. A JSON body earns
# error 12300, "Invalid Content-Type", logged against the account on every
# single inbound message and every delivery receipt. The processing underneath
# was always correct; only the reply was the wrong shape, which is why replies,
# STOP handling and cadence stops all worked while the Twilio console filled
# with errors.
#
# An empty <Response/> is the documented way to say "received, and I am not
# replying to the sender" — which is exactly right here: the advisor answers a
# reply from the app, and an auto-reply would be an unexpected text to a family.
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _twiml_ack() -> Response:
    """200 with an empty TwiML document. No message is sent to the sender."""
    return Response(content=_EMPTY_TWIML, media_type="application/xml")

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


class DraftReplyResponse(BaseModel):
    suggested_reply: str
    booking_url: Optional[str] = None
    booking_link_id: Optional[str] = None
    source: str


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
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


class DraftReplyRequest(BaseModel):
    tone: str = "warm"  # cold | warm | hot | urgent
    ai_direction: Optional[str] = None  # per-lead context override
    sample_message: Optional[str] = None  # User-provided sample to use as AI foundation


@router.post("/draft-reply/{lead_id}", response_model=DraftReplyResponse)
def draft_reply_for_lead(
    lead_id: str,
    req: DraftReplyRequest = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    lead = _get_lead_for_current_org_or_404(db, lead_id, current_user)
    from app.services.draft_reply_service import draft_reply
    tone = (req.tone if req and req.tone else "warm")
    ai_direction = (req.ai_direction if req and req.ai_direction else None)
    sample_message = (req.sample_message if req and req.sample_message else None)
    result = draft_reply(db, lead, current_user, tone=tone, ai_direction=ai_direction, sample_message=sample_message)
    return result




@router.post("/send")
def send_single(req: SendRequest, db: Session = Depends(get_db), current_user: User = Depends(require_tenant_user)):
    """Send one SMS AS THE LEAD'S ASSIGNED ADVISOR, not as whoever pressed Send.

    This used to pass `current_user` straight through. The composer, the email
    sender and the resend button were all corrected to `acting_advisor`; this
    endpoint - the one that actually sends the text - was missed, so it kept
    resolving the sender from the caller.

    Under impersonation the caller is the platform owner, so a send refused
    outright (correctly - the platform-owner guard). The quieter failure is the
    one that matters: with organization credentials present it would NOT have
    refused. It would have resolved the ORGANIZATION's shared number instead of
    the advisor's own assigned number, and a family would have been texted from
    a number that is not their advisor's - which is exactly what per-advisor
    number assignment exists to prevent.
    """
    lead = authorized_lead_query(db, current_user).filter(Lead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    try:
        message = send_sms(db, acting_advisor(db, lead, current_user), lead,
                           req.template, req.include_booking_link)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message_id": message.id, "status": message.twilio_status}


@router.post("/send-batch")
def send_batch_endpoint(req: BatchSendRequest, db: Session = Depends(get_db), current_user: User = Depends(require_tenant_user)):
    """Send to many leads, EACH AS ITS OWN ASSIGNED ADVISOR.

    A batch is the case where one sender for every lead is most obviously
    wrong: the leads in it usually belong to different people, and each one
    should go out from the number the family would recognise as their own
    advisor's. Leads are grouped by acting advisor and each group sent under
    that advisor; the counts are merged so the response shape is unchanged.

    `send_batch` swallows a per-lead failure into `skipped`, so before this fix
    a batch sent under an impersonating platform owner would have reported
    every lead as merely "skipped" - no error, no sends, nothing to explain it.
    """
    leads = authorized_lead_query(db, current_user).filter(Lead.id.in_(req.lead_ids)).all()

    groups: dict[str, tuple[User, list[Lead]]] = {}
    for lead in leads:
        who = acting_advisor(db, lead, current_user)
        groups.setdefault(who.id, (who, []))[1].append(lead)

    merged = {"sent_count": 0, "skipped_count": 0, "sent_ids": [], "skipped_ids": []}
    for who, group in groups.values():
        part = send_batch(db, who, group, req.template, req.include_booking_link)
        merged["sent_count"] += part.get("sent_count", 0)
        merged["skipped_count"] += part.get("skipped_count", 0)
        merged["sent_ids"].extend(part.get("sent_ids", []))
        merged["skipped_ids"].extend(part.get("skipped_ids", []))
    return merged


@router.post("/webhook/status-callback")
async def sms_status_callback(
    request: Request,
    MessageSid: str = Form(...),
    MessageStatus: str = Form(...),
    ErrorCode: str | None = Form(None),
    ErrorMessage: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """
    Twilio delivery status callback — called by Twilio each time an SMS
    delivery status changes (sent → delivered, failed, undelivered, etc.).

    AUTHENTICATE FIRST. `guard_status_callback` resolves the sending Twilio
    account from AccountSid, loads THAT account's auth token (per-advisor or
    per-org, decrypted from the DB — never a global env var), verifies the
    signature against it, and confirms the account owns this MessageSid. It
    raises 403 on any failure. Nothing below runs unless it returns, so a
    forged callback carrying a real MessageSid leaves the row untouched.
    """
    await guard_status_callback(request, db)
    from app.models.models import Message

    msg = db.query(Message).filter(Message.twilio_sid == MessageSid).first()
    if not msg:
        # Twilio may re-POST for messages sent before this feature existed — that's OK
        return _twiml_ack()

    msg.twilio_status = MessageStatus
    msg.delivery_status = MessageStatus  # keep both columns in sync
    msg.delivery_status_at = datetime.utcnow()

    # Explicit outcome, written from the provider's own receipt. The transcript
    # reads THIS, not the presence of a row — see app/services/message_state.py.
    from app.services.message_state import normalize_provider_status
    msg.send_state = normalize_provider_status(MessageStatus)

    # Twilio only sends ErrorCode on a failure receipt. Never clear a code we
    # already have: 'sent' then 'undelivered' then a retry's 'sent' must not
    # erase the reason the first attempt failed.
    if ErrorCode:
        msg.error_code = str(ErrorCode)[:32]
    if ErrorMessage:
        msg.error_message = str(ErrorMessage)[:500]

    db.commit()
    logger.info("twilio status callback: message=%s status=%s state=%s error=%s",
                msg.id, MessageStatus, msg.send_state, ErrorCode or "-")
    return _twiml_ack()


@router.post("/webhook/inbound")
async def inbound_webhook(
    request: Request,
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(...),
    MessageSid: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Twilio webhook for inbound SMS replies.

    P0 AUTHENTICATION BOUNDARY. Everything below this guard is a side effect a
    forged message must never be able to cause: creating a Reply, stopping a
    cadence, flipping lead.status, writing a DNC/suppression entry, or invoking
    the AI conversation and pipeline services.

    `guard_inbound` resolves the sending Twilio account from AccountSid, loads
    that account's own decrypted auth token, verifies the signature against it,
    and confirms the account owns the destination number in `To` — so a valid
    signature from Org A cannot inject a reply into Org B's inbox. It raises
    403 on any failure, before any of the above is reachable.
    """
    await guard_inbound(request, db)
    from app.services.dedup_service import normalize_phone
    from app.services.cadence_service import stop_cadence_for_lead
    from app.services.reply_classification_service import classify_reply, contains_hard_stop_language
    from app.models.models import CadenceStatus, ReplyClassification
    lead_phone = normalize_phone(From)
    twilio_to = normalize_phone(To)  # the advisor Twilio number that received this message

    # Multi-tenant safety: find WHO OWNS the number this arrived on, then scope
    # the lead lookup to their organization. Without this, a lead in Org A with
    # the same phone as a lead in Org B would match the wrong record when Org B's
    # Twilio number receives the message.
    #
    # BOTH LEVELS, mirroring the send ladder. This used to check only
    # `users.twilio_phone_number`, so an organization sending from its SHARED
    # number — the toll-free/10DLC case, and the only kind Restland has — had
    # every inbound reply dropped on the "unrecognized number" branch below.
    # Silently: no lead, no conversation, and no STOP processing, which makes
    # it a compliance failure and not merely a missing feature. A shared sender
    # is the normal configuration for a funeral home whose advisors do not each
    # carry their own number.
    advisor = db.query(User).filter(User.twilio_phone_number == twilio_to).first()
    org_id = advisor.organization_id if advisor else None
    if org_id is None:
        from app.models.models import Organization
        _org = (db.query(Organization)
                .filter(Organization.org_twilio_phone_number == twilio_to)
                .first())
        org_id = _org.id if _org else None

    if org_id is not None:
        # `advisor` stays None on the shared-sender path ON PURPOSE. The reply
        # belongs to whoever owns the LEAD, and the handler below already
        # resolves that from `lead.assigned_to_id`; inventing an advisor here
        # would attach a family's reply to whichever colleague owns the number.
        lead = db.query(Lead).filter(
            Lead.phone == lead_phone,
            Lead.organization_id == org_id,
        ).order_by(Lead.updated_at.desc()).first()
    else:
        # Nobody owns this Twilio number — misconfigured, or a number that
        # belongs to something else entirely. Return early rather than doing a
        # cross-org lead lookup which could apply DNC flags or AI pipeline
        # triggers to the wrong org's data.
        logger.warning("[sms_webhook] Unrecognized Twilio number %s — matches no "
                       "advisor and no organization sender, dropping inbound",
                       twilio_to)
        return _twiml_ack()

    if not lead:
        # Unknown sender - log nothing actionable, just acknowledge Twilio
        logger.info("[sms_webhook] inbound from a number with no matching lead")
        return _twiml_ack()

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
    db.flush()  # get reply.id before pipeline processing

    # Route to the correct AI handler:
    #   booked leads  → post-booking concierge (gpt-4o, intent detection, escalate on reschedule/cancel)
    #   all others    → pipeline auto-conversation (pre-booking cadence)
    # Both handlers are wrapped so a failure never breaks the Twilio webhook response.
    try:
        _reply_advisor = advisor or (
            db.query(User).filter(User.id == lead.assigned_to_id).first() if lead.assigned_to_id else None
        )
        if _reply_advisor:
            if lead.status == "booked":
                from app.services.ai_conversation_service import handle_inbound_reply
                handle_inbound_reply(db, lead, _reply_advisor, Body)
            else:
                from app.services.pipeline_service import process_inbound_reply
                process_inbound_reply(db, lead, _reply_advisor, reply)
    except Exception as _pe:
        import logging
        logging.getLogger(__name__).error("AI/pipeline processing error: %s", _pe)

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

    if is_hot and lead.assigned_to:
        from app.services.notification_service import notify_hot_reply
        try:
            notify_hot_reply(db, lead.assigned_to, lead, reply)
        except Exception:
            pass  # never let a notification failure break the Twilio webhook response

    logger.info("twilio inbound: lead=%s hot=%s classification=%s",
                lead.id, is_hot, classification.value)
    return _twiml_ack()


@router.patch("/replies/{reply_id}/mark-reviewed")
def mark_reply_reviewed(
    reply_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    reply = _get_org_reply_or_404(db, reply_id, current_user)
    reply.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(reply)
    return {
        "id": reply.id,
        "lead_id": reply.lead_id,
        "body": reply.body,
        "classification": reply.classification.value if reply.classification else None,
        "is_hot": reply.is_hot,
        "reviewed_at": reply.reviewed_at,
        "received_at": reply.received_at,
    }


@router.patch("/replies/{reply_id}/reclassify")
def reclassify_reply(
    reply_id: str,
    req: ReclassifyReplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    reply = _get_org_reply_or_404(db, reply_id, current_user)
    reply.classification = req.classification
    reply.is_hot = req.classification == ReplyClassification.INTERESTED
    reply.classification_confidence = "manual"
    reply.classification_reasoning = f"Manually reclassified by {current_user.full_name}"
    db.commit()
    db.refresh(reply)
    return {
        "id": reply.id,
        "lead_id": reply.lead_id,
        "body": reply.body,
        "classification": reply.classification.value if reply.classification else None,
        "is_hot": reply.is_hot,
        "reviewed_at": reply.reviewed_at,
        "received_at": reply.received_at,
    }


@router.get("/replies/activity-by-day")
def reply_activity_by_day(
    days: int = Query(14, ge=1, le=60),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
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

    is_manager = current_user.role in ("org_admin", "super_admin", "god_admin")
    activity_filters = [
        Lead.organization_id == current_user.organization_id,
        Reply.received_at.isnot(None),
        Reply.received_at >= start_at,
    ]
    if not is_manager:
        activity_filters.append(Lead.assigned_to_id == current_user.id)
    replies = (
        db.query(Reply.received_at)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(*activity_filters)
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


@router.get("/replies")
def list_replies(
    hot_only: bool = False,
    needs_attention: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
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
    """
    from app.models.models import ReplyClassification

    is_manager = current_user.role in ("org_admin", "super_admin", "god_admin")
    query = (
        db.query(Reply)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(Lead.organization_id == current_user.organization_id)
    )
    if not is_manager:
        query = query.filter(Lead.assigned_to_id == current_user.id)
    if hot_only:
        query = query.filter(Reply.is_hot == True)
    if needs_attention:
        query = query.filter(Reply.classification.in_([ReplyClassification.INTERESTED, ReplyClassification.CALLBACK]))

    results = (
        query
        .add_columns(Lead.first_name, Lead.last_name)
        .order_by(Reply.received_at.desc())
        .limit(200)
        .all()
    )

    return [
        {
            "id": r.Reply.id,
            "lead_id": r.Reply.lead_id,
            "lead_name": f"{r.first_name or ''} {r.last_name or ''}".strip() or "Unknown lead",
            "body": r.Reply.body,
            "classification": r.Reply.classification.value if r.Reply.classification else None,
            "is_hot": r.Reply.is_hot,
            "source": r.Reply.source,
            "reviewed_at": r.Reply.reviewed_at,
            "received_at": r.Reply.received_at,
            "hot_reason": r.Reply.hot_reason,
            "classification_confidence": r.Reply.classification_confidence,
            "classification_reasoning": r.Reply.classification_reasoning,
        }
        for r in results
    ]


# ── MMS (image/flyer) send ────────────────────────────────────────────────────

class MMSSendRequest(BaseModel):
    lead_id: str
    template: str
    media_url: str          # publicly accessible URL of image/flyer
    include_booking_link: bool = False


@router.post("/send-mms")
def send_mms_endpoint(
    req: MMSSendRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Send an MMS (text + image/flyer) to a single lead.

    Same sender rule as `/send`: the lead's assigned advisor, not the caller.
    """
    lead = authorized_lead_query(db, current_user).filter(Lead.id == req.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    try:
        message = send_mms(db, acting_advisor(db, lead, current_user), lead,
                           req.template, req.media_url, req.include_booking_link)
        return {"message_id": message.id, "status": message.twilio_status}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Media upload (flyers/images for MMS or email attachments) ────────────────

@router.post("/upload-media")
async def upload_media(
    file: UploadFile = File(...),
    current_user: User = Depends(require_tenant_user),
):
    """
    Upload a flyer/image to be used in MMS or email.
    Returns a public URL. Files are stored in /tmp for now —
    configure MEDIA_BASE_URL env var to point to your CDN/S3 bucket.
    In production, replace local storage with S3 or Cloudinary upload.
    """
    import uuid, os
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".pdf"}
    MAX_MEDIA_BYTES = 5 * 1024 * 1024  # 5 MB (Twilio MMS limit is ~5MB)

    ext = os.path.splitext(file.filename or "upload")[1].lower() or ".jpg"
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Accepted: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    media_base = os.environ.get("MEDIA_BASE_URL", "")
    filename = f"{uuid.uuid4().hex}{ext}"
    upload_dir = "/tmp/bookaboost_media"
    os.makedirs(upload_dir, exist_ok=True)
    dest = os.path.join(upload_dir, filename)

    written = 0
    chunk_size = 64 * 1024
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_MEDIA_BYTES:
                    f.close()
                    os.unlink(dest)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum size is {MAX_MEDIA_BYTES // (1024*1024)} MB.",
                    )
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).error("Media upload failed: %s", e)
        if os.path.exists(dest):
            os.unlink(dest)
        raise HTTPException(status_code=500, detail="Upload failed. Please try again.")

    if media_base:
        public_url = f"{media_base.rstrip('/')}/{filename}"
    else:
        public_url = f"/media/{filename}"   # serve locally via static mount if no CDN

    return {
        "filename": filename,
        "media_url": public_url,
        "size_bytes": written,
        "note": "Set MEDIA_BASE_URL env var to your CDN/S3 for public MMS delivery"
    }
