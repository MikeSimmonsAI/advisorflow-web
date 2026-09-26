"""Tell the people who work a Wholesale workspace that a seller needs them.

Before this, a public seller inquiry created a property, a deal and a Lead and
told nobody: the records were unassigned, so even the platform's hot-reply
alert (which goes to `lead.assigned_to`) never fired for that seller.

WHO IS TOLD
  1. the configured inquiry assignee (`WholesaleSettings.inquiry_assignee_id`)
     when that is an active user of THIS organization; otherwise
  2. the organization's own workspace admins.
Platform staff (god/super admins) are never recipients: their access to a
tenant is support access, not membership, and a tenant's seller is not theirs
to be paged about.

HOW
  An in-app Notification (the bell), with `link` pointing at the deal. Nothing
  is emailed or texted from here. Creating a notification can never fail the
  request that caused it: it runs in a savepoint and a failure is logged.
"""
from __future__ import annotations

import logging
from typing import List, Optional

log = logging.getLogger(__name__)

TENANT_ADMIN_ROLES = ("org_admin", "admin", "owner")


def recipients(db, org_id: str, assignee_id: Optional[str] = None) -> List[str]:
    from app.models.models import User
    if assignee_id:
        u = (db.query(User).filter(User.id == assignee_id, User.organization_id == org_id,
                                   User.is_active.isnot(False)).first())
        if u is not None:
            return [u.id]
    rows = (db.query(User.id).filter(User.organization_id == org_id,
                                     User.role.in_(TENANT_ADMIN_ROLES),
                                     User.is_active.isnot(False)).all())
    return [r[0] for r in rows]


def notify(db, org_id: str, *, kind, message: str, lead_id: Optional[str] = None,
           link: Optional[str] = None, assignee_id: Optional[str] = None) -> int:
    """Create one bell notification per recipient. Returns how many."""
    from app.models.models import Notification
    try:
        users = recipients(db, org_id, assignee_id)
        if not users:
            return 0
        with db.begin_nested():
            for uid in users:
                db.add(Notification(user_id=uid, lead_id=lead_id, type=kind,
                                    message=message[:1000], link=link))
        return len(users)
    except Exception:  # noqa: BLE001 - a missing badge must never lose an inquiry
        log.exception("wholesale notify failed for org %s", org_id)
        return 0


def deal_link(deal_id: Optional[str]) -> Optional[str]:
    return ("/wholesale/deals/%s" % deal_id) if deal_id else None


# ── Seller inquiries: in-app always, email when the workspace turns it on ───
#
# EMAIL IS CONFIGURABLE PER WORKSPACE (WholesaleSettings.inquiry_email_*),
# OFF by default: a new tenant never starts receiving mail it did not ask for.
# Recipients are the addresses the admin typed, or - left blank - the same
# people the bell goes to (their notification address, else their login).
# Sent AFTER the inquiry is committed (`send_pending_email`), through the
# organization's own verified sender, and it carries no phone number or
# message body: the email says an inquiry is waiting and links to it.
#
# Internal SMS to staff is deliberately not here - it needs its own messaging
# service and consent model before it exists.

EMAIL_KINDS = ("new", "re-engaged", "additional contact", "held")


def inquiry(db, org_id: str, *, kind: str, held: bool, message: str, lead_id: Optional[str],
            deal_id: Optional[str], assignee_id: Optional[str], details: dict) -> dict:
    """In-app notification now; returns the email job to run after commit."""
    from app.models.models import NotificationType
    n = notify(db, org_id, kind=NotificationType.WHOLESALE_INQUIRY, message=message,
               lead_id=lead_id, link=deal_link(deal_id), assignee_id=assignee_id)
    job = {"org_id": org_id, "kind": "held" if held else kind, "deal_id": deal_id,
           "assignee_id": assignee_id, "details": details, "in_app": n}
    db.info.setdefault("wholesale_inquiry_email_jobs", []).append(job)
    return job


def email_recipients(db, org_id: str, settings, assignee_id: Optional[str]) -> List[str]:
    from app.models.models import User
    typed = [e.strip().lower() for e in (getattr(settings, "inquiry_email_recipients", None) or "")
             .replace(";", ",").split(",") if e.strip()]
    if typed:
        return sorted(set(typed))
    out = []
    for uid in recipients(db, org_id, assignee_id):
        u = db.query(User).filter(User.id == uid).first()
        addr = (getattr(u, "notification_email", None) or getattr(u, "email", None) or "").strip()
        if addr:
            out.append(addr.lower())
    return sorted(set(out))


def _email_html(org_name: str, job: dict, link: Optional[str]) -> tuple:
    import html
    d = job["details"]
    heading = {"new": "New seller inquiry", "re-engaged": "A seller came back (re-engaged)",
               "additional contact": "New contact for a property that already has a seller",
               "held": "Seller inquiry waiting - lead limit reached"}[job["kind"]]
    rows = [("Seller", d.get("name")), ("Property", d.get("address")),
            ("Condition", (d.get("condition") or "").replace("_", " ") or None),
            ("Timeline", (d.get("timeline") or "").replace("_", " ") or None),
            ("SMS consent", "yes" if d.get("sms_consent") else "no"), ("Reference", d.get("reference"))]
    body = "".join("<tr><td style='padding:4px 12px 4px 0;color:#56637a'>%s</td><td>%s</td></tr>"
                   % (html.escape(k), html.escape(str(v))) for k, v in rows if v)
    extra = ""
    if job["kind"] == "held":
        extra = ("<p><strong>This seller is being held, not contacted:</strong> the workspace has "
                 "reached its plan's lead limit. Nothing will be sent to them until capacity is "
                 "available or the plan is upgraded.</p>")
    if job["kind"] == "additional contact":
        extra = "<p>Verify this person's connection to the property before any contact.</p>"
    cta = ("<p><a href='%s'>Open the deal</a></p>" % html.escape(link)) if link else ""
    subject = "%s - %s" % (heading, d.get("address") or d.get("reference") or "")
    return subject[:180], ("<p><strong>%s</strong></p><table>%s</table>%s%s"
                           "<p style='color:#56637a;font-size:12px'>You receive this because %s has "
                           "seller inquiry emails turned on in EvoSys Wholesale settings.</p>"
                           % (html.escape(heading), body, extra, cta, html.escape(org_name)))


def send_pending_email(db) -> List[dict]:
    """Run the email jobs queued by `inquiry` in this session. Call AFTER commit.
    Never raises; returns what happened for each job."""
    jobs = db.info.pop("wholesale_inquiry_email_jobs", []) or []
    results = []
    for job in jobs:
        try:
            results.append(_send_one(db, job))
        except Exception:  # noqa: BLE001 - an unsent email must not fail the inquiry
            log.exception("wholesale inquiry email failed for org %s", job.get("org_id"))
            results.append({"sent": 0, "error": "exception"})
    return results


def _send_one(db, job: dict) -> dict:
    from app.models.models import Organization
    from app.models.wholesale_models import WholesaleSettings
    s = db.query(WholesaleSettings).filter(WholesaleSettings.organization_id == job["org_id"]).first()
    if s is None or not getattr(s, "inquiry_email_enabled", False):
        return {"sent": 0, "skipped": "disabled"}
    if job["kind"] not in EMAIL_KINDS:
        return {"sent": 0, "skipped": "kind %s" % job["kind"]}
    to = email_recipients(db, job["org_id"], s, job.get("assignee_id"))
    if not to:
        return {"sent": 0, "skipped": "no recipients"}
    org = db.query(Organization).filter(Organization.id == job["org_id"]).first()
    base = None
    try:
        from app.services.public_identity import public_base_url
        base = public_base_url(db, job["org_id"])
    except Exception:  # noqa: BLE001
        base = None
    link = ("%s/wholesale/deals/%s" % (base.rstrip("/"), job["deal_id"])) if base and job.get("deal_id") else None
    subject, body = _email_html(getattr(org, "name", "") or "Your workspace", job, link)
    from app.services.email_service import send_email_via_provider
    sent, errors = 0, []
    for addr in to:
        r = send_email_via_provider(addr, subject, body, org=org, message_type="internal_notification",
                                    sensitivity="internal")
        if r.get("success"):
            sent += 1
        else:
            errors.append(r.get("error") or "failed")
    from app.services import wholesale_service as svc
    try:
        svc.log_event(db, job["org_id"], "seller_inquiry.email", deal_id=job.get("deal_id"),
                      summary="Inquiry email: %s sent, %s failed" % (sent, len(errors)),
                      details={"recipients": len(to), "kind": job["kind"], "errors": errors[:3]})
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return {"sent": sent, "failed": len(errors), "recipients": len(to)}
