"""VOLT consumer leads (DEV-4): intake, assignment, status history, reports.

Authorization lives here, not in the router. Every read, write, search and
report goes through `scope()`, which is the ONLY place a lead query is built:

    owner (god_admin)                       all leads of the tenant
    org staff WITH an active owner grant    all leads of the tenant
    org staff WITHOUT a grant               only leads assigned to them
    viewers, ACA applicant logins, others   nothing (403)

Another tenant's leads are never reachable: every query filters on the
organization id from the path, which the router's guard already validated.

Personal data: lead events, audit rows, log lines and public responses never
contain names, phone numbers, emails or ZIP codes.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.volt_leads_models import VoltLead, VoltLeadEvent, VoltLeadGrant, VoltLeadSettings
from app.services.volt import common as C

# ── content that needs compliance review before real traffic ────────────────
CONSENT_VERSION = "2026-11-draft-1"
CONSENT_TEXT = (
    "I agree that a licensed agent working with VOLT may contact me by phone, text message or email "
    "about health coverage options, including with an automated dialer. Consent is not required to "
    "get coverage or help. Message and data rates may apply. I can withdraw consent at any time."
)
ELIGIBILITY_CAVEAT = (
    "Some people qualify for plans with a $0 monthly premium after financial assistance. That does not "
    "mean all healthcare is free, and not everyone qualifies. Eligibility depends on location, household "
    "income, household size and other coverage. This is not a government website."
)
# Shown on the staff screen: everything the owner must clear before turning intake on.
REVIEW_CHECKLIST = [
    "Privacy notice and consent wording (CONSENT_TEXT, CONSENT_VERSION) approved by compliance",
    "$0-premium / savings eligibility claims approved by compliance",
    "Agent routing and the licensed-agent list confirmed",
    "TCPA / state telemarketing rules checked for the call and text consent",
]

AGE_RANGES = ("18-25", "26-44", "45-64", "65+")
COVERAGE = ("none", "have_compare", "not_sure")
SOURCES = ("facebook", "google", "direct", "other")
STATUSES = ("new", "contacted", "qualified", "appointment_set", "enrolled", "not_interested")
STATUS_LABELS = {"new": "New", "contacted": "Contacted", "qualified": "Qualified",
                 "appointment_set": "Appointment Set", "enrolled": "Enrolled", "not_interested": "Not Interested"}
MAX_PAGE = 200
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
_CAMPAIGN = re.compile(r"[^A-Za-z0-9._\- ]")


# ── settings ────────────────────────────────────────────────────────────────

def settings_row(db: Session, org_id: str) -> VoltLeadSettings:
    row = db.get(VoltLeadSettings, org_id)
    if row is None:
        row = VoltLeadSettings(organization_id=org_id, intake_enabled=False, updated_at=datetime.utcnow())
        db.add(row)
        db.flush()
    return row


def public_config(db: Session, org_id: str) -> Dict[str, Any]:
    row = db.get(VoltLeadSettings, org_id)
    return {"enabled": bool(row and row.intake_enabled), "consent_version": CONSENT_VERSION,
            "consent_text": CONSENT_TEXT, "eligibility_caveat": ELIGIBILITY_CAVEAT}


def set_intake(db: Session, org_id: str, actor: User, body: Dict[str, Any]) -> Dict[str, Any]:
    """Owner only (checked by the caller). Turning intake ON needs a review note."""
    row = settings_row(db, org_id)
    enable = bool(body.get("intake_enabled"))
    note = str(body.get("review_note") or "").strip()[:1000]
    if enable and (body.get("reviewed") is not True or len(note) < 10):
        raise HTTPException(status_code=422, detail="Confirm the compliance review and record who approved it before turning intake on.")
    old = row.intake_enabled
    row.intake_enabled = enable
    if enable:
        row.review_note, row.reviewed_by, row.reviewed_at = note, actor.id, datetime.utcnow()
    row.updated_at = datetime.utcnow()
    C.audit(db, org_id, actor, "lead_settings", org_id, "intake_enabled", "intake_enabled", old, enable, note or None)
    return settings_dict(row)


def settings_dict(row: VoltLeadSettings) -> Dict[str, Any]:
    return {"intake_enabled": bool(row.intake_enabled), "review_note": row.review_note,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            "consent_version": CONSENT_VERSION, "checklist": REVIEW_CHECKLIST}


# ── intake ──────────────────────────────────────────────────────────────────

def _bad(field: str, msg: str):
    raise HTTPException(status_code=422, detail={"field": field, "message": msg})


def _clean_campaign(v) -> Optional[str]:
    s = _CAMPAIGN.sub("", str(v or "")).strip()[:80]
    return s or None


def _norm_phone(v) -> Optional[str]:
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 and d[0] in "23456789" else None


def validate(body: Dict[str, Any]) -> Dict[str, Any]:
    first = str(body.get("first_name") or "").strip()
    if not 1 <= len(first) <= 60:
        _bad("first_name", "Enter your first name.")
    last = str(body.get("last_name") or "").strip()[:60] or None
    zip_code = str(body.get("zip") or "").strip()
    if not re.fullmatch(r"\d{5}", zip_code):
        _bad("zip", "Enter a valid 5-digit ZIP code.")
    phone = _norm_phone(body.get("phone")) if body.get("phone") else None
    if body.get("phone") and phone is None:
        _bad("phone", "Enter a valid 10-digit phone number.")
    email = str(body.get("email") or "").strip().lower() or None
    if email and not _EMAIL.match(email):
        _bad("email", "Enter a valid email address.")
    if not phone and not email:
        _bad("phone", "Enter a phone number or an email address so an agent can reach you.")
    age = body.get("age_range")
    if age not in AGE_RANGES:
        _bad("age_range", "Choose an age range.")
    cov = body.get("coverage_status")
    if cov not in COVERAGE:
        _bad("coverage_status", "Choose the answer that best describes you.")
    hh = body.get("household_size")
    if hh in (None, ""):
        hh = None
    else:
        try:
            hh = int(hh)
        except (TypeError, ValueError):
            _bad("household_size", "Household size must be a number.")
        if not 1 <= hh <= 20:
            _bad("household_size", "Household size must be between 1 and 20.")
    if body.get("consent") is not True:
        _bad("consent", "Please agree to be contacted so an agent can help you.")
    if body.get("consent_version") != CONSENT_VERSION:
        _bad("consent_version", "The consent wording changed. Reload the page and review it again.")
    source = str(body.get("source") or "direct").strip().lower()
    return {"first_name": first, "last_name": last, "zip_code": zip_code, "phone": phone, "email": email,
            "age_range": age, "coverage_status": cov, "household_size": hh,
            "help_wanted": body.get("help_wanted") is not False,
            "source": source if source in SOURCES else "other", "campaign": _clean_campaign(body.get("campaign"))}


def _event(db, lead, actor, action, old=None, new=None, note=None):
    db.add(VoltLeadEvent(lead_id=lead.id, organization_id=lead.organization_id,
                         actor_user_id=getattr(actor, "id", None), action=action,
                         old_value=None if old is None else str(old), new_value=None if new is None else str(new),
                         note=note))


def submit(db: Session, org_id: str, body: Dict[str, Any]) -> None:
    """Public intake. Same silent outcome for new and repeat submissions: the
    caller learns nothing about whether a person is already on file."""
    cfg = db.get(VoltLeadSettings, org_id)
    if cfg is None or not cfg.intake_enabled:
        raise HTTPException(status_code=503, detail="This form is not accepting requests right now.")
    v = validate(body)
    ident = v["phone"] or v["email"]
    key = hashlib.sha256(("%s|%s" % (org_id, ident)).encode()).hexdigest()
    existing = db.query(VoltLead).filter(VoltLead.organization_id == org_id, VoltLead.dedup_key == key).first()
    if existing is None:
        now = datetime.utcnow()
        lead = VoltLead(organization_id=org_id, dedup_key=key, consent_version=CONSENT_VERSION,
                        consent_text_hash=hashlib.sha256(CONSENT_TEXT.encode()).hexdigest(), consent_at=now,
                        status="new", created_at=now, updated_at=now, **v)
        try:
            with db.begin_nested():
                db.add(lead)
                db.flush()
        except IntegrityError:           # a concurrent retry won the race
            existing = db.query(VoltLead).filter(VoltLead.organization_id == org_id, VoltLead.dedup_key == key).first()
        else:
            _event(db, lead, None, "created", new=v["source"])
            return
    existing.submissions = (existing.submissions or 1) + 1
    existing.updated_at = datetime.utcnow()
    _event(db, existing, None, "repeat_submission")


# ── authorization ───────────────────────────────────────────────────────────

def has_grant(db: Session, org_id: str, user: User) -> bool:
    g = db.get(VoltLeadGrant, (org_id, user.id))
    return bool(g and g.active)


def access(db: Session, user: User, org_id: str) -> str:
    """'all' | 'assigned'. Raises 403 for everyone else."""
    base = C.staff_access(db, user, org_id)
    if base == "owner":
        return "all"
    if base == "staff":
        return "all" if has_grant(db, org_id, user) else "assigned"
    raise HTTPException(status_code=403, detail="VOLT consumer leads are for authorized agents.")


def scope(db: Session, user: User, org_id: str):
    mode = access(db, user, org_id)
    q = db.query(VoltLead).filter(VoltLead.organization_id == org_id)
    if mode != "all":
        q = q.filter(VoltLead.assigned_user_id == user.id)
    return q, mode


def _lead(db, user, org_id, lead_id) -> VoltLead:
    q, _ = scope(db, user, org_id)
    lead = q.filter(VoltLead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found.")       # same for "exists but not yours"
    return lead


def require_owner(db: Session, user: User, org_id: str) -> None:
    if C.staff_access(db, user, org_id) != "owner":
        raise HTTPException(status_code=403, detail="Only the VOLT owner can do this.")


# ── dashboard ───────────────────────────────────────────────────────────────

def _names(db, ids) -> Dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(ids)).all()}


def lead_dict(lead: VoltLead, names: Dict[str, str], detail: bool = False) -> Dict[str, Any]:
    out = {"id": lead.id, "name": ("%s %s" % (lead.first_name, lead.last_name or "")).strip(), "phone": lead.phone,
           "zip": lead.zip_code, "source": lead.source, "status": lead.status,
           "status_label": STATUS_LABELS.get(lead.status, lead.status), "assigned_user_id": lead.assigned_user_id,
           "assigned_to": names.get(lead.assigned_user_id) if lead.assigned_user_id else None,
           "created_at": lead.created_at.isoformat()}
    if detail:
        out.update(email=lead.email, age_range=lead.age_range, coverage_status=lead.coverage_status,
                   household_size=lead.household_size, help_wanted=lead.help_wanted, campaign=lead.campaign,
                   consent_version=lead.consent_version, consent_at=lead.consent_at.isoformat(),
                   submissions=lead.submissions)
    return out


def listing(db: Session, user: User, org_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    q, mode = scope(db, user, org_id)
    status, source = params.get("status") or "", params.get("source") or ""
    if status:
        q = q.filter(VoltLead.status == status)
    if source:
        q = q.filter(VoltLead.source == source)
    term = str(params.get("q") or "").strip()[:60]
    if term:
        like = "%" + term.lower().replace("%", "").replace("_", "") + "%"
        digits = re.sub(r"\D", "", term)
        conds = [VoltLead.first_name.ilike(like), VoltLead.last_name.ilike(like), VoltLead.zip_code.like(like)]
        if digits:
            conds.append(VoltLead.phone.like("%" + digits + "%"))
        q = q.filter(or_(*conds))
    try:
        limit = max(1, min(int(params.get("limit") or 100), MAX_PAGE))
    except (TypeError, ValueError):
        limit = 100
    rows = q.order_by(VoltLead.created_at.desc()).limit(limit).all()
    names = _names(db, [r.assigned_user_id for r in rows])
    return {"access": mode, "leads": [lead_dict(r, names) for r in rows]}


def detail(db: Session, user: User, org_id: str, lead_id: str) -> Dict[str, Any]:
    lead = _lead(db, user, org_id, lead_id)
    events = db.query(VoltLeadEvent).filter(VoltLeadEvent.lead_id == lead.id).order_by(VoltLeadEvent.created_at).all()
    names = _names(db, [lead.assigned_user_id] + [e.actor_user_id for e in events])
    out = lead_dict(lead, names, detail=True)
    out["history"] = [{"action": e.action, "old": e.old_value, "new": e.new_value, "note": e.note,
                       "by": names.get(e.actor_user_id) if e.actor_user_id else "Visitor",
                       "at": e.created_at.isoformat()} for e in events]
    return out


def update(db: Session, user: User, org_id: str, lead_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    lead = _lead(db, user, org_id, lead_id)
    status = body.get("status")
    if status is not None:
        if status not in STATUSES:
            _bad("status", "Unknown status.")
        if status != lead.status:
            _event(db, lead, user, "status", lead.status, status)
            lead.status = status
    note = str(body.get("note") or "").strip()[:1000]
    if note:
        _event(db, lead, user, "note", note=note)
    lead.updated_at = datetime.utcnow()
    db.flush()
    return detail(db, user, org_id, lead.id)


def assignable(db: Session, org_id: str) -> List[Dict[str, Any]]:
    """Org staff who can hold leads: owner-role people are not listed (they see all)."""
    from app.models.sales_models import Membership
    ids = [m.user_id for m in db.query(Membership).filter(
        Membership.scope_type == "customer_org", Membership.scope_id == org_id, Membership.is_active.is_(True),
        Membership.role.in_(C.STAFF_ROLES)).all()]
    users = db.query(User).filter(User.id.in_(ids), User.is_active.is_(True)).all() if ids else []
    return [{"id": u.id, "name": u.full_name or u.email} for u in users if C.staff_access(db, u, org_id) == "staff"]


def assign(db: Session, user: User, org_id: str, lead_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if access(db, user, org_id) != "all":
        raise HTTPException(status_code=403, detail="Assigning leads needs an owner grant.")
    lead = _lead(db, user, org_id, lead_id)
    target = body.get("user_id") or None
    if target is not None and target not in {a["id"] for a in assignable(db, org_id)}:
        _bad("user_id", "That person cannot be assigned leads.")
    if target != lead.assigned_user_id:
        _event(db, lead, user, "assigned", lead.assigned_user_id, target)
        lead.assigned_user_id = target
        lead.updated_at = datetime.utcnow()
    db.flush()
    return detail(db, user, org_id, lead.id)


def report(db: Session, user: User, org_id: str) -> Dict[str, Any]:
    q, mode = scope(db, user, org_id)
    rows = q.with_entities(VoltLead.status, VoltLead.source).all()
    by_status = {s: 0 for s in STATUSES}
    by_source: Dict[str, Dict[str, int]] = {}
    for st, src in rows:
        by_status[st] = by_status.get(st, 0) + 1
        b = by_source.setdefault(src, {"leads": 0, "enrolled": 0})
        b["leads"] += 1
        b["enrolled"] += 1 if st == "enrolled" else 0
    return {"access": mode, "total": len(rows), "by_status": by_status, "by_source": by_source}


# ── owner grants ────────────────────────────────────────────────────────────

def grants(db: Session, org_id: str) -> List[Dict[str, Any]]:
    rows = db.query(VoltLeadGrant).filter(VoltLeadGrant.organization_id == org_id, VoltLeadGrant.active.is_(True)).all()
    names = _names(db, [r.user_id for r in rows])
    return [{"user_id": r.user_id, "name": names.get(r.user_id)} for r in rows]


def set_grant(db: Session, org_id: str, actor: User, body: Dict[str, Any]) -> List[Dict[str, Any]]:
    uid = str(body.get("user_id") or "")
    if uid not in {a["id"] for a in assignable(db, org_id)}:
        _bad("user_id", "That person cannot be granted lead access.")
    active = body.get("active") is not False
    g = db.get(VoltLeadGrant, (org_id, uid))
    if g is None:
        db.add(VoltLeadGrant(organization_id=org_id, user_id=uid, granted_by=actor.id, active=active))
    else:
        g.active, g.granted_by = active, actor.id
    C.audit(db, org_id, actor, "lead_grant", uid, "granted" if active else "revoked")
    db.flush()
    return grants(db, org_id)
