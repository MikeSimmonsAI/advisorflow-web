"""The Retell bridge — a narrow translation layer, not a scheduler.

WHAT THIS DOES NOT DO. It does not compute availability. Every opening it
returns comes from `availability.find_shared_slots`, which is the identical
function behind `POST /sales/availability/find`, so a voice agent and a
salesperson looking at the same advisor see the same openings computed by the
same rules — working hours, recurring blocks, time off, existing meetings,
external calendar busy time, per-person buffers, minimum notice and booking
horizon. There is one scheduler in this system and this file is not it.

It also does not re-implement booking side effects: the Zoom room, the calendar
push and the prospect invitation all run through `_push_appointment`, the same
function the sales workspace uses, imported rather than copied.

WHAT IT DOES DO is the part that has to be different for a machine caller:
resolve a scoped credential to exactly one advisor, refuse anything outside that
scope with an answer that reveals nothing, re-check the slot at booking time
because a voice call takes minutes and a calendar can change inside one, make a
retry safe, and write down what happened.

SCOPE IS ENFORCED HERE, NOT TRUSTED FROM THE REQUEST. The brand comes from the
credential. The advisor must be an active member of that brand and, when the
key names an allowlist, must be on it. An advisor id that is unknown and an
advisor id that belongs to another brand produce the identical 404 — a caller
must not be able to use this endpoint to discover who exists.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, date as date_cls
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import (
    BrandSalesOrg, Opportunity, OpportunityEvent, Membership,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (
    MeetingType, SalesAppointment, AppointmentParticipant,
    APPT_SCHEDULED, CONF_PENDING, ATTEND_UNKNOWN, DEFAULT_TIMEZONE,
)
from app.models.integration_models import (
    IntegrationCredential, IntegrationRequestLog,
    ACTION_AVAILABILITY, ACTION_BOOK,
)
from app.services import availability as av

log = logging.getLogger(__name__)

# A voice call cannot wait on a 60-day sweep, and a caller asking "what have you
# got?" does not mean "read me two months of diary".
MAX_RANGE_DAYS = 21
# Enough for a spoken menu several times over; more is noise down a phone line.
MAX_SLOTS = 40
DEFAULT_DURATION_MINUTES = 30

# Every out-of-scope answer is this one, whatever the real reason.
_NO_ADVISOR = "Advisor not found."


# ── audit ───────────────────────────────────────────────────────────────────

def audit(db: Session, cred: Optional[IntegrationCredential], action: str,
          success: bool, status_code: int, detail: str = None,
          advisor_user_id: str = None, appointment_id: str = None,
          external_ref: str = None, row: IntegrationRequestLog = None,
          now: datetime = None) -> IntegrationRequestLog:
    """Write (or complete) one audit row. Never raises into the request.

    An audit trail that can fail a request teaches people to switch it off.
    """
    now = now or datetime.utcnow()
    try:
        if row is None:
            row = IntegrationRequestLog(
                credential_id=cred.id if cred else None,
                integration_name=cred.name if cred else None,
                key_prefix=cred.key_prefix if cred else None,
                brand_sales_org_id=cred.brand_sales_org_id if cred else None,
                action=action, external_ref=external_ref, occurred_at=now)
            db.add(row)
        row.action = action
        row.success = bool(success)
        row.status_code = status_code
        row.detail = (detail or "")[:1000] or None
        if advisor_user_id:
            row.advisor_user_id = advisor_user_id
        if appointment_id:
            row.appointment_id = appointment_id
        db.flush()
        return row
    except Exception:
        log.exception("integration audit write failed (%s)", action)
        return row


# ── scope resolution ────────────────────────────────────────────────────────

def brand_for(db: Session, cred: IntegrationCredential) -> BrandSalesOrg:
    org = (db.query(BrandSalesOrg)
           .filter(BrandSalesOrg.id == cred.brand_sales_org_id).first())
    if org is None:
        # The key outlived its brand. Fail closed rather than fall back to any
        # other brand — a credential with nothing to point at points at nothing.
        raise HTTPException(status_code=503,
                            detail="This integration is not currently configured.")
    return org


def _is_brand_member(db: Session, user_id: str, brand_sales_org_id: str) -> bool:
    return db.query(Membership).filter(
        Membership.user_id == user_id,
        Membership.scope_type == SCOPE_BRAND_SALES_ORG,
        Membership.scope_id == brand_sales_org_id,
        Membership.role.in_((ROLE_SALES_MANAGER, ROLE_SALES_REP)),
        Membership.is_active.is_(True),
    ).first() is not None


def resolve_advisor(db: Session, cred: IntegrationCredential,
                    advisor_id: Optional[str]) -> User:
    """The one calendar this request may touch.

    Order: an explicit id if given, else the credential's default. Then three
    checks that all fail the same way — allowlist, brand membership, active
    user. A caller learns only that the advisor is not available to them.
    """
    target = (advisor_id or "").strip() or (cred.default_advisor_user_id or "")
    if not target:
        raise HTTPException(
            status_code=400,
            detail="No advisor was named and this integration has no default advisor.")

    allow = cred.advisor_allowlist()
    if allow and target not in allow:
        raise HTTPException(status_code=404, detail=_NO_ADVISOR)

    user = db.query(User).filter(User.id == target).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail=_NO_ADVISOR)
    if not _is_brand_member(db, user.id, cred.brand_sales_org_id):
        # Exists, but not in this credential's brand. Same answer as "does not
        # exist" — otherwise this route enumerates the platform's users.
        raise HTTPException(status_code=404, detail=_NO_ADVISOR)
    return user


def resolve_meeting_type(db: Session, cred: IntegrationCredential,
                         key_or_id: Optional[str]) -> Optional[MeetingType]:
    """Optional. Accepts either the stable key ('discovery') or the row id."""
    val = (key_or_id or "").strip()
    if not val:
        return None
    mt = (db.query(MeetingType)
          .filter(MeetingType.brand_sales_org_id == cred.brand_sales_org_id,
                  MeetingType.key == val).first())
    if mt is None:
        mt = (db.query(MeetingType)
              .filter(MeetingType.brand_sales_org_id == cred.brand_sales_org_id,
                      MeetingType.id == val).first())
    if mt is None:
        raise HTTPException(status_code=404, detail="Meeting type not found.")
    return mt


def _tz_for(db: Session, user: User, org: BrandSalesOrg,
            requested: Optional[str]) -> str:
    """The timezone the answer is spoken in.

    Defaults to the ADVISOR's own, because that is the zone their availability
    was defined in. A caller may override for display, but an unknown zone is
    refused rather than silently swapped for UTC — a voice agent reading out the
    wrong hour is worse than one that says it cannot help.
    """
    if requested:
        # Validated against zoneinfo DIRECTLY, not through availability._zone().
        # That helper deliberately falls back to the team default so an unknown
        # name can never take the scheduler down — correct there, wrong here: a
        # voice agent handed a bad zone would confidently read out a Chicago
        # time that the caller hears as their own. Refuse instead.
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(requested)
        except Exception:
            raise HTTPException(status_code=400,
                                detail="Unknown timezone: %s" % requested)
        return requested
    prof = av.get_or_create_profile(db, user)
    return prof.timezone or org.timezone or DEFAULT_TIMEZONE


# ── availability ────────────────────────────────────────────────────────────

def availability(db: Session, cred: IntegrationCredential, advisor: User,
                 org: BrandSalesOrg, date_from: date_cls,
                 date_to: Optional[date_cls], duration_minutes: Optional[int],
                 timezone: Optional[str], meeting_type: Optional[MeetingType],
                 now: Optional[datetime] = None) -> dict:
    """Openings for one advisor, from the one scheduling engine."""
    now = now or datetime.utcnow()
    tz = _tz_for(db, advisor, org, timezone)

    duration = duration_minutes or (meeting_type.duration_minutes if meeting_type
                                    else None) or DEFAULT_DURATION_MINUTES
    if duration < 5 or duration > 480:
        raise HTTPException(status_code=400,
                            detail="duration_minutes must be between 5 and 480.")

    d_to = date_to or date_from
    if d_to < date_from:
        raise HTTPException(status_code=400,
                            detail="date_to is before date_from.")
    if (d_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail="Search at most %d days at a time." % MAX_RANGE_DAYS)

    start_utc = av.local_to_utc(date_from, 0, tz)
    end_utc = av.local_to_utc(d_to + timedelta(days=1), 0, tz)

    # THE ENGINE. Same call shape as /sales/availability/find, with one required
    # participant and no optional ones.
    result = av.find_shared_slots(db, [advisor], [], start_utc, end_utc,
                                  duration, now_utc=now, limit=MAX_SLOTS)

    slots = []
    for s in result["slots"]:
        local_start = av.utc_to_local(s["starts_at"], tz)
        local_end = av.utc_to_local(s["ends_at"], tz)
        slots.append({
            # Unambiguous machine form.
            "starts_at": s["starts_at"].replace(microsecond=0).isoformat() + "Z",
            "ends_at": s["ends_at"].replace(microsecond=0).isoformat() + "Z",
            # Wall clock in the stated zone, for a human ear.
            "starts_at_local": local_start.replace(microsecond=0).isoformat(),
            "ends_at_local": local_end.replace(microsecond=0).isoformat(),
            "duration_minutes": duration,
            # What the agent can read aloud without formatting anything itself.
            "label": local_start.strftime("%A, %B %d at %I:%M %p").replace(" 0", " "),
        })

    return {
        "success": True,
        "advisor_id": advisor.id,
        "advisor_name": advisor.full_name,
        "timezone": tz,
        "duration_minutes": duration,
        "meeting_type": meeting_type.key if meeting_type else None,
        "date_from": date_from.isoformat(),
        "date_to": d_to.isoformat(),
        "slot_count": len(slots),
        "slots": slots,
        # Why there is nothing, in words a voice agent can say. Empty when there
        # are slots. An empty list with no reason is the thing this avoids.
        "blockers": list(result.get("blockers") or []),
        "reason": (None if slots else
                   (result["blockers"][0] if result.get("blockers")
                    else "No openings in that range.")),
    }


# ── booking ─────────────────────────────────────────────────────────────────

def _replay(db: Session, row: IntegrationRequestLog) -> dict:
    appt = (db.query(SalesAppointment)
            .filter(SalesAppointment.id == row.appointment_id).first())
    if appt is None:
        return {"success": True, "idempotent_replay": True,
                "appointment_id": row.appointment_id,
                "message": "Already booked."}
    return {
        "success": True,
        "idempotent_replay": True,
        "appointment_id": appt.id,
        "starts_at": appt.starts_at.replace(microsecond=0).isoformat() + "Z",
        "ends_at": appt.ends_at.replace(microsecond=0).isoformat() + "Z",
        "timezone": appt.timezone,
        "starts_at_local": av.utc_to_local(appt.starts_at, appt.timezone)
                             .replace(microsecond=0).isoformat(),
        "title": appt.title,
        "confirmation_status": appt.confirmation_status,
        "message": "This booking was already made.",
    }


def find_prior_attempt(db: Session, cred: IntegrationCredential,
                       external_ref: str) -> Optional[IntegrationRequestLog]:
    return (db.query(IntegrationRequestLog)
            .filter(IntegrationRequestLog.credential_id == cred.id,
                    IntegrationRequestLog.external_ref == external_ref)
            .first())


def book(db: Session, cred: IntegrationCredential, advisor: User,
         org: BrandSalesOrg, starts_at: datetime,
         duration_minutes: Optional[int], meeting_type: Optional[MeetingType],
         external_ref: str, timezone: Optional[str] = None,
         prospect_name: str = None, prospect_email: str = None,
         prospect_phone: str = None, prospect_timezone: str = None,
         opportunity_id: str = None, notes: str = None,
         now: Optional[datetime] = None) -> dict:
    """Take a slot, having proved it is still free at this instant.

    THE RE-CHECK IS THE POINT. The openings this agent read may be minutes old;
    a phone call is long enough for a colleague to book the same time, or for
    the advisor's own calendar to change. `find_conflicts` runs inside this
    transaction, and on Postgres the participant exclusion constraint catches
    the genuine race the check cannot see — two requests that both pass before
    either commits. One wins; the other is told so.
    """
    now = now or datetime.utcnow()
    tz = _tz_for(db, advisor, org, timezone)

    # ── idempotency: has this exact request already been answered? ──
    prior = find_prior_attempt(db, cred, external_ref)
    if prior is not None and prior.success and prior.appointment_id:
        return _replay(db, prior)
    # A prior FAILED attempt is not a reason to refuse a retry — it is the
    # reason retries exist. Reuse the row so the ref stays unique.
    row = prior

    duration = duration_minutes or (meeting_type.duration_minutes if meeting_type
                                    else None) or DEFAULT_DURATION_MINUTES
    ends_at = starts_at + timedelta(minutes=duration)

    if ends_at <= now:
        raise HTTPException(status_code=400, detail="That time is already in the past.")

    opp = None
    if opportunity_id:
        opp = (db.query(Opportunity)
               .filter(Opportunity.id == opportunity_id).first())
        if opp is None or opp.brand_sales_org_id != org.id:
            # Same answer for absent and out-of-brand.
            raise HTTPException(status_code=404, detail="Opportunity not found.")

    conflicts = av.find_conflicts(db, [advisor.id], starts_at, ends_at)
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail="That time is no longer available. Offer another opening.")

    base = meeting_type.name if meeting_type else "Appointment"
    title = "%s · %s" % (base, prospect_name) if prospect_name else base

    appt = SalesAppointment(
        brand_sales_org_id=org.id,
        opportunity_id=opp.id if opp else None,
        meeting_type_id=meeting_type.id if meeting_type else None,
        title=title,
        starts_at=starts_at, ends_at=ends_at,
        timezone=tz,
        status=APPT_SCHEDULED,
        prospect_name=prospect_name or (opp.contact_name if opp else None),
        prospect_company=(opp.company_name if opp else None),
        prospect_email=prospect_email or (opp.email if opp else None),
        prospect_phone=prospect_phone or (opp.phone if opp else None),
        prospect_timezone=prospect_timezone,
        confirmation_status=CONF_PENDING,
        notes=notes,
        created_by=advisor.id,
    )
    db.add(appt)
    db.flush()

    prof = av.get_or_create_profile(db, advisor)
    bs, be = av.buffered_window(prof, starts_at, ends_at)
    db.add(AppointmentParticipant(
        appointment_id=appt.id, user_id=advisor.id,
        role_slot=None, is_required=True,
        attendance_status=ATTEND_UNKNOWN,
        busy_start_at=bs, busy_end_at=be, is_blocking=True))

    if opp:
        db.add(OpportunityEvent(
            opportunity_id=opp.id, event_type="appointment_booked",
            summary="%s booked by %s" % (base, cred.name),
            detail="%s · %s" % (
                av.utc_to_local(starts_at, tz).strftime("%b %d, %Y %I:%M %p"),
                advisor.full_name),
            # No actor_user_id: a voice integration is not a member of staff,
            # and naming one here would misattribute the action.
            actor_user_id=None))

    row = audit(db, cred, ACTION_BOOK, success=False, status_code=0,
                detail="booking in progress", advisor_user_id=advisor.id,
                appointment_id=appt.id, external_ref=external_ref, row=row, now=now)

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        msg = str(e).lower()
        if "uq_integration_external_ref" in msg:
            # Two identical retries raced. The other one owns this ref.
            other = find_prior_attempt(db, cred, external_ref)
            if other is not None and other.appointment_id:
                return _replay(db, other)
            raise HTTPException(status_code=409,
                                detail="That booking reference is already in use.")
        if "sales_participant_no_overlap" in msg or "exclusion" in msg:
            raise HTTPException(
                status_code=409,
                detail="That time was taken moments ago. Offer another opening.")
        raise

    db.refresh(appt)

    # ── side effects, AFTER the commit ──
    # The same function the sales workspace uses, imported rather than copied so
    # a machine booking and a human booking cannot drift apart. Deferred import:
    # a service must not depend on a router at module load.
    try:
        from app.routers.sales_scheduling_router import _push_appointment
        _push_appointment(db, appt, advisor, kind="invite")
    except Exception:
        # Nothing here can un-book the meeting. Every failure inside is already
        # recorded on the appointment or participant rows.
        log.exception("integration booking side effects raised for %s", appt.id)

    db.refresh(appt)
    return {
        "success": True,
        "idempotent_replay": False,
        "appointment_id": appt.id,
        "starts_at": appt.starts_at.replace(microsecond=0).isoformat() + "Z",
        "ends_at": appt.ends_at.replace(microsecond=0).isoformat() + "Z",
        "timezone": appt.timezone,
        "starts_at_local": av.utc_to_local(appt.starts_at, appt.timezone)
                             .replace(microsecond=0).isoformat(),
        "title": appt.title,
        "advisor_id": advisor.id,
        "advisor_name": advisor.full_name,
        "confirmation_status": appt.confirmation_status,
        "message": "Booked.",
    }
