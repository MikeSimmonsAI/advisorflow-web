"""
ENERGY OPERATIONS — the Follow-Up / Renewals queues and Move Concierge.

Both screens previously opened a read-only configured view (workspace_views)
that could only list leads at a tier. This router gives them real work queues.

── QUEUES (GET /energy-ops/queues, GET /energy-ops/queues/{key}) ──────────────
Every queue is a documented query over records that already exist. Nothing is
estimated, and a queue whose source the caller may not read returns
`available: false` with a null count, never a zero.

  follow_up_due    open LeadTask due today (local server day, UTC)
  overdue          open LeadTask due before today
  upcoming         open LeadTask due in the next 7 days (after today)
  escalation       open LeadTask overdue by 3+ days, OR a lead carrying a
                   manual flag (manual_flag) - the manager's attention list
  no_response      lead messaged 7+ days ago, no reply recorded since, not
                   replied/hot/booked/dnc/dead
  customers        lead at tier contract_signed (enrolled)
  renewal_window   enrolled/renewal_due lead whose REAL contract_end_date
                   (custom field, entered by a person or the rate request) is
                   within -30..+120 days. A lead with no date on file is NOT
                   in this queue; it is counted as `renewal_date_missing` so the
                   gap is visible instead of invented.
  previous_customers  OrgContact record_class previous_customer (or
                   historical_customer true), not archived   [managers only]
  reactivation     previous customers with no lead yet - candidates for a
                   DELIBERATE promotion (nothing here promotes)  [managers only]

Scope: lead-backed queues start from lead_scope.authorized_lead_query (acting
workspace; an advisor sees only their own book). Task queues are org-scoped and,
for non-managers, limited to tasks assigned to the caller. Contact-backed
queues are manager-only, like the Contacts page.

── MOVE CONCIERGE (/energy-ops/moves) ─────────────────────────────────────────
A move request tracks contact, move date (only if given), services requested,
assignment, checklist, status, tasks, notes and activity. External vendors are
NOT CONFIGURED; no checklist item calls anyone. Every write is audited.
"""
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_user
from app.models.energy_models import EnergyMoveRequest
from app.models.intake_models import OrgContact, RecordClass, ContactLifecycle
from app.models.models import AuditLogEntry, Lead, Reply, User
from app.models.work_models import LeadTask
from app.routers.audit_log_router import log_action
from app.services import lead_scope
from app.services.entitlements import require_feature
from app.services import pipeline_stage  # noqa: F401  (stage clock listener)
from app.utils.time_fmt import iso_utc  # S19: explicit-UTC timestamps

router = APIRouter(prefix="/energy-ops", tags=["energy-ops"],
                   dependencies=[Depends(require_feature("leads"))])

ENROLLED_TIER = "contract_signed"
RENEWAL_TIERS = ("contract_signed", "renewal_due")
NO_RESPONSE_DAYS = 7
ESCALATE_AFTER_DAYS = 3
UPCOMING_DAYS = 7
RENEWAL_AHEAD_DAYS = 120
RENEWAL_BEHIND_DAYS = 30
_QUIET_STATUSES = ("replied", "hot", "booked", "dnc", "dead")

QUEUES = [
    ("follow_up_due", "Follow-Up Due Today", "tasks"),
    ("overdue", "Overdue", "tasks"),
    ("upcoming", "Upcoming (7 days)", "tasks"),
    ("escalation", "Manager Escalation", "mixed"),
    ("no_response", "No Response (7+ days)", "leads"),
    ("customers", "Enrolled Customers", "leads"),
    ("renewal_window", "Renewal Window", "leads"),
    ("previous_customers", "Previous Customers", "contacts"),
    ("reactivation", "Reactivation Candidates", "contacts"),
]
_QUEUE_KEYS = {k for k, _, _ in QUEUES}


def _now() -> datetime:
    return datetime.utcnow()


def _iso(ts):
    if ts is None:
        return None
    return ts.isoformat() + ("Z" if isinstance(ts, datetime) else "")


def _org_id(db: Session, user: User, request: Request) -> str:
    from app.services.platform_owner import is_platform_pseudo_org
    org_id = lead_scope.active_workspace_org_id(user, db, request)
    if not org_id or is_platform_pseudo_org(org_id):
        raise HTTPException(status_code=409, detail="No customer organization is selected.")
    return org_id


def _is_manager(db, user, request) -> bool:
    return lead_scope.is_manager_here(user, db, request)


def _name(first, last, fallback="Unnamed"):
    n = " ".join(p for p in (first, last) if p)
    return n or fallback


def _cf(raw) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def _parse_date(v) -> Optional[date]:
    if not v or not isinstance(v, str):
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


# ── task-backed queues ──────────────────────────────────────────────────────

def _tasks_q(db, user, request, org_id):
    """Open, dated tasks the caller may see - ONE query used for both the
    count and the paged list, so the two can never disagree.

    Scope: the acting workspace; a non-manager only their assigned tasks; and
    a task tied to a lead only when that lead is inside the caller's lead
    scope (lead_scope.authorized_lead_query), pushed into SQL as a subquery.
    """
    q = db.query(LeadTask).filter(LeadTask.organization_id == org_id,
                                  LeadTask.status == "open", LeadTask.due_at.isnot(None))
    if not _is_manager(db, user, request):
        q = q.filter(LeadTask.assigned_to_id == user.id)
    scoped_ids = lead_scope.authorized_lead_query(db, user, Lead.id, request=request)
    q = q.filter(or_(LeadTask.lead_id.is_(None), LeadTask.lead_id.in_(scoped_ids.scalar_subquery())))
    return q


def _flagged_q(db, user, request):
    return (lead_scope.authorized_lead_query(db, user, request=request)
            .filter(Lead.manual_flag.isnot(None), Lead.manual_flag != ""))


def _day_bounds(now):
    start = datetime(now.year, now.month, now.day)
    return start, start + timedelta(days=1)


def _task_filter(q, key, now):
    start, end = _day_bounds(now)
    if key == "follow_up_due":
        return q.filter(LeadTask.due_at >= start, LeadTask.due_at < end)
    if key == "overdue":
        return q.filter(LeadTask.due_at < start)
    if key == "upcoming":
        return q.filter(LeadTask.due_at >= end, LeadTask.due_at < end + timedelta(days=UPCOMING_DAYS))
    if key == "escalation":
        return q.filter(LeadTask.due_at < start - timedelta(days=ESCALATE_AFTER_DAYS - 1))
    raise ValueError(key)


# ── lead-backed queues ──────────────────────────────────────────────────────

def _no_response_q(db, user, request, now):
    cutoff = now - timedelta(days=NO_RESPONSE_DAYS)
    later_reply = (db.query(Reply.id).filter(Reply.lead_id == Lead.id,
                                             Reply.received_at >= Lead.last_messaged_at)
                   .exists())
    return (lead_scope.authorized_lead_query(db, user, request=request)
            .filter(Lead.last_messaged_at.isnot(None), Lead.last_messaged_at <= cutoff,
                    or_(Lead.status.is_(None), ~Lead.status.in_(_QUIET_STATUSES)),
                    ~later_reply))


def _renewal_rows(db, user, request, now):
    """(in_window rows sorted by date, missing_count). Python-side because the
    date lives in a JSON custom field; bounded by the renewal tiers."""
    rows = (lead_scope.authorized_lead_query(db, user, request=request)
            .filter(Lead.tier.in_(RENEWAL_TIERS),
                    or_(Lead.status.is_(None), ~Lead.status.in_(("dnc", "dead")))).all())
    today = now.date()
    inside, missing = [], 0
    for l in rows:
        d = _parse_date(_cf(l.custom_fields).get("contract_end_date"))
        if d is None:
            missing += 1
            continue
        delta = (d - today).days
        if -RENEWAL_BEHIND_DAYS <= delta <= RENEWAL_AHEAD_DAYS:
            inside.append((d, l))
    inside.sort(key=lambda t: t[0])
    return inside, missing


def _renewal_counts(db, user, request, now):
    """(in_window_count, missing_count) - exactly what len(_renewal_rows()[0])
    and _renewal_rows()[1] report, for the /queues summary. PERFORMANCE (S18):
    the summary used to run _renewal_rows TWICE per request, each time loading
    every renewal-tier lead as a full ORM object; the counts need only the
    custom_fields column, read once."""
    rows = (lead_scope.authorized_lead_query(db, user, Lead.custom_fields, request=request)
            .filter(Lead.tier.in_(RENEWAL_TIERS),
                    or_(Lead.status.is_(None), ~Lead.status.in_(("dnc", "dead")))).all())
    today = now.date()
    inside = missing = 0
    for (raw,) in rows:
        d = _parse_date(_cf(raw).get("contract_end_date"))
        if d is None:
            missing += 1
        elif -RENEWAL_BEHIND_DAYS <= (d - today).days <= RENEWAL_AHEAD_DAYS:
            inside += 1
    return inside, missing


# ── contact-backed queues ───────────────────────────────────────────────────

def _prev_customers_q(db, org_id):
    return db.query(OrgContact).filter(
        OrgContact.organization_id == org_id,
        or_(OrgContact.record_class == RecordClass.PREVIOUS_CUSTOMER,
            OrgContact.historical_customer.is_(True)),
        or_(OrgContact.lifecycle.is_(None), OrgContact.lifecycle != ContactLifecycle.ARCHIVED),
        OrgContact.archived_at.is_(None))


def _queue_count(db, user, request, org_id, key, now):
    if key in ("follow_up_due", "overdue", "upcoming"):
        return _task_filter(_tasks_q(db, user, request, org_id), key, now).count()
    if key == "escalation":
        t = _task_filter(_tasks_q(db, user, request, org_id), key, now).count()
        return t + _flagged_q(db, user, request).count()
    if key == "no_response":
        return _no_response_q(db, user, request, now).count()
    if key == "customers":
        return (lead_scope.authorized_lead_query(db, user, request=request)
                .filter(Lead.tier == ENROLLED_TIER).count())
    if key == "renewal_window":
        return len(_renewal_rows(db, user, request, now)[0])
    if key in ("previous_customers", "reactivation"):
        if not _is_manager(db, user, request):
            return None
        q = _prev_customers_q(db, org_id)
        if key == "reactivation":
            q = q.filter(OrgContact.lead_id.is_(None))
        return q.count()
    raise HTTPException(404, "Unknown queue")


def _enrollment_stats(db, user, request, now):
    """ENROLLMENTS, counted from Lead.enrolled_at (set by POST
    /rate-requests/{id}/enroll). A customer enrolled before that column
    existed has no date and is reported as `date_not_recorded`, never counted
    into a month. Month = calendar month, server UTC."""
    month_start = datetime(now.year, now.month, 1)
    base = lead_scope.authorized_lead_query(db, user, request=request)
    this_month = base.filter(Lead.enrolled_at.isnot(None), Lead.enrolled_at >= month_start).count()
    enrolled = or_(Lead.tier == ENROLLED_TIER, Lead.relationship_type == "customer")
    undated = (lead_scope.authorized_lead_query(db, user, request=request)
               .filter(enrolled, Lead.enrolled_at.is_(None)).count())
    recent = (lead_scope.authorized_lead_query(db, user, request=request)
              .filter(Lead.enrolled_at.isnot(None))
              .order_by(Lead.enrolled_at.desc(), Lead.id.asc()).limit(5).all())
    names = _user_names(db, [l.assigned_to_id for l in recent])
    return {"this_month": this_month, "date_not_recorded": undated,
            "month_start": _iso(month_start),
            "recent": [_lead_row(l, names, {"enrolled_at": _iso(l.enrolled_at),
                                            "current_supplier": _cf(l.custom_fields).get("current_supplier")})
                       for l in recent],
            "basis": "Lead.enrolled_at, recorded by the Enroll action"}


@router.get("/queues")
def queue_summary(request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user)):
    org_id = _org_id(db, user, request)
    now = _now()
    out = []
    in_window, missing = _renewal_counts(db, user, request, now)
    for key, label, kind in QUEUES:
        n = (in_window if key == "renewal_window"
             else _queue_count(db, user, request, org_id, key, now))
        out.append({"key": key, "label": label, "kind": kind, "count": n,
                    "available": n is not None})
    return {"queues": out, "renewal_date_missing": missing,
            "enrollments": _enrollment_stats(db, user, request, now),
            "rules": {"no_response_days": NO_RESPONSE_DAYS,
                      "escalate_after_days": ESCALATE_AFTER_DAYS,
                      "upcoming_days": UPCOMING_DAYS,
                      "renewal_window_days": [-RENEWAL_BEHIND_DAYS, RENEWAL_AHEAD_DAYS]},
            "as_of": _iso(now)}


def _user_names(db, ids):
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(ids)).all()}


def _lead_row(l: Lead, names, extra=None):
    r = {"type": "lead", "id": l.id, "lead_id": l.id,
         "name": _name(l.first_name, l.last_name), "phone": l.phone, "email": l.email,
         "tier": l.tier, "status": l.status, "owner": names.get(l.assigned_to_id),
         "assigned_to_id": l.assigned_to_id, "dnc": (l.status == "dnc"),
         "sms_consent": bool(l.sms_consent), "last_messaged_at": _iso(l.last_messaged_at),
         "last_contact_date": _iso(l.last_contact_date), "updated_at": _iso(l.updated_at),
         "link": f"/leads/{l.id}"}
    if extra:
        r.update(extra)
    return r


def _task_row(t: LeadTask, lead: Optional[Lead], names, now):
    start, _ = _day_bounds(now)
    overdue_days = (start - datetime(t.due_at.year, t.due_at.month, t.due_at.day)).days
    return {"type": "task", "id": t.id, "title": t.title, "details": t.details,
            "due_at": _iso(t.due_at), "days_overdue": max(overdue_days, 0),
            "lead_id": t.lead_id, "name": _name(lead.first_name, lead.last_name) if lead else None,
            "owner": names.get(t.assigned_to_id), "assigned_to_id": t.assigned_to_id,
            "source": t.source, "link": f"/leads/{t.lead_id}" if t.lead_id else None}


def _contact_row(c: OrgContact):
    return {"type": "contact", "id": c.id, "name": c.full_name or _name(c.first_name, c.last_name),
            "company": c.company, "email": c.email, "phone": c.phone or c.mobile_phone,
            "record_class": c.record_class, "lead_id": c.lead_id, "source": c.source,
            "last_activity_at": _iso(c.last_activity_at),
            "link": f"/contacts/{c.id}"}


@router.get("/queues/{key}")
def queue_items(key: str, request: Request, page: int = Query(1, ge=1),
                per_page: int = Query(25, ge=1, le=100),
                db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    if key not in _QUEUE_KEYS:
        raise HTTPException(404, "Unknown queue")
    org_id = _org_id(db, user, request)
    now = _now()
    off = (page - 1) * per_page
    items: List[Dict[str, Any]] = []
    total = 0

    if key in ("follow_up_due", "overdue", "upcoming", "escalation"):
        # Count and page in SQL over the same scoped query the summary counts
        # (tasks first by due date, then - for escalation - flagged leads).
        q = _task_filter(_tasks_q(db, user, request, org_id), key, now)
        task_total = q.count()
        tasks = (q.order_by(LeadTask.due_at.asc(), LeadTask.id.asc())
                 .offset(off).limit(per_page).all()) if off < task_total else []
        flagged: List[Lead] = []
        total = task_total
        if key == "escalation":
            fq = _flagged_q(db, user, request)
            total += fq.count()
            room = per_page - len(tasks)
            if room > 0:
                flagged = (fq.order_by(Lead.updated_at.desc(), Lead.id.asc())
                           .offset(max(0, off - task_total)).limit(room).all())
        lead_ids = [t.lead_id for t in tasks if t.lead_id]
        leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(lead_ids)).all()} if lead_ids else {}
        names = _user_names(db, [t.assigned_to_id for t in tasks] +
                            [l.assigned_to_id for l in flagged])
        items = [_task_row(t, leads.get(t.lead_id), names, now) for t in tasks]
        items += [_lead_row(l, names, {"reason": f"Flagged: {l.manual_flag}"
                                       + (f" - {l.manual_flag_reason}" if l.manual_flag_reason else "")})
                  for l in flagged]
    elif key == "no_response":
        q = _no_response_q(db, user, request, now)
        total = q.count()
        rows = q.order_by(Lead.last_messaged_at.asc()).offset(off).limit(per_page).all()
        names = _user_names(db, [l.assigned_to_id for l in rows])
        items = [_lead_row(l, names, {"days_since_message": (now - l.last_messaged_at).days})
                 for l in rows]
    elif key == "customers":
        q = (lead_scope.authorized_lead_query(db, user, request=request)
             .filter(Lead.tier == ENROLLED_TIER))
        total = q.count()
        rows = q.order_by(Lead.updated_at.desc()).offset(off).limit(per_page).all()
        names = _user_names(db, [l.assigned_to_id for l in rows])
        items = [_lead_row(l, names, {"contract_end_date":
                                      _cf(l.custom_fields).get("contract_end_date")})
                 for l in rows]
    elif key == "renewal_window":
        inside, _ = _renewal_rows(db, user, request, now)
        total = len(inside)
        page_rows = inside[off:off + per_page]
        names = _user_names(db, [l.assigned_to_id for _, l in page_rows])
        today = now.date()
        items = [_lead_row(l, names, {"contract_end_date": d.isoformat(),
                                      "days_to_renewal": (d - today).days,
                                      "current_supplier": _cf(l.custom_fields).get("current_supplier")})
                 for d, l in page_rows]
    else:
        if not _is_manager(db, user, request):
            return {"key": key, "available": False, "total": None, "items": [],
                    "detail": "Contact queues are visible to workspace managers."}
        q = _prev_customers_q(db, org_id)
        if key == "reactivation":
            q = q.filter(OrgContact.lead_id.is_(None))
        total = q.count()
        rows = (q.order_by(OrgContact.last_name.asc().nullslast(), OrgContact.id.asc())
                .offset(off).limit(per_page).all())
        items = [_contact_row(c) for c in rows]

    return {"key": key, "available": True, "total": total, "page": page,
            "per_page": per_page, "items": items}


# ════════════════════════════════════════════════════════════════════════════
# MOVE CONCIERGE
# ════════════════════════════════════════════════════════════════════════════

MOVE_STATUSES = ("requested", "in_progress", "waiting_on_customer", "completed", "cancelled")
OPEN_MOVE_STATUSES = ("requested", "in_progress", "waiting_on_customer")
SERVICES = {
    "electricity_start": "Start electricity at new address",
    "electricity_transfer": "Transfer electricity plan",
    "electricity_stop": "Stop electricity at old address",
    "internet": "Internet / cable",
    "water": "Water / utilities",
    "gas": "Natural gas",
    "trash": "Trash service",
    "movers": "Moving company",
    "other": "Other",
}
VENDOR_INTEGRATIONS = {"status": "not_configured",
                       "detail": "No outside vendor is connected. Every checklist item is "
                                 "completed by your team and recorded here; nothing is "
                                 "ordered or scheduled automatically."}


def _default_checklist(services: List[str]) -> List[Dict[str, Any]]:
    items = [{"key": "confirm_details", "label": "Confirm move date and new address with customer"}]
    for s in services:
        items.append({"key": f"svc_{s}", "label": f"Arrange: {SERVICES[s]}"})
    items.append({"key": "confirm_complete", "label": "Confirm with customer that services are active"})
    for i in items:
        i.update({"done": False, "done_at": None, "done_by": None})
    return items


class MoveCreate(BaseModel):
    lead_id: Optional[str] = None
    org_contact_id: Optional[str] = None
    contact_name: Optional[str] = Field(None, max_length=200)
    move_date: Optional[date] = None
    from_address: Optional[str] = Field(None, max_length=300)
    to_address: Optional[str] = Field(None, max_length=300)
    services: List[str] = Field(default_factory=list)
    assign_to: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=5000)


class MoveUpdate(BaseModel):
    status: Optional[str] = None
    move_date: Optional[date] = None
    from_address: Optional[str] = Field(None, max_length=300)
    to_address: Optional[str] = Field(None, max_length=300)
    services: Optional[List[str]] = None
    assign_to: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=5000)


class ChecklistToggle(BaseModel):
    key: str
    done: bool


class MoveTask(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    due_at: Optional[datetime] = None
    details: Optional[str] = Field(None, max_length=2000)


def _validate_services(services):
    bad = [s for s in services if s not in SERVICES]
    if bad:
        raise HTTPException(400, detail={"message": "Unknown service(s): " + ", ".join(bad),
                                         "valid_services": list(SERVICES)})
    seen = []
    for s in services:
        if s not in seen:
            seen.append(s)
    return seen


def _moves_q(db, user, request, org_id):
    q = db.query(EnergyMoveRequest).filter(EnergyMoveRequest.organization_id == org_id)
    if not _is_manager(db, user, request):
        q = q.filter(or_(EnergyMoveRequest.assigned_to_id == user.id,
                         EnergyMoveRequest.created_by_id == user.id))
    return q


def _load_move(db, user, request, move_id) -> EnergyMoveRequest:
    org_id = _org_id(db, user, request)
    m = _moves_q(db, user, request, org_id).filter(EnergyMoveRequest.id == move_id).first()
    if m is None:
        raise HTTPException(404, "Move request not found")
    return m


def _resolve_assignee(db, user, request, org_id, assign_to):
    if assign_to in (None, ""):
        return None
    if assign_to != user.id and not _is_manager(db, user, request):
        raise HTTPException(403, "Only a workspace manager can assign to someone else.")
    from app.routers.admin_router import _is_workspace_person
    target = db.query(User).filter(User.id == assign_to, User.is_active.is_(True)).first()
    if target is None or not _is_workspace_person(db, org_id, target):
        raise HTTPException(404, "Assignee not found in this workspace.")
    return target.id


def _move_row(m: EnergyMoveRequest, names, now=None):
    checklist = json.loads(m.checklist) if m.checklist else []
    services = json.loads(m.services) if m.services else []
    done = sum(1 for i in checklist if i.get("done"))
    return {"id": m.id, "lead_id": m.lead_id, "org_contact_id": m.org_contact_id,
            "contact_name": m.contact_name,
            "move_date": iso_utc(m.move_date),
            "from_address": m.from_address, "to_address": m.to_address,
            "services": [{"key": s, "label": SERVICES.get(s, s)} for s in services],
            "checklist": checklist, "checklist_done": done, "checklist_total": len(checklist),
            "status": m.status, "assigned_to_id": m.assigned_to_id,
            "owner": names.get(m.assigned_to_id), "notes": m.notes,
            "created_at": _iso(m.created_at), "updated_at": _iso(m.updated_at),
            "completed_at": _iso(m.completed_at),
            "lead_link": f"/leads/{m.lead_id}" if m.lead_id else None,
            "contact_link": f"/contacts/{m.org_contact_id}" if m.org_contact_id else None}


@router.get("/moves/meta")
def move_meta(user: User = Depends(require_tenant_user)):
    return {"statuses": list(MOVE_STATUSES), "open_statuses": list(OPEN_MOVE_STATUSES),
            "services": [{"key": k, "label": v} for k, v in SERVICES.items()],
            "vendor_integrations": VENDOR_INTEGRATIONS}


@router.get("/moves")
def list_moves(request: Request, status: Optional[str] = Query(None),
               search: Optional[str] = Query(None), page: int = Query(1, ge=1),
               per_page: int = Query(25, ge=1, le=100),
               db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = _org_id(db, user, request)
    base = _moves_q(db, user, request, org_id)
    counts = dict(base.with_entities(EnergyMoveRequest.status, func.count())
                  .group_by(EnergyMoveRequest.status).all())
    today = _now().date()
    summary = {s: counts.get(s, 0) for s in MOVE_STATUSES}
    summary["open"] = sum(summary[s] for s in OPEN_MOVE_STATUSES)
    summary["unassigned"] = base.filter(EnergyMoveRequest.assigned_to_id.is_(None),
                                        EnergyMoveRequest.status.in_(OPEN_MOVE_STATUSES)).count()
    summary["moving_in_14_days"] = base.filter(
        EnergyMoveRequest.status.in_(OPEN_MOVE_STATUSES),
        EnergyMoveRequest.move_date.isnot(None),
        EnergyMoveRequest.move_date >= today,
        EnergyMoveRequest.move_date <= today + timedelta(days=14)).count()
    summary["no_move_date"] = base.filter(EnergyMoveRequest.status.in_(OPEN_MOVE_STATUSES),
                                          EnergyMoveRequest.move_date.is_(None)).count()
    q = base
    if status == "open":
        q = q.filter(EnergyMoveRequest.status.in_(OPEN_MOVE_STATUSES))
    elif status == "unassigned":
        q = q.filter(EnergyMoveRequest.assigned_to_id.is_(None),
                     EnergyMoveRequest.status.in_(OPEN_MOVE_STATUSES))
    elif status:
        if status not in MOVE_STATUSES:
            raise HTTPException(422, "Unknown status")
        q = q.filter(EnergyMoveRequest.status == status)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(or_(EnergyMoveRequest.contact_name.ilike(like),
                         EnergyMoveRequest.to_address.ilike(like),
                         EnergyMoveRequest.from_address.ilike(like)))
    total = q.count()
    rows = (q.order_by(EnergyMoveRequest.move_date.is_(None), EnergyMoveRequest.move_date.asc(),
                       EnergyMoveRequest.created_at.desc())
            .offset((page - 1) * per_page).limit(per_page).all())
    names = _user_names(db, [m.assigned_to_id for m in rows])
    return {"summary": summary, "total": total, "page": page, "per_page": per_page,
            "items": [_move_row(m, names) for m in rows],
            "vendor_integrations": VENDOR_INTEGRATIONS}


@router.post("/moves", status_code=201, dependencies=[Depends(require_not_observation)])
def create_move(payload: MoveCreate, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user)):
    org_id = _org_id(db, user, request)
    services = _validate_services(payload.services)
    name = (payload.contact_name or "").strip() or None
    lead = contact = None
    if payload.lead_id:
        lead = lead_scope.load_lead_in_scope(db, user, payload.lead_id, request)
        name = name or _name(lead.first_name, lead.last_name)
    if payload.org_contact_id:
        contact = db.query(OrgContact).filter(OrgContact.id == payload.org_contact_id,
                                              OrgContact.organization_id == org_id).first()
        if contact is None:
            raise HTTPException(404, "Contact not found")
        name = name or contact.full_name or _name(contact.first_name, contact.last_name)
    if not name:
        raise HTTPException(422, "A move request needs a contact, a lead, or a contact name.")
    assignee = _resolve_assignee(db, user, request, org_id, payload.assign_to)
    if payload.assign_to is None:
        assignee = user.id
    m = EnergyMoveRequest(organization_id=org_id, lead_id=lead.id if lead else None,
                          org_contact_id=contact.id if contact else None, contact_name=name,
                          move_date=payload.move_date, from_address=payload.from_address,
                          to_address=payload.to_address, services=json.dumps(services),
                          checklist=json.dumps(_default_checklist(services)),
                          status="requested", assigned_to_id=assignee,
                          created_by_id=user.id, notes=payload.notes)
    db.add(m)
    db.flush()
    log_action(db, org_id, user.id, action="move_request.created", target_type="move_request",
               target_id=m.id, details={"lead_id": m.lead_id, "org_contact_id": m.org_contact_id,
                                        "services": services}, commit=False)
    db.commit()
    db.refresh(m)
    return _move_row(m, _user_names(db, [m.assigned_to_id]))


@router.get("/moves/{move_id}")
def get_move(move_id: str, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user)):
    m = _load_move(db, user, request, move_id)
    out = _move_row(m, _user_names(db, [m.assigned_to_id]))
    acts = (db.query(AuditLogEntry)
            .filter(AuditLogEntry.organization_id == m.organization_id,
                    AuditLogEntry.target_type == "move_request",
                    AuditLogEntry.target_id == m.id)
            .order_by(AuditLogEntry.created_at.desc()).limit(100).all())
    names = _user_names(db, [a.actor_user_id for a in acts])
    out["activity"] = [{"action": a.action, "actor": names.get(a.actor_user_id),
                        "at": _iso(a.created_at), "details": _safe_json(a.details)} for a in acts]
    tasks = []
    lead_readable = False
    if m.lead_id:
        # The move is visible to its assignee/creator, but the LEAD's tasks are
        # the lead's: an advisor who cannot read that lead does not see them.
        try:
            lead_scope.load_lead_in_scope(db, user, m.lead_id, request)
            lead_readable = True
        except HTTPException:
            lead_readable = False
    if lead_readable:
        tasks = (db.query(LeadTask).filter(LeadTask.organization_id == m.organization_id,
                                           LeadTask.lead_id == m.lead_id)
                 .order_by(LeadTask.status.asc(), LeadTask.due_at.asc()).all())
    out["tasks"] = [{"id": t.id, "title": t.title, "status": t.status, "due_at": _iso(t.due_at),
                     "details": t.details} for t in tasks]
    out["tasks_available"] = lead_readable
    out["vendor_integrations"] = VENDOR_INTEGRATIONS
    return out


def _safe_json(raw):
    try:
        return json.loads(raw) if raw else None
    except (ValueError, TypeError):
        return raw


@router.patch("/moves/{move_id}", dependencies=[Depends(require_not_observation)])
def update_move(move_id: str, payload: MoveUpdate, request: Request,
                db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    m = _load_move(db, user, request, move_id)
    sent = payload.model_fields_set
    changed: Dict[str, Any] = {}
    if "status" in sent and payload.status is not None and payload.status != m.status:
        if payload.status not in MOVE_STATUSES:
            raise HTTPException(400, detail={"message": "Unknown status",
                                             "valid_statuses": list(MOVE_STATUSES)})
        changed["status"] = {"from": m.status, "to": payload.status}
        m.status = payload.status
        m.completed_at = _now() if payload.status == "completed" else None
    for f in ("move_date", "from_address", "to_address", "notes"):
        if f in sent and getattr(payload, f) != getattr(m, f):
            changed[f] = True
            setattr(m, f, getattr(payload, f))
    if "services" in sent and payload.services is not None:
        services = _validate_services(payload.services)
        old = json.loads(m.services) if m.services else []
        if services != old:
            checklist = json.loads(m.checklist) if m.checklist else []
            keys = {i["key"] for i in checklist}
            for s in services:
                if f"svc_{s}" not in keys:
                    checklist.insert(max(len(checklist) - 1, 0),
                                     {"key": f"svc_{s}", "label": f"Arrange: {SERVICES[s]}",
                                      "done": False, "done_at": None, "done_by": None})
            # Removing a service drops its UNTICKED item; a ticked one is history.
            checklist = [i for i in checklist if not (i["key"].startswith("svc_")
                         and i["key"][4:] not in services and not i.get("done"))]
            m.services = json.dumps(services)
            m.checklist = json.dumps(checklist)
            changed["services"] = {"from": old, "to": services}
    if "assign_to" in sent:
        org_id = m.organization_id
        if payload.assign_to and payload.assign_to != m.assigned_to_id:
            target = _resolve_assignee(db, user, request, org_id, payload.assign_to)
            changed["assigned"] = {"from": m.assigned_to_id, "to": target}
            m.assigned_to_id = target
        elif not payload.assign_to and m.assigned_to_id:
            if not _is_manager(db, user, request):
                raise HTTPException(403, "Only a workspace manager can unassign.")
            changed["assigned"] = {"from": m.assigned_to_id, "to": None}
            m.assigned_to_id = None
    if changed:
        log_action(db, m.organization_id, user.id, action="move_request.updated",
                   target_type="move_request", target_id=m.id,
                   details={k: (v if v is not True else "changed") for k, v in changed.items()
                            if k != "move_date"} | ({"move_date": "changed"} if "move_date" in changed else {}),
                   commit=False)
    db.commit()
    db.refresh(m)
    out = _move_row(m, _user_names(db, [m.assigned_to_id]))
    out["changed"] = sorted(changed)
    return out


@router.post("/moves/{move_id}/checklist", dependencies=[Depends(require_not_observation)])
def toggle_checklist(move_id: str, payload: ChecklistToggle, request: Request,
                     db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    m = _load_move(db, user, request, move_id)
    checklist = json.loads(m.checklist) if m.checklist else []
    item = next((i for i in checklist if i.get("key") == payload.key), None)
    if item is None:
        raise HTTPException(404, "Checklist item not found")
    if bool(item.get("done")) != payload.done:
        item["done"] = payload.done
        item["done_at"] = _iso(_now()) if payload.done else None
        item["done_by"] = user.id if payload.done else None
        m.checklist = json.dumps(checklist)
        if m.status == "requested" and payload.done:
            m.status = "in_progress"
        log_action(db, m.organization_id, user.id, action="move_request.checklist",
                   target_type="move_request", target_id=m.id,
                   details={"key": payload.key, "done": payload.done}, commit=False)
    db.commit()
    db.refresh(m)
    return _move_row(m, _user_names(db, [m.assigned_to_id]))


@router.post("/moves/{move_id}/tasks", status_code=201,
             dependencies=[Depends(require_not_observation)])
def add_move_task(move_id: str, payload: MoveTask, request: Request,
                  db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    m = _load_move(db, user, request, move_id)
    if not m.lead_id:
        raise HTTPException(409, "Link this move to a lead before adding tasks; tasks live on a lead.")
    lead_scope.load_lead_in_scope(db, user, m.lead_id, request)
    t = LeadTask(organization_id=m.organization_id, lead_id=m.lead_id, title=payload.title,
                 details=payload.details, due_at=payload.due_at, status="open",
                 assigned_to_id=m.assigned_to_id or user.id, created_by_id=user.id,
                 source="move_concierge")
    db.add(t)
    db.flush()
    log_action(db, m.organization_id, user.id, action="move_request.task_added",
               target_type="move_request", target_id=m.id,
               details={"task_id": t.id, "title": t.title}, commit=False)
    db.commit()
    return {"id": t.id, "title": t.title, "status": t.status, "due_at": _iso(t.due_at),
            "lead_id": t.lead_id}
