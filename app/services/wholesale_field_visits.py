"""THE VISIT RECORD - build list #12 (seller interview) and #13 (walkthrough:
room-by-room repairs and photos).

One record per appointment. Whoever can see the appointment can read it
(the rep, their managers, the office). The rep on the visit and anyone who
is not a field login can write it. Answers are cleaned to known questions so
the record stays readable, and money answers are stored as whole dollars.

The repair lines are the Deal Calculator's own (dealCalc.REPAIR_ITEMS), so a
walkthrough can fill the calculator (#14).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.wholesale_ops_models import WholesaleFieldVisit, WholesaleFieldVisitPhoto
from app.services import wholesale_appointments as A
from app.services import wholesale_team as T

# ── the questions ───────────────────────────────────────────────────────────
INTERVIEW_TEXT = ("motivation", "decision_makers", "on_deed", "occupancy_note", "notes")
INTERVIEW_CHOICE = {
    "timeline": ("asap", "30_days", "60_90_days", "flexible", "just_exploring"),
    "occupancy": ("owner", "tenant", "vacant", "family"),
    "behind_on_payments": ("yes", "no", "unknown"),
    "listed_with_agent": ("yes", "no", "was_listed"),
    "condition": ("good", "fair", "poor", "distressed"),
}
INTERVIEW_MONEY = ("owed", "price_wanted", "monthly_payment", "back_taxes")
REPAIR_ITEMS = ("Roof", "HVAC", "Foundation", "Plumbing", "Electrical", "Kitchen", "Bathrooms",
                "Flooring", "Paint (inside & out)", "Windows & doors", "Yard & clean-out", "Other")
ROOMS = ("Front", "Kitchen", "Living", "Bedroom", "Bathroom", "Back yard", "Roof", "HVAC",
         "Foundation", "Garage", "Other")
MAX_PHOTOS = 60


def _money(v) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        n = float(str(v).replace("$", "").replace(",", "").strip())
    except ValueError:
        raise HTTPException(422, "Money answers must be numbers, like 145000.")
    if n < 0 or n > 50_000_000:
        raise HTTPException(422, "That amount does not look right.")
    return int(round(n))


def clean_interview(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in INTERVIEW_TEXT:
        v = (raw.get(k) or "").strip() if isinstance(raw.get(k), str) else None
        if v:
            out[k] = v[:2000]
    for k, choices in INTERVIEW_CHOICE.items():
        v = raw.get(k)
        if v in (None, ""):
            continue
        if v not in choices:
            raise HTTPException(422, "Unknown answer for %s." % k.replace("_", " "))
        out[k] = v
    for k in INTERVIEW_MONEY:
        m = _money(raw.get(k))
        if m is not None:
            out[k] = m
    return out


def clean_repairs(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    cond = raw.get("condition")
    if cond not in (None, ""):
        if cond not in INTERVIEW_CHOICE["condition"]:
            raise HTTPException(422, "Unknown condition.")
        out["condition"] = cond
    lines = {}
    for item, line in (raw.get("items") or {}).items():
        if item not in REPAIR_ITEMS or not isinstance(line, dict):
            continue
        clean = {"needed": bool(line.get("needed"))}
        amt = _money(line.get("amount"))
        if amt is not None:
            clean["amount"] = amt
        note = (line.get("note") or "").strip() if isinstance(line.get("note"), str) else ""
        if note:
            clean["note"] = note[:500]
        if clean["needed"] or "amount" in clean or "note" in clean:
            lines[item] = clean
    out["items"] = lines
    out["total"] = sum(l.get("amount", 0) for l in lines.values() if l.get("needed") or l.get("amount"))
    return out


# ── reading and writing ─────────────────────────────────────────────────────

def _may_write(db: Session, user: User, appt) -> None:
    if T.is_field(db, user) and appt.assigned_to_id != user.id:
        raise HTTPException(404, "Appointment not found")


def _row(db: Session, org_id: str, appt_id: str) -> Optional[WholesaleFieldVisit]:
    return db.query(WholesaleFieldVisit).filter(WholesaleFieldVisit.organization_id == org_id,
                                                WholesaleFieldVisit.appointment_id == appt_id).first()


def _photos(db: Session, org_id: str, appt_id: str) -> List[WholesaleFieldVisitPhoto]:
    return (db.query(WholesaleFieldVisitPhoto)
            .filter(WholesaleFieldVisitPhoto.organization_id == org_id,
                    WholesaleFieldVisitPhoto.appointment_id == appt_id)
            .order_by(WholesaleFieldVisitPhoto.created_at.asc()).all())


def photo_json(p: WholesaleFieldVisitPhoto) -> Dict[str, Any]:
    return {"id": p.id, "room": p.room, "caption": p.caption, "content_type": p.content_type,
            "byte_size": p.byte_size, "created_at": A._iso(p.created_at),
            "url": "/wholesale/appointments/%s/photos/%s" % (p.appointment_id, p.id)}


def visit_json(db: Session, org_id: str, appt) -> Dict[str, Any]:
    row = _row(db, org_id, appt.id)
    return {
        "appointment_id": appt.id,
        "interview": json.loads(row.interview) if row and row.interview else {},
        "repairs": json.loads(row.repairs) if row and row.repairs else {"items": {}, "total": 0},
        "photos": [photo_json(p) for p in _photos(db, org_id, appt.id)],
        "updated_at": A._iso(row.updated_at) if row else None,
        "updated_by_name": A._name(db, row.updated_by_id) if row else None,
        "questions": {"choices": {k: list(v) for k, v in INTERVIEW_CHOICE.items()},
                      "repair_items": list(REPAIR_ITEMS), "rooms": list(ROOMS)},
    }


def get_visit(db: Session, org_id: str, user: User, appt_id: str) -> Dict[str, Any]:
    appt = A.get_visible(db, org_id, user, appt_id)
    return visit_json(db, org_id, appt)


def save_visit(db: Session, org_id: str, user: User, appt_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    appt = A.get_visible(db, org_id, user, appt_id)
    _may_write(db, user, appt)
    row = _row(db, org_id, appt.id)
    if row is None:
        row = WholesaleFieldVisit(organization_id=org_id, appointment_id=appt.id,
                                  evosense_property_id=appt.evosense_property_id,
                                  created_at=datetime.utcnow())
        db.add(row)
    if data.get("interview") is not None:
        row.interview = json.dumps(clean_interview(data["interview"]))
    if data.get("repairs") is not None:
        row.repairs = json.dumps(clean_repairs(data["repairs"]))
    row.updated_by_id = user.id
    row.updated_at = datetime.utcnow()
    db.commit()
    return visit_json(db, org_id, appt)


async def add_photo(db: Session, org_id: str, user: User, appt_id: str, upload, room=None, caption=None):
    from app.services import wholesale_files as files
    appt = A.get_visible(db, org_id, user, appt_id)
    _may_write(db, user, appt)
    if len(_photos(db, org_id, appt.id)) >= MAX_PHOTOS:
        raise HTTPException(422, "That visit already has %d photos." % MAX_PHOTOS)
    data, content_type, original = await files.read_upload(upload, images_only=True)
    stored = files.put(org_id, "field_photo", data, content_type, db=db)
    p = WholesaleFieldVisitPhoto(organization_id=org_id, appointment_id=appt.id,
                                 room=(room if room in ROOMS else None), caption=(caption or None),
                                 original_filename=original, created_by_id=user.id,
                                 created_at=datetime.utcnow(), **stored)
    db.add(p)
    db.commit()
    return photo_json(p)


def _photo(db, org_id, user, appt_id, photo_id) -> WholesaleFieldVisitPhoto:
    appt = A.get_visible(db, org_id, user, appt_id)
    p = (db.query(WholesaleFieldVisitPhoto)
         .filter(WholesaleFieldVisitPhoto.id == photo_id, WholesaleFieldVisitPhoto.organization_id == org_id,
                 WholesaleFieldVisitPhoto.appointment_id == appt.id).first())
    if p is None:
        raise HTTPException(404, "Photo not found")
    return p


def photo_bytes(db: Session, org_id: str, user: User, appt_id: str, photo_id: str):
    from app.services import wholesale_files as files
    p = _photo(db, org_id, user, appt_id, photo_id)
    return files.fetch(p.storage_backend, p.storage_key, db=db), p.content_type


def remove_photo(db: Session, org_id: str, user: User, appt_id: str, photo_id: str) -> None:
    from app.services import wholesale_files as files
    p = _photo(db, org_id, user, appt_id, photo_id)
    if T.is_field(db, user) and p.created_by_id != user.id:
        raise HTTPException(403, "You can remove only photos you took.")
    backend, key = p.storage_backend, p.storage_key
    db.delete(p)
    db.commit()
    files.remove(backend, key, db=db)
