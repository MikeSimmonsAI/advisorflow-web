# -*- coding: utf-8 -*-
"""ATLANTIS TEAM & ALERTS - who hears about a new request, who can take it, and
the shared New Requests inbox they take it from.

Managers run this from the app (Settings -> Team & Alerts). Nothing here needs
the website, a server variable or a developer when someone joins or leaves.

* Recipients and assignment are SEPARATE. Any number of people may be alerted;
  a customer has exactly one accountable advisor (Lead.assigned_to_id), set by
  the default advisor, by someone claiming it, or by a manager assigning it.
* ONE request -> ONE alert event. The event key is the request's first-contact
  task (website) or the referral itself (field). A retry or a repeat form while
  that task is still open finds the event already recorded and alerts nobody.
* EMAIL is never sent until the shared mailbox has been verified (a code sent to
  it was typed back in), the workspace has a working sender, and the server's
  OUTBOUND_EMAIL_ENERGY_TEAM_ALERT switch is on. Until then the screen says
  "Email not configured" and why, and no delivery is ever reported. Test
  records are never emailed.
* Only the workspace's own team is ever emailed. No customer is contacted.
"""
import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

TEAM_ROLES = ("org_admin", "manager", "advisor")
MANAGER_ROLES = ("org_admin", "manager")
ALERT_ACTION = "energy.request_alert"
FIRST_CONTACT_SOURCES = ("website", "field_referral")
INBOX_LINK = "/energy/follow-up?view=new"


def _now() -> datetime:
    return datetime.utcnow()


def _stamp(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_energy(org) -> bool:
    return (getattr(org, "industry", None) or "").lower() == "energy"


# ── settings row ─────────────────────────────────────────────────────────────
def _row(db: Session, org_id: str, create: bool = False):
    from app.models.energy_models import EnergyTeamSettings
    row = db.query(EnergyTeamSettings).filter(EnergyTeamSettings.organization_id == org_id).first()
    if row is None and create:
        # two managers saving for the very first time at once: one insert wins,
        # the other reloads it instead of failing
        from sqlalchemy.exc import IntegrityError
        sp = db.begin_nested()
        try:
            row = EnergyTeamSettings(organization_id=org_id, data="{}")
            db.add(row)
            db.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()
            row = db.query(EnergyTeamSettings).filter(EnergyTeamSettings.organization_id == org_id).first()
    return row


def load(db: Session, org_id: str, lock: bool = False) -> Dict[str, Any]:
    row = _row(db, org_id, create=lock)
    if lock and row is not None:
        from app.models.energy_models import EnergyTeamSettings
        row = (db.query(EnergyTeamSettings).filter(EnergyTeamSettings.id == row.id)
               .with_for_update().populate_existing().first()) or row
    try:
        d = json.loads(row.data) if row is not None and row.data else {}
    except (ValueError, TypeError):
        d = {}
    d = d if isinstance(d, dict) else {}
    d["members"] = d.get("members") if isinstance(d.get("members"), dict) else {}
    d["mailbox"] = d.get("mailbox") if isinstance(d.get("mailbox"), dict) else {}
    return d


def save(db: Session, org_id: str, data: Dict[str, Any], actor_id: Optional[str]) -> None:
    row = _row(db, org_id, create=True)
    # serialise concurrent edits from two managers
    from app.models.energy_models import EnergyTeamSettings
    row = (db.query(EnergyTeamSettings).filter(EnergyTeamSettings.id == row.id)
           .with_for_update().populate_existing().first()) or row
    row.data = json.dumps(data)
    row.updated_by_id = actor_id


# ── the team ─────────────────────────────────────────────────────────────────
def people(db: Session, org_id: str) -> List[Dict[str, Any]]:
    """Active people with a team role IN THIS WORKSPACE (membership role wins
    over users.role, as everywhere else on the platform)."""
    from app.models.sales_models import Membership, SCOPE_CUSTOMER_ORG
    from app.services.customer_provisioning import customer_people
    ws = {}
    for m in db.query(Membership).filter(Membership.scope_type == SCOPE_CUSTOMER_ORG,
                                         Membership.scope_id == org_id).all():
        if m.is_active or m.user_id not in ws:      # an active row wins over an old one
            ws[m.user_id] = (m.role, bool(m.is_active))
    out = []
    for u in customer_people(db, org_id):
        if u.id in ws and not ws[u.id][1]:
            continue                                # membership turned off = left this team
        role = ws[u.id][0] if u.id in ws else u.role
        if u.is_active is False or role not in TEAM_ROLES:
            continue
        out.append({"user_id": u.id, "name": u.full_name or u.email, "email": u.email,
                    "notification_email": getattr(u, "notification_email", None) or None,
                    "role": role, "is_manager": role in MANAGER_ROLES,
                    "setup_pending": bool(getattr(u, "must_change_password", False))})
    return out


def flags(person: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, bool]:
    stored = data["members"].get(person["user_id"]) or {}
    return {
        "in_app": bool(stored.get("in_app", person["is_manager"])),   # default: managers
        "email": bool(stored.get("email", False)),                   # default: nobody
        "assignable": bool(stored.get("assignable", True)),          # default: everyone
    }


def member(db: Session, org_id: str, user_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    return next((p for p in people(db, org_id) if p["user_id"] == user_id), None)


def can_take(db: Session, org_id: str, user_id: str, data: Optional[Dict[str, Any]] = None) -> bool:
    p = member(db, org_id, user_id)
    return p is not None and flags(p, data if data is not None else load(db, org_id))["assignable"]


def default_advisor(db: Session, org_id: str, data: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The workspace's default advisor, only while they are still an active,
    assignable member - someone who left never silently receives new work."""
    data = data if data is not None else load(db, org_id)
    uid = data.get("default_advisor_id")
    return uid if uid and can_take(db, org_id, uid, data) else None


# ── email: honest status, verification by code ──────────────────────────────
def _sender(db: Session, org_id: str) -> Dict[str, Any]:
    from app.services.email_service import describe_email_sender
    try:
        return describe_email_sender(db, org_id)
    except Exception:                                    # noqa: BLE001
        log.exception("energy_team: sender lookup failed")
        return {"ready": False, "from_email": None, "reason": "The sending address could not be read."}


def _switch_on() -> bool:
    from app.services import outbound_email_gate as gate
    return gate.source_enabled(gate.ENERGY_TEAM_ALERT)


CODE_TTL_MINUTES = 30
CODE_MAX_ATTEMPTS = 5


def _hash(org_id: str, code: str) -> str:
    import os
    key = (os.getenv("JWT_SECRET") or "energy-team").encode()
    return hmac.new(key, ("%s:%s" % (org_id, code.strip())).encode(), hashlib.sha256).hexdigest()


def email_status(db: Session, org_id: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Whether new-request email can go out, and in plain words why not."""
    data = data if data is not None else load(db, org_id)
    mb = data["mailbox"]
    sender = _sender(db, org_id)
    switch = _switch_on()
    address = mb.get("address")
    verified = bool(address and mb.get("verified_at") and mb.get("verified_address") == address
                    and mb.get("verified_from") and mb.get("verified_from") == sender.get("from_email"))
    reasons = []
    if not sender.get("ready"):
        reasons.append(sender.get("reason") or "No working sending address for this workspace.")
    if not switch:
        reasons.append("Team alert email is switched off on the server (OUTBOUND_EMAIL_ENERGY_TEAM_ALERT).")
    if not address:
        reasons.append("No shared alert mailbox has been entered.")
    elif not verified:
        reasons.append("The shared mailbox has not been verified yet (send a test code and type it back in).")
    return {
        "state": "ready" if not reasons else "not_configured",
        "label": "Email ready" if not reasons else "Email not configured",
        "reasons": reasons,
        "mailbox": address, "mailbox_verified": verified,
        "verified_at": mb.get("verified_at") if verified else None,
        "test_sent_at": mb.get("test_sent_at"),
        "sender": sender.get("from_email"), "sender_ready": bool(sender.get("ready")),
        "switch_on": switch,
        "last_delivery": mb.get("last_delivery"),
    }


def _send(db: Session, org_id: str, to: str, subject: str, html: str) -> Dict[str, Any]:
    """One email to one of the workspace's own people. Gate first, then send;
    returns what really happened - never assumes delivery."""
    from app.services import outbound_email_gate as gate
    from app.services.email_service import send_email_via_provider
    from app.services.public_identity import sending_identity_for_org
    try:
        gate.gate_transactional_email(to, purpose="energy team alert", source=gate.ENERGY_TEAM_ALERT)
        res = send_email_via_provider(to, subject, html, org=sending_identity_for_org(db, org_id),
                                      message_type="energy_team_alert", sensitivity="operational")
    except Exception as exc:                             # noqa: BLE001
        return {"to": to, "sent": False, "error": str(exc)[:300]}
    return {"to": to, "sent": bool(res.get("success")), "error": None if res.get("success") else (res.get("error") or "not sent")[:300]}


def set_mailbox(db: Session, org_id: str, address: Optional[str], actor_id: str) -> Dict[str, Any]:
    address = (address or "").strip().lower() or None
    if address and ("@" not in address or " " in address or len(address) > 200):
        raise ValueError("Enter one email address for the shared mailbox.")
    data = load(db, org_id, lock=True)
    if data["mailbox"].get("address") != address:
        data["mailbox"] = {"address": address}          # a new address must be verified again
    save(db, org_id, data, actor_id)
    return email_status(db, org_id, data)


def send_test(db: Session, org_id: str, actor_id: str) -> Dict[str, Any]:
    """Send a 6-digit code to the shared mailbox. Proves sending works; typing
    the code back proves the mailbox receives."""
    data = load(db, org_id, lock=True)
    mb = data["mailbox"]
    if not mb.get("address"):
        raise ValueError("Enter the shared mailbox address first.")
    sender = _sender(db, org_id)
    if not sender.get("ready"):
        raise ValueError(sender.get("reason") or "No working sending address for this workspace.")
    if not _switch_on():
        raise ValueError("Team alert email is switched off on the server (OUTBOUND_EMAIL_ENERGY_TEAM_ALERT).")
    code = "%06d" % secrets.randbelow(1000000)
    res = _send(db, org_id, mb["address"], "Atlantis alert mailbox check: %s" % code,
                "<p>Type this code into Settings &rarr; Team &amp; Alerts to confirm this mailbox "
                "receives new-request alerts:</p><p style='font-size:22px'><b>%s</b></p>" % code)
    if not res["sent"]:
        raise RuntimeError("The test email was not sent: %s" % (res["error"] or "unknown error"))
    mb.update({"code_hash": _hash(org_id, code), "test_sent_at": _stamp(), "test_from": sender.get("from_email"),
               "attempts": 0,
               "verified_at": None, "verified_address": None, "verified_from": None})
    save(db, org_id, data, actor_id)
    return email_status(db, org_id, data)


def confirm(db: Session, org_id: str, code: str, actor_id: str) -> Dict[str, Any]:
    data = load(db, org_id, lock=True)
    mb = data["mailbox"]
    if not mb.get("code_hash"):
        raise ValueError("Send a test code first.")
    try:
        sent_at = datetime.strptime(mb.get("test_sent_at") or "", "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        sent_at = None
    if sent_at is None or (_now() - sent_at).total_seconds() > CODE_TTL_MINUTES * 60 \
            or int(mb.get("attempts") or 0) >= CODE_MAX_ATTEMPTS:
        mb["code_hash"] = None
        save(db, org_id, data, actor_id)
        db.commit()
        raise ValueError("That code has expired or had too many tries. Send a new test code.")
    if not hmac.compare_digest(mb["code_hash"], _hash(org_id, code or "")):
        mb["attempts"] = int(mb.get("attempts") or 0) + 1
        save(db, org_id, data, actor_id)
        db.commit()
        raise ValueError("That code does not match the last test email.")
    mb.update({"verified_at": _stamp(), "verified_address": mb.get("address"),
               "verified_from": mb.get("test_from"), "code_hash": None})
    save(db, org_id, data, actor_id)
    return email_status(db, org_id, data)


# ── one request -> one alert event ──────────────────────────────────────────
def _claim_event(db: Session, org_id: str, event_key: str, lead, origin: str):
    """Record the event FIRST; the unique key makes the second attempt lose."""
    from sqlalchemy.exc import IntegrityError
    from app.models.energy_models import EnergyRequestAlert
    if db.query(EnergyRequestAlert.id).filter(EnergyRequestAlert.organization_id == org_id,
                                              EnergyRequestAlert.event_key == event_key).first():
        return None
    sp = db.begin_nested()
    try:
        ev = EnergyRequestAlert(organization_id=org_id, event_key=event_key, lead_id=lead.id, origin=origin)
        db.add(ev)
        db.flush()
        sp.commit()
        return ev
    except IntegrityError:
        sp.rollback()
        return None


def _lead_name(lead) -> str:
    return " ".join(x for x in (lead.first_name, lead.last_name) if x) or "Someone"


def alert_new_request(db: Session, *, org, lead, event_key: str, origin: str, what: str = "",
                      actor_id: Optional[str] = None) -> Dict[str, Any]:
    """Tell the selected team about one new request - once. Caller holds the
    lead row lock (both intake paths do), so a retry racing the first call
    waits and then finds the event."""
    from app.models.models import Notification, NotificationType
    event = _claim_event(db, org.id, event_key, lead, origin)
    if event is None:
        return {"duplicate": True, "event_key": event_key, "in_app": 0, "email": {"state": "duplicate"}}
    data = load(db, org.id)
    team = people(db, org.id)
    recipients = [p["user_id"] for p in team if flags(p, data)["in_app"]]
    fallback = False
    if not recipients:                     # never let a request arrive unseen
        recipients = [p["user_id"] for p in team if p["is_manager"]]
        fallback = True
    owner = member(db, org.id, lead.assigned_to_id)
    if owner and owner["user_id"] not in recipients:
        recipients.append(owner["user_id"])          # the accountable advisor always hears
    label = "Field referral" if origin == "field" else "Website request"
    tail = ("Assigned to %s." % owner["name"]) if owner else "Unclaimed - claim it in New Requests."
    msg = "%s from %s%s. %s" % (label, _lead_name(lead), (": " + what[:80]) if what else "", tail)
    if getattr(lead, "is_test", False):
        msg = "TEST record - " + msg
    can_inbox = {p["user_id"] for p in team if p["is_manager"] or flags(p, data)["assignable"]}
    for uid in recipients:
        link = ("%s&open=%s" % (INBOX_LINK, lead.id)) if uid in can_inbox else "/energy/follow-up"
        if owner and uid == owner["user_id"]:
            link = "/leads/%s?tab=overview" % lead.id
        db.add(Notification(user_id=uid, lead_id=lead.id, type=NotificationType.DEMO_REQUEST,
                            message=msg[:480], link=link))

    status = email_status(db, org.id, data)
    if getattr(lead, "is_test", False):
        email = {"state": "skipped", "reason": "Test records are never emailed.", "results": []}
    elif status["state"] != "ready":
        email = {"state": "not_configured", "reason": "; ".join(status["reasons"]), "results": []}
    else:
        to = [status["mailbox"]] + [(p["notification_email"] or p["email"]) for p in team
                                    if flags(p, data)["email"] and (p["notification_email"] or p["email"])]
        seen, uniq = set(), []
        for addr in to:
            if addr.lower() not in seen:
                seen.add(addr.lower())
                uniq.append(addr)
        # QUEUED, not sent: the emails go out only after this request is
        # committed (see _after_commit), so a rolled-back request never
        # emails anyone and a retry cannot email twice.
        email = {"state": "queued", "to": uniq,
                 "subject": "New %s - %s" % (label.lower(), _lead_name(lead)),
                 "html": ("<p><b>%s</b></p><p>Open New Requests in the app to claim or assign it. "
                          "No message has been sent to the customer.</p>" % msg.replace("<", "&lt;"))}
    event.details = json.dumps({"lead_id": lead.id, "origin": origin, "in_app": recipients,
                                "fallback_to_managers": fallback, "assigned_to_id": lead.assigned_to_id,
                                "by": actor_id, "email": email, "at": _stamp()})
    if email["state"] == "queued":
        _send_after_commit(db, org.id, event.id)
    return {"duplicate": False, "event_key": event_key, "in_app": len(recipients),
            "fallback_to_managers": fallback, "email": {k: v for k, v in email.items() if k != "html"}}


def _send_after_commit(db: Session, org_id: str, event_id: str) -> None:
    """after_commit ALSO fires when a savepoint is released (the website intake
    runs each step in one), while the request itself is still uncommitted and
    invisible to another connection. So: ignore nested releases and deliver at
    the first real commit; once only. If the request is rolled back, the next
    real commit finds no event row and sends nothing."""
    from sqlalchemy import event as sa_event
    state = {"done": False}

    def _fire(session):
        if state["done"] or session.in_nested_transaction():
            return
        state["done"] = True
        try:
            deliver_queued(session.get_bind(), org_id, event_id)
        except Exception:                                   # noqa: BLE001
            log.exception("energy_team: queued alert email failed for event %s", event_id)
    sa_event.listen(db, "after_commit", _fire)


def deliver_queued(bind, org_id: str, event_id: str) -> Dict[str, Any]:
    """Send one committed event's queued emails in a fresh session and record
    exactly what happened. An event that was rolled back is simply not found."""
    from sqlalchemy.orm import Session as _S
    from app.models.energy_models import EnergyRequestAlert
    s = _S(bind=bind)
    try:
        ev = s.get(EnergyRequestAlert, event_id)
        d = _json(ev.details) if ev is not None else {}
        em = d.get("email") or {}
        if ev is None or em.get("state") != "queued":
            return {"state": "nothing"}
        results = [_send(s, org_id, addr, em.get("subject") or "New request", em.get("html") or "")
                   for addr in (em.get("to") or [])]
        d["email"] = {"state": "attempted", "sent": sum(1 for r in results if r["sent"]),
                      "failed": sum(1 for r in results if not r["sent"]), "results": results}
        ev.details = json.dumps(d)
        s.commit()
        return d["email"]
    finally:
        s.close()


def apply_default_advisor(db: Session, org_id: str, lead) -> Optional[str]:
    """New, unowned request + a default advisor set -> they own it."""
    if lead.assigned_to_id or (lead.tier or "") not in ("", "web_lead", "new_inquiry"):
        return None
    uid = default_advisor(db, org_id)
    if uid:
        lead.assigned_to_id = uid
    return uid


# ── the shared New Requests inbox ───────────────────────────────────────────
CLOSED = ("dnc", "dead", "lost", "closed")


class Conflict(Exception):
    pass


def may_use_inbox(db: Session, org_id: str, user, is_manager: bool) -> bool:
    return is_manager or can_take(db, org_id, user.id)


def _first_contact_tasks(db: Session, org_id: str, lead_id: str):
    from app.models.work_models import LeadTask
    return db.query(LeadTask).filter(LeadTask.organization_id == org_id, LeadTask.lead_id == lead_id,
                                     LeadTask.source.in_(FIRST_CONTACT_SOURCES), LeadTask.status == "open").all()


def _requests_q(db: Session, org_id: str):
    """A NEW REQUEST = a lead in this workspace, in New, not closed, with an open
    first-contact task from the website or a field referral. Imported or older
    leads that merely sit in the New tier are not requests and never appear."""
    from sqlalchemy import or_
    from app.models.models import Lead
    from app.models.work_models import LeadTask
    open_task = db.query(LeadTask.lead_id).filter(
        LeadTask.organization_id == org_id, LeadTask.source.in_(FIRST_CONTACT_SOURCES),
        LeadTask.status == "open", LeadTask.lead_id.isnot(None))
    return db.query(Lead).filter(Lead.organization_id == org_id, Lead.tier == "new_inquiry",
                                 or_(Lead.status.is_(None), ~Lead.status.in_(CLOSED)),
                                 Lead.id.in_(open_task.scalar_subquery()))


def is_request(db: Session, org_id: str, lead_id: str) -> bool:
    from app.models.models import Lead
    return _requests_q(db, org_id).filter(Lead.id == lead_id).first() is not None


LIMIT = 200


def inbox(db: Session, org_id: str, viewer, is_manager: bool, state: str = "unclaimed") -> Dict[str, Any]:
    from sqlalchemy import or_
    from app.models.models import Lead, User
    from app.models.work_models import LeadTask
    base = _requests_q(db, org_id)
    visible_all = base if is_manager else base.filter(
        or_(Lead.assigned_to_id.is_(None), Lead.assigned_to_id == viewer.id))
    counts = {"unclaimed": base.filter(Lead.assigned_to_id.is_(None)).count(),
              "mine": base.filter(Lead.assigned_to_id == viewer.id).count(), "all": visible_all.count()}
    q = base.filter(Lead.assigned_to_id.is_(None)) if state == "unclaimed" else \
        base.filter(Lead.assigned_to_id == viewer.id) if state == "mine" else visible_all
    leads = q.order_by(Lead.created_at.asc(), Lead.id.asc()).limit(LIMIT).all()
    ids = [l.id for l in leads]
    tasks: Dict[str, Any] = {}
    if ids:
        for t in db.query(LeadTask).filter(LeadTask.lead_id.in_(ids), LeadTask.source.in_(FIRST_CONTACT_SOURCES),
                                           LeadTask.status == "open").all():
            tasks.setdefault(t.lead_id, t)
    names = {u.id: (u.full_name or u.email) for u in db.query(User).filter(
        User.id.in_([l.assigned_to_id for l in leads if l.assigned_to_id] or ["-"])).all()}
    now = _now()
    rows = []
    for l in leads:
        try:
            cf = json.loads(l.custom_fields or "{}")
        except (ValueError, TypeError):
            cf = {}
        cf = cf if isinstance(cf, dict) else {}
        mine = l.assigned_to_id == viewer.id
        open_ok = is_manager or mine
        t = tasks.get(l.id)
        rows.append({
            "lead_id": l.id, "name": _lead_name(l), "origin": "field" if cf.get("field_rep_id") else "website",
            "request_kind": cf.get("request_kind"), "segment": cf.get("segment"),
            "area": l.zip_code or cf.get("service_area"), "company": cf.get("company"),
            "field_rep": cf.get("field_rep_name"),
            "created_at": l.created_at.isoformat() + "Z" if l.created_at else None,
            "age_hours": round((now - l.created_at).total_seconds() / 3600.0, 1) if l.created_at else None,
            "assigned_to_id": l.assigned_to_id, "assigned_to": names.get(l.assigned_to_id),
            "is_test": bool(getattr(l, "is_test", False)),
            "first_contact_due": t.due_at.isoformat() + "Z" if t is not None and t.due_at else None,
            "first_contact_overdue": bool(t is not None and t.due_at and t.due_at < now),
            # contact details only for the person who owns it (or a manager)
            "phone": l.phone if open_ok else None, "email": l.email if open_ok else None,
            "can_open": open_ok, "link": "/leads/%s?tab=overview" % l.id if open_ok else None,
        })
    return {"state": state, "counts": counts, "items": rows,
            "truncated": counts.get(state, 0) > LIMIT}


def _take(db: Session, org, lead_id: str, new_owner_id: str, actor_id: str, how: str, allow_reassign: bool):
    from app.models.models import Lead
    from app.routers.audit_log_router import log_action
    from app.services.energy_suppliers import _lock
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org.id).first()
    if lead is None or not is_request(db, org.id, lead_id):
        raise LookupError("Not an open new request in this workspace.")
    lead = _lock(db, lead)
    if not is_request(db, org.id, lead_id):          # closed while we waited for the lock
        raise LookupError("Not an open new request in this workspace.")
    if lead.assigned_to_id == new_owner_id:
        return lead, False
    if lead.assigned_to_id and not allow_reassign:
        owner = member(db, org.id, lead.assigned_to_id)
        raise Conflict("Already claimed by %s." % (owner["name"] if owner else "someone else"))
    before = lead.assigned_to_id
    lead.assigned_to_id = new_owner_id
    for t in _first_contact_tasks(db, org.id, lead.id):
        t.assigned_to_id = new_owner_id
    log_action(db, org.id, actor_id, action="energy.request_" + how, target_type="lead", target_id=lead.id,
               details={"from": before, "to": new_owner_id}, commit=False)
    return lead, True


def claim_request(db: Session, org, user, lead_id: str) -> Dict[str, Any]:
    if not can_take(db, org.id, user.id):
        raise PermissionError("You are not set up to take new requests. Ask a manager.")
    lead, changed = _take(db, org, lead_id, user.id, user.id, "claimed", allow_reassign=False)
    return {"lead_id": lead.id, "assigned_to_id": lead.assigned_to_id, "changed": changed}


def assign_request(db: Session, org, manager, lead_id: str, user_id: str) -> Dict[str, Any]:
    from app.models.models import Notification, NotificationType
    data = load(db, org.id)
    if not can_take(db, org.id, user_id, data):
        raise ValueError("That person cannot be assigned requests (not on the team, inactive, or switched off).")
    lead, changed = _take(db, org, lead_id, user_id, manager.id, "assigned", allow_reassign=True)
    if changed and user_id != manager.id:
        db.add(Notification(user_id=user_id, lead_id=lead.id, type=NotificationType.DEMO_REQUEST,
                            message="%s assigned you a new request: %s."
                                    % (manager.full_name or manager.email, _lead_name(lead)),
                            link="/leads/%s?tab=overview" % lead.id))
    return {"lead_id": lead.id, "assigned_to_id": lead.assigned_to_id, "changed": changed}


# ── Settings -> Team & Alerts ───────────────────────────────────────────────
def team_view(db: Session, org_id: str, viewer_role: str) -> Dict[str, Any]:
    from app.models.energy_models import EnergyRequestAlert
    data = load(db, org_id)
    team = people(db, org_id)
    rows = [dict(p, **flags(p, data)) for p in team]
    dflt = default_advisor(db, org_id, data)
    recent = []
    for e in (db.query(EnergyRequestAlert).filter(EnergyRequestAlert.organization_id == org_id)
              .order_by(EnergyRequestAlert.created_at.desc()).limit(5).all()):
        d = _json(e.details)
        em = d.get("email") or {}
        recent.append({"at": d.get("at"), "origin": d.get("origin"), "lead_id": d.get("lead_id"),
                       "in_app": len(d.get("in_app") or []), "email_state": em.get("state"),
                       "email_sent": em.get("sent", 0), "email_failed": em.get("failed", 0)})
    return {
        "members": rows,
        "default_advisor_id": dflt,
        "default_advisor": next((p["name"] for p in team if p["user_id"] == dflt), None),
        "default_advisor_note": ("New requests go straight to this person." if dflt else
                                 "No default advisor: new requests wait in New Requests for the team to claim."),
        "email": email_status(db, org_id, data),
        "recent_alerts": recent,
        "can_invite": viewer_role == "org_admin",
        "rules": ["Alerts and assignment are separate: several people can be alerted, one advisor owns the customer.",
                  "One request sends one alert - a retry or repeat form does not alert again.",
                  "Email goes only to this team, and only after the shared mailbox is verified."],
    }


def _json(raw) -> Dict[str, Any]:
    try:
        d = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        d = {}
    return d if isinstance(d, dict) else {}


def update_member(db: Session, org_id: str, actor_id: str, user_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    p = member(db, org_id, user_id)
    if p is None:
        raise LookupError("That person is not an active member of this team.")
    data = load(db, org_id, lock=True)
    cur = flags(p, data)
    for k in ("in_app", "email", "assignable"):
        if k in changes and changes[k] is not None:
            cur[k] = bool(changes[k])
    data["members"][user_id] = cur
    note = None
    if not cur["assignable"] and data.get("default_advisor_id") == user_id:
        data["default_advisor_id"] = None
        note = "%s was the default advisor; new requests now wait in New Requests to be claimed." % p["name"]
    save(db, org_id, data, actor_id)
    return {"member": dict(p, **cur), "note": note}


def set_default(db: Session, org_id: str, actor_id: str, user_id: Optional[str]) -> Dict[str, Any]:
    data = load(db, org_id, lock=True)
    if user_id and not can_take(db, org_id, user_id, data):
        raise ValueError("The default advisor must be an active team member who can be assigned requests.")
    data["default_advisor_id"] = user_id or None
    save(db, org_id, data, actor_id)
    return {"default_advisor_id": user_id or None}
