"""
HOT Reply Notification Service
Per Mike's Phase 1 priority: "Personal email notifications for HOT replies."

When the inbound SMS webhook flags a reply as hot (see sms_router.py
HOT_KEYWORDS), this service sends an immediate email alert to the
advisor so they don't have to keep checking the Replies screen.

Uses the same SendGrid setup as email_service.py.
"""

import os
from sqlalchemy.orm import Session
from app.models.models import User, Lead, Reply, Notification, NotificationType
from app.services.email_service import send_email_via_provider

NOTIFICATION_FROM_EMAIL = os.environ.get("EMAIL_FROM_ADDRESS", "noreply@restland-advisorflow.com")


def notify_hot_reply(db: Session, advisor: User, lead: Lead, reply: Reply) -> Notification:
    """
    Sends an email to the advisor's notification_email (falls back to
    their login email if not set) and logs a Notification row regardless
    of whether the email send succeeds, so it always shows up in-app too.
    """
    lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "A lead"
    message_text = f"{lead_name} ({lead.phone}) just replied: \"{reply.body}\""

    notification = Notification(
        user_id=advisor.id,
        lead_id=lead.id,
        type=NotificationType.HOT_REPLY,
        message=message_text,
    )
    db.add(notification)
    db.commit()

    target_email = advisor.notification_email or advisor.email
    if not advisor.notify_on_hot_reply:
        return notification

    subject = f"🔥 HOT lead reply: {lead_name}"
    body_html = f"""
        <p><strong>{lead_name}</strong> just replied to your message:</p>
        <blockquote style="border-left: 3px solid #c41e3a; padding-left: 12px; color: #333;">
            {reply.body}
        </blockquote>
        <p>Phone: {lead.phone or 'N/A'}<br>
        Tier: {lead.tier if lead.tier else 'N/A'}</p>
        <p>Log in to AdvisorFlow to respond.</p>
    """

    result = send_email_via_provider(target_email, subject, body_html)
    if result["success"]:
        notification.is_sent = True
        from datetime import datetime, timezone
        notification.sent_at = datetime.now(timezone.utc)
        db.commit()

    return notification


def get_unread_notifications(db: Session, user_id: str) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.is_read == False)
        .order_by(Notification.created_at.desc())
        .all()
    )


def mark_notification_read(db: Session, notification_id: str, user_id: str) -> bool:
    notification = db.query(Notification).filter(
        Notification.id == notification_id, Notification.user_id == user_id
    ).first()
    if not notification:
        return False
    notification.is_read = True
    db.commit()
    return True
