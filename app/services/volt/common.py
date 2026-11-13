"""VOLT - tenant resolution, staff authority, names, rep codes, audit.

AUTHORITY IS DECIDED HERE AND NOWHERE ELSE
------------------------------------------
    owner   god_admin - the platform owner's existing oversight of every tenant
    staff   an ACTIVE customer_org membership in the VOLT organization with
            role org_admin, manager or super_admin (granted through the
            existing God -> Manage Access flow; nothing new grants it)
    viewer  the same membership with role viewer or spectator: reads, never writes
    None    everybody else, including every applicant login

Nothing in a request body, query string or header can raise that answer: the
organization id in the path is only a REQUEST, re-checked against a real
membership on every call.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.volt_models import VoltAuditEvent, VoltProgram, VoltProject, VoltRep

FEATURE_KEY = "volt_recruiting"
DEFAULT_SLUG = "volt-marketing"
STAFF_ROLES = ("org_admin", "manager", "super_admin")
VIEW_ROLES = ("viewer", "spectator")

STATUSES = ("draft", "submitted", "needs_update", "approved", "started", "withdrawn")
STATUS_LABELS = {"draft": "Draft", "submitted": "Submitted", "needs_update": "Needs update",
                 "approved": "Approved", "started": "Started", "withdrawn": "Withdrawn / closed"}

REP_CODE_OFFSET = 1000      # serial 42 is shown as REP-1042


def now() -> datetime:
    return datetime.utcnow()


def iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if hasattr(dt, "hour"):
        return dt.replace(microsecond=0).isoformat() + "Z"
    return dt.isoformat()


def app_ref(app) -> Optional[str]:
    """The applicant-facing APPLICATION reference, e.g. VOLT-3F9A1C2E. Derived
    from the application's permanent id, so it never changes and never reveals
    the internal person id (REP-xxxx stays staff-side)."""
    aid = getattr(app, "id", None) or ""
    return ("VOLT-" + aid.replace("-", "")[:8].upper()) if aid else None


def rep_code(serial: Optional[int]) -> Optional[str]:
    return None if serial is None else "REP-%d" % (int(serial) + REP_CODE_OFFSET)


def serial_from_code(code: Any) -> Optional[int]:
    """'REP-1042' / 'rep1042' / '1042' -> 42. Anything else -> None."""
    m = re.fullmatch(r"\s*(?:rep[-\s]?)?(\d{1,9})\s*", str(code or ""), re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1)) - REP_CODE_OFFSET
    return n if n > 0 else None


_APOSTROPHES = re.compile(r"['’‘`ʼ]")


def normalize_name(value: Any) -> str:
    """Case, apostrophes and spacing never decide a match: D'Angelo == Dangelo."""
    s = _APOSTROPHES.sub("", str(value or "")).lower()
    s = re.sub(r"[-.,]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def display_name(rep: VoltRep) -> str:
    return ("%s %s" % (rep.first_name or "", rep.last_name or "")).strip()


def loads(raw, default):
    try:
        v = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        v = None
    return default if v is None else v


# ── tenants ──────────────────────────────────────────────────────────────────

def is_volt_org(org) -> bool:
    try:
        keys = json.loads(getattr(org, "enabled_features", None) or "null")
    except (TypeError, ValueError):
        return False
    return isinstance(keys, list) and FEATURE_KEY in keys


def program_by_slug(db: Session, slug: str) -> VoltProgram:
    prog = db.query(VoltProgram).filter(VoltProgram.slug == (slug or "").strip().lower()).first()
    org = db.get(Organization, prog.organization_id) if prog else None
    if prog is None or org is None or org.is_active is False or not is_volt_org(org):
        raise HTTPException(status_code=404, detail="This recruiting portal was not found.")
    return prog


def program_for_org(db: Session, org_id: str) -> VoltProgram:
    prog = db.get(VoltProgram, org_id)
    org = db.get(Organization, org_id) if prog else None
    if prog is None or org is None or org.is_active is False or not is_volt_org(org):
        raise HTTPException(status_code=404, detail="Not found.")
    return prog


# ── staff authority ──────────────────────────────────────────────────────────

def staff_access(db: Session, user: User, org_id: str) -> Optional[str]:
    if user is None or not getattr(user, "is_active", False):
        return None
    if getattr(user, "role", None) == "god_admin":
        return "owner"
    from app.services.volt import access as _access
    if _access.blocked(db, org_id, user):
        return None                     # suspended / removed in VOLT (People & Access)
    from app.models.volt_models import VoltApplicantAccess
    grant = db.get(VoltApplicantAccess, (org_id, user.id))
    if grant is not None:
        return "aca_applicants" if grant.active else None
    from app.services import workspace_access
    role = workspace_access.workspace_role(user, db, org_id)
    if role in STAFF_ROLES:
        return "staff"
    if role in VIEW_ROLES:
        return "viewer"
    return None


def require_staff(db: Session, user: User, org_id: str, write: bool = False, *, applicant_read: bool = False) -> str:
    access = staff_access(db, user, org_id)
    if access is None:
        # Same answer whether the tenant exists or not.
        raise HTTPException(status_code=403, detail="VOLT operations are for authorized recruiting staff.")
    program_for_org(db, org_id)
    if access == "aca_applicants" and (write or not applicant_read):
        raise HTTPException(status_code=403, detail="This login has ACA applicant access only.")
    if write and access == "viewer":
        raise HTTPException(status_code=403, detail="Your VOLT access is view-only.")
    return access


def contexts_for(db: Session, user: User) -> List[Dict[str, Any]]:
    """Program Access cards: one per VOLT tenant this login may operate."""
    if user is None or getattr(user, "role", None) == "volt_rep":
        return []
    progs = db.query(VoltProgram).all()
    out = []
    for p in progs:
        access = staff_access(db, user, p.organization_id)
        if not access:
            continue
        org = db.get(Organization, p.organization_id)
        if org is None or org.is_active is False or not is_volt_org(org):
            continue
        out.append({"type": "volt_ops", "organization_id": p.organization_id, "organization_name": org.name,
                    "slug": p.slug, "access": access, "path": "/volt-ops/%s" % p.organization_id,
                    "public_path": "/volt"})
    return out


# ── reps ─────────────────────────────────────────────────────────────────────

def rep_for_user(db: Session, prog: VoltProgram, user: User) -> Optional[VoltRep]:
    return (db.query(VoltRep).filter(VoltRep.organization_id == prog.organization_id,
                                     VoltRep.user_id == user.id).first())


def public_rep(rep: Optional[VoltRep]) -> Optional[Dict[str, Any]]:
    """The ONLY shape of another person a non-staff caller ever receives."""
    if rep is None:
        return None
    return {"rep_id": rep.serial, "rep_code": rep_code(rep.serial), "display_name": display_name(rep)}


def project_dict(p: VoltProject) -> Dict[str, Any]:
    return {"id": p.id, "key": p.key, "name": p.name, "short_name": p.short_name or p.name,
            "summary": p.summary, "icon": p.icon, "question": p.question, "status": p.status,
            "recruiting": p.status == "open", "sort_order": p.sort_order,
            "required_docs": loads(p.required_docs, [])}


def projects(db: Session, org_id: str) -> List[VoltProject]:
    return (db.query(VoltProject).filter(VoltProject.organization_id == org_id)
            .order_by(VoltProject.sort_order.asc(), VoltProject.name.asc()).all())


def project_by_key(db: Session, org_id: str, key: str) -> VoltProject:
    p = (db.query(VoltProject).filter(VoltProject.organization_id == org_id,
                                      VoltProject.key == (key or "").strip().lower()).first())
    if p is None:
        raise HTTPException(status_code=404, detail="That project was not found.")
    return p


def audit(db: Session, org_id: str, actor: Optional[User], entity_type: str, entity_id: Any, action: str,
          field: Optional[str] = None, old=None, new=None, reason: Optional[str] = None) -> None:
    db.add(VoltAuditEvent(organization_id=org_id, actor_user_id=getattr(actor, "id", None),
                          entity_type=entity_type, entity_id=str(entity_id), action=action, field=field,
                          old_value=None if old is None else str(old), new_value=None if new is None else str(new),
                          reason=reason))


def is_test_email(email: str) -> bool:
    e = (email or "").strip().lower()
    # "+volt-test" plus-addresses are Mike's own deliverable test inbox: a test
    # record (cleanup, reports) that is still emailed, for real mail checks.
    return (e.endswith("@example.test") or e.endswith(".example.test") or e.endswith("@volt-test.invalid")
            or "+volt-test" in e.split("@")[0])
