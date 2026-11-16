"""Service operations reporting, and SUGGESTIONS for the office.

Suggestions are rule-based and always labelled as suggestions: they never
create invoices, message customers or change contracts. The optional AI
summary of a finished job uses the existing EvoSys AI gateway through the
Acquisition AI worker - same daily call cap, same dollar cap, one call per
job at most - and only when the company has AI assist switched on.
"""
from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func

from app.models.service_ops_models import (ServiceCustomer, ServiceInvoice, ServiceJob, ServiceLocation,
                                           ServiceRecurringSchedule, ServiceRequest)
from app.services.service_ops import billing as BL
from app.services.service_ops import core as K
from app.services.service_ops import jobs as J


def report(ctx: K.OpsCtx, *, days: int = 30, now: Optional[datetime] = None) -> Dict[str, Any]:
    ctx.need("reports", "see reports")
    db, org = ctx.db, ctx.org_id
    now = now or datetime.utcnow()
    since = now - timedelta(days=days)
    q = db.query(ServiceJob).filter(ServiceJob.organization_id == org)
    by_status = dict(q.with_entities(ServiceJob.status, func.count(ServiceJob.id)).group_by(ServiceJob.status).all())
    done = (q.filter(ServiceJob.status.in_(BL.BILLABLE_JOB), ServiceJob.actual_end >= since).all())
    on_time = [j for j in done if j.actual_start and j.scheduled_start
               and j.actual_start <= j.scheduled_start + timedelta(minutes=30)]
    inv = db.query(ServiceInvoice).filter(ServiceInvoice.organization_id == org,
                                          ServiceInvoice.status != "void").all()
    issued = [i for i in inv if i.status != "draft"]
    sched = (db.query(ServiceRecurringSchedule)
             .filter(ServiceRecurringSchedule.organization_id == org, ServiceRecurringSchedule.status == "active").all())
    return {
        "period_days": days,
        "jobs_by_status": {J.STATUS_LABEL.get(k, k): v for k, v in by_status.items()},
        "completed_in_period": len(done),
        "on_time_rate": round(len(on_time) / len(done), 2) if done else None,
        "active_customers": db.query(ServiceCustomer).filter(ServiceCustomer.organization_id == org,
                                                             ServiceCustomer.status == "active").count(),
        "active_locations": db.query(ServiceLocation).filter(ServiceLocation.organization_id == org,
                                                             ServiceLocation.active.is_(True)).count(),
        "recurring_agreements": len(sched),
        "recurring_monthly_value": round(sum(s.expected_monthly_value or 0 for s in sched), 2),
        "invoiced_total": round(sum(i.total for i in issued), 2),
        "confirmed_paid": round(sum(i.amount_confirmed for i in issued), 2),
        "reported_not_confirmed": round(sum(i.amount_reported for i in issued), 2),
        "outstanding": round(sum(i.total - i.amount_confirmed for i in issued), 2),
        "overdue_invoices": sum(1 for i in issued if BL.display_status(i) == "overdue"),
        "open_requests": db.query(ServiceRequest).filter(ServiceRequest.organization_id == org,
                                                         ServiceRequest.status == "open").count(),
    }


def suggestions(ctx: K.OpsCtx, *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """What deserves the office's attention. Read-only; each item says what
    it is and links to the record - a person decides."""
    if not (ctx.can("schedule") or ctx.can("reports")):
        raise HTTPException(403, "Your role can't see suggestions")
    db, org = ctx.db, ctx.org_id
    now = now or datetime.utcnow()
    out: List[Dict[str, Any]] = []
    jobs = (db.query(ServiceJob).filter(ServiceJob.organization_id == org, ServiceJob.status.in_(J.BUSY))
            .filter(ServiceJob.scheduled_start < now + timedelta(days=3)).all())
    for j in jobs:
        if j.scheduled_start is None:
            continue
        if j.status in ("scheduled",) and not J.active_member_ids(db, j.id) and j.scheduled_start > now:
            out.append({"kind": "unassigned", "severity": "high", "job_id": j.id,
                        "text": "No one is assigned to %s on %s" % (j.service_type, K.iso(j.scheduled_start))})
        late = j.scheduled_start + timedelta(minutes=(j.duration_min or 60) + 60)
        if j.status in ("scheduled", "assigned", "confirmed") and late < now:
            out.append({"kind": "missed_visit", "severity": "high", "job_id": j.id,
                        "text": "%s was due %s and hasn't been started" % (j.service_type, K.iso(j.scheduled_start))})
        if j.status == "in_progress" and j.actual_start and j.actual_start + timedelta(
                minutes=(j.duration_min or 60) * 3) < now:
            out.append({"kind": "overrun", "severity": "medium", "job_id": j.id,
                        "text": "%s has been in progress far longer than planned" % j.service_type})
    for j in db.query(ServiceJob).filter(ServiceJob.organization_id == org,
                                         ServiceJob.status == "needs_attention").limit(50):
        out.append({"kind": "needs_attention", "severity": "high", "job_id": j.id,
                    "text": "%s needs attention: %s" % (j.service_type, (j.attention_reason or "")[:160])})
    invoiced = {r[0] for r in db.query(ServiceInvoice.job_id).filter(ServiceInvoice.organization_id == org,
                                                                     ServiceInvoice.job_id.isnot(None))}
    for j in (db.query(ServiceJob).filter(ServiceJob.organization_id == org,
                                          ServiceJob.status.in_(BL.BILLABLE_JOB))
              .order_by(ServiceJob.actual_end.desc()).limit(200)):
        if j.id not in invoiced and j.price:
            out.append({"kind": "not_invoiced", "severity": "medium", "job_id": j.id,
                        "text": "Finished work not invoiced yet: %s ($%.2f)" % (j.service_type, j.price)})
    for i in db.query(ServiceInvoice).filter(ServiceInvoice.organization_id == org,
                                             ServiceInvoice.status.in_(("issued", "partially_paid"))):
        if BL.display_status(i) == "overdue":
            out.append({"kind": "overdue_invoice", "severity": "medium", "invoice_id": i.id,
                        "text": "Invoice %s is past due ($%.2f open)" % (i.number, i.total - i.amount_confirmed)})
        if i.amount_reported > 0:
            out.append({"kind": "confirm_payment", "severity": "low", "invoice_id": i.id,
                        "text": "A payment on %s was reported - confirm when it arrives" % i.number})
    soon = now.date() + timedelta(days=30)
    for s in db.query(ServiceRecurringSchedule).filter(ServiceRecurringSchedule.organization_id == org,
                                                       ServiceRecurringSchedule.status == "active",
                                                       ServiceRecurringSchedule.end_date.isnot(None),
                                                       ServiceRecurringSchedule.end_date <= soon):
        out.append({"kind": "renewal", "severity": "medium", "schedule_id": s.id,
                    "text": "Recurring %s ends %s - time to talk renewal" % (s.service_type, s.end_date.isoformat())})
    # Upsell: a customer served for one trade only, whose location type usually buys more.
    cats = (db.query(ServiceLocation.customer_id, func.count(func.distinct(ServiceLocation.service_category)))
            .filter(ServiceLocation.organization_id == org, ServiceLocation.active.is_(True))
            .group_by(ServiceLocation.customer_id).all())
    for cid, n in cats[:200]:
        if n == 1:
            done = db.query(ServiceJob).filter(ServiceJob.customer_id == cid,
                                               ServiceJob.status.in_(BL.BILLABLE_JOB)).count()
            if done >= 3:
                c = db.query(ServiceCustomer).filter(ServiceCustomer.id == cid).first()
                out.append({"kind": "upsell", "severity": "low", "customer_id": cid,
                            "text": "%s has had %d visits for one service - a good moment to offer another"
                                    % (c.name if c else "A customer", done)})
    order = {"high": 0, "medium": 1, "low": 2}
    for x in out:
        x["suggestion"] = True
    return sorted(out, key=lambda x: order[x["severity"]])[:100]


def ai_summary(ctx: K.OpsCtx, jid: str) -> Dict[str, Any]:
    """One short AI summary of a finished job's notes, on request. Gated:
    company switch + the existing AI policy/budget. A suggestion, not a record."""
    if not (ctx.can("schedule") or ctx.can("reports")):
        raise HTTPException(403, "Your role can't use AI assist")
    if not (ctx.settings and ctx.settings.ai_assist_enabled):
        raise HTTPException(402, "AI assist is not switched on for this company")
    job = J.view_job(ctx, jid)
    if job.status not in BL.BILLABLE_JOB:
        raise HTTPException(409, "AI summaries are for finished jobs")
    items = K.jload(job.checklist, [])
    facts = {"service": job.service_type, "checklist": [{"item": i["label"], "done": bool(i.get("done"))} for i in items],
             "notes": (job.completion_notes or "")[:1500]}
    from app.services.acquisition import ai_worker as AI
    import json
    res = AI.invoke(ctx.db, purpose="service_ops_job_summary", capability="acquisition_draft", org_id=ctx.org_id,
                    job_id="svcjob:%s" % job.id, messages=[
                        {"role": "system", "content": "Summarize a completed service visit for the office in "
                         "2-3 plain sentences and list any follow-up the notes mention. Use ONLY the facts given. "
                         "Reply as JSON: {\"summary\": str, \"follow_ups\": [str]}."},
                        {"role": "user", "content": json.dumps(facts)}])
    if not res.get("ok"):
        return {"ok": False, "reason": res.get("reason"), "suggestion": True}
    d = res["data"]
    return {"ok": True, "suggestion": True, "summary": str(d.get("summary") or "")[:800],
            "follow_ups": [str(x)[:200] for x in (d.get("follow_ups") or [])][:5], "reused": bool(res.get("reused"))}
