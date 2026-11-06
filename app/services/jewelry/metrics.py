"""Dashboard and reports - counted from stored rows only, never estimated.

Every number returned here comes with a plain definition (DEFINITIONS), and a
figure that depends on a connection that does not exist yet (POS, message
delivery) is returned with connected=False so the screen can say "Not
connected" instead of showing a number.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.models.jewelry_models import (JewelryAiExchange, JewelryAppointmentDetail, JewelryCampaign,
                                       JewelryTransaction)
from app.models.models import BookingLink, Lead
from app.models.work_models import LeadTask
from app.services.jewelry import common as C

DEFINITIONS = {
    "total_customers": "Every customer record in this workspace you are allowed to see.",
    "active_customers": "Customers with a recorded visit or transaction in the last %d days, or added in that time." % C.INACTIVE_DAYS,
    "inactive_customers": "Customers with no recorded visit or transaction in the last %d days." % C.INACTIVE_DAYS,
    "returning_customers": "Customers with two or more recorded transactions or completed visits.",
    "new_customers": "Customer records created in the selected period.",
    "upcoming_appointments": "Scheduled, confirmed or checked-in appointments in the next 7 days.",
    "appointments": "Appointments whose start time falls in the selected period (any status).",
    "completed_visits": "Appointments in the period marked Completed.",
    "transactions": "Store transactions staff recorded in the period (there is no POS connection).",
    "revenue": "Total of recorded sales and trades in the period. Gold purchased FROM customers is shown separately.",
    "purchases": "Total paid to customers for gold and items the store bought, as recorded.",
    "reactivated": "Customers whose recorded transaction in the period came after %d+ days with no activity." % C.INACTIVE_DAYS,
    "campaigns": "Campaigns by status. Sending is not connected, so delivery and replies read zero.",
    "responses": "Inbound replies stored for these customers in the period (only arrive once messaging is connected).",
    "ai_activity": "Questions answered in the AI receptionist (test mode) in the period, and how many were handed to a person.",
    "follow_ups": "Open follow-up tasks, and how many are past due.",
    "conversion": "Completed visits divided by appointments in the period.",
    "marketing_cost": "Total of the costs entered on campaigns created in the period.",
}

PERIODS = {"7d": 7, "30d": 30, "90d": 90, "12m": 365}


def period(key: Optional[str], org) -> Tuple[datetime, datetime, str]:
    key = key if key in PERIODS else "30d"
    end = C.local_now(org) + timedelta(minutes=1)
    return end - timedelta(days=PERIODS[key]), end, key


def _buckets(d0: datetime, d1: datetime) -> List[Tuple[datetime, datetime, str]]:
    days = (d1 - d0).days
    step = 1 if days <= 7 else (7 if days <= 100 else 30)
    out, cur = [], d0
    while cur < d1:
        nxt = min(cur + timedelta(days=step), d1)
        lab = cur.strftime("%b %-d") if step < 30 else cur.strftime("%b")
        out.append((cur, nxt, lab))
        cur = nxt
    return out


def compute(ctx: C.Ctx, key: Optional[str]) -> Dict[str, Any]:
    from app.services.jewelry import appointments as AP
    from app.services.jewelry import customers as CU
    d0, d1, key = period(key, ctx.org)
    # Rows stamped by the database (created_at, received_at) are UTC; dates
    # staff enter (transactions, appointment times) are the store's local time.
    u1 = datetime.utcnow() + timedelta(minutes=1)
    u0 = u1 - (d1 - d0)
    leads = CU.scoped_query(ctx).all()
    ids = [l.id for l in leads]
    act = CU.activity_index(ctx, ids)
    now = C.local_now(ctx.org)
    inactive = sum(1 for l in leads if CU.is_inactive(l, act[l.id], now))
    returning = sum(1 for l in leads if CU.is_returning(act[l.id]))
    new = sum(1 for l in leads if l.created_at and u0 <= l.created_at < u1)

    tq = ctx.db.query(JewelryTransaction).filter(JewelryTransaction.organization_id == ctx.org_id,
                                                 JewelryTransaction.lead_id.in_(ids or ["-"]))
    all_txns = tq.all()
    txns = [t for t in all_txns if d0 <= t.occurred_on < d1]
    revenue = round(sum(float(t.amount or 0) for t in txns if t.kind in ("sale", "trade")), 2)
    purchases = round(sum(float(t.amount or 0) for t in txns if t.kind == "purchase"), 2)

    # Reactivation: a transaction in the period preceded by a 180+ day gap.
    by_lead: Dict[str, List[datetime]] = {}
    for t in all_txns:
        by_lead.setdefault(t.lead_id, []).append(t.occurred_on)
    reactivated = 0
    for lid, dates in by_lead.items():
        dates.sort()
        for i in range(1, len(dates)):
            if d0 <= dates[i] < d1 and (dates[i] - dates[i - 1]).days >= C.INACTIVE_DAYS:
                reactivated += 1
                break

    ap = AP.summary(ctx, d0, d1)
    upcoming = AP.upcoming(ctx, 7)
    completed = ap["by_status"].get("completed", 0)

    camps = ctx.db.query(JewelryCampaign).filter(JewelryCampaign.organization_id == ctx.org_id).all()
    camp_status: Dict[str, int] = {}
    for c in camps:
        camp_status[c.status] = camp_status.get(c.status, 0) + 1
    cost = round(sum(float(c.marketing_cost or 0) for c in camps if c.created_at and u0 <= c.created_at < u1), 2)

    responses = 0
    try:
        from app.models.models import Reply
        responses = (ctx.db.query(Reply).filter(Reply.lead_id.in_(ids or ["-"]),
                                                Reply.received_at >= u0, Reply.received_at < u1).count())
    except Exception:  # noqa: BLE001
        responses = 0

    ai_q = ctx.db.query(JewelryAiExchange).filter(JewelryAiExchange.organization_id == ctx.org_id,
                                                  JewelryAiExchange.created_at >= u0, JewelryAiExchange.created_at < u1)
    ai_rows = ai_q.all()
    tasks_q = ctx.db.query(LeadTask).filter(LeadTask.organization_id == ctx.org_id, LeadTask.status == "open")
    if not ctx.manager:
        tasks_q = tasks_q.filter(LeadTask.assigned_to_id == ctx.user.id)
    tasks = tasks_q.all()
    utc_now = datetime.utcnow()

    series = []
    shift = u0 - d0
    for a, b, lab in _buckets(d0, d1):
        series.append({"label": lab,
                       "new_customers": sum(1 for l in leads if l.created_at and a + shift <= l.created_at < b + shift),
                       "revenue": round(sum(float(t.amount or 0) for t in txns
                                            if t.kind in ("sale", "trade") and a <= t.occurred_on < b), 2),
                       "transactions": sum(1 for t in txns if a <= t.occurred_on < b)})

    recent_txns = sorted(txns, key=lambda t: t.occurred_on, reverse=True)[:8]
    lead_by_id = {l.id: l for l in leads}
    return {
        "period": key, "start": d0.isoformat(timespec="minutes"), "end": d1.isoformat(timespec="minutes"),
        "scope": "business" if ctx.manager else "my customers",
        "customers": {"total": len(leads), "active": len(leads) - inactive, "inactive": inactive,
                      "returning": returning, "new": new, "reactivated": reactivated,
                      "opted_out": sum(1 for l in leads if CU.opted_out(l))},
        "appointments": {"upcoming": len(upcoming), "in_period": ap["total"], "completed": completed,
                         "by_status": ap["by_status"], "by_type": ap["by_type"],
                         "conversion": round(completed / ap["total"] * 100, 1) if ap["total"] else None,
                         "next": upcoming[:6]},
        "transactions": {"count": len(txns), "revenue": revenue, "purchases": purchases,
                         "pos_connected": False,
                         "recent": [dict(CU.txn_json(t), customer_name=C.lead_name(lead_by_id[t.lead_id]))
                                    for t in recent_txns if t.lead_id in lead_by_id]},
        "campaigns": {"by_status": camp_status, "total": len(camps), "sent": 0, "delivered": 0,
                      "replies": 0, "delivery_connected": False, "marketing_cost": cost},
        "responses": {"count": responses, "messaging_connected": False},
        "ai": {"questions": len(ai_rows), "handed_to_person": sum(1 for r in ai_rows if r.needs_human),
               "blocked": sum(1 for r in ai_rows if r.answered_by == "blocked"), "mode": "test"},
        "follow_ups": {"open": len(tasks), "overdue": sum(1 for t in tasks if t.due_at and t.due_at < utc_now)},
        "series": series,
        "definitions": DEFINITIONS,
    }


def report(ctx: C.Ctx, key: Optional[str]) -> Dict[str, Any]:
    C.require_manager(ctx)
    from app.services.jewelry import campaigns as CA
    out = compute(ctx, key)
    camps = ctx.db.query(JewelryCampaign).filter(JewelryCampaign.organization_id == ctx.org_id) \
        .order_by(JewelryCampaign.created_at.desc()).all()
    out["campaign_rows"] = [dict(CA.camp_json(c), results=CA.results(ctx, c)) for c in camps]
    return out


def ai_summary(ctx: C.Ctx) -> Dict[str, Any]:
    rows = (ctx.db.query(JewelryAiExchange).filter(JewelryAiExchange.organization_id == ctx.org_id)
            .order_by(JewelryAiExchange.created_at.desc()).limit(400).all())
    since = datetime.utcnow() - timedelta(days=7)
    week = [r for r in rows if r.created_at and r.created_at >= since]
    return {"total": len(rows), "last_7_days": len(week),
            "handed_to_person": sum(1 for r in week if r.needs_human),
            "operator_answers": sum(1 for r in week if r.answered_by == "operator"),
            "blocked": sum(1 for r in week if r.answered_by == "blocked")}
