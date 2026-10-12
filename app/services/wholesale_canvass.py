"""CANVASSING - build list #17 (a route of doors by ZIP) and #18 (the quick
door-knock form; hot leads go to a field specialist).

WHO
    Building and handing out routes: anyone who is not a field login (you,
    managers, the office). A canvasser sees and works only their own routes;
    managers see their part of the team tree; admins see everything.

WHERE THE DOORS COME FROM
    EvoSense's own findings in that ZIP, best opportunity first. Skipped:
    owners who opted out (suppressed), properties already in Deal Operations,
    and anything dismissed. Each door keeps the address, the owner of record
    and why it was flagged, so the canvasser needs nothing else.

A HOT LEAD
    becomes a call-back in the Callback Center for the canvasser's manager
    (or whoever built the route), with who they talked to, the number and the
    notes, plus a bell notification - so someone books the field specialist.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.wholesale_ops_models import CANVASS_RESULTS, WholesaleCanvassDoor as Door, WholesaleCanvassRoute as Route
from app.services import wholesale_appointments as A
from app.services import wholesale_team as T

SKIP_STATUSES = ("suppressed", "promoted", "dismissed")
MAX_DOORS = 150


def _addr(p) -> str:
    line2 = " ".join(x for x in (p.state, p.zip_code) if x)
    return ", ".join(x for x in (" ".join(y for y in (p.street_address, p.unit and "#%s" % p.unit) if y),
                                 p.city, line2) if x)


def _why(db, org_id, prop_id) -> Optional[str]:
    from app.models.evosense_models import EvoSenseSignal
    rows = (db.query(EvoSenseSignal.signal_type)
            .filter(EvoSenseSignal.organization_id == org_id, EvoSenseSignal.property_id == prop_id,
                    EvoSenseSignal.active.is_(True)).distinct().limit(4).all())
    return ", ".join(r[0].replace("_", " ").capitalize() for r in rows) or None


def candidates(db: Session, org_id: str, zips: List[str], include_test: bool = False):
    from app.models.evosense_models import EvoSenseProperty as P
    q = db.query(P).filter(P.organization_id == org_id, P.zip_code.in_(zips),
                           P.promoted_deal_id.is_(None), ~P.status.in_(SKIP_STATUSES),
                           P.street_address.isnot(None))
    if not include_test:
        q = q.filter(P.is_test.is_(False))
    return q.order_by(P.opportunity_score.desc().nullslast(), P.street_address.asc())


def _zips(raw) -> List[str]:
    out = [z.strip()[:5] for z in str(raw or "").replace(";", ",").split(",") if z.strip()]
    if not out or any(not z.isdigit() or len(z) != 5 for z in out):
        raise HTTPException(422, "Enter one or more 5-digit ZIP codes, separated by commas.")
    return out[:5]


def preview(db: Session, org_id: str, user: User, zips, include_test=False) -> Dict[str, Any]:
    if T.is_field(db, user):
        raise HTTPException(403, "Ask your manager for a route.")
    zs = _zips(zips)
    return {"zips": zs, "doors": candidates(db, org_id, zs, include_test).count()}


def route_json(db: Session, r: Route, doors: Optional[List[Door]] = None) -> Dict[str, Any]:
    ds = doors if doors is not None else (db.query(Door).filter(Door.route_id == r.id).order_by(Door.sort).all())
    done = sum(1 for d in ds if d.result)
    out = {"id": r.id, "name": r.name, "area": r.area, "status": r.status,
           "route_date": r.route_date.date().isoformat() if r.route_date else None,
           "assigned_to_id": r.assigned_to_id, "assigned_to_name": A._name(db, r.assigned_to_id),
           "created_by_name": A._name(db, r.created_by_id), "doors_total": len(ds), "doors_done": done,
           "hot": sum(1 for d in ds if d.result == "hot"), "is_test": bool(r.is_test)}
    return out


def door_json(d: Door) -> Dict[str, Any]:
    return {"id": d.id, "sort": d.sort, "address": d.address, "owner_of_record": d.owner_of_record,
            "why": d.why, "result": d.result, "talked_to": d.talked_to, "phone": d.phone,
            "permission": d.permission, "interest": d.interest, "notes": d.notes,
            "knocked_at": A._iso(d.knocked_at), "handed_off": bool(d.handed_off_at),
            "directions_url": A.DIRECTIONS + A.quote(d.address) if d.address else None}


def create_route(db: Session, org_id: str, user: User, data: Dict[str, Any]) -> Dict[str, Any]:
    if T.is_field(db, user):
        raise HTTPException(403, "Ask your manager for a route.")
    zs = _zips(data.get("zips"))
    limit = max(1, min(int(data.get("limit") or 40), MAX_DOORS))
    rep = data.get("assigned_to_id") or None
    if rep and rep not in {r["user_id"] for r in A.reps(db, org_id, user)}:
        raise HTTPException(400, "That person cannot be given this route.")
    day = None
    if data.get("route_date"):
        try:
            d = date.fromisoformat(str(data["route_date"])[:10])
        except ValueError:
            raise HTTPException(422, "Route day must look like 2026-10-12.")
        day = A.day_window(d)[0]
    include_test = bool(data.get("include_test"))
    props = candidates(db, org_id, zs, include_test).limit(limit).all()
    if not props:
        raise HTTPException(409, "No doors to knock in %s yet - EvoSense has not found properties there."
                            % ", ".join(zs))
    r = Route(organization_id=org_id, name=(data.get("name") or "").strip()[:120] or "Canvass %s" % ", ".join(zs),
              area=", ".join(zs), route_date=day, assigned_to_id=rep, created_by_id=user.id,
              is_test=all(p.is_test for p in props), created_at=datetime.utcnow())
    db.add(r)
    db.flush()
    from app.services.evosense import contacts as CT
    doors = []
    for i, p in enumerate(props):
        owner = CT.primary_owner(db, p)
        d = Door(organization_id=org_id, route_id=r.id, evosense_property_id=p.id, sort=i, address=_addr(p),
                 owner_of_record=getattr(owner, "display_name", None), why=_why(db, org_id, p.id))
        db.add(d)
        doors.append(d)
    db.commit()
    return {**route_json(db, r, doors), "doors": [door_json(d) for d in doors]}


def _visible_route(db: Session, org_id: str, user: User, route_id: str) -> Route:
    r = db.query(Route).filter(Route.id == route_id, Route.organization_id == org_id).first()
    if r is None:
        raise HTTPException(404, "Route not found")
    visible = A.visible_user_ids(db, org_id, user)
    if visible is not None and r.assigned_to_id not in visible and r.created_by_id != user.id:
        raise HTTPException(404, "Route not found")
    return r


def routes(db: Session, org_id: str, user: User, status: str = "open") -> Dict[str, Any]:
    q = db.query(Route).filter(Route.organization_id == org_id)
    if status in ("open", "done"):
        q = q.filter(Route.status == status)
    visible = A.visible_user_ids(db, org_id, user)
    if visible is not None:
        q = q.filter((Route.assigned_to_id.in_(list(visible))) | (Route.created_by_id == user.id))
    rows = q.order_by(Route.route_date.desc().nullslast(), Route.created_at.desc()).limit(100).all()
    return {"items": [route_json(db, r) for r in rows], "can_build": not T.is_field(db, user)}


def get_route(db: Session, org_id: str, user: User, route_id: str) -> Dict[str, Any]:
    r = _visible_route(db, org_id, user, route_id)
    doors = db.query(Door).filter(Door.route_id == r.id).order_by(Door.sort).all()
    return {**route_json(db, r, doors), "doors": [door_json(d) for d in doors]}


def update_route(db: Session, org_id: str, user: User, route_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    r = _visible_route(db, org_id, user, route_id)
    field = T.is_field(db, user)
    if field and r.assigned_to_id != user.id:
        raise HTTPException(404, "Route not found")
    if "assigned_to_id" in data and data["assigned_to_id"] is not None:
        if field:
            raise HTTPException(403, "Ask your manager to hand this route to someone else.")
        rep = data["assigned_to_id"] or None
        if rep and rep not in {x["user_id"] for x in A.reps(db, org_id, user)}:
            raise HTTPException(400, "That person cannot be given this route.")
        r.assigned_to_id = rep
    if data.get("status") in ("open", "done"):
        r.status = data["status"]
    db.commit()
    return route_json(db, r)


def knock(db: Session, org_id: str, user: User, door_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    d = db.query(Door).filter(Door.id == door_id, Door.organization_id == org_id).first()
    if d is None:
        raise HTTPException(404, "Door not found")
    r = _visible_route(db, org_id, user, d.route_id)
    if T.is_field(db, user) and r.assigned_to_id != user.id:
        raise HTTPException(404, "Door not found")
    res = data.get("result")
    if res is not None and res not in CANVASS_RESULTS:
        raise HTTPException(422, "Unknown result.")
    for k, n in (("talked_to", 120), ("phone", 40), ("notes", 2000)):
        if data.get(k) is not None:
            setattr(d, k, (str(data[k]).strip()[:n] or None))
    if data.get("permission") in ("yes", "no", ""):
        d.permission = data["permission"] or None
    if data.get("interest") in ("hot", "warm", "cold", ""):
        d.interest = data["interest"] or None
    if res is not None:
        d.result = res
        d.knocked_at, d.knocked_by_id = datetime.utcnow(), user.id
        if res == "hot":
            d.interest = "hot"
    if d.result == "hot" and not d.handed_off_at:
        _hand_off(db, org_id, user, r, d)
    db.commit()
    return door_json(d)


def _hand_off(db: Session, org_id: str, user: User, r: Route, d: Door) -> None:
    """A hot door becomes a call-back for whoever sends the field specialist."""
    from app.models.models import NotificationType
    from app.services import wholesale_notify as N
    mem_boss = None
    try:
        from app.models.sales_models import Membership, SCOPE_PRODUCT
        m = (db.query(Membership).filter(Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == "wholesale",
                                         Membership.user_id == user.id, Membership.is_active.is_(True)).first())
        mem_boss = getattr(m, "reports_to_user_id", None)
    except Exception:  # noqa: BLE001
        mem_boss = None
    who = mem_boss or r.created_by_id
    text = ("Hot lead from door knocking (%s): talked to %s%s. %s" % (
        A._name(db, user.id) or "canvasser", d.talked_to or "the owner",
        (", phone " + d.phone) if d.phone else "", d.notes or "")).strip()
    if d.evosense_property_id:
        from app.models.evosense_models import EvoSenseProperty
        from app.services.evosense import followups as FU
        prop = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == d.evosense_property_id,
                                                 EvoSenseProperty.organization_id == org_id).first()
        if prop is not None:
            FU.create(db, org_id, prop, user, due_at=datetime.utcnow().isoformat(), note=text[:2000],
                      assigned_to_id=who)
    N.notify(db, org_id, kind=NotificationType.WHOLESALE_INQUIRY, assignee_id=who,
             message=("Hot lead at %s - book a field specialist. %s" % (d.address, text))[:900],
             link=("/wholesale/evosense/property/%s" % d.evosense_property_id) if d.evosense_property_id
             else "/wholesale/callbacks")
    d.handed_off_at = datetime.utcnow()
