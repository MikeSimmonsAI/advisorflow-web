from fastapi import APIRouter, Depends, HTTPException, Form
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, Reply, Message
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
    lead is engaged - no more touches needed), and fires a HOT reply
    email notification to the owning advisor.
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
        # CALLBACK and NEUTRAL both count as "replied" at the lead-status
        # level - the richer classification distinction lives on the
        # Reply record itself for the filtered inbox to use.
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

    return {"status": "received", "is_hot": is_hot, "classification": classification.value}


@router.get("/replies")
def list_replies(
    hot_only: bool = False,
    needs_attention: bool = False,
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
    return query.order_by(Reply.received_at.desc()).limit(200).all()
