"""EARLIER WORK, READ IN PLACE (Issue #507).

Restland's correspondence and appointments predate its program record: its
21 original inbound replies live in `replies` (not `program_responses`), its
legacy follow-up state in `pipeline_conversations`, its booking history in
`booking_links` and its eleven appointment case files in
`appointment_case_files` (a table with no ORM model). The program screens only
read the program tables, so all of it looked gone.

This module READS those rows where they are. Nothing is written, replayed
through inbound handling, or converted into program state: an old reply here
never raises an alert, never reaches the AI setter, never changes a journey,
and never counts as unread or as a hand-off. A reply that the program already
recorded (ProgramResponse.reply_id) is shown once, from the program side.
"""
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.models import BookingLink, Lead, PipelineConversation, Reply
from app.models.program_models import ProgramResponse


def _iso(dt) -> Optional[str]:
    return dt.isoformat() + "Z" if isinstance(dt, datetime) else None


def _cls(v) -> Optional[str]:
    return getattr(v, "value", v) if v is not None else None


def _already_in_program(db: Session, lead_ids) -> set:
    return {r for (r,) in db.query(ProgramResponse.reply_id)
            .filter(ProgramResponse.lead_id.in_(lead_ids), ProgramResponse.reply_id.isnot(None)).all()}


def earlier_replies(db: Session, lead: Lead) -> List[Dict]:
    """The family's original inbound replies not already shown as program
    responses, oldest first, marked historical."""
    rows = db.query(Reply).filter(Reply.lead_id == lead.id).order_by(Reply.received_at, Reply.id).all()
    seen = _already_in_program(db, [lead.id])
    return [{"who": "family", "text": r.body, "channel": "email" if (r.source or "") == "email" else "sms",
             "at": _iso(r.received_at), "historical": True, "reply_id": r.id,
             "classification": _cls(r.classification)}
            for r in rows if r.id not in seen]


def earlier_record(db: Session, lead: Lead) -> Optional[Dict]:
    """The legacy follow-up record (pipeline_conversations) as it was left -
    a summary only; it is not an active program conversation."""
    pc = (db.query(PipelineConversation).filter(PipelineConversation.lead_id == lead.id,
                                                PipelineConversation.organization_id == lead.organization_id)
          .order_by(PipelineConversation.created_at.desc()).first())
    if pc is None:
        return None
    return {"stage": pc.stage, "lead_type": pc.lead_type, "channel": pc.channel,
            "touch_number": pc.touch_number, "messages_sent": pc.messages_sent,
            "replies_received": pc.replies_received, "paused": bool(pc.paused),
            "flagged": bool(pc.flagged), "flag_reason": pc.flag_reason,
            "started_at": _iso(pc.started_at), "completed_at": _iso(pc.completed_at),
            "last_outbound_at": _iso(pc.last_outbound_at), "last_inbound_at": _iso(pc.last_inbound_at),
            "booked_at": _iso(pc.booked_at), "appointment_kept_at": _iso(pc.appointment_kept_at),
            "updated_at": _iso(pc.updated_at)}


def bookings(db: Session, lead: Lead) -> List[Dict]:
    """Every booking that has a time - booked, confirmed, completed,
    cancelled - newest first, with its real status. Pending links that were
    never used are not appointments and are not listed."""
    from app.services import booking_time as _bt
    from app.models.models import User
    rows = (db.query(BookingLink).filter(BookingLink.lead_id == lead.id, BookingLink.booked_time.isnot(None))
            .order_by(BookingLink.booked_time.desc()).all())
    staff = {u.id: (u.full_name or u.email) for u in db.query(User)
             .filter(User.id.in_([b.user_id for b in rows] or ["-"])).all()}
    return [{"id": b.id, "status": b.status, "when": _bt.wire(db, b), "label": getattr(b, "appt_label", None),
             "staff": staff.get(b.user_id), "created_at": _iso(b.created_at)} for b in rows]


CASE_COLUMNS = ("id, lead_id, booking_link_id, appointment_date, appointment_type, outcome_type, case_status, "
                "next_action, next_action_date, next_action_notes, advisor_notes, objections_raised, "
                "client_concerns, products_discussed, products_sold, referral_potential, created_at, updated_at")


def _case_json(r, booking_status: Dict[str, str]) -> Dict:
    m = dict(r._mapping)
    out = {k: (_iso(v) if isinstance(v, datetime) else v) for k, v in m.items()}
    bl = m.get("booking_link_id")
    out["booking_status"] = booking_status.get(bl) if bl else None
    # A case file pointing at a booking that no longer exists is shown as such,
    # never re-linked to a guess.
    out["booking_missing"] = bool(bl) and bl not in booking_status
    return out


def case_files(db: Session, org_id: str, lead_id: Optional[str] = None,
               offset: int = 0, limit: int = 50) -> Dict:
    """Appointment case files of this workspace, read-only. With lead_id: that
    family's. Without: all of them, including the ones whose family record no
    longer exists (flagged `family_missing`)."""
    try:
        return _case_files(db, org_id, lead_id, offset, limit)
    except Exception:                                    # noqa: BLE001 - table not created on this database
        db.rollback()
        return {"items": [], "total": 0, "offset": offset, "limit": limit, "available": False}


def _case_files(db: Session, org_id: str, lead_id: Optional[str], offset: int, limit: int) -> Dict:
    where = "WHERE c.organization_id = :org" + (" AND c.lead_id = :lead" if lead_id else "")
    params = {"org": org_id, "lead": lead_id, "lim": int(limit), "off": int(offset)}
    total = db.execute(text("SELECT count(*) FROM appointment_case_files c " + where), params).scalar() or 0
    rows = db.execute(text(
        "SELECT " + ", ".join("c." + c.strip() for c in CASE_COLUMNS.split(",")) +
        ", l.first_name AS fam_first, l.last_name AS fam_last, (l.id IS NULL) AS family_missing "
        "FROM appointment_case_files c LEFT JOIN leads l ON l.id = c.lead_id AND l.organization_id = c.organization_id "
        + where + " ORDER BY c.appointment_date DESC, c.id LIMIT :lim OFFSET :off"), params).fetchall()
    bl_ids = [r._mapping["booking_link_id"] for r in rows if r._mapping["booking_link_id"]]
    status = {b.id: b.status for b in db.query(BookingLink.id, BookingLink.status)
              .filter(BookingLink.id.in_(bl_ids or ["-"])).all()}
    items = []
    for r in rows:
        j = _case_json(r, status)
        first, last = j.pop("fam_first", None), j.pop("fam_last", None)
        j["family_missing"] = bool(j.get("family_missing"))
        j["family_name"] = None if j["family_missing"] else (("%s %s" % (first or "", last or "")).strip().title()
                                                              or "Family")
        items.append(j)
    return {"items": items, "total": int(total), "offset": offset, "limit": limit, "available": True}


def earlier_conversations(db: Session, org_id: str, offset: int = 0, limit: int = 25) -> Dict:
    """Families with original replies (read in place), newest reply first,
    one row per family. Historical: never unread, never a hand-off."""
    from sqlalchemy import func
    base = (db.query(Reply.lead_id, func.count(Reply.id).label("n"), func.max(Reply.received_at).label("last"))
            .join(Lead, Lead.id == Reply.lead_id)
            .filter(Lead.organization_id == org_id, Lead.is_test.isnot(True))
            .group_by(Reply.lead_id))
    total = base.count()
    page = base.order_by(func.max(Reply.received_at).desc(), Reply.lead_id).offset(offset).limit(limit).all()
    ids = [p.lead_id for p in page]
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(ids or ["-"])).all()}
    latest = {}
    for r in (db.query(Reply).filter(Reply.lead_id.in_(ids or ["-"])).order_by(Reply.received_at).all()):
        latest[r.lead_id] = r
    items = []
    for p in page:
        l, r = leads.get(p.lead_id), latest.get(p.lead_id)
        items.append({"lead_id": p.lead_id,
                      "name": ("%s %s" % (l.first_name or "", l.last_name or "")).strip().title() if l else "Family",
                      "replies": int(p.n), "last_at": _iso(p.last),
                      "last_text": (r.body or "")[:240] if r else None,
                      "channel": ("email" if r is not None and (r.source or "") == "email" else "sms"),
                      "historical": True})
    return {"items": items, "total": int(total), "offset": offset, "limit": limit}
