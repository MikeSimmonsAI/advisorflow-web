"""Atlantis residential field acquisition - what a rep records at a door.

A VISIT IS WHAT THE REP SAYS HAPPENED. Every visit is an audit event
(action `energy.field_visit`) with the outcome, the address the rep typed and
the rep's id. Nothing here verifies location - there is no GPS and no
evidence beyond the rep's own entry - and every count in the summary says so
(`basis`). A door attempted is never an account.

ONLY AN INTERESTED RESIDENT WHO SAID YES BECOMES A LEAD. Outcomes
`interested` / `appointment_requested` need a phone or email AND the
resident's permission to be contacted by an Atlantis advisor
(`permission_to_contact`). That permission is stored as the rep's record of a
verbal yes - it is NOT text-message marketing consent, so sms_consent stays
false and nothing is sent. The lead joins the normal Atlantis queue (Rate
Requests -> New, tier new_inquiry) with one "Contact field referral" task and
an in-app alert to the workspace admins, credited to the rep
(custom_fields.field_rep_id). A business the rep met becomes a commercial
opportunity (segment Commercial) for an advisor.

A person already in the workspace (same phone or email) is NOT duplicated:
the visit is linked to that lead and counted as `existing_customer`, not as a
new referral.

`do_not_contact`: the visit is recorded and, when the rep supplied a phone or
email that matches a lead in this workspace, that lead is set to DNC.

Duplicate protection: the same rep recording the same outcome at the same
address within 12 hours is answered with the earlier visit and not counted
twice.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

OUTCOMES = ("no_answer", "door_hanger_left", "declined", "interested",
            "appointment_requested", "do_not_contact", "not_eligible", "business_lead")
LEAD_OUTCOMES = ("interested", "appointment_requested", "business_lead")
ACTION = "energy.field_visit"
TASK_SOURCE = "field_referral"
DUP_WINDOW = timedelta(hours=12)
BASIS = ("Visits are what each rep recorded; location is not verified (no GPS). "
         "Enrollments are counted only from the Enroll action on Rate Requests.")


def _norm(s) -> str:
    return " ".join(str(s or "").lower().replace(",", " ").split())


def _visits_q(db, org_id, since=None, rep_id=None):
    from app.models.models import AuditLogEntry
    q = db.query(AuditLogEntry).filter(AuditLogEntry.organization_id == org_id,
                                       AuditLogEntry.action == ACTION)
    if since is not None:
        q = q.filter(AuditLogEntry.created_at >= since)
    if rep_id:
        q = q.filter(AuditLogEntry.actor_user_id == rep_id)
    return q


def _details(entry) -> Dict[str, Any]:
    try:
        d = json.loads(entry.details or "{}")
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def _find_person(db, org_id, phone, email):
    """Anyone in this workspace with this phone or email (case-insensitive).
    Used only to avoid duplicates - what the REP may see is decided by
    `visible` in record_visit, never by this lookup."""
    from sqlalchemy import func, or_
    from app.models.models import Lead
    from app.services.dedup_service import normalize_phone
    conds = []
    p = normalize_phone(phone or "") if phone else None
    if p:
        conds.append(Lead.phone == p)
        digits = "".join(ch for ch in str(phone) if ch.isdigit())
        if digits:
            conds.append(Lead.phone == digits)
    e = (email or "").strip().lower()
    if e:
        conds.append(func.lower(Lead.email) == e)
    if not conds:
        return None
    return db.query(Lead).filter(Lead.organization_id == org_id, or_(*conds)).first()


def record_visit(db, *, org, rep, data: Dict[str, Any], visible=None) -> Dict[str, Any]:
    """`visible(lead_id) -> bool` says whether the rep may see that lead (the
    router passes the rep's lead scope). A match the rep may not see is never
    revealed (no id, no "existing customer" result) and never changed by the
    rep - its owner gets an internal task instead."""
    from app.models.models import Lead, Notification, NotificationType, User
    from app.models.work_models import LeadTask
    from app.routers.audit_log_router import log_action
    from app.services.dedup_service import normalize_phone

    outcome = data.get("outcome")
    if outcome not in OUTCOMES:
        raise ValueError("Unknown outcome. Use one of: %s" % ", ".join(OUTCOMES))
    address = str(data.get("address") or "").strip()[:300]
    zip_code = str(data.get("zip_code") or "").strip()[:10]
    if not address:
        raise ValueError("Record the address you visited.")
    phone = str(data.get("phone") or "").strip()[:40]
    email = str(data.get("email") or "").strip().lower()[:200]
    now = datetime.utcnow()

    who = _norm(phone and "".join(ch for ch in phone if ch.isdigit())[-10:]) or _norm(email)
    # same rep, same address, same outcome (and, for a referral, the same
    # resident) within the window -> the earlier visit, not counted twice
    for e in _visits_q(db, org.id, since=now - DUP_WINDOW, rep_id=rep.id).all():
        d = _details(e)
        if d.get("outcome") == outcome and _norm(d.get("address")) == _norm(address) \
                and (outcome not in LEAD_OUTCOMES or d.get("who") == who):
            return {"visit_id": e.id, "duplicate": True, "lead_id": d.get("lead_id"),
                    "result": d.get("result")}

    def _owner_task(lead, title, detail):
        db.add(LeadTask(organization_id=org.id, lead_id=lead.id, title=title, details=detail,
                        due_at=now + timedelta(hours=24), status="open", source=TASK_SOURCE,
                        assigned_to_id=lead.assigned_to_id, created_by_id=rep.id))

    lead_id, result = None, "recorded"
    if outcome in LEAD_OUTCOMES:
        if not (phone or email):
            raise ValueError("A referral needs the resident's phone or email.")
        if data.get("permission_to_contact") is not True:
            raise ValueError("Only refer someone who said an Atlantis advisor may contact them.")
        commercial = outcome == "business_lead" or data.get("segment") == "commercial"
        if commercial and not str(data.get("company") or "").strip():
            raise ValueError("A business referral needs the business name.")
        existing = _find_person(db, org.id, phone, email)
        if existing is not None and (visible is None or visible(existing.id)):
            lead_id, result = existing.id, "existing_customer"
        elif existing is not None:
            # someone else's customer: tell its owner, reveal nothing to the rep
            _owner_task(existing, "Field rep reported interest",
                        "%s recorded '%s' at a door for this customer. Review and follow up."
                        % (rep.full_name or rep.email, outcome))
            result = "forwarded_to_owner"
        else:
            from app.services import lead_capacity, master_contacts
            lead_capacity.require_capacity_user_initiated(db, org, adding=1)
            cf = {
                "request_kind": "commercial" if commercial else "rate_review",
                "segment": "Commercial" if commercial else "Residential",
                "service_address": address, "service_area": zip_code or None,
                "field_rep_id": rep.id, "field_rep_name": rep.full_name or rep.email,
                "field_referred_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "contact_permission": {"given": True, "how": "verbal, recorded by field rep",
                                       "by": rep.id, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")},
            }
            if commercial:
                cf["company"] = str(data.get("company")).strip()[:160]
            if data.get("appointment_note"):
                cf["appointment_request"] = str(data["appointment_note"]).strip()[:300]
            cf = {k: v for k, v in cf.items() if v not in (None, "")}
            lead = Lead(organization_id=org.id,
                        first_name=str(data.get("first_name") or "").strip()[:80] or "Resident",
                        last_name=str(data.get("last_name") or "").strip()[:80] or None,
                        phone=normalize_phone(phone) if phone else None,
                        email=email or None, status="new", tier="new_inquiry",
                        source="Atlantis Field Referral",
                        source_detail="Field rep: %s" % (rep.full_name or rep.email),
                        street_address=address, zip_code=zip_code or None,
                        sms_consent=False, custom_fields=json.dumps(cf),
                        notes=(str(data.get("notes") or "").strip()[:1000] or None))
            db.add(lead)
            db.flush()
            master_contacts.record_lead(db, lead, source="Atlantis Field Referral",
                                        source_detail="Field rep entry",
                                        ingestion_path="energy_field.record_visit")
            db.add(LeadTask(organization_id=org.id, lead_id=lead.id, title="Contact field referral",
                            details="Referred at the door by %s (%s). Permission to contact: verbal, "
                                    "recorded by the rep. No text consent. Nothing has been sent."
                                    % (rep.full_name or rep.email, outcome),
                            due_at=now + timedelta(hours=24), status="open", source=TASK_SOURCE,
                            created_by_id=rep.id))
            admins = [r[0] for r in db.query(User.id).filter(
                User.organization_id == org.id, User.role == "org_admin",
                User.is_active.isnot(False)).all()]
            for uid in admins:
                db.add(Notification(user_id=uid, lead_id=lead.id, type=NotificationType.DEMO_REQUEST,
                                    message="Field referral from %s: %s - assign an advisor."
                                            % (rep.full_name or rep.email,
                                               "business" if commercial else "home"),
                                    link="/rate-requests?open=%s" % lead.id))
            lead_id, result = lead.id, "new_referral"
    elif outcome == "do_not_contact" and (phone or email):
        existing = _find_person(db, org.id, phone, email)
        if existing is not None and (visible is None or visible(existing.id)):
            existing.status = "dnc"
            lead_id, result = existing.id, "marked_dnc"
        elif existing is not None:
            # a rep cannot change someone else's customer; the owner reviews it
            _owner_task(existing, "Review do-not-contact request from the field",
                        "%s recorded that this person asked not to be contacted. Review and set DNC."
                        % (rep.full_name or rep.email))
            result = "forwarded_to_owner"

    entry = log_action(db, org.id, rep.id, action=ACTION, target_type="lead" if lead_id else "door",
                       target_id=lead_id or _norm(address)[:120],
                       details={"outcome": outcome, "address": address, "zip_code": zip_code or None,
                                "lead_id": lead_id, "result": result, "who": who or None,
                                "door_hanger": bool(data.get("door_hanger_left")) or outcome == "door_hanger_left"},
                       commit=False)
    db.commit()
    return {"visit_id": entry.id, "duplicate": False, "lead_id": lead_id, "result": result}


def summary(db, *, org_id, days: int, rep_id: Optional[str] = None) -> Dict[str, Any]:
    """Per-rep counts from what was recorded. `rep_id` limits to one rep."""
    from app.models.models import Lead, User
    since = datetime.utcnow() - timedelta(days=days)
    reps: Dict[str, Dict[str, Any]] = {}

    def row(uid):
        return reps.setdefault(uid, {"rep_id": uid, "visits": 0, "by_outcome": {o: 0 for o in OUTCOMES},
                                     "door_hangers": 0, "new_referrals": 0, "existing_customers": 0,
                                     "referrals_worked": 0, "handed_to_supplier": 0, "enrolled": 0})

    for e in _visits_q(db, org_id, since=since, rep_id=rep_id).all():
        d = _details(e)
        r = row(e.actor_user_id)
        r["visits"] += 1
        if d.get("outcome") in r["by_outcome"]:
            r["by_outcome"][d["outcome"]] += 1
        r["door_hangers"] += 1 if d.get("door_hanger") else 0
        r["new_referrals"] += 1 if d.get("result") == "new_referral" else 0
        r["existing_customers"] += 1 if d.get("result") == "existing_customer" else 0

    q = db.query(Lead).filter(Lead.organization_id == org_id,
                              Lead.custom_fields.like('%"field_rep_id"%'))
    for l in q.all():
        try:
            cf = json.loads(l.custom_fields or "{}")
        except (ValueError, TypeError):
            continue
        uid = cf.get("field_rep_id")
        if not uid or (rep_id and uid != rep_id):
            continue
        if str(cf.get("field_referred_at") or "") < since.strftime("%Y-%m-%dT%H:%M:%SZ"):
            continue                                       # same window as the visits
        r = row(uid)
        r["referrals_worked"] += 1 if (l.tier or "") not in ("", "new_inquiry", "web_lead") else 0
        r["handed_to_supplier"] += 1 if cf.get("supplier_handoffs") else 0
        r["enrolled"] += 1 if l.enrolled_at else 0
    names = {u.id: u.full_name or u.email for u in
             db.query(User).filter(User.id.in_(list(reps) or [""])).all()}
    out: List[Dict[str, Any]] = []
    for uid, r in reps.items():
        r["name"] = names.get(uid)
        r["referral_rate"] = (round(r["new_referrals"] / r["visits"], 3) if r["visits"] else None)
        out.append(r)
    out.sort(key=lambda r: (-r["new_referrals"], -r["visits"]))
    return {"days": days, "reps": out, "basis": BASIS}
