"""Service jobs: ACTUAL delivery work, its statuses, who is on it, its
checklist, its private photos, and an append-only history.

An Acquisition appointment is a SALES opportunity; a job is delivery. A won
contract is CONVERTED into a job once (idempotency key "opp:<id>"), so a
double click or a retried request returns the same job instead of a second.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date as date_cls, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.service_ops_models import (ServiceChecklistTemplate, ServiceCustomer, ServiceJob,
                                           ServiceJobAssignment, ServiceJobAttachment, ServiceJobEvent,
                                           ServiceLocation, ServiceTeamMember)
from app.services.service_ops import core as K

STATUSES = ("draft", "scheduled", "assigned", "confirmed", "in_progress", "completed", "customer_review",
            "approved", "cancelled", "needs_attention")
STATUS_LABEL = {"draft": "Draft", "scheduled": "Scheduled", "assigned": "Assigned", "confirmed": "Confirmed",
                "in_progress": "In Progress", "completed": "Completed",
                "customer_review": "Customer Review Required", "approved": "Approved", "cancelled": "Cancelled",
                "needs_attention": "Needs Attention"}
# Legitimate moves only. Nothing ever becomes Completed because time passed.
TRANSITIONS = {
    "draft": {"scheduled", "cancelled"},
    "scheduled": {"assigned", "confirmed", "cancelled", "needs_attention"},
    "assigned": {"scheduled", "confirmed", "in_progress", "cancelled", "needs_attention"},
    "confirmed": {"assigned", "in_progress", "cancelled", "needs_attention"},
    "in_progress": {"completed", "needs_attention"},
    "completed": {"customer_review", "approved", "needs_attention"},
    "customer_review": {"approved", "needs_attention"},
    "needs_attention": {"scheduled", "assigned", "in_progress", "completed", "cancelled"},
    "approved": set(),
    "cancelled": set(),
}
LIVE = ("draft", "scheduled", "assigned", "confirmed", "in_progress", "needs_attention")
BUSY = ("scheduled", "assigned", "confirmed", "in_progress", "needs_attention")

DEFAULT_CHECKLISTS = {   # editable examples; a company replaces them with its own templates
    "cleaning": ["Restrooms", "Floors", "Common areas", "Trash removal", "Final inspection"],
    "window_washing": ["Exterior glass", "Interior glass", "Tracks", "Screens", "Final inspection"],
    "pest_control": ["Inspection", "Treatment areas", "Notes", "Follow-up recommendations"],
    "landscaping": ["Mowing", "Edging", "Cleanup", "Inspection", "Completion notes"],
    "pressure_washing": ["Pre-rinse", "Surfaces washed", "Final rinse", "Final inspection"],
}


# ── history (append-only, hash-chained) ────────────────────────────────────
def _hash(prev: Optional[str], payload: Dict[str, Any]) -> str:
    return hashlib.sha256(((prev or "") + json.dumps(payload, sort_keys=True, default=str)).encode()).hexdigest()


def record(db: Session, job: ServiceJob, kind: str, actor: Optional[User], *, from_status=None, to_status=None,
           data: Optional[Dict[str, Any]] = None, at: Optional[datetime] = None) -> ServiceJobEvent:
    last = (db.query(ServiceJobEvent).filter(ServiceJobEvent.job_id == job.id)
            .order_by(ServiceJobEvent.seq.desc()).first())
    seq = (last.seq + 1) if last else 1
    at = (at or datetime.utcnow()).replace(microsecond=0)
    payload = {"job": job.id, "seq": seq, "kind": kind, "from": from_status, "to": to_status,
               "actor": actor.id if actor else None, "at": at.isoformat(), "data": data or {}}
    ev = ServiceJobEvent(organization_id=job.organization_id, job_id=job.id, seq=seq, kind=kind,
                         from_status=from_status, to_status=to_status, actor_id=actor.id if actor else None, at=at,
                         data=K.jdump(data or {}), prev_hash=last.hash if last else None,
                         hash=_hash(last.hash if last else None, payload))
    db.add(ev)
    db.flush()
    return ev


def verify_history(db: Session, job: ServiceJob) -> Dict[str, Any]:
    """Recompute the chain: an edited, removed or reordered event breaks it."""
    prev = None
    rows = db.query(ServiceJobEvent).filter(ServiceJobEvent.job_id == job.id).order_by(ServiceJobEvent.seq).all()
    for i, ev in enumerate(rows, start=1):
        payload = {"job": job.id, "seq": ev.seq, "kind": ev.kind, "from": ev.from_status, "to": ev.to_status,
                   "actor": ev.actor_id, "at": ev.at.replace(microsecond=0).isoformat(),
                   "data": K.jload(ev.data, {})}
        if ev.seq != i or ev.prev_hash != prev or ev.hash != _hash(prev, payload):
            return {"intact": False, "broken_at": ev.seq, "events": len(rows)}
        prev = ev.hash
    return {"intact": True, "events": len(rows)}


def _move(db: Session, job: ServiceJob, to: str, actor: Optional[User], data: Optional[dict] = None) -> None:
    if to not in TRANSITIONS.get(job.status, set()):
        raise HTTPException(409, "A %s job can't become %s" % (STATUS_LABEL.get(job.status, job.status),
                                                               STATUS_LABEL.get(to, to)))
    frm = job.status
    job.status = to
    job.version = (job.version or 1) + 1
    record(db, job, "status", actor, from_status=frm, to_status=to, data=data)


# ── visibility ────────────────────────────────────────────────────────────
def get_job(db: Session, org_id: str, jid: str) -> ServiceJob:
    j = db.query(ServiceJob).filter(ServiceJob.organization_id == org_id, ServiceJob.id == jid).first()
    if j is None:
        raise HTTPException(404, "Job not found")
    return j


def active_member_ids(db: Session, job_id: str) -> List[str]:
    return [r[0] for r in db.query(ServiceJobAssignment.member_id)
            .filter(ServiceJobAssignment.job_id == job_id, ServiceJobAssignment.removed_at.is_(None)).all()]


def _team_member_ids(ctx: K.OpsCtx) -> List[str]:
    if not (ctx.member and ctx.member.team_name):
        return [ctx.member_id] if ctx.member_id else []
    return [m.id for m in ctx.db.query(ServiceTeamMember)
            .filter(ServiceTeamMember.organization_id == ctx.org_id,
                    ServiceTeamMember.team_name == ctx.member.team_name).all()]


def is_assigned(ctx: K.OpsCtx, job: ServiceJob) -> bool:
    return bool(ctx.member_id) and ctx.member_id in active_member_ids(ctx.db, job.id)


def may_view(ctx: K.OpsCtx, job: ServiceJob) -> bool:
    if ctx.can("view_all"):
        return True
    ids = set(active_member_ids(ctx.db, job.id))
    if ctx.can("view_team"):
        return bool(ids & set(_team_member_ids(ctx)))
    return ctx.member_id in ids


def view_job(ctx: K.OpsCtx, jid: str) -> ServiceJob:
    j = get_job(ctx.db, ctx.org_id, jid)
    if not may_view(ctx, j):
        raise HTTPException(404, "Job not found")      # not 403: don't confirm another job exists
    return j


def job_query(ctx: K.OpsCtx):
    q = ctx.db.query(ServiceJob).filter(ServiceJob.organization_id == ctx.org_id)
    if ctx.can("view_all"):
        return q
    ids = _team_member_ids(ctx) if ctx.can("view_team") else ([ctx.member_id] if ctx.member_id else [])
    sub = (ctx.db.query(ServiceJobAssignment.job_id)
           .filter(ServiceJobAssignment.organization_id == ctx.org_id,
                   ServiceJobAssignment.member_id.in_(ids or ["-"]),
                   ServiceJobAssignment.removed_at.is_(None)))
    return q.filter(ServiceJob.id.in_(sub))


def local(dt_utc: Optional[datetime], tz: str) -> Optional[datetime]:
    if dt_utc is None:
        return None
    return dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz)).replace(tzinfo=None)


def to_utc(local_dt: datetime, tz: str) -> datetime:
    return local_dt.replace(tzinfo=ZoneInfo(tz)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def job_json(ctx: K.OpsCtx, j: ServiceJob, *, full: bool = False) -> Dict[str, Any]:
    db = ctx.db
    loc = db.query(ServiceLocation).filter(ServiceLocation.id == j.location_id).first()
    cust = db.query(ServiceCustomer).filter(ServiceCustomer.id == j.customer_id).first()
    mids = active_member_ids(db, j.id)
    members = db.query(ServiceTeamMember).filter(ServiceTeamMember.id.in_(mids or ["-"])).all()
    users = {u.id: u for u in db.query(User).filter(User.id.in_([m.user_id for m in members] or ["-"])).all()}
    lt = local(j.scheduled_start, j.timezone)
    out = {
        "id": j.id, "status": j.status, "status_label": STATUS_LABEL.get(j.status, j.status),
        "service_type": j.service_type, "customer": {"id": cust.id, "name": cust.name} if cust else None,
        "location": ({"id": loc.id, "name": loc.name, "address": ", ".join(x for x in (loc.address, loc.city, loc.state)
                                                                          if x) or None,
                      "category": loc.service_category} if loc else None),
        "scheduled_start": K.iso(j.scheduled_start), "local_start": lt.strftime("%Y-%m-%dT%H:%M") if lt else None,
        "timezone": j.timezone, "duration_min": j.duration_min,
        "assigned": [{"member_id": m.id, "name": users.get(m.user_id).full_name if users.get(m.user_id) else None}
                     for m in members],
        "schedule_id": j.schedule_id, "occurrence_date": j.occurrence_date.isoformat() if j.occurrence_date else None,
        "opportunity_id": j.opportunity_id, "estimate_id": j.estimate_id, "price": j.price,
        "actual_start": K.iso(j.actual_start), "actual_end": K.iso(j.actual_end),
        "is_mine": bool(ctx.member_id and ctx.member_id in mids),
        "next": sorted(TRANSITIONS.get(j.status, set())),
    }
    if full:
        sens = loc is not None and (ctx.can("sensitive") or out["is_mine"] or K.assigned_to_location(ctx, loc))
        out.update({
            "instructions": j.instructions, "supplies": j.supplies,
            "internal_notes": j.internal_notes if (ctx.can("view_all") or out["is_mine"] or ctx.can("view_team")) else None,
            "checklist": K.jload(j.checklist, []), "completion_notes": j.completion_notes,
            "attention_reason": j.attention_reason, "cancel_reason": j.cancel_reason,
            "requires_customer_review": j.requires_customer_review,
            "location_detail": K.location_json(ctx, loc, sensitive=sens) if loc else None,
            "attachments": [attachment_json(a) for a in db.query(ServiceJobAttachment)
                            .filter(ServiceJobAttachment.job_id == j.id, ServiceJobAttachment.removed_at.is_(None))
                            .order_by(ServiceJobAttachment.created_at).all()],
            "history": [{"seq": e.seq, "kind": e.kind, "from": e.from_status, "to": e.to_status,
                         "actor_id": e.actor_id, "at": K.iso(e.at), "data": K.jload(e.data, {})}
                        for e in db.query(ServiceJobEvent).filter(ServiceJobEvent.job_id == j.id)
                        .order_by(ServiceJobEvent.seq).all()],
            "history_check": verify_history(db, j),
        })
    return out


def list_jobs(ctx: K.OpsCtx, *, status: Optional[str] = None, start: Optional[datetime] = None,
              end: Optional[datetime] = None, mine: bool = False, customer_id: Optional[str] = None,
              location_id: Optional[str] = None, limit: int = 300) -> List[Dict[str, Any]]:
    q = job_query(ctx)
    if mine:
        sub = (ctx.db.query(ServiceJobAssignment.job_id)
               .filter(ServiceJobAssignment.member_id == (ctx.member_id or "-"),
                       ServiceJobAssignment.removed_at.is_(None)))
        q = q.filter(ServiceJob.id.in_(sub))
    if status:
        q = q.filter(ServiceJob.status.in_(status.split(",")))
    if start:
        q = q.filter(ServiceJob.scheduled_start >= start)
    if end:
        q = q.filter(ServiceJob.scheduled_start < end)
    if customer_id:
        q = q.filter(ServiceJob.customer_id == customer_id)
    if location_id:
        q = q.filter(ServiceJob.location_id == location_id)
    rows = q.order_by(ServiceJob.scheduled_start.is_(None), ServiceJob.scheduled_start).limit(min(limit, 1000)).all()
    return [job_json(ctx, j) for j in rows]


# ── checklists ────────────────────────────────────────────────────────────
def template_items(db: Session, org_id: str, category: str, template_id: Optional[str] = None) -> List[Dict[str, Any]]:
    t = None
    if template_id:
        t = (db.query(ServiceChecklistTemplate)
             .filter(ServiceChecklistTemplate.organization_id == org_id, ServiceChecklistTemplate.id == template_id).first())
        if t is None:
            raise HTTPException(404, "Checklist not found")
    if t is None:
        t = (db.query(ServiceChecklistTemplate)
             .filter(ServiceChecklistTemplate.organization_id == org_id,
                     ServiceChecklistTemplate.service_category == category,
                     ServiceChecklistTemplate.active.is_(True))
             .order_by(ServiceChecklistTemplate.created_at.desc()).first())
    if t is not None:
        return [dict(i, done=False) for i in K.jload(t.items, [])]
    return [{"key": "item%d" % n, "label": lbl, "required": True, "done": False}
            for n, lbl in enumerate(DEFAULT_CHECKLISTS.get(category, []), start=1)]


def clean_items(items) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for n, i in enumerate((items or [])[:40], start=1):
        label = K.text((i or {}).get("label") if isinstance(i, dict) else i, 160)
        if not label:
            continue
        key = K.text((i or {}).get("key") if isinstance(i, dict) else None, 40) or "item%d" % n
        if key in seen:
            key = "item%d" % n
        seen.add(key)
        out.append({"key": key, "label": label,
                    "required": bool((i or {}).get("required", True)) if isinstance(i, dict) else True})
    return out


def save_template(ctx: K.OpsCtx, data: Dict[str, Any], tid: Optional[str] = None) -> Dict[str, Any]:
    ctx.need("schedule", "edit checklists")
    cat = data.get("service_category")
    if cat not in K.CATEGORIES:
        raise HTTPException(422, "Unknown service category")
    items = clean_items(data.get("items"))
    if not items:
        raise HTTPException(422, "A checklist needs at least one item")
    if tid:
        t = (ctx.db.query(ServiceChecklistTemplate)
             .filter(ServiceChecklistTemplate.organization_id == ctx.org_id, ServiceChecklistTemplate.id == tid).first())
        if t is None:
            raise HTTPException(404, "Checklist not found")
    else:
        t = ServiceChecklistTemplate(organization_id=ctx.org_id, created_by_id=ctx.user.id)
        ctx.db.add(t)
    t.service_category, t.items = cat, K.jdump(items)
    t.name = K.text(data.get("name"), 120) or K.CATEGORY_LABEL[cat]
    if "active" in data:
        t.active = bool(data["active"])
    ctx.db.commit()
    return template_json(t)


def template_json(t: ServiceChecklistTemplate) -> Dict[str, Any]:
    return {"id": t.id, "name": t.name, "service_category": t.service_category, "items": K.jload(t.items, []),
            "active": t.active}


# ── creating jobs ─────────────────────────────────────────────────────────
def _parse_local(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:16])
    except ValueError:
        raise HTTPException(422, "Use a local date and time like 2026-11-20T09:00")


def create_job(ctx: K.OpsCtx, data: Dict[str, Any], *, idempotency_key: Optional[str] = None,
               opportunity_id: Optional[str] = None, schedule_id: Optional[str] = None,
               occurrence_date: Optional[date_cls] = None, actor: Optional[User] = None,
               assign_ids: Optional[List[str]] = None) -> Tuple[ServiceJob, bool]:
    """Returns (job, created). Same idempotency key -> the existing job."""
    db = ctx.db
    if idempotency_key:
        existing = (db.query(ServiceJob).filter(ServiceJob.organization_id == ctx.org_id,
                                                ServiceJob.idempotency_key == idempotency_key).first())
        if existing is not None:
            return existing, False
    loc = K.get_location(db, ctx.org_id, data.get("location_id") or "-")
    if not loc.active:
        raise HTTPException(409, "That location is inactive")
    tz = K.tz_or(data.get("timezone") or loc.timezone, K.company_tz(db, ctx.org_id))
    start_local = _parse_local(data.get("local_start"))
    dur = data.get("duration_min") or loc.standard_duration_min or 60
    try:
        dur = max(15, min(int(dur), 24 * 60))
    except (TypeError, ValueError):
        raise HTTPException(422, "Duration must be minutes")
    service_type = K.text(data.get("service_type"), 120) or K.CATEGORY_LABEL.get(loc.service_category, "Service")
    job = ServiceJob(organization_id=ctx.org_id, customer_id=loc.customer_id, location_id=loc.id,
                     schedule_id=schedule_id, occurrence_date=occurrence_date, opportunity_id=opportunity_id,
                     estimate_id=data.get("estimate_id") or None, idempotency_key=idempotency_key,
                     service_type=service_type, status="draft", timezone=tz, duration_min=dur,
                     scheduled_start=to_utc(start_local, tz) if start_local else None,
                     instructions=K.text(data.get("instructions"), 4000),
                     internal_notes=K.text(data.get("internal_notes"), 4000),
                     supplies=K.text(data.get("supplies"), 2000), price=K.money(data.get("price"), "Price"),
                     requires_customer_review=bool(data.get("requires_customer_review")),
                     checklist=K.jdump(clean_items(data["checklist"]) if data.get("checklist")
                                       else template_items(db, ctx.org_id, loc.service_category,
                                                           data.get("checklist_template_id"))),
                     created_by_id=(actor or ctx.user).id)
    sp = db.begin_nested()
    try:
        db.add(job)
        db.flush()
    except IntegrityError:
        sp.rollback()
        existing = None
        if idempotency_key:
            existing = (db.query(ServiceJob).filter(ServiceJob.organization_id == ctx.org_id,
                                                    ServiceJob.idempotency_key == idempotency_key).first())
        if existing is None and schedule_id and occurrence_date:
            existing = (db.query(ServiceJob).filter(ServiceJob.schedule_id == schedule_id,
                                                    ServiceJob.occurrence_date == occurrence_date).first())
        if existing is None:
            raise
        return existing, False
    record(db, job, "created", actor or ctx.user,
           data={"opportunity_id": opportunity_id, "schedule_id": schedule_id, "source": data.get("_source", "manual")})
    if job.scheduled_start is not None:
        _move(db, job, "scheduled", actor or ctx.user)
    for mid in (assign_ids or []):
        _assign(ctx, job, mid, actor=actor or ctx.user, enforce=False)
    return job, True


def new_job(ctx: K.OpsCtx, data: Dict[str, Any]) -> Tuple[ServiceJob, bool]:
    ctx.need("schedule", "create jobs")
    key = K.text(data.get("idempotency_key"), 120)
    job, created = create_job(ctx, dict(data, _source="manual"), idempotency_key=("client:%s" % key) if key else None,
                              assign_ids=K._member_ids(ctx.db, ctx.org_id, data.get("assign_member_ids") or []))
    ctx.db.commit()
    if created:
        K.audit(ctx.db, ctx.org_id, ctx.user, "service_job_create", "service_job", job.id, {})
    return job, created


def convert_opportunity(ctx: K.OpsCtx, opp_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Won Acquisition contract -> the service company's customer, location and
    first job. Safe to call twice: the second call returns the same records."""
    ctx.need("schedule", "convert contracts to jobs")
    from app.services.acquisition import common as AC
    from app.services.acquisition import engine as AE
    from app.models.models import Lead
    db = ctx.db
    opp = AE.load_opp(db, ctx.org_id, opp_id, client=True)
    if opp.stage != AC.WON:
        raise HTTPException(409, "Only a won contract can become a job (this one is %s)"
                            % AC.STAGE_LABEL.get(opp.stage, opp.stage))
    key = "opp:%s" % opp.id
    existing = (db.query(ServiceJob).filter(ServiceJob.organization_id == ctx.org_id,
                                            ServiceJob.idempotency_key == key).first())
    if existing is not None:
        return {"created": False, "job": job_json(ctx, existing), "customer_id": existing.customer_id,
                "location_id": existing.location_id}
    lead = db.query(Lead).filter(Lead.id == opp.lead_id, Lead.organization_id == ctx.org_id).first()
    facts = AE.opp_json(opp, lead, None, False) if lead else {}
    # Customer: chosen, already converted from this opportunity, or new.
    cust = None
    if data.get("customer_id"):
        cust = K.get_customer(db, ctx.org_id, data["customer_id"])
    if cust is None:
        cust = (db.query(ServiceCustomer).filter(ServiceCustomer.organization_id == ctx.org_id,
                                                 ServiceCustomer.source_opportunity_id == opp.id).first())
    if cust is None:
        cust = K.create_customer(ctx, {
            "name": data.get("customer_name") or facts.get("business_name") or "Customer",
            "contact_name": facts.get("contact_name"), "contact_email": facts.get("email"),
            "contact_phone": facts.get("phone"), "billing_name": facts.get("business_name"),
            "billing_address": facts.get("address")}, source_opportunity_id=opp.id)
    # Location: chosen or new.
    if data.get("location_id"):
        loc = K.get_location(db, ctx.org_id, data["location_id"])
        if loc.customer_id != cust.id:
            raise HTTPException(422, "That location belongs to a different customer")
    else:
        ld = dict(data.get("location") or {})
        ld.setdefault("name", facts.get("business_name") or cust.name)
        ld.setdefault("address", lead.street_address if lead else None)
        ld.setdefault("city", lead.city if lead else None)
        ld.setdefault("state", lead.state if lead else None)
        ld.setdefault("postal_code", lead.zip_code if lead else None)
        ld.setdefault("service_category", _category_for(db, ctx.org_id))
        loc = K.create_location(ctx, cust.id, ld)
    jd = dict(data.get("job") or {}, location_id=loc.id, _source="won_contract")
    jd.setdefault("price", opp.monthly_value or opp.quote_amount)
    job, created = create_job(ctx, jd, idempotency_key=key, opportunity_id=opp.id,
                              assign_ids=K._member_ids(db, ctx.org_id, (data.get("job") or {}).get("assign_member_ids") or []))
    db.commit()
    AE._event(db, ctx.org_id, opp.id, "converted_to_job", ctx.user.id, note="Service job created")
    db.commit()
    K.audit(db, ctx.org_id, ctx.user, "service_job_convert", "service_job", job.id, {"opportunity_id": opp.id})
    return {"created": created, "job": job_json(ctx, job), "customer_id": cust.id, "location_id": loc.id}


def _category_for(db: Session, org_id: str) -> str:
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == org_id).first()
    ind = (getattr(org, "industry", None) or "").lower()
    for c in K.CATEGORIES:
        if c in ind:
            return c
    return "cleaning" if "clean" in ind else "other"


# ── assignment ────────────────────────────────────────────────────────────
def _in_availability(member: ServiceTeamMember, job: ServiceJob) -> bool:
    av = K.jload(member.availability, {})
    if not av or job.scheduled_start is None:
        return True                                  # no availability configured: no constraint
    st = local(job.scheduled_start, job.timezone)
    en = st + timedelta(minutes=job.duration_min or 60)
    hours = av.get(str(st.weekday()))
    if not hours:
        return False
    return st.strftime("%H:%M") >= hours[0] and en.strftime("%H:%M") <= hours[1] and en.date() == st.date()


def conflicts(db: Session, member_id: str, job: ServiceJob) -> List[ServiceJob]:
    if job.scheduled_start is None:
        return []
    st = job.scheduled_start
    en = st + timedelta(minutes=job.duration_min or 60)
    others = (db.query(ServiceJob)
              .join(ServiceJobAssignment, ServiceJobAssignment.job_id == ServiceJob.id)
              .filter(ServiceJobAssignment.member_id == member_id, ServiceJobAssignment.removed_at.is_(None),
                      ServiceJob.id != job.id, ServiceJob.status.in_(BUSY),
                      ServiceJob.scheduled_start < en,
                      ServiceJob.scheduled_start > st - timedelta(hours=24)).all())
    return [o for o in others if o.scheduled_start + timedelta(minutes=o.duration_min or 60) > st]


def _assign(ctx: K.OpsCtx, job: ServiceJob, member_id: str, *, actor: User, enforce: bool = True) -> None:
    db = ctx.db
    m = (db.query(ServiceTeamMember).filter(ServiceTeamMember.organization_id == ctx.org_id,
                                            ServiceTeamMember.id == member_id).first())
    if m is None or not m.active:
        raise HTTPException(422, "That team member is not active")
    if member_id in active_member_ids(db, job.id):
        return
    if enforce:
        if not _in_availability(m, job):
            raise HTTPException(409, "Outside this person's availability")
        c = conflicts(db, member_id, job)
        if c:
            raise HTTPException(409, "This person already has a job then (%s)" % c[0].service_type)
    db.add(ServiceJobAssignment(organization_id=ctx.org_id, job_id=job.id, member_id=member_id,
                                assigned_by_id=actor.id))
    db.flush()
    record(db, job, "assigned", actor, data={"member_id": member_id})
    if job.status == "scheduled":
        _move(db, job, "assigned", actor)


def set_assignment(ctx: K.OpsCtx, jid: str, add: List[str], remove: List[str]) -> ServiceJob:
    if not (ctx.can("schedule") or ctx.can("approve_team")):
        raise HTTPException(403, "Your role can't assign work")
    job = view_job(ctx, jid)
    if job.status in ("completed", "customer_review", "approved", "cancelled"):
        raise HTTPException(409, "This job is finished; assignments can't change")
    db = ctx.db
    for mid in remove or []:
        row = (db.query(ServiceJobAssignment).filter(ServiceJobAssignment.job_id == job.id,
                                                     ServiceJobAssignment.member_id == mid,
                                                     ServiceJobAssignment.removed_at.is_(None)).first())
        if row is not None:
            row.removed_at, row.removed_by_id = datetime.utcnow(), ctx.user.id
            record(db, job, "unassigned", ctx.user, data={"member_id": mid})
    db.flush()
    for mid in add or []:
        if ctx.role == "team_lead" and mid not in _team_member_ids(ctx):
            raise HTTPException(403, "A team lead can assign only their own team")
        _assign(ctx, job, mid, actor=ctx.user)
    if job.status == "assigned" and not active_member_ids(db, job.id):
        _move(db, job, "scheduled", ctx.user, data={"reason": "no one assigned"})
    db.commit()
    K.audit(db, ctx.org_id, ctx.user, "service_job_assign", "service_job", job.id, {"add": add, "remove": remove})
    return job


# ── edits, reschedule, cancel ─────────────────────────────────────────────
def update_job(ctx: K.OpsCtx, jid: str, data: Dict[str, Any]) -> ServiceJob:
    ctx.need("schedule", "edit jobs")
    job = view_job(ctx, jid)
    if job.status in ("approved", "cancelled"):
        raise HTTPException(409, "A %s job can't be edited" % STATUS_LABEL[job.status])
    changed = {}
    for f, w in (("service_type", 120), ("instructions", 4000), ("internal_notes", 4000), ("supplies", 2000)):
        if f in data:
            setattr(job, f, K.text(data[f], w) if f != "service_type" else (K.text(data[f], w) or job.service_type))
            changed[f] = True
    if "price" in data:
        job.price = K.money(data["price"], "Price")
        changed["price"] = job.price
    if "requires_customer_review" in data:
        job.requires_customer_review = bool(data["requires_customer_review"])
    if "checklist" in data and job.status not in ("in_progress", "completed", "customer_review"):
        job.checklist = K.jdump([dict(i, done=False) for i in clean_items(data["checklist"])])
        changed["checklist"] = True
    if "local_start" in data or "duration_min" in data or "timezone" in data:
        tz = K.tz_or(data.get("timezone") or job.timezone)
        old = K.iso(job.scheduled_start)
        st = _parse_local(data.get("local_start")) if "local_start" in data else local(job.scheduled_start, job.timezone)
        if "duration_min" in data:
            try:
                job.duration_min = max(15, min(int(data["duration_min"]), 24 * 60))
            except (TypeError, ValueError):
                raise HTTPException(422, "Duration must be minutes")
        job.timezone = tz
        job.scheduled_start = to_utc(st, tz) if st else None
        for mid in active_member_ids(ctx.db, job.id):
            c = conflicts(ctx.db, mid, job)
            if c:
                raise HTTPException(409, "That time clashes with another job for someone assigned")
        record(ctx.db, job, "rescheduled", ctx.user, data={"from": old, "to": K.iso(job.scheduled_start)})
        if job.status == "draft" and job.scheduled_start is not None:
            _move(ctx.db, job, "scheduled", ctx.user)
    if changed:
        record(ctx.db, job, "edited", ctx.user, data={"fields": sorted(changed)})
    job.version = (job.version or 1) + 1
    ctx.db.commit()
    return job


def change_status(ctx: K.OpsCtx, jid: str, to: str, data: Dict[str, Any]) -> ServiceJob:
    """Office-side status changes (confirm, cancel, approve, flag, resolve)."""
    job = view_job(ctx, jid)
    reason = K.text(data.get("reason"), 1000)
    if to == "cancelled":
        ctx.need("schedule", "cancel jobs")
        if not reason:
            raise HTTPException(422, "Say why the job is cancelled")
        job.cancel_reason = reason
    elif to == "approved":
        if not (ctx.can("approve") or (ctx.can("approve_team") and may_view(ctx, job))):
            raise HTTPException(403, "Your role can't approve completed work")
        job.approved_by_id = ctx.user.id
    elif to == "needs_attention":
        if not (ctx.can("schedule") or is_assigned(ctx, job) or ctx.can("approve_team")):
            raise HTTPException(403, "Your role can't flag this job")
        if not reason:
            raise HTTPException(422, "Say what needs attention")
        job.attention_reason = reason
    elif to in ("scheduled", "assigned", "confirmed", "customer_review", "in_progress", "completed"):
        if to in ("in_progress", "completed"):
            raise HTTPException(409, "Use Start / Complete for that")
        ctx.need("schedule", "change job status")
    else:
        raise HTTPException(422, "Unknown status")
    _move(ctx.db, job, to, ctx.user, data={"reason": reason} if reason else None)
    ctx.db.commit()
    K.audit(ctx.db, ctx.org_id, ctx.user, "service_job_status", "service_job", job.id, {"to": to})
    return job


# ── doing the work (assigned people) ──────────────────────────────────────
def _worker_or_office(ctx: K.OpsCtx, job: ServiceJob) -> None:
    if not (is_assigned(ctx, job) or ctx.can("schedule")):
        raise HTTPException(403, "Only someone assigned to this job can do that")


def start_job(ctx: K.OpsCtx, jid: str) -> ServiceJob:
    job = view_job(ctx, jid)
    _worker_or_office(ctx, job)
    if job.status == "in_progress":
        return job                                             # retry-safe
    if job.status == "needs_attention" and job.actual_start is None:
        pass
    job.actual_start = job.actual_start or datetime.utcnow()
    _move(ctx.db, job, "in_progress", ctx.user, data={"arrived": K.iso(job.actual_start)})
    ctx.db.commit()
    return job


def tick(ctx: K.OpsCtx, jid: str, key: str, done: bool) -> ServiceJob:
    job = view_job(ctx, jid)
    _worker_or_office(ctx, job)
    if job.status != "in_progress":
        raise HTTPException(409, "Start the job before working the checklist")
    items = K.jload(job.checklist, [])
    hit = next((i for i in items if i.get("key") == key), None)
    if hit is None:
        raise HTTPException(404, "Checklist item not found")
    if bool(hit.get("done")) == bool(done):
        return job
    hit["done"] = bool(done)
    hit["done_by"] = ctx.user.id if done else None
    hit["done_at"] = K.iso(datetime.utcnow()) if done else None
    job.checklist = K.jdump(items)
    record(ctx.db, job, "checklist", ctx.user, data={"key": key, "done": bool(done)})
    ctx.db.commit()
    return job


def complete_job(ctx: K.OpsCtx, jid: str, data: Dict[str, Any]) -> ServiceJob:
    job = view_job(ctx, jid)
    _worker_or_office(ctx, job)
    if job.status in ("completed", "customer_review", "approved"):
        return job                                             # retry-safe: never completes twice
    if job.status != "in_progress":
        raise HTTPException(409, "Start the job before completing it")
    missing = [i["label"] for i in K.jload(job.checklist, []) if i.get("required") and not i.get("done")]
    if missing:
        raise HTTPException(409, "Finish the required checklist first: %s" % ", ".join(missing[:5]))
    job.actual_end = datetime.utcnow()
    job.completed_by_id = ctx.user.id
    job.completion_notes = K.text(data.get("notes"), 4000)
    _move(ctx.db, job, "completed", ctx.user,
          data={"finished": K.iso(job.actual_end), "notes": bool(job.completion_notes)})
    if job.requires_customer_review:
        _move(ctx.db, job, "customer_review", ctx.user)
    ctx.db.commit()
    K.audit(ctx.db, ctx.org_id, ctx.user, "service_job_complete", "service_job", job.id, {})
    return job


def worker_note(ctx: K.OpsCtx, jid: str, kind: str, body: str) -> ServiceJob:
    """Flag an issue, ask for help or reassignment, or add a note."""
    job = view_job(ctx, jid)
    _worker_or_office(ctx, job)
    body = K.text(body, 2000)
    if not body:
        raise HTTPException(422, "Write a short note")
    if kind not in ("note", "issue", "help", "reassign"):
        raise HTTPException(422, "Unknown note type")
    record(ctx.db, job, kind, ctx.user, data={"body": body})
    if kind in ("issue", "help", "reassign") and job.status in TRANSITIONS and "needs_attention" in TRANSITIONS[job.status]:
        job.attention_reason = "%s: %s" % ({"issue": "Issue", "help": "Help requested",
                                            "reassign": "Reassignment requested"}[kind], body)
        _move(ctx.db, job, "needs_attention", ctx.user)
    ctx.db.commit()
    return job


# ── private photos ────────────────────────────────────────────────────────
def attachment_json(a: ServiceJobAttachment) -> Dict[str, Any]:
    return {"id": a.id, "filename": a.filename, "content_type": a.content_type, "size": a.byte_size,
            "caption": a.caption, "uploaded_by_id": a.uploaded_by_id, "created_at": K.iso(a.created_at)}


def add_attachment(ctx: K.OpsCtx, jid: str, data: bytes, content_type: str, filename: str,
                   caption: Optional[str]) -> ServiceJobAttachment:
    job = view_job(ctx, jid)
    _worker_or_office(ctx, job)
    if job.status in ("approved", "cancelled"):
        raise HTTPException(409, "This job is closed")
    from app.services import wholesale_files as FS          # the platform's private file store, reused
    stored = FS.put(ctx.org_id, "service_job_photo", data, content_type, db=ctx.db)
    a = ServiceJobAttachment(organization_id=ctx.org_id, job_id=job.id, storage_backend=stored["storage_backend"],
                             storage_key=stored["storage_key"], content_type=content_type,
                             byte_size=stored["byte_size"], checksum=stored["checksum"], filename=filename,
                             caption=K.text(caption, 300), uploaded_by_id=ctx.user.id)
    ctx.db.add(a)
    ctx.db.flush()
    record(ctx.db, job, "photo", ctx.user, data={"attachment_id": a.id, "checksum": a.checksum})
    ctx.db.commit()
    return a


def read_attachment(ctx: K.OpsCtx, jid: str, aid: str) -> Tuple[ServiceJobAttachment, bytes]:
    job = view_job(ctx, jid)                                   # same visibility as the job itself
    a = (ctx.db.query(ServiceJobAttachment)
         .filter(ServiceJobAttachment.organization_id == ctx.org_id, ServiceJobAttachment.job_id == job.id,
                 ServiceJobAttachment.id == aid, ServiceJobAttachment.removed_at.is_(None)).first())
    if a is None:
        raise HTTPException(404, "File not found")
    from app.services import wholesale_files as FS
    return a, FS.fetch(a.storage_backend, a.storage_key, db=ctx.db)


# ── workload ─────────────────────────────────────────────────────────────
def workload(ctx: K.OpsCtx, days: int = 14) -> List[Dict[str, Any]]:
    if not (ctx.can("schedule") or ctx.can("team") or ctx.can("view_all")):
        raise HTTPException(403, "Your role can't see the workload")
    now = datetime.utcnow()
    rows = (ctx.db.query(ServiceJobAssignment.member_id, func.count(ServiceJob.id),
                         func.coalesce(func.sum(ServiceJob.duration_min), 0))
            .join(ServiceJob, ServiceJob.id == ServiceJobAssignment.job_id)
            .filter(ServiceJob.organization_id == ctx.org_id, ServiceJobAssignment.removed_at.is_(None),
                    ServiceJob.status.in_(BUSY), ServiceJob.scheduled_start >= now - timedelta(hours=12),
                    ServiceJob.scheduled_start < now + timedelta(days=days))
            .group_by(ServiceJobAssignment.member_id).all())
    by = {r[0]: {"jobs": int(r[1]), "minutes": int(r[2])} for r in rows}
    return [dict(m, upcoming=by.get(m["id"], {"jobs": 0, "minutes": 0})) for m in K.list_team(ctx) if m["active"]]
