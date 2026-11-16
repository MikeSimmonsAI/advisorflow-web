"""Who may do what in a service company's Service Operations, its settings,
its customers, their locations, and its team.

ROLES map onto the platform's existing scoped role system:
  * a workspace manager (org_admin / super_admin / manager membership, or the
    EvoSys owner standing in the workspace) is COMPANY ADMIN here;
  * everyone else needs a ServiceTeamMember row in this company, whose
    ops_role says what they may do. Identity is always the platform User.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.service_ops_models import (ServiceBuyerAccess, ServiceCustomer, ServiceJob, ServiceJobAssignment,
                                           ServiceLocation, ServiceOpsSettings, ServiceTeamMember)

ROLES = ("company_admin", "ops_manager", "dispatcher", "team_lead", "field_worker", "viewer")
ROLE_LABEL = {"company_admin": "Company Admin", "ops_manager": "Operations Manager",
              "dispatcher": "Dispatcher / Scheduler", "team_lead": "Team Lead",
              "field_worker": "Field Worker", "viewer": "Read-Only Viewer"}

# What each role may do. "work" (start/finish their own assigned jobs) is
# granted by being ASSIGNED, never by role alone.
PERMS: Dict[str, frozenset] = {
    "company_admin": frozenset({"settings", "team", "customers", "view_all", "schedule", "billing", "approve",
                                "sensitive", "requests", "reports", "buyers"}),
    "ops_manager": frozenset({"team", "customers", "view_all", "schedule", "billing", "approve", "sensitive",
                              "requests", "reports", "buyers"}),
    "dispatcher": frozenset({"customers", "view_all", "schedule", "sensitive", "requests"}),
    "team_lead": frozenset({"view_team", "approve_team"}),
    "field_worker": frozenset(),
    "viewer": frozenset({"view_all", "reports"}),
}

CATEGORIES = ("cleaning", "window_washing", "pest_control", "landscaping", "pressure_washing", "other")
CATEGORY_LABEL = {"cleaning": "Cleaning", "window_washing": "Window washing", "pest_control": "Pest control",
                  "landscaping": "Landscaping", "pressure_washing": "Pressure washing", "other": "Other"}
CONTACT_PREFS = ("email", "phone", "text", "any")
DEFAULT_TZ = "America/Chicago"


# ── small helpers ──────────────────────────────────────────────────────────
def jload(raw, default):
    if raw in (None, ""):
        return default
    try:
        v = json.loads(raw)
        return v if v is not None else default
    except (TypeError, ValueError):
        return default


def jdump(v) -> str:
    return json.dumps(v)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0).isoformat() + "Z"


def text(v, width: int = 2000) -> Optional[str]:
    s = (str(v) if v is not None else "").strip()
    return s[:width] or None


def valid_tz(name: Optional[str]) -> bool:
    if not name:
        return False
    try:
        ZoneInfo(str(name))
        return True
    except Exception:                                            # noqa: BLE001
        return False


def tz_or(name: Optional[str], fallback: str = DEFAULT_TZ) -> str:
    return name if valid_tz(name) else fallback


def money(v, field: str = "amount") -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        f = round(float(v), 2)
    except (TypeError, ValueError):
        raise HTTPException(422, "%s must be a number" % field)
    if f < 0:
        raise HTTPException(422, "%s can't be negative" % field)
    return f


def audit(db: Session, org_id: str, user: Optional[User], action: str, ttype: str, tid: str,
          details: Optional[dict] = None) -> None:
    """Material changes go to the platform's audit log. An audit hiccup must
    not undo the change itself."""
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, org_id, user.id if user else None, action, ttype, tid, details=details)
    except Exception:                                            # noqa: BLE001
        pass


# ── settings / entitlements ────────────────────────────────────────────────
def settings(db: Session, org_id: str) -> Optional[ServiceOpsSettings]:
    return db.query(ServiceOpsSettings).filter(ServiceOpsSettings.organization_id == org_id).first()


def settings_json(s: Optional[ServiceOpsSettings]) -> Dict[str, Any]:
    if s is None:
        return {"ops_enabled": False, "advanced_enabled": False, "buyer_portal_enabled": False,
                "ai_assist_enabled": False, "timezone": DEFAULT_TZ}
    return {"ops_enabled": s.ops_enabled, "advanced_enabled": s.advanced_enabled,
            "buyer_portal_enabled": s.buyer_portal_enabled, "ai_assist_enabled": s.ai_assist_enabled,
            "timezone": tz_or(s.timezone), "business_name": s.business_name, "billing_address": s.billing_address,
            "billing_email": s.billing_email, "billing_phone": s.billing_phone,
            "invoice_prefix": s.invoice_prefix, "estimate_prefix": s.estimate_prefix,
            "payment_terms_days": s.payment_terms_days, "payment_instructions": s.payment_instructions,
            "default_tax_rate": s.default_tax_rate}


ENTITLEMENT_FLAGS = ("ops_enabled", "advanced_enabled", "buyer_portal_enabled", "ai_assist_enabled")
BUSINESS_FIELDS = ("timezone", "business_name", "billing_address", "billing_email", "billing_phone",
                   "invoice_prefix", "estimate_prefix", "payment_terms_days", "payment_instructions",
                   "default_tax_rate")


def save_settings(db: Session, org_id: str, actor: User, data: Dict[str, Any], *, entitlements: bool) -> ServiceOpsSettings:
    """`entitlements=True` only for EvoSys (program admin / owner): switching
    features on is EvoSys's decision. Business details are the company's."""
    s = settings(db, org_id)
    if s is None:
        s = ServiceOpsSettings(organization_id=org_id)
        db.add(s)
    changed = []
    if entitlements:
        for f in ENTITLEMENT_FLAGS:
            if f in data:
                setattr(s, f, bool(data[f]))
                changed.append(f)
        if s.buyer_portal_enabled and not s.ops_enabled:
            raise HTTPException(422, "Switch on Service Operations before the buyer portal")
    for f in BUSINESS_FIELDS:
        if f not in data:
            continue
        v = data[f]
        if f == "timezone":
            if v and not valid_tz(v):
                raise HTTPException(422, "Unknown time zone")
            s.timezone = v or None
        elif f == "payment_terms_days":
            try:
                s.payment_terms_days = max(0, min(int(v), 365))
            except (TypeError, ValueError):
                raise HTTPException(422, "Payment terms must be a number of days")
        elif f == "default_tax_rate":
            r = money(v, "Tax rate") or 0.0
            if r > 30:
                raise HTTPException(422, "Tax rate looks wrong (over 30%)")
            s.default_tax_rate = r
        elif f in ("invoice_prefix", "estimate_prefix"):
            p = re.sub(r"[^A-Z0-9-]", "", str(v or "").upper())[:12]
            if not p:
                raise HTTPException(422, "Number prefix needs letters or digits")
            setattr(s, f, p)
        else:
            setattr(s, f, text(v, 2000))
        changed.append(f)
    s.updated_by_id = actor.id
    db.commit()
    audit(db, org_id, actor, "service_ops_settings_update", "service_ops_settings", s.id, {"fields": changed})
    return s


def company_tz(db: Session, org_id: str) -> str:
    s = settings(db, org_id)
    if s and valid_tz(s.timezone):
        return s.timezone
    try:
        from app.models.acquisition_models import AcquisitionProfile
        p = db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == org_id).first()
        if p is not None and valid_tz(p.timezone):
            return p.timezone
    except Exception:                                            # noqa: BLE001
        pass
    return DEFAULT_TZ


# ── the caller in a company ────────────────────────────────────────────────
class OpsCtx:
    def __init__(self, db: Session, user: User, org: Organization, role: str,
                 member: Optional[ServiceTeamMember], request=None, settings_row=None):
        self.db, self.user, self.org, self.org_id = db, user, org, org.id
        self.role, self.member, self.request = role, member, request
        self.settings = settings_row

    def can(self, perm: str) -> bool:
        return perm in PERMS.get(self.role, frozenset())

    def need(self, perm: str, what: str = "do that") -> None:
        if not self.can(perm):
            raise HTTPException(403, "Your role can't %s" % what)

    @property
    def member_id(self) -> Optional[str]:
        return self.member.id if self.member else None


def member_for(db: Session, org_id: str, user_id: str) -> Optional[ServiceTeamMember]:
    return (db.query(ServiceTeamMember)
            .filter(ServiceTeamMember.organization_id == org_id, ServiceTeamMember.user_id == user_id).first())


def resolve(db: Session, user: User, org: Organization, *, is_manager: bool, request=None) -> OpsCtx:
    """The caller's Service Operations role in this company, or 403/402.
    The feature must be switched on for the company (no row = off)."""
    s = settings(db, org.id)
    if s is None or not s.ops_enabled:
        raise HTTPException(402, "Service Operations is not switched on for this company")
    m = member_for(db, org.id, user.id)
    if is_manager:
        return OpsCtx(db, user, org, "company_admin", m if (m and m.active) else None, request, s)
    if m is None or not m.active:
        raise HTTPException(403, "You are not on this company's service team")
    return OpsCtx(db, user, org, m.ops_role if m.ops_role in ROLES else "viewer", m, request, s)


def is_ops_only_user(db: Session, org_id: str, user_id: str) -> bool:
    """A field-side or buyer login: kept OUT of the Acquisition (sales) portal.
    Workspace managers are never ops-only (the caller checks that first)."""
    m = member_for(db, org_id, user_id)
    if m is not None and m.active and m.ops_role in ("field_worker", "team_lead", "viewer", "dispatcher"):
        return True
    b = (db.query(ServiceBuyerAccess)
         .filter(ServiceBuyerAccess.organization_id == org_id, ServiceBuyerAccess.user_id == user_id).first())
    return b is not None


# ── customers ─────────────────────────────────────────────────────────────
CUSTOMER_FIELDS = ("name", "kind", "contact_name", "contact_email", "contact_phone", "contact_preference",
                   "billing_name", "billing_address", "billing_email", "notes")


def _apply_customer(c: ServiceCustomer, data: Dict[str, Any]) -> None:
    for f in CUSTOMER_FIELDS:
        if f not in data:
            continue
        v = data[f]
        if f == "name":
            v = text(v, 200)
            if not v:
                raise HTTPException(422, "A customer needs a name")
        elif f == "kind":
            v = v if v in ("business", "individual") else "business"
        elif f == "contact_preference":
            v = v if v in CONTACT_PREFS else None
        else:
            v = text(v, 2000)
        setattr(c, f, v)


def get_customer(db: Session, org_id: str, cid: str) -> ServiceCustomer:
    c = db.query(ServiceCustomer).filter(ServiceCustomer.organization_id == org_id, ServiceCustomer.id == cid).first()
    if c is None:
        raise HTTPException(404, "Customer not found")
    return c


def create_customer(ctx: OpsCtx, data: Dict[str, Any], *, source_opportunity_id: Optional[str] = None) -> ServiceCustomer:
    ctx.need("customers", "add customers")
    c = ServiceCustomer(organization_id=ctx.org_id, created_by_id=ctx.user.id, name="",
                        source_opportunity_id=source_opportunity_id)
    _apply_customer(c, dict(data, name=data.get("name")))
    ctx.db.add(c)
    ctx.db.flush()
    audit(ctx.db, ctx.org_id, ctx.user, "service_customer_create", "service_customer", c.id, {"name": c.name})
    return c


def update_customer(ctx: OpsCtx, cid: str, data: Dict[str, Any]) -> ServiceCustomer:
    ctx.need("customers", "edit customers")
    c = get_customer(ctx.db, ctx.org_id, cid)
    _apply_customer(c, data)
    if "status" in data:
        if data["status"] not in ("active", "archived"):
            raise HTTPException(422, "Unknown status")
        c.status = data["status"]
    ctx.db.commit()
    audit(ctx.db, ctx.org_id, ctx.user, "service_customer_update", "service_customer", c.id,
          {"fields": sorted(data.keys())})
    return c


def customer_json(c: ServiceCustomer, *, locations: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    out = {f: getattr(c, f) for f in CUSTOMER_FIELDS}
    out.update(id=c.id, status=c.status, source_opportunity_id=c.source_opportunity_id, created_at=iso(c.created_at))
    if locations is not None:
        out["locations"] = locations
    return out


def list_customers(ctx: OpsCtx, *, include_archived: bool = False) -> List[Dict[str, Any]]:
    ctx.need("view_all", "see every customer")
    q = ctx.db.query(ServiceCustomer).filter(ServiceCustomer.organization_id == ctx.org_id)
    if not include_archived:
        q = q.filter(ServiceCustomer.status == "active")
    rows = q.order_by(ServiceCustomer.name).limit(1000).all()
    locs = (ctx.db.query(ServiceLocation).filter(ServiceLocation.organization_id == ctx.org_id).all()) if rows else []
    by: Dict[str, List[Dict[str, Any]]] = {}
    for l in locs:
        by.setdefault(l.customer_id, []).append(location_json(ctx, l))
    return [customer_json(c, locations=by.get(c.id, [])) for c in rows]


# ── locations ─────────────────────────────────────────────────────────────
LOCATION_FIELDS = ("name", "address", "city", "state", "postal_code", "service_category", "facility_type",
                   "contact_name", "contact_phone", "contact_email", "contact_preference", "access_instructions",
                   "special_requirements", "standard_duration_min", "preferred_windows", "timezone",
                   "assigned_member_ids", "active")


def _clean_windows(v) -> List[Dict[str, Any]]:
    out = []
    for w in (v or [])[:14]:
        try:
            days = sorted({int(d) for d in (w.get("days") or []) if 0 <= int(d) <= 6})
            a, b = str(w.get("start", "")), str(w.get("end", ""))
            if not (re.fullmatch(r"\d\d:\d\d", a) and re.fullmatch(r"\d\d:\d\d", b) and a < b):
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(422, "Service windows need days and start/end like 08:00")
        out.append({"days": days, "start": a, "end": b})
    return out


def _member_ids(db: Session, org_id: str, ids: Iterable[str]) -> List[str]:
    ids = [str(i) for i in (ids or [])][:50]
    if not ids:
        return []
    ok = {m.id for m in db.query(ServiceTeamMember).filter(ServiceTeamMember.organization_id == org_id,
                                                           ServiceTeamMember.id.in_(ids)).all()}
    bad = [i for i in ids if i not in ok]
    if bad:
        raise HTTPException(422, "Unknown team member")
    return list(dict.fromkeys(ids))


def _apply_location(db: Session, org_id: str, l: ServiceLocation, data: Dict[str, Any]) -> None:
    for f in LOCATION_FIELDS:
        if f not in data:
            continue
        v = data[f]
        if f == "name":
            v = text(v, 200)
            if not v:
                raise HTTPException(422, "A location needs a name")
        elif f == "service_category":
            if v not in CATEGORIES:
                raise HTTPException(422, "Unknown service category")
        elif f == "contact_preference":
            v = v if v in CONTACT_PREFS else None
        elif f == "standard_duration_min":
            try:
                v = None if v in (None, "") else max(15, min(int(v), 24 * 60))
            except (TypeError, ValueError):
                raise HTTPException(422, "Duration must be minutes")
        elif f == "preferred_windows":
            v = jdump(_clean_windows(v))
        elif f == "timezone":
            if v and not valid_tz(v):
                raise HTTPException(422, "Unknown time zone")
            v = v or None
        elif f == "assigned_member_ids":
            v = jdump(_member_ids(db, org_id, v))
        elif f == "active":
            v = bool(v)
        else:
            v = text(v, 4000)
        setattr(l, f, v)


def get_location(db: Session, org_id: str, lid: str) -> ServiceLocation:
    l = db.query(ServiceLocation).filter(ServiceLocation.organization_id == org_id, ServiceLocation.id == lid).first()
    if l is None:
        raise HTTPException(404, "Location not found")
    return l


def create_location(ctx: OpsCtx, customer_id: str, data: Dict[str, Any]) -> ServiceLocation:
    ctx.need("customers", "add locations")
    c = get_customer(ctx.db, ctx.org_id, customer_id)
    l = ServiceLocation(organization_id=ctx.org_id, customer_id=c.id, name="", created_by_id=ctx.user.id,
                        service_category="other")
    _apply_location(ctx.db, ctx.org_id, l, dict(data, name=data.get("name")))
    ctx.db.add(l)
    ctx.db.flush()
    audit(ctx.db, ctx.org_id, ctx.user, "service_location_create", "service_location", l.id,
          {"customer_id": c.id, "name": l.name})
    return l


def update_location(ctx: OpsCtx, lid: str, data: Dict[str, Any]) -> ServiceLocation:
    ctx.need("customers", "edit locations")
    l = get_location(ctx.db, ctx.org_id, lid)
    _apply_location(ctx.db, ctx.org_id, l, data)
    ctx.db.commit()
    audit(ctx.db, ctx.org_id, ctx.user, "service_location_update", "service_location", l.id,
          {"fields": sorted(data.keys()), "access_changed": "access_instructions" in data})
    return l


def assigned_to_location(ctx: OpsCtx, l: ServiceLocation) -> bool:
    if not ctx.member_id:
        return False
    if ctx.member_id in jload(l.assigned_member_ids, []):
        return True
    # On any live job at this location.
    return (ctx.db.query(ServiceJobAssignment.id)
            .join(ServiceJob, ServiceJob.id == ServiceJobAssignment.job_id)
            .filter(ServiceJob.organization_id == ctx.org_id, ServiceJob.location_id == l.id,
                    ServiceJob.status.notin_(("cancelled", "approved")),
                    ServiceJobAssignment.member_id == ctx.member_id,
                    ServiceJobAssignment.removed_at.is_(None)).first() is not None)


def may_see_sensitive(ctx: OpsCtx, l: ServiceLocation) -> bool:
    return ctx.can("sensitive") or assigned_to_location(ctx, l)


def location_json(ctx: OpsCtx, l: ServiceLocation, *, sensitive: Optional[bool] = None) -> Dict[str, Any]:
    out = {f: getattr(l, f) for f in LOCATION_FIELDS if f not in ("preferred_windows", "assigned_member_ids",
                                                                  "access_instructions")}
    out.update(id=l.id, customer_id=l.customer_id, preferred_windows=jload(l.preferred_windows, []),
               assigned_member_ids=jload(l.assigned_member_ids, []),
               category_label=CATEGORY_LABEL.get(l.service_category, l.service_category),
               timezone=l.timezone or None)
    show = may_see_sensitive(ctx, l) if sensitive is None else sensitive
    out["has_access_instructions"] = bool(l.access_instructions)
    out["access_instructions"] = l.access_instructions if show else None
    return out


# ── team ──────────────────────────────────────────────────────────────────
def _clean_availability(v) -> Dict[str, List[str]]:
    out = {}
    for k, hours in (v or {}).items():
        try:
            d = int(k)
            a, b = hours
            if not (0 <= d <= 6 and re.fullmatch(r"\d\d:\d\d", a) and re.fullmatch(r"\d\d:\d\d", b) and a < b):
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(422, "Availability is weekday -> [start, end] like 08:00")
        out[str(d)] = [a, b]
    return out


def member_json(m: ServiceTeamMember, user: Optional[User]) -> Dict[str, Any]:
    return {"id": m.id, "user_id": m.user_id, "name": user.full_name if user else None,
            "email": user.email if user else None, "role": m.ops_role, "role_label": ROLE_LABEL.get(m.ops_role),
            "team_name": m.team_name, "active": m.active, "availability": jload(m.availability, {}),
            "has_signed_in": bool(user and user.last_login_at), "login_active": bool(user and user.is_active)}


def list_team(ctx: OpsCtx) -> List[Dict[str, Any]]:
    if not (ctx.can("team") or ctx.can("view_all") or ctx.can("schedule")):
        raise HTTPException(403, "Your role can't see the team")
    rows = ctx.db.query(ServiceTeamMember).filter(ServiceTeamMember.organization_id == ctx.org_id).all()
    users = {u.id: u for u in ctx.db.query(User).filter(User.id.in_([m.user_id for m in rows] or ["-"])).all()}
    return sorted((member_json(m, users.get(m.user_id)) for m in rows), key=lambda x: (not x["active"], x["name"] or ""))


def invite_member(ctx: OpsCtx, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create (or reuse) the person's platform login in THIS company and put
    them on the team. Returns a one-time setup link to hand over; nothing is
    emailed or texted (no automatic notifications)."""
    ctx.need("team", "invite team members")
    role = data.get("role") or "field_worker"
    if role not in ROLES:
        raise HTTPException(422, "Unknown role")
    if role == "company_admin" and ctx.role != "company_admin":
        raise HTTPException(403, "Only a company admin can add another company admin")
    email = (data.get("email") or "").strip().lower()
    name = text(data.get("full_name"), 160)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or not name:
        raise HTTPException(422, "A team member needs a name and a valid email")
    db = ctx.db
    user = db.query(User).filter(User.email == email).first()
    setup_url = None
    if user is not None and user.organization_id != ctx.org_id:
        raise HTTPException(409, "That email already belongs to a login outside this company")
    if user is None:
        from app.services import plan_limits
        plan_limits.require_capacity(db, ctx.org, plan_limits.LIMIT_USERS, adding=1)
        from app.services.auth_service import hash_password
        user = User(organization_id=ctx.org_id, email=email, full_name=name, role="advisor",
                    password_hash=hash_password(secrets.token_urlsafe(48)), must_change_password=True)
        db.add(user)
        db.flush()
    m = member_for(db, ctx.org_id, user.id)
    if m is None:
        m = ServiceTeamMember(organization_id=ctx.org_id, user_id=user.id, created_by_id=ctx.user.id)
        db.add(m)
    m.ops_role, m.active = role, True
    m.team_name = text(data.get("team_name"), 80)
    if "availability" in data:
        m.availability = jdump(_clean_availability(data.get("availability")))
    db.flush()
    if user.last_login_at is None or user.must_change_password:
        from app.services import staff_activation
        from app.models.staff_models import PURPOSE_SETUP
        _row, raw = staff_activation.issue(db, user, ctx.user, purpose=PURPOSE_SETUP)
        setup_url = staff_activation.activation_url(data.get("base_url"), raw)
    db.commit()
    audit(db, ctx.org_id, ctx.user, "service_team_invite", "service_team_member", m.id,
          {"email": email, "role": role})
    return dict(member_json(m, user), setup_url=setup_url)


def update_member(ctx: OpsCtx, mid: str, data: Dict[str, Any]) -> Dict[str, Any]:
    ctx.need("team", "change the team")
    m = (ctx.db.query(ServiceTeamMember)
         .filter(ServiceTeamMember.organization_id == ctx.org_id, ServiceTeamMember.id == mid).first())
    if m is None:
        raise HTTPException(404, "Team member not found")
    before = {"role": m.ops_role, "active": m.active}
    if "role" in data:
        if data["role"] not in ROLES:
            raise HTTPException(422, "Unknown role")
        if "company_admin" in (data["role"], m.ops_role) and ctx.role != "company_admin":
            raise HTTPException(403, "Only a company admin can change a company admin")
        m.ops_role = data["role"]
    if "active" in data:
        m.active = bool(data["active"])
    if "team_name" in data:
        m.team_name = text(data["team_name"], 80)
    if "availability" in data:
        m.availability = jdump(_clean_availability(data["availability"]))
    ctx.db.commit()
    user = ctx.db.query(User).filter(User.id == m.user_id).first()
    audit(ctx.db, ctx.org_id, ctx.user, "service_team_update", "service_team_member", m.id,
          {"before": before, "after": {"role": m.ops_role, "active": m.active}})
    return member_json(m, user)
