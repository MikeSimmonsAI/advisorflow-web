"""Recurring service agreements and the bounded creation of their jobs.

Jobs are created only inside a horizon (HORIZON_DAYS ahead, in the company's
own time zone), never unlimited. One job per (schedule, local date) is
guaranteed by a database unique key, so a scheduler restart, a second Render
instance or a retried pass can never create a duplicate. Local wall time is
kept across daylight-saving changes (09:00 stays 09:00), and the UTC instant
is computed per date by the tz database.

Skipping, moving or cancelling ONE visit changes that visit's job only; the
series keeps its rule and the unique key stops it being recreated.
"""
from __future__ import annotations

import calendar
import logging
import re
from datetime import date as date_cls, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.service_ops_models import (ServiceJob, ServiceOpsSettings, ServiceRecurringSchedule)
from app.services.service_ops import core as K
from app.services.service_ops import jobs as J

log = logging.getLogger(__name__)
FREQUENCIES = ("one_time", "daily", "weekly", "biweekly", "monthly", "custom")
FREQ_LABEL = {"one_time": "One time", "daily": "Daily", "weekly": "Weekly", "biweekly": "Every 2 weeks",
              "monthly": "Monthly", "custom": "Every N days"}
HORIZON_DAYS = 35
MAX_PER_SCHEDULE_PASS = 60
MAX_SCHEDULES_PASS = 300
VISITS_PER_MONTH = {"daily": 30.4, "weekly": 4.35, "biweekly": 2.17, "monthly": 1.0}


def occurrences(s: ServiceRecurringSchedule, start: date_cls, end: date_cls) -> List[date_cls]:
    """Local dates of visits between start and end (inclusive)."""
    out: List[date_cls] = []
    first, last = s.start_date, min(end, s.end_date) if s.end_date else end
    if last < start or last < first:
        return out
    if s.frequency == "one_time":
        return [first] if start <= first <= last else []
    if s.frequency == "monthly":
        n = 0
        while True:
            y, m = first.year + (first.month - 1 + n) // 12, (first.month - 1 + n) % 12 + 1
            d = date_cls(y, m, min(first.day, calendar.monthrange(y, m)[1]))
            if d > last:
                break
            if d >= start:
                out.append(d)
            n += 1
        return out
    step = {"daily": 1, "weekly": 7, "biweekly": 14}.get(s.frequency) or max(1, min(int(s.interval_days or 7), 365))
    k = max(0, (start - first).days // step)
    d = first + timedelta(days=k * step)
    while d <= last:
        if d >= start:
            out.append(d)
        d += timedelta(days=step)
    return out


def _today(tz: str, now: Optional[datetime] = None) -> date_cls:
    return J.local(now or datetime.utcnow(), tz).date()


def materialize(db: Session, s: ServiceRecurringSchedule, *, now: Optional[datetime] = None,
                horizon_days: int = HORIZON_DAYS) -> int:
    """Create the jobs this schedule owes inside the horizon. Idempotent."""
    if s.status != "active":
        return 0
    org = db.query(Organization).filter(Organization.id == s.organization_id).first()
    actor = db.query(User).filter(User.id == s.created_by_id).first()
    if org is None or actor is None:
        return 0
    ctx = K.OpsCtx(db, actor, org, "company_admin", None)
    today = _today(s.timezone, now)
    frm = max(today, s.start_date)
    if s.materialized_through and s.materialized_through >= frm:
        frm = s.materialized_through + timedelta(days=1)
    upto = today + timedelta(days=horizon_days)
    made = 0
    hh, mm = (int(x) for x in s.start_time.split(":"))
    for d in occurrences(s, frm, upto)[:MAX_PER_SCHEDULE_PASS]:
        job, created = J.create_job(ctx, {
            "location_id": s.location_id, "service_type": s.service_type,
            "local_start": datetime(d.year, d.month, d.day, hh, mm).strftime("%Y-%m-%dT%H:%M"),
            "timezone": s.timezone, "duration_min": s.duration_min, "instructions": s.instructions,
            "price": s.price_per_visit, "estimate_id": s.estimate_id,
            "checklist_template_id": s.checklist_template_id, "_source": "recurring"},
            schedule_id=s.id, occurrence_date=d, opportunity_id=s.opportunity_id, actor=actor,
            assign_ids=K.jload(s.assigned_member_ids, []))
        made += 1 if created else 0
        s.materialized_through = d
    if s.frequency == "one_time" or (s.end_date and s.end_date <= upto):
        s.materialized_through = max(s.materialized_through or upto, min(upto, s.end_date or upto))
    else:
        s.materialized_through = max(s.materialized_through or frm, upto)
    db.commit()
    return made


def run_pass(db: Session, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """One bounded pass over every active schedule of every company with
    Service Operations switched on. Safe to run on two instances at once."""
    enabled = [r[0] for r in db.query(ServiceOpsSettings.organization_id)
               .filter(ServiceOpsSettings.ops_enabled.is_(True)).all()]
    if not enabled:
        return {"schedules": 0, "created": 0, "failed": 0}
    rows = (db.query(ServiceRecurringSchedule)
            .filter(ServiceRecurringSchedule.organization_id.in_(enabled),
                    ServiceRecurringSchedule.status == "active")
            .order_by(ServiceRecurringSchedule.materialized_through.is_(None).desc(),
                      ServiceRecurringSchedule.materialized_through)
            .limit(MAX_SCHEDULES_PASS).all())
    made = failed = 0
    for s in rows:
        try:
            made += materialize(db, s, now=now)
        except Exception:                                    # noqa: BLE001 - one bad schedule never stops the rest
            db.rollback()
            failed += 1
            log.exception("service schedule %s failed to materialize", s.id)
    return {"schedules": len(rows), "created": made, "failed": failed}


# ── agreements ────────────────────────────────────────────────────────────
def _date(v, field: str) -> Optional[date_cls]:
    if v in (None, ""):
        return None
    try:
        return date_cls.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(422, "%s must be a date like 2026-12-01" % field)


def get_schedule(db: Session, org_id: str, sid: str) -> ServiceRecurringSchedule:
    s = (db.query(ServiceRecurringSchedule)
         .filter(ServiceRecurringSchedule.organization_id == org_id, ServiceRecurringSchedule.id == sid).first())
    if s is None:
        raise HTTPException(404, "Schedule not found")
    return s


def create_schedule(ctx: K.OpsCtx, data: Dict[str, Any], *, now: Optional[datetime] = None) -> ServiceRecurringSchedule:
    ctx.need("schedule", "set up recurring service")
    db = ctx.db
    loc = K.get_location(db, ctx.org_id, data.get("location_id") or "-")
    freq = data.get("frequency")
    if freq not in FREQUENCIES:
        raise HTTPException(422, "Frequency must be one of: %s" % ", ".join(FREQUENCIES))
    start = _date(data.get("start_date"), "Start date")
    if start is None:
        raise HTTPException(422, "A schedule needs a start date")
    end = _date(data.get("end_date"), "End date")
    if end and end < start:
        raise HTTPException(422, "End date is before the start date")
    st = str(data.get("start_time") or "09:00")
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", st):
        raise HTTPException(422, "Start time must be like 09:00")
    interval = None
    if freq == "custom":
        try:
            interval = int(data.get("interval_days"))
        except (TypeError, ValueError):
            raise HTTPException(422, "Custom schedules need interval_days")
        if not 1 <= interval <= 365:
            raise HTTPException(422, "interval_days must be 1-365")
    opp_id = data.get("opportunity_id") or None
    if opp_id:
        from app.services.acquisition import engine as AE
        AE.load_opp(db, ctx.org_id, opp_id, client=True)
        dup = (db.query(ServiceRecurringSchedule)
               .filter(ServiceRecurringSchedule.organization_id == ctx.org_id,
                       ServiceRecurringSchedule.opportunity_id == opp_id,
                       ServiceRecurringSchedule.location_id == loc.id,
                       ServiceRecurringSchedule.status != "cancelled").first())
        if dup is not None and not data.get("allow_second"):
            return dup                                      # double click / retry: the same agreement
    try:
        dur = max(15, min(int(data.get("duration_min") or loc.standard_duration_min or 60), 24 * 60))
    except (TypeError, ValueError):
        raise HTTPException(422, "Duration must be minutes")
    price = K.money(data.get("price_per_visit"), "Price per visit")
    s = ServiceRecurringSchedule(
        organization_id=ctx.org_id, customer_id=loc.customer_id, location_id=loc.id,
        service_type=K.text(data.get("service_type"), 120) or K.CATEGORY_LABEL.get(loc.service_category, "Service"),
        frequency=freq, interval_days=interval, start_date=start, end_date=end, start_time=st, duration_min=dur,
        timezone=K.tz_or(data.get("timezone") or loc.timezone, K.company_tz(db, ctx.org_id)),
        assigned_member_ids=K.jdump(K._member_ids(db, ctx.org_id, data.get("assigned_member_ids") or [])),
        checklist_template_id=data.get("checklist_template_id") or None, opportunity_id=opp_id,
        estimate_id=data.get("estimate_id") or None, price_per_visit=price,
        expected_monthly_value=K.money(data.get("expected_monthly_value"), "Monthly value")
        or (round(price * VISITS_PER_MONTH[freq], 2) if price and freq in VISITS_PER_MONTH else None),
        instructions=K.text(data.get("instructions"), 4000), status="active", created_by_id=ctx.user.id)
    db.add(s)
    db.commit()
    K.audit(db, ctx.org_id, ctx.user, "service_schedule_create", "service_recurring_schedule", s.id,
            {"frequency": freq, "location_id": loc.id})
    materialize(db, s, now=now)
    return s


def set_schedule_status(ctx: K.OpsCtx, sid: str, status: str, reason: Optional[str] = None,
                        *, now: Optional[datetime] = None) -> ServiceRecurringSchedule:
    ctx.need("schedule", "change recurring service")
    if status not in ("active", "paused", "cancelled"):
        raise HTTPException(422, "Unknown status")
    s = get_schedule(ctx.db, ctx.org_id, sid)
    if s.status == "cancelled":
        raise HTTPException(409, "A cancelled schedule can't be changed; create a new one")
    s.status = status
    cancelled = 0
    if status == "cancelled":
        # Future visits nobody has started are cancelled (never billed).
        for j in (ctx.db.query(ServiceJob)
                  .filter(ServiceJob.schedule_id == s.id, ServiceJob.status.in_(("draft", "scheduled", "assigned",
                                                                                 "confirmed")),
                          ServiceJob.scheduled_start > (now or datetime.utcnow())).all()):
            j.cancel_reason = "Recurring service cancelled" + (": %s" % reason if reason else "")
            J._move(ctx.db, j, "cancelled", ctx.user, data={"reason": "schedule cancelled"})
            cancelled += 1
    ctx.db.commit()
    K.audit(ctx.db, ctx.org_id, ctx.user, "service_schedule_status", "service_recurring_schedule", s.id,
            {"status": status, "future_jobs_cancelled": cancelled})
    if status == "active":
        materialize(ctx.db, s, now=now)
    return s


def skip_occurrence(ctx: K.OpsCtx, jid: str, reason: Optional[str]) -> ServiceJob:
    """Skip ONE visit of a series: that job is cancelled, the series is untouched."""
    ctx.need("schedule", "skip visits")
    job = J.view_job(ctx, jid)
    if not job.schedule_id:
        raise HTTPException(409, "This job isn't part of a recurring schedule")
    job.cancel_reason = "Skipped" + (": %s" % reason if reason else "")
    J._move(ctx.db, job, "cancelled", ctx.user, data={"skipped": True, "reason": reason})
    ctx.db.commit()
    return job


def schedule_json(db: Session, s: ServiceRecurringSchedule) -> Dict[str, Any]:
    nxt = (db.query(ServiceJob).filter(ServiceJob.schedule_id == s.id, ServiceJob.status.in_(J.BUSY),
                                       ServiceJob.scheduled_start >= datetime.utcnow())
           .order_by(ServiceJob.scheduled_start).first())
    return {"id": s.id, "customer_id": s.customer_id, "location_id": s.location_id, "service_type": s.service_type,
            "frequency": s.frequency, "frequency_label": FREQ_LABEL.get(s.frequency, s.frequency),
            "interval_days": s.interval_days, "start_date": s.start_date.isoformat(),
            "end_date": s.end_date.isoformat() if s.end_date else None, "start_time": s.start_time,
            "duration_min": s.duration_min, "timezone": s.timezone,
            "assigned_member_ids": K.jload(s.assigned_member_ids, []), "opportunity_id": s.opportunity_id,
            "estimate_id": s.estimate_id, "price_per_visit": s.price_per_visit,
            "expected_monthly_value": s.expected_monthly_value, "status": s.status,
            "materialized_through": s.materialized_through.isoformat() if s.materialized_through else None,
            "next_visit": K.iso(nxt.scheduled_start) if nxt else None}


def list_schedules(ctx: K.OpsCtx) -> List[Dict[str, Any]]:
    ctx.need("view_all", "see recurring service")
    rows = (ctx.db.query(ServiceRecurringSchedule)
            .filter(ServiceRecurringSchedule.organization_id == ctx.org_id)
            .order_by(ServiceRecurringSchedule.created_at.desc()).limit(500).all())
    return [schedule_json(ctx.db, s) for s in rows]
