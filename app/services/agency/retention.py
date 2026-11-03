"""INSURANCE RETENTION — scope, renewal engine, queue, metrics (Issue #499).

Everything here is organization-scoped first, then role/location/assignment
scoped. The router never queries agency_renewals without going through
`renewals_q(ctx)` so a scope rule lives in exactly one place.

SCOPE (decided on the server; the UI only mirrors it)
  owner   - org_admin / super_admin / god_admin in this workspace: whole agency.
  manager - manager: renewals at the locations they belong to (UserLocation)
            plus anything assigned to them. No location membership = only their
            own assigned work (deny by default, never "everything").
  agent   - advisor (producer / service / retention staff): their assigned work,
            plus the UNASSIGNED shared queue at their locations when the agency
            has shared_queue on - so they can claim it.

DELIVERY. This module never calls a provider. A workflow step writes an event
with delivery_status="simulated" when the workflow's delivery_mode is
"simulated". delivery_mode "live" is refused until a sender is configured
through the platform's existing messaging stack - a queued message is never
presented as delivered.
"""
import csv
import io
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agency_models import AgencyAppointment, AgencyPolicy
from app.models.agency_retention_models import (AgencyClientProfile, AgencyRenewal, AgencyRenewalEnrollment,
                                                AgencyRenewalEvent, AgencyRenewalWorkflow,
                                                AgencyRetentionSettings, AgencyRetentionStaff)
from app.models.location_models import Location, UserLocation
from app.models.models import Lead, Organization, User

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

DEFAULT_TZ = "America/Chicago"
DEFAULT_OFFSETS = [60, 30, 14, 7]
OWNER_ROLES = ("org_admin", "super_admin", "god_admin")
MANAGER_ROLES = ("manager",)
OPEN_APPT = ("booked", "confirmed", "pending")
PRODUCTS = ("auto", "home", "renters", "condo", "business", "life", "landlord")
PRODUCT_LABELS = {"auto": "Auto", "home": "Home", "renters": "Renters", "condo": "Condo",
                  "business": "Business", "life": "Life", "landlord": "Landlord / Host"}

# Attention reasons, highest priority first. Label is what the queue shows.
REASONS = {
    "reply_premium": "Client replied with a premium concern",
    "reply_cancel": "Client mentioned cancelling or switching",
    "reply_review": "Client asked for a review",
    "reply_question": "Client replied - needs a person",
    "outreach_failed": "Outreach failed - alternate contact needed",
    "unassigned": "Unassigned service request",
    "follow_up_overdue": "Follow-up overdue",
    "review_not_booked": "Renewal approaching - review not booked",
    "annual_review_overdue": "Annual review overdue",
}
REASON_ORDER = list(REASONS.keys())
REPLY_REASONS = ("reply_premium", "reply_cancel", "reply_review", "reply_question")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(microsecond=0).isoformat() + "Z"
    return v.isoformat()


def jl(raw, default=None):
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def jd(v):
    return None if v is None else json.dumps(v)


def mask_policy(num: Optional[str]) -> Optional[str]:
    if not num:
        return None
    s = re.sub(r"\s", "", str(num))
    return ("•••" + s[-4:]) if len(s) > 4 else "•••"


def lead_name(lead: Lead) -> str:
    n = " ".join(p for p in ((lead.first_name or "").strip(), (lead.last_name or "").strip()) if p)
    return n or "Client"


def tzinfo_for(name: Optional[str]):
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


# ─────────────────────────────── settings / context ───────────────────────────────

def get_settings(db: Session, org_id: str) -> Optional[AgencyRetentionSettings]:
    return db.query(AgencyRetentionSettings).filter(AgencyRetentionSettings.organization_id == org_id).first()


def settings_dict(s: AgencyRetentionSettings, org: Organization) -> Dict[str, Any]:
    return {
        "enabled": bool(s.enabled),
        "display_name": s.display_name or org.name,
        "tagline": s.tagline or "Keep every renewal moving.",
        "timezone": s.timezone or DEFAULT_TZ,
        "default_offsets": jl(s.default_offsets, DEFAULT_OFFSETS),
        "send_window_start": s.send_window_start, "send_window_end": s.send_window_end,
        "shared_queue": bool(s.shared_queue), "routing_mode": s.routing_mode,
        "review_minutes": s.review_minutes, "data_mode": s.data_mode,
        "is_demo": bool(s.is_demo or getattr(org, "is_demo", False)),
        "reference": jl(s.reference, None),
    }


class RCtx:
    """The VIEWER whose scope applies. In a demo workspace an owner may preview
    as one of their staff (read-only): `actor` stays the real person, `user`
    becomes the previewed staff member, and every write is refused."""

    def __init__(self, db: Session, actor: User, user: User, role: str, org: Organization,
                 settings: AgencyRetentionSettings, request=None, preview: bool = False):
        self.db, self.actor, self.user, self.org, self.request = db, actor, user, org, request
        self.org_id = org.id
        self.settings = settings
        self.role_name = role
        self.kind = "owner" if role in OWNER_ROLES else "manager" if role in MANAGER_ROLES else "agent"
        self.preview = preview
        self.tz = tzinfo_for(settings.timezone)
        self.is_demo = bool(settings.is_demo or getattr(org, "is_demo", False))
        self._locs = None

    @property
    def owner(self) -> bool:
        return self.kind == "owner"

    @property
    def manager_plus(self) -> bool:
        return self.kind in ("owner", "manager")

    @property
    def loc_ids(self) -> List[str]:
        if self._locs is None:
            self._locs = [r[0] for r in self.db.query(UserLocation.location_id).filter(
                UserLocation.organization_id == self.org_id, UserLocation.user_id == self.user.id).all()]
        return self._locs

    def today(self) -> date:
        return datetime.now(self.tz).date()

    def require_write(self):
        if self.preview:
            raise HTTPException(403, "Preview is read-only. Return to your own view to make changes.")

    def require_manager(self):
        self.require_write()
        if not self.manager_plus:
            raise HTTPException(403, "Manager access required")

    def require_owner(self):
        self.require_write()
        if not self.owner:
            raise HTTPException(403, "Agency owner or admin access required")


# ─────────────────────────────── scoped queries ───────────────────────────────

def permitted_location_ids(ctx: RCtx) -> Optional[List[str]]:
    """None = every location of the agency (owner)."""
    return None if ctx.owner else list(ctx.loc_ids)


def renewals_q(ctx: RCtx):
    q = ctx.db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == ctx.org_id)
    if ctx.owner:
        return q
    me = ctx.user.id
    locs = ctx.loc_ids
    if ctx.kind == "manager":
        cond = [AgencyRenewal.assigned_user_id == me]
        if locs:
            cond.append(AgencyRenewal.location_id.in_(locs))
        return q.filter(or_(*cond))
    cond = [AgencyRenewal.assigned_user_id == me]
    if ctx.settings.shared_queue and locs:
        cond.append(and_(AgencyRenewal.assigned_user_id.is_(None), AgencyRenewal.location_id.in_(locs)))
    return q.filter(or_(*cond))


def visible_lead_ids_q(ctx: RCtx):
    """Households the viewer may open: any visible renewal, or (manager+) a client
    profile at a permitted location, or a client they service."""
    sub = renewals_q(ctx).with_entities(AgencyRenewal.lead_id)
    q = ctx.db.query(AgencyClientProfile.lead_id).filter(AgencyClientProfile.organization_id == ctx.org_id)
    if ctx.owner:
        return q
    cond = [AgencyClientProfile.lead_id.in_(sub), AgencyClientProfile.servicing_user_id == ctx.user.id]
    if ctx.kind == "manager" and ctx.loc_ids:
        cond.append(AgencyClientProfile.location_id.in_(ctx.loc_ids))
    return q.filter(or_(*cond))


def get_renewal(ctx: RCtx, renewal_id: str) -> AgencyRenewal:
    r = renewals_q(ctx).filter(AgencyRenewal.id == renewal_id).first()
    if r is None:
        raise HTTPException(404, "Renewal not found")
    return r


def get_client_lead(ctx: RCtx, lead_id: str) -> Lead:
    ok = visible_lead_ids_q(ctx).filter(AgencyClientProfile.lead_id == lead_id).first()
    if ok is None:
        raise HTTPException(404, "Client not found")
    lead = ctx.db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == ctx.org_id).first()
    if lead is None:
        raise HTTPException(404, "Client not found")
    return lead


def org_user(ctx: RCtx, user_id: str) -> User:
    """A staff member of THIS agency. Membership-based workspaces keep staff in
    the org via users.organization_id or a workspace membership; both are checked."""
    u = ctx.db.query(User).filter(User.id == user_id).first()
    if u is None or not is_org_staff(ctx.db, ctx.org_id, u):
        raise HTTPException(404, "Staff member not found")
    return u


def is_org_staff(db: Session, org_id: str, u: User) -> bool:
    if u.organization_id == org_id:
        return True
    st = db.query(AgencyRetentionStaff.id).filter(AgencyRetentionStaff.organization_id == org_id,
                                                   AgencyRetentionStaff.user_id == u.id).first()
    return st is not None


def location_in_scope(ctx: RCtx, location_id: Optional[str]) -> Optional[Location]:
    if not location_id:
        return None
    loc = ctx.db.query(Location).filter(Location.id == location_id, Location.organization_id == ctx.org_id).first()
    if loc is None:
        raise HTTPException(404, "Location not found")
    allowed = permitted_location_ids(ctx)
    if allowed is not None and loc.id not in allowed:
        raise HTTPException(404, "Location not found")
    return loc


# ─────────────────────────────── events / history ───────────────────────────────

def add_event(ctx_or_db, org_id: str, lead_id: str, kind: str, *, renewal_id=None, enrollment_id=None,
              direction="internal", channel=None, body=None, delivery_status=None, detail=None,
              key=None, actor=None, is_demo=False, at=None) -> Optional[AgencyRenewalEvent]:
    """Write one history row. With `key`, a second write of the same key is a no-op
    (duplicate webhook / retried step) and returns None."""
    db = ctx_or_db.db if hasattr(ctx_or_db, "db") else ctx_or_db
    if key:
        if db.query(AgencyRenewalEvent.id).filter(AgencyRenewalEvent.idempotency_key == key).first():
            return None
    ev = AgencyRenewalEvent(organization_id=org_id, lead_id=lead_id, renewal_id=renewal_id,
                            enrollment_id=enrollment_id, kind=kind, direction=direction, channel=channel,
                            body=body, delivery_status=delivery_status, detail=jd(detail),
                            idempotency_key=key, actor_user_id=actor, is_demo=is_demo, at=at or utcnow())
    if key:
        try:
            with db.begin_nested():
                db.add(ev)
                db.flush()
        except IntegrityError:
            return None
    else:
        db.add(ev)
    return ev


def event_dict(e: AgencyRenewalEvent, names: Dict[str, str]) -> Dict[str, Any]:
    return {"id": e.id, "kind": e.kind, "direction": e.direction, "channel": e.channel, "body": e.body,
            "delivery_status": e.delivery_status, "detail": jl(e.detail, None), "at": iso(e.at),
            "renewal_id": e.renewal_id, "handled": bool(e.handled),
            "actor": names.get(e.actor_user_id) if e.actor_user_id else None,
            "simulated": e.delivery_status == "simulated"}


def user_names(db: Session, ids: Iterable[str]) -> Dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(ids)).all()}


# ─────────────────────────────── reply classification ───────────────────────────────

_RULES = [
    ("opt_out", r"^\s*(stop|unsubscribe|cancel texts|no more)\b|^\s*alto\b"),
    ("reply_cancel", r"\bcancel|switch(ing)? (to|carrier|companies)|another (company|carrier)|cancelar|cambiar de"),
    ("reply_premium", r"\bpremium|price|rate (went|go(es)?) up|increase|went up|too (high|expensive)|expensive|cost|precio|caro|subi[oó]"),
    ("reply_review", r"\breview|appointment|meet|come in|call me|schedule|book|cita|revisi[oó]n|llamar"),
]


def classify_reply(text: str) -> Dict[str, Any]:
    """Keyword rules over the client's own words. The result is an INFERENCE and
    is labeled as one; the stored message text is the fact."""
    t = (text or "").lower()
    for code, pat in _RULES:
        m = re.search(pat, t)
        if m:
            return {"code": code, "matched": m.group(0).strip(), "inferred": True,
                    "method": "keyword rules"}
    return {"code": "reply_question", "matched": None, "inferred": True, "method": "keyword rules"}


# ─────────────────────────────── attention / queue ───────────────────────────────

def attention_for(ctx: RCtx, r: AgencyRenewal, policy: Optional[AgencyPolicy], has_review: bool,
                  today: date, now: datetime) -> Optional[Tuple[str, str]]:
    if r.outcome:
        return None
    if r.attention_reason in REASONS:
        return r.attention_reason, REASONS[r.attention_reason]
    if r.assigned_user_id is None:
        return "unassigned", REASONS["unassigned"]
    if r.next_action_at and r.next_action_at < now:
        return "follow_up_overdue", REASONS["follow_up_overdue"]
    if not has_review and r.renewal_date <= today + timedelta(days=14):
        return "review_not_booked", REASONS["review_not_booked"]
    if policy is not None and policy.annual_review_date and policy.annual_review_date < today and not has_review:
        return "annual_review_overdue", REASONS["annual_review_overdue"]
    return None


def _review_map(ctx: RCtx, renewals: List[AgencyRenewal]) -> Dict[str, AgencyAppointment]:
    ids = [r.review_appointment_id for r in renewals if r.review_appointment_id]
    if not ids:
        return {}
    rows = ctx.db.query(AgencyAppointment).filter(AgencyAppointment.organization_id == ctx.org_id,
                                                  AgencyAppointment.id.in_(ids)).all()
    return {a.id: a for a in rows}


def renewal_rows(ctx: RCtx, renewals: List[AgencyRenewal]) -> List[Dict[str, Any]]:
    if not renewals:
        return []
    db = ctx.db
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_({r.lead_id for r in renewals})).all()}
    pols = {p.id: p for p in db.query(AgencyPolicy).filter(AgencyPolicy.id.in_({r.policy_id for r in renewals})).all()}
    profs = {p.lead_id: p for p in db.query(AgencyClientProfile).filter(
        AgencyClientProfile.organization_id == ctx.org_id,
        AgencyClientProfile.lead_id.in_({r.lead_id for r in renewals})).all()}
    locs = {l.id: l for l in db.query(Location).filter(Location.organization_id == ctx.org_id).all()}
    names = user_names(db, [r.assigned_user_id for r in renewals])
    reviews = _review_map(ctx, renewals)
    today, now = ctx.today(), utcnow()
    out = []
    for r in renewals:
        lead, pol, prof = leads.get(r.lead_id), pols.get(r.policy_id), profs.get(r.lead_id)
        appt = reviews.get(r.review_appointment_id)
        has_review = appt is not None and appt.status in OPEN_APPT + ("completed",)
        att = attention_for(ctx, r, pol, has_review, today, now)
        loc = locs.get(r.location_id)
        out.append({
            "id": r.id, "lead_id": r.lead_id,
            "household": (prof.household_name if prof and prof.household_name else
                          ((lead.last_name + " Household") if lead and lead.last_name else lead_name(lead) if lead else "Client")),
            "client_name": lead_name(lead) if lead else None,
            "product": r.product, "product_label": PRODUCT_LABELS.get(r.product, (r.product or "").title()),
            "policy_masked": mask_policy(pol.policy_number) if pol else None,
            "policy_status": pol.status if pol else None,
            "renewal_date": iso(r.renewal_date), "days_to_renewal": (r.renewal_date - today).days,
            "workflow_status": r.workflow_status, "outcome": r.outcome,
            "attention": {"code": att[0], "label": att[1]} if att else None,
            "attention_detail": r.attention_detail,
            "assigned_user_id": r.assigned_user_id, "assigned_name": names.get(r.assigned_user_id),
            "assigned_at": iso(r.assigned_at), "assignment_reason": r.assignment_reason,
            "version": r.version,
            "location_id": r.location_id, "location_name": loc.name if loc else None,
            "next_action": r.next_action or ("Book renewal review" if att and att[0] == "review_not_booked" else None),
            "next_action_at": iso(r.next_action_at),
            "review": ({"id": appt.id, "starts_at": iso(appt.starts_at), "status": appt.status}
                       if appt else None),
            "preferred_language": prof.preferred_language if prof else None,
            "source": r.source, "source_updated_at": iso(r.source_updated_at), "is_demo": bool(r.is_demo),
        })
    return out


def _priority(row) -> Tuple:
    a = row["attention"]
    rank = REASON_ORDER.index(a["code"]) if a else 99
    return (rank, row["days_to_renewal"])


def apply_filters(ctx: RCtx, q, location_id=None, agent_id=None, product=None):
    if location_id:
        location_in_scope(ctx, location_id)
        q = q.filter(AgencyRenewal.location_id == location_id)
    if agent_id:
        if agent_id == "unassigned":
            q = q.filter(AgencyRenewal.assigned_user_id.is_(None))
        else:
            q = q.filter(AgencyRenewal.assigned_user_id == agent_id)
    if product:
        q = q.filter(AgencyRenewal.product == product)
    return q


def list_renewals(ctx: RCtx, view: str = "all", location_id=None, agent_id=None, product=None,
                  days: int = 30, limit: int = 200) -> Dict[str, Any]:
    """view: due | attention | booked | confirmed | lost | all | mine | unassigned"""
    today = ctx.today()
    q = apply_filters(ctx, renewals_q(ctx), location_id, agent_id, product)
    if view == "due":
        q = q.filter(AgencyRenewal.outcome.is_(None), AgencyRenewal.renewal_date >= today,
                     AgencyRenewal.renewal_date <= today + timedelta(days=days))
    elif view == "confirmed":
        q = q.filter(AgencyRenewal.outcome == "confirmed")
    elif view == "lost":
        q = q.filter(AgencyRenewal.outcome == "lost")
    elif view == "booked":
        q = q.filter(AgencyRenewal.outcome.is_(None), AgencyRenewal.review_appointment_id.isnot(None))
    elif view == "mine":
        q = q.filter(AgencyRenewal.assigned_user_id == ctx.user.id, AgencyRenewal.outcome.is_(None))
    elif view == "unassigned":
        q = q.filter(AgencyRenewal.assigned_user_id.is_(None), AgencyRenewal.outcome.is_(None))
    elif view == "attention":
        q = q.filter(AgencyRenewal.outcome.is_(None))
    rows = renewal_rows(ctx, q.order_by(AgencyRenewal.renewal_date.asc()).limit(2000).all())
    if view == "attention":
        rows = [r for r in rows if r["attention"]]
    if view == "booked":
        rows = [r for r in rows if r["review"] and r["review"]["status"] in OPEN_APPT]
    if view in ("attention", "due", "mine", "unassigned", "all"):
        rows.sort(key=_priority)
    return {"items": rows[:limit], "total": len(rows),
            "households": len({r["lead_id"] for r in rows})}


# ─────────────────────────────── today / metrics ───────────────────────────────

def today_view(ctx: RCtx, location_id=None, agent_id=None, days: int = 30) -> Dict[str, Any]:
    today = ctx.today()
    base = apply_filters(ctx, renewals_q(ctx), location_id, agent_id)
    open_rows = renewal_rows(ctx, base.filter(AgencyRenewal.outcome.is_(None)).all())
    due = [r for r in open_rows if 0 <= r["days_to_renewal"] <= days]
    attention = [r for r in open_rows if r["attention"]]
    booked = [r for r in open_rows if r["review"] and r["review"]["status"] in OPEN_APPT]
    since = utcnow() - timedelta(days=30)
    confirmed = base.filter(AgencyRenewal.outcome == "confirmed", AgencyRenewal.outcome_at >= since).all()
    queue = sorted(attention, key=_priority)[:8]
    hh = lambda rows: len({r["lead_id"] if isinstance(r, dict) else r.lead_id for r in rows})  # noqa: E731
    metrics = [
        {"key": "due", "label": "Renewals due in 30 days" if days == 30 else "Renewals due in %d days" % days,
         "value": hh(due), "policies": len(due),
         "basis": "Households with an unresolved policy renewal dated today through %s." % (today + timedelta(days=days)).isoformat()},
        {"key": "attention", "label": "Clients needing attention", "value": hh(attention), "policies": len(attention),
         "basis": "Households with an open renewal that has a reply, failure, overdue follow-up, no owner, or no review booked within 14 days."},
        {"key": "booked", "label": "Reviews booked", "value": hh(booked), "policies": len(booked),
         "basis": "Households with a scheduled review on an open renewal. A booked review does not mark a renewal confirmed."},
        {"key": "confirmed", "label": "Renewals confirmed", "value": hh(confirmed), "policies": len(confirmed),
         "basis": "Households with a renewal outcome explicitly recorded as confirmed in the last 30 days."},
    ]
    return {"metrics": metrics, "queue": queue, "assistant": assistant_status(ctx, location_id, agent_id),
            "workload": workload(ctx, location_id), "appointments": todays_appointments(ctx, location_id, agent_id),
            "today": today.isoformat(), "viewer": viewer_dict(ctx)}


def viewer_dict(ctx: RCtx) -> Dict[str, Any]:
    return {"user_id": ctx.user.id, "name": ctx.user.full_name or ctx.user.email, "kind": ctx.kind,
            "role": ctx.role_name, "location_ids": permitted_location_ids(ctx), "preview": ctx.preview,
            "actor_name": ctx.actor.full_name or ctx.actor.email}


def assistant_status(ctx: RCtx, location_id=None, agent_id=None) -> Dict[str, Any]:
    db = ctx.db
    vis = apply_filters(ctx, renewals_q(ctx), location_id, agent_id).with_entities(AgencyRenewal.id)
    wfs = db.query(AgencyRenewalWorkflow).filter(AgencyRenewalWorkflow.organization_id == ctx.org_id).all()
    evq = db.query(AgencyRenewalEvent).filter(AgencyRenewalEvent.organization_id == ctx.org_id,
                                              AgencyRenewalEvent.renewal_id.in_(vis))
    recent = evq.order_by(AgencyRenewalEvent.at.desc()).limit(6).all()
    needs_person = evq.filter(AgencyRenewalEvent.kind == "reply", AgencyRenewalEvent.handled.is_(False)).count()
    failures = evq.filter(AgencyRenewalEvent.delivery_status == "failed").count()
    nxt = db.query(func.min(AgencyRenewalEnrollment.next_run_at)).filter(
        AgencyRenewalEnrollment.organization_id == ctx.org_id, AgencyRenewalEnrollment.state == "active",
        AgencyRenewalEnrollment.renewal_id.in_(vis),
        AgencyRenewalEnrollment.workflow_id.in_([w.id for w in wfs if w.state == "running"] or ["-"])).scalar()
    running = [w for w in wfs if w.state == "running"]
    blocked = None
    if not wfs:
        blocked = "No renewal workflow has been set up yet."
    elif not running:
        blocked = "All renewal workflows are paused."
    names = user_names(db, [e.actor_user_id for e in recent])
    lmap = {l.id: l for l in db.query(Lead).filter(Lead.id.in_({e.lead_id for e in recent} or {"-"})).all()}
    acts = []
    for e in recent:
        d = event_dict(e, names)
        d["client"] = lead_name(lmap[e.lead_id]) if e.lead_id in lmap else None
        d["summary"] = summarize_event(e, d["client"], names)
        acts.append(d)
    return {"state": "running" if running else ("paused" if wfs else "not_set_up"),
            "workflows": [{"id": w.id, "name": w.name, "state": w.state, "delivery_mode": w.delivery_mode,
                           "last_run_at": iso(w.last_run_at)} for w in wfs],
            "last_activity": acts[0] if acts else None, "activity": acts,
            "next_scheduled_at": iso(nxt), "replies_needing_person": needs_person,
            "delivery_failures": failures, "blocked_reason": blocked,
            "delivery_note": "Simulated delivery - no message is sent to anyone." if all(
                w.delivery_mode == "simulated" for w in wfs) else None}


def summarize_event(e: AgencyRenewalEvent, client: Optional[str], names: Dict[str, str]) -> str:
    c = client or "client"
    d = jl(e.detail, {}) or {}
    if e.kind == "outreach":
        st = {"simulated": "Simulated", "failed": "Failed", "queued": "Queued", "delivered": "Delivered"}.get(
            e.delivery_status, "Recorded")
        return "%s renewal reminder to %s (%s)" % (st, c, e.channel or "message")
    if e.kind == "reply":
        return "Reply from %s - %s" % (c, REASONS.get((d.get("classification") or {}).get("code"), "needs a person"))
    if e.kind == "review_booked":
        return "Review booked for %s" % c
    if e.kind in ("assigned", "reassigned", "claimed"):
        return "%s %s to %s" % (c, "claimed by" if e.kind == "claimed" else "assigned", d.get("to_name") or "staff")
    if e.kind == "outcome":
        return "Renewal %s for %s" % (d.get("outcome"), c)
    if e.kind == "enrolled":
        return "%s enrolled in %s" % (c, d.get("workflow") or "renewal workflow")
    return "%s - %s" % (e.kind.replace("_", " ").capitalize(), c)


def workload(ctx: RCtx, location_id=None) -> List[Dict[str, Any]]:
    db = ctx.db
    allowed = permitted_location_ids(ctx)
    locs = db.query(Location).filter(Location.organization_id == ctx.org_id, Location.is_active.is_(True))
    if allowed is not None:
        locs = locs.filter(Location.id.in_(allowed or ["-"]))
    if location_id:
        locs = locs.filter(Location.id == location_id)
    out = []
    today = ctx.today()
    for loc in locs.order_by(Location.is_primary.desc(), Location.name).all():
        q = renewals_q(ctx).filter(AgencyRenewal.location_id == loc.id, AgencyRenewal.outcome.is_(None),
                                   AgencyRenewal.renewal_date <= today + timedelta(days=60))
        open_n = q.count()
        staff_ids = [r[0] for r in db.query(UserLocation.user_id).filter(UserLocation.location_id == loc.id).all()]
        cap = sum((s.review_capacity or 0) for s in db.query(AgencyRetentionStaff).filter(
            AgencyRetentionStaff.organization_id == ctx.org_id, AgencyRetentionStaff.user_id.in_(staff_ids or ["-"]),
            AgencyRetentionStaff.available.is_(True)).all())
        out.append({"location_id": loc.id, "location": loc.name, "open": open_n, "capacity": cap or None,
                    "staff": len(staff_ids), "pct": round(100 * open_n / cap) if cap else None,
                    "verified": not ((loc.notes or "").lower().startswith("illustrative"))})
    return out


def todays_appointments(ctx: RCtx, location_id=None, agent_id=None, day: Optional[date] = None) -> List[Dict[str, Any]]:
    day = day or ctx.today()
    start = datetime.combine(day, time.min, tzinfo=ctx.tz).astimezone(timezone.utc).replace(tzinfo=None)
    end = start + timedelta(days=1)
    return list_appointments(ctx, start, end, location_id, agent_id)


def list_appointments(ctx: RCtx, start: datetime, end: datetime, location_id=None, agent_id=None):
    db = ctx.db
    vis_leads = visible_lead_ids_q(ctx)
    q = db.query(AgencyAppointment).filter(AgencyAppointment.organization_id == ctx.org_id,
                                           AgencyAppointment.starts_at >= start, AgencyAppointment.starts_at < end)
    if not ctx.owner:
        q = q.filter(or_(AgencyAppointment.agent_user_id == ctx.user.id, AgencyAppointment.lead_id.in_(vis_leads)))
    if agent_id:
        q = q.filter(AgencyAppointment.agent_user_id == agent_id)
    rows = q.order_by(AgencyAppointment.starts_at).all()
    profs = {p.lead_id: p for p in db.query(AgencyClientProfile).filter(
        AgencyClientProfile.organization_id == ctx.org_id,
        AgencyClientProfile.lead_id.in_({a.lead_id for a in rows} or {"-"})).all()}
    if location_id:
        rows = [a for a in rows if profs.get(a.lead_id) and profs[a.lead_id].location_id == location_id]
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_({a.lead_id for a in rows} or {"-"})).all()}
    locs = {l.id: l.name for l in db.query(Location).filter(Location.organization_id == ctx.org_id).all()}
    names = user_names(db, [a.agent_user_id for a in rows])
    rmap = {r.review_appointment_id: r for r in ctx.db.query(AgencyRenewal).filter(
        AgencyRenewal.organization_id == ctx.org_id,
        AgencyRenewal.review_appointment_id.in_([a.id for a in rows] or ["-"])).all()}
    out = []
    for a in rows:
        p = profs.get(a.lead_id)
        r = rmap.get(a.id)
        out.append({"id": a.id, "lead_id": a.lead_id, "starts_at": iso(a.starts_at), "status": a.status,
                    "type": a.type, "medium": a.medium, "notes": a.notes,
                    "title": "Renewal review" if a.type == "review" else (a.type or "Appointment").replace("_", " ").title(),
                    "household": (p.household_name if p and p.household_name else lead_name(leads[a.lead_id]) if a.lead_id in leads else None),
                    "product_label": PRODUCT_LABELS.get(r.product) if r else None, "renewal_id": r.id if r else None,
                    "location": locs.get(p.location_id) if p else None,
                    "agent_user_id": a.agent_user_id, "agent_name": names.get(a.agent_user_id),
                    "is_demo": bool(a.is_demo)})
    return out


# ─────────────────────────────── renewal engine ───────────────────────────────

def _parse_hm(s: str) -> time:
    h, m = (s or "08:30").split(":")
    return time(int(h), int(m))


def location_tz(ctx_db: Session, settings: AgencyRetentionSettings, location_id: Optional[str]):
    if location_id:
        loc = ctx_db.query(Location).filter(Location.id == location_id).first()
        if loc is not None and loc.timezone:
            return tzinfo_for(loc.timezone)
    return tzinfo_for(settings.timezone)


def next_allowed_send(at_utc: datetime, tz, start_s: str, end_s: str) -> datetime:
    """The first moment >= at_utc that falls inside the recipient location's
    local send window. Naive UTC in, naive UTC out."""
    start, end = _parse_hm(start_s), _parse_hm(end_s)
    local = at_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    if start <= local.time() <= end:
        return at_utc
    day = local.date() if local.time() < start else local.date() + timedelta(days=1)
    nxt = datetime.combine(day, start, tzinfo=tz)
    return nxt.astimezone(timezone.utc).replace(tzinfo=None)


def in_send_window(at_utc: datetime, tz, start_s: str, end_s: str) -> bool:
    local = at_utc.replace(tzinfo=timezone.utc).astimezone(tz).time()
    return _parse_hm(start_s) <= local <= _parse_hm(end_s)


def _step_time(renewal_date: date, offset: int, tz) -> datetime:
    local = datetime.combine(renewal_date - timedelta(days=offset), time(10, 0), tzinfo=tz)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def exclusions_for(ctx: RCtx, wf: AgencyRenewalWorkflow, r: AgencyRenewal, lead: Lead, pol: Optional[AgencyPolicy],
                   prof: Optional[AgencyClientProfile], enrolled: set, today: date) -> List[str]:
    reasons = []
    if r.outcome:
        reasons.append("Outcome already recorded (%s)" % r.outcome)
    if r.renewal_date < today:
        reasons.append("Renewal date has passed")
    elif (r.renewal_date - today).days > wf.window_days:
        reasons.append("Renewal is more than %d days away" % wf.window_days)
    if pol is not None and pol.status not in ("in_force", "active"):
        reasons.append("Policy status is %s" % pol.status)
    if (lead.status or "").lower() == "dnc" or (prof is not None and not prof.contact_ok):
        reasons.append("Client opted out of contact")
    if wf.channel == "sms" and not lead.phone:
        reasons.append("No mobile number on file")
    if wf.channel == "email" and not lead.email:
        reasons.append("No email on file")
    if r.id in enrolled:
        reasons.append("Already enrolled in this workflow")
    prods = jl(wf.products, None)
    if prods and r.product not in prods:
        reasons.append("Product not included in this workflow")
    locs = jl(wf.location_ids, None)
    if locs and r.location_id not in locs:
        reasons.append("Location not included in this workflow")
    return reasons


def preview_workflow(ctx: RCtx, wf: AgencyRenewalWorkflow) -> Dict[str, Any]:
    today = ctx.today()
    rs = renewals_q(ctx).filter(AgencyRenewal.renewal_date >= today - timedelta(days=1),
                                AgencyRenewal.renewal_date <= today + timedelta(days=max(wf.window_days, 1) + 30)).all()
    enrolled = {e[0] for e in ctx.db.query(AgencyRenewalEnrollment.renewal_id).filter(
        AgencyRenewalEnrollment.workflow_id == wf.id).all()}
    leads = {l.id: l for l in ctx.db.query(Lead).filter(Lead.id.in_({r.lead_id for r in rs} or {"-"})).all()}
    pols = {p.id: p for p in ctx.db.query(AgencyPolicy).filter(AgencyPolicy.id.in_({r.policy_id for r in rs} or {"-"})).all()}
    profs = {p.lead_id: p for p in ctx.db.query(AgencyClientProfile).filter(
        AgencyClientProfile.organization_id == ctx.org_id).all()}
    rows = renewal_rows(ctx, rs)
    eligible, excluded = [], []
    for r, row in zip(rs, rows):
        lead = leads.get(r.lead_id)
        if lead is None:
            continue
        why = exclusions_for(ctx, wf, r, lead, pols.get(r.policy_id), profs.get(r.lead_id), enrolled, today)
        item = {k: row[k] for k in ("id", "lead_id", "household", "product_label", "renewal_date",
                                    "days_to_renewal", "assigned_name", "location_name", "preferred_language")}
        if why:
            item["reasons"] = why
            excluded.append(item)
        else:
            eligible.append(item)
    reason_counts: Dict[str, int] = {}
    for x in excluded:
        for w in x["reasons"]:
            reason_counts[w] = reason_counts.get(w, 0) + 1
    return {"eligible": eligible, "excluded": excluded, "eligible_count": len(eligible),
            "excluded_count": len(excluded), "exclusion_summary": reason_counts,
            "sample_message": render_template(wf, rows[0] if rows else None, ctx)}


def render_template(wf: AgencyRenewalWorkflow, row: Optional[Dict[str, Any]], ctx: RCtx,
                    language: Optional[str] = None) -> Optional[str]:
    """Personalised only from VERIFIED record fields. No prices, quotes or savings."""
    lang = language or (row or {}).get("preferred_language") or "en"
    tpl = (wf.template_es if lang == "es" and wf.template_es else wf.template) or DEFAULT_TEMPLATE
    s = settings_dict(ctx.settings, ctx.org)
    fields = {
        "first_name": ((row or {}).get("client_name") or "there").split(" ")[0],
        "product": ((row or {}).get("product_label") or "policy").lower(),
        "renewal_date": (row or {}).get("renewal_date") or "",
        "agency": s["display_name"], "agent": ((row or {}).get("assigned_name") or "our team").split(" ")[0],
    }
    try:
        return tpl.format(**fields)
    except (KeyError, IndexError, ValueError):
        return tpl


DEFAULT_TEMPLATE = ("Hi {first_name}, this is {agent} with {agency}. Your {product} policy renews on "
                    "{renewal_date}. Would you like a quick review before then? Reply with a good time.")
DEFAULT_TEMPLATE_ES = ("Hola {first_name}, le saluda {agent} de {agency}. Su póliza de {product} se renueva el "
                       "{renewal_date}. ¿Le gustaría una revisión rápida antes? Responda con un buen horario.")


def start_workflow(ctx: RCtx, wf: AgencyRenewalWorkflow) -> Dict[str, Any]:
    if wf.delivery_mode != "simulated":
        raise HTTPException(409, "Live delivery is not configured for this workspace. "
                                 "Workflows run in simulated mode until a sender is approved and connected.")
    pv = preview_workflow(ctx, wf)
    offsets = sorted(jl(wf.offsets, DEFAULT_OFFSETS), reverse=True)
    created = 0
    now = utcnow()
    for item in pv["eligible"]:
        r = ctx.db.query(AgencyRenewal).filter(AgencyRenewal.id == item["id"],
                                               AgencyRenewal.organization_id == ctx.org_id).first()
        tz = location_tz(ctx.db, ctx.settings, r.location_id)
        days_left = (r.renewal_date - ctx.today()).days
        # Steps whose day has already passed collapse into the next applicable one.
        pending = [o for o in offsets if o <= max(days_left, 0)] or [0]
        first = pending[0]
        run_at = max(_step_time(r.renewal_date, first, tz), now)
        run_at = next_allowed_send(run_at, tz, ctx.settings.send_window_start, ctx.settings.send_window_end)
        en = AgencyRenewalEnrollment(organization_id=ctx.org_id, workflow_id=wf.id, renewal_id=r.id,
                                     state="active", steps_done=jd([o for o in offsets if o > first]),
                                     next_offset=first, next_run_at=run_at, is_demo=r.is_demo)
        try:
            with ctx.db.begin_nested():
                ctx.db.add(en)
                ctx.db.flush()
        except IntegrityError:
            continue   # duplicate enrollment - the unique constraint is the guarantee
        created += 1
        if r.workflow_status == "not_started":
            r.workflow_status = "in_outreach"
        add_event(ctx, ctx.org_id, r.lead_id, "enrolled", renewal_id=r.id, enrollment_id=en.id,
                  detail={"workflow": wf.name, "first_step_days_before": first}, actor=ctx.actor.id,
                  is_demo=r.is_demo)
    wf.state = "running"
    return {"enrolled": created, "excluded": pv["excluded_count"], "state": wf.state}


def run_due(db: Session, org_id: str, settings: AgencyRetentionSettings, *, now: Optional[datetime] = None,
            workflow_id: Optional[str] = None, renewal_ids: Optional[List[str]] = None,
            force_now: bool = False, actor_id: Optional[str] = None) -> Dict[str, int]:
    """Process due steps. Idempotent per (enrollment, offset) through the event
    idempotency key, so a retried run or two overlapping workers never produce
    a second message. `force_now` (demo only) treats every active enrollment's
    next step as due - used by the presenter's "run next step" control."""
    now = now or utcnow()
    q = db.query(AgencyRenewalEnrollment).join(
        AgencyRenewalWorkflow, AgencyRenewalWorkflow.id == AgencyRenewalEnrollment.workflow_id).filter(
        AgencyRenewalEnrollment.organization_id == org_id, AgencyRenewalEnrollment.state == "active",
        AgencyRenewalWorkflow.state == "running")
    if workflow_id:
        q = q.filter(AgencyRenewalEnrollment.workflow_id == workflow_id)
    if renewal_ids is not None:
        q = q.filter(AgencyRenewalEnrollment.renewal_id.in_(renewal_ids or ["-"]))
    if not force_now:
        q = q.filter(AgencyRenewalEnrollment.next_run_at <= now)
    stats = {"processed": 0, "simulated": 0, "failed": 0, "deferred_quiet_hours": 0, "skipped_duplicate": 0,
             "stopped": 0}
    wfs = {}
    for en in q.all():
        wf = wfs.get(en.workflow_id) or db.query(AgencyRenewalWorkflow).get(en.workflow_id)
        wfs[wf.id] = wf
        r = db.query(AgencyRenewal).filter(AgencyRenewal.id == en.renewal_id).first()
        if r is None or r.outcome:
            en.state = "completed" if r is not None else "stopped"
            stats["stopped"] += 1
            continue
        lead = db.query(Lead).filter(Lead.id == r.lead_id).first()
        prof = db.query(AgencyClientProfile).filter(AgencyClientProfile.lead_id == r.lead_id).first()
        if (lead.status or "").lower() == "dnc" or (prof is not None and not prof.contact_ok):
            en.state, en.paused_reason = "stopped", "opted_out"
            stats["stopped"] += 1
            continue
        tz = location_tz(db, settings, r.location_id)
        send_at = now
        if not in_send_window(send_at, tz, settings.send_window_start, settings.send_window_end):
            en.next_run_at = next_allowed_send(send_at, tz, settings.send_window_start, settings.send_window_end)
            stats["deferred_quiet_hours"] += 1
            if not force_now:
                continue
            # The presenter asked for it now; quiet hours still apply - simulated
            # delivery is recorded at the next allowed moment, never inside them.
            send_at = en.next_run_at
        offset = en.next_offset if en.next_offset is not None else 0
        key = "step:%s:%s" % (en.id, offset)
        lang = (prof.preferred_language if prof else None) or "en"
        channel = wf.channel
        failed = channel == "sms" and not (lead.phone or "").strip() or (lead.phone or "").startswith("+1555000")
        row = {"client_name": lead_name(lead), "product_label": PRODUCT_LABELS.get(r.product, r.product),
               "renewal_date": r.renewal_date.isoformat(), "preferred_language": lang,
               "assigned_name": (user_names(db, [r.assigned_user_id]).get(r.assigned_user_id)
                                 if r.assigned_user_id else None)}
        fake_ctx = type("C", (), {"settings": settings, "org": db.query(Organization).get(org_id)})()
        body = render_template(wf, row, fake_ctx, lang)
        status = "failed" if failed else ("simulated" if wf.delivery_mode == "simulated" else "queued")
        ev = add_event(db, org_id, r.lead_id, "outreach", renewal_id=r.id, enrollment_id=en.id, direction="out",
                       channel=channel, body=body, delivery_status=status, key=key,
                       detail={"days_before_renewal": offset, "language": lang,
                               "failure": "Number on file is not a working mobile number" if failed else None},
                       actor=actor_id, is_demo=r.is_demo, at=send_at)
        if ev is None:
            stats["skipped_duplicate"] += 1
        else:
            stats["processed"] += 1
            stats["failed" if failed else "simulated"] += 1
        done = jl(en.steps_done, []) or []
        if offset not in done:
            done.append(offset)
        en.steps_done = jd(done)
        en.attempts = (en.attempts or 0) + 1
        offsets = sorted(jl(wf.offsets, DEFAULT_OFFSETS), reverse=True)
        remaining = [o for o in offsets if o < offset and o not in done]
        r.last_activity_at = send_at
        if failed:
            en.state, en.paused_reason = "needs_human", "delivery_failed"
            r.attention_reason = "outreach_failed"
            r.attention_detail = "The %s to %s could not be delivered. Try another contact method." % (
                channel, lead_name(lead))
            r.workflow_status = "needs_attention"
            r.next_action, r.next_action_at = "Find an alternate contact", send_at
        elif remaining:
            en.next_offset = remaining[0]
            en.next_run_at = next_allowed_send(max(_step_time(r.renewal_date, remaining[0], tz), send_at),
                                               tz, settings.send_window_start, settings.send_window_end)
            r.workflow_status = "in_outreach"
            r.next_action, r.next_action_at = "Await reply", en.next_run_at
        else:
            en.state, en.next_run_at, en.next_offset = "completed", None, None
            r.workflow_status = "in_outreach"
            r.next_action, r.next_action_at = "Call client - no reply to reminders", send_at + timedelta(days=1)
        wf.last_run_at = now
    return stats


def record_reply(db: Session, org_id: str, settings: AgencyRetentionSettings, r: AgencyRenewal, text: str, *,
                 channel: str = "sms", key: Optional[str] = None, simulated: bool = True,
                 actor_id: Optional[str] = None) -> Optional[AgencyRenewalEvent]:
    """An inbound reply. Untrusted text: it is stored, classified by rules and
    routed - it can never change configuration or authorize anything. Pauses
    automation for the record until a person handles it."""
    cls = classify_reply(text)
    ev = add_event(db, org_id, r.lead_id, "reply", renewal_id=r.id, direction="in", channel=channel, body=text,
                   delivery_status="simulated" if simulated else "delivered", key=key,
                   detail={"classification": cls, "simulated": simulated}, actor=actor_id, is_demo=r.is_demo)
    if ev is None:
        return None   # duplicate webhook
    for en in db.query(AgencyRenewalEnrollment).filter(AgencyRenewalEnrollment.renewal_id == r.id,
                                                       AgencyRenewalEnrollment.state == "active").all():
        en.state, en.paused_reason = "needs_human", "reply_received"
    if cls["code"] == "opt_out":
        prof = db.query(AgencyClientProfile).filter(AgencyClientProfile.lead_id == r.lead_id).first()
        if prof is not None:
            prof.contact_ok = False
        for en in db.query(AgencyRenewalEnrollment).join(
                AgencyRenewal, AgencyRenewal.id == AgencyRenewalEnrollment.renewal_id).filter(
                AgencyRenewal.lead_id == r.lead_id, AgencyRenewalEnrollment.state.in_(("active", "needs_human"))).all():
            en.state, en.paused_reason = "stopped", "opted_out"
        ev.handled = True
        r.next_action, r.next_action_at = "Client opted out - call only if they ask", None
        return ev
    r.attention_reason = cls["code"]
    r.attention_detail = text[:280]
    r.workflow_status = "needs_attention"
    r.last_activity_at = ev.at
    r.next_action = {"reply_premium": "Call about premium concern", "reply_cancel": "Retention call today",
                     "reply_review": "Book the review", "reply_question": "Reply to client"}[cls["code"]]
    r.next_action_at = ev.at + timedelta(hours=4)
    return ev


def resolve_attention(r: AgencyRenewal):
    r.attention_reason, r.attention_detail = None, None


def book_review(ctx: RCtx, r: AgencyRenewal, starts_at: datetime, agent_id: Optional[str], medium: str,
                notes: Optional[str]) -> AgencyAppointment:
    agent = r.assigned_user_id
    if agent_id:
        u = org_user(ctx, agent_id)
        if not ctx.manager_plus and u.id != ctx.user.id:
            raise HTTPException(403, "Only a manager can book on another person's schedule")
        agent = u.id
    if not agent:
        raise HTTPException(409, "Assign this renewal before booking a review")
    dur = timedelta(minutes=ctx.settings.review_minutes or 30)
    clash = ctx.db.query(AgencyAppointment).filter(
        AgencyAppointment.organization_id == ctx.org_id, AgencyAppointment.agent_user_id == agent,
        AgencyAppointment.status.in_(OPEN_APPT), AgencyAppointment.starts_at > starts_at - dur,
        AgencyAppointment.starts_at < starts_at + dur).first()
    if clash is not None:
        raise HTTPException(409, "That time overlaps another appointment on this person's schedule")
    if r.review_appointment_id:
        old = ctx.db.query(AgencyAppointment).filter(AgencyAppointment.id == r.review_appointment_id).first()
        if old is not None and old.status in OPEN_APPT:
            raise HTTPException(409, "A review is already booked for this renewal")
    appt = AgencyAppointment(organization_id=ctx.org_id, lead_id=r.lead_id, agent_user_id=agent, type="review",
                             medium=medium, starts_at=starts_at, status="booked", notes=notes, is_demo=r.is_demo)
    ctx.db.add(appt)
    ctx.db.flush()
    r.review_appointment_id = appt.id
    r.workflow_status = "review_booked"
    if r.attention_reason in ("reply_review", "reply_question", "reply_premium", "review_not_booked"):
        resolve_attention(r)
    r.next_action, r.next_action_at = "Hold renewal review", starts_at
    for en in ctx.db.query(AgencyRenewalEnrollment).filter(AgencyRenewalEnrollment.renewal_id == r.id,
                                                           AgencyRenewalEnrollment.state.in_(("active", "needs_human"))).all():
        en.state, en.paused_reason = "paused", "review_booked"
    for ev in ctx.db.query(AgencyRenewalEvent).filter(AgencyRenewalEvent.renewal_id == r.id,
                                                      AgencyRenewalEvent.kind == "reply",
                                                      AgencyRenewalEvent.handled.is_(False)).all():
        ev.handled = True
    names = user_names(ctx.db, [agent])
    add_event(ctx, ctx.org_id, r.lead_id, "review_booked", renewal_id=r.id,
              detail={"appointment_id": appt.id, "starts_at": iso(starts_at), "agent": names.get(agent),
                      "note": "Booking a review does not change the renewal outcome."},
              actor=ctx.actor.id, is_demo=r.is_demo)
    r.last_activity_at = utcnow()
    return appt


def record_outcome(ctx: RCtx, r: AgencyRenewal, outcome: str, note: Optional[str]):
    if outcome not in ("confirmed", "lost"):
        raise HTTPException(422, "outcome must be confirmed or lost")
    if not ctx.manager_plus and r.assigned_user_id != ctx.user.id:
        raise HTTPException(403, "Only the assigned agent or a manager can record an outcome")
    prev = r.outcome
    r.outcome, r.outcome_at, r.outcome_by, r.outcome_source, r.outcome_note = outcome, utcnow(), ctx.actor.id, "staff", note
    r.workflow_status = "closed"
    resolve_attention(r)
    r.next_action, r.next_action_at = None, None
    for en in ctx.db.query(AgencyRenewalEnrollment).filter(AgencyRenewalEnrollment.renewal_id == r.id,
                                                           AgencyRenewalEnrollment.state.in_(("active", "needs_human", "paused"))).all():
        en.state = "completed"
    add_event(ctx, ctx.org_id, r.lead_id, "outcome", renewal_id=r.id,
              detail={"outcome": outcome, "previous": prev, "note": note, "source": "staff"},
              actor=ctx.actor.id, is_demo=r.is_demo)


def assign(ctx: RCtx, r: AgencyRenewal, to_user_id: Optional[str], reason: str, expected_version: Optional[int],
           kind: str = "reassigned", move_review: bool = False) -> AgencyRenewal:
    """Atomic: a compare-and-set on `version`. Two managers (or two agents
    claiming) racing for the same renewal - exactly one wins, the other gets 409.
    History is preserved as an event; enrollments and appointments carry on, so
    reassignment never re-sends a message or drops a booked review."""
    new_u = org_user(ctx, to_user_id) if to_user_id else None
    if new_u is not None and r.location_id:
        st_locs = [x[0] for x in ctx.db.query(UserLocation.location_id).filter(
            UserLocation.user_id == new_u.id, UserLocation.organization_id == ctx.org_id).all()]
        if st_locs and r.location_id not in st_locs and not ctx.manager_plus:
            raise HTTPException(403, "That person does not work at this location")
    ver = r.version if expected_version is None else expected_version
    frm = r.assigned_user_id
    q = ctx.db.query(AgencyRenewal).filter(AgencyRenewal.id == r.id, AgencyRenewal.organization_id == ctx.org_id,
                                           AgencyRenewal.version == ver)
    if kind == "claimed":
        q = q.filter(AgencyRenewal.assigned_user_id.is_(None))
    now = utcnow()
    n = q.update({AgencyRenewal.assigned_user_id: new_u.id if new_u else None, AgencyRenewal.assigned_at: now,
                  AgencyRenewal.assignment_reason: reason, AgencyRenewal.version: ver + 1},
                 synchronize_session=False)
    if n != 1:
        raise HTTPException(409, "Someone else changed this item first. Refresh and try again.")
    ctx.db.refresh(r)
    if r.attention_reason == "unassigned":
        resolve_attention(r)
    if move_review and r.review_appointment_id and new_u is not None:
        appt = ctx.db.query(AgencyAppointment).filter(AgencyAppointment.id == r.review_appointment_id).first()
        if appt is not None and appt.status in OPEN_APPT:
            appt.agent_user_id = new_u.id
    names = user_names(ctx.db, [frm, new_u.id if new_u else None])
    add_event(ctx, ctx.org_id, r.lead_id, kind, renewal_id=r.id,
              detail={"from": frm, "from_name": names.get(frm), "to": new_u.id if new_u else None,
                      "to_name": names.get(new_u.id) if new_u else None, "reason": reason,
                      "review_moved": bool(move_review)},
              actor=ctx.actor.id, is_demo=r.is_demo)
    return r


def eligible_staff(ctx: RCtx, r: AgencyRenewal, lang: Optional[str]) -> List[Tuple[User, Dict[str, Any]]]:
    db = ctx.db
    staff = db.query(AgencyRetentionStaff).filter(AgencyRetentionStaff.organization_id == ctx.org_id,
                                                  AgencyRetentionStaff.available.is_(True)).all()
    out = []
    for s in staff:
        if s.title in ("owner",):
            continue
        locs = [x[0] for x in db.query(UserLocation.location_id).filter(UserLocation.user_id == s.user_id,
                                                                        UserLocation.organization_id == ctx.org_id).all()]
        if r.location_id and r.location_id not in locs:
            continue
        langs = jl(s.languages, ["en"]) or ["en"]
        if lang and lang != "en" and lang not in langs:
            continue
        prods = jl(s.products, None)
        if prods and r.product not in prods:
            continue
        load = db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == ctx.org_id,
                                              AgencyRenewal.assigned_user_id == s.user_id,
                                              AgencyRenewal.outcome.is_(None)).count()
        cap = s.review_capacity
        if cap is not None and load >= cap * 4:
            continue
        u = db.query(User).filter(User.id == s.user_id).first()
        if u is not None and u.is_active:
            out.append((u, {"load": load, "languages": langs}))
    return out


def auto_route(ctx: RCtx, renewal_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    ctx.require_manager()
    q = renewals_q(ctx).filter(AgencyRenewal.assigned_user_id.is_(None), AgencyRenewal.outcome.is_(None))
    if renewal_ids:
        q = q.filter(AgencyRenewal.id.in_(renewal_ids))
    assigned, unrouted = [], []
    rr_state: Dict[str, int] = {}
    for r in q.order_by(AgencyRenewal.renewal_date).all():
        prof = ctx.db.query(AgencyClientProfile).filter(AgencyClientProfile.lead_id == r.lead_id).first()
        lang = prof.preferred_language if prof else None
        if prof and prof.servicing_user_id:
            cand = [(u, m) for u, m in eligible_staff(ctx, r, lang) if u.id == prof.servicing_user_id]
            if cand:
                assign(ctx, r, cand[0][0].id, "Current servicing agent", None, kind="assigned")
                assigned.append({"id": r.id, "to": cand[0][0].full_name, "why": "Current servicing agent"})
                continue
        cands = eligible_staff(ctx, r, lang)
        if not cands:
            unrouted.append({"id": r.id, "why": "No available staff at this location%s" % (
                " who speaks %s" % lang if lang and lang != "en" else "")})
            continue
        if ctx.settings.routing_mode == "round_robin":
            key = r.location_id or "-"
            i = rr_state.get(key, 0)
            cands.sort(key=lambda x: x[0].id)
            u, meta = cands[i % len(cands)]
            rr_state[key] = i + 1
            why = "Round robin at location"
        else:
            u, meta = min(cands, key=lambda x: (x[1]["load"], x[0].id))
            why = "Least loaded (%d open)" % meta["load"]
        if lang and lang != "en":
            why += "; speaks %s" % lang
        assign(ctx, r, u.id, why, None, kind="assigned")
        assigned.append({"id": r.id, "to": u.full_name, "why": why})
    return {"assigned": assigned, "unrouted": unrouted}


# ─────────────────────────────── client detail ───────────────────────────────

def client_detail(ctx: RCtx, lead: Lead) -> Dict[str, Any]:
    db = ctx.db
    prof = db.query(AgencyClientProfile).filter(AgencyClientProfile.lead_id == lead.id).first()
    pols = db.query(AgencyPolicy).filter(AgencyPolicy.organization_id == ctx.org_id, AgencyPolicy.lead_id == lead.id).all()
    rens = db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == ctx.org_id,
                                          AgencyRenewal.lead_id == lead.id).order_by(AgencyRenewal.renewal_date).all()
    if not ctx.owner:
        vis = {x.id for x in renewals_q(ctx).filter(AgencyRenewal.lead_id == lead.id).all()}
    else:
        vis = {r.id for r in rens}
    rows = renewal_rows(ctx, rens)
    for row in rows:
        row["in_scope"] = row["id"] in vis
    evs = db.query(AgencyRenewalEvent).filter(AgencyRenewalEvent.organization_id == ctx.org_id,
                                              AgencyRenewalEvent.lead_id == lead.id).order_by(AgencyRenewalEvent.at.asc()).all()
    names = user_names(db, [e.actor_user_id for e in evs] + [p.agent_user_id for p in pols] +
                       [prof.servicing_user_id if prof else None])
    appts = db.query(AgencyAppointment).filter(AgencyAppointment.organization_id == ctx.org_id,
                                               AgencyAppointment.lead_id == lead.id).order_by(AgencyAppointment.starts_at).all()
    loc = db.query(Location).filter(Location.id == prof.location_id).first() if prof and prof.location_id else None
    enr = db.query(AgencyRenewalEnrollment).filter(AgencyRenewalEnrollment.renewal_id.in_([r.id for r in rens] or ["-"])).all()
    wfn = {w.id: w.name for w in db.query(AgencyRenewalWorkflow).filter(AgencyRenewalWorkflow.organization_id == ctx.org_id).all()}
    return {
        "lead_id": lead.id, "name": lead_name(lead),
        "household": (prof.household_name if prof and prof.household_name else lead_name(lead)),
        "contact": {"phone": lead.phone, "email": lead.email, "city": lead.city, "state": lead.state},
        "preferred_language": prof.preferred_language if prof else None,
        "preferred_channel": prof.preferred_channel if prof else None,
        "contact_ok": prof.contact_ok if prof else True,
        "location": {"id": loc.id, "name": loc.name} if loc else None,
        "servicing_agent": names.get(prof.servicing_user_id) if prof else None,
        "source": prof.source if prof else None, "source_updated_at": iso(prof.source_updated_at) if prof else None,
        "policies": [{"id": p.id, "product": p.product_category,
                      "product_label": PRODUCT_LABELS.get(p.product_category, p.product_category),
                      "policy_masked": mask_policy(p.policy_number), "status": p.status, "carrier": p.carrier,
                      "effective_date": iso(p.effective_date), "annual_review_date": iso(p.annual_review_date),
                      "service_owner": names.get(p.agent_user_id)} for p in pols],
        "renewals": rows,
        "timeline": [event_dict(e, names) for e in evs],
        "appointments": [{"id": a.id, "starts_at": iso(a.starts_at), "status": a.status, "type": a.type,
                          "medium": a.medium, "agent": names.get(a.agent_user_id) or user_names(db, [a.agent_user_id]).get(a.agent_user_id)}
                         for a in appts],
        "enrollments": [{"id": e.id, "renewal_id": e.renewal_id, "workflow": wfn.get(e.workflow_id), "state": e.state,
                         "paused_reason": e.paused_reason, "next_run_at": iso(e.next_run_at),
                         "next_step_days_before": e.next_offset} for e in enr],
        "is_demo": bool(prof.is_demo) if prof else False,
    }


def list_clients(ctx: RCtx, search: Optional[str] = None, location_id=None, limit=200) -> Dict[str, Any]:
    db = ctx.db
    q = db.query(AgencyClientProfile).filter(AgencyClientProfile.organization_id == ctx.org_id,
                                             AgencyClientProfile.lead_id.in_(visible_lead_ids_q(ctx)))
    if location_id:
        location_in_scope(ctx, location_id)
        q = q.filter(AgencyClientProfile.location_id == location_id)
    profs = q.all()
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_([p.lead_id for p in profs] or ["-"])).all()}
    if search:
        s = search.lower().strip()
        profs = [p for p in profs if s in (p.household_name or "").lower() or
                 s in lead_name(leads.get(p.lead_id)).lower() if leads.get(p.lead_id)]
    rens = db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == ctx.org_id,
                                          AgencyRenewal.lead_id.in_([p.lead_id for p in profs] or ["-"])).all()
    by_lead: Dict[str, List[AgencyRenewal]] = {}
    for r in rens:
        by_lead.setdefault(r.lead_id, []).append(r)
    locs = {l.id: l.name for l in db.query(Location).filter(Location.organization_id == ctx.org_id).all()}
    names = user_names(db, [p.servicing_user_id for p in profs])
    today = ctx.today()
    out = []
    for p in profs:
        rs = by_lead.get(p.lead_id, [])
        open_rs = sorted([r for r in rs if not r.outcome], key=lambda r: r.renewal_date)
        out.append({"lead_id": p.lead_id, "household": p.household_name or lead_name(leads[p.lead_id]),
                    "name": lead_name(leads[p.lead_id]) if p.lead_id in leads else None,
                    "policies": len(rs), "products": sorted({PRODUCT_LABELS.get(r.product, r.product) for r in rs}),
                    "next_renewal": iso(open_rs[0].renewal_date) if open_rs else None,
                    "days_to_next": (open_rs[0].renewal_date - today).days if open_rs else None,
                    "location": locs.get(p.location_id), "servicing_agent": names.get(p.servicing_user_id),
                    "preferred_language": p.preferred_language, "source": p.source})
    out.sort(key=lambda x: (x["days_to_next"] is None, x["days_to_next"] or 0))
    return {"items": out[:limit], "total": len(out)}


def conversations(ctx: RCtx, only_open: bool = False) -> Dict[str, Any]:
    vis = renewals_q(ctx).with_entities(AgencyRenewal.id)
    q = ctx.db.query(AgencyRenewalEvent).filter(AgencyRenewalEvent.organization_id == ctx.org_id,
                                                AgencyRenewalEvent.renewal_id.in_(vis),
                                                AgencyRenewalEvent.kind.in_(("outreach", "reply", "note")))
    evs = q.order_by(AgencyRenewalEvent.at.desc()).limit(2000).all()
    threads: Dict[str, Dict[str, Any]] = {}
    for e in evs:
        t = threads.get(e.lead_id)
        if t is None:
            t = threads[e.lead_id] = {"lead_id": e.lead_id, "last_at": iso(e.at), "last_body": e.body,
                                      "last_direction": e.direction, "last_status": e.delivery_status,
                                      "unhandled": 0, "messages": 0, "renewal_id": e.renewal_id,
                                      "classification": None}
        t["messages"] += 1
        if e.kind == "reply" and not e.handled:
            t["unhandled"] += 1
            if t["classification"] is None:
                t["classification"] = ((jl(e.detail, {}) or {}).get("classification") or {}).get("code")
    leads = {l.id: l for l in ctx.db.query(Lead).filter(Lead.id.in_(list(threads) or ["-"])).all()}
    profs = {p.lead_id: p for p in ctx.db.query(AgencyClientProfile).filter(
        AgencyClientProfile.lead_id.in_(list(threads) or ["-"])).all()}
    items = []
    for lid, t in threads.items():
        if only_open and not t["unhandled"]:
            continue
        p = profs.get(lid)
        t["household"] = p.household_name if p and p.household_name else lead_name(leads[lid]) if lid in leads else None
        t["reason"] = REASONS.get(t["classification"]) if t["classification"] else None
        items.append(t)
    items.sort(key=lambda x: (-x["unhandled"], x["last_at"] or ""), reverse=False)
    items.sort(key=lambda x: x["unhandled"] == 0)
    return {"items": items, "total": len(items)}


# ─────────────────────────────── team ───────────────────────────────

def team(ctx: RCtx) -> Dict[str, Any]:
    db = ctx.db
    allowed = permitted_location_ids(ctx)
    locs = db.query(Location).filter(Location.organization_id == ctx.org_id)
    if allowed is not None:
        locs = locs.filter(Location.id.in_(allowed or ["-"]))
    locs = locs.order_by(Location.is_primary.desc(), Location.name).all()
    links = db.query(UserLocation).filter(UserLocation.organization_id == ctx.org_id,
                                          UserLocation.location_id.in_([l.id for l in locs] or ["-"])).all()
    staff_ids = {l.user_id for l in links}
    if ctx.kind == "agent":
        staff_ids = {ctx.user.id}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(staff_ids or {"-"})).all()}
    st = {s.user_id: s for s in db.query(AgencyRetentionStaff).filter(AgencyRetentionStaff.organization_id == ctx.org_id).all()}
    today = ctx.today()
    two_weeks = datetime.combine(today + timedelta(days=14), time.min)
    people = []
    for uid, u in users.items():
        s = st.get(uid)
        open_n = db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == ctx.org_id,
                                                AgencyRenewal.assigned_user_id == uid, AgencyRenewal.outcome.is_(None)).count()
        reviews = db.query(AgencyAppointment).filter(AgencyAppointment.organization_id == ctx.org_id,
                                                     AgencyAppointment.agent_user_id == uid,
                                                     AgencyAppointment.status.in_(OPEN_APPT),
                                                     AgencyAppointment.starts_at >= utcnow(),
                                                     AgencyAppointment.starts_at < two_weeks).count()
        overdue = db.query(AgencyRenewal).filter(AgencyRenewal.organization_id == ctx.org_id,
                                                 AgencyRenewal.assigned_user_id == uid, AgencyRenewal.outcome.is_(None),
                                                 AgencyRenewal.next_action_at < utcnow()).count()
        people.append({"user_id": uid, "name": u.full_name or u.email, "title": s.title if s else None,
                       "languages": jl(s.languages, ["en"]) if s else ["en"],
                       "available": s.available if s else True, "review_capacity": s.review_capacity if s else None,
                       "open_renewals": open_n, "reviews_next_14_days": reviews, "overdue": overdue,
                       "spare_review_capacity": (s.review_capacity - reviews) if s and s.review_capacity is not None else None,
                       "location_ids": [l.location_id for l in links if l.user_id == uid],
                       "is_demo": bool(s.is_demo) if s else False})
    people.sort(key=lambda p: (p["title"] != "owner", p["title"] != "manager", p["name"]))
    return {"locations": [{"id": l.id, "name": l.name, "city": l.city, "state": l.state, "address": l.address_line1,
                           "phone": l.phone, "is_primary": bool(l.is_primary), "timezone": l.timezone,
                           "verified": not ((l.notes or "").lower().startswith("illustrative")), "notes": l.notes,
                           "staff": sum(1 for x in links if x.location_id == l.id)} for l in locs],
            "people": people}


# ─────────────────────────────── assistant ───────────────────────────────

def ask(ctx: RCtx, question: str) -> Dict[str, Any]:
    """Scoped answers from the viewer's own data. Facts are stored values;
    priorities and classifications are listed as inferences. No quotes,
    premiums, savings, coverage determinations or policy changes - those are
    routed to licensed staff."""
    qn = (question or "").lower()
    today = ctx.today()
    if re.search(r"\b(quote|how much|cheaper|save|coverage|covered|bind|change my policy|lower my)\b", qn):
        return {"answer": "That needs a licensed agent. I can't quote, compare prices, decide coverage or change "
                          "a policy. Open the client's record and book a review instead.",
                "facts": [], "inferred": [], "records": [], "intent": "licensed_only"}
    if re.search(r"(price|premium|increase|went up)", qn):
        rows = [r for r in list_renewals(ctx, "attention")["items"] if r["attention"]["code"] == "reply_premium"]
        return _answer("%d client%s replied about price." % (len(rows), "" if len(rows) == 1 else "s"), rows,
                       ["Message text stored on each record"],
                       ["'Premium concern' is a keyword classification of the client's reply"], "premium_replies")
    if re.search(r"(location|office|branch).*(overdue|behind)|(overdue|behind).*(location|office|branch)", qn):
        counts: Dict[str, int] = {}
        for r in list_renewals(ctx, "attention")["items"]:
            if r["attention"]["code"] == "follow_up_overdue":
                counts[r["location_name"] or "No location"] = counts.get(r["location_name"] or "No location", 0) + 1
        if not counts:
            return _answer("No overdue follow-ups in the locations you can see.", [], [], [], "overdue_by_location")
        top = max(counts.items(), key=lambda x: x[1])
        return {"answer": "%s has the most overdue follow-ups (%d)." % top,
                "facts": ["%s: %d overdue" % (k, v) for k, v in sorted(counts.items(), key=lambda x: -x[1])],
                "inferred": [], "records": [], "intent": "overdue_by_location"}
    if re.search(r"(agent|staff|who).*(capacity|more reviews|take more|free|available)", qn):
        people = [p for p in team(ctx)["people"] if p["spare_review_capacity"] is not None]
        people.sort(key=lambda p: -p["spare_review_capacity"])
        top = [p for p in people if p["spare_review_capacity"] > 0][:5]
        return {"answer": ("%s can take the most reviews in the next 14 days." % top[0]["name"]) if top else
                          "Nobody you can see has spare review capacity configured.",
                "facts": ["%s: %d booked of %d capacity" % (p["name"], p["reviews_next_14_days"], p["review_capacity"])
                          for p in top],
                "inferred": [], "records": [], "intent": "capacity"}
    # default: renewals needing attention this week
    rows = [r for r in list_renewals(ctx, "attention")["items"] if r["days_to_renewal"] <= 7]
    return _answer("%d renewal%s within 7 days need attention." % (len(rows), "" if len(rows) == 1 else "s"),
                   rows, ["Renewal dates and assignments are stored values"],
                   ["Order is a priority ranking: replies, failures, unassigned, overdue, then date"],
                   "attention_this_week")


def _answer(text, rows, facts, inferred, intent):
    return {"answer": text, "facts": facts, "inferred": inferred, "intent": intent,
            "records": [{"renewal_id": r["id"], "lead_id": r["lead_id"], "household": r["household"],
                         "reason": r["attention"]["label"] if r.get("attention") else None,
                         "renewal_date": r["renewal_date"], "assigned": r["assigned_name"]} for r in rows[:10]]}


def summarize_client(ctx: RCtx, lead: Lead) -> Dict[str, Any]:
    d = client_detail(ctx, lead)
    open_r = [r for r in d["renewals"] if not r["outcome"]]
    facts = ["%d polic%s: %s" % (len(d["policies"]), "y" if len(d["policies"]) == 1 else "ies",
                                 ", ".join(p["product_label"] or "?" for p in d["policies"]))]
    for r in open_r:
        facts.append("%s renews %s (%d days)" % (r["product_label"], r["renewal_date"], r["days_to_renewal"]))
    replies = [e for e in d["timeline"] if e["kind"] == "reply"]
    if replies:
        facts.append("Last reply: “%s”" % (replies[-1]["body"] or "")[:160])
    if d["appointments"]:
        a = d["appointments"][-1]
        facts.append("Appointment %s %s" % (a["status"], a["starts_at"]))
    inferred = ["%s: %s" % (r["product_label"], r["attention"]["label"]) for r in open_r if r["attention"]]
    return {"facts": facts, "inferred": inferred,
            "note": "Summary built from stored fields only. Coverage advice and policy changes go to a licensed agent."}


# ─────────────────────────────── reports ───────────────────────────────

def report(ctx: RCtx, start: date, end: date, location_id=None, agent_id=None, product=None) -> Dict[str, Any]:
    q = apply_filters(ctx, renewals_q(ctx), location_id, agent_id, product).filter(
        AgencyRenewal.renewal_date >= start, AgencyRenewal.renewal_date <= end,
        AgencyRenewal.is_demo == bool(ctx.is_demo))
    rs = q.all()
    ids = [r.id for r in rs] or ["-"]
    evs = ctx.db.query(AgencyRenewalEvent).filter(AgencyRenewalEvent.renewal_id.in_(ids)).all()
    contacted = {e.renewal_id for e in evs if e.kind == "outreach" and e.delivery_status in ("simulated", "delivered")}
    replied = {e.renewal_id for e in evs if e.kind == "reply"}
    appts = {a.id: a for a in ctx.db.query(AgencyAppointment).filter(
        AgencyAppointment.id.in_([r.review_appointment_id for r in rs if r.review_appointment_id] or ["-"])).all()}
    booked = {r.id for r in rs if r.review_appointment_id in appts}
    completed = {r.id for r in rs if r.review_appointment_id in appts and appts[r.review_appointment_id].status == "completed"}
    conf = [r for r in rs if r.outcome == "confirmed"]
    lost = [r for r in rs if r.outcome == "lost"]
    n = len(rs)
    decided = len(conf) + len(lost)
    delivery: Dict[str, int] = {}
    for e in evs:
        if e.kind == "outreach":
            delivery[e.delivery_status or "unknown"] = delivery.get(e.delivery_status or "unknown", 0) + 1
    by_loc: Dict[str, Dict[str, int]] = {}
    locs = {l.id: l.name for l in ctx.db.query(Location).filter(Location.organization_id == ctx.org_id).all()}
    for r in rs:
        b = by_loc.setdefault(locs.get(r.location_id, "No location"), {"due": 0, "confirmed": 0, "lost": 0, "unresolved": 0})
        b["due"] += 1
        b["confirmed" if r.outcome == "confirmed" else "lost" if r.outcome == "lost" else "unresolved"] += 1
    pct = lambda a, b: round(100.0 * a / b, 1) if b else None  # noqa: E731
    return {
        "cohort": {"start": start.isoformat(), "end": end.isoformat(),
                   "definition": "Policy renewals whose renewal date falls in this range."},
        "data": "demo" if ctx.is_demo else "live",
        "funnel": [
            {"key": "due", "label": "Renewals due", "count": n, "of": None},
            {"key": "contacted", "label": "Contacted", "count": len(contacted), "of": n, "pct": pct(len(contacted), n),
             "note": "At least one outreach recorded as delivered or simulated."},
            {"key": "replied", "label": "Replied", "count": len(replied), "of": n, "pct": pct(len(replied), n)},
            {"key": "reviews_booked", "label": "Reviews booked", "count": len(booked), "of": n, "pct": pct(len(booked), n)},
            {"key": "reviews_completed", "label": "Reviews completed", "count": len(completed), "of": n,
             "pct": pct(len(completed), n)},
            {"key": "confirmed", "label": "Confirmed", "count": len(conf), "of": n, "pct": pct(len(conf), n)},
            {"key": "lost", "label": "Lost", "count": len(lost), "of": n, "pct": pct(len(lost), n)},
            {"key": "unresolved", "label": "Unresolved", "count": n - decided, "of": n, "pct": pct(n - decided, n)},
        ],
        "retention": {"rate": pct(len(conf), decided), "of": decided,
                      "definition": "Confirmed ÷ (confirmed + lost) among renewals with a recorded outcome. "
                                    "Unresolved renewals are excluded, not counted as retained."},
        "delivery": delivery,
        "by_location": [{"location": k, **v} for k, v in sorted(by_loc.items())],
        "revenue_note": "Retained revenue is not shown: no premium amounts are stored for these records.",
    }


# ─────────────────────────────── CSV import ───────────────────────────────

IMPORT_FIELDS = ["household_name", "first_name", "last_name", "email", "phone", "preferred_language",
                 "location", "policy_number", "product", "renewal_date", "policy_status",
                 "servicing_agent_email", "annual_review_date"]
REQUIRED = ["last_name", "policy_number", "product", "renewal_date"]
_PRODUCT_ALIASES = {"automobile": "auto", "car": "auto", "homeowners": "home", "homeowner": "home", "ho3": "home",
                    "renter": "renters", "condominium": "condo", "commercial": "business", "bop": "business",
                    "landlords": "landlord", "host": "landlord", "landlord/host": "landlord", "term life": "life",
                    "whole life": "life"}


def _parse_import_date(v: str) -> Optional[date]:
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _norm_phone(v: str) -> Optional[str]:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 10:
        return "+1" + d
    if len(d) == 11 and d.startswith("1"):
        return "+" + d
    return None


def import_preview(ctx: RCtx, text: str, mapping: Optional[Dict[str, str]] = None, filename: str = "import.csv"):
    ctx.require_manager()
    reader = csv.DictReader(io.StringIO(text or ""))
    headers = reader.fieldnames or []
    if mapping is None:
        mapping = {}
        for f in IMPORT_FIELDS:
            for h in headers:
                if h and h.strip().lower().replace(" ", "_") == f:
                    mapping[f] = h
    missing_map = [f for f in REQUIRED if f not in mapping]
    locs = {l.name.strip().lower(): l for l in ctx.db.query(Location).filter(Location.organization_id == ctx.org_id).all()}
    allowed = permitted_location_ids(ctx)
    rows = []
    for i, raw in enumerate(reader, start=2):
        get = lambda f: (raw.get(mapping[f]) or "").strip() if f in mapping else ""  # noqa: E731
        errs, warns = [], []
        for f in REQUIRED:
            if not get(f):
                errs.append("%s is required" % f)
        rd = _parse_import_date(get("renewal_date"))
        if get("renewal_date") and rd is None:
            errs.append("renewal_date '%s' is not a date (use YYYY-MM-DD or MM/DD/YYYY)" % get("renewal_date"))
        ard = _parse_import_date(get("annual_review_date")) if get("annual_review_date") else None
        prod = get("product").lower()
        prod = _PRODUCT_ALIASES.get(prod, prod)
        if prod and prod not in PRODUCTS:
            errs.append("product '%s' is not recognised" % get("product"))
        loc = None
        if get("location"):
            loc = locs.get(get("location").lower())
            if loc is None:
                errs.append("location '%s' not found" % get("location"))
            elif allowed is not None and loc.id not in allowed:
                errs.append("location '%s' is outside your permitted locations" % get("location"))
        elif allowed is not None:
            errs.append("location is required for managers")
        phone = _norm_phone(get("phone")) if get("phone") else None
        if get("phone") and phone is None:
            warns.append("phone '%s' could not be normalised; it will be left blank" % get("phone"))
        lang = (get("preferred_language") or "en").lower()[:2]
        if lang not in ("en", "es"):
            warns.append("language '%s' kept as given" % lang)
        match = None
        if get("policy_number"):
            p = ctx.db.query(AgencyPolicy).filter(AgencyPolicy.organization_id == ctx.org_id,
                                                  AgencyPolicy.policy_number == get("policy_number")).first()
            if p is not None:
                match = {"type": "policy", "policy_id": p.id, "lead_id": p.lead_id}
        if match is None and (get("email") or phone):
            cond = []
            if get("email"):
                cond.append(Lead.email == get("email").lower())
            if phone:
                cond.append(Lead.phone == phone)
            l = ctx.db.query(Lead).filter(Lead.organization_id == ctx.org_id, or_(*cond)).first()
            if l is not None:
                match = {"type": "client", "lead_id": l.id}
        rows.append({"row": i, "errors": errs, "warnings": warns, "match": match,
                     "action": "error" if errs else ("update" if match else "create"),
                     "values": {"household_name": get("household_name"), "first_name": get("first_name"),
                                "last_name": get("last_name"), "email": get("email").lower() or None, "phone": phone,
                                "preferred_language": lang, "location_id": loc.id if loc else None,
                                "location": loc.name if loc else get("location"),
                                "policy_number": get("policy_number"), "product": prod,
                                "renewal_date": rd.isoformat() if rd else None, "policy_status": get("policy_status") or "in_force",
                                "servicing_agent_email": get("servicing_agent_email").lower(),
                                "annual_review_date": ard.isoformat() if ard else None}})
    return {"filename": filename, "headers": headers, "mapping": mapping, "missing_required_mapping": missing_map,
            "rows": rows, "summary": {"total": len(rows), "create": sum(r["action"] == "create" for r in rows),
                                      "update": sum(r["action"] == "update" for r in rows),
                                      "errors": sum(r["action"] == "error" for r in rows)},
            "timezone_note": "Dates are calendar dates in the agency's time zone (%s); no time-of-day is imported." %
                             (ctx.settings.timezone or DEFAULT_TZ)}


def import_commit(ctx: RCtx, text: str, mapping=None, filename="import.csv") -> Dict[str, Any]:
    pv = import_preview(ctx, text, mapping, filename)
    if pv["missing_required_mapping"]:
        raise HTTPException(422, "Map the required columns first: %s" % ", ".join(pv["missing_required_mapping"]))
    now = utcnow()
    created = updated = skipped = 0
    for row in pv["rows"]:
        if row["action"] == "error":
            skipped += 1
            continue
        v = row["values"]
        agent = None
        if v["servicing_agent_email"]:
            agent = ctx.db.query(User).filter(func.lower(User.email) == v["servicing_agent_email"]).first()
            if agent is not None and not is_org_staff(ctx.db, ctx.org_id, agent):
                agent = None
        lead = None
        if row["match"]:
            lead = ctx.db.query(Lead).filter(Lead.id == row["match"]["lead_id"], Lead.organization_id == ctx.org_id).first()
        if lead is None:
            lead = Lead(organization_id=ctx.org_id, first_name=v["first_name"] or None, last_name=v["last_name"],
                        email=v["email"], phone=v["phone"], status="client", source="csv_import",
                        source_file=filename, relationship_type="client", is_test=ctx.is_demo)
            ctx.db.add(lead)
            ctx.db.flush()
            created += 1
        else:
            # Non-destructive: fill blanks, never wipe a value with an empty cell.
            for f in ("first_name", "email", "phone"):
                if v.get(f) and not getattr(lead, f):
                    setattr(lead, f, v[f])
            updated += 1
        prof = ctx.db.query(AgencyClientProfile).filter(AgencyClientProfile.lead_id == lead.id).first()
        if prof is None:
            prof = AgencyClientProfile(organization_id=ctx.org_id, lead_id=lead.id, is_demo=ctx.is_demo, source="csv_import")
            ctx.db.add(prof)
        prof.household_name = v["household_name"] or prof.household_name or ("%s Household" % v["last_name"])
        prof.location_id = v["location_id"] or prof.location_id
        prof.preferred_language = v["preferred_language"] or prof.preferred_language
        prof.servicing_user_id = (agent.id if agent else None) or prof.servicing_user_id
        prof.source, prof.source_ref, prof.source_updated_at = "csv_import", filename, now
        pol = ctx.db.query(AgencyPolicy).filter(AgencyPolicy.organization_id == ctx.org_id,
                                                AgencyPolicy.policy_number == v["policy_number"]).first()
        if pol is None:
            pol = AgencyPolicy(organization_id=ctx.org_id, lead_id=lead.id, policy_number=v["policy_number"],
                               product_category=v["product"], status=v["policy_status"], is_demo=ctx.is_demo)
            ctx.db.add(pol)
            ctx.db.flush()
        else:
            pol.status = v["policy_status"] or pol.status
        if agent:
            pol.agent_user_id = agent.id
        if v["annual_review_date"]:
            pol.annual_review_date = date.fromisoformat(v["annual_review_date"])
        rd = date.fromisoformat(v["renewal_date"])
        r = ctx.db.query(AgencyRenewal).filter(AgencyRenewal.policy_id == pol.id, AgencyRenewal.renewal_date == rd).first()
        if r is None:
            r = AgencyRenewal(organization_id=ctx.org_id, lead_id=lead.id, policy_id=pol.id, renewal_date=rd,
                              product=v["product"], location_id=prof.location_id, source="csv_import",
                              assigned_user_id=agent.id if agent else None, assigned_at=now if agent else None,
                              assignment_reason="Servicing agent from import" if agent else None,
                              is_demo=ctx.is_demo)
            ctx.db.add(r)
        r.source_updated_at = now
    return {"created": created, "updated": updated, "skipped_rows_with_errors": skipped,
            "summary": pv["summary"], "source": "csv_import", "imported_at": iso(now)}
