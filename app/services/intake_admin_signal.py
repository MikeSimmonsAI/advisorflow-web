"""In-app signal to a workspace's admins when a public form captures a lead there.

A demo request filed through a brand's site lands as an UNASSIGNED lead in the
destination workspace. The brand got its notice (public_demo_notifications) but
nobody inside the workspace was told. This writes one in-app Notification row
per ACTIVE org_admin of exactly the lead's own organization.

DATABASE ONLY. No email, SMS, push outbox row or provider call is made here -
it deliberately does not go through notification_service, whose senders do.
Replay-safe: the same submission never writes a second row per recipient.

The payload/recipient helpers are pure (no app imports at module level) so they
can be exercised without a database.
"""
from typing import Callable, Iterable, List, Optional


def intake_ref(submission_id: Optional[str]) -> str:
    """Stable dedupe marker from the site's submission id; '' when it sent none."""
    sid = str(submission_id or "").strip()[:64]
    return "[ref %s]" % sid if sid else ""


def signal_message(lead, ref: str = "") -> str:
    name = ("%s %s" % (getattr(lead, "first_name", "") or "",
                       getattr(lead, "last_name", "") or "")).strip() or "A new lead"
    text = "%s requested a demo through your website. Open the lead to follow up." % name
    return ("%s %s" % (text, ref)).strip()[:500]


def signal_plan(lead, admins: Iterable, already: Callable[[str, str], bool],
                submission_id: Optional[str] = None) -> List[dict]:
    """One payload per active admin of the lead's workspace not already told."""
    if lead is None or not getattr(lead, "organization_id", None):
        return []
    message = signal_message(lead, intake_ref(submission_id))
    out, seen = [], set()
    for u in admins:
        if getattr(u, "is_active", True) is False:
            continue
        if getattr(u, "organization_id", lead.organization_id) != lead.organization_id:
            continue  # never cross a tenant boundary
        if u.id in seen or already(u.id, message):
            continue
        seen.add(u.id)
        out.append({"user_id": u.id, "lead_id": lead.id, "message": message})
    return out


def notify_workspace_admins(db, lead, submission_id: Optional[str] = None) -> int:
    """Write the in-app rows; returns how many were created. Never raises."""
    try:
        from app.models.models import Notification, NotificationType
        from app.services.reply_handoff import workspace_org_admins
        ref = intake_ref(submission_id)

        def already(user_id: str, message: str) -> bool:
            q = db.query(Notification.id).filter(
                Notification.user_id == user_id,
                Notification.lead_id == lead.id,
                Notification.type == NotificationType.DEMO_REQUEST)
            # With a submission ref the same text is the same submission; with
            # none, one unread signal per lead is enough.
            q = q.filter(Notification.message == message) if ref else \
                q.filter(Notification.is_read.isnot(True))
            return q.first() is not None

        plan = signal_plan(lead, workspace_org_admins(db, lead.organization_id),
                           already, submission_id)
        for p in plan:
            db.add(Notification(user_id=p["user_id"], lead_id=p["lead_id"],
                                type=NotificationType.DEMO_REQUEST,
                                message=p["message"]))
        if plan:
            db.commit()
        return len(plan)
    except Exception:  # noqa: BLE001 - the captured lead must stand regardless
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
