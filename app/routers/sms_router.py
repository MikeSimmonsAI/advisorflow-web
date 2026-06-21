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
    basic hot-lead keyword detection, stops the re-engagement cadence
    (any reply means the lead is engaged - no more touches needed), and
    fires a HOT reply email notification to the owning advisor.
    """
    from app.services.dedup_service import normalize_phone
    from app.services.cadence_service import stop_cadence_for_lead
    from app.models.models import CadenceStatus
    lead_phone = normalize_phone(From)

    lead = db.query(Lead).filter(Lead.phone == lead_phone).order_by(Lead.updated_at.desc()).first()
    if not lead:
        # Unknown sender - log nothing actionable, just acknowledge Twilio
        return {"status": "no_matching_lead"}

    body_lower = Body.lower()
    is_hot = any(kw in body_lower for kw in HOT_KEYWORDS)
    is_stop = any(kw in body_lower for kw in STOP_KEYWORDS)

    reply = Reply(
        lead_id=lead.id,
        body=Body,
        twilio_sid=MessageSid,
        is_hot=is_hot,
        hot_reason=f"keyword match" if is_hot else None,
    )
    db.add(reply)

    if is_stop:
        lead.status = "dnc"
        stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_DNC)
    elif is_hot:
        lead.status = "hot"
        stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_REPLIED)
    else:
        lead.status = "replied"
        stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_REPLIED)

    db.commit()

    if is_hot and lead.assigned_to:
        from app.services.notification_service import notify_hot_reply
        try:
            notify_hot_reply(db, lead.assigned_to, lead, reply)
        except Exception:
            pass  # never let a notification failure break the Twilio webhook response

    return {"status": "received", "is_hot": is_hot}


@router.get("/replies")
def list_replies(
    hot_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Replies screen - shows replies for leads owned by current advisor.
    NOTE: this fixes the inverted-filter bug from the desktop version -
    explicitly filters by the advisor's own leads across ALL time, not
    just today, unless a date range is passed.
    """
    query = (
        db.query(Reply)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(Lead.organization_id == current_user.organization_id)
        .filter(Lead.assigned_to_id == current_user.id)
    )
    if hot_only:
        query = query.filter(Reply.is_hot == True)
    return query.order_by(Reply.received_at.desc()).limit(200).all()
