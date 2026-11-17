"""THE ACQUISITION COMMAND WORK QUEUE (My Deal Room redesign, Nov 2026).

One bounded, paginated list that replaces the Needs You hero, "Also waiting
on you", "Act first", "Work first" and "Next opportunities" sections. Those
were five views of the same records, so a property could appear three times
on one page. Here a property appears ONCE per view, carrying every open task
on it (a hand-off, an auction deadline), and the views are filters over real,
existing work states - nothing new is stored:

    needs_decision  a hand-off is OPEN: automation stopped and nobody has
                    taken it yet (EvoSenseHandoff.status == "open")
    handoffs        a person has taken the hand-off and it is still in
                    progress (status == "acknowledged")
    auctions        a foreclosure or tax sale on record that has not passed
                    (EvoSenseProperty.sale_deadline >= today, Central Time),
                    soonest first, whatever its investment evidence
    all             the Acquisition Command working set: everything the old
                    command center's lists drew from (work statuses, or a
                    critical / urgent-verify sale) plus every open hand-off

Order everywhere is THE work-priority policy (work_priority.py) unless the
caller asks for a different sort.

SALE DATES. A hand-off opened for an imminent sale stores the sale date in
two places: its AUCTION_DEADLINE reason (`sale_date`) and the free-text note
EvoSense writes ("Foreclosure auction 2026-11-03 - Find contact"). The note is
history. When the trustee sale moves (Texas trustee sales re-post to the next
first Tuesday), the property's current sale event changes, but a hand-off is
re-escalated only when the NEW date is itself critical - so a Dec 1 sale
15 days out left the Nov 3 note open beside it. `sale_context` keeps the two
apart: the CURRENT sale always comes from the property's sale event; dates
written into tasks are returned as labelled history; and a date nobody can
reconcile is surfaced as "Needs verification" with both sources. No record
is rewritten.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_

from app.models.evosense_models import EvoSenseHandoff, EvoSenseProperty
from app.services.evosense import common as C
from app.services.evosense import work_priority as WP

VIEWS = ("needs_decision", "auctions", "handoffs", "all")
VIEW_LABELS = {"needs_decision": "Needs Decision", "auctions": "Upcoming Auctions",
               "handoffs": "Handoffs", "all": "All"}
SORTS = ("work", "sale_date", "evidence", "newest")
MAX_LIMIT = 100
DEFAULT_LIMIT = 25

# the statuses the old command-center lists drew from (views.command_center)
_WORK_STATUSES = None


def _work_statuses():
    global _WORK_STATUSES
    if _WORK_STATUSES is None:
        _WORK_STATUSES = (C.S_NEEDS_YOU, C.S_READY, C.S_HIGH, C.S_CONTACT_FOUND, C.S_OUTREACH,
                          C.S_RESPONDED, C.S_WAITING_DATA, C.S_BUDGET_BLOCKED)
    return _WORK_STATUSES


_ISO = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
_VERIFY_URGENCY = (WP.A_URGENT_VERIFY, WP.A_VERIFY, WP.A_OUTCOME)


def _day(v) -> Optional[date]:
    return WP._day(v)


def _ct_day(dt) -> Optional[date]:
    """A stored (naive UTC) timestamp as a Central Time calendar day."""
    if dt is None:
        return None
    from datetime import timezone
    from zoneinfo import ZoneInfo
    return dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(WP.TZ)).date()


def _fmt(d: Optional[date]) -> Optional[str]:
    return ("%s %d, %d" % (d.strftime("%b"), d.day, d.year)) if d else None


def sale_context(work: Optional[Dict[str, Any]], handoff: Optional[EvoSenseHandoff] = None,
                 *, today: Optional[date] = None) -> Dict[str, Any]:
    """The CURRENT sale (from the property's sale event, via the work-priority
    summary) and any OTHER sale date written into a task, labelled.

    status: "none"                no sale on record and no task cites one
            "current"             one sale date, verified on the official record
            "historical"          a task cites an EARLIER sale date that has
                                  passed; the current sale is later (the sale
                                  moved). The old date is history, not a conflict.
            "needs_verification"  the current sale is not verified on the
                                  official record, or a task cites a date that
                                  cannot be reconciled with it (both are shown).
    """
    today = today or WP.today_ct()
    w = work or {}
    cur = _day(w.get("auction_date"))
    current = None
    if cur or w.get("urgency") not in (None, WP.A_NONE):
        current = {"date": cur.isoformat() if cur else None, "display": _fmt(cur),
                   "kind": w.get("auction_kind"), "verified": bool(w.get("auction_verified")),
                   "urgency": w.get("urgency"), "label": w.get("urgency_label"),
                   "days": (cur - today).days if cur else None,
                   "source": "Current sale event on the property record",
                   "why": w.get("why")}
    noted: List[Dict[str, Any]] = []
    if handoff is not None:
        seen = set()
        for r in C.jload(handoff.reasons, []) or []:
            d = _day(r.get("sale_date")) if r.get("code") == "AUCTION_DEADLINE" else None
            if d and d not in seen:
                seen.add(d)
                noted.append({"date": d.isoformat(), "display": _fmt(d),
                              "source": "Hand-off opened %s for this sale" % (
                                  _fmt(_ct_day(handoff.created_at)) if handoff.created_at else "earlier")})
        for m in _ISO.findall(handoff.next_action or ""):
            d = _day(m)
            if d and d not in seen:
                seen.add(d)
                noted.append({"date": d.isoformat(), "display": _fmt(d),
                              "source": "Date written in the hand-off note"})
    others = [n for n in noted if n["date"] != (cur.isoformat() if cur else None)]
    status, message = "none", None
    if current is None and not others:
        status = "none"
    elif current is None:
        status = "needs_verification"
        message = ("A task cites a sale on %s, but no current sale is on the property record. "
                   "Verify the sale on the official record." % others[0]["display"])
    else:
        status = "current"
        unverified = current["urgency"] in _VERIFY_URGENCY or not current["verified"]
        future_other = [n for n in others if _day(n["date"]) >= today]
        past_other = [n for n in others if _day(n["date"]) < today]
        if future_other:
            status = "needs_verification"
            message = ("Two sale dates disagree. The current sale event says %s; the %s says %s. "
                       "Verify which is on the official record." % (
                           current["display"] or "no date",
                           "hand-off note" if "note" in future_other[0]["source"] else "hand-off",
                           future_other[0]["display"]))
        elif unverified and current["urgency"] not in (WP.A_NONE, None):
            status = "needs_verification"
            message = current.get("why") or "The sale is not verified on the official record."
        elif past_other:
            status = "historical"
            message = ("The hand-off was opened for the %s sale, which has passed. The current sale is %s." % (
                past_other[0]["display"], current["display"] or "not dated"))
        for n in past_other:
            n["historical"] = True
    return {"status": status, "current": current, "history": others, "message": message}


def _base(db, org_id: str, include_test: bool):
    q = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                          EvoSenseProperty.archived_at.is_(None))
    if not include_test:
        q = q.filter(EvoSenseProperty.is_test.isnot(True))
    return q


def _open_handoffs(db, org_id: str, include_test: bool, statuses):
    q = db.query(EvoSenseHandoff).filter(EvoSenseHandoff.organization_id == org_id,
                                         EvoSenseHandoff.status.in_(statuses))
    if not include_test:
        q = q.filter(EvoSenseHandoff.is_test.isnot(True))
    return q


def _membership(db, org_id: str, view: str, include_test: bool, today: date):
    """The property query for one view (filters only; no order)."""
    base = _base(db, org_id, include_test)
    if view == "needs_decision":
        sub = _open_handoffs(db, org_id, include_test, ("open",)).with_entities(EvoSenseHandoff.property_id)
        return base.filter(EvoSenseProperty.id.in_(sub))
    if view == "handoffs":
        sub = _open_handoffs(db, org_id, include_test, ("acknowledged",)).with_entities(EvoSenseHandoff.property_id)
        return base.filter(EvoSenseProperty.id.in_(sub))
    if view == "auctions":
        return base.filter(EvoSenseProperty.sale_deadline.isnot(None),
                           EvoSenseProperty.sale_deadline >= today,
                           or_(EvoSenseProperty.work_priority.is_(None),
                               EvoSenseProperty.work_priority != WP.W_CLOSED))
    sub = _open_handoffs(db, org_id, include_test, ("open", "acknowledged")).with_entities(EvoSenseHandoff.property_id)
    return base.filter(or_(EvoSenseProperty.status.in_(_work_statuses()),
                           EvoSenseProperty.work_priority.in_((WP.W_CRITICAL, WP.W_REVIEW_URGENT)),
                           EvoSenseProperty.id.in_(sub)))


def _order(q, view: str, sort: str):
    P = EvoSenseProperty
    if sort == "sale_date" or (sort == "work" and view == "auctions"):
        return q.order_by(P.sale_deadline.asc().nullslast(), P.work_priority_rank.asc().nullslast(),
                          P.opportunity_score.desc().nullslast(), P.id)
    if sort == "evidence":
        return q.order_by(P.opportunity_score.desc().nullslast(), P.work_priority_rank.asc().nullslast(), P.id)
    if sort == "newest":
        return q.order_by(P.discovered_at.desc().nullslast(), P.id)
    return q.order_by(P.work_priority_rank.asc().nullslast(), P.sale_deadline.asc().nullslast(),
                      P.seller_intent.desc().nullslast(), P.opportunity_score.desc().nullslast(), P.id)


def _search(q, text: Optional[str]):
    t = (text or "").strip()
    if not t:
        return q
    like = "%" + t.lower()[:80].replace("%", "").replace("_", "") + "%"
    P = EvoSenseProperty
    return q.filter(or_(func.lower(P.street_address).like(like), func.lower(P.city).like(like),
                        func.lower(P.zip_code).like(like), func.lower(P.county).like(like)))


def counts(db, org_id: str, *, include_test: bool, q: Optional[str] = None,
           today: Optional[date] = None) -> Dict[str, int]:
    today = today or WP.today_ct()
    return {v: int(_search(_membership(db, org_id, v, include_test, today), q)
                   .with_entities(func.count(EvoSenseProperty.id)).scalar() or 0) for v in VIEWS}


def _task_rows(handoffs: List[EvoSenseHandoff]) -> List[Dict[str, Any]]:
    return [{"kind": "handoff", "id": h.id, "status": h.status, "priority": h.priority,
             "reasons": C.jload(h.reasons, []) or [], "note": h.next_action,
             "since": h.created_at.isoformat() if h.created_at else None} for h in handoffs]


def item(p: EvoSenseProperty, handoffs: List[EvoSenseHandoff], *, today: date) -> Dict[str, Any]:
    from app.services.evosense import views as V
    from app.services.evosense import valuation as VAL
    work = V._work(p)
    primary = handoffs[0] if handoffs else None
    sale = sale_context(work, primary, today=today)
    vals = VAL.view(p)
    tasks = _task_rows(handoffs)
    if sale["current"] and sale["current"].get("date") and (sale["current"]["days"] or 0) >= 0:
        tasks.append({"kind": "auction", "date": sale["current"]["date"], "label": sale["current"]["label"],
                      "urgency": sale["current"]["urgency"], "verified": sale["current"]["verified"]})
    return {
        "id": p.id, "address": p.street_address, "unit": p.unit, "city": p.city, "state": p.state,
        "zip_code": p.zip_code, "county": p.county, "property_type": p.property_type,
        "is_test": bool(p.is_test), "status": p.status, "promoted_deal_id": p.promoted_deal_id,
        # four separate answers, never folded together (work_priority.py)
        "work": work,
        "investment_evidence": p.opportunity_score,
        "contact_confidence": p.contact_confidence,
        "seller_intent": p.seller_intent,
        # three different numbers, each labelled for what it is
        "values": {"tax_value": V._appraisal_json(vals["appraisal"]),
                   "market_estimate": vals["market_value"],
                   "arv": {"value": None, "label": VAL.ARV_INSUFFICIENT}},
        "sale": sale,
        "tasks": tasks,
        # the action for TODAY comes from the current work state; the hand-off
        # note is shown as history beside it, never as the current instruction
        "next_action": (work or {}).get("next_action") or p.next_action,
        "handoff_note": primary.next_action if primary else None,
    }


def queue(db, org_id: str, *, view: str = "needs_decision", sort: str = "work", q: Optional[str] = None,
          offset: int = 0, limit: int = DEFAULT_LIMIT, include_test: bool = False,
          today: Optional[date] = None) -> Dict[str, Any]:
    today = today or WP.today_ct()
    view = view if view in VIEWS else "needs_decision"
    sort = sort if sort in SORTS else "work"
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    offset = max(0, int(offset or 0))
    base = _search(_membership(db, org_id, view, include_test, today), q)
    total = int(base.with_entities(func.count(EvoSenseProperty.id)).scalar() or 0)
    rows = _order(base, view, sort).offset(offset).limit(limit).all()
    ids = [p.id for p in rows] or ["-"]
    hq = _open_handoffs(db, org_id, include_test, ("open", "acknowledged")).filter(EvoSenseHandoff.property_id.in_(ids))
    by_prop: Dict[str, List[EvoSenseHandoff]] = {}
    for h in hq.order_by(EvoSenseHandoff.priority.desc(), EvoSenseHandoff.created_at.asc()).all():
        by_prop.setdefault(h.property_id, []).append(h)
    return {
        "view": view, "sort": sort, "q": q or None, "offset": offset, "limit": limit, "total": total,
        "views": [{"key": v, "label": VIEW_LABELS[v]} for v in VIEWS],
        "counts": counts(db, org_id, include_test=include_test, q=q, today=today),
        "include_test": include_test, "today": today.isoformat(),
        "items": [item(p, by_prop.get(p.id, []), today=today) for p in rows],
    }
