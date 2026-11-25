"""My Day, individual performance and the Management Command Center (s11,
s32-34). Every number comes from authorized records; nothing is estimated,
and a rate with a zero denominator is None ("N/A" on screen).

Metric definitions (docs/restland-bookit/revenue-attribution.md):
* Show rate = Showed / (Showed + No-Show). Cancelled and rescheduled are not
  no-shows.
* Closing rate = attended appointments with at least one closed sale /
  attended appointments.
* Average contract value = net closed value / closed contracts.
* Closed value per generated appointment = net closed value of sales tied to
  EvoSys-generated appointments / EvoSys-generated appointments, same window.
* Cohorts: appointments by appointment date; sales by sale date; referrals
  by creation date. Labeled in the response.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.bookit_models import (BookitAddCase, BookitDisposition, BookitPaperwork, BookitReferralBatch,
                                      BookitReferralContact, BookitSale, BookitServiceRequest)
from app.services.bookit.core import Who
from app.services.programs import restland_sheet as RS
from app.services.programs import team_board as TB

EVOSYS_SOURCES = ("evosys_appointment", "evosys_follow_up", "evo6_block", "add_lead")


def today_ct() -> date:
    return RS._central(datetime.utcnow()).date()


def window(name: Optional[str], start: Optional[str] = None, end: Optional[str] = None) -> Tuple[date, date, str]:
    """(first day, last day, label) in Central time. Default: this month."""
    t = today_ct()
    name = (name or "this_month").lower()
    if name == "today":
        return t, t, "Today"
    if name == "yesterday":
        y = t - timedelta(days=1)
        return y, y, "Yesterday"
    if name == "this_week":
        s = t - timedelta(days=t.weekday())
        return s, t, "This week"
    if name == "last_week":
        s = t - timedelta(days=t.weekday() + 7)
        return s, s + timedelta(days=6), "Last week"
    if name == "last_month":
        e = t.replace(day=1) - timedelta(days=1)
        return e.replace(day=1), e, "Last month"
    if name == "quarter":
        q = (t.month - 1) // 3 * 3 + 1
        return t.replace(month=q, day=1), t, "This quarter"
    if name == "ytd":
        return t.replace(month=1, day=1), t, "Year to date"
    if name == "lifetime":
        return date(2000, 1, 1), t, "Lifetime"
    if name == "custom" and start and end:
        try:
            s, e = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError:
            s, e = t.replace(day=1), t
        return min(s, e), max(s, e), "%s - %s" % (min(s, e).isoformat(), max(s, e).isoformat())
    return t.replace(day=1), t, "This month"


def rate(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


def _in(d: Optional[str], s: date, e: date) -> bool:
    return bool(d) and s.isoformat() <= d[:10] <= e.isoformat()


# ── shared pieces ───────────────────────────────────────────────────────────

def _sales(db, org: str, w: Who, person: Optional[str]):
    q = db.query(BookitSale).filter(BookitSale.organization_id == org, BookitSale.status != "void")
    rows = q.all()
    return [s for s in rows if w.sees(s.closing_advisor, s.assigned_advisor)
            and (person is None or person in (s.closing_advisor, s.assigned_advisor))]


def _sale_totals(sales, s: date, e: date) -> Dict[str, Any]:
    closed = [x for x in sales if x.status == "closed" and not x.amount_missing and x.sale_date
              and s <= x.sale_date <= e]
    pending = [x for x in sales if x.status == "pending"]
    net = sum(x.net_cents for x in closed)
    return {"closed_value_cents": net, "closed_contracts": len(closed),
            "average_contract_cents": (net // len(closed)) if closed else None,
            "pending_opportunities": len(pending), "pending_value_cents": sum(x.net_cents for x in pending),
            "missing_amounts": sum(1 for x in sales if x.amount_missing)}


def _legacy_sold_missing(data) -> int:
    """'Sold? Yes' on the sheet with no BookIT sale behind it."""
    return sum(1 for a in data["appointments"] if a.get("sold") == "Yes" and not a.get("_has_sale"))


def _mark_sales(db, org, data) -> None:
    ids = [a["id"] for a in data["appointments"]]
    have = {x for (x,) in db.query(BookitSale.appointment_id).filter(
        BookitSale.organization_id == org, BookitSale.appointment_id.in_(ids or ["-"]),
        BookitSale.status == "closed")}
    for a in data["appointments"]:
        a["_has_sale"] = a["id"] in have


def _dispositions(db, org, ids) -> Dict[str, BookitDisposition]:
    return {d.appointment_id: d for d in db.query(BookitDisposition).filter(
        BookitDisposition.organization_id == org, BookitDisposition.appointment_id.in_(list(ids) or ["-"]))}


# ── My Day ──────────────────────────────────────────────────────────────────

def my_day(db: Session, prog, w: Who, person: Optional[str] = None) -> Dict[str, Any]:
    """The advisor's day. A manager may look at one person's day (?person=)."""
    org = prog.organization_id
    me = person if (w.manager and person) else (None if w.manager else w.name)
    data = TB.work(db, prog, person=me)
    _mark_sales(db, org, data)
    t = today_ct().isoformat()
    disp = _dispositions(db, org, [a["id"] for a in data["appointments"]])
    actions: List[Dict[str, Any]] = []

    def act(kind, icon, ref_type, ref_id, name, reason, due, status, nxt, rank):
        actions.append({"kind": kind, "icon": icon, "ref_type": ref_type, "ref_id": ref_id, "name": name,
                        "reason": reason, "due": due, "status": status, "next": nxt, "_rank": rank})
    appts_today = upcoming = pending_out = 0
    for a in data["appointments"]:
        st = str(a["status"] or "")
        if a["date"] == t and not st.startswith("Cancel"):
            appts_today += 1
            act("appointment", "calendar", "appointment", a["id"], a["family"], "Appointment today at %s"
                % (a["time"] or "?"), a["date"], st, "Prepare and meet the family", 2)
        if a["upcoming"]:
            upcoming += 1
        d = disp.get(a["id"])
        past = a["date"] and a["date"] < t
        if past and st.startswith(("Booked", "Confirmed")) and not a["outcome"] and not (d and d.closed_at):
            pending_out += 1
            act("missing_outcome", "clipboard", "appointment", a["id"], a["family"], "Outcome not recorded",
                a["date"], st, "Close the appointment ticket", 3)
        if a.get("sold") == "Yes" and not a["_has_sale"]:
            act("sale_amounts", "dollar", "appointment", a["id"], a["family"], "Sold - amounts not recorded",
                a["date"], "Sale", "Record the products and amounts", 4)
    due = overdue = 0
    for f in data["follow_ups"]:
        if not f["open"]:
            continue
        if f["due_now"]:
            due += 1
            late = f["follow_up_on"] and f["follow_up_on"] < t
            if late:
                overdue += 1
            callback = (f["why"] or "").startswith("Asked for a call back")
            act("follow_up", "phone" if callback else "repeat", "follow_up", f["id"], f["family"],
                (f["why"] or "Follow up").split("\n")[0][:160], f["follow_up_on"] or "now",
                "Overdue" if late else "Due now", "Call or text, then record what happened",
                0 if callback else (1 if late else 2))
    # BookIT's own records
    sales = _sales(db, org, w, me)
    pend = [s for s in sales if s.status == "pending"]
    for s in pend[:20]:
        act("pending_sale", "hourglass", "sale", s.id, s.contract_ref or "Pending decision", "Pending decision",
            s.sale_date.isoformat() if s.sale_date else None, "Pending", "Follow up on the decision", 4)
    lead_ids = list({a.get("lead_id") for a in data["appointments"] if a.get("lead_id")})
    pta_open = db.query(BookitPaperwork).filter(BookitPaperwork.organization_id == org,
                                                BookitPaperwork.lead_id.in_(lead_ids or ["-"]),
                                                BookitPaperwork.pta_status.in_(["pending", "needs_update"])).count()
    bq = db.query(BookitReferralBatch).filter(BookitReferralBatch.organization_id == org,
                                              BookitReferralBatch.collection_status.in_(["not_started", "partial"]))
    if me:
        bq = bq.filter(BookitReferralBatch.submitting_advisor == me)
    open_batches = bq.all()
    for b in open_batches[:20]:
        act("evo6", "network", "referral_batch", b.id, "Evo 6 Block" if b.kind == "evo6" else "ADD family network",
            "Family network %s" % b.collection_status.replace("_", " "), None, b.collection_status,
            "Add the family contacts", 5)
    cq = db.query(BookitAddCase).filter(BookitAddCase.organization_id == org, BookitAddCase.status != "resolved")
    if me:
        cq = cq.filter(BookitAddCase.assigned_advisor == me)
    add_open = cq.all()
    for c in add_open:
        act("add_lead", "heart", "add_case", c.id, c.deceased_name, (c.immediate_needs or "Family assistance")[:160],
            c.reported_at.date().isoformat() if c.reported_at else None, c.status, "Reach the family", 0)
    sq = db.query(BookitServiceRequest).filter(BookitServiceRequest.organization_id == org,
                                               BookitServiceRequest.status.notin_(["resolved", "closed"]))
    if me:
        sq = sq.filter(BookitServiceRequest.assigned_to == me)
    for r in sq.all():
        act("service", "wrench", "service_request", r.id, r.family_name or "Family", r.description[:160],
            r.received_at.date().isoformat() if r.received_at else None, r.status, "Help the family",
            0 if r.priority == "urgent" else 3)
    contacts = 0
    if me:
        contacts = db.query(BookitReferralContact).filter(BookitReferralContact.organization_id == org,
                                                          BookitReferralContact.assigned_advisor == me).count()
    else:
        contacts = db.query(BookitReferralContact).filter(BookitReferralContact.organization_id == org).count()
    s, e, _ = window("this_month")
    totals = _sale_totals(sales, s, e)
    actions.sort(key=lambda x: (x["_rank"], x["due"] or "9999"))
    for x in actions:
        x.pop("_rank", None)
    return {
        "date": today_ct().isoformat(), "name": me or ("Team" if w.manager else w.name),
        "hero": {"priority_cases": sum(1 for x in actions if x["kind"] in ("add_lead", "service")),
                 "overdue_follow_ups": overdue, "upcoming_appointments": upcoming,
                 "missing_outcomes": pending_out, "pending_sales": len(pend), "pta_incomplete": pta_open,
                 "evo6_incomplete": len(open_batches)},
        "kpis": {"appointments_today": appts_today, "upcoming_appointments": upcoming, "follow_ups_due": due,
                 "follow_ups_overdue": overdue, "evo6_contacts": contacts,
                 "add_leads_open": len(add_open), "pending_outcomes": pending_out,
                 "closed_value_cents_this_month": totals["closed_value_cents"]},
        "actions": actions[:150],
    }


# ── performance (one advisor, or the team for management) ──────────────────

def performance(db: Session, prog, w: Who, range_name: Optional[str] = None, person: Optional[str] = None,
                start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    org = prog.organization_id
    me = person if (w.manager and person) else (None if w.manager else w.name)
    s, e, label = window(range_name, start, end)
    data = TB.work(db, prog, person=me)
    _mark_sales(db, org, data)
    appts = [a for a in data["appointments"] if _in(a["date"], s, e)]
    showed = [a for a in appts if (a["outcome"] or a["status"]) == "Showed"]
    noshow = [a for a in appts if (a["outcome"] or a["status"]) == "No-Show"]
    sales = _sales(db, org, w, me)
    totals = _sale_totals(sales, s, e)
    closed_appt = {x.appointment_id for x in sales if x.status == "closed" and x.appointment_id}
    with_sale = [a for a in showed if a["id"] in closed_appt]
    fus = data["follow_ups"]
    worked = [f for f in fus if _in(TB._date_text(f["fu_date"]), s, e)]
    booked_fu = [f for f in worked if (f["fu_result"] or "").startswith("Talked - booked")]
    bq = db.query(BookitReferralBatch).filter(BookitReferralBatch.organization_id == org)
    if me:
        bq = bq.filter(BookitReferralBatch.submitting_advisor == me)
    batches = [b for b in bq.all() if b.created_at and s <= b.created_at.date() <= e]
    contacts = (db.query(BookitReferralContact).filter(
        BookitReferralContact.batch_id.in_([b.id for b in batches] or ["-"])).all())
    pq = db.query(BookitPaperwork).filter(BookitPaperwork.organization_id == org,
                                          BookitPaperwork.pta_status == "completed")
    if me:
        pq = pq.filter(BookitPaperwork.pta_advisor == me)
    pta = [p for p in pq.all() if p.pta_completed_on and s <= p.pta_completed_on <= e]
    cq = db.query(BookitAddCase).filter(BookitAddCase.organization_id == org)
    if me:
        cq = cq.filter(BookitAddCase.assigned_advisor == me)
    add = [c for c in cq.all() if c.reported_at and s <= c.reported_at.date() <= e]
    by_source = defaultdict(int)
    by_cat: Dict[str, int] = defaultdict(int)
    from app.models.bookit_models import BookitSaleLine
    closed_ids = [x.id for x in sales if x.status == "closed" and not x.amount_missing and x.sale_date
                  and s <= x.sale_date <= e]
    for x in sales:
        if x.id in closed_ids:
            by_source[x.acquisition_source] += x.net_cents
    for ln in db.query(BookitSaleLine).filter(BookitSaleLine.sale_id.in_(closed_ids or ["-"])):
        by_cat[ln.category] += ln.net_cents
    daily = defaultdict(lambda: {"follow_ups_worked": 0, "appointments": 0})
    for f in worked:
        daily[TB._date_text(f["fu_date"])]["follow_ups_worked"] += 1
    for a in appts:
        daily[a["date"]]["appointments"] += 1
    generated = [a for a in appts]
    return {
        "range": {"start": s.isoformat(), "end": e.isoformat(), "label": label,
                  "cohorts": "appointments by appointment date; sales by sale date; referrals by creation date"},
        "name": me or "Team",
        "metrics": {
            "assigned_appointments": len(appts), "attended": len(showed), "no_shows": len(noshow),
            "show_rate": rate(len(showed), len(showed) + len(noshow)),
            "appointments_with_sales": len(with_sale), "closing_rate": rate(len(with_sale), len(showed)),
            "closed_contracts": totals["closed_contracts"], "closed_value_cents": totals["closed_value_cents"],
            "average_contract_cents": totals["average_contract_cents"],
            "closed_value_per_generated_appointment_cents": (totals["closed_value_cents"] // len(generated))
            if generated else None,
            "pending_opportunities": totals["pending_opportunities"],
            "missing_sale_amounts": totals["missing_amounts"] + _legacy_sold_missing(data),
            "follow_ups_worked": len(worked), "follow_up_conversion": rate(len(booked_fu), len(worked)),
            "pta_completed": len(pta), "evo6_submitted": sum(1 for b in batches if b.kind == "evo6"),
            "contacts_collected": len(contacts),
            "average_contacts_per_review": round(len(contacts) / len(batches), 2) if batches else None,
            "eligible_enrollments": sum(1 for c in contacts if c.enrollment_state == "enrolled"),
            "add_leads_assigned": len(add),
        },
        "closed_value_by_source_cents": dict(by_source),
        "closed_value_by_category_cents": dict(by_cat),
        "daily": [dict(v, date=k) for k, v in sorted(daily.items()) if k],
    }


# ── the Command Center ─────────────────────────────────────────────────────

def command_center(db: Session, prog, w: Who, range_name: Optional[str] = None, person: Optional[str] = None,
                   source: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
    org = prog.organization_id
    s, e, label = window(range_name, start, end)
    data = TB.work(db, prog, person=person)
    _mark_sales(db, org, data)
    sales = [x for x in _sales(db, org, w, person) if not source or x.acquisition_source == source]
    closed = sorted([x for x in sales if x.status == "closed" and not x.amount_missing and x.sale_date
                     and s <= x.sale_date <= e], key=lambda x: x.sale_date)
    evo = [x for x in closed if x.acquisition_source in EVOSYS_SOURCES]
    running, trend = 0, []
    for x in evo:
        running += x.net_cents
        trend.append({"date": x.sale_date.isoformat(), "cumulative_cents": running})
    appts = [a for a in data["appointments"] if _in(a["date"], s, e)]
    showed = [a for a in appts if (a["outcome"] or a["status"]) == "Showed"]
    noshow = [a for a in appts if (a["outcome"] or a["status"]) == "No-Show"]
    closed_appt = {x.appointment_id for x in closed if x.appointment_id}
    with_sale = [a for a in showed if a["id"] in closed_appt]
    fus = data["follow_ups"]
    t = today_ct().isoformat()
    contacts = db.query(BookitReferralContact).filter(BookitReferralContact.organization_id == org).all()
    contact_leads = [c.lead_id for c in contacts if c.lead_id]
    booked_leads = set()
    if contact_leads:
        from app.models.models import BookingLink
        booked_leads = {x for (x,) in db.query(BookingLink.lead_id).filter(
            BookingLink.lead_id.in_(contact_leads), BookingLink.booked_time.isnot(None))}
    ref_closed = [x for x in closed if x.acquisition_source in ("evo6_block", "add_lead")]
    by_source = defaultdict(int)
    for x in closed:
        by_source[x.acquisition_source] += x.net_cents
    team = TB.summary(db, prog, data)["by_person"]
    sales_by_person = defaultdict(lambda: [0, 0])
    for x in closed:
        k = x.closing_advisor or "Not assigned"
        sales_by_person[k][0] += 1
        sales_by_person[k][1] += x.net_cents
    for p in team:
        p["closed_contracts"], p["closed_value_cents"] = sales_by_person.get(p["name"], [0, 0])
        mine = [a for a in appts if a["assigned"] == p["name"]]
        sh = sum(1 for a in mine if (a["outcome"] or a["status"]) == "Showed")
        ns = sum(1 for a in mine if (a["outcome"] or a["status"]) == "No-Show")
        p["show_rate"] = rate(sh, sh + ns)
        p["evo6_blocks"] = db.query(BookitReferralBatch).filter(
            BookitReferralBatch.organization_id == org, BookitReferralBatch.submitting_advisor == p["name"]).count()
        p["add_leads"] = db.query(BookitAddCase).filter(BookitAddCase.organization_id == org,
                                                        BookitAddCase.assigned_advisor == p["name"]).count()
    recent = [{"type": "sale", "id": x.id, "advisor": x.closing_advisor, "net_cents": x.net_cents,
               "date": x.sale_date.isoformat() if x.sale_date else None} for x in closed[-10:][::-1]]
    add_open = db.query(BookitAddCase).filter(BookitAddCase.organization_id == org,
                                              BookitAddCase.status != "resolved").all()
    evo_net = sum(x.net_cents for x in evo)
    generated = len(appts)
    return {
        "range": {"start": s.isoformat(), "end": e.isoformat(), "label": label,
                  "cohorts": "appointments by appointment date; sales by sale date"},
        "hero": {"evosys_closed_value_cents": evo_net, "closed_contracts": len(evo),
                 "average_contract_cents": (evo_net // len(evo)) if evo else None,
                 "closed_value_per_generated_appointment_cents": (evo_net // generated) if generated else None,
                 "trend": trend},
        "kpis": {"appointments_generated": generated, "attended": len(showed),
                 "show_rate": rate(len(showed), len(showed) + len(noshow)), "appointments_with_sales": len(with_sale),
                 "closing_rate": rate(len(with_sale), len(showed)),
                 "open_follow_ups": sum(1 for f in fus if f["open"]),
                 "overdue_follow_ups": sum(1 for f in fus if f["open"] and f["follow_up_on"] and f["follow_up_on"] < t),
                 "evo6_blocks": db.query(BookitReferralBatch).filter(BookitReferralBatch.organization_id == org,
                                                                     BookitReferralBatch.kind == "evo6").count(),
                 "family_contacts": len(contacts), "add_leads": len(add_open),
                 "priority_cases": sum(1 for c in add_open if c.priority in ("high", "urgent")),
                 "unassigned_work": sum(1 for a in data["appointments"] if a["upcoming"] and not a["assigned"])
                 + sum(1 for f in fus if f["open"] and not f["assigned"]),
                 "missing_sale_amounts": sum(1 for x in sales if x.amount_missing) + _legacy_sold_missing(data)},
        "production_by_source_cents": dict(by_source),
        "network_funnel": {"collected": len(contacts),
                           "eligible": sum(1 for c in contacts if c.validation_status == "valid"
                                           and c.outreach_authorized and not c.suppressed),
                           "enrolled": sum(1 for c in contacts if c.enrollment_state == "enrolled"),
                           "reached": sum(1 for c in contacts if (c.enrollment_step or 0) > 0),
                           "booked": len(booked_leads), "closed_sales": len(ref_closed)},
        "team": team,
        "recent": recent,
    }
