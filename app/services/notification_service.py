"""
Reply Notification Service

ORIGINALLY: "HOT Reply Notification Service" - only fired on
classification == INTERESTED, per the original Phase 1 priority
("Personal email notifications for HOT replies").

EXPANDED per Mike's explicit, direct request: he can't be watching the
dashboard all day, and a missed reply of ANY kind - not just a hot one -
is something he wants to know about the moment it happens. notify_reply()
below now fires for every classification a reply can have (interested,
callback, dnc, not_interested, wrong_number, question, neutral), with
the subject/urgency framing adjusted per classification so a DNC alert
doesn't read like exciting news and a hot lead alert doesn't read like
routine traffic.

notify_hot_reply is kept as a thin backward-compatible wrapper (in case
anything else ever calls it directly), but the real logic now lives in
notify_reply, which sms_router.py's inbound webhook calls unconditionally
for every reply, not just when is_hot is True.

SILENT FAILURE FIX: the email send result was previously checked only to
decide whether to mark the Notification as sent - if send_email_via_provider
failed (e.g. SendGrid not configured, which is a real possibility this
project has hit before), there was no record anywhere of WHY it failed,
just an is_sent=False with no explanation. notify_reply now stores the
failure reason on the Notification record itself, so System Health or a
future admin view can surface "your alert emails have been failing
because X" instead of alerts just silently not arriving with zero trace.
"""

import os
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.models import User, Lead, Reply, Notification, NotificationType, ReplyClassification
from app.services.email_service import send_email_via_provider

NOTIFICATION_FROM_EMAIL = os.environ.get("EMAIL_FROM_ADDRESS", "noreply@restland-advisorflow.com")

# Per-classification framing so the alert reads appropriately - a DNC
# reply shouldn't look like an exciting opportunity, and a hot lead
# shouldn't read like routine traffic. Keys match ReplyClassification's
# string values exactly.
CLASSIFICATION_ALERT_COPY = {
    "interested": {"emoji": "🔥", "label": "HOT lead reply", "urgency": "Respond quickly - this lead is ready to talk."},
    "callback": {"emoji": "📞", "label": "Callback requested", "urgency": "They want a call - reach out when you can."},
    "dnc": {"emoji": "🛑", "label": "DNC / opt-out reply", "urgency": "This number has been added to the suppression list automatically."},
    "not_interested": {"emoji": "👋", "label": "Reply: not interested", "urgency": "No action required, but worth a glance."},
    "wrong_number": {"emoji": "❓", "label": "Reply: wrong number", "urgency": "This may need a contact-info correction in Lead Cleanup."},
    "question": {"emoji": "💬", "label": "Reply: question asked", "urgency": "They're asking something - a quick answer keeps them engaged."},
    "neutral": {"emoji": "💬", "label": "New reply", "urgency": "Take a look when you get a chance."},
}


def notify_reply(db: Session, advisor: User, lead: Lead, reply: Reply) -> Notification:
    """
    Sends an immediate email alert for ANY inbound reply, regardless of
    classification, to the advisor's notification_email (falls back to
    their login email if not set). Always logs a Notification row first,
    before attempting the send, so there's a permanent in-app record
    even if the email itself never goes out.
    """
    lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "A lead"
    classification_value = reply.classification.value if reply.classification else "neutral"
    is_hot = classification_value == "interested" or bool(reply.is_hot)
    if is_hot:
        classification_value = "interested"  # normalize so the copy lookup below is consistent even if is_hot was set without a matching classification
    copy = CLASSIFICATION_ALERT_COPY.get(classification_value, CLASSIFICATION_ALERT_COPY["neutral"])

    notification_type = NotificationType.HOT_REPLY if is_hot else NotificationType.REPLY_RECEIVED
    message_text = f"{lead_name} ({lead.phone}) replied: \"{reply.body}\""

    notification = Notification(
        user_id=advisor.id,
        lead_id=lead.id,
        type=notification_type,
        message=message_text,
    )
    db.add(notification)
    db.commit()

    if not advisor.notify_on_hot_reply:
        # Setting name predates this expansion (originally "notify on
        # HOT reply specifically") but is kept as the single on/off
        # toggle for ALL reply alerts now, rather than adding a second
        # setting - simpler for the advisor to reason about ("do I get
        # emailed when leads reply" is one question, not several).
        return notification

    target_email = advisor.notification_email or advisor.email
    subject = f"{copy['emoji']} {copy['label']}: {lead_name}"
    body_html = f"""
        <p><strong>{lead_name}</strong> just replied to your message:</p>
        <blockquote style="border-left: 3px solid #c41e3a; padding-left: 12px; color: #333;">
            {reply.body}
        </blockquote>
        <p>{copy['urgency']}</p>
        <p>Phone: {lead.phone or 'N/A'}<br>
        Tier: {lead.tier.value if lead.tier else 'N/A'}<br>
        Classification: {classification_value.replace('_', ' ')}</p>
        <p>Log in to AdvisorFlow to respond.</p>
    """

    result = send_email_via_provider(target_email, subject, body_html)
    if result["success"]:
        notification.is_sent = True
        notification.sent_at = datetime.now(timezone.utc)
    else:
        # Previously this branch did nothing at all - is_sent simply
        # stayed False with zero explanation anywhere. Now the actual
        # failure reason is captured on the Notification record itself.
        notification.send_failure_reason = result.get("error") or "Unknown email send failure"
    db.commit()

    # SMS-to-advisor: the "fastest, hardest to miss" channel Mike
    # explicitly asked for, on top of email. Off by default
    # (notify_via_sms) and requires notification_phone to be set
    # separately from twilio_phone_number (the number LEADS get texted
    # from) - this is the advisor's own personal cell. Deliberately a
    # best-effort add-on: if this fails, it's recorded but never raises,
    # since the email alert above is the channel of record and this
    # must never block or break it.
    if advisor.notify_via_sms and advisor.notification_phone:
        from app.services.sms_service import send_plain_sms
        sms_text = f"{copy['emoji']} {copy['label']}: {lead_name} replied \"{reply.body[:100]}\""
        sms_result = send_plain_sms(advisor, advisor.notification_phone, sms_text)
        if not sms_result["success"]:
            existing_reason = notification.send_failure_reason or ""
            notification.send_failure_reason = f"{existing_reason} | SMS alert failed: {sms_result.get('error')}".strip(" |")
            db.commit()

    return notification


def notify_hot_reply(db: Session, advisor: User, lead: Lead, reply: Reply) -> Notification:
    """
    Backward-compatible wrapper - the real logic now lives in
    notify_reply(), which handles every classification including hot.
    Kept in case anything else still imports this name directly.
    """
    return notify_reply(db, advisor, lead, reply)


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
