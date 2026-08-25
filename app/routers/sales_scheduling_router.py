"""
Sales scheduling API — /sales/meeting-types, /sales/availability/*, /sales/appointments/*

Mounted under the same /sales prefix as the workspace and guarded by the same
server-side dependencies. A brand-sales member can only ever see and book within
a brand they hold a membership in.

TENANCY: nothing here reads or writes a customer `organization_id`. A sales
appointment belongs to a brand sales org, an opportunity and its participants.
The customer-side booking surface (booking_links, calendar_router) is a
different system and is not touched.
"""
from datetime import datetime, timedelta, date, time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import User
from app.models.sales_models import (
    BrandSalesOrg, Opportunity, OpportunityEvent,
)
from app.models.scheduling_models import (
    AvailabilityProfile, AvailabilityWindow, AvailabilityBlock,
    MeetingType, SalesAppointment, AppointmentParticipant,
    BLOCK_RECURRING, BLOCK_TIME_OFF, DEFAULT_TIMEZONE,
    APPT_SCHEDULED, APPT_CANCELLED, APPT_COMPLETED, APPT_NO_SHOW,
    APPOINTMENT_STATUSES, BLOCKING_STATUSES,
    CONF_PENDING, CONF_SENT, CONF_CONFIRMED, CONF_DECLINED, CONF_CANCELLED,
    CONF_NO_SHOW, CONFIRMATION_STATUSES, CONFIRMATION_SOURCES,
    CONF_SRC_STAFF_MANUAL, ATTEND_UNKNOWN, SLOT_LABELS,
)
from app.services.sales_access import (
    require_sales_member, require_sales_manager,
    assert_can_view_opportunity, sales_org_ids, is_sales_manager, is_god,
)
from app.services import availability as av
from app.services.meeting_roles import (
    ensure_meeting_types, resolve_meeting_slots, brand_members,
)
from app.models.calendar_models import SYNC_LABELS, SYNC_NEEDS_ATTENTION
from app.services import appointment_sync as apsync
from app.services import appointment_invites as apinvite

router = APIRouter(prefix="/sales", tags=["sales-scheduling"])

MAX_RANGE_DAYS = 60


# ── shared helpers ──────────────────────────────────────────────────────────

def _org(user: User, db: Session, brand_sales_org_id: Optional[str] = None) -> BrandSalesOrg:
    allowed = sales_org_ids(user, db)
    if not allowed:
        raise HTTPException(status_code=403, detail="No active brand sales membership.")
    target = brand_sales_org_id or sorted(allowed)[0]
    if target not in allowed:
        raise HTTPException(status_code=404, detail="Brand sales org not found")
    org = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == target).first()
    if not org:
        raise HTTPException(status_code=404, detail="Brand sales org not found")
    return org


def _assert_same_brand(db: Session, user_ids: List[str], org: BrandSalesOrg) -> List[User]:
    """Everyone on a meeting must belong to the brand running it.

    Without this, a rep could name any user id in the request body and pull a
    stranger — or someone from another brand — into a meeting and onto their
    calendar.
    """
    members = {u.id: u for u in brand_members(db, org.id)}
    out = []
    for uid in user_ids:
        u = members.get(uid)
        if not u:
            raise HTTPException(
                status_code=400,
                detail="A selected participant is not an active member of this "
                       "brand sales organization.")
        out.append(u)
    return out


def _parse_dt(value, field: str) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else \
            value.astimezone(tz=None).replace(tzinfo=None)
    raise HTTPException(status_code=400, detail="%s must be a datetime" % field)


def _profile_out(db: Session, prof: AvailabilityProfile) -> dict:
    windows = (db.query(AvailabilityWindow)
               .filter(AvailabilityWindow.profile_id == prof.id)
               .order_by(AvailabilityWindow.day_of_week.asc(),
                         AvailabilityWindow.start_minute.asc()).all())
    blocks = (db.query(AvailabilityBlock)
              .filter(AvailabilityBlock.profile_id == prof.id).all())
    return {
        "timezone": prof.timezone,
        "buffer_before_minutes": prof.buffer_before_minutes,
        "buffer_after_minutes": prof.buffer_after_minutes,
        "min_notice_minutes": prof.min_notice_minutes,
        "booking_horizon_days": prof.booking_horizon_days,
        "accepts_bookings": prof.accepts_bookings,
        "windows": [{"id": w.id, "day_of_week": w.day_of_week,
                     "start_minute": w.start_minute, "end_minute": w.end_minute}
                    for w in windows],
        "recurring_blocks": [{"id": b.id, "label": b.label, "day_of_week": b.day_of_week,
                              "start_minute": b.start_minute, "end_minute": b.end_minute}
                             for b in blocks if b.kind == BLOCK_RECURRING],
        "time_off": [{"id": b.id, "label": b.label,
                      "starts_at": b.starts_at, "ends_at": b.ends_at}
                     for b in blocks if b.kind == BLOCK_TIME_OFF],
    }


def _appt_out(db: Session, appt: SalesAppointment, viewer: User) -> dict:
    parts = (db.query(AppointmentParticipant, User)
             .join(User, User.id == AppointmentParticipant.user_id)
             .filter(AppointmentParticipant.appointment_id == appt.id).all())
    mt = (db.query(MeetingType).filter(MeetingType.id == appt.meeting_type_id).first()
          if appt.meeting_type_id else None)
    opp = (db.query(Opportunity).filter(Opportunity.id == appt.opportunity_id).first()
           if appt.opportunity_id else None)
    return {
        "id": appt.id,
        "title": appt.title,
        "brand_sales_org_id": appt.brand_sales_org_id,
        "opportunity_id": appt.opportunity_id,
        "opportunity_company": opp.company_name if opp else None,
        "opportunity_stage": opp.stage if opp else None,
        "meeting_type_id": appt.meeting_type_id,
        "meeting_type": mt.name if mt else None,
        "meeting_type_key": mt.key if mt else None,
        "starts_at": appt.starts_at,
        "ends_at": appt.ends_at,
        "timezone": appt.timezone,
        "starts_at_local": av.utc_to_local(appt.starts_at, appt.timezone),
        "ends_at_local": av.utc_to_local(appt.ends_at, appt.timezone),
        "duration_minutes": int((appt.ends_at - appt.starts_at).total_seconds() // 60),
        "status": appt.status,
        "confirmation_status": appt.confirmation_status,
        "confirmation_source": appt.confirmation_source,
        "confirmation_sent_at": appt.confirmation_sent_at,
        "confirmed_at": appt.confirmed_at,
        "prospect": {
            "name": appt.prospect_name, "company": appt.prospect_company,
            "email": appt.prospect_email, "phone": appt.prospect_phone,
            "timezone": appt.prospect_timezone,
        },
        "meeting_provider": appt.meeting_provider,
        "meeting_url": appt.meeting_url,
        "location": appt.location,
        "notes": appt.notes,
        "participants": [{
            "user_id": u.id, "full_name": u.full_name, "email": u.email,
            "role_slot": p.role_slot,
            "role_label": SLOT_LABELS.get(p.role_slot, p.role_slot),
            "is_required": bool(p.is_required),
            "attendance_status": p.attendance_status,
            # Checkpoint 3. Reported honestly rather than omitted: the UI must
            # be able to distinguish "on their Outlook calendar" from "we
            # emailed them an invite" from "we could not reach their calendar".
            "calendar_synced": bool(p.external_event_id),
            "calendar_provider": p.external_calendar_provider,
            "sync_status": p.sync_status,
            "sync_label": SYNC_LABELS.get(p.sync_status, p.sync_status),
            "sync_error": p.sync_error,
            "sync_attempts": p.sync_attempts,
            "sync_last_attempt": p.sync_last_attempt,
            "external_synced_at": p.external_synced_at,
            "ics_sent_at": p.ics_sent_at,
            "needs_attention": p.sync_status in SYNC_NEEDS_ATTENTION,
        } for p, u in parts],
        "viewer_is_participant": any(p.user_id == viewer.id for p, _ in parts),
        # One number the appointment card can render without walking the list.
        "sync_needs_attention": sum(
            1 for p, _ in parts if p.sync_status in SYNC_NEEDS_ATTENTION),
        "prospect_invite_sent_at": appt.prospect_invite_sent_at,
        "prospect_invite_error": appt.prospect_invite_error,
        "rescheduled_count": appt.rescheduled_count or 0,
        "rescheduled_at": appt.rescheduled_at,
        "previous_starts_at": appt.previous_starts_at,
        "reschedule_reason": appt.reschedule_reason,
        "cancelled_at": appt.cancelled_at,
        "cancel_reason": appt.cancel_reason,
        "created_at": appt.created_at,
    }


def _visible_appointments(db: Session, user: User, org: BrandSalesOrg):
    """A rep sees meetings they are on, or that belong to a deal they own.
    A manager sees the whole brand."""
    q = db.query(SalesAppointment).filter(
        SalesAppointment.brand_sales_org_id == org.id)
    if is_sales_manager(user, db, org.id):
        return q
    own_appt_ids = [r[0] for r in db.query(AppointmentParticipant.appointment_id)
                    .filter(AppointmentParticipant.user_id == user.id).all()]
    own_opp_ids = [r[0] for r in db.query(Opportunity.id)
                   .filter(Opportunity.owner_user_id == user.id).all()]
    return q.filter(
        SalesAppointment.id.in_(own_appt_ids or [""])
        | SalesAppointment.opportunity_id.in_(own_opp_ids or [""]))


# ── request models ──────────────────────────────────────────────────────────

class WindowIn(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)
    start_minute: int = Field(..., ge=0, le=1440)
    end_minute: int = Field(..., ge=0, le=1440)


class BlockIn(BaseModel):
    label: Optional[str] = None
    day_of_week: int = Field(..., ge=0, le=6)
    start_minute: int = Field(..., ge=0, le=1440)
    end_minute: int = Field(..., ge=0, le=1440)


class AvailabilityIn(BaseModel):
    timezone: Optional[str] = None
    buffer_before_minutes: Optional[int] = Field(None, ge=0, le=240)
    buffer_after_minutes: Optional[int] = Field(None, ge=0, le=240)
    min_notice_minutes: Optional[int] = Field(None, ge=0, le=20160)
    booking_horizon_days: Optional[int] = Field(None, ge=1, le=365)
    accepts_bookings: Optional[bool] = None
    windows: Optional[List[WindowIn]] = None
    recurring_blocks: Optional[List[BlockIn]] = None


class TimeOffIn(BaseModel):
    label: Optional[str] = None
    starts_at: datetime
    ends_at: datetime


class FindTimeIn(BaseModel):
    meeting_type_id: Optional[str] = None
    duration_minutes: Optional[int] = Field(None, ge=5, le=480)
    required_user_ids: List[str] = []
    optional_user_ids: List[str] = []
    date_from: date
    date_to: Optional[date] = None
    opportunity_id: Optional[str] = None
    brand_sales_org_id: Optional[str] = None
    exclude_appointment_id: Optional[str] = None


class BookIn(BaseModel):
    starts_at: datetime
    meeting_type_id: Optional[str] = None
    duration_minutes: Optional[int] = Field(None, ge=5, le=480)
    opportunity_id: Optional[str] = None
    brand_sales_org_id: Optional[str] = None
    title: Optional[str] = None
    timezone: Optional[str] = None
    required_user_ids: List[str] = []
    optional_user_ids: List[str] = []
    role_slot_by_user: Optional[dict] = None
    meeting_provider: Optional[str] = None
    meeting_url: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    prospect_name: Optional[str] = None
    prospect_email: Optional[str] = None
    prospect_phone: Optional[str] = None
    prospect_timezone: Optional[str] = None


class ConfirmIn(BaseModel):
    confirmation_status: str
    source: Optional[str] = None
    note: Optional[str] = None


class CancelIn(BaseModel):
    reason: Optional[str] = None


# ── meeting types ───────────────────────────────────────────────────────────

@router.get("/meeting-types")
def list_meeting_types(brand_sales_org_id: Optional[str] = Query(None),
                       opportunity_id: Optional[str] = Query(None),
                       user: User = Depends(require_sales_member),
                       db: Session = Depends(get_db)):
    """The brand's meeting types, each with its role slots already resolved to
    real candidates for this opportunity."""
    org = _org(user, db, brand_sales_org_id)
    types = ensure_meeting_types(db, org.id)
    db.commit()

    opp = None
    if opportunity_id:
        opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
        if opp:
            assert_can_view_opportunity(user, opp, db)

    out = []
    for mt in types:
        resolved = resolve_meeting_slots(db, mt, org.id, opp)
        out.append({
            "id": mt.id, "key": mt.key, "name": mt.name,
            "description": mt.description,
            "duration_minutes": mt.duration_minutes,
            "is_internal": bool(mt.is_internal),
            "required_slots": mt.required_slot_list(),
            "optional_slots": mt.optional_slot_list(),
            "resolved": resolved,
        })
    return out


# ── my availability ─────────────────────────────────────────────────────────

@router.get("/availability/me")
def get_my_availability(user: User = Depends(require_sales_member),
                        db: Session = Depends(get_db)):
    prof = av.get_or_create_profile(db, user)
    db.commit()
    return _profile_out(db, prof)


@router.put("/availability/me")
def put_my_availability(body: AvailabilityIn,
                        user: User = Depends(require_sales_member),
                        db: Session = Depends(get_db)):
    """Replace-in-place. `windows` and `recurring_blocks` are whole-collection
    writes: sending them replaces the set, omitting them leaves it alone. A
    partial merge on a weekly schedule is ambiguous and produces duplicates."""
    prof = av.get_or_create_profile(db, user)
    data = body.model_dump(exclude_unset=True)

    if body.timezone:
        if av._zone(body.timezone) is None or str(av._zone(body.timezone)) != body.timezone:
            # Reject an unknown IANA name loudly rather than silently defaulting
            # someone's whole calendar to Central.
            raise HTTPException(status_code=400,
                                detail="Unknown timezone '%s'." % body.timezone)
        prof.timezone = body.timezone

    for f in ("buffer_before_minutes", "buffer_after_minutes",
              "min_notice_minutes", "booking_horizon_days", "accepts_bookings"):
        if f in data and data[f] is not None:
            setattr(prof, f, data[f])

    if body.windows is not None:
        for w in body.windows:
            if w.end_minute <= w.start_minute:
                raise HTTPException(status_code=400,
                                    detail="A working window must end after it starts.")
        db.query(AvailabilityWindow).filter(
            AvailabilityWindow.profile_id == prof.id).delete(synchronize_session=False)
        for w in body.windows:
            db.add(AvailabilityWindow(profile_id=prof.id, day_of_week=w.day_of_week,
                                      start_minute=w.start_minute, end_minute=w.end_minute))

    if body.recurring_blocks is not None:
        for b in body.recurring_blocks:
            if b.end_minute <= b.start_minute:
                raise HTTPException(status_code=400,
                                    detail="A blocked period must end after it starts.")
        db.query(AvailabilityBlock).filter(
            AvailabilityBlock.profile_id == prof.id,
            AvailabilityBlock.kind == BLOCK_RECURRING).delete(synchronize_session=False)
        for b in body.recurring_blocks:
            db.add(AvailabilityBlock(profile_id=prof.id, kind=BLOCK_RECURRING,
                                     label=b.label or "Blocked",
                                     day_of_week=b.day_of_week,
                                     start_minute=b.start_minute, end_minute=b.end_minute))

    db.commit()
    return _profile_out(db, prof)


@router.post("/availability/time-off", status_code=201)
def add_time_off(body: TimeOffIn,
                 user: User = Depends(require_sales_member),
                 db: Session = Depends(get_db)):
    if body.ends_at <= body.starts_at:
        raise HTTPException(status_code=400, detail="Time off must end after it starts.")
    prof = av.get_or_create_profile(db, user)
    b = AvailabilityBlock(profile_id=prof.id, kind=BLOCK_TIME_OFF,
                          label=body.label or "Time off",
                          starts_at=_parse_dt(body.starts_at, "starts_at"),
                          ends_at=_parse_dt(body.ends_at, "ends_at"))
    db.add(b)
    db.commit()
    return {"id": b.id, "label": b.label, "starts_at": b.starts_at, "ends_at": b.ends_at}


@router.delete("/availability/time-off/{block_id}")
def delete_time_off(block_id: str,
                    user: User = Depends(require_sales_member),
                    db: Session = Depends(get_db)):
    prof = av.get_or_create_profile(db, user)
    b = db.query(AvailabilityBlock).filter(
        AvailabilityBlock.id == block_id,
        AvailabilityBlock.profile_id == prof.id,
        AvailabilityBlock.kind == BLOCK_TIME_OFF).first()
    if not b:
        raise HTTPException(status_code=404, detail="Time off not found")
    db.delete(b)
    db.commit()
    return {"deleted": True, "id": block_id}


# ── team availability ───────────────────────────────────────────────────────

@router.get("/availability/team")
def team_availability(day: Optional[date] = Query(None),
                      days: int = Query(1, ge=1, le=14),
                      brand_sales_org_id: Optional[str] = Query(None),
                      user: User = Depends(require_sales_member),
                      db: Session = Depends(get_db)):
    """Per-person free and busy for the grid.

    A rep legitimately needs to SEE colleagues' free/busy to book a meeting —
    that is the whole point — but gets titles only for meetings they are on.
    Someone else's calendar shows as occupied, not as a readable agenda.
    """
    org = _org(user, db, brand_sales_org_id)
    tz = org.timezone or DEFAULT_TIMEZONE
    start_local_day = day or av.utc_to_local(datetime.utcnow(), tz).date()
    start_utc = av.local_to_utc(start_local_day, 0, tz)
    end_utc = av.local_to_utc(start_local_day + timedelta(days=days), 0, tz)

    manager = is_sales_manager(user, db, org.id)
    members = brand_members(db, org.id)
    now = datetime.utcnow()

    out = []
    for m in members:
        prof = av.get_or_create_profile(db, m)
        free = av.free_intervals_for_user(db, m, start_utc, end_utc, now_utc=now,
                                          ignore_notice=True)
        parts = (db.query(AppointmentParticipant, SalesAppointment)
                 .join(SalesAppointment,
                       SalesAppointment.id == AppointmentParticipant.appointment_id)
                 .filter(AppointmentParticipant.user_id == m.id,
                         AppointmentParticipant.is_blocking.is_(True),
                         AppointmentParticipant.busy_start_at < end_utc,
                         AppointmentParticipant.busy_end_at > start_utc).all())
        busy = []
        for p, appt in parts:
            visible = manager or p.user_id == user.id or db.query(
                AppointmentParticipant).filter(
                AppointmentParticipant.appointment_id == appt.id,
                AppointmentParticipant.user_id == user.id).first() is not None
            busy.append({
                "appointment_id": appt.id if visible else None,
                "title": appt.title if visible else "Busy",
                "starts_at": appt.starts_at, "ends_at": appt.ends_at,
                "busy_start_at": p.busy_start_at, "busy_end_at": p.busy_end_at,
                "confirmation_status": appt.confirmation_status if visible else None,
            })
        out.append({
            "user_id": m.id, "full_name": m.full_name, "email": m.email,
            "timezone": prof.timezone,
            "accepts_bookings": prof.accepts_bookings,
            "free": [{"starts_at": s, "ends_at": e} for s, e in free],
            "busy": sorted(busy, key=lambda b: b["starts_at"]),
        })

    db.commit()
    return {
        "brand_sales_org": {"id": org.id, "name": org.name, "timezone": tz},
        "range": {"start_utc": start_utc, "end_utc": end_utc,
                  "start_local_date": start_local_day, "days": days},
        "is_manager": manager,
        "members": out,
    }


# ── the shared time finder ──────────────────────────────────────────────────

@router.post("/availability/find")
def find_team_time(body: FindTimeIn,
                   user: User = Depends(require_sales_member),
                   db: Session = Depends(get_db)):
    """Return ONLY the times every required participant is free.

    This is the intersection, not a union. An optional participant never removes
    a slot — each returned slot reports which optional people happen to be free
    so the salesperson can prefer a fuller room without being denied a viable one.
    """
    org = _org(user, db, body.brand_sales_org_id)
    tz = org.timezone or DEFAULT_TIMEZONE

    opp = None
    if body.opportunity_id:
        opp = db.query(Opportunity).filter(Opportunity.id == body.opportunity_id).first()
        if not opp:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        assert_can_view_opportunity(user, opp, db)
        if opp.brand_sales_org_id != org.id:
            raise HTTPException(status_code=404, detail="Opportunity not found")

    duration = body.duration_minutes
    mt = None
    if body.meeting_type_id:
        mt = db.query(MeetingType).filter(MeetingType.id == body.meeting_type_id).first()
        if not mt or mt.brand_sales_org_id != org.id:
            raise HTTPException(status_code=404, detail="Meeting type not found")
        duration = duration or mt.duration_minutes
    if not duration:
        raise HTTPException(status_code=400,
                            detail="Provide a meeting type or an explicit duration.")

    required_ids = list(body.required_user_ids)
    optional_ids = [u for u in body.optional_user_ids if u not in required_ids]

    # No explicit selection: resolve the meeting type's role slots. Any slot with
    # exactly one candidate fills itself; an ambiguous slot is reported rather
    # than guessed.
    unresolved = []
    if not required_ids and mt:
        resolved = resolve_meeting_slots(db, mt, org.id, opp)
        for s in resolved["required"]:
            if s["auto_selected_user_id"]:
                required_ids.append(s["auto_selected_user_id"])
            elif s["candidates"]:
                unresolved.append(s["label"])
            else:
                unresolved.append(s["label"])
        for s in resolved["optional"]:
            if s["auto_selected_user_id"] and s["auto_selected_user_id"] not in required_ids:
                optional_ids.append(s["auto_selected_user_id"])

    if not required_ids:
        return {"slots": [], "timezone": tz, "duration_minutes": duration,
                "required": [], "optional": [],
                "blockers": ["Select at least one required participant."
                             + (" Ambiguous roles: " + ", ".join(unresolved) if unresolved else "")]}

    required = _assert_same_brand(db, required_ids, org)
    optional = _assert_same_brand(db, optional_ids, org) if optional_ids else []

    d_from = body.date_from
    d_to = body.date_to or d_from
    if d_to < d_from:
        raise HTTPException(status_code=400, detail="date_to is before date_from.")
    if (d_to - d_from).days > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400,
                            detail="Search at most %d days at a time." % MAX_RANGE_DAYS)

    start_utc = av.local_to_utc(d_from, 0, tz)
    end_utc = av.local_to_utc(d_to + timedelta(days=1), 0, tz)

    result = av.find_shared_slots(
        db, required, optional, start_utc, end_utc, duration,
        exclude_appointment_id=body.exclude_appointment_id)
    db.commit()

    names = {u.id: u.full_name for u in list(required) + list(optional)}
    return {
        "timezone": tz,
        "duration_minutes": duration,
        "meeting_type": {"id": mt.id, "name": mt.name} if mt else None,
        "required": [{"user_id": u.id, "full_name": u.full_name} for u in required],
        "optional": [{"user_id": u.id, "full_name": u.full_name} for u in optional],
        "unresolved_roles": unresolved,
        "blockers": result["blockers"],
        "slots": [{
            "starts_at": s["starts_at"],
            "ends_at": s["ends_at"],
            "starts_at_local": av.utc_to_local(s["starts_at"], tz),
            "optional_available": [{"user_id": uid, "full_name": names.get(uid)}
                                   for uid in s["optional_available_user_ids"]],
            "optional_available_count": s["optional_available_count"],
        } for s in result["slots"]],
        "total": len(result["slots"]),
    }


# ── appointments ────────────────────────────────────────────────────────────

@router.post("/appointments", status_code=201)
def create_appointment(body: BookIn,
                       user: User = Depends(require_sales_member),
                       db: Session = Depends(get_db)):
    """Book it.

    DOUBLE-BOOKING: `find_conflicts` runs inside this transaction and refuses a
    clash with 409. On Postgres the participant exclusion constraint added in
    auto_migrate.py catches the genuine concurrent race that the check cannot
    see — two requests that both pass the check before either commits. The
    IntegrityError from that constraint is caught below and turned into the same
    clean 409, so one booking wins and the other fails honestly.
    """
    org = _org(user, db, body.brand_sales_org_id)
    tz_org = org.timezone or DEFAULT_TIMEZONE

    opp = None
    if body.opportunity_id:
        opp = db.query(Opportunity).filter(Opportunity.id == body.opportunity_id).first()
        if not opp:
            raise HTTPException(status_code=404, detail="Opportunity not found")
        assert_can_view_opportunity(user, opp, db)
        if opp.brand_sales_org_id != org.id:
            raise HTTPException(status_code=404, detail="Opportunity not found")

    mt = None
    duration = body.duration_minutes
    if body.meeting_type_id:
        mt = db.query(MeetingType).filter(MeetingType.id == body.meeting_type_id).first()
        if not mt or mt.brand_sales_org_id != org.id:
            raise HTTPException(status_code=404, detail="Meeting type not found")
        duration = duration or mt.duration_minutes
    if not duration:
        raise HTTPException(status_code=400,
                            detail="Provide a meeting type or an explicit duration.")

    starts_at = _parse_dt(body.starts_at, "starts_at")
    ends_at = starts_at + timedelta(minutes=duration)

    required_ids = list(body.required_user_ids)
    optional_ids = [u for u in body.optional_user_ids if u not in required_ids]
    if not required_ids:
        raise HTTPException(status_code=400,
                            detail="At least one required participant is needed.")
    required = _assert_same_brand(db, required_ids, org)
    optional = _assert_same_brand(db, optional_ids, org) if optional_ids else []
    everyone = required + optional

    # Refuse a booking in the past outright — it can only be a client clock bug
    # or a stale slot list, and either way it produces a meeting nobody attends.
    if ends_at <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="That time is already in the past.")

    conflicts = av.find_conflicts(db, [u.id for u in everyone], starts_at, ends_at)
    if conflicts:
        names = sorted({c["user_name"] for c in conflicts})
        raise HTTPException(
            status_code=409,
            detail="Already booked at that time: %s. Pick another opening." % ", ".join(names))

    title = (body.title or "").strip()
    if not title:
        base = mt.name if mt else "Sales meeting"
        title = "%s · %s" % (base, opp.company_name) if opp else base

    appt = SalesAppointment(
        brand_sales_org_id=org.id,
        opportunity_id=opp.id if opp else None,
        meeting_type_id=mt.id if mt else None,
        title=title,
        starts_at=starts_at, ends_at=ends_at,
        timezone=body.timezone or tz_org,
        status=APPT_SCHEDULED,
        # Carried forward from the opportunity so nobody retypes what the system
        # already knows; the explicit body values win when supplied.
        prospect_name=body.prospect_name or (opp.contact_name if opp else None),
        prospect_company=(opp.company_name if opp else None),
        prospect_email=body.prospect_email or (opp.email if opp else None),
        prospect_phone=body.prospect_phone or (opp.phone if opp else None),
        prospect_timezone=body.prospect_timezone or (opp.timezone if opp else None),
        confirmation_status=CONF_PENDING,
        meeting_provider=body.meeting_provider,
        meeting_url=body.meeting_url,
        location=body.location,
        notes=body.notes,
        created_by=user.id,
    )
    db.add(appt)
    db.flush()

    slot_by_user = body.role_slot_by_user or {}
    for u in everyone:
        prof = av.get_or_create_profile(db, u)
        bs, be = av.buffered_window(prof, starts_at, ends_at)
        db.add(AppointmentParticipant(
            appointment_id=appt.id, user_id=u.id,
            role_slot=slot_by_user.get(u.id),
            is_required=u in required,
            attendance_status=ATTEND_UNKNOWN,
            busy_start_at=bs, busy_end_at=be, is_blocking=True))

    if opp:
        db.add(OpportunityEvent(
            opportunity_id=opp.id, event_type="appointment_booked",
            summary="%s booked" % (mt.name if mt else "Meeting"),
            detail="%s · %s" % (
                av.utc_to_local(starts_at, appt.timezone).strftime("%b %d, %Y %I:%M %p"),
                ", ".join(u.full_name for u in everyone)),
            actor_user_id=user.id))

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        # The Postgres exclusion constraint fired: another request booked one of
        # these people between our check and our commit. One wins, this one
        # fails cleanly — which is exactly the required behaviour.
        if "sales_participant_no_overlap" in str(e).lower() or "exclusion" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail="Someone booked one of these participants moments ago. "
                       "Refresh the openings and pick another time.")
        raise
    db.refresh(appt)

    # ── Calendar sync + prospect invitation ─────────────────────────────────
    # AFTER the commit, deliberately. The meeting is booked, saved and blocking
    # everyone's time before a single vendor is contacted. Nothing below can
    # un-book it: both calls swallow their own failures and record them on the
    # rows, so a Microsoft outage produces a meeting flagged "needs attention",
    # never a lost booking or a 500 to the person who just booked it.
    _push_appointment(db, appt, user, kind="invite")

    db.refresh(appt)
    return _appt_out(db, appt, user)


def _push_appointment(db: Session, appt: SalesAppointment, user: User,
                      kind: str = "invite") -> dict:
    """Sync the internal team's calendars and email the prospect.

    One place, so booking and rescheduling cannot drift apart. Never raises —
    every failure mode inside is already recorded on the appointment or the
    participant rows, which is what the UI reads.
    """
    sync_report, invite_report = None, None
    try:
        sync_report = apsync.sync_appointment(db, appt, organizer=user)
    except Exception:
        # The orchestrator is not supposed to raise. If it ever does, the
        # booking still stands and the sales workspace still shows the meeting.
        import logging
        logging.getLogger(__name__).exception(
            "appointment sync raised for %s", appt.id)
    try:
        invite_report = apinvite.send_prospect_invitation(db, appt, kind=kind)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "prospect invitation raised for %s", appt.id)
    return {"sync": sync_report, "invite": invite_report}


@router.get("/appointments")
def list_appointments(brand_sales_org_id: Optional[str] = Query(None),
                      date_from: Optional[date] = Query(None),
                      date_to: Optional[date] = Query(None),
                      scope: str = Query("mine"),
                      include_cancelled: bool = Query(False),
                      user: User = Depends(require_sales_member),
                      db: Session = Depends(get_db)):
    org = _org(user, db, brand_sales_org_id)
    tz = org.timezone or DEFAULT_TIMEZONE

    if scope == "team":
        if not is_sales_manager(user, db, org.id):
            raise HTTPException(status_code=403,
                                detail="Only a sales manager can view the team schedule.")
        q = db.query(SalesAppointment).filter(
            SalesAppointment.brand_sales_org_id == org.id)
    else:
        q = _visible_appointments(db, user, org)

    if date_from:
        q = q.filter(SalesAppointment.starts_at >= av.local_to_utc(date_from, 0, tz))
    if date_to:
        q = q.filter(SalesAppointment.starts_at
                     < av.local_to_utc(date_to + timedelta(days=1), 0, tz))
    if not include_cancelled:
        q = q.filter(SalesAppointment.status != APPT_CANCELLED)

    rows = q.order_by(SalesAppointment.starts_at.asc()).limit(500).all()
    return {"brand_sales_org": {"id": org.id, "name": org.name, "timezone": tz},
            "scope": scope,
            "is_manager": is_sales_manager(user, db, org.id),
            "appointments": [_appt_out(db, a, user) for a in rows],
            "total": len(rows)}


def _load_appt(db: Session, appt_id: str, user: User) -> SalesAppointment:
    appt = db.query(SalesAppointment).filter(SalesAppointment.id == appt_id).first()
    if not appt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if appt.brand_sales_org_id not in sales_org_ids(user, db):
        # 404, not 403 — do not confirm that another brand's id exists.
        raise HTTPException(status_code=404, detail="Appointment not found")
    if not is_sales_manager(user, db, appt.brand_sales_org_id):
        on_it = db.query(AppointmentParticipant).filter(
            AppointmentParticipant.appointment_id == appt.id,
            AppointmentParticipant.user_id == user.id).first()
        owns_deal = False
        if appt.opportunity_id:
            opp = db.query(Opportunity).filter(
                Opportunity.id == appt.opportunity_id).first()
            owns_deal = bool(opp and opp.owner_user_id == user.id)
        if not on_it and not owns_deal:
            raise HTTPException(status_code=403,
                                detail="This meeting belongs to another representative.")
    return appt


# ── PUBLIC prospect confirmation ────────────────────────────────────────────
#
# DECLARED BEFORE `/appointments/{appt_id}`. The three-segment path would not
# actually be captured by the two-segment one, but route order in this file is
# the only thing protecting that, and a future `/appointments/confirm` (no
# token) would silently resolve as appt_id="confirm". Keeping these first makes
# the protection independent of anyone noticing the segment count.
#
# NO AUTHENTICATION. A prospect has no account and must never be asked for one.
# The token IS the authorisation, which is why it is CSPRNG-generated, scoped to
# a single appointment, expiring, and revocable.

_CONFIRM_PAGE_CSS = (
    "body{font-family:-apple-system,Segoe UI,Arial,sans-serif;background:#f8fafc;"
    "margin:0;padding:40px 16px;color:#111827}"
    ".card{max-width:520px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;"
    "border-radius:12px;padding:28px}"
    "h1{font-size:20px;margin:0 0 6px}.muted{color:#6b7280;font-size:14px}"
    ".when{font-size:17px;font-weight:600;margin:18px 0}"
    "button{font:inherit;font-weight:600;padding:12px 20px;border-radius:8px;"
    "border:1px solid transparent;cursor:pointer;margin-right:10px}"
    ".yes{background:#1d4ed8;color:#fff}.no{background:#fff;border-color:#d1d5db;color:#374151}"
)


def _confirm_page(title: str, body_html: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>%s</title><style>%s</style></head>"
        "<body><div class='card'>%s</div></body></html>" % (title, _CONFIRM_PAGE_CSS, body_html)
    )


@router.get("/appointments/confirm/{token}", include_in_schema=False)
def prospect_confirm_page(token: str, db: Session = Depends(get_db)):
    """Render the confirmation page. CHANGES NOTHING.

    This is a GET and it must stay side-effect free. Corporate mail scanners
    (Safe Links, Proofpoint, Mimecast) fetch every link in an inbound message.
    If confirming happened here, a security appliance would auto-confirm a large
    share of invitations within seconds of delivery and the prospect's real
    answer would never be recorded — while the salesperson saw a confirmation
    nobody made.
    """
    from html import escape as _esc
    row, appt, err = apinvite.resolve_token(db, token)
    if err:
        return _confirm_page("Meeting", "<h1>%s</h1><p class='muted'>Please contact "
                                        "whoever arranged this meeting.</p>" % _esc(err))

    ident = apinvite.brand_identity(db, appt)
    when = _esc(apinvite._local_when(appt))
    who = _esc(ident.get("name") or "us")

    if appt.status == APPT_CANCELLED:
        return _confirm_page("Meeting cancelled",
                             "<h1>This meeting has been cancelled</h1>"
                             "<p class='muted'>No action is needed.</p>")

    already = ""
    if appt.confirmation_status == CONF_CONFIRMED:
        already = "<p class='muted'>You have already confirmed. You can change your answer below.</p>"
    elif appt.confirmation_status == CONF_DECLINED:
        already = "<p class='muted'>You previously declined. You can change your answer below.</p>"

    phone = ident.get("support_phone")
    contact = ("<p class='muted'>Need a different time? Call %s.</p>" % _esc(phone)) if phone else ""

    body = (
        "<h1>%s</h1><p class='muted'>with %s</p>"
        "<p class='when'>%s</p>%s"
        "<form method='post' action='/sales/appointments/confirm/%s'>"
        "<button class='yes' name='action' value='confirm' type='submit'>Yes, I'll be there</button>"
        "<button class='no' name='action' value='decline' type='submit'>I can't make it</button>"
        "</form>%s"
    ) % (_esc(appt.title or "Your meeting"), who, when, already, _esc(token), contact)
    return _confirm_page("Confirm your meeting", body)


@router.post("/appointments/confirm/{token}", include_in_schema=False)
async def prospect_confirm_submit(token: str, request: Request,
                                  db: Session = Depends(get_db)):
    """Record the prospect's answer. The only endpoint here that changes state.

    Accepts a form POST from the page above. Still unauthenticated — the token
    is the authorisation — but a POST is not prefetched by link scanners, which
    is the whole reason the action lives here and not on the GET.
    """
    from html import escape as _esc
    row, appt, err = apinvite.resolve_token(db, token)
    if err:
        return _confirm_page("Meeting", "<h1>%s</h1>" % _esc(err))

    action = ""
    try:
        form = await request.form()
        action = str(form.get("action") or "")
    except Exception:
        action = ""

    if appt.status == APPT_CANCELLED:
        return _confirm_page("Meeting cancelled",
                             "<h1>This meeting has been cancelled</h1>"
                             "<p class='muted'>No action is needed.</p>")

    client_ip = request.client.host if request.client else None
    result = apinvite.redeem_token(db, row, appt, action, ip=client_ip)
    if not result.get("ok"):
        return _confirm_page("Meeting", "<h1>Something went wrong</h1>"
                                        "<p class='muted'>Please try the link again.</p>")

    if appt.opportunity_id:
        # The prospect is not a user, so `actor_user_id` stays NULL. Recording a
        # staff member here would misattribute the action.
        db.add(OpportunityEvent(
            opportunity_id=appt.opportunity_id, event_type="confirmation",
            summary="Prospect %sed the meeting" % result["action"],
            detail="via confirmation link", actor_user_id=None))
    db.commit()

    ident = apinvite.brand_identity(db, appt)
    phone = ident.get("support_phone")
    tail = ("<p class='muted'>Need to change something? Call %s.</p>"
            % _esc(phone)) if phone else ""
    if result["action"] == "confirm":
        return _confirm_page("Confirmed",
                             "<h1>You're confirmed</h1><p class='when'>%s</p>%s"
                             % (_esc(apinvite._local_when(appt)), tail))
    return _confirm_page("Thanks for letting us know",
                         "<h1>Thanks for letting us know</h1>"
                         "<p class='muted'>We've told the team you can't make it.</p>%s" % tail)


@router.get("/appointments/{appt_id}")
def get_appointment(appt_id: str,
                    user: User = Depends(require_sales_member),
                    db: Session = Depends(get_db)):
    return _appt_out(db, _load_appt(db, appt_id, user), user)


@router.post("/appointments/{appt_id}/confirmation")
def set_confirmation(appt_id: str, body: ConfirmIn,
                     user: User = Depends(require_sales_member),
                     db: Session = Depends(get_db)):
    """One confirmation model, several possible sources.

    A staff member marking confirmed and a prospect clicking a link are both
    valid, and the difference is recorded rather than flattened — "confirmed"
    means something weaker when a rep ticked it from memory.

    Nothing is sent from here. Email is the default channel when sending is
    built; a cold prospect is never auto-SMSed.
    """
    appt = _load_appt(db, appt_id, user)
    if body.confirmation_status not in CONFIRMATION_STATUSES:
        raise HTTPException(status_code=400,
                            detail="Unknown confirmation status '%s'." % body.confirmation_status)
    src = body.source or CONF_SRC_STAFF_MANUAL
    if src not in CONFIRMATION_SOURCES:
        raise HTTPException(status_code=400, detail="Unknown confirmation source '%s'." % src)

    appt.confirmation_status = body.confirmation_status
    appt.confirmation_source = src
    now = datetime.utcnow()
    if body.confirmation_status == CONF_SENT:
        appt.confirmation_sent_at = now
    if body.confirmation_status == CONF_CONFIRMED:
        appt.confirmed_at = now
        appt.confirmed_by = user.id
    if body.confirmation_status == CONF_NO_SHOW:
        appt.status = APPT_NO_SHOW

    if appt.opportunity_id:
        db.add(OpportunityEvent(
            opportunity_id=appt.opportunity_id, event_type="confirmation",
            summary="Meeting %s" % body.confirmation_status,
            detail=body.note or ("via %s" % src), actor_user_id=user.id))
    db.commit()
    db.refresh(appt)
    return _appt_out(db, appt, user)


@router.post("/appointments/{appt_id}/cancel")
def cancel_appointment(appt_id: str, body: CancelIn,
                       user: User = Depends(require_sales_member),
                       db: Session = Depends(get_db)):
    """Cancelling frees everybody's time without deleting the history.

    Flipping `is_blocking` off on the participant rows is what releases the slot
    — both for the availability engine and for the exclusion constraint, whose
    predicate is `WHERE (is_blocking)`.
    """
    appt = _load_appt(db, appt_id, user)
    if appt.status == APPT_CANCELLED:
        return _appt_out(db, appt, user)
    appt.status = APPT_CANCELLED
    appt.confirmation_status = CONF_CANCELLED
    appt.cancelled_at = datetime.utcnow()
    appt.cancel_reason = (body.reason or "").strip() or None
    db.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == appt.id).update(
        {"is_blocking": False}, synchronize_session=False)
    if appt.opportunity_id:
        db.add(OpportunityEvent(
            opportunity_id=appt.opportunity_id, event_type="appointment_cancelled",
            summary="Meeting cancelled", detail=appt.cancel_reason,
            actor_user_id=user.id))
    db.commit()

    # ── Propagate the cancellation outward ──────────────────────────────────
    # After the commit, same reasoning as booking. A cancellation that only
    # changes our own row is the worst of the three outcomes: everyone still
    # holds the meeting, nobody knows it is off, and somebody dials in. Both
    # calls record their own failures rather than raising.
    try:
        apsync.cancel_appointment_sync(db, appt, organizer=user)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "cancel sync raised for %s", appt.id)
    try:
        apinvite.send_prospect_invitation(db, appt, kind="cancel")
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "cancel notice raised for %s", appt.id)

    db.refresh(appt)
    return _appt_out(db, appt, user)


class RescheduleIn(BaseModel):
    # datetime, matching BookIn — `_parse_dt` accepts only a real datetime and
    # rejects a bare string, so declaring this as `str` would have made every
    # reschedule fail with "starts_at must be a datetime".
    starts_at: datetime
    duration_minutes: Optional[int] = Field(None, ge=5, le=480)
    reason: Optional[str] = None
    notify: bool = True


@router.post("/appointments/{appt_id}/reschedule")
def reschedule_appointment(appt_id: str, body: RescheduleIn,
                           user: User = Depends(require_sales_member),
                           db: Session = Depends(get_db)):
    """Move a meeting. MOVES the row; never cancel-and-recreate.

    Recreating would mint a new appointment id, which would orphan the
    prospect's confirmation link, break every stored provider event id into a
    duplicate, and split the opportunity timeline into two unrelated halves.
    Moving keeps all three intact, which is why the provider layer's
    update-in-place path exists at all.
    """
    appt = _load_appt(db, appt_id, user)
    if appt.status == APPT_CANCELLED:
        raise HTTPException(status_code=400,
                            detail="This meeting is cancelled. Book a new one instead.")

    starts_at = _parse_dt(body.starts_at, "starts_at")
    duration = body.duration_minutes or int(
        (appt.ends_at - appt.starts_at).total_seconds() // 60)
    if duration <= 0:
        raise HTTPException(status_code=400, detail="Duration must be positive.")
    ends_at = starts_at + timedelta(minutes=duration)

    if ends_at <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="That time is already in the past.")
    if starts_at == appt.starts_at and ends_at == appt.ends_at:
        return _appt_out(db, appt, user)

    parts = (db.query(AppointmentParticipant)
             .filter(AppointmentParticipant.appointment_id == appt.id).all())
    user_ids = [p.user_id for p in parts]

    # The meeting must not be treated as blocking ITSELF at its new time.
    conflicts = av.find_conflicts(db, user_ids, starts_at, ends_at,
                                  exclude_appointment_id=appt.id)
    if conflicts:
        names = sorted({c["user_name"] for c in conflicts})
        raise HTTPException(
            status_code=409,
            detail="Already booked at that time: %s. Pick another opening." % ", ".join(names))

    previous = appt.starts_at
    appt.previous_starts_at = previous
    appt.starts_at = starts_at
    appt.ends_at = ends_at
    appt.rescheduled_count = (appt.rescheduled_count or 0) + 1
    appt.rescheduled_at = datetime.utcnow()
    appt.reschedule_reason = (body.reason or "").strip() or None
    # A moved meeting is not a confirmed meeting. The prospect agreed to a time
    # that no longer exists, so carrying the old confirmation forward would show
    # the rep a "confirmed" meeting nobody has actually agreed to.
    appt.confirmation_status = CONF_PENDING
    appt.confirmed_at = None
    appt.confirmed_by = None

    for p in parts:
        u = db.query(User).filter(User.id == p.user_id).first()
        if u is None:
            continue
        prof = av.get_or_create_profile(db, u)
        bs, be = av.buffered_window(prof, starts_at, ends_at)
        p.busy_start_at, p.busy_end_at = bs, be

    if appt.opportunity_id:
        db.add(OpportunityEvent(
            opportunity_id=appt.opportunity_id, event_type="appointment_rescheduled",
            summary="Meeting moved",
            detail="%s → %s" % (
                av.utc_to_local(previous, appt.timezone).strftime("%b %d, %Y %I:%M %p"),
                av.utc_to_local(starts_at, appt.timezone).strftime("%b %d, %Y %I:%M %p")),
            actor_user_id=user.id))

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        if "sales_participant_no_overlap" in str(e).lower() or "exclusion" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail="Someone booked one of these participants moments ago. "
                       "Refresh the openings and pick another time.")
        raise
    db.refresh(appt)

    if body.notify:
        _push_appointment(db, appt, user, kind="reschedule")
        db.refresh(appt)
    return _appt_out(db, appt, user)


@router.post("/appointments/{appt_id}/resync")
def resync_appointment_sync(appt_id: str,
                            user_id: Optional[str] = Query(None),
                            user: User = Depends(require_sales_member),
                            db: Session = Depends(get_db)):
    """Manual retry for participants whose calendar sync needs attention.

    Exists because a sync failure is invisible by nature — the meeting looks
    fine in AdvisorFlow and nobody finds out until someone does not show up.
    This is the button that closes that gap.
    """
    appt = _load_appt(db, appt_id, user)
    report = apsync.retry_failed_sync(db, appt, organizer=user, user_id=user_id)
    db.refresh(appt)
    out = _appt_out(db, appt, user)
    out["sync_report"] = report
    return out


@router.post("/appointments/{appt_id}/resend-invitation")
def resend_prospect_invitation(appt_id: str,
                               user: User = Depends(require_sales_member),
                               db: Session = Depends(get_db)):
    """Re-send the prospect's invitation. Reuses the SAME confirmation token, so
    a link the prospect already has keeps working."""
    appt = _load_appt(db, appt_id, user)
    if not appt.prospect_email:
        raise HTTPException(status_code=400,
                            detail="This meeting has no prospect email address.")
    report = apinvite.send_prospect_invitation(db, appt, kind="invite")
    db.refresh(appt)
    out = _appt_out(db, appt, user)
    out["invite_report"] = report
    return out
