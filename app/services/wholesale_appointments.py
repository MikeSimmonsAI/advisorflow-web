"""FIELD APPOINTMENTS - build list #8 (assign to a rep), #9 (my appointments
today) and #10 (book from a call).

WHO SEES WHAT
    admins, and logins with full access   every appointment in the workspace
    managers and team leads               their own and everyone under them (Team page tree)
    field specialists and canvassers      only their own

WHO CHANGES WHAT
    Field logins may mark their OWN visit done / no-show and add notes. Booking,
    moving, reassigning and cancelling is for everyone else who can see it.

Times are stored in UTC. "Today" is the workspace's day in Central time
(Dallas-Fort Worth), not the server's.

Every query is filtered by the acting organization. Another company's
appointment is a 404, never a 403, so an id cannot be probed.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Lead, User
from app.models.wholesale_ops_models import APPOINTMENT_STATUSES, WholesaleFieldAppointment
from app.services import wholesale_team as T

CENTRAL = ZoneInfo("America/Chicago")
DIRECTIONS = "https://www.google.com/maps/dir/?api=1&destination="
DURATIONS = (15, 30, 45, 60, 90, 120)


def _iso(dt) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def _now() -> datetime:
    return datetime.utcnow()


def parse_when(raw) -> datetime:
    try:
        dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise HTTPException(422, "Pick a date and time for the appointment.")
    if dt.tzinfo is None:                       # a bare time is Central, like the screen shows it
        dt = dt.replace(tzinfo=CENTRAL)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def day_window(day: Optional[date] = None):
    """UTC start/end of one Central-time day."""
    if day is None:
        day = datetime.now(CENTRAL).date()
    start = datetime.combine(day, time(0, 0), tzinfo=CENTRAL)
    end = start + timedelta(days=1)
    to_utc = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)
    return to_utc(start), to_utc(end), day


# ── who is on whose team ────────────────────────────────────────────────────

def _is_admin_here(db: Session, user: User) -> bool:
    if getattr(user, "role", None) in ("god_admin", "super_admin"):
        return True
    from app.services.lead_scope import effective_role
    return (effective_role(user, db) or "").lower() in ("org_admin", "super_admin")


def visible_user_ids(db: Session, org_id: str, user: User) -> Optional[Set[str]]:
    """None = everyone in the workspace. Otherwise the people whose
    appointments this person may see (always including themselves)."""
    if _is_admin_here(db, user):
        return None
    role = T.role_of(db, user)
    if role in (None, T.MEMBER):
        return None
    if role in T.FIELD_ROLES:
        return {user.id}
    # manager / team lead: themselves and everyone below them in the tree
    from app.models.sales_models import Membership, SCOPE_PRODUCT
    rows = (db.query(Membership.user_id, Membership.reports_to_user_id)
            .join(User, User.id == Membership.user_id)
            .filter(Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == "wholesale",
                    Membership.is_active.is_(True), User.organization_id == org_id).all())
    kids: Dict[str, List[str]] = {}
    for uid, boss in rows:
        if boss:
            kids.setdefault(boss, []).append(uid)
    seen, todo = {user.id}, [user.id]
    while todo:
        for k in kids.get(todo.pop(), []):
            if k not in seen:
                seen.add(k)
                todo.append(k)
    return seen


def may_book(db: Session, user: User) -> bool:
    return not T.is_field(db, user)


def reps(db: Session, org_id: str, user: User) -> List[Dict[str, Any]]:
    """The people this person may give an appointment to."""
    from app.services import product_access as PA
    visible = visible_user_ids(db, org_id, user)
    people = (db.query(User).filter(User.organization_id == org_id, User.is_active.is_(True))
              .order_by(User.full_name.asc()).all())
    out = []
    for u in people:
        if visible is not None and u.id not in visible:
            continue
        if not PA.may_use(db, u, PA.WHOLESALE):
            continue
        role = T.role_of(db, u)
        out.append({"user_id": u.id, "name": u.full_name or u.email,
                    "role": role, "role_label": T.ROLE_LABELS.get(role) if role else None,
                    "field": role in T.FIELD_ROLES})
    out.sort(key=lambda r: (not r["field"], (r["name"] or "").lower()))
    return out


# ── what an appointment points at ───────────────────────────────────────────

def _addr(row) -> Optional[str]:
    if row is None:
        return None
    line2 = " ".join(x for x in (getattr(row, "state", None), getattr(row, "zip_code", None)) if x)
    parts = [getattr(row, "street_address", None), getattr(row, "city", None), line2]
    return ", ".join(p for p in parts if p) or None


def _evo_property(db, org_id, pid):
    if not pid:
        return None
    from app.models.evosense_models import EvoSenseProperty
    row = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == pid,
                                            EvoSenseProperty.organization_id == org_id).first()
    if row is None:
        raise HTTPException(404, "Property not found")
    return row


def _ws_property(db, org_id, pid):
    if not pid:
        return None
    from app.models.wholesale_models import WholesaleProperty
    row = db.query(WholesaleProperty).filter(WholesaleProperty.id == pid,
                                             WholesaleProperty.organization_id == org_id).first()
    if row is None:
        raise HTTPException(404, "Property not found")
    return row


def _deal(db, org_id, did):
    if not did:
        return None
    from app.models.wholesale_models import WholesaleDeal
    row = db.query(WholesaleDeal).filter(WholesaleDeal.id == did,
                                         WholesaleDeal.organization_id == org_id).first()
    if row is None:
        raise HTTPException(404, "Deal not found")
    return row


def _lead(db, org_id, lid):
    if not lid:
        return None
    row = db.query(Lead).filter(Lead.id == lid, Lead.organization_id == org_id).first()
    if row is None:
        raise HTTPException(404, "Seller not found")
    return row


def _rep(db, org_id, user, rep_id) -> Optional[str]:
    if not rep_id:
        return None
    if rep_id not in {r["user_id"] for r in reps(db, org_id, user)}:
        raise HTTPException(400, "That person cannot be given this appointment.")
    return rep_id


def _name(db, uid) -> Optional[str]:
    if not uid:
        return None
    u = db.query(User).filter(User.id == uid).first()
    return (u.full_name or u.email) if u else None


def appt_json(db, a: WholesaleFieldAppointment) -> Dict[str, Any]:
    lead = db.query(Lead).filter(Lead.id == a.lead_id, Lead.organization_id == a.organization_id).first() \
        if a.lead_id else None
    seller = " ".join(x for x in (getattr(lead, "first_name", None), getattr(lead, "last_name", None)) if x) or None
    local = a.starts_at.replace(tzinfo=timezone.utc).astimezone(CENTRAL) if a.starts_at else None
    return {
        "id": a.id, "status": a.status, "title": a.title, "notes": a.notes,
        "starts_at": _iso(a.starts_at), "duration_minutes": a.duration_minutes,
        "local_date": local.date().isoformat() if local else None,
        "local_time": local.strftime("%I:%M %p").lstrip("0") if local else None,
        "address": a.address,
        "directions_url": (DIRECTIONS + quote(a.address)) if a.address else None,
        "evosense_property_id": a.evosense_property_id, "property_id": a.property_id,
        "deal_id": a.deal_id, "lead_id": a.lead_id, "seller_name": seller,
        "seller_phone": getattr(lead, "phone", None),
        "assigned_to_id": a.assigned_to_id, "assigned_to_name": _name(db, a.assigned_to_id),
        "outcome": a.outcome, "outcome_note": a.outcome_note,
        "source": a.source, "source_ref": a.source_ref,
        "created_by_name": _name(db, a.created_by_id),
        "completed_at": _iso(a.completed_at), "is_test": bool(a.is_test),
    }


# ── book, list, change ──────────────────────────────────────────────────────

def book(db: Session, org_id: str, user: User, data: Dict[str, Any]) -> WholesaleFieldAppointment:
    if not may_book(db, user):
        raise HTTPException(403, "Field logins cannot book appointments. Ask your manager.")
    if not data.get("starts_at"):
        raise HTTPException(422, "Pick a date and time for the appointment.")
    evo = _evo_property(db, org_id, data.get("evosense_property_id"))
    wsp = _ws_property(db, org_id, data.get("property_id"))
    deal = _deal(db, org_id, data.get("deal_id"))
    if wsp is None and deal is not None and getattr(deal, "property_id", None):
        wsp = _ws_property(db, org_id, deal.property_id)
    lead = _lead(db, org_id, data.get("lead_id") or getattr(deal, "seller_lead_id", None))
    address = (data.get("address") or "").strip() or _addr(evo) or _addr(wsp)
    if not address and evo is None and wsp is None:
        raise HTTPException(422, "Where is the appointment? Add the address.")
    minutes = int(data.get("duration_minutes") or 60)
    if minutes not in DURATIONS:
        raise HTTPException(422, "Length must be 15, 30, 45, 60, 90 or 120 minutes.")
    title = (data.get("title") or "").strip() or ("Visit - %s" % (address or "seller"))[:200]
    a = WholesaleFieldAppointment(
        organization_id=org_id,
        evosense_property_id=getattr(evo, "id", None), property_id=getattr(wsp, "id", None),
        deal_id=getattr(deal, "id", None), lead_id=getattr(lead, "id", None),
        assigned_to_id=_rep(db, org_id, user, data.get("assigned_to_id")),
        starts_at=parse_when(data["starts_at"]), duration_minutes=minutes,
        address=address, title=title, notes=(data.get("notes") or "").strip() or None,
        status="scheduled", source=data.get("source") or ("property" if evo or wsp else "manual"),
        source_ref=data.get("source_ref"), created_by_id=user.id,
        is_test=bool(getattr(evo, "is_test", False) or getattr(wsp, "is_test", False) or getattr(lead, "is_test", False)
                     or getattr(deal, "is_test", False)),
        created_at=_now())
    if a.source not in ("manual", "property", "callback", "call"):
        a.source = "manual"
    db.add(a)
    db.commit()
    return a


def get_visible(db: Session, org_id: str, user: User, appt_id: str) -> WholesaleFieldAppointment:
    a = db.query(WholesaleFieldAppointment).filter(WholesaleFieldAppointment.id == appt_id,
                                                   WholesaleFieldAppointment.organization_id == org_id).first()
    visible = visible_user_ids(db, org_id, user) if a is not None else None
    if a is None or (visible is not None and a.assigned_to_id not in visible):
        raise HTTPException(404, "Appointment not found")
    return a


def listing(db: Session, org_id: str, user: User, *, when: str = "today", day: Optional[str] = None,
            rep_id: Optional[str] = None, property_id: Optional[str] = None,
            include_test: bool = False) -> Dict[str, Any]:
    q = db.query(WholesaleFieldAppointment).filter(WholesaleFieldAppointment.organization_id == org_id)
    if not include_test:
        q = q.filter(WholesaleFieldAppointment.is_test.is_(False))
    visible = visible_user_ids(db, org_id, user)
    if visible is not None:
        q = q.filter(WholesaleFieldAppointment.assigned_to_id.in_(list(visible)))
    if rep_id == "unassigned":
        q = q.filter(WholesaleFieldAppointment.assigned_to_id.is_(None))
    elif rep_id == "me":
        q = q.filter(WholesaleFieldAppointment.assigned_to_id == user.id)
    elif rep_id:
        q = q.filter(WholesaleFieldAppointment.assigned_to_id == rep_id)
    if property_id:
        q = q.filter(WholesaleFieldAppointment.evosense_property_id == property_id)
    the_day = None
    if day:
        try:
            the_day = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(422, "day must look like 2026-10-12")
    start, end, the_day = day_window(the_day)
    now = _now()
    if property_id:
        q = q.order_by(WholesaleFieldAppointment.starts_at.desc())
    elif when == "today" or day:
        q = q.filter(WholesaleFieldAppointment.starts_at >= start, WholesaleFieldAppointment.starts_at < end,
                     WholesaleFieldAppointment.status != "cancelled").order_by(WholesaleFieldAppointment.starts_at.asc())
    elif when == "upcoming":
        q = q.filter(WholesaleFieldAppointment.starts_at >= now, WholesaleFieldAppointment.status == "scheduled") \
             .order_by(WholesaleFieldAppointment.starts_at.asc())
    elif when == "past":
        q = q.filter(WholesaleFieldAppointment.starts_at < now).order_by(WholesaleFieldAppointment.starts_at.desc())
    else:
        q = q.order_by(WholesaleFieldAppointment.starts_at.desc())
    items = [appt_json(db, a) for a in q.limit(300).all()]
    return {"when": when, "day": the_day.isoformat(), "items": items,
            "can_book": may_book(db, user), "is_field": T.is_field(db, user)}


def change(db: Session, org_id: str, user: User, appt_id: str, data: Dict[str, Any]) -> WholesaleFieldAppointment:
    a = get_visible(db, org_id, user, appt_id)
    field = T.is_field(db, user)
    if field:
        if a.assigned_to_id != user.id:
            raise HTTPException(404, "Appointment not found")
        allowed = {"status", "notes"}
        if set(k for k, v in data.items() if v is not None) - allowed:
            raise HTTPException(403, "Ask your manager to move or reassign this appointment.")
        if data.get("status") not in (None, "completed", "no_show"):
            raise HTTPException(403, "You can mark a visit done or a no-show. Ask your manager to cancel it.")
    if "assigned_to_id" in data and data["assigned_to_id"] is not None:
        a.assigned_to_id = _rep(db, org_id, user, data["assigned_to_id"]) if data["assigned_to_id"] else None
    if data.get("starts_at"):
        a.starts_at = parse_when(data["starts_at"])
        if a.status in ("no_show", "cancelled"):
            a.status = "scheduled"          # moved to a new time: it is back on
    if data.get("duration_minutes"):
        if int(data["duration_minutes"]) not in DURATIONS:
            raise HTTPException(422, "Length must be 15, 30, 45, 60, 90 or 120 minutes.")
        a.duration_minutes = int(data["duration_minutes"])
    if data.get("address") is not None and not field:
        a.address = data["address"].strip() or a.address
    if data.get("notes") is not None:
        a.notes = data["notes"].strip() or None
    status = data.get("status")
    if status is not None:
        if status not in APPOINTMENT_STATUSES:
            raise HTTPException(422, "Unknown status.")
        a.status = status
        a.completed_at = _now() if status == "completed" else None
    db.commit()
    return a
