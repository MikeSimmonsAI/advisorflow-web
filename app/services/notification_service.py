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
from app.services.platform_utils import get_brand_name

# NO CUSTOMER NAME IN A PLATFORM DEFAULT. This named a real cemetery
# customer in an address every brand on the platform would have sent from.
# Unused now that the send passes the advisor's own organization, and left
# empty rather than pointing anywhere.
NOTIFICATION_FROM_EMAIL = os.environ.get("EMAIL_FROM_ADDRESS", "").strip() or None


# ══════════════════════════════════════════════════════════════════════════════
# REPLY NOTIFICATION POLICY — hot-only today, configurable later
# ══════════════════════════════════════════════════════════════════════════════
#
# WHY THERE IS NO `notify_reply` IN THIS FILE.
#
# There used to be two functions here: `notify_reply`, which alerted an advisor
# on EVERY inbound reply, and `notify_hot_reply`, which alerts only on the ones
# that need a decision. `notify_reply` was removed during the 2026-07-06..09
# bulk-overwrite window, along with a number of other things - so for a while
# it was not clear whether its loss was a decision or an accident.
#
# IT IS NOW A DECISION, CONFIRMED. Hot-only is the intended operating model:
# surface actionable replies to advisors rather than flooding them with every
# neutral "ok". That matches the same reasoning the Replies screen is built on
# ("only hand me a hot lead when I'm ready to book") and the `needs_attention`
# filter that implements it. Classified as superseded behaviour, not a bug, and
# `notify_reply` is deliberately NOT restored.
#
# ── THE EXTENSION POINT ──────────────────────────────────────────────────────
#
# That decision is right for now and should not be welded shut. The modes a
# brand or organization may eventually want:
#
#     HOT_ONLY           interested / callback only            ← today
#     HOT_AND_QUESTIONS  the above, plus question replies
#     ALL_REPLIES        every inbound reply
#
# WHERE IT PLUGS IN. There is exactly one caller — sms_router.py, in the
# inbound webhook handler, which today reads:
#
#     from app.services.notification_service import notify_hot_reply
#     notify_hot_reply(db, lead.assigned_to, lead, reply)
#
# The change is to ask a policy resolver whether THIS reply's classification is
# notifiable for THIS organization's brand, and to keep `notify_hot_reply` as
# the delivery mechanism it already is. Sketched:
#
#     if reply_notification_policy(db, org).notifies(reply.classification):
#         notify_hot_reply(db, lead.assigned_to, lead, reply)
#
# WHERE THE SETTING BELONGS. Brand or organization configuration, alongside the
# other per-brand policy this platform already keeps in the database - the same
# shape as `brand_billing_configs` and `compensation_plans`, where a brand's own
# row beats a platform default. It does NOT belong as a constant in this module,
# and it does not belong in the webhook handler: a notification policy hardcoded
# in core business logic is exactly what makes the next brand impossible.
#
# NOT BUILT YET, AND DELIBERATELY SO. No column, no resolver, no configuration
# UI - because an unset policy would need a default, and a default here is a
# decision about how much noise every advisor receives. Today's behaviour is
# hot-only, stated in one place, easy to find, and easy to widen when somebody
# decides to.


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
        <p>Log in to {get_brand_name(db, str(advisor.organization_id))} to respond.</p>
    """

    # SENT AS THE ADVISOR'S OWN ORGANIZATION, NOT AS WHOEVER THE DEPLOYMENT
    # DEFAULTS TO. This called `send_email_via_provider` with no `org` at all,
    # so the From address fell all the way back to the process-wide
    # EMAIL_FROM_ADDRESS — one address for every brand on the platform. An
    # EvoSys Pro advisor's hot-reply alert could arrive wearing another
    # brand's name. The organization carries its own verified sender and its
    # brand resolves the rest; passing it is the whole fix.
    org = None
    org_id = getattr(advisor, "organization_id", None)
    if org_id:
        from app.models.models import Organization
        org = db.query(Organization).filter(Organization.id == org_id).first()
    result = send_email_via_provider(target_email, subject, body_html, org=org)
    if result["success"]:
        notification.is_sent = True
        from datetime import datetime, timezone
        notification.sent_at = datetime.now(timezone.utc)
        db.commit()

    return notification


# THE BELL POLLS THIS EVERY 60 SECONDS AND IT HAD NO CEILING.
#
# `GET /notifications/` is the single most-called endpoint in production - about
# 1,440 calls a day per signed-in client, roughly 53% of all HTTP traffic - and
# this function answered every one of them with an unbounded
# `.all()` of full ORM rows, each carrying a free-text `message` built from a
# lead's name, phone and the entire body of their reply.
#
# Unread is not a small set by construction. Nothing expires a notification and
# nothing marks one read except a human clicking it, so an advisor who ignores
# the bell for a month is asking the server to materialise, hydrate into
# mapped objects, and serialise their whole backlog once a minute forever. On a
# 512 MB instance that is the wrong shape of query to put on the hottest path
# in the system.
#
# The fix is the ceiling the UI already implies. The bell shows a list and a
# count; it has never rendered more than a screenful. So the rows are capped,
# the true count comes back as a number from SQL instead of as len() of a list
# nobody displays, and the columns are named rather than hydrated - the caller
# uses five of them.
#
# `NOTIFICATION_PAGE_SIZE` is the cap. `has_more` tells the client the list is
# truncated; `unread_count` stays exact regardless of the cap, so the badge
# still reads 312 while the payload carries 50.
NOTIFICATION_PAGE_SIZE = 50


def unread_notification_count(db: Session, user_id: str) -> int:
    """Exact unread count, computed in SQL. Never materialises the rows."""
    from sqlalchemy import func as _func
    return int(
        db.query(_func.count(Notification.id))
        .filter(Notification.user_id == user_id, Notification.is_read == False)
        .scalar()
        or 0
    )


def get_unread_notifications(
    db: Session, user_id: str, limit: int = NOTIFICATION_PAGE_SIZE
) -> list[dict]:
    """The most recent unread notifications, newest first, capped at `limit`.

    Returns plain dicts of the five columns the bell renders - not ORM objects -
    so nothing lazy-loads a Lead or a User behind the serializer's back.
    """
    if limit < 1:
        limit = 1
    rows = (
        db.query(
            Notification.id,
            Notification.lead_id,
            Notification.type,
            Notification.message,
            Notification.created_at,
            Notification.link,
        )
        .filter(Notification.user_id == user_id, Notification.is_read == False)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "lead_id": r.lead_id,
            "type": r.type.value if hasattr(r.type, "value") else r.type,
            "message": r.message,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "link": r.link,
            "is_read": False,
        }
        for r in rows
    ]


def mark_notification_read(db: Session, notification_id: str, user_id: str) -> bool:
    notification = db.query(Notification).filter(
        Notification.id == notification_id, Notification.user_id == user_id
    ).first()
    if not notification:
        return False
    notification.is_read = True
    db.commit()
    return True
