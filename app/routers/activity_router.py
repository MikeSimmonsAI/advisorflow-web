"""
Activity feed — unified sent-message log for SMS + email.

Returns the most recent outbound messages (SMS + email) for the advisor's
organization, merged and sorted by sent time newest-first. Designed for the
Activity page and for the "sent today" badge on the Leads list.

Delivery status is included for SMS messages (updated by Twilio status-callback
webhook). Email delivery status is always 'sent' until read-tracking is wired.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, Message, EmailMessage

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("/sent")
def sent_activity(
    limit: int = Query(default=200, ge=1, le=500),
    days: int = Query(default=30, ge=1, le=365, description="How many days back to look"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Unified activity feed: last N sends (SMS + email) for this advisor's org,
    merged newest-first. Includes delivery status for SMS.
    god_admin with no org selected sees activity across ALL orgs.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    is_manager = current_user.role in ("org_admin", "super_admin", "god_admin")
    god_all = getattr(current_user, '_god_all_orgs', False)

    # ── SMS sends ──────────────────────────────────────────────────────────
    sms_base = db.query(Message, Lead).join(Lead, Message.lead_id == Lead.id)
    sms_filters = [Message.sent_at >= cutoff]
    if not god_all:
        sms_filters.append(Lead.organization_id == current_user.organization_id)
    sms_query = sms_base.filter(*sms_filters)
    if not is_manager:
        sms_query = sms_query.filter(Message.sender_id == current_user.id)
    sms_rows = sms_query.order_by(Message.sent_at.desc()).limit(limit).all()

    sms_items = [
        {
            "id": msg.id,
            "channel": "sms",
            "lead_id": lead.id,
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.phone or "—",
            "lead_phone": lead.phone,
            "lead_email": lead.email,
            "body_preview": (msg.body[:120] + "…") if msg.body and len(msg.body) > 120 else (msg.body or ""),
            "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
            "delivery_status": msg.delivery_status or msg.twilio_status or "pending",
            "delivery_status_at": msg.delivery_status_at.isoformat() if getattr(msg, "delivery_status_at", None) else None,
        }
        for msg, lead in sms_rows
    ]

    # ── Email sends ────────────────────────────────────────────────────────
    email_base = db.query(EmailMessage, Lead).join(Lead, EmailMessage.lead_id == Lead.id)
    email_filters = [EmailMessage.sent_at >= cutoff]
    if not god_all:
        email_filters.append(Lead.organization_id == current_user.organization_id)
    email_query = email_base.filter(*email_filters)
    if not is_manager:
        email_query = email_query.filter(EmailMessage.sender_id == current_user.id)
    email_rows = email_query.order_by(EmailMessage.sent_at.desc()).limit(limit).all()

    email_items = [
        {
            "id": msg.id,
            "channel": "email",
            "lead_id": lead.id,
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() or lead.email or "—",
            "lead_phone": lead.phone,
            "lead_email": lead.email,
            "subject": msg.subject,
            "body_preview": None,
            "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
            "delivery_status": msg.status or "sent",
            "delivery_status_at": None,
        }
        for msg, lead in email_rows
    ]

    # Merge and sort newest-first
    merged = sorted(
        sms_items + email_items,
        key=lambda x: x["sent_at"] or "",
        reverse=True,
    )[:limit]

    return merged
