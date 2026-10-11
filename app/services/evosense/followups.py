"""Follow-ups on EvoSense properties: "call this owner back on Thursday".

A caller working the Discovery Inbox promises call-backs long before a
property becomes a Wholesale deal. These are those promises. They appear in
the Callback Center beside deal callbacks (Due now / Upcoming / Overdue /
Completed), so one list holds every call somebody owes an owner.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.evosense_models import EvoSenseFollowUp, EvoSenseProperty


def _iso(dt) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def create(db, org_id: str, prop: EvoSenseProperty, user, *, due_at, note: Optional[str] = None,
           assigned_to_id: Optional[str] = None) -> EvoSenseFollowUp:
    from app.services import wholesale_ops as OPS
    when = OPS.parse_when(due_at)
    fu = EvoSenseFollowUp(organization_id=org_id, property_id=prop.id, due_at=when, status="due",
                          note=(note or "").strip()[:2000] or None,
                          assigned_to_id=OPS.assignable_user(db, org_id, assigned_to_id) or getattr(user, "id", None),
                          created_by_id=getattr(user, "id", None), is_test=bool(prop.is_test),
                          created_at=datetime.utcnow())
    db.add(fu)
    db.flush()
    return fu


def get(db, org_id: str, followup_id: str) -> EvoSenseFollowUp:
    fu = (db.query(EvoSenseFollowUp)
          .filter(EvoSenseFollowUp.id == followup_id, EvoSenseFollowUp.organization_id == org_id).first())
    if fu is None:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    return fu


def finish(db, fu: EvoSenseFollowUp, user, status: str, note: Optional[str] = None) -> EvoSenseFollowUp:
    if fu.status != "due":
        raise HTTPException(status_code=409, detail="This follow-up is already %s." % fu.status)
    fu.status = status
    fu.outcome_note = (note or "").strip()[:2000] or None
    fu.completed_at = datetime.utcnow()
    fu.completed_by_id = getattr(user, "id", None)
    db.flush()
    return fu


def item(db, fu: EvoSenseFollowUp, now: datetime, prop: Optional[EvoSenseProperty] = None) -> Dict[str, Any]:
    """The Callback Center's item shape, for an EvoSense property follow-up."""
    from app.services import wholesale_ops as OPS
    from app.services.evosense import contacts as CT
    if prop is None:
        prop = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == fu.property_id).first()
    owner = CT.primary_owner(db, prop) if prop is not None else None
    phone = None
    if prop is not None:
        for cp, _info in CT.contact_points_for(db, prop):
            if getattr(cp, "kind", None) == "phone" and getattr(cp, "status", "active") == "active":
                phone = cp.raw_value or cp.value
                break
    address = ", ".join(x for x in (getattr(prop, "street_address", None), getattr(prop, "city", None)) if x)
    return {"id": "fu:%s" % fu.id, "kind": "followup", "followup_id": fu.id, "lead_id": None, "deal_id": None,
            "property_id": fu.property_id, "evosense_property_id": fu.property_id,
            "seller_name": getattr(owner, "display_name", None), "address": address or None, "phone": phone,
            "dnc": None, "due_at": _iso(fu.due_at), "status": fu.status,
            "bucket": OPS.bucket_of(fu.status, fu.due_at, now), "notes": fu.note,
            "outcome_note": fu.outcome_note, "assigned_to_id": fu.assigned_to_id,
            "assigned_to_name": OPS.user_name(db, fu.assigned_to_id),
            "completed_at": _iso(fu.completed_at), "source": "evosense", "source_ref": fu.property_id,
            "is_test": bool(fu.is_test), "created_at": _iso(fu.created_at)}


def center_items(db, org_id: str, now: datetime, *, user=None, mine: bool = False, include_test: bool = False,
                 completed_limit: int = 50) -> List[Dict[str, Any]]:
    q = db.query(EvoSenseFollowUp).filter(EvoSenseFollowUp.organization_id == org_id)
    if not include_test:
        q = q.filter(EvoSenseFollowUp.is_test.is_(False))
    if mine and user is not None:
        q = q.filter(EvoSenseFollowUp.assigned_to_id == user.id)
    rows = (q.filter(EvoSenseFollowUp.status == "due").order_by(EvoSenseFollowUp.due_at.asc()).limit(1000).all()
            + q.filter(EvoSenseFollowUp.status == "completed")
            .order_by(EvoSenseFollowUp.completed_at.desc()).limit(completed_limit).all())
    return [item(db, fu, now) for fu in rows]


def open_for(db, org_id: str, prop: EvoSenseProperty) -> List[Dict[str, Any]]:
    now = datetime.utcnow()
    rows = (db.query(EvoSenseFollowUp)
            .filter(EvoSenseFollowUp.organization_id == org_id, EvoSenseFollowUp.property_id == prop.id,
                    EvoSenseFollowUp.status == "due")
            .order_by(EvoSenseFollowUp.due_at.asc()).all())
    return [item(db, fu, now, prop) for fu in rows]
