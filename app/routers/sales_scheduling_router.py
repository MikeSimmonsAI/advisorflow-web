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
from app.limiter import limiter
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
    assert_can_view_opportunity, assert_can_edit_opportunity,
    sales_org_ids, is_sales_manager, is_god,
)
from app.services import availability as av
from app.services.meeting_roles import (
    ensure_meeting_types, resolve_meeting_slots, brand_members,
)
from app.models.calendar_models import (
    SYNC_LABELS, SYNC_NEEDS_ATTENTION, CalendarConnection,
    PROVIDER_LABELS, PROVIDER_MICROSOFT, PROVIDER_GOOGLE,
    CONFLICT_LABELS, CONFLICT_KINDS,
)
from app.services import appointment_sync as apsync
from app.services import appointment_invites as apinvite
from app.services import appointment_meetings as apmeet
from app.services import external_busy as extbusy
from app.services import appointment_outcome as apoutcome
from app.services import appointment_reconcile as apreconcile
from app.services.meeting_providers import get_provider, PROVIDER_ZOOM

router = APIRouter(prefix="/sales", tags=["sales-scheduling"])

MAX_RANGE_DAYS = 60

# How wide a window the calendar view will assemble in one request. Larger than
# MAX_RANGE_DAYS because a month view legitimately needs six weeks of grid, and
# smaller than a year because every day added multiplies the external-busy
# refresh cost per team member.
MAX_VIEW_DAYS = 62


def _refresh_external(db: Session, users, start_utc: datetime, end_utc: datetime,
                      org=None, force: bool = False) -> dict:
    """Re-read the team's external calendars, then report what we could see.

    WHY EVERY AVAILABILITY ANSWER GOES THROUGH HERE. The availability engine
    reads the external-busy CACHE and never calls a provider — that split is
    what keeps a four-person search from becoming four vendor round trips. The
    cost of the split is that an unrefreshed cache does not make the engine
    slow, it makes it wrong: it reports somebody free during a meeting it has
    not heard about. `refresh_many` existed for exactly this and was never
    called from the sales surfaces, so until now Team Availability and Find
    Team Time were answering from whatever the cache happened to hold.

    Never fatal. A provider outage returns a report saying so and the search
    still runs — the answer is just honestly labelled as partial rather than
    silently presented as complete.
    """
    try:
        report = extbusy.refresh_many(db, users, start_utc, end_utc,
                                      org=org, force=force)
        db.flush()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("external busy refresh failed")
        report = {}
    return extbusy.visibility_from_report(report)


def _external_visibility_summary(vis: dict) -> dict:
    """One sentence a screen can render about how complete the answer is."""
    total = len(vis or {})
    checked = len([v for v in (vis or {}).values() if v.get("external_checked")])
    not_connected = len([v for v in (vis or {}).values()
                         if v.get("state") == "not_connected"])
    degraded = total - checked - not_connected
    if total and checked == total:
        note = None
    elif degraded:
        note = ("%d of %d connected calendars could not be read, so some outside "
                "conflicts may be missing." % (degraded, total))
    elif not_connected:
        note = ("%d of %d people have no connected calendar, so conflicts outside "
                "EvoSys Pro cannot be checked for them." % (not_connected, total))
    else:
        note = None
    return {
        "people": total,
        "external_checked": checked,
        "not_connected": not_connected,
        "degraded": degraded,
        # True only when every person's outside calendar was actually read.
        "complete": bool(total) and checked == total,
        "note": note,
    }


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
        # The legend colours by TYPE, so the type's own internal flag has to
        # reach the client. Deriving "internal" from the title in the browser
        # would repaint the calendar the first time a rep typed the word into a
        # demo's name.
        "meeting_type_internal": bool(mt.is_internal) if mt else False,
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
        # Checkpoint 4. `meeting_out` has no field for the host URL, so no edit
        # to this serializer can leak one — the host link is fetched separately
        # by a participant-gated endpoint.
        "video": apmeet.meeting_out(apmeet.get_meeting_row(db, appt.id)),
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
            # Drift, reported separately from sync failure because they are
            # different problems with different fixes: a failed sync means we
            # could not write, a conflict means somebody else did.
            "sync_conflict": bool(p.sync_conflict),
            "sync_conflict_kind": p.sync_conflict_kind,
            "sync_conflict_label": (CONFLICT_LABELS.get(p.sync_conflict_kind)
                                    if p.sync_conflict else None),
            "sync_conflict_detail": p.sync_conflict_detail if p.sync_conflict else None,
            "sync_conflict_at": p.sync_conflict_at if p.sync_conflict else None,
        } for p, u in parts],
        "viewer_is_participant": any(p.user_id == viewer.id for p, _ in parts),
        # One number the appointment card can render without walking the list.
        "sync_needs_attention": sum(
            1 for p, _ in parts if p.sync_status in SYNC_NEEDS_ATTENTION),
        "sync_conflicts": sum(1 for p, _ in parts if p.sync_conflict),
        # THE OUTCOME BLOCK. Embedded on every appointment so the calendar, the
        # mobile view and the manager queue all read the same `needs_outcome`
        # answer instead of each computing its own from timestamps.
        "outcome_state": apoutcome.outcome_out(appt),
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


def _member_layers(db: Session, member: User, prof: AvailabilityProfile,
                   start_utc: datetime, end_utc: datetime) -> dict:
    """Everything that shapes a person's day other than their appointments.

    Four bands, each rendered differently in the grid because each means
    something different to somebody trying to book:

      working       — their configured hours. Outside them is not "free at 3am",
                      it is off duty, and the difference is the whole reason
                      the grid has a shape at all.
      blocked       — recurring carve-outs (lunch, a standing internal slot).
      time_off      — dated absence. PTO reads differently from lunch: one is
                      "try after 1pm", the other is "try next week".
      external_busy — a commitment on a connected outside calendar. INTERVAL
                      ONLY. The cache never stored a subject, so there is
                      nothing here to leak even by accident.

    Expanded through the same `local_to_utc` the engine uses, per local date,
    so a window resolves correctly across a DST boundary instead of sliding an
    hour twice a year.
    """
    tz = prof.timezone or DEFAULT_TIMEZONE
    days = av.local_dates_covering(start_utc, end_utc, tz)

    windows = (db.query(AvailabilityWindow)
               .filter(AvailabilityWindow.profile_id == prof.id).all())
    by_dow = {}
    for w in windows:
        by_dow.setdefault(w.day_of_week, []).append(w)

    working = []
    for d in days:
        for w in by_dow.get(d.weekday(), []):
            s = av.local_to_utc(d, w.start_minute, tz)
            e = av.local_to_utc(d, w.end_minute, tz)
            if e > start_utc and s < end_utc:
                working.append({"starts_at": s, "ends_at": e})

    blocks = (db.query(AvailabilityBlock)
              .filter(AvailabilityBlock.profile_id == prof.id).all())
    blocked, time_off = [], []
    rec_by_dow = {}
    for b in blocks:
        if b.kind == BLOCK_RECURRING and b.day_of_week is not None:
            rec_by_dow.setdefault(b.day_of_week, []).append(b)
        elif b.kind == BLOCK_TIME_OFF and b.starts_at and b.ends_at:
            if b.ends_at > start_utc and b.starts_at < end_utc:
                time_off.append({"label": b.label or "Time off",
                                 "starts_at": b.starts_at, "ends_at": b.ends_at})
    for d in days:
        for b in rec_by_dow.get(d.weekday(), []):
            s = av.local_to_utc(d, b.start_minute, tz)
            e = av.local_to_utc(d, b.end_minute, tz)
            if e > start_utc and s < end_utc:
                blocked.append({"label": b.label or "Blocked",
                                "starts_at": s, "ends_at": e})

    ext = extbusy.external_busy_intervals(db, member.id, start_utc, end_utc)
    external_busy = [{"starts_at": s, "ends_at": e,
                      # Spelled out here so no screen has to decide what to
                      # call it, and so the only available wording is the
                      # privacy-safe one.
                      "label": "Busy — external calendar"}
                     for s, e in ext]

    return {"working": sorted(working, key=lambda x: x["starts_at"]),
            "blocked": sorted(blocked, key=lambda x: x["starts_at"]),
            "time_off": sorted(time_off, key=lambda x: x["starts_at"]),
            "external_busy": sorted(external_busy, key=lambda x: x["starts_at"])}


def _sync_status_for(db: Session, members, viewer: User,
                     is_manager: bool = False) -> dict:
    """Provider health, in words that tell somebody what to do.

    THE BRIEF'S RULE: no meaningless green dots. So each provider reports a
    STATE with a reason and an action, and the states are genuinely different
    from one another — `not_connected` is not a failure, `reauth_required` is
    the user's to fix, `degraded` is ours to retry, and `connected` carries the
    time it was last actually read rather than the time somebody first
    authorised it.

    A rep sees only their OWN connections. A manager additionally gets a team
    roll-up of counts — never another person's mailbox address, which is
    account detail they have no need for to staff a week.
    """
    def _one(u):
        rows = (db.query(CalendarConnection)
                .filter(CalendarConnection.user_id == u.id).all())
        by_provider = {r.provider: r for r in rows}
        out = []
        for key in (PROVIDER_MICROSOFT, PROVIDER_GOOGLE):
            r = by_provider.get(key)
            if r is None or not r.is_connected:
                out.append({
                    "provider": key, "label": PROVIDER_LABELS.get(key, key),
                    "state": "not_connected", "state_label": "Not connected",
                    "detail": "Conflicts on this calendar cannot be checked.",
                    "action": "connect", "last_sync_at": None,
                })
                continue
            if not r.calendar_scope_ok:
                # Live for email, useless for calendar. A genuinely distinct
                # condition from "not connected", and one the user can only fix
                # by reconsenting — so it must not be reported as an outage we
                # might recover from.
                out.append({
                    "provider": key, "label": PROVIDER_LABELS.get(key, key),
                    "state": "reauth_required",
                    "state_label": "Reauthentication required",
                    "detail": "This connection does not grant calendar access.",
                    "action": "reconnect", "last_sync_at": r.last_sync_at,
                })
                continue
            if (r.failure_count or 0) > 0 and r.last_error:
                out.append({
                    "provider": key, "label": PROVIDER_LABELS.get(key, key),
                    "state": "degraded", "state_label": "Sync degraded",
                    # The provider's own message, already truncated and
                    # token-free where it was stored.
                    "detail": (r.last_error or "")[:300],
                    "action": "retry", "last_sync_at": r.last_sync_at,
                    "failure_count": r.failure_count,
                })
                continue
            out.append({
                "provider": key, "label": PROVIDER_LABELS.get(key, key),
                "state": "connected", "state_label": "Connected",
                # LAST SUCCESSFUL READ, not the connection date. "Connected"
                # beside a two-week-old sync is the meaningless green dot the
                # brief is about.
                "detail": None,
                "action": None, "last_sync_at": r.last_sync_at,
                "account_email": r.account_email,
            })
        return out

    mine = _one(viewer)
    result = {"mine": mine,
              "any_attention": any(p["state"] in ("reauth_required", "degraded")
                                   for p in mine)}

    if is_manager:
        counts = {"connected": 0, "not_connected": 0,
                  "reauth_required": 0, "degraded": 0}
        people = []
        for m in members or []:
            states = [p["state"] for p in _one(m)]
            # One verdict per person: the worst state they are in, because a
            # manager staffing a week cares whether this person's calendar can
            # be trusted, not which of two vendors is unhappy.
            worst = ("reauth_required" if "reauth_required" in states
                     else "degraded" if "degraded" in states
                     else "connected" if "connected" in states
                     else "not_connected")
            counts[worst] = counts.get(worst, 0) + 1
            people.append({"user_id": m.id, "full_name": m.full_name,
                           "state": worst})
        result["team"] = {"counts": counts, "people": people}

    return result


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

    # THE FIX. Read everyone's outside calendar BEFORE computing free time.
    # Without this the grid answers from a stale cache and calls somebody
    # available because EvoSys Pro itself holds no appointment for them — the
    # exact failure the availability engine was built to avoid, arriving by the
    # back door because nobody was refreshing its input.
    visibility = _refresh_external(db, members, start_utc, end_utc, org=org)

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
            mt = (db.query(MeetingType)
                  .filter(MeetingType.id == appt.meeting_type_id).first()
                  if appt.meeting_type_id else None)
            busy.append({
                "appointment_id": appt.id if visible else None,
                "title": appt.title if visible else "Busy",
                "starts_at": appt.starts_at, "ends_at": appt.ends_at,
                "busy_start_at": p.busy_start_at, "busy_end_at": p.busy_end_at,
                "confirmation_status": appt.confirmation_status if visible else None,
                # `kind` is what the grid colours by. Derived from the meeting
                # TYPE, not from the title, because a title is free text a rep
                # can write anything into and a legend has to mean something.
                #
                # A meeting this viewer may not read is BLOCKED, not
                # "internal": telling them which of a colleague's meetings are
                # customer-facing is already more than they are entitled to
                # know, and it is the kind of leak that looks harmless until
                # somebody infers a deal from it.
                "kind": (("internal" if (mt and mt.is_internal) else "customer")
                         if visible else "blocked"),
                "meeting_type": (mt.name if (mt and visible) else None),
                "is_required": bool(p.is_required),
                "needs_outcome": (apoutcome.outcome_out(appt)["needs_outcome"]
                                  if visible else False),
            })

        # ── the non-appointment layers ──────────────────────────────────────
        #
        # Image 4's grid shows lunch, PTO and external busy as distinct bands,
        # and it has to: a rep looking at a colleague's column needs to know
        # WHY an hour is unavailable, because "at lunch" and "on leave all
        # week" lead to different decisions. Previously the grid drew free time
        # and meetings only, so every other reason a person was unavailable
        # rendered as blank space that looked bookable.
        layers = _member_layers(db, m, prof, start_utc, end_utc)

        vis = (visibility or {}).get(m.id) or {}
        out.append({
            "user_id": m.id, "full_name": m.full_name, "email": m.email,
            "timezone": prof.timezone,
            "accepts_bookings": prof.accepts_bookings,
            "free": [{"starts_at": s, "ends_at": e} for s, e in free],
            "busy": sorted(busy, key=lambda b: b["starts_at"]),
            "working": layers["working"],
            "blocked": layers["blocked"],
            "time_off": layers["time_off"],
            # Interval only. There is no title here and no field to put one in,
            # which is what makes "BUSY — external calendar" the only thing the
            # UI can possibly render.
            "external_busy": layers["external_busy"],
            # Whether this person's outside calendar was ACTUALLY read for this
            # window. The grid uses it to mark a column as unverified rather
            # than presenting a guess with the same confidence as a fact.
            "external": vis,
        })

    db.commit()
    return {
        "brand_sales_org": {"id": org.id, "name": org.name, "timezone": tz},
        "range": {"start_utc": start_utc, "end_utc": end_utc,
                  "start_local_date": start_local_day, "days": days},
        "is_manager": manager,
        "members": out,
        "external_visibility": _external_visibility_summary(visibility),
        "sync_status": _sync_status_for(db, members, viewer=user,
                                       is_manager=manager),
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

    # Read every candidate's outside calendar BEFORE intersecting. The engine
    # only ever reads the cache, so an unrefreshed cache here does not produce
    # a slow search — it produces a confident list of times somebody is
    # already in a meeting for.
    visibility = _refresh_external(db, list(required) + list(optional),
                                   start_utc, end_utc, org=org)

    result = av.find_shared_slots(
        db, required, optional, start_utc, end_utc, duration,
        exclude_appointment_id=body.exclude_appointment_id)
    db.commit()

    names = {u.id: u.full_name for u in list(required) + list(optional)}
    ext_summary = _external_visibility_summary(visibility)

    # An honest caveat rather than a silent one. If somebody's outside calendar
    # could not be read, these slots are still the best available answer — but
    # the rep is told which part of the answer is unverified, so a clash later
    # is a known risk rather than a betrayal.
    blockers = list(result["blockers"])
    return {
        "timezone": tz,
        "duration_minutes": duration,
        "meeting_type": {"id": mt.id, "name": mt.name} if mt else None,
        "required": [{"user_id": u.id, "full_name": u.full_name,
                      "external": (visibility or {}).get(u.id) or {}}
                     for u in required],
        "optional": [{"user_id": u.id, "full_name": u.full_name,
                      "external": (visibility or {}).get(u.id) or {}}
                     for u in optional],
        "unresolved_roles": unresolved,
        "blockers": blockers,
        "external_visibility": ext_summary,
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

    # ── FINAL EXTERNAL REVALIDATION ─────────────────────────────────────────
    #
    # "Do not trust a slot just because it was displayed 30 seconds ago."
    #
    # `find_conflicts` above sees only EvoSys Pro's own appointments, so on its
    # own it cannot stop a booking being written on top of a meeting that lives
    # in Outlook. The slot list the rep clicked was also computed against a
    # cache that is up to ten minutes old by design — long enough for somebody
    # to have accepted an invitation in between.
    #
    # So the outside calendars are re-read HERE, with force=True to bypass the
    # TTL, and the window is checked again. This is the last moment at which
    # refusing is cheap: after the commit the meeting exists, invitations go
    # out, and the clash becomes a phone call.
    #
    # Failing to READ a calendar is deliberately NOT a refusal. A Microsoft
    # outage must not stop the team booking meetings; it degrades us to the
    # pre-check state, which is where every booking stood before this block
    # existed. Refusing on unreadable would hand a vendor a veto over the
    # sales team's day.
    ext_vis = _refresh_external(db, everyone, starts_at - timedelta(hours=1),
                               ends_at + timedelta(hours=1), org=org, force=True)
    ext_clashes = extbusy.external_conflicts(
        db, [u.id for u in everyone], starts_at, ends_at)
    if ext_clashes:
        by_id = {u.id: u for u in everyone}
        names = sorted({(by_id[c["user_id"]].full_name or by_id[c["user_id"]].email)
                        for c in ext_clashes if c["user_id"] in by_id})
        # Says WHERE the clash is, and says nothing about what the meeting is.
        # The interval is all the cache holds, and all the person booking is
        # entitled to.
        raise HTTPException(
            status_code=409,
            detail="A conflicting event appeared on the connected calendar of: "
                   "%s. Refresh the openings and pick another time." % ", ".join(names))

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
    sync_report, invite_report, meeting_report = None, None, None
    # VIDEO FIRST, deliberately. Calendar sync and the prospect invitation both
    # put `appt.meeting_url` in their bodies, so the Zoom room has to exist
    # before either runs or the join link is missing from every invitation.
    try:
        meeting_report = apmeet.ensure_meeting(db, appt)
        db.refresh(appt)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "video meeting provisioning raised for %s", appt.id)
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
    return {"sync": sync_report, "invite": invite_report, "meeting": meeting_report}


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
@limiter.limit("60/minute")
def prospect_confirm_page(request: Request, token: str, db: Session = Depends(get_db)):
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
@limiter.limit("60/minute")
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


# ── PUBLIC prospect confirmation, as JSON ───────────────────────────────────
#
# The two endpoints below back the branded page at
# https://<brand-host>/appointments/confirm/:token. They exist so the link a
# prospect receives carries the BRAND's hostname instead of the API's - the
# HTML page and form POST above are unchanged and still serve every link
# already sitting in somebody's inbox.
#
# The same discipline applies here: the GET changes nothing, so a corporate
# link scanner that fetches the page cannot confirm a meeting. Only the
# explicit POST below records an answer.
#
# NO AUTHENTICATION, for the same reason as above: the token is the
# authorisation. These return exactly what the HTML page shows a stranger and
# nothing more - no prospect email, no opportunity, no internal ids.

@router.get("/appointments/confirm/{token}/context", include_in_schema=False)
@limiter.limit("60/minute")
def prospect_confirm_context(request: Request, token: str, db: Session = Depends(get_db)):
    """Everything the branded confirmation page renders. CHANGES NOTHING."""
    row, appt, err = apinvite.resolve_token(db, token)
    if err:
        return {"ok": False, "error": err}

    ident = apinvite.brand_identity(db, appt)
    return {
        "ok": True,
        "title": appt.title or "Your meeting",
        "when": apinvite._local_when(appt),
        "cancelled": appt.status == APPT_CANCELLED,
        "confirmation_status": appt.confirmation_status,
        "brand_name": ident.get("name"),
        "support_phone": ident.get("support_phone"),
        "accent": ident.get("accent"),
        # THE BRAND IS CORRECT HERE, unlike on /book and /survey. This page is
        # opened by a PROSPECT of the platform - somebody being sold EvoSys Pro
        # or BookaBoost - so the platform's name is who they are dealing with.
        # What was wrong is that the tab said whatever the static index.html
        # said, so a BookaBoost prospect saw "EvoSys Pro". Resolved per brand.
        "document_title": ident.get("name") or "",
    }


@router.post("/appointments/confirm/{token}/respond", include_in_schema=False)
@limiter.limit("60/minute")
async def prospect_confirm_respond(token: str, request: Request,
                                   db: Session = Depends(get_db)):
    """Record the prospect's answer from the branded page.

    Same state change, same audit trail and same idempotency as the form POST
    above - it calls the identical `redeem_token`. Only the transport differs.
    """
    row, appt, err = apinvite.resolve_token(db, token)
    if err:
        return {"ok": False, "error": err}

    if appt.status == APPT_CANCELLED:
        return {"ok": False, "error": "This meeting has been cancelled."}

    action = ""
    try:
        body = await request.json()
        action = str((body or {}).get("action") or "")
    except Exception:
        action = ""
    if action not in ("confirm", "decline"):
        return {"ok": False, "error": "Please choose whether you can attend."}

    client_ip = request.client.host if request.client else None
    result = apinvite.redeem_token(db, row, appt, action, ip=client_ip)
    if not result.get("ok"):
        return {"ok": False, "error": "Something went wrong. Please try the link again."}

    if appt.opportunity_id:
        # The prospect is not a user, so `actor_user_id` stays NULL - recording
        # a staff member here would misattribute the action.
        db.add(OpportunityEvent(
            opportunity_id=appt.opportunity_id, event_type="confirmation",
            summary="Prospect %sed the meeting" % result["action"],
            detail="via confirmation link", actor_user_id=None))
    db.commit()

    ident = apinvite.brand_identity(db, appt)
    return {
        "ok": True,
        "action": result["action"],
        "when": apinvite._local_when(appt),
        "support_phone": ident.get("support_phone"),
    }


# ── COLLECTION ROUTES — declared before /appointments/{appt_id} ─────────────
#
# Route order is what keeps these from being swallowed. FastAPI matches in
# declaration order, so `/appointments/pending-outcome` declared AFTER
# `/appointments/{appt_id}` would resolve as appt_id="pending-outcome" and
# return a 404 that looks like a missing appointment. Same protection the
# prospect-confirmation routes above rely on.

@router.get("/appointments/pending-outcome")
def appointments_pending_outcome(brand_sales_org_id: Optional[str] = Query(None),
                                 scope: str = Query("mine"),
                                 limit: int = Query(50, ge=1, le=200),
                                 user: User = Depends(require_sales_member),
                                 db: Session = Depends(get_db)):
    """Meetings whose time has passed with no recorded outcome.

    THE MECHANISM THAT MAKES THE DATA AUTHORITATIVE. An optional field gets
    filled in when somebody feels like it, and the resulting dataset is worse
    than no dataset because its gaps are invisible. A visible queue turns
    "unrecorded" into a number a manager can drive to zero — which is the only
    reason T9 can stop guessing.
    """
    org = _org(user, db, brand_sales_org_id)
    tz = org.timezone or DEFAULT_TIMEZONE
    manager = is_sales_manager(user, db, org.id)

    if scope == "team" and not manager:
        raise HTTPException(status_code=403,
                            detail="Only a sales manager can view the team's queue.")

    rows = apoutcome.pending_outcomes(
        db, org.id, user=user,
        restrict_to_user=(scope != "team" or not manager),
        limit=limit)
    return {
        "brand_sales_org": {"id": org.id, "name": org.name, "timezone": tz},
        "scope": "team" if (scope == "team" and manager) else "mine",
        "is_manager": manager,
        "total": len(rows),
        "appointments": [_appt_out(db, a, user) for a in rows],
    }


@router.get("/appointments/completion-facts")
def appointments_completion_facts(brand_sales_org_id: Optional[str] = Query(None),
                                  date_from: Optional[date] = Query(None),
                                  date_to: Optional[date] = Query(None),
                                  user: User = Depends(require_sales_member),
                                  db: Session = Depends(get_db)):
    """Authoritative completion and outcome counts. THE CONTRACT WITH T9.

    Manager-gated because it is a team measure, and because a rep does not need
    the brand's aggregate to do their job.

    Every figure comes from a recorded human verdict. `unrecorded` is reported
    as its own number rather than folded into either side, so a consumer that
    computes a completion rate is computing it over what is actually known.
    """
    org = _org(user, db, brand_sales_org_id)
    if not is_sales_manager(user, db, org.id):
        raise HTTPException(status_code=403,
                            detail="Only a sales manager can read team completion data.")
    tz = org.timezone or DEFAULT_TIMEZONE
    start_utc = av.local_to_utc(date_from, 0, tz) if date_from else None
    end_utc = (av.local_to_utc(date_to + timedelta(days=1), 0, tz)
               if date_to else None)
    return apoutcome.completion_facts(db, org.id, start_utc, end_utc)


# ── the calendar view ───────────────────────────────────────────────────────

@router.get("/calendar/view")
def calendar_view(brand_sales_org_id: Optional[str] = Query(None),
                  date_from: Optional[date] = Query(None),
                  date_to: Optional[date] = Query(None),
                  scope: str = Query("team"),
                  member_ids: Optional[str] = Query(None),
                  meeting_type_ids: Optional[str] = Query(None),
                  location: Optional[str] = Query(None),
                  include_cancelled: bool = Query(False),
                  include_external: bool = Query(True),
                  user: User = Depends(require_sales_member),
                  db: Session = Depends(get_db)):
    """Everything the Team Calendar needs for a range, in one request.

    ONE REQUEST BY DESIGN. The screen renders a grid, a roster with live
    status, provider health, an agenda, an attention list and an upcoming
    list — all of which are views of the same window of time. Assembling them
    from six endpoints would make them disagree with each other during a
    paint, and a calendar whose panels contradict the grid beside them is a
    calendar nobody trusts.

    Day, Week, Month and Agenda are all THIS endpoint with a different range.
    The server does not need to know which one is on screen, and keeping that
    knowledge in the client is what stops four view modes becoming four
    subtly-different queries.

    `include_external` mirrors the screen's "show external calendars" control.
    Turning it off hides the band; it never changes what booking will allow,
    because a rep choosing a tidier view must not be able to switch off a
    conflict check.
    """
    org = _org(user, db, brand_sales_org_id)
    tz = org.timezone or DEFAULT_TIMEZONE
    manager = is_sales_manager(user, db, org.id)
    now = datetime.utcnow()

    d_from = date_from or av.utc_to_local(now, tz).date()
    d_to = date_to or (d_from + timedelta(days=6))
    if d_to < d_from:
        raise HTTPException(status_code=400, detail="date_to is before date_from.")
    if (d_to - d_from).days > MAX_VIEW_DAYS:
        raise HTTPException(status_code=400,
                            detail="Load at most %d days at a time." % MAX_VIEW_DAYS)

    start_utc = av.local_to_utc(d_from, 0, tz)
    end_utc = av.local_to_utc(d_to + timedelta(days=1), 0, tz)

    # ── who ─────────────────────────────────────────────────────────────────
    members = brand_members(db, org.id)
    wanted = [m for m in members
              if m.id in {s for s in (member_ids or "").split(",") if s}] \
        if member_ids else members
    if member_ids and not wanted:
        # An unknown id in the filter. Silently returning the whole team would
        # be a lie about what was filtered; returning nothing is the honest
        # answer to "show me these people" when none of them exist here.
        wanted = []

    # ── appointments ────────────────────────────────────────────────────────
    if scope == "team":
        if not manager:
            # A rep asking for the team scope is narrowed rather than refused.
            # The calendar is their own week either way, and a 403 rendered on
            # a landing screen punishes somebody who did nothing wrong.
            q = _visible_appointments(db, user, org)
            scope = "mine"
        else:
            q = db.query(SalesAppointment).filter(
                SalesAppointment.brand_sales_org_id == org.id)
    else:
        q = _visible_appointments(db, user, org)

    q = q.filter(SalesAppointment.starts_at < end_utc,
                 SalesAppointment.ends_at > start_utc)
    if not include_cancelled:
        q = q.filter(SalesAppointment.status != APPT_CANCELLED)

    type_filter = {s for s in (meeting_type_ids or "").split(",") if s}
    if type_filter:
        q = q.filter(SalesAppointment.meeting_type_id.in_(type_filter))
    if location:
        q = q.filter(SalesAppointment.location.ilike("%" + location + "%"))

    rows = q.order_by(SalesAppointment.starts_at.asc()).limit(500).all()

    # The member filter is applied on participants, which is a join this query
    # deliberately avoids: an appointment matches if ANY selected person is on
    # it, and expressing that in SQL alongside the visibility filter above
    # produced a query that was easy to get subtly wrong. Filtering the
    # (bounded) result set is correct and obviously correct.
    appts = [_appt_out(db, a, user) for a in rows]
    if member_ids is not None:
        keep = {m.id for m in wanted}
        appts = [a for a in appts
                 if any(p["user_id"] in keep for p in a["participants"])]

    # ── the per-person layers ───────────────────────────────────────────────
    visibility = {}
    if include_external and wanted:
        visibility = _refresh_external(db, wanted, start_utc, end_utc, org=org)

    people = []
    for m in wanted:
        prof = av.get_or_create_profile(db, m)
        layers = _member_layers(db, m, prof, start_utc, end_utc)
        if not include_external:
            layers["external_busy"] = []
        # "In a meeting" / "Available" right now, which is what the roster
        # panel shows. Computed from the participant rows rather than from the
        # filtered appointment list, so a rep filtered out of the grid still
        # reports their true status.
        in_meeting = (db.query(AppointmentParticipant)
                      .join(SalesAppointment,
                            SalesAppointment.id == AppointmentParticipant.appointment_id)
                      .filter(AppointmentParticipant.user_id == m.id,
                              AppointmentParticipant.is_blocking.is_(True),
                              SalesAppointment.starts_at <= now,
                              SalesAppointment.ends_at > now).first())
        people.append({
            "user_id": m.id, "full_name": m.full_name, "email": m.email,
            "timezone": prof.timezone,
            "accepts_bookings": prof.accepts_bookings,
            "status": "in_meeting" if in_meeting else "available",
            "status_label": "In a meeting" if in_meeting else "Available",
            "working": layers["working"],
            "blocked": layers["blocked"],
            "time_off": layers["time_off"],
            "external_busy": layers["external_busy"],
            "external": (visibility or {}).get(m.id) or {},
        })

    # ── the panels ──────────────────────────────────────────────────────────
    today_local = av.utc_to_local(now, tz).date()
    agenda = [a for a in appts
              if str(a.get("starts_at_local") or "")[:10] == today_local.isoformat()]

    # ATTENTION is a mixed list on purpose: a manager does not think in terms
    # of "unconfirmed" versus "sync failed" versus "no outcome recorded" — they
    # think "what will bite me". So the three are one ranked list with a reason
    # on each, rather than three panels somebody has to check separately.
    attention = []
    for a in appts:
        if a["status"] == APPT_CANCELLED:
            continue
        if a["confirmation_status"] == CONF_PENDING and a["starts_at"] >= now:
            attention.append({"appointment_id": a["id"], "title": a["title"],
                              "kind": "unconfirmed",
                              "label": "Prospect has not confirmed",
                              "action": "send_confirmation",
                              "starts_at_local": a["starts_at_local"]})
        if a["sync_needs_attention"]:
            attention.append({"appointment_id": a["id"], "title": a["title"],
                              "kind": "sync",
                              "label": "%d calendar%s could not be written"
                                       % (a["sync_needs_attention"],
                                          "" if a["sync_needs_attention"] == 1 else "s"),
                              "action": "resync",
                              "starts_at_local": a["starts_at_local"]})
        if a.get("sync_conflicts"):
            attention.append({"appointment_id": a["id"], "title": a["title"],
                              "kind": "conflict",
                              "label": "Changed outside EvoSys Pro",
                              "action": "review_conflict",
                              "starts_at_local": a["starts_at_local"]})
        if a["outcome_state"]["needs_outcome"]:
            attention.append({"appointment_id": a["id"], "title": a["title"],
                              "kind": "outcome",
                              "label": "No outcome recorded",
                              "action": "record_outcome",
                              "starts_at_local": a["starts_at_local"]})

    upcoming = [a for a in appts
                if a["starts_at"] >= now and a["status"] == APPT_SCHEDULED][:12]

    # ── the filter vocabularies the screen renders ──────────────────────────
    types = ensure_meeting_types(db, org.id)
    db.commit()
    locations = sorted({(a["location"] or "").strip() for a in appts
                        if (a["location"] or "").strip()})

    return {
        "brand_sales_org": {"id": org.id, "name": org.name, "timezone": tz},
        "range": {"date_from": d_from, "date_to": d_to,
                  "start_utc": start_utc, "end_utc": end_utc,
                  "days": (d_to - d_from).days + 1},
        "now_utc": now,
        "now_local": av.utc_to_local(now, tz),
        "scope": scope,
        "is_manager": manager,
        "appointments": appts,
        "total": len(appts),
        "people": people,
        "agenda_today": agenda,
        "attention": attention,
        "upcoming": upcoming,
        "meeting_types": [{"id": t.id, "key": t.key, "name": t.name,
                           "duration_minutes": t.duration_minutes,
                           "is_internal": bool(t.is_internal)} for t in types],
        "locations": locations,
        "external_visibility": _external_visibility_summary(visibility),
        "sync_status": _sync_status_for(db, members, viewer=user, is_manager=manager),
        # Stated so the client never has to infer whether the external band it
        # is not drawing was filtered out or simply unreadable.
        "external_included": bool(include_external),
    }


@router.get("/calendar/sync-status")
def calendar_sync_status(brand_sales_org_id: Optional[str] = Query(None),
                         user: User = Depends(require_sales_member),
                         db: Session = Depends(get_db)):
    """Provider health with a reason and an action on every row.

    Its own endpoint as well as a block inside the calendar view, because the
    integrations panel needs to refresh after somebody reconnects without
    reloading a week of appointments to find out whether it worked.
    """
    org = _org(user, db, brand_sales_org_id)
    manager = is_sales_manager(user, db, org.id)
    members = brand_members(db, org.id) if manager else []
    return {
        "brand_sales_org": {"id": org.id, "name": org.name},
        "is_manager": manager,
        **_sync_status_for(db, members, viewer=user, is_manager=manager),
    }


@router.get("/calendar/conflicts")
def calendar_conflicts(brand_sales_org_id: Optional[str] = Query(None),
                       limit: int = Query(50, ge=1, le=200),
                       user: User = Depends(require_sales_member),
                       db: Session = Depends(get_db)):
    """Provider events changed outside EvoSys Pro, awaiting a decision.

    Manager-gated: a conflict names a specific person's calendar and how it
    disagrees with ours, which is team-management information rather than
    something every rep needs about every colleague.

    Both sides of each disagreement are returned. A review screen that shows
    only our version is asking somebody to choose blind.
    """
    org = _org(user, db, brand_sales_org_id)
    if not is_sales_manager(user, db, org.id):
        raise HTTPException(status_code=403,
                            detail="Only a sales manager can review calendar conflicts.")
    rows = apreconcile.conflict_rows(db, org.id, limit=limit)
    out = []
    for appt, part in rows:
        u = db.query(User).filter(User.id == part.user_id).first()
        out.append(apreconcile.conflict_out(appt, part, u))
    return {"brand_sales_org": {"id": org.id, "name": org.name},
            "total": len(out), "conflicts": out}


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
        # Cancel the Zoom room too. A meeting nobody cancelled is a room the
        # prospect can still walk into after the deal is dead.
        apmeet.cancel_meeting(db, appt, reason=appt.cancel_reason)
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "video meeting cancel raised for %s", appt.id)
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

    # The same external revalidation booking does, for the same reason: the new
    # time was chosen against a cache, and a reschedule that lands on top of
    # somebody's Outlook meeting is no better than a booking that does.
    #
    # `external_conflicts` reads the cache and cannot know that one of these
    # intervals is this meeting's own provider event. It does not need to: the
    # events we wrote are excluded by the fact that a MOVE is checked against
    # the NEW window, which our existing events do not occupy — and a genuine
    # self-match would only ever narrow a move to a time the rep already holds.
    _org_row = db.query(BrandSalesOrg).filter(
        BrandSalesOrg.id == appt.brand_sales_org_id).first()
    participants_users = [u for u in
                          (db.query(User).filter(User.id.in_(user_ids or [""])).all())]
    _refresh_external(db, participants_users, starts_at - timedelta(hours=1),
                      ends_at + timedelta(hours=1), org=_org_row, force=True)
    ext_clashes = [c for c in extbusy.external_conflicts(db, user_ids, starts_at, ends_at)
                   # Exclude this appointment's OWN provider events, which are
                   # legitimately in the cache and would otherwise make every
                   # small nudge of a meeting look like a clash with itself.
                   if c.get("starts_at") != appt.starts_at]
    if ext_clashes:
        by_id = {u.id: u for u in participants_users}
        names = sorted({(by_id[c["user_id"]].full_name or by_id[c["user_id"]].email)
                        for c in ext_clashes if c["user_id"] in by_id})
        if names:
            raise HTTPException(
                status_code=409,
                detail="A conflicting event appeared on the connected calendar of: "
                       "%s. Pick another time." % ", ".join(names))

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


# ── OUTCOME — what actually happened ────────────────────────────────────────

class OutcomeIn(BaseModel):
    outcome: str
    notes: Optional[str] = None
    # {user_id: attendance_status}. Optional: the meeting's own outcome is the
    # headline fact, and per-person attendance is extra detail a rep may or may
    # not have. Absent means 'unknown', never 'everyone attended'.
    attendance: Optional[dict] = None
    next_action: Optional[str] = None
    next_action_due_at: Optional[datetime] = None
    # OPT-IN, and defaulted False. A stage that moves because somebody recorded
    # an outcome — without being asked — is how every "Won" in a pipeline
    # becomes suspect.
    advance_stage: bool = False


@router.get("/appointments/{appt_id}/outcome-options")
def get_outcome_options(appt_id: str,
                        user: User = Depends(require_sales_member),
                        db: Session = Depends(get_db)):
    """Which outcomes apply to THIS meeting, and why the others do not.

    Returns the full vocabulary annotated rather than pre-filtered, so the UI
    can show an unavailable option with its reason. Silently omitting "Won"
    from a meeting with no deal attached leaves the rep hunting for a button
    that was never there.
    """
    appt = _load_appt(db, appt_id, user)
    cat = apoutcome.outcome_catalog(db, appt)
    # Whether THIS caller could also move the deal, so the dialog can offer the
    # stage checkbox only to somebody it would actually work for.
    can_edit = False
    if appt.opportunity_id:
        opp = db.query(Opportunity).filter(
            Opportunity.id == appt.opportunity_id).first()
        if opp is not None:
            try:
                assert_can_edit_opportunity(user, opp, db)
                can_edit = True
            except HTTPException:
                can_edit = False
    cat["can_advance_stage"] = can_edit
    return cat


@router.post("/appointments/{appt_id}/outcome")
def record_appointment_outcome(appt_id: str, body: OutcomeIn,
                               user: User = Depends(require_sales_member),
                               db: Session = Depends(get_db)):
    """Record what happened. THE FIX FOR THE T9 COMPLETION GAP.

    Only a PARTICIPANT or the deal's owner (or a manager) reaches this, via
    `_load_appt`. That is deliberate and slightly stricter than it looks: an
    outcome is testimony about a meeting, and testimony from somebody who was
    not on it and does not own the deal is exactly the low-quality data that
    would make these numbers worthless again.

    The write and any stage move commit TOGETHER. A half-applied outcome — the
    meeting marked won, the deal still in Discovery — would be worse than none,
    because it looks recorded.
    """
    appt = _load_appt(db, appt_id, user)

    can_edit = False
    if appt.opportunity_id and body.advance_stage:
        opp = db.query(Opportunity).filter(
            Opportunity.id == appt.opportunity_id).first()
        if opp is not None:
            # Raises 403 if they may not. Asked for explicitly and refused
            # explicitly, rather than silently ignored — somebody who ticked
            # "move the deal" deserves to be told it did not happen.
            assert_can_edit_opportunity(user, opp, db)
            can_edit = True

    try:
        report = apoutcome.record_outcome(
            db, appt, user, body.outcome,
            notes=body.notes,
            attendance=body.attendance,
            next_action=body.next_action,
            next_action_due_at=body.next_action_due_at,
            advance_stage=body.advance_stage,
            can_edit_opportunity=can_edit)
    except apoutcome.OutcomeError as e:
        db.rollback()
        raise HTTPException(status_code=e.code, detail=e.message)

    db.commit()
    db.refresh(appt)

    # ── propagate a cancellation outward ────────────────────────────────────
    # An outcome of `cancelled` is a cancellation. If it only changed our own
    # row, everyone would still hold the meeting on their calendar and
    # somebody would dial in — the same failure the cancel endpoint exists to
    # prevent, so it gets the same treatment. Recorded AFTER the commit and
    # never allowed to raise.
    if body.outcome == apoutcome.OUTCOME_CANCELLED:
        try:
            apmeet.cancel_meeting(db, appt, reason=body.notes)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "video cancel raised for outcome on %s", appt.id)
        try:
            apsync.cancel_appointment_sync(db, appt, organizer=user)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "cancel sync raised for outcome on %s", appt.id)
        db.refresh(appt)

    out = _appt_out(db, appt, user)
    out["outcome_report"] = report
    return out


# ── RECONCILIATION — external edits ─────────────────────────────────────────

class ResolveConflictIn(BaseModel):
    user_id: str
    action: str          # push_evosys | unlink


@router.post("/appointments/{appt_id}/reconcile")
def reconcile_appointment_route(appt_id: str,
                                auto_heal: bool = Query(True),
                                user: User = Depends(require_sales_member),
                                db: Session = Depends(get_db)):
    """Check every participant's calendar copy against what we pushed.

    Reads the providers; writes only where the resolution is deterministic. A
    time change made outside EvoSys Pro is never overwritten here — it is
    raised as a conflict, because the likeliest reason somebody moved a meeting
    in Outlook is that they agreed the new time with the prospect, and
    silently restoring ours would put the rep back in a meeting the customer
    has already left.
    """
    appt = _load_appt(db, appt_id, user)
    org = db.query(BrandSalesOrg).filter(
        BrandSalesOrg.id == appt.brand_sales_org_id).first()
    report = apreconcile.reconcile_appointment(
        db, appt, org=org, organizer=user, auto_heal=auto_heal)
    db.refresh(appt)
    out = _appt_out(db, appt, user)
    out["reconcile_report"] = report
    return out


@router.post("/appointments/{appt_id}/resolve-conflict")
def resolve_appointment_conflict(appt_id: str, body: ResolveConflictIn,
                                 user: User = Depends(require_sales_member),
                                 db: Session = Depends(get_db)):
    """Apply a human's decision about one participant's drifted calendar.

    Manager-gated. Resolving a conflict writes to somebody else's calendar (or
    deliberately declines to), and that is a team decision rather than
    something any colleague should be able to do to another's Outlook.

    Adopting the provider's NEW TIME is not offered here. That is a reschedule:
    it has to re-check every other participant, re-push every calendar, reset
    the prospect's confirmation and write the deal timeline. A button that
    just rewrote two columns would produce a meeting that looks moved and is
    not, so the conflict payload points at the reschedule flow instead.
    """
    appt = _load_appt(db, appt_id, user)
    if not is_sales_manager(user, db, appt.brand_sales_org_id):
        raise HTTPException(status_code=403,
                            detail="Only a sales manager can resolve a calendar conflict.")

    part = (db.query(AppointmentParticipant)
            .filter(AppointmentParticipant.appointment_id == appt.id,
                    AppointmentParticipant.user_id == body.user_id).first())
    if part is None:
        # 404 rather than 403: do not confirm whether that user id exists at
        # all to somebody probing this endpoint with guesses.
        raise HTTPException(status_code=404, detail="Participant not found on this meeting.")
    if not part.sync_conflict:
        raise HTTPException(status_code=400,
                            detail="That participant's calendar is not in conflict.")
    if body.action not in ("push_evosys", "unlink"):
        raise HTTPException(status_code=400,
                            detail="Unknown resolution '%s'." % body.action)

    target = db.query(User).filter(User.id == part.user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Participant not found on this meeting.")
    org = db.query(BrandSalesOrg).filter(
        BrandSalesOrg.id == appt.brand_sales_org_id).first()

    result = apreconcile.resolve_conflict(db, appt, part, target, user,
                                          body.action, org=org)
    db.commit()
    db.refresh(appt)
    out = _appt_out(db, appt, user)
    out["resolution"] = result
    return out


@router.get("/appointments/{appt_id}/host-link")
def get_meeting_host_link(appt_id: str,
                          user: User = Depends(require_sales_member),
                          db: Session = Depends(get_db)):
    """The Zoom HOST link. Deliberately its own endpoint, not a serializer field.

    Zoom's start_url starts the meeting AS the host — whoever holds it can
    impersonate the host of a real customer meeting. So it is:

      · never included in `_appt_out`, any email, any .ics, or any calendar body
      · fetched only on demand, by an explicit request
      · restricted to PARTICIPANTS of this specific meeting

    A sales manager who can VIEW the appointment still cannot take it — being
    able to see a meeting on the team calendar is not the same as being
    entitled to host it.
    """
    appt = _load_appt(db, appt_id, user)
    on_it = (db.query(AppointmentParticipant)
             .filter(AppointmentParticipant.appointment_id == appt.id,
                     AppointmentParticipant.user_id == user.id).first())
    if not on_it:
        raise HTTPException(
            status_code=403,
            detail="Only a participant of this meeting can start it as host.")
    host_url = apmeet.host_url_for(db, appt.id)
    if not host_url:
        raise HTTPException(status_code=404,
                            detail="This meeting has no host link.")
    return {"host_url": host_url}


@router.post("/appointments/{appt_id}/video/retry")
def retry_video_meeting(appt_id: str,
                        user: User = Depends(require_sales_member),
                        db: Session = Depends(get_db)):
    """Retry provisioning after a provider failure. Idempotent — with an
    existing provider meeting id this updates rather than creating a second."""
    appt = _load_appt(db, appt_id, user)
    if appt.status == APPT_CANCELLED:
        raise HTTPException(status_code=400,
                            detail="This meeting is cancelled.")
    report = apmeet.ensure_meeting(db, appt)
    db.refresh(appt)
    out = _appt_out(db, appt, user)
    out["video_report"] = report
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


# ── Video meeting status ─────────────────────────────────────────────────────

@router.get("/video/status")
def video_status(verify: bool = Query(False),
                 user: User = Depends(require_sales_member),
                 db: Session = Depends(get_db)):
    """Zoom configuration health for this brand.

    Returns state, credential source and which meeting types will auto-create
    a Zoom room. No credential values are ever included.

    Pass ?verify=true to perform a real round-trip to the Zoom API — this
    proves both that credentials are valid AND that the S2S OAuth scope is
    present. A status that only proves a database row exists is worth nothing,
    so the 'Test connection' button in VideoStatus.jsx always uses verify=true.

    HOST URL SAFETY: this endpoint never reads or returns a host URL.
    """
    from app.models.meeting_models import MeetingProviderConfig

    org = _org(user, db)

    # ensure_meeting_types triggers the requires_video backfill for brands that
    # were seeded before Checkpoint 4. Idempotent — a no-op if already set.
    types = ensure_meeting_types(db, org.id)
    db.commit()

    type_out = [
        {"id": t.id, "name": t.name, "requires_video": t.requires_video}
        for t in types
    ]

    provider = get_provider(db, org.id, PROVIDER_ZOOM, None)
    _ready, _reason = provider.is_ready() if provider is not None else (False, None)
    if provider is None or not _ready:
        return {
            # `provider` is the MACHINE key and provider_label is the human
            # one. Both are needed: a caller deciding behaviour must not have
            # to string-match a display name, which is why the one that only
            # shipped a label was a defect rather than a style choice.
            "brand_sales_org_id": org.id,
            "provider": PROVIDER_ZOOM,
            "has_credentials": False,
            "state": "not_configured",
            "provider_label": "Zoom",
            "detail": None,
            "credential_source": None,
            "last_verified_at": None,
            "setup_hint": (
                "Set ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET "
                "as server environment variables to enable automatic Zoom "
                "meeting creation for this brand."
            ),
            "meeting_types": type_out,
        }

    cfg_row = (db.query(MeetingProviderConfig)
               .filter(MeetingProviderConfig.brand_sales_org_id == org.id,
                       MeetingProviderConfig.provider == PROVIDER_ZOOM,
                       MeetingProviderConfig.is_active.is_(True)).first())
    credential_source = "brand_config" if cfg_row else "environment"
    last_verified_at = (
        cfg_row.last_verified_at.isoformat()
        if cfg_row and cfg_row.last_verified_at else None
    )

    if verify:
        result = provider.verify()
        if result.ok:
            if cfg_row:
                cfg_row.last_verified_at = datetime.utcnow()
                db.commit()
                last_verified_at = cfg_row.last_verified_at.isoformat()
            return {
                "brand_sales_org_id": org.id,
                "provider": PROVIDER_ZOOM,
                "has_credentials": True,
                "verified": True,
                "state": "ready",
                "provider_label": "Zoom Server-to-Server OAuth",
                "detail": "Connected — host identity and API scope confirmed.",
                "credential_source": credential_source,
                "last_verified_at": last_verified_at,
                "meeting_types": type_out,
            }
        return {
            "brand_sales_org_id": org.id,
            "provider": PROVIDER_ZOOM,
            # Credentials EXIST; they were rejected. Those are different
            # answers and collapsing them sends a person to re-enter a secret
            # that was never the problem.
            "has_credentials": True,
            "verified": False,
            "state": "error",
            "provider_label": "Zoom Server-to-Server OAuth",
            "detail": result.error_message or "Zoom API returned an error.",
            "credential_source": credential_source,
            "last_verified_at": last_verified_at,
            "setup_hint": (
                "Check that ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET "
                "are correct and that the Server-to-Server OAuth app has the "
                "meeting:write:admin and user:read:admin scopes activated."
            ),
            "meeting_types": type_out,
        }

    # Credentials present but no live verify requested.
    return {
        "brand_sales_org_id": org.id,
        "provider": PROVIDER_ZOOM,
        "has_credentials": True,
        # NOT verified - nobody asked for a round-trip. None means unknown,
        # which is the honest answer and not the same as False.
        "verified": None,
        "state": "ready",
        "provider_label": "Zoom Server-to-Server OAuth",
        "detail": "Credentials present. Use 'Test connection' to verify scope.",
        "credential_source": credential_source,
        "last_verified_at": last_verified_at,
        "meeting_types": type_out,
    }
