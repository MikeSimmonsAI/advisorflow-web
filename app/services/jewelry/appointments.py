"""Appointment Center - every appointment IS a BookingLink (the platform's record).

Times are the store's local wall-clock time, stored the way the platform's
other programs store BookingLink.booked_time. Double booking is refused for the
same staff member: no two open appointments for one person may overlap.

NOTHING HERE MESSAGES A CUSTOMER. Every appointment is written with the
platform's confirmation / reminder / survey markers already set, so the
existing reminder and post-visit survey jobs skip it. Reminders for this
program stay off until messaging is configured and the owner switches it on.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.jewelry_models import JewelryAppointmentDetail, JewelryCampaign
from app.models.models import BookingLink, Lead
from app.models.work_models import LeadTask
from app.services.jewelry import common as C

MIN_MIN, MAX_MIN = 10, 480


def _token() -> str:
    import secrets
    return "jw_" + secrets.token_urlsafe(18)


def parse_local(raw: str) -> datetime:
    try:
        s = (raw or "").strip().replace("Z", "")
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, "starts_at must look like 2026-11-12T14:30")
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.replace(second=0, microsecond=0)


def types_for(profile) -> List[str]:
    t = C.jload(getattr(profile, "appointment_types", None), None)
    return t if isinstance(t, list) and t else list(C.DEFAULT_APPOINTMENT_TYPES)


def _rows(ctx: C.Ctx, q):
    return q.join(JewelryAppointmentDetail, JewelryAppointmentDetail.booking_link_id == BookingLink.id) \
            .filter(JewelryAppointmentDetail.organization_id == ctx.org_id)


def appt_json(b: BookingLink, d: JewelryAppointmentDetail, lead: Optional[Lead], names: Dict[str, str]) -> Dict[str, Any]:
    dur = int(b.appt_duration or 30)
    return {"id": b.id, "lead_id": b.lead_id, "customer_name": C.lead_name(lead) if lead else "Customer",
            "type": b.appt_label, "starts_at": b.booked_time.isoformat(timespec="minutes") if b.booked_time else None,
            "ends_at": (b.booked_time + timedelta(minutes=dur)).isoformat(timespec="minutes") if b.booked_time else None,
            "duration_minutes": dur, "staff_id": b.user_id, "staff_name": names.get(b.user_id or "", "Staff"),
            "status": d.status, "internal_notes": d.internal_notes, "campaign_id": d.campaign_id,
            "follow_up_task_id": d.follow_up_task_id, "created_at": C.iso(d.created_at)}


def _names(ctx: C.Ctx) -> Dict[str, str]:
    from app.services.jewelry.team import members
    return {m["id"]: m["name"] for m in members(ctx.db, ctx.org_id)}


def list_range(ctx: C.Ctx, start: Optional[str], end: Optional[str], staff_id: Optional[str] = None,
               include_cancelled: bool = True) -> List[Dict[str, Any]]:
    d0 = parse_local(start) if start else datetime.utcnow() - timedelta(days=31)
    d1 = parse_local(end) if end else d0 + timedelta(days=62)
    if d1 - d0 > timedelta(days=400):
        raise HTTPException(400, "Ask for at most about a year at a time")
    q = _rows(ctx, ctx.db.query(BookingLink, JewelryAppointmentDetail)) \
        .filter(BookingLink.booked_time >= d0, BookingLink.booked_time < d1)
    if not ctx.manager:
        q = q.filter(BookingLink.user_id == ctx.user.id)
    elif staff_id:
        q = q.filter(BookingLink.user_id == staff_id)
    if not include_cancelled:
        q = q.filter(JewelryAppointmentDetail.status != "cancelled")
    rows = q.order_by(BookingLink.booked_time.asc()).all()
    leads = {l.id: l for l in ctx.db.query(Lead).filter(Lead.id.in_([b.lead_id for b, _ in rows] or ["-"])).all()}
    names = _names(ctx)
    return [appt_json(b, d, leads.get(b.lead_id), names) for b, d in rows]


def list_for_lead(ctx: C.Ctx, lead_id: str) -> List[Dict[str, Any]]:
    rows = _rows(ctx, ctx.db.query(BookingLink, JewelryAppointmentDetail)) \
        .filter(BookingLink.lead_id == lead_id).order_by(BookingLink.booked_time.desc()).limit(100).all()
    lead = ctx.db.query(Lead).filter(Lead.id == lead_id).first()
    names = _names(ctx)
    return [appt_json(b, d, lead, names) for b, d in rows]


def _load(ctx: C.Ctx, appt_id: str):
    row = _rows(ctx, ctx.db.query(BookingLink, JewelryAppointmentDetail)).filter(BookingLink.id == appt_id).first()
    if not row:
        raise HTTPException(404, "Appointment not found")
    b, d = row
    if not ctx.manager and b.user_id != ctx.user.id:
        raise HTTPException(404, "Appointment not found")
    return b, d


def _check_overlap(ctx: C.Ctx, staff_id: str, start: datetime, minutes: int, exclude: Optional[str] = None):
    end = start + timedelta(minutes=minutes)
    q = _rows(ctx, ctx.db.query(BookingLink, JewelryAppointmentDetail)) \
        .filter(BookingLink.user_id == staff_id,
                JewelryAppointmentDetail.status.in_(C.OPEN_APPOINTMENT),
                BookingLink.booked_time < end,
                BookingLink.booked_time > start - timedelta(minutes=MAX_MIN))
    if exclude:
        q = q.filter(BookingLink.id != exclude)
    for b, _d in q.all():
        b_end = b.booked_time + timedelta(minutes=int(b.appt_duration or 30))
        if b.booked_time < end and b_end > start:
            raise HTTPException(409, "That staff member already has an appointment from %s to %s. Pick another time." % (
                b.booked_time.strftime("%-I:%M %p"), b_end.strftime("%-I:%M %p")))


def _staff(ctx: C.Ctx, staff_id: Optional[str]) -> str:
    from app.services.jewelry.team import member_ids
    ids = member_ids(ctx.db, ctx.org_id)
    sid = staff_id or ctx.user.id
    if sid not in ids:
        if staff_id:
            raise HTTPException(404, "That staff member is not on this workspace's team")
        raise HTTPException(409, "Choose which staff member the appointment is with.")
    if not ctx.manager and sid != ctx.user.id:
        raise HTTPException(403, "Only an owner or manager can book appointments for someone else")
    return sid


def create(ctx: C.Ctx, lead: Lead, profile, appt_type: str, starts_at: str, duration: Optional[int],
           staff_id: Optional[str], notes: Optional[str], follow_up_days: Optional[int],
           campaign_id: Optional[str]) -> Dict[str, Any]:
    types = types_for(profile)
    if appt_type not in types:
        raise HTTPException(400, "Unknown appointment type")
    start = parse_local(starts_at)
    if start < datetime.utcnow() - timedelta(days=366):
        raise HTTPException(400, "That date is too far in the past")
    minutes = int(duration or getattr(profile, "appointment_minutes", None) or 30)
    if not MIN_MIN <= minutes <= MAX_MIN:
        raise HTTPException(400, "Length must be between %d and %d minutes" % (MIN_MIN, MAX_MIN))
    sid = _staff(ctx, staff_id)
    if campaign_id and not ctx.db.query(JewelryCampaign.id).filter(
            JewelryCampaign.id == campaign_id, JewelryCampaign.organization_id == ctx.org_id).first():
        raise HTTPException(404, "Campaign not found")
    _check_overlap(ctx, sid, start, minutes)
    now = datetime.utcnow()
    b = BookingLink(lead_id=lead.id, user_id=sid, status="booked", token=_token(), booked_time=start,
                    appt_label=appt_type, appt_duration=minutes,
                    # The existing reminder and survey jobs must never pick these up.
                    confirmation_sent=True, reminder_24hr_sent=True, reminder_1hr_sent=True,
                    review_request_sent_at=now)
    ctx.db.add(b)
    ctx.db.flush()
    d = JewelryAppointmentDetail(organization_id=ctx.org_id, booking_link_id=b.id, status="scheduled",
                                 internal_notes=(notes or "").strip()[:2000] or None,
                                 campaign_id=campaign_id, created_by=ctx.user.id)
    ctx.db.add(d)
    if follow_up_days:
        t = LeadTask(organization_id=ctx.org_id, lead_id=lead.id,
                     title="Follow up after %s" % appt_type.lower(),
                     due_at=start + timedelta(days=max(1, min(int(follow_up_days), 90))),
                     status="open", assigned_to_id=sid, created_by_id=ctx.user.id, source="appointment")
        ctx.db.add(t)
        ctx.db.flush()
        d.follow_up_task_id = t.id
    if C.custom(lead).get(C.CF_FOLLOW_UP) != "do_not_contact":
        C.set_custom(lead, **{C.CF_FOLLOW_UP: "appointment_set"})
    if not lead.assigned_to_id:
        lead.assigned_to_id = sid
    ctx.db.flush()
    return appt_json(b, d, lead, _names(ctx))


def update(ctx: C.Ctx, appt_id: str, data: Dict[str, Any], profile) -> Dict[str, Any]:
    b, d = _load(ctx, appt_id)
    if "type" in data and data["type"]:
        if data["type"] not in types_for(profile):
            raise HTTPException(400, "Unknown appointment type")
        b.appt_label = data["type"]
    start = parse_local(data["starts_at"]) if data.get("starts_at") else b.booked_time
    minutes = int(data.get("duration_minutes") or b.appt_duration or 30)
    if not MIN_MIN <= minutes <= MAX_MIN:
        raise HTTPException(400, "Length must be between %d and %d minutes" % (MIN_MIN, MAX_MIN))
    sid = _staff(ctx, data["staff_id"]) if data.get("staff_id") else b.user_id
    status = data.get("status") or d.status
    if status not in C.APPOINTMENT_STATUSES:
        raise HTTPException(400, "Unknown appointment status")
    moving = (start != b.booked_time) or (minutes != (b.appt_duration or 30)) or (sid != b.user_id)
    if status in C.OPEN_APPOINTMENT and (moving or d.status not in C.OPEN_APPOINTMENT):
        _check_overlap(ctx, sid, start, minutes, exclude=b.id)
    b.booked_time, b.appt_duration, b.user_id = start, minutes, sid
    d.status = status
    b.status = "cancelled" if status == "cancelled" else ("confirmed" if status in ("confirmed", "checked_in", "completed") else "booked")
    if status == "confirmed" and not b.confirmed_at:
        b.confirmed_at = datetime.utcnow()
    if "internal_notes" in data:
        d.internal_notes = (data["internal_notes"] or "").strip()[:2000] or None
    if status == "completed":
        lead = ctx.db.query(Lead).filter(Lead.id == b.lead_id).first()
        if lead:
            prev = C.custom(lead).get(C.CF_LAST_VISIT)
            day = start.date().isoformat()
            if not prev or day > str(prev):
                C.set_custom(lead, **{C.CF_LAST_VISIT: day})
    ctx.db.flush()
    lead = ctx.db.query(Lead).filter(Lead.id == b.lead_id).first()
    return appt_json(b, d, lead, _names(ctx))


def summary(ctx: C.Ctx, d0: datetime, d1: datetime) -> Dict[str, Any]:
    q = _rows(ctx, ctx.db.query(BookingLink, JewelryAppointmentDetail)) \
        .filter(BookingLink.booked_time >= d0, BookingLink.booked_time < d1)
    if not ctx.manager:
        q = q.filter(BookingLink.user_id == ctx.user.id)
    by_status = {s: 0 for s in C.APPOINTMENT_STATUSES}
    by_type: Dict[str, int] = {}
    for b, d in q.all():
        by_status[d.status] = by_status.get(d.status, 0) + 1
        by_type[b.appt_label or "Other"] = by_type.get(b.appt_label or "Other", 0) + 1
    return {"by_status": by_status, "by_type": by_type, "total": sum(by_status.values())}


def upcoming(ctx: C.Ctx, days: int = 7) -> List[Dict[str, Any]]:
    now = C.local_now(ctx.org)
    rows = list_range(ctx, now.isoformat(timespec="minutes"),
                      (now + timedelta(days=days)).isoformat(timespec="minutes"),
                      include_cancelled=False)
    return [r for r in rows if r["status"] in C.OPEN_APPOINTMENT]
