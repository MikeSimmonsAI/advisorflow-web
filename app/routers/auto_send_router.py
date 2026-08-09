"""
Auto-Send Queue Router

Manages a queue of AI-generated messages waiting for advisor approval
before sending. When auto_send=True on a campaign or cadence, messages
go here first unless the advisor has enabled fully automatic mode.

Queue states: pending | approved | skipped | sent | failed

auto_send_phase values on User:
  "off"       — default, feature disabled
  "candidate" — eligible inbound replies go to review queue
  "auto"      — eligible inbound replies are sent immediately
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, Base
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/auto-send", tags=["auto-send"])


# ── Model ─────────────────────────────────────────────────────────────────────

class AutoSendItem(Base):
    __tablename__ = "auto_send_queue"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False)
    advisor_id = Column(String, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    channel = Column(String, default="sms")  # sms | email
    subject = Column(String, nullable=True)  # for email
    source = Column(String, default="ai")  # ai | cadence | campaign
    source_ref = Column(String, nullable=True)  # campaign_id or cadence_state_id or reply_id
    status = Column(String, default="pending")  # pending | approved | skipped | sent | failed
    ai_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    actioned_at = Column(DateTime, nullable=True)
    actioned_by_id = Column(String, ForeignKey("users.id"), nullable=True)


def _serialize(item: AutoSendItem, lead: Lead) -> dict:
    return {
        "id": item.id,
        "lead_id": item.lead_id,
        "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "—",
        "phone": lead.phone if lead else None,
        "email": lead.email if lead else None,
        "message": item.message,
        "channel": item.channel,
        "subject": item.subject,
        "source": item.source,
        "status": item.status,
        "ai_reason": item.ai_reason,
        "created_at": item.created_at,
        "actioned_at": item.actioned_at,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/queue")
def get_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all pending items in the auto-send queue for this advisor."""
    items = (
        db.query(AutoSendItem)
        .filter(
            AutoSendItem.organization_id == current_user.organization_id,
            AutoSendItem.advisor_id == current_user.id,
            AutoSendItem.status == "pending",
        )
        .order_by(AutoSendItem.created_at.asc())
        .all()
    )
    result = []
    for item in items:
        lead = db.query(Lead).filter(Lead.id == item.lead_id).first()
        result.append(_serialize(item, lead))
    return result


@router.get("/history")
def get_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get recently actioned items."""
    items = (
        db.query(AutoSendItem)
        .filter(
            AutoSendItem.organization_id == current_user.organization_id,
            AutoSendItem.advisor_id == current_user.id,
            AutoSendItem.status.in_(["sent", "approved", "skipped", "failed"]),
        )
        .order_by(AutoSendItem.actioned_at.desc())
        .limit(50)
        .all()
    )
    result = []
    for item in items:
        lead = db.query(Lead).filter(Lead.id == item.lead_id).first()
        result.append(_serialize(item, lead))
    return result


@router.get("/settings")
def get_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get current advisor's auto-send phase setting."""
    return {
        "auto_send_phase": current_user.auto_send_phase or "off",
        "display": {
            "off": "Off — inbound replies handled manually",
            "candidate": "Review queue — AI drafts replies, you approve before sending",
            "auto": "Full auto — eligible simple replies sent immediately",
        }.get(current_user.auto_send_phase or "off", "Off"),
    }


class SettingsRequest(BaseModel):
    auto_send_phase: str  # "off" | "candidate" | "auto"


@router.post("/settings")
def update_settings(
    req: SettingsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update the advisor's auto-send phase setting."""
    valid_phases = {"off", "candidate", "auto"}
    if req.auto_send_phase not in valid_phases:
        raise HTTPException(status_code=400, detail=f"auto_send_phase must be one of: {', '.join(valid_phases)}")

    current_user.auto_send_phase = req.auto_send_phase
    db.commit()
    log_action(
        db,
        current_user.organization_id,
        current_user.id,
        action="auto_send.settings_updated",
        target_type="user",
        target_id=current_user.id,
        details={"auto_send_phase": req.auto_send_phase},
    )
    return {"auto_send_phase": req.auto_send_phase}


class EditRequest(BaseModel):
    message: str
    subject: Optional[str] = None


@router.patch("/{item_id}/edit")
def edit_item(
    item_id: str,
    req: EditRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit the message body of a pending item before approving."""
    item = db.query(AutoSendItem).filter(
        AutoSendItem.id == item_id,
        AutoSendItem.organization_id == current_user.organization_id,
        AutoSendItem.status == "pending",
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found or already actioned")

    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message body cannot be empty")

    item.message = req.message.strip()
    if req.subject is not None:
        item.subject = req.subject
    db.commit()
    db.refresh(item)
    lead = db.query(Lead).filter(Lead.id == item.lead_id).first()
    return _serialize(item, lead)


@router.post("/{item_id}/approve")
def approve_item(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve and send a queued message."""
    item = db.query(AutoSendItem).filter(
        AutoSendItem.id == item_id,
        AutoSendItem.organization_id == current_user.organization_id,
        AutoSendItem.status == "pending",
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found or already actioned")

    lead = db.query(Lead).filter(Lead.id == item.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    try:
        if item.channel == "email" and lead.email:
            from app.services.email_service import send_email
            send_email(db=db, lead=lead, advisor=current_user, subject=item.subject or "Following up", body=item.message)
        else:
            from app.services.sms_service import send_sms
            send_sms(db=db, lead=lead, advisor=current_user, template=item.message, include_booking_link=False)

        item.status = "sent"
        log_action(db, current_user.organization_id, current_user.id, action="auto_send.approved", target_type="lead", target_id=lead.id)
    except Exception as e:
        item.status = "failed"
        item.ai_reason = str(e)

    item.actioned_at = datetime.utcnow()
    item.actioned_by_id = current_user.id
    db.commit()
    return {"status": item.status, "item_id": item_id}


@router.post("/{item_id}/skip")
def skip_item(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Skip a queued message without sending."""
    item = db.query(AutoSendItem).filter(
        AutoSendItem.id == item_id,
        AutoSendItem.organization_id == current_user.organization_id,
        AutoSendItem.status == "pending",
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found or already actioned")

    item.status = "skipped"
    item.actioned_at = datetime.utcnow()
    item.actioned_by_id = current_user.id
    db.commit()
    log_action(db, current_user.organization_id, current_user.id, action="auto_send.skipped", target_type="lead", target_id=item.lead_id)
    return {"status": "skipped", "item_id": item_id}


@router.post("/approve-all")
def approve_all(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve and send all pending items at once."""
    items = db.query(AutoSendItem).filter(
        AutoSendItem.organization_id == current_user.organization_id,
        AutoSendItem.advisor_id == current_user.id,
        AutoSendItem.status == "pending",
    ).all()

    sent = 0
    failed = 0
    for item in items:
        lead = db.query(Lead).filter(Lead.id == item.lead_id).first()
        if not lead:
            continue
        try:
            if item.channel == "email" and lead.email:
                from app.services.email_service import send_email
                send_email(db=db, lead=lead, advisor=current_user, subject=item.subject or "Following up", body=item.message)
            else:
                from app.services.sms_service import send_sms
                send_sms(db=db, lead=lead, advisor=current_user, template=item.message, include_booking_link=False)
            item.status = "sent"
            sent += 1
        except Exception:
            item.status = "failed"
            failed += 1
        item.actioned_at = datetime.utcnow()
        item.actioned_by_id = current_user.id

    db.commit()
    return {"sent": sent, "failed": failed, "total": len(items)}


class EnqueueRequest(BaseModel):
    lead_id: str
    message: str
    channel: str = "sms"
    subject: Optional[str] = None
    source: str = "ai"
    source_ref: Optional[str] = None
    ai_reason: Optional[str] = None


@router.post("/enqueue")
def enqueue_item(
    req: EnqueueRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a message to the auto-send queue for advisor review."""
    lead = db.query(Lead).filter(
        Lead.id == req.lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    item = AutoSendItem(
        id=str(uuid.uuid4()),
        organization_id=current_user.organization_id,
        lead_id=req.lead_id,
        advisor_id=current_user.id,
        message=req.message,
        channel=req.channel,
        subject=req.subject,
        source=req.source,
        source_ref=req.source_ref,
        ai_reason=req.ai_reason,
        status="pending",
        created_at=datetime.utcnow(),
    )
    db.add(item)
    db.commit()
    return {"id": item.id, "status": "pending"}
