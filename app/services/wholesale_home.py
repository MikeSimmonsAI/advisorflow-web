# -*- coding: utf-8 -*-
"""Wholesale home: one dashboard, composed from what this person may do (#439).

There are no per-role screens here. Every panel names the permission it needs
(module + action, from app.services.wholesale_access); a panel is built only
when the person's EFFECTIVE access allows it, and every number and row inside
it is narrowed to the records their visibility allows (assigned / team /
workspace). A starter profile only decides the default ORDER of the panels it
gets - Francis on "Acquisition Wholesaler" who is also granted Buyer
Intelligence simply gets the buyer panel too, with no code change.

Rules kept from the rest of Wholesale:
  - sandbox / test rows are left out unless `include_test` (as the other
    dashboards do);
  - nothing here calls a provider, queues a job or spends money - it reads
    what is already stored;
  - when the product has no data for something a mockup shows (a territory
    map, staff messaging, an appointment "confirmed" state), the panel says so
    instead of inventing it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set

from sqlalchemy import func

from app.models.models import User
from app.services import wholesale_access as A

# ── layout: which panels a starter profile shows first ─────────────────────
# ORDER ONLY. A panel the person may not use is never built, whatever this says.
FOCUS = {
    "acquisition": {"label": "Acquisition", "sub": "Find motivated sellers, work your leads and move deals forward.",
                    "order": ["tasks", "pipeline", "areas", "opportunities", "appointments", "callbacks", "discovery",
                              "buyers", "deal_room", "routes", "research", "reviews", "team", "activity", "providers"]},
    "manager": {"label": "Team oversight", "sub": "See your team's pipeline, appointments and what needs a decision.",
                "order": ["team", "pipeline", "reviews", "areas", "activity", "appointments", "buyers", "deal_room",
                          "opportunities", "tasks", "callbacks", "discovery", "routes", "research", "providers"]},
    "field": {"label": "Field operations", "sub": "Run your route, log visits and capture what you see at each property.",
              "order": ["routes", "appointments", "visits", "callbacks", "tasks", "areas", "opportunities", "discovery",
                        "pipeline", "buyers", "deal_room", "research", "reviews", "team", "activity", "providers"]},
    "dispo": {"label": "Dispositions", "sub": "Match deals to buyers, work the Deal Room and follow up on offers.",
              "order": ["deal_room", "buyers", "outreach", "opportunities", "pipeline", "reviews", "tasks", "callbacks",
                        "appointments", "discovery", "routes", "research", "team", "activity", "areas", "providers"]},
    "research": {"label": "Data & verification", "sub": "Verify records, resolve conflicting evidence and keep property data clean.",
                 "order": ["research", "providers", "discovery", "areas", "tasks", "opportunities", "pipeline",
                           "callbacks", "appointments", "buyers", "deal_room", "routes", "reviews", "team", "activity"]},
    "va": {"label": "Support", "sub": "Work your task queue, return calls and keep records tidy.",
           "order": ["tasks", "callbacks", "appointments", "replies", "research", "opportunities", "pipeline",
                     "discovery", "routes", "buyers", "deal_room", "reviews", "team", "activity", "areas", "providers"]},
    "investor": {"label": "Deal access", "sub": "The deals that have been shared with you.",
                 "order": ["opportunities", "deal_room", "pipeline", "tasks", "appointments", "buyers", "research",
                           "callbacks", "discovery", "routes", "reviews", "team", "activity", "areas", "providers"]},
}
TEMPLATE_FOCUS = {"acquisition_wholesaler": "acquisition", "wholesale_manager": "manager",
                  "field_representative": "field", "buyer_disposition": "dispo", "research_assistant": "research",
                  "virtual_assistant": "va", "investor_restricted": "investor"}


def guess_focus(perms: Dict[str, List[str]]) -> str:
    """For a custom profile with no starter: lead with what they can do most of.
    Still order only - panels follow permissions."""
    has = lambda m, a="view": a in (perms.get(m) or [])                                   # noqa: E731
    if has("team_admin") or has("team_board", "assign"):
        return "manager"
    if has("deal_room") or has("buyer_intel") or has("investor_outreach"):
        if not has("acquisition") and not has("canvassing"):
            return "dispo"
    if has("canvassing") and not has("deals", "edit"):
        return "field"
    if has("acquisition") or has("deals", "edit"):
        return "acquisition"
    if has("properties", "edit") and not has("deals"):
        return "research"
    if has("sellers") or has("appointments"):
        return "va"
    if has("deals"):
        return "investor"
    return "acquisition"


# ── quick actions: each opens an existing screen, shown only when allowed ──
ACTIONS = [
    # key, label, route, module, action
    ("discovery", "Discovery Inbox", "/wholesale/evosense/inbox", "acquisition", "view"),
    ("open_property", "Open Property", "/wholesale/properties", "properties", "view"),
    ("callbacks", "Work Callbacks", "/wholesale/callbacks", "sellers", "view"),
    ("book", "Book Appointment", "/wholesale/appointments?book=1", "appointments", "create"),
    ("appointments", "My Appointments", "/wholesale/appointments", "appointments", "view"),
    ("route", "Start Route", "/wholesale/canvass", "canvassing", "view"),
    ("underwrite", "Run Underwriting", "/wholesale/calculator", "underwriting", "view"),
    ("assign", "Assign Work", "/wholesale/board", "team_board", "assign"),
    ("board", "Open Team Board", "/wholesale/board", "team_board", "view"),
    ("deals", "Review Deals", "/wholesale", "deals", "view"),
    ("buyers", "Buyer Intelligence", "/wholesale/buyers", "buyer_intel", "view"),
    ("buy_boxes", "Buy Boxes", "/wholesale/buyers/buy-boxes", "buy_boxes", "view"),
    ("deal_room", "Open Deal Room", "/wholesale/deal-room", "deal_room", "view"),
    ("outreach", "Investor Outreach", "/wholesale/deal-room/investors", "investor_outreach", "view"),
    ("dispositions", "Dispositions", "/wholesale/dispositions", "dispositions", "view"),
    ("closing", "Contracts & Closing", "/wholesale/closing", "contracts", "view"),
    ("foreclosures", "Foreclosures", "/wholesale/foreclosures", "foreclosures", "view"),
    ("exceptions", "Exceptions", "/wholesale/exceptions", "team_board", "view"),
    ("providers", "Providers & Controls", "/wholesale/evosense/controls", "paid_lookups", "view"),
]
ACTION_ORDER = {
    "acquisition": ["callbacks", "open_property", "discovery", "book", "underwrite", "deals"],
    "manager": ["assign", "deals", "board", "buyers", "appointments", "exceptions"],
    "field": ["route", "appointments", "open_property", "callbacks", "book"],
    "dispo": ["deal_room", "buyers", "outreach", "dispositions", "buy_boxes"],
    "research": ["open_property", "discovery", "foreclosures", "underwrite", "providers"],
    "va": ["callbacks", "appointments", "open_property", "exceptions", "book"],
    "investor": ["deals", "deal_room", "closing"],
}


def _now() -> datetime:
    return datetime.utcnow()


def _iso(d) -> Optional[str]:
    return d.isoformat() + "Z" if d else None


class _Ctx:
    """Everything a panel needs: who, where, what they may do, whose records."""

    def __init__(self, db, org_id, user, eff, visible, include_test, preview=False):
        self.db, self.org_id, self.user, self.eff = db, org_id, user, eff
        self.visible: Optional[Set[str]] = visible
        self.include_test, self.preview = include_test, preview
        self.assigned_only = (eff.get("record_scope") == A.ASSIGNED and eff.get("mode") == "custom")

    def may(self, module: str, action: str = "view") -> bool:
        return A.allows(self.eff, module, action)

    def owned(self, q, column):
        """Narrow by assignee the way the rest of Wholesale does."""
        if self.visible is None:
            return q
        return q.filter(column.in_(list(self.visible)))

    def live(self, q, model):
        if self.include_test or not hasattr(model, "is_test"):
            return q
        return q.filter(model.is_test.isnot(True))


# ── panels ──────────────────────────────────────────────────────────────────
def _addr(p) -> str:
    if p is None:
        return ""
    return ", ".join(x for x in (p.street_address, p.city) if x)


def _people(db, ids) -> Dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(ids)).all()}


def panel_tasks(c: _Ctx) -> Optional[Dict[str, Any]]:
    """My work: assigned exceptions, follow-ups and callbacks that are due."""
    from app.models.evosense_models import EvoSenseFollowUp
    from app.models.wholesale_models import EXCEPTION_OPEN_STATUSES, WholesaleWorkException
    from app.models.wholesale_ops_models import WholesaleSellerCallback
    if not (c.may("team_board") or c.may("sellers")):
        return None
    db, end = c.db, datetime.combine(_now().date(), datetime.max.time())
    items: List[Dict[str, Any]] = []
    if c.may("team_board"):
        q = c.live(db.query(WholesaleWorkException).filter(  # noqa: E501WholesaleWorkException.organization_id == c.org_id,
                                                           WholesaleWorkException.assigned_to_id == c.user.id,
                                                           WholesaleWorkException.status.in_(EXCEPTION_OPEN_STATUSES)),
                   WholesaleWorkException)
        for e in q.order_by(WholesaleWorkException.priority.asc(), WholesaleWorkException.due_at.asc().nullslast()).limit(8).all():
            pr = e.priority if e.priority is not None else 50
            items.append({"kind": "exception", "title": e.title,
                          "priority": "urgent" if pr <= 10 else "high" if pr <= 30 else "normal" if pr <= 60 else "low",
                          "due_at": _iso(e.due_at), "to": "/wholesale/exceptions"})
    if c.may("sellers"):
        fq = c.owned(c.live(db.query(EvoSenseFollowUp).filter(EvoSenseFollowUp.organization_id == c.org_id,
                                                              EvoSenseFollowUp.status == "due",
                                                              EvoSenseFollowUp.due_at <= end), EvoSenseFollowUp),
                     EvoSenseFollowUp.assigned_to_id)
        for f in fq.order_by(EvoSenseFollowUp.due_at.asc()).limit(8).all():
            items.append({"kind": "follow_up", "title": "Follow up: " + (f.note or "seller follow-up")[:80],
                          "priority": "high" if f.due_at and f.due_at < _now() else "normal", "due_at": _iso(f.due_at),
                          "to": "/wholesale/evosense/property/%s" % f.property_id})
        cq = c.owned(c.live(db.query(WholesaleSellerCallback).filter(
            WholesaleSellerCallback.organization_id == c.org_id, WholesaleSellerCallback.status == "due",
            WholesaleSellerCallback.due_at <= end), WholesaleSellerCallback), WholesaleSellerCallback.assigned_to_id)
        for cb in cq.order_by(WholesaleSellerCallback.due_at.asc()).limit(8).all():
            items.append({"kind": "callback", "title": "Call back: " + (cb.notes or "seller")[:80],
                          "priority": "high" if cb.due_at and cb.due_at < _now() else "normal",
                          "due_at": _iso(cb.due_at), "to": "/wholesale/callbacks"})
    rank = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    items.sort(key=lambda i: (rank.get(i["priority"], 2), i["due_at"] or "9999"))
    return {"title": "My Work Queue", "items": items[:8], "count": len(items),
            "empty": "Nothing assigned to you is due today."}


def panel_pipeline(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.models.wholesale_models import WholesaleDeal
    from app.services import wholesale_pipeline as PL
    from app.services.wholesale_service import resolve_settings
    if not c.may("deals"):
        return None
    db = c.db
    q = c.owned(c.live(db.query(WholesaleDeal.stage, func.count(WholesaleDeal.id))
                       .filter(WholesaleDeal.organization_id == c.org_id), WholesaleDeal), WholesaleDeal.assigned_to_id)
    counts = {s: int(n) for s, n in q.group_by(WholesaleDeal.stage).all()}
    settings = resolve_settings(db, c.org_id, commit=False)
    rows = [{"key": s["key"], "label": s["label"], "count": counts.get(s["key"], 0)}
            for s in PL.resolve_stages(settings) if not s["terminal"]]
    known = {r["key"] for r in rows}
    rows += [{"key": k, "label": PL.stage_label(settings, k), "count": n} for k, n in counts.items()
             if k not in known and not PL.is_terminal(settings, k)]
    rows = [r for r in rows if r["count"]] or rows[:5]
    return {"title": "Deal Pipeline", "rows": rows, "total": sum(r["count"] for r in rows), "to": "/wholesale",
            "empty": "No deals in your view yet."}


def panel_opportunities(c: _Ctx) -> Optional[Dict[str, Any]]:
    """The deals they may see, most recently touched first."""
    from app.models.evosense_models import EvoSenseProperty
    from app.models.wholesale_models import WholesaleDeal, WholesaleEvent, WholesaleProperty
    from app.services import wholesale_pipeline as PL
    from app.services.wholesale_service import resolve_settings
    if not c.may("deals"):
        return None
    db = c.db
    q = c.owned(c.live(db.query(WholesaleDeal).filter(WholesaleDeal.organization_id == c.org_id,
                                                      WholesaleDeal.stage.notin_(("closed", "dead"))), WholesaleDeal),
                WholesaleDeal.assigned_to_id)
    total = q.count()
    deals = q.order_by(WholesaleDeal.updated_at.desc()).limit(8).all()
    ids = [d.id for d in deals]
    props = {p.id: p for p in db.query(WholesaleProperty).filter(
        WholesaleProperty.id.in_([d.property_id for d in deals] or [""])).all()}
    evo = {p.promoted_deal_id: p for p in db.query(EvoSenseProperty).filter(
        EvoSenseProperty.organization_id == c.org_id, EvoSenseProperty.promoted_deal_id.in_(ids or [""])).all()}
    last = {}
    for ev in (db.query(WholesaleEvent).filter(WholesaleEvent.organization_id == c.org_id,
                                               WholesaleEvent.deal_id.in_(ids or [""]))
               .order_by(WholesaleEvent.created_at.desc()).limit(80).all()):
        last.setdefault(ev.deal_id, ev)
    names = _people(db, [d.assigned_to_id for d in deals])
    settings = resolve_settings(db, c.org_id, commit=False)
    show_owner = c.may("sellers") or c.may("properties")          # owner of record is seller-side data
    rows = []
    for d in deals:
        p, e, ev = props.get(d.property_id), evo.get(d.id), last.get(d.id)
        rows.append({"deal_id": d.id, "address": _addr(p), "zip": getattr(p, "zip_code", None),
                     "owner": (p.owner_name if p is not None and show_owner else None),
                     "stage": d.stage, "stage_label": PL.stage_label(settings, d.stage),
                     "score": (round(e.urgency_score) if e is not None and e.urgency_score is not None else None),
                     "next_step": (e.next_best_action if e is not None else None),
                     "assigned_to": names.get(d.assigned_to_id),
                     "last_activity": (ev.summary if ev is not None else None),
                     "last_activity_at": _iso(ev.created_at if ev is not None else d.updated_at),
                     "to": "/wholesale/deals/%s" % d.id})
    return {"title": "Your Opportunities" if c.assigned_only else "Active Opportunities",
            "rows": rows, "count": total, "to": "/wholesale",
            "empty": "No open deals in your view. Deals assigned to you will appear here."}


def panel_appointments(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.models.wholesale_ops_models import WholesaleFieldAppointment as Appt
    if not c.may("appointments"):
        return None
    db, now = c.db, _now()
    base = c.owned(c.live(db.query(Appt).filter(Appt.organization_id == c.org_id), Appt), Appt.assigned_to_id)
    upcoming = base.filter(Appt.status == "scheduled", Appt.starts_at >= now)
    week = upcoming.filter(Appt.starts_at < now + timedelta(days=7)).count()
    soon = upcoming.filter(Appt.starts_at < now + timedelta(days=2)).count()
    rows = upcoming.order_by(Appt.starts_at.asc()).limit(6).all()
    names = _people(db, [a.assigned_to_id for a in rows])
    return {"title": "Upcoming Appointments", "week": week, "next_48h": soon,
            "rows": [{"id": a.id, "title": a.title or "Appointment", "address": a.address, "starts_at": _iso(a.starts_at),
                      "who": names.get(a.assigned_to_id), "to": "/wholesale/appointments"} for a in rows],
            "note": "Appointments have no separate 'confirmed' step yet; the next 48 hours are the ones to call and confirm.",
            "to": "/wholesale/appointments", "empty": "No upcoming appointments in your view."}


def panel_callbacks(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.models.wholesale_ops_models import WholesaleSellerCallback as Cb
    if not c.may("sellers"):
        return None
    db, end = c.db, datetime.combine(_now().date(), datetime.max.time())
    q = c.owned(c.live(db.query(Cb).filter(Cb.organization_id == c.org_id, Cb.status == "due"), Cb),
                Cb.assigned_to_id)
    due = q.filter(Cb.due_at <= end)
    rows = due.order_by(Cb.due_at.asc()).limit(6).all()
    return {"title": "Calls to Return", "due_today": due.count(), "scheduled": q.count(),
            "overdue": q.filter(Cb.due_at < _now()).count(),
            "rows": [{"id": r.id, "note": (r.notes or "")[:90], "due_at": _iso(r.due_at), "to": "/wholesale/callbacks"}
                     for r in rows],
            "to": "/wholesale/callbacks", "empty": "No calls due today."}


def panel_replies(c: _Ctx) -> Optional[Dict[str, Any]]:
    """Seller replies waiting on the Wholesale side (the only inbox that exists;
    there is no staff-to-staff messaging in the product)."""
    from app.models.evosense_models import EvoSenseMessage, EvoSenseProperty
    if not c.may("sellers") or c.visible is not None:
        # Seller replies hang off discovery records, which have no assignee:
        # only someone who sees the whole workspace sees them all.
        return None
    db = c.db
    q = (db.query(EvoSenseMessage).filter(EvoSenseMessage.organization_id == c.org_id,
                                          EvoSenseMessage.direction == "inbound",
                                          EvoSenseMessage.created_at >= _now() - timedelta(days=7)))
    rows = q.order_by(EvoSenseMessage.created_at.desc()).limit(5).all()
    props = {p.id: p for p in db.query(EvoSenseProperty).filter(
        EvoSenseProperty.id.in_([m.property_id for m in rows] or [""])).all()}
    return {"title": "Seller Replies (7 days)", "count": q.count(),
            "rows": [{"property": _addr(props.get(m.property_id)), "text": (m.body or "")[:110], "at": _iso(m.created_at),
                      "to": "/wholesale/evosense/property/%s" % m.property_id} for m in rows],
            "note": "Staff-to-staff messaging isn't part of Wholesale yet; this shows seller replies.",
            "empty": "No seller replies in the last 7 days."}


def panel_discovery(c: _Ctx) -> Optional[Dict[str, Any]]:
    """Discovery Inbox headline, the same statuses Acquisition Command reads."""
    from app.models.evosense_models import EvoSenseProperty as P
    from app.services.evosense import common as C
    if not c.may("acquisition"):
        return None
    db = c.db
    base = c.live(db.query(P).filter(P.organization_id == c.org_id, P.archived_at.is_(None)), P)
    by = dict(base.with_entities(P.status, func.count(P.id)).group_by(P.status).all())
    top = (base.filter(P.status.in_((C.S_NEEDS_YOU, C.S_HIGH, C.S_READY, C.S_CONTACT_FOUND, C.S_RESPONDED)))
           .order_by(P.work_priority_rank.asc().nullslast(), P.urgency_score.desc().nullslast()).limit(5).all())
    return {"title": "Discovery: Motivation Signals",
            "counts": {"needs_you": by.get(C.S_NEEDS_YOU, 0), "high": by.get(C.S_HIGH, 0),
                       "ready": by.get(C.S_READY, 0), "responded": by.get(C.S_RESPONDED, 0),
                       "new_7d": base.filter(P.discovered_at >= _now() - timedelta(days=7)).count()},
            "rows": [{"id": p.id, "address": _addr(p), "score": round(p.urgency_score) if p.urgency_score is not None else None,
                      "signals": p.signal_count or 0, "next": p.next_best_action or p.next_action,
                      "to": "/wholesale/evosense/property/%s" % p.id} for p in top],
            "to": "/wholesale/evosense/inbox", "empty": "Nothing in the Discovery Inbox needs a person right now."}


def panel_routes(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.models.wholesale_ops_models import WholesaleCanvassDoor as Door, WholesaleCanvassRoute as Route
    if not c.may("canvassing"):
        return None
    from app.services.wholesale_appointments import day_window
    db = c.db
    start, end, _day = day_window()                       # today in Central, as UTC (how routes are stored)
    q = c.owned(c.live(db.query(Route).filter(Route.organization_id == c.org_id, Route.status != "done"), Route),
                Route.assigned_to_id)
    routes = q.order_by(Route.route_date.asc().nullslast(), Route.created_at.desc()).limit(4).all()
    todays = [r for r in routes if r.route_date is not None and start <= r.route_date < end]
    first = todays[0] if todays else (routes[0] if routes else None)
    doors = (db.query(Door).filter(Door.route_id == first.id).order_by(Door.sort.asc()).limit(8).all()) if first else []
    left = (db.query(func.count(Door.id)).filter(Door.route_id.in_([r.id for r in routes] or [""]),
                                                 Door.knocked_at.is_(None)).scalar() or 0)
    return {"title": "Today's Route" if todays else "My Routes",
            "routes": [{"id": r.id, "name": r.name, "area": r.area, "date": _iso(r.route_date),
                        "status": r.status} for r in routes],
            "stops": [{"n": i + 1, "address": d.address, "result": d.result, "knocked": d.knocked_at is not None}
                      for i, d in enumerate(doors)],
            "doors_left": int(left), "to": "/wholesale/canvass", "empty": "No canvassing route is assigned to you."}


def panel_visits(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.models.wholesale_ops_models import WholesaleFieldAppointment as Appt, WholesaleFieldVisit as Visit
    if not (c.may("appointments") and c.may("canvassing")):   # field visits are fieldwork
        return None
    db = c.db
    q = c.owned(c.live(db.query(Visit, Appt).join(Appt, Appt.id == Visit.appointment_id)
                       .filter(Visit.organization_id == c.org_id), Appt), Appt.assigned_to_id)
    rows = q.order_by(Visit.updated_at.desc()).limit(6).all()
    return {"title": "Recent Field Visits", "count": q.count(),
            "rows": [{"address": a.address, "outcome": a.outcome or a.status, "at": _iso(v.updated_at),
                      "to": "/wholesale/appointments"} for v, a in rows],
            "note": "Condition notes and photos are captured on each visit (Appointments → the visit).",
            "empty": "No field visits logged yet."}


def panel_team(c: _Ctx) -> Optional[Dict[str, Any]]:
    """Per person: open deals, appointments this week, properties - only the
    people this viewer can see."""
    from app.models.wholesale_models import WholesaleDeal, WholesaleProperty
    from app.models.wholesale_ops_models import WholesaleFieldAppointment as Appt
    from app.services import wholesale_team as T
    if not c.may("team_board") or c.assigned_only:
        return None
    db, now = c.db, _now()
    people = T.team(db, c.org_id)["members"]
    ids = [m["user_id"] for m in people if c.visible is None or m["user_id"] in c.visible]

    def per(model, *flt):
        q = c.live(db.query(model.assigned_to_id, func.count(model.id)).filter(model.organization_id == c.org_id,
                                                                                model.assigned_to_id.in_(ids or [""]),
                                                                                *flt), model)
        return dict(q.group_by(model.assigned_to_id).all())
    deals = per(WholesaleDeal, WholesaleDeal.stage.notin_(("closed", "dead")))
    appts = per(Appt, Appt.starts_at >= now - timedelta(days=now.weekday()), Appt.status != "cancelled")
    props = per(WholesaleProperty)
    names = {m["user_id"]: m for m in people}
    rows = [{"user_id": i, "name": names[i]["name"], "role": names[i].get("role_label"),
             "deals": deals.get(i, 0), "appointments": appts.get(i, 0), "properties": props.get(i, 0)} for i in ids]
    rows.sort(key=lambda r: (-r["deals"], -r["appointments"], r["name"] or ""))
    return {"title": "Team Performance", "rows": rows[:10], "count": len(rows), "to": "/wholesale/board",
            "empty": "Nobody is on the Wholesale team yet. Invite people from Wholesale → Team."}


def panel_reviews(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.models.wholesale_models import WholesaleApproval, WholesaleDeal, WholesaleProperty
    # A decision queue is for people who can decide, or who work the deals.
    if not c.may("team_board") or not (c.may("team_board", "approve") or c.may("deals")):
        return None
    db = c.db
    q = db.query(WholesaleApproval).filter(WholesaleApproval.organization_id == c.org_id,
                                           WholesaleApproval.status == "pending")
    if c.visible is not None:
        seen = c.owned(db.query(WholesaleDeal.id).filter(WholesaleDeal.organization_id == c.org_id),
                       WholesaleDeal.assigned_to_id)
        q = q.filter(WholesaleApproval.deal_id.in_(seen.subquery()))
    rows = q.order_by(WholesaleApproval.created_at.asc()).limit(5).all()
    deals = {d.id: d for d in db.query(WholesaleDeal).filter(WholesaleDeal.id.in_([r.deal_id for r in rows] or [""]))}
    props = {p.id: p for p in db.query(WholesaleProperty).filter(
        WholesaleProperty.id.in_([d.property_id for d in deals.values()] or [""]))}
    return {"title": "Needs a Decision", "count": q.count(), "can_decide": c.may("team_board", "approve"),
            "rows": [{"id": r.id, "kind": r.kind, "amount": float(r.amount) if r.amount is not None else None,
                      "address": _addr(props.get(getattr(deals.get(r.deal_id), "property_id", None))),
                      "to": "/wholesale/deals/%s" % r.deal_id} for r in rows],
            "empty": "No approvals are waiting."}


def panel_activity(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.models.wholesale_models import WholesaleDeal, WholesaleEvent, WholesaleProperty
    if not c.may("deals") or c.assigned_only:
        return None
    db = c.db
    q = db.query(WholesaleEvent).filter(WholesaleEvent.organization_id == c.org_id, WholesaleEvent.deal_id.isnot(None))
    if c.visible is not None:
        seen = c.owned(db.query(WholesaleDeal.id).filter(WholesaleDeal.organization_id == c.org_id),
                       WholesaleDeal.assigned_to_id)
        q = q.filter(WholesaleEvent.deal_id.in_(seen.subquery()))
    rows = q.order_by(WholesaleEvent.created_at.desc()).limit(8).all()
    deals = {d.id: d for d in db.query(WholesaleDeal).filter(WholesaleDeal.id.in_([r.deal_id for r in rows] or [""]))}
    props = {p.id: p for p in db.query(WholesaleProperty).filter(
        WholesaleProperty.id.in_([d.property_id for d in deals.values()] or [""]))}
    names = _people(db, [r.actor_user_id for r in rows])
    return {"title": "Live Team Activity",
            "rows": [{"who": names.get(r.actor_user_id) or r.actor_label or r.actor_type, "summary": r.summary,
                      "address": _addr(props.get(getattr(deals.get(r.deal_id), "property_id", None))),
                      "at": _iso(r.created_at), "to": "/wholesale/deals/%s" % r.deal_id} for r in rows],
            "empty": "No deal activity yet."}


def panel_areas(c: _Ctx) -> Optional[Dict[str, Any]]:
    """Where their records are. The product has no territory assignments or
    map layer yet, so this is a count by city of the properties they may see."""
    from app.models.wholesale_models import WholesaleProperty as P
    if not c.may("properties"):
        return None
    q = c.owned(c.live(c.db.query(P.city, func.count(P.id)).filter(P.organization_id == c.org_id), P), P.assigned_to_id)
    rows = [{"city": city or "No city", "count": int(n)} for city, n in
            q.group_by(P.city).order_by(func.count(P.id).desc()).limit(6).all()]
    return {"title": "My Areas", "rows": rows, "total": sum(r["count"] for r in rows),
            "note": "Territories and a map aren't set up in Wholesale yet; this counts your properties by city.",
            "to": "/wholesale/properties", "empty": "No properties in your view yet."}


def panel_buyers(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.models.wholesale_models import WholesaleBuyer, WholesaleBuyerMatch, WholesaleBuyBox, WholesaleDeal, WholesaleProperty
    if not c.may("buyer_intel"):
        return None
    db = c.db
    buyers = c.live(db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == c.org_id,
                                                    WholesaleBuyer.is_active.is_(True)), WholesaleBuyer)
    mq = db.query(WholesaleBuyerMatch).filter(WholesaleBuyerMatch.organization_id == c.org_id,
                                              WholesaleBuyerMatch.disqualified.isnot(True))
    if c.visible is not None:
        seen = c.owned(db.query(WholesaleDeal.id).filter(WholesaleDeal.organization_id == c.org_id),
                       WholesaleDeal.assigned_to_id)
        mq = mq.filter(WholesaleBuyerMatch.deal_id.in_(seen.subquery()))
    top = mq.order_by(WholesaleBuyerMatch.score.desc()).limit(5).all()
    bmap = {b.id: b for b in db.query(WholesaleBuyer).filter(WholesaleBuyer.id.in_([m.buyer_id for m in top] or [""]))}
    deals = {d.id: d for d in db.query(WholesaleDeal).filter(WholesaleDeal.id.in_([m.deal_id for m in top] or [""]))}
    props = {p.id: p for p in db.query(WholesaleProperty).filter(
        WholesaleProperty.id.in_([d.property_id for d in deals.values()] or [""]))}
    out = {"title": "Buyer Match Queue", "buyers": buyers.count(),
           "rows": [{"buyer": (bmap.get(m.buyer_id).company_name or bmap.get(m.buyer_id).contact_name) if bmap.get(m.buyer_id) else "Buyer",
                     "score": round(m.score) if m.score is not None else None,
                     "deal": _addr(props.get(getattr(deals.get(m.deal_id), "property_id", None))),
                     "to": "/wholesale/deals/%s" % m.deal_id} for m in top],
           "to": "/wholesale/buyers", "empty": "No buyer matches yet. Matches appear once deals and buy boxes line up."}
    if c.may("buy_boxes"):
        out["buy_boxes"] = c.live(db.query(WholesaleBuyBox).filter(WholesaleBuyBox.organization_id == c.org_id,
                                                                   WholesaleBuyBox.is_active.is_(True)), WholesaleBuyBox).count()
    return out


def panel_outreach(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.models.wholesale_models import WholesaleBuyerOutreach as O, WholesaleDeal
    if not c.may("investor_outreach"):
        return None
    db, since = c.db, _now() - timedelta(days=7)
    q = db.query(O).filter(O.organization_id == c.org_id, O.created_at >= since)
    if c.visible is not None:
        seen = c.owned(db.query(WholesaleDeal.id).filter(WholesaleDeal.organization_id == c.org_id),
                       WholesaleDeal.assigned_to_id)
        q = q.filter(O.deal_id.in_(seen.subquery()))
    by = dict(q.filter(O.sent_at.isnot(None)).with_entities(O.channel, func.count(O.id)).group_by(O.channel).all())
    return {"title": "Outreach Activity (7 days)", "sent": {k or "other": int(v) for k, v in by.items()},
            "replies": q.filter(O.replied_at.isnot(None)).count(),
            "offers": q.filter(O.offer_amount.isnot(None)).count(),
            "to": "/wholesale/deal-room/investors", "empty": "Nothing sent to buyers in the last 7 days."}


def panel_deal_room(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.services import wholesale_deal_room_manage as MG
    if not c.may("deal_room"):
        return None
    try:
        ov = MG.overview(c.db, c.org_id, c.include_test)
    except Exception:  # noqa: BLE001 - a dashboard panel must not break the page
        return {"title": "Deal Room", "error": "The Deal Room status couldn't be read just now.", "to": "/wholesale/deal-room"}
    k = ov.get("counts") or {}
    return {"title": "Deal Room Status", "status": ov.get("status"),
            "counts": {"listings_live": k.get("listings_live", 0), "investors": k.get("investors", 0),
                       "holds_pending": k.get("holds_pending", 0), "holds_active": k.get("holds_active", 0),
                       "messages_unread": k.get("messages_unread", 0), "files_to_review": k.get("files_to_review", 0),
                       "interest": k.get("interest", 0) if not isinstance(k.get("interest"), (list, dict)) else len(k.get("interest"))},
            "can_publish": c.may("deal_room", "publish"), "to": "/wholesale/deal-room",
            "note": "Publishing and sending stay behind the Deal Room's own approval and sending controls."}


def panel_research(c: _Ctx) -> Optional[Dict[str, Any]]:
    from app.models.evosense_models import EvoSenseIdentityReview as IR, EvoSenseProperty as P
    from app.services.evosense import common as C
    if not c.may("properties", "edit"):            # research means fixing records, so it needs edit rights
        return None
    db = c.db
    base = c.live(db.query(P).filter(P.organization_id == c.org_id, P.archived_at.is_(None)), P)
    conflicts = base.filter(P.has_conflicts.is_(True))
    waiting = base.filter(P.status == C.S_WAITING_DATA)
    reviews = db.query(IR).filter(IR.organization_id == c.org_id, IR.status == "open").count()
    rows = (base.filter((P.has_conflicts.is_(True)) | (P.status == C.S_WAITING_DATA))
            .order_by(P.updated_at.desc()).limit(6).all())
    return {"title": "Research Queue",
            "counts": {"conflicts": conflicts.count(), "waiting_data": waiting.count(), "identity_reviews": reviews,
                       "needs_review": base.filter(P.status == C.S_NEEDS_REVIEW).count()},
            "rows": [{"id": p.id, "address": _addr(p), "county": p.county,
                      "issue": "Conflicting sources" if p.has_conflicts else "Waiting for data",
                      "confidence": p.data_confidence,          # high | medium | low | insufficient
                      "to": "/wholesale/evosense/property/%s" % p.id} for p in rows],
            "to": "/wholesale/evosense/inbox", "empty": "No records need research right now."}


def panel_providers(c: _Ctx) -> Optional[Dict[str, Any]]:
    """Truthful provider state from stored configuration - no network call."""
    from app.services.wholesale_intel import provider_readiness
    if not c.may("paid_lookups"):
        return None
    return {"title": "Provider Readiness", "rows": provider_readiness(), "to": "/wholesale/evosense/controls",
            "can_manage": c.may("settings", "manage"),
            "note": "Shown from saved settings; nothing is called from this screen."}


PANELS: Dict[str, Callable[[_Ctx], Optional[Dict[str, Any]]]] = {
    "tasks": panel_tasks, "pipeline": panel_pipeline, "opportunities": panel_opportunities,
    "appointments": panel_appointments, "callbacks": panel_callbacks, "replies": panel_replies,
    "discovery": panel_discovery, "routes": panel_routes, "visits": panel_visits, "team": panel_team,
    "reviews": panel_reviews, "activity": panel_activity, "areas": panel_areas, "buyers": panel_buyers,
    "outreach": panel_outreach, "deal_room": panel_deal_room, "research": panel_research,
    "providers": panel_providers,
}


def _kpis(panels: Dict[str, Dict[str, Any]], focus: str) -> List[Dict[str, Any]]:
    """Four headline numbers, each taken from a panel already built (one
    calculation, shown twice), in the order this focus cares about."""
    cand = []
    p = {k: v for k, v in panels.items() if "error" not in v}
    if "opportunities" in p:
        cand.append(("open_deals", "Open Deals", p["opportunities"]["count"], "/wholesale"))
    if "tasks" in p:
        cand.append(("tasks", "Due Today", p["tasks"]["count"], None))
    if "appointments" in p:
        cand.append(("appts", "Appointments (7 days)", p["appointments"]["week"], "/wholesale/appointments"))
    if "callbacks" in p:
        cand.append(("callbacks", "Calls to Return", p["callbacks"]["due_today"], "/wholesale/callbacks"))
    if "routes" in p:
        cand.append(("doors", "Doors Left on Routes", p["routes"]["doors_left"], "/wholesale/canvass"))
    if "visits" in p:
        cand.append(("visits", "Field Visits Logged", p["visits"]["count"], "/wholesale/appointments"))
    if "discovery" in p:
        cand.append(("needs_you", "Discovery: Needs You", p["discovery"]["counts"]["needs_you"], "/wholesale/evosense/inbox"))
    if "reviews" in p:
        cand.append(("reviews", "Awaiting Decision", p["reviews"]["count"], None))
    if "team" in p:
        cand.append(("team", "Team Members", p["team"]["count"], "/wholesale/board"))
    if "deal_room" in p and "counts" in p["deal_room"]:
        cand.append(("listings", "Live Listings", p["deal_room"]["counts"]["listings_live"], "/wholesale/deal-room"))
        cand.append(("holds", "Exclusive Requests", p["deal_room"]["counts"]["holds_pending"], "/wholesale/deal-room"))
    if "buyers" in p:
        cand.append(("buyers", "Active Buyers", p["buyers"]["buyers"], "/wholesale/buyers"))
    if "research" in p:
        cand.append(("conflicts", "Records With Conflicts", p["research"]["counts"]["conflicts"], "/wholesale/evosense/inbox"))
        cand.append(("waiting", "Waiting for Data", p["research"]["counts"]["waiting_data"], "/wholesale/evosense/inbox"))
    pref = {"acquisition": ["open_deals", "needs_you", "appts", "tasks"],
            "manager": ["open_deals", "appts", "reviews", "team"],
            "field": ["doors", "appts", "visits", "callbacks"],
            "dispo": ["listings", "buyers", "holds", "open_deals"],
            "research": ["conflicts", "waiting", "needs_you", "tasks"],
            "va": ["tasks", "callbacks", "appts", "open_deals"],
            "investor": ["open_deals", "listings", "appts", "tasks"]}.get(focus, [])
    rank = {k: i for i, k in enumerate(pref)}
    cand.sort(key=lambda t: rank.get(t[0], 99))
    return [{"key": k, "label": l, "value": v, "to": to} for k, l, v, to in cand[:4]]


def home(db, org_id: str, user: User, *, include_test: bool = False,
         preview_profile=None) -> Dict[str, Any]:
    """The composed dashboard for this person in this workspace."""
    eff = A.effective(db, user, org_id)
    preview = None
    if preview_profile is not None:
        # The owner previewing a profile: that profile's permissions and record
        # visibility, filled with what would be visible to the owner under it.
        perms = A._load(preview_profile.permissions_json)
        eff = {"mode": "custom", "permissions": perms, "record_scope": preview_profile.record_scope,
               "profile": {"id": preview_profile.id, "name": preview_profile.name,
                           "template_key": preview_profile.template_key}}
        preview = {"profile_id": preview_profile.id, "name": preview_profile.name}
    visible = A.visible_user_ids(db, org_id, user, eff)
    prof = eff.get("profile") or {}
    tkey = prof.get("template_key") if isinstance(prof, dict) else None
    if eff["mode"] in ("admin", "legacy"):
        focus = "manager"
    else:
        focus = TEMPLATE_FOCUS.get(tkey or "") or guess_focus(eff.get("permissions") or {})
    c = _Ctx(db, org_id, user, eff, visible, include_test, preview=bool(preview))
    built: Dict[str, Dict[str, Any]] = {}
    for key, fn in PANELS.items():
        try:
            out = fn(c)
        except Exception:  # noqa: BLE001 - one broken panel must not blank the page
            import logging
            logging.getLogger(__name__).exception("wholesale home: panel %s failed", key)
            db.rollback()
            out = {"title": key.replace("_", " ").title(), "error": "This panel couldn't load just now."}
        if out is not None:
            built[key] = out
    order = FOCUS[focus]["order"]
    keys = [k for k in order if k in built] + [k for k in built if k not in order]
    actions = [{"key": k, "label": label, "to": to} for k, label, to, mod, act in ACTIONS if A.allows(eff, mod, act)]
    ao = {k: i for i, k in enumerate(ACTION_ORDER.get(focus, []))}
    actions.sort(key=lambda a: ao.get(a["key"], 99))
    scope = eff.get("record_scope") or A.WORKSPACE
    return {
        "person": {"name": user.full_name or user.email, "first_name": (user.full_name or user.email or "").split(" ")[0],
                   "mode": eff["mode"], "profile": prof.get("name") if isinstance(prof, dict) else None},
        "focus": focus, "focus_label": FOCUS[focus]["label"], "focus_sub": FOCUS[focus]["sub"],
        "actions_first": focus in ("dispo", "research", "va"),     # as the approved boards place them
        "record_scope": scope,
        "record_scope_label": {A.ASSIGNED: "Only records assigned to you", A.TEAM: "Your records and your team's",
                               A.WORKSPACE: "Everything in this workspace"}.get(scope, "Everything in this workspace"),
        "modules": sorted(m for m, a in (eff.get("permissions") or {}).items() if "view" in a),
        "kpis": _kpis(built, focus), "panels": [dict(built[k], key=k) for k in keys], "actions": actions,
        "preview": preview, "generated_at": _iso(_now()),
    }
