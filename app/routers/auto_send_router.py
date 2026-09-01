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

Phase 2 additions:
  - handle_inbound_for_auto_send() — called from ai_conversation_router
    when a new inbound reply comes in and advisor has auto_send enabled.
    Generates an AI reply, runs compliance check, then either queues it
    (candidate mode) or sends immediately (auto mode).
  - POST /auto-send/proactive-scan — advisor-triggered scan that finds
    dormant/warm leads with no recent outbound and drafts an AI re-engagement
    message for each one, adding them to the review queue.
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, Base
from app.routers.audit_log_router import log_action
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)
from app.services import lead_scope

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
    source = Column(String, default="ai")  # ai | cadence | campaign | proactive
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


# ── Existing Endpoints (unchanged) ────────────────────────────────────────────

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
    # OWN QUEUE ONLY. This was organization-scoped, so an advisor could rewrite
    # the body of a colleague's pending message and let the colleague approve
    # and send it under their own name.
    item = lead_scope.own_records_only(
        db.query(AutoSendItem).filter(
            AutoSendItem.id == item_id,
            AutoSendItem.organization_id == current_user.organization_id,
            AutoSendItem.status == "pending",
        ),
        AutoSendItem.advisor_id, current_user,
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
    lead = lead_scope.load_lead_in_scope(db, current_user, item.lead_id)
    return _serialize(item, lead)


@router.post("/{item_id}/approve")
def approve_item(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Approve and send a queued message."""
    # OWN QUEUE ONLY. Organization scope here meant an advisor could approve a
    # colleague's queued message and send it to a family that is not theirs -
    # from their own Twilio identity, with their own name substituted in.
    item = lead_scope.own_records_only(
        db.query(AutoSendItem).filter(
            AutoSendItem.id == item_id,
            AutoSendItem.organization_id == current_user.organization_id,
            AutoSendItem.status == "pending",
        ),
        AutoSendItem.advisor_id, current_user,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found or already actioned")

    # Second gate: the LEAD must also be in scope. The item and the lead can
    # disagree after a reassignment, and the message is what actually reaches a
    # family, so the family is checked as well as the queue row.
    lead = lead_scope.load_lead_in_scope(db, current_user, item.lead_id)

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
    # Own queue only - same reason as edit and approve above. Skipping somebody
    # else's queued message is quieter than sending it and just as wrong: the
    # follow-up they were relying on never goes out and nothing tells them.
    item = lead_scope.own_records_only(
        db.query(AutoSendItem).filter(
            AutoSendItem.id == item_id,
            AutoSendItem.organization_id == current_user.organization_id,
            AutoSendItem.status == "pending",
        ),
        AutoSendItem.advisor_id, current_user,
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
    lead = authorized_lead_query(db, current_user).filter(Lead.id == req.lead_id).first()
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


# ── Phase 2: Inbound Auto-Reply Handler ───────────────────────────────────────

# Simple question patterns that are safe to answer automatically without
# advisor review. Anything that seems complex, emotional, or pricing-related
# goes to the review queue regardless of auto_send_phase.
_SAFE_QUESTION_PATTERNS = [
    "what time", "what's the address", "where are you located",
    "can i reschedule", "can we reschedule", "how do i reschedule",
    "what do i need to bring", "is parking", "how long will",
    "remind me", "confirm my appointment", "what is the appointment",
    "still on for", "are we still", "is my appointment",
]


def _is_simple_question(text: str) -> bool:
    """Return True if the inbound message looks like a routine logistical question."""
    lower = (text or "").lower()
    return any(pat in lower for pat in _SAFE_QUESTION_PATTERNS)


def handle_inbound_for_auto_send(
    db: Session,
    lead: "Lead",
    advisor: "User",
    inbound_text: str,
    reply_id: Optional[str] = None,
) -> Optional[str]:
    """
    Called from ai_conversation_router when a new inbound reply arrives
    and the advisor has auto_send_phase != 'off'.

    Generates an AI reply, runs compliance_preflight, then either:
      - 'candidate' phase: adds to review queue, returns 'queued'
      - 'auto' phase + simple question: sends immediately, returns 'sent'
      - 'auto' phase + complex: adds to review queue, returns 'queued'
      - Any compliance block: does nothing, returns 'blocked'

    Returns action taken: 'sent' | 'queued' | 'blocked' | 'skipped'
    """
    phase = advisor.auto_send_phase or "off"
    if phase == "off":
        return "skipped"

    # Compliance check
    try:
        from app.routers.compliance_router import compliance_preflight
        ok, reason = compliance_preflight(db, lead, "sms")
        if not ok:
            return "blocked"
    except Exception:
        pass  # if compliance import fails, fall through to queue

    # Generate AI reply
    try:
        import openai, os
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        system_prompt = (
            "You are a professional scheduling assistant for a financial services advisor. "
            "Reply to the lead's message below in 1-2 short, friendly sentences. "
            "Do NOT make promises about pricing or specific policy details. "
            "If the question is about rescheduling, confirm the advisor will reach out shortly. "
            "Keep it under 160 characters if possible."
        )
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": inbound_text},
            ],
            max_tokens=120,
            temperature=0.4,
        )
        ai_reply = completion.choices[0].message.content.strip()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("auto_send AI generation failed: %s", exc)
        return "skipped"

    # Decide: send immediately or queue for review
    send_immediately = (
        phase == "auto" and _is_simple_question(inbound_text)
    )

    if send_immediately:
        try:
            from app.services.sms_service import send_sms
            send_sms(db=db, lead=lead, advisor=advisor, template=ai_reply, include_booking_link=False)
            log_action(db, advisor.organization_id, advisor.id, action="auto_send.auto_sent", target_type="lead", target_id=lead.id)
            return "sent"
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("auto_send immediate send failed: %s", exc)
            # Fall through to queue

    # Queue for advisor review
    item = AutoSendItem(
        id=str(uuid.uuid4()),
        organization_id=advisor.organization_id,
        lead_id=lead.id,
        advisor_id=advisor.id,
        message=ai_reply,
        channel="sms",
        source="ai",
        source_ref=reply_id,
        ai_reason=f"AI-drafted reply to: {inbound_text[:120]}",
        status="pending",
        created_at=datetime.utcnow(),
    )
    db.add(item)
    db.commit()
    log_action(db, advisor.organization_id, advisor.id, action="auto_send.queued", target_type="lead", target_id=lead.id)
    return "queued"


# ── Phase 2: Proactive Re-engagement Scan ─────────────────────────────────────

class ProactiveScanRequest(BaseModel):
    days_dormant: int = 14      # leads with no outbound for this many days
    max_leads: int = 10         # max leads to draft messages for per scan
    statuses: list = ["sent", "replied", "hot"]  # which lead statuses to include


@router.post("/proactive-scan")
def proactive_scan(
    req: ProactiveScanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Phase 2 — Proactive outreach scan.

    Finds leads assigned to this advisor that:
      - Have the specified statuses (e.g. warm/replied leads gone quiet)
      - Have had no outbound message in the last N days
      - Are not DNC
      - Don't already have a pending auto-send item

    For each eligible lead, generates an AI re-engagement message and
    adds it to the review queue. Advisor reviews and approves/skips.
    Returns a count of leads queued.
    """
    if req.days_dormant < 3:
        raise HTTPException(status_code=400, detail="days_dormant must be at least 3")
    if req.max_leads > 50:
        raise HTTPException(status_code=400, detail="max_leads cannot exceed 50")

    cutoff = datetime.utcnow() - timedelta(days=req.days_dormant)

    # REWRITTEN FROM RAW SQL, FOR TWO REASONS.
    #
    # It selected `WHERE l.user_id = :advisor_id` and `WHERE m.direction =
    # 'outbound'`. Neither column exists: leads owns its advisor through
    # assigned_to_id, and `messages` has no direction at all - it is the
    # outbound table, with `replies` holding the inbound side. Postgres rejects
    # the statement on both counts, so this endpoint has never once run in
    # production. Every Message row IS an outbound message, so the dormancy
    # test is simply "nothing sent since the cutoff".
    #
    # And hand-written SQL is exactly how the org-wide default crept back in
    # everywhere else: it cannot be reached by the one authorization function.
    # Starting from authorized_lead_query means the scan is confined to the
    # caller's own book by the same code that confines the lead list, and a
    # future edit to the scope reaches this query for free.
    from app.models.models import Message as _Msg
    from app.services.lead_scope import authorized_lead_query as _alq

    pending_lead_ids = db.query(AutoSendItem.lead_id).filter(
        AutoSendItem.status == "pending").subquery()
    contacted_lead_ids = db.query(_Msg.lead_id).filter(
        _Msg.sent_at > cutoff).subquery()

    rows = (
        _alq(db, current_user)
        .filter(
            Lead.status.in_(req.statuses),
            Lead.status != "dnc",
            Lead.phone.isnot(None),
            ~Lead.id.in_(db.query(pending_lead_ids.c.lead_id)),
            ~Lead.id.in_(db.query(contacted_lead_ids.c.lead_id)),
        )
        .order_by(Lead.updated_at.asc())
        .limit(req.max_leads)
        .all()
    )

    if not rows:
        return {"queued": 0, "message": "No dormant leads found matching criteria"}

    import openai, os
    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    queued_count = 0

    for row in rows:
        try:
            # `rows` are Lead entities straight out of the authorized query now,
            # so there is nothing to re-fetch and no second chance to widen.
            lead = row
            try:
                from app.routers.compliance_router import compliance_preflight
                ok, _ = compliance_preflight(db, lead, "sms")
                if not ok:
                    continue
            except Exception:
                pass

            # AI-draft re-engagement message
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=[{
                    "role": "system",
                    "content": (
                        "You are a professional scheduling assistant drafting a re-engagement "
                        "SMS for a financial services advisor. The lead hasn't heard from us in "
                        f"at least {req.days_dormant} days. Write a warm, brief (under 160 chars) "
                        "check-in message. Do not make pricing promises. End with a soft call to action."
                    ),
                }, {
                    "role": "user",
                    "content": f"Lead name: {row.first_name}. Tier/status: {row.tier} / {row.status}.",
                }],
                max_tokens=100,
                temperature=0.5,
            )
            ai_message = completion.choices[0].message.content.strip()

            item = AutoSendItem(
                id=str(uuid.uuid4()),
                organization_id=current_user.organization_id,
                lead_id=row.id,
                advisor_id=current_user.id,
                message=ai_message,
                channel="sms",
                source="proactive",
                ai_reason=f"Dormant {req.days_dormant}+ days — proactive re-engagement draft",
                status="pending",
                created_at=datetime.utcnow(),
            )
            db.add(item)
            queued_count += 1
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("proactive_scan draft failed for lead %s: %s", row.id, exc)

    db.commit()
    log_action(
        db, current_user.organization_id, current_user.id,
        action="auto_send.proactive_scan",
        target_type="user", target_id=current_user.id,
        details={"queued": queued_count, "days_dormant": req.days_dormant},
    )
    return {"queued": queued_count, "total_eligible": len(rows)}
