"""What happens after an energy (Atlantis-class) website form is filed.

`customer_sites.record_inquiry` files the person (find-or-create, consent,
provenance). For an organization whose industry is "energy" this module then
makes the request WORKABLE by a person in that workspace:

  1. QUEUE.  A new website lead starts at tier `new_inquiry`, so it is listed on
     Rate Requests under New. (public_capture marks it `web_lead`, a tier Rate
     Requests never lists - the request was in the workspace and invisible on
     the screen advisors work from.) A lead somebody already advanced is never
     moved back.
  2. WHAT THEY ASKED FOR.  The form's request type is recorded on the lead as
     `request_kind` (rate_review / bill_review / advisor / move / commercial /
     partner) and, where it is knowable, the energy template's `segment`
     ("Residential" / "Commercial"). A partner referral has no segment: the
     partner is not the customer.
  3. FOLLOW-UP.  One open follow-up task "Contact website request" on the lead,
     assigned to the lead's owner - not a second one while one is still open.
  4. MOVES.  A move request also opens ONE Move Concierge record (status
     requested) with the services the visitor picked. Nothing is ordered from
     any vendor; a person works the checklist.
  5. ALERT.  An in-app notification to the lead's owner, else the site's
     default owner, else this workspace's active admins - never another
     tenant's users.

NOTHING IS SENT. No email, no text, no cadence, no supplier call, no quote.
Every step is best effort inside a savepoint: a request is never lost because
a follow-up record could not be written.

Provenance: steps 1 and 5 are adapted from PR #116 (fb4b7399, b041fa54),
which stays on HOLD and unmerged. Changes vs #116: segment values match the
energy template's select options; partner gets no segment; the alert's
rollback no longer references an unbound savepoint; tasks and moves are new.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

TASK_TITLE = "Contact website request"
TASK_SOURCE = "website"

# The site's "How can we help?" answers (public-sites/atlantis-light-and-power).
_KIND_BY_REQUEST = {
    "compare electricity options": "rate_review",
    "analyze my current bill": "bill_review",
    "i'm moving": "move",
    "commercial energy procurement": "commercial",
    "partner / referral program": "partner",
}
_SEGMENT_BY_KIND = {
    "rate_review": "Residential",
    "bill_review": "Residential",
    "move": "Residential",
    "commercial": "Commercial",
    "advisor": None,
    "partner": None,
}
# Move picker labels on the page -> Move Concierge service keys.
_MOVE_SERVICES = {
    "electricity": "electricity_start",
    "internet": "internet",
    "tv": "internet",
    "utility support": "water",
}
_COPY_FIELDS = ("service_area", "monthly_usage", "locations", "move_date",
                "services_requested", "request_type", "company")


def is_energy(org) -> bool:
    return (getattr(org, "industry", "") or "").strip().lower() == "energy"


def request_kind(payload: Dict[str, Any]) -> str:
    raw = str(payload.get("request_type") or "").strip().lower()
    if raw in _KIND_BY_REQUEST:
        return _KIND_BY_REQUEST[raw]
    if raw.startswith("partner"):
        return "partner"
    if "moving" in raw or "move" in raw:
        return "move"
    if payload.get("company") and payload.get("locations"):
        return "commercial"
    return "advisor"


def _cf(lead) -> Dict[str, Any]:
    try:
        d = json.loads(lead.custom_fields or "{}")
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def _parse_date(value) -> Optional[date]:
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def move_services(text: Any) -> List[str]:
    out: List[str] = []
    for part in str(text or "").split(","):
        key = _MOVE_SERVICES.get(part.strip().lower())
        if part.strip() and key is None:
            key = "other"
        if key and key not in out:
            out.append(key)
    return out


def _recipients(db: Session, *, lead, site, organization) -> List[str]:
    from app.models.models import User
    if lead.assigned_to_id:
        return [lead.assigned_to_id]
    if getattr(site, "default_owner_user_id", None):
        return [site.default_owner_user_id]
    return [r[0] for r in db.query(User.id).filter(
        User.organization_id == organization.id, User.role == "org_admin",
        User.is_active.isnot(False)).all()]


def _step(db: Session, name: str, fn) -> Optional[Any]:
    """Run one follow-up step in its own savepoint; never raise."""
    sp = db.begin_nested()
    try:
        out = fn()
        db.flush()
        sp.commit()
        return out
    except Exception:                                   # noqa: BLE001
        sp.rollback()
        log.exception("energy_site_intake: step %s failed", name)
        return None


def handle(db: Session, *, site, organization, result: Dict[str, Any],
           payload: Dict[str, Any]) -> Dict[str, Any]:
    """Make one filed website request workable. Returns what was done."""
    done: Dict[str, Any] = {"energy": False}
    if not is_energy(organization):
        return done
    lead_id = (result or {}).get("lead_id")
    if not lead_id:
        return done
    from app.models.models import Lead
    lead = db.query(Lead).filter(Lead.id == lead_id,
                                 Lead.organization_id == organization.id).first()
    if lead is None:
        return done
    done["energy"] = True
    kind = request_kind(payload)
    done["request_kind"] = kind

    def _queue():
        if (lead.tier or "") in ("", "web_lead"):
            lead.tier = "new_inquiry"
        cf = _cf(lead)
        cf["request_kind"] = kind
        seg = _SEGMENT_BY_KIND.get(kind)
        if seg and not cf.get("segment"):
            cf["segment"] = seg
        for key in _COPY_FIELDS:
            val = payload.get(key)
            if val not in (None, "") and not cf.get(key):
                cf[key] = str(val)[:300]
        zip_ = str(payload.get("service_area") or "").strip()
        if re.fullmatch(r"\d{5}", zip_) and not lead.zip_code:
            lead.zip_code = zip_
        lead.custom_fields = json.dumps(cf)
        return lead.tier
    done["tier"] = _step(db, "queue", _queue)

    def _task():
        from app.models.work_models import LeadTask
        open_task = db.query(LeadTask).filter(
            LeadTask.organization_id == organization.id, LeadTask.lead_id == lead.id,
            LeadTask.source == TASK_SOURCE, LeadTask.status == "open").first()
        if open_task is not None:
            return open_task.id
        what = (payload.get("request_type") or kind)
        t = LeadTask(organization_id=organization.id, lead_id=lead.id, title=TASK_TITLE,
                     details="Website request: %s. Nothing has been sent to this person." % what,
                     due_at=datetime.utcnow() + timedelta(hours=4), status="open",
                     assigned_to_id=lead.assigned_to_id or getattr(site, "default_owner_user_id", None),
                     source=TASK_SOURCE)
        db.add(t)
        db.flush()
        return t.id
    done["task_id"] = _step(db, "task", _task)

    if kind == "move":
        def _move():
            from app.models.energy_models import EnergyMoveRequest
            from app.routers.energy_ops_router import OPEN_MOVE_STATUSES, _default_checklist
            existing = db.query(EnergyMoveRequest).filter(
                EnergyMoveRequest.organization_id == organization.id,
                EnergyMoveRequest.lead_id == lead.id,
                EnergyMoveRequest.status.in_(OPEN_MOVE_STATUSES)).first()
            if existing is not None:
                _refresh_move(existing, payload)
                return existing.id
            services = move_services(payload.get("services_requested"))
            name = " ".join(x for x in (lead.first_name, lead.last_name) if x) or None
            m = EnergyMoveRequest(
                organization_id=organization.id, lead_id=lead.id, contact_name=name,
                move_date=_parse_date(payload.get("move_date")),
                to_address=(str(payload.get("service_area") or "").strip() or None),
                services=json.dumps(services), checklist=json.dumps(_default_checklist(services)),
                status="requested",
                assigned_to_id=lead.assigned_to_id or getattr(site, "default_owner_user_id", None),
                notes="Opened from the website move form.")
            db.add(m)
            db.flush()
            return m.id
        done["move_id"] = _step(db, "move", _move)

    def _alert():
        from app.models.models import Notification, NotificationType
        users = _recipients(db, lead=lead, site=site, organization=organization)
        name = " ".join(x for x in (lead.first_name, lead.last_name) if x) or "Someone"
        what = str(payload.get("request_type") or "").strip()
        msg = "Website request from %s%s - contact them." % (name, (": " + what[:80]) if what else "")
        if getattr(lead, "is_test", False):
            msg = "TEST record - " + msg
        for uid in users:
            db.add(Notification(user_id=uid, lead_id=lead.id, type=NotificationType.DEMO_REQUEST,
                                message=msg, link="/rate-requests?open=%s" % lead.id))
        return len(users)
    done["alerted"] = _step(db, "alert", _alert) or 0
    return done



def _refresh_move(m, payload: Dict[str, Any]) -> None:
    """The same customer sent the move form again while their move is open.
    A new move date replaces the old one (the customer knows their date) and a
    newly picked service is ADDED with an unticked checklist item. Nothing is
    removed and no ticked item is touched - an advisor's work is never undone
    by a website form. Each change is noted on the move."""
    from app.routers.energy_ops_router import SERVICES
    notes = []
    new_date = _parse_date(payload.get("move_date"))
    if new_date and new_date != m.move_date:
        notes.append("Customer re-sent the website form: move date %s -> %s."
                     % (m.move_date.isoformat() if m.move_date else "not given", new_date.isoformat()))
        m.move_date = new_date
    services = json.loads(m.services) if m.services else []
    checklist = json.loads(m.checklist) if m.checklist else []
    keys = {i.get("key") for i in checklist}
    added = [s for s in move_services(payload.get("services_requested")) if s not in services]
    for s in added:
        services.append(s)
        if "svc_" + s not in keys:
            checklist.insert(max(len(checklist) - 1, 0),
                             {"key": "svc_" + s, "label": "Arrange: %s" % SERVICES[s],
                              "done": False, "done_at": None, "done_by": None})
    if added:
        notes.append("Customer re-sent the website form and added: %s."
                     % ", ".join(SERVICES[s] for s in added))
        m.services = json.dumps(services)
        m.checklist = json.dumps(checklist)
    if notes:
        m.notes = ((m.notes + "\n") if m.notes else "") + " ".join(notes)
