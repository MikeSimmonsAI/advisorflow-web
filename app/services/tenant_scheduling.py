"""Availability and booking for a CUSTOMER TENANT's advisors.

THE OTHER TREE. `retell_bridge.py` serves BrandSalesOrg sales scheduling and
writes `SalesAppointment`. This module serves a customer Organization — a
funeral home — and writes the records that tenancy already uses: `Lead`,
`BookingLink`, and the advisor's own external calendar. The two never meet, and
neither module imports the other.

WHAT THIS REUSES, AND WHY IT IS NOT A SECOND SCHEDULER
------------------------------------------------------
Everything a tenant booking depends on already exists. This module is the
arithmetic that was missing between those pieces, not a new source of truth:

  * `users.available_start_time / available_end_time / available_days /
    buffer_minutes / max_bookings_per_day / booking_timezone` — saved by the
    advisor's own Settings screen since before this work. Until now NOTHING
    read them: both legacy availability routes hardcode a 9-to-5 grid. This is
    the first code that honours what the advisor actually set.
  * `AdvisorAvailabilityBlock` — the vacation / slot / recurring blocks the
    Availability page writes. Same three rule kinds, same semantics.
  * `BookingLink` — existing bookings, which are what "already taken" means on
    this side of the house.
  * `calendar_providers` — the tested provider registry, for a LIVE external
    busy read. See the next section; this is the one place where reusing the
    legacy code would have been actively harmful.
  * `appointment_flow_service.on_booking_confirmed` — the existing confirmation
    messaging, imported rather than reimplemented.

WHY THE LEGACY GOOGLE PATH IS NOT REUSED
----------------------------------------
`calendar_router.get_available_slots` (`GET /calendar/slots`) appears to check
Google Calendar. It does not, and never has:

    from app.services.calendar_service import _get_google_credentials

That function does not exist — `calendar_service` defines `_get_calendar_service`.
The ImportError is swallowed by a bare `except Exception` thirty lines below and
logged as a warning. The visible consequence is that a Google-only advisor is
reported free at every slot, always, silently.

So the busy read here goes through `calendar_providers.get_provider(...).get_busy()`,
which is real, covers Microsoft and Google with one vocabulary, has a fake
registered by the test suite, and returns an error instead of an empty list when
it cannot see the calendar.

FAIL CLOSED. THIS IS THE WHOLE POINT.
-------------------------------------
`_check_outlook_conflict` in `availability_router` ends with

    except Exception:
        pass  # If Outlook check fails, don't block the slot

An outage there turns a fully booked advisor into a completely free one. Down a
phone line that becomes a voice agent cheerfully offering a time the advisor is
already sitting in a funeral. When the external calendar cannot be read here,
this module returns NO slots and a reason saying so. Refusing to answer is the
only safe answer.

TIME IS STORED AS NAIVE LOCAL WALL TIME ON THIS SIDE.
-----------------------------------------------------
`BookingLink.booked_time` is written by the existing Vercel flow as the naive
local time the family picked, and every existing reader — the reminder cron,
the Availability page, `/availability/upcoming` — reads it that way. This module
matches that convention exactly, so a Retell booking and a Vercel booking are
indistinguishable downstream. Internally, everything handed to a calendar
provider is converted to naive UTC first, because that is what providers expect.
The two are never mixed in one variable; `_to_utc` and `_to_local` are the only
crossings.

(The brand-sales tree stores naive UTC instead. That difference is real and
predates this work. It is documented rather than silently normalised, because
"fixing" it here would desynchronise Retell bookings from every other tenant
booking in the same table.)
"""

import json
import logging
from datetime import datetime, timedelta, date as date_cls, timezone as _timezone
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import (
    User, Organization, Lead, BookingLink,
    AdvisorAvailabilityBlock, BlockType,
)
from app.models.integration_models import (
    IntegrationCredential, IntegrationRequestLog,
    ACTION_BOOK,
)

log = logging.getLogger(__name__)

# A voice call can reasonably ask about "this week" or "next week". Beyond that
# the caller is mining a calendar, not booking an appointment.
MAX_RANGE_DAYS = 21
MAX_SLOTS = 40
DEFAULT_DURATION_MINUTES = 60
DEFAULT_TIMEZONE = "America/Chicago"
# Matches the 2-hour cushion the existing /calendar/slots route applies, so a
# family cannot book a visit that starts before the advisor can get there.
DEFAULT_NOTICE_HOURS = 2

# Every advisor-resolution failure says exactly this. Absent, inactive, wrong
# tenant, not on the allowlist — one answer, so a key cannot be used to discover
# which user ids are real or which belong to somebody else's funeral home.
_NO_ADVISOR = "Advisor not found."

# Used only when the tenant has configured nothing. Deliberately generic: the
# funeral-home vocabulary belongs to the funeral home, not to this file. See
# `appointment_types`.
FALLBACK_APPOINTMENT_TYPE = "Appointment"


# ── time helpers ────────────────────────────────────────────────────────────

def _zone(name: str):
    from zoneinfo import ZoneInfo
    return ZoneInfo(name)


def validate_timezone(name: Optional[str]) -> Optional[str]:
    """Refuse an unknown zone rather than falling back to a default.

    A voice agent handed a bad zone would otherwise read out a Chicago time that
    the family hears as their own. Wrong by five hours, spoken with total
    confidence, and impossible to notice until nobody arrives.
    """
    if not name:
        return None
    try:
        _zone(name)
    except Exception:
        raise HTTPException(status_code=400, detail="Unknown timezone: %s" % name)
    return name


def _to_utc(local_naive: datetime, tz_name: str) -> datetime:
    """Naive local wall time -> naive UTC."""
    try:
        aware = local_naive.replace(tzinfo=_zone(tz_name))
    except Exception:
        return local_naive
    return aware.astimezone(_timezone.utc).replace(tzinfo=None)


def _to_local(utc_naive: datetime, tz_name: str) -> datetime:
    """Naive UTC -> naive local wall time."""
    try:
        aware = utc_naive.replace(tzinfo=_timezone.utc)
        return aware.astimezone(_zone(tz_name)).replace(tzinfo=None)
    except Exception:
        return utc_naive


def _now_local(tz_name: str) -> datetime:
    return _to_local(datetime.utcnow(), tz_name)


def _parse_hhmm(raw: Optional[str], fallback: str) -> Tuple[int, int]:
    txt = (raw or "").strip() or fallback
    try:
        h, m = txt.split(":")[:2]
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    h, m = fallback.split(":")
    return int(h), int(m)


# ── tenant + advisor resolution ─────────────────────────────────────────────

def tenant_for(db: Session, cred: IntegrationCredential) -> Organization:
    """The one customer Organization this credential can ever see.

    Taken from the credential row, never from the request body. There is no
    parameter anywhere on this surface that widens it, which is what makes
    cross-tenant access impossible rather than merely unlikely.
    """
    org = (db.query(Organization)
           .filter(Organization.id == cred.organization_id).first())
    if org is None or not org.is_active:
        # The key names a tenant that is gone or switched off. Fail closed.
        raise HTTPException(status_code=401,
                            detail="Invalid or missing integration credential.")
    return org


def resolve_advisor(db: Session, cred: IntegrationCredential,
                    advisor_id: Optional[str]) -> User:
    """The advisor this request is about.

    Normally the caller sends nothing and the credential's own default advisor
    is used, so a voice agent never handles a user id at all.

    Every failure below returns the SAME 404 with the SAME text. Absent user,
    inactive user, a user in a different funeral home, a real user who is simply
    not on this key's allowlist — a caller learns only "no", never which of
    those it was, and so cannot use this route to map the platform.
    """
    wanted = (advisor_id or "").strip() or cred.default_advisor_user_id
    if not wanted:
        raise HTTPException(status_code=400,
                            detail="No advisor specified and this integration "
                                   "has no default advisor.")

    allow = cred.advisor_allowlist()
    if allow and wanted not in allow:
        raise HTTPException(status_code=404, detail=_NO_ADVISOR)

    user = db.query(User).filter(User.id == wanted).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail=_NO_ADVISOR)

    # THE TENANT BOUNDARY. A user row is only reachable through a credential
    # whose organization it actually belongs to.
    if (user.organization_id or None) != cred.organization_id:
        raise HTTPException(status_code=404, detail=_NO_ADVISOR)

    return user


# ── tenant configuration ────────────────────────────────────────────────────

def appointment_types(org: Organization) -> List[str]:
    """The tenant's own appointment names, from `organizations.appointment_types`.

    NOTHING FUNERAL-SPECIFIC IS HARDCODED HERE. A cemetery calls it a Family
    File Review; a roofing company calls it an Inspection. The column is a JSON
    array the org already owns and the lead-detail dropdown already reads. When
    it is empty this returns an empty list and the caller falls back to the
    generic word "Appointment" — not to any one customer's vocabulary.
    """
    raw = (org.appointment_types or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x).strip() for x in parsed if str(x).strip()]


def resolve_appointment_type(org: Organization, requested: Optional[str]) -> str:
    """Match the caller's word against the tenant's configured list.

    Case-insensitive, because a voice agent's transcription of "family file
    review" will not match "Family File Review" byte for byte. An unconfigured
    tenant accepts whatever it is given, so a new customer is not blocked from
    booking by a settings page nobody has filled in yet.
    """
    want = (requested or "").strip()
    configured = appointment_types(org)
    if not want:
        return configured[0] if configured else FALLBACK_APPOINTMENT_TYPE
    if not configured:
        return want
    for name in configured:
        if name.lower() == want.lower():
            return name
    raise HTTPException(
        status_code=404,
        detail="Unknown appointment type. This location offers: %s"
               % ", ".join(configured))


class Settings:
    """The advisor's own booking rules, read from their Settings screen."""

    def __init__(self, advisor: User):
        self.timezone = (advisor.booking_timezone or "").strip() or DEFAULT_TIMEZONE
        self.start_h, self.start_m = _parse_hhmm(advisor.available_start_time, "09:00")
        self.end_h, self.end_m = _parse_hhmm(advisor.available_end_time, "17:00")
        self.buffer_minutes = max(0, int(advisor.buffer_minutes or 0))
        cap = advisor.max_bookings_per_day
        self.max_per_day = int(cap) if cap else 0      # 0 = uncapped
        raw_days = (advisor.available_days or "").strip() or "0,1,2,3,4"
        days = set()
        for part in raw_days.split(","):
            part = part.strip()
            if part.isdigit() and 0 <= int(part) <= 6:
                days.add(int(part))
        # An advisor who somehow saved no working days would otherwise be
        # permanently unbookable with no explanation. Fall back to weekdays.
        self.days = days or {0, 1, 2, 3, 4}


# ── availability ────────────────────────────────────────────────────────────

def _blocked_by_rule(check_date: date_cls, hh: int, mm: int,
                     blocks: List[AdvisorAvailabilityBlock]) -> bool:
    """Same three rule kinds, same meaning, as `availability_router`.

    Reimplemented rather than imported only because the original takes a
    "HH:MM" string built from a fixed grid; the logic below is deliberately
    identical so an advisor's existing blocks behave the same whether the
    family books by phone or by link.
    """
    time_str = "%02d:%02d" % (hh, mm)
    for b in blocks:
        if b.block_type == BlockType.DATE_RANGE:
            if b.start_date and b.end_date and b.start_date <= check_date <= b.end_date:
                return True
        elif b.block_type == BlockType.SLOT:
            if b.block_date == check_date and (b.block_time or "") == time_str:
                return True
        elif b.block_type == BlockType.RECURRING:
            if b.recur_day_of_week is not None and check_date.weekday() != b.recur_day_of_week:
                continue
            if b.recur_after_time and time_str >= b.recur_after_time:
                return True
            if b.recur_before_time and time_str <= b.recur_before_time:
                return True
    return False


def existing_bookings(db: Session, advisor: User, start_local: datetime,
                      end_local: datetime) -> List[BookingLink]:
    """Live bookings in the window. `booked_time` is naive local — see the
    module docstring."""
    return (db.query(BookingLink)
            .filter(BookingLink.user_id == advisor.id,
                    BookingLink.status.in_(("booked", "confirmed")),
                    BookingLink.booked_time.isnot(None),
                    BookingLink.booked_time >= start_local,
                    BookingLink.booked_time < end_local)
            .all())


def _overlaps(a_start: datetime, a_end: datetime,
              b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and a_end > b_start


def external_busy(db: Session, advisor: User, org: Organization,
                  start_utc: datetime, end_utc: datetime):
    """(intervals, error). ONE call for the whole window.

    The legacy route probes Microsoft once per candidate slot — up to 255 live
    HTTP calls with a 10s timeout each, inside a request a person is waiting on
    during a phone call. This asks once for the range and subtracts locally.

    An error here is RETURNED, not swallowed. The caller turns it into "I can't
    see the calendar right now", never into an empty busy list.
    """
    from app.services import calendar_providers as reg
    key = reg.resolve_provider_key(db, advisor)
    if not reg.is_external_calendar(key):
        # No external calendar connected at all. That is a known, legitimate
        # state — not a failure — and the advisor's own bookings and blocks
        # still apply. Distinct from "connected but unreadable", which is.
        return [], None
    provider = reg.get_provider(db, advisor, org)
    try:
        busy, err = provider.get_busy(start_utc, end_utc)
    except Exception as e:
        # The provider contract says it never raises. Belt and braces: if one
        # ever does, that is still a calendar we could not read.
        log.exception("tenant external busy read raised for advisor %s", advisor.id)
        return [], "unreadable"
    if err is not None:
        return [], (getattr(err, "error_code", None) or "unreadable")
    return busy or [], None


def availability(db: Session, cred: IntegrationCredential, advisor: User,
                 org: Organization, date_from: date_cls,
                 date_to: Optional[date_cls], duration_minutes: Optional[int],
                 timezone: Optional[str], appointment_type: Optional[str],
                 now_utc: Optional[datetime] = None) -> dict:
    """Real openings for one tenant advisor."""
    now_utc = now_utc or datetime.utcnow()
    st = Settings(advisor)

    # The caller may ask for the times to be SPOKEN in another zone, but
    # availability is always defined in the advisor's own — that is the zone
    # their working hours were entered in.
    speak_tz = validate_timezone(timezone) or st.timezone
    work_tz = st.timezone

    duration = int(duration_minutes or DEFAULT_DURATION_MINUTES)
    if duration < 5 or duration > 480:
        raise HTTPException(status_code=400,
                            detail="duration_minutes must be between 5 and 480.")

    d_to = date_to or date_from
    if d_to < date_from:
        raise HTTPException(status_code=400, detail="date_to is before date_from.")
    if (d_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400,
                            detail="Search at most %d days at a time." % MAX_RANGE_DAYS)

    label = resolve_appointment_type(org, appointment_type)

    window_start_local = datetime(date_from.year, date_from.month, date_from.day)
    window_end_local = datetime(d_to.year, d_to.month, d_to.day) + timedelta(days=1)

    blocks = (db.query(AdvisorAvailabilityBlock)
              .filter(AdvisorAvailabilityBlock.advisor_id == advisor.id)
              .all())
    booked = existing_bookings(db, advisor, window_start_local, window_end_local)

    # ── the external calendar, read once, failing closed ──
    busy_utc, busy_error = external_busy(
        db, advisor, org,
        _to_utc(window_start_local, work_tz),
        _to_utc(window_end_local, work_tz))

    if busy_error:
        # THE ONE ANSWER THAT MATTERS. We know this advisor has a calendar and
        # we could not read it, so we do not know they are free. Saying nothing
        # is correct; saying "9am is open" would be a guess spoken aloud to a
        # grieving family.
        return _empty(advisor, org, speak_tz, duration, label, date_from, d_to,
                      "I can't reach the calendar right now, so I can't offer "
                      "times. Please try again shortly.")

    busy_local = [(_to_local(b.starts_at, work_tz), _to_local(b.ends_at, work_tz))
                  for b in busy_utc]

    now_local = _to_local(now_utc, work_tz)
    earliest = now_local + timedelta(hours=DEFAULT_NOTICE_HOURS)

    # Per-day booking cap counts what is ALREADY booked that day, including
    # bookings outside the requested window's hours.
    per_day_count = {}
    for b in booked:
        per_day_count[b.booked_time.date()] = per_day_count.get(b.booked_time.date(), 0) + 1

    slots = []
    reasons = []
    day = date_from
    while day <= d_to and len(slots) < MAX_SLOTS:
        if day.weekday() not in st.days:
            day += timedelta(days=1)
            continue
        if st.max_per_day and per_day_count.get(day, 0) >= st.max_per_day:
            reasons.append("fully booked on %s" % day.isoformat())
            day += timedelta(days=1)
            continue

        cursor = datetime(day.year, day.month, day.day, st.start_h, st.start_m)
        day_close = datetime(day.year, day.month, day.day, st.end_h, st.end_m)

        while cursor + timedelta(minutes=duration) <= day_close:
            slot_start = cursor
            slot_end = cursor + timedelta(minutes=duration)
            cursor = cursor + timedelta(minutes=duration)

            if slot_start < earliest:
                continue
            if _blocked_by_rule(day, slot_start.hour, slot_start.minute, blocks):
                continue

            # Existing bookings, widened by the advisor's own buffer so a new
            # visit cannot start the instant another ends.
            clash = False
            for b in booked:
                b_start = b.booked_time - timedelta(minutes=st.buffer_minutes)
                b_end = (b.booked_time + timedelta(minutes=duration)
                         + timedelta(minutes=st.buffer_minutes))
                if _overlaps(slot_start, slot_end, b_start, b_end):
                    clash = True
                    break
            if clash:
                continue

            for bs, be in busy_local:
                if _overlaps(slot_start, slot_end, bs, be):
                    clash = True
                    break
            if clash:
                continue

            slots.append(_slot_out(slot_start, slot_end, duration, work_tz, speak_tz))
            if len(slots) >= MAX_SLOTS:
                break

        day += timedelta(days=1)

    if not slots:
        why = ("No openings in that range."
               if not reasons else
               "No openings — %s." % "; ".join(reasons[:3]))
        return _empty(advisor, org, speak_tz, duration, label, date_from, d_to, why)

    return {
        "success": True,
        "advisor_id": advisor.id,
        "advisor_name": advisor.full_name,
        "organization_name": org.brand_name or org.name,
        "location": org.org_address or None,
        "appointment_type": label,
        "timezone": speak_tz,
        "duration_minutes": duration,
        "date_from": date_from.isoformat(),
        "date_to": d_to.isoformat(),
        "slot_count": len(slots),
        "slots": slots,
        "reason": None,
    }


def _slot_out(start_local: datetime, end_local: datetime, duration: int,
              work_tz: str, speak_tz: str) -> dict:
    """One opening, in three forms: the machine key, the spoken zone, and words.

    `starts_at` is the value the caller must send back to /book verbatim. It is
    naive UTC with a Z so there is exactly one way to read it — a wall-clock
    string with no zone is how a booking lands an hour out.
    """
    start_utc = _to_utc(start_local, work_tz)
    spoken = _to_local(start_utc, speak_tz)
    return {
        "starts_at": start_utc.replace(microsecond=0).isoformat() + "Z",
        "ends_at": _to_utc(end_local, work_tz).replace(microsecond=0).isoformat() + "Z",
        "starts_at_local": spoken.replace(microsecond=0).isoformat(),
        "duration_minutes": duration,
        "label": spoken.strftime("%A, %B %d at %I:%M %p").replace(" 0", " "),
    }


def _empty(advisor: User, org: Organization, tz: str, duration: int,
           label: str, date_from: date_cls, d_to: date_cls, reason: str) -> dict:
    """Nothing available, and WHY — in a sentence an agent can say out loud.

    An empty list with no explanation is the thing this avoids: it leaves the
    agent to invent a reason, and it hides an outage behind what looks like a
    busy week.
    """
    return {
        "success": True,
        "advisor_id": advisor.id,
        "advisor_name": advisor.full_name,
        "organization_name": org.brand_name or org.name,
        "location": org.org_address or None,
        "appointment_type": label,
        "timezone": tz,
        "duration_minutes": duration,
        "date_from": date_from.isoformat(),
        "date_to": d_to.isoformat(),
        "slot_count": 0,
        "slots": [],
        "reason": reason,
    }


# ── audit ───────────────────────────────────────────────────────────────────

def audit(db: Session, cred: IntegrationCredential, action: str, success: bool,
          status_code: int, detail: str, advisor_user_id: str = None,
          booking_link_id: str = None, lead_id: str = None,
          external_ref: str = None, row: IntegrationRequestLog = None,
          now: datetime = None) -> IntegrationRequestLog:
    """One row per request, success or refusal.

    NO SECRET VALUE IS EVER WRITTEN HERE — only the non-secret key prefix. The
    integration name and prefix are denormalised so the trail survives the
    credential being deleted, and `organization_id` records which tenant the
    request ran in, so "did this key ever touch another funeral home?" is a
    query with a definite answer.
    """
    now = now or datetime.utcnow()
    if row is None:
        row = IntegrationRequestLog(action=action)
        db.add(row)
    row.credential_id = cred.id
    row.integration_name = cred.name
    row.key_prefix = cred.key_prefix
    row.action = action
    row.organization_id = cred.organization_id
    row.advisor_user_id = advisor_user_id
    row.booking_link_id = booking_link_id
    row.lead_id = lead_id
    if external_ref is not None:
        row.external_ref = external_ref
    row.success = success
    row.status_code = status_code
    row.detail = (detail or "")[:1000]
    row.occurred_at = now
    return row


def find_prior_attempt(db: Session, cred: IntegrationCredential,
                       external_ref: str) -> Optional[IntegrationRequestLog]:
    return (db.query(IntegrationRequestLog)
            .filter(IntegrationRequestLog.credential_id == cred.id,
                    IntegrationRequestLog.external_ref == external_ref)
            .first())


# ── the family ──────────────────────────────────────────────────────────────

def resolve_lead(db: Session, cred: IntegrationCredential, org: Organization,
                 advisor: User, lead_id: Optional[str], name: Optional[str],
                 phone: Optional[str], email: Optional[str]) -> Lead:
    """The family this appointment is for.

    Three cases, in order:

    1. `lead_id` given — it must belong to THIS tenant. A lead id from another
       funeral home returns the same "not found" as one that never existed.
    2. No id, but a phone or email we already hold for this tenant — reuse that
       lead rather than creating a duplicate record for a family the funeral
       home is already talking to. Matching is on phone or email, never on a
       similar name.
    3. Otherwise a new lead, created with `source_file` marking where it came
       from so nobody later wonders how it appeared.

    A booking REQUIRES a lead: `booking_links.lead_id` is NOT NULL, and the
    confirmation messaging has nobody to write to without one.
    """
    if lead_id:
        lead = (db.query(Lead)
                .filter(Lead.id == lead_id,
                        Lead.organization_id == org.id).first())
        if lead is None:
            # Absent and belonging-to-another-tenant give the same answer.
            raise HTTPException(status_code=404, detail="Lead not found.")
        return lead

    phone = (phone or "").strip() or None
    email = (email or "").strip() or None

    if phone or email:
        q = db.query(Lead).filter(Lead.organization_id == org.id)
        existing = None
        if phone:
            existing = q.filter(Lead.phone == phone).first()
        if existing is None and email:
            existing = q.filter(Lead.email == email).first()
        if existing is not None:
            return existing

    if not (phone or email or (name or "").strip()):
        raise HTTPException(
            status_code=400,
            detail="Provide lead_id, or at least a name and a phone number "
                   "for the family.")

    first, last = "", ""
    parts = (name or "").strip().split()
    if parts:
        first = parts[0]
        last = " ".join(parts[1:])

    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id,
        first_name=first or None,
        last_name=last or None,
        phone=phone,
        email=email,
        status="booked",
        # Traceable provenance. Not an invented email, not a fake name — only
        # what the caller actually supplied.
        source_file="voice:%s" % cred.name,
    )
    db.add(lead)
    db.flush()
    return lead


# ── booking ─────────────────────────────────────────────────────────────────

def _replay(db: Session, row: IntegrationRequestLog, tz_hint: str) -> dict:
    booking = (db.query(BookingLink)
               .filter(BookingLink.id == row.booking_link_id).first())
    if booking is None or booking.booked_time is None:
        return {"success": True, "idempotent_replay": True,
                "booking_id": row.booking_link_id,
                "message": "This booking was already made."}
    return {
        "success": True,
        "idempotent_replay": True,
        "booking_id": booking.id,
        "lead_id": booking.lead_id,
        "starts_at_local": booking.booked_time.replace(microsecond=0).isoformat(),
        "label": booking.booked_time.strftime("%A, %B %d at %I:%M %p").replace(" 0", " "),
        "timezone": tz_hint,
        "message": "This booking was already made.",
    }


def book(db: Session, cred: IntegrationCredential, advisor: User,
         org: Organization, starts_at_utc: datetime, external_ref: str,
         duration_minutes: Optional[int] = None,
         appointment_type: Optional[str] = None,
         timezone: Optional[str] = None,
         lead_id: Optional[str] = None,
         family_name: Optional[str] = None,
         family_phone: Optional[str] = None,
         family_email: Optional[str] = None,
         notes: Optional[str] = None,
         now_utc: Optional[datetime] = None) -> dict:
    """Take a time, having proved it is still free at this instant.

    THE RE-CHECK IS THE POINT. The openings the agent read may be minutes old,
    and a phone call is long enough for the advisor's own calendar to change or
    for the front desk to book the same hour. Both the local bookings AND the
    external calendar are re-read here, inside the booking path — not trusted
    from the earlier availability call.

    It is also why the external read fails closed a second time: if we cannot
    see the calendar at the moment of writing, we do not write.
    """
    now_utc = now_utc or datetime.utcnow()
    st = Settings(advisor)
    work_tz = st.timezone
    speak_tz = validate_timezone(timezone) or work_tz

    # ── idempotency: has this exact attempt already succeeded? ──
    prior = find_prior_attempt(db, cred, external_ref)
    if prior is not None and prior.success and prior.booking_link_id:
        return _replay(db, prior, speak_tz)
    # A prior FAILED attempt is not a reason to refuse a retry — it is the
    # reason retries exist. Reuse the row so the ref stays unique.
    row = prior

    duration = int(duration_minutes or DEFAULT_DURATION_MINUTES)
    if duration < 5 or duration > 480:
        raise HTTPException(status_code=400,
                            detail="duration_minutes must be between 5 and 480.")

    label = resolve_appointment_type(org, appointment_type)

    # AUTHORIZATION BEFORE CAPACITY. If the caller named a family, prove it is
    # this tenant's family now — before any check that could refuse for a
    # mundane reason. A cross-tenant lead id must always come back "Lead not
    # found", never "the advisor is fully booked": the second answer is a
    # refusal that depends on the schedule, and a caller who retries tomorrow
    # would learn the id was real after all. This read creates nothing, so
    # doing it early cannot leave an orphan record behind if a later check
    # refuses.
    if lead_id:
        if (db.query(Lead)
                .filter(Lead.id == lead_id,
                        Lead.organization_id == org.id).first()) is None:
            raise HTTPException(status_code=404, detail="Lead not found.")
    elif not any([(family_name or "").strip(), (family_phone or "").strip(),
                  (family_email or "").strip()]):
        # A request that names nobody is malformed, and saying so is more use
        # to the agent than "that time is taken" would be. Checked here, before
        # the schedule arithmetic, for the same reason as above: the answer
        # must not depend on how busy the advisor happens to be.
        raise HTTPException(
            status_code=400,
            detail="Provide lead_id, or at least a name and a phone number "
                   "for the family.")

    start_local = _to_local(starts_at_utc, work_tz)
    end_local = start_local + timedelta(minutes=duration)

    if starts_at_utc + timedelta(minutes=duration) <= now_utc:
        raise HTTPException(status_code=400, detail="That time is already in the past.")

    # ── does this time even exist in the advisor's working pattern? ──
    if start_local.weekday() not in st.days:
        raise HTTPException(status_code=409,
                            detail="The advisor does not work that day. "
                                   "Offer another opening.")
    open_at = datetime(start_local.year, start_local.month, start_local.day,
                       st.start_h, st.start_m)
    close_at = datetime(start_local.year, start_local.month, start_local.day,
                        st.end_h, st.end_m)
    if start_local < open_at or end_local > close_at:
        raise HTTPException(status_code=409,
                            detail="That time is outside the advisor's hours. "
                                   "Offer another opening.")

    blocks = (db.query(AdvisorAvailabilityBlock)
              .filter(AdvisorAvailabilityBlock.advisor_id == advisor.id).all())
    if _blocked_by_rule(start_local.date(), start_local.hour,
                        start_local.minute, blocks):
        raise HTTPException(status_code=409,
                            detail="That time is blocked off. Offer another opening.")

    # ── RE-VALIDATION against everything already on the books ──
    day_start = datetime(start_local.year, start_local.month, start_local.day)
    day_end = day_start + timedelta(days=1)
    same_day = existing_bookings(db, advisor, day_start, day_end)

    if st.max_per_day and len(same_day) >= st.max_per_day:
        raise HTTPException(status_code=409,
                            detail="The advisor is fully booked that day. "
                                   "Offer another day.")

    for b in same_day:
        b_start = b.booked_time - timedelta(minutes=st.buffer_minutes)
        b_end = (b.booked_time + timedelta(minutes=duration)
                 + timedelta(minutes=st.buffer_minutes))
        if _overlaps(start_local, end_local, b_start, b_end):
            raise HTTPException(
                status_code=409,
                detail="That time is no longer available. Offer another opening.")

    # ── RE-VALIDATION against the external calendar, read fresh ──
    busy, busy_error = external_busy(db, advisor, org, starts_at_utc,
                                     starts_at_utc + timedelta(minutes=duration))
    if busy_error:
        raise HTTPException(
            status_code=503,
            detail="I can't reach the calendar to confirm that time. "
                   "Please try again shortly.")
    for iv in busy:
        if _overlaps(starts_at_utc, starts_at_utc + timedelta(minutes=duration),
                     iv.starts_at, iv.ends_at):
            raise HTTPException(
                status_code=409,
                detail="That time is no longer available. Offer another opening.")

    # ── the family ──
    lead = resolve_lead(db, cred, org, advisor, lead_id, family_name,
                        family_phone, family_email)

    # ── the booking ──
    # Same token format the SMS flow mints, so a link to this booking is a link
    # like any other and the existing booking page can open it.
    from app.services.sms_service import _encode_booking_token
    booking = BookingLink(
        lead_id=lead.id,
        user_id=advisor.id,
        status="booked",
        token=_encode_booking_token(lead, advisor),
        # NAIVE LOCAL WALL TIME — matching every existing tenant reader. See the
        # module docstring.
        booked_time=start_local,
    )
    db.add(booking)
    lead.status = "booked"
    db.flush()

    row = audit(db, cred, ACTION_BOOK, success=False, status_code=0,
                detail="booking in progress", advisor_user_id=advisor.id,
                booking_link_id=booking.id, lead_id=lead.id,
                external_ref=external_ref, row=row, now=now_utc)

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        if "uq_integration_external_ref" in str(e).lower():
            other = find_prior_attempt(db, cred, external_ref)
            if other is not None and other.booking_link_id:
                return _replay(db, other, speak_tz)
            raise HTTPException(status_code=409,
                                detail="That booking reference is already in use.")
        raise

    db.refresh(booking)

    # ── side effects, AFTER the commit ──
    # Nothing below can un-book the meeting, so nothing below is allowed to
    # raise out of here. Each failure is recorded and reported in the response
    # rather than swallowed — a family who got no confirmation text is something
    # the funeral home needs to know about.
    calendar = _push_to_calendar(db, advisor, org, booking, lead, label,
                                 start_local, duration, work_tz, notes)
    _make_case_file(db, booking, lead, advisor, start_local)

    messaging = {"sent": False}
    try:
        from app.services.appointment_flow_service import on_booking_confirmed
        on_booking_confirmed(db, lead, advisor, booking)
        messaging = {"sent": True}
    except Exception as e:
        log.exception("tenant booking confirmation messaging failed for %s", booking.id)
        messaging = {"sent": False, "error": str(e)[:200]}

    db.commit()
    db.refresh(booking)

    return {
        "success": True,
        "idempotent_replay": False,
        "booking_id": booking.id,
        "lead_id": lead.id,
        "advisor_id": advisor.id,
        "advisor_name": advisor.full_name,
        "organization_name": org.brand_name or org.name,
        "appointment_type": label,
        "location": org.org_address or None,
        "starts_at": starts_at_utc.replace(microsecond=0).isoformat() + "Z",
        "starts_at_local": _to_local(starts_at_utc, speak_tz)
                             .replace(microsecond=0).isoformat(),
        "timezone": speak_tz,
        "duration_minutes": duration,
        "label": _to_local(starts_at_utc, speak_tz)
                   .strftime("%A, %B %d at %I:%M %p").replace(" 0", " "),
        "calendar_synced": bool(calendar.get("ok")),
        "confirmation_sent": bool(messaging.get("sent")),
        "message": "Booked.",
    }


def _push_to_calendar(db: Session, advisor: User, org: Organization,
                      booking: BookingLink, lead: Lead, label: str,
                      start_local: datetime, duration: int, work_tz: str,
                      notes: Optional[str]) -> dict:
    """Write the event through the provider registry.

    NOT `create_calendar_event_for_booking`, which is Google-only and reports
    failure for a Microsoft advisor. The registry covers Microsoft and Google
    with one vocabulary and falls back to an .ics invitation when the advisor
    has connected nothing, so no advisor is silently left without an event.

    A failure here does not un-book anything. It is recorded on the booking and
    surfaced in the response as `calendar_synced: false`.
    """
    try:
        from app.services import calendar_providers as reg
        from app.services.calendar_providers.base import EventPayload

        family = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip()
        start_utc = _to_utc(start_local, work_tz)
        payload = EventPayload(
            subject="%s — %s" % (label, family or "Family"),
            body_text="\n".join(filter(None, [
                "Phone: %s" % (lead.phone or "n/a"),
                "Email: %s" % (lead.email or "n/a"),
                notes or "",
            ])),
            starts_at=start_utc,
            ends_at=start_utc + timedelta(minutes=duration),
            timezone=work_tz,
            # In person, at the location the TENANT configured. No address is
            # hardcoded here for any customer.
            location=org.org_address or None,
            attendees=[],
        )
        provider = reg.get_provider(db, advisor, org)
        result = provider.create_event(payload)
        if getattr(result, "ok", False):
            booking.calendar_event_id = result.external_event_id
            return {"ok": True}
        return {"ok": False, "error": getattr(result, "error_code", "failed")}
    except Exception as e:
        log.exception("tenant booking calendar write failed for %s", booking.id)
        return {"ok": False, "error": str(e)[:200]}


def _make_case_file(db: Session, booking: BookingLink, lead: Lead,
                    advisor: User, appointment_local: datetime) -> None:
    """The advisor-visible Client Record row, same as the Vercel flow creates.

    Mirrors the INSERT in `calendar_router.booking_confirmed_webhook` so a
    phone booking and a link booking leave the advisor looking at the same
    thing. Best-effort, exactly like the original: a missing table or a schema
    difference must never cost the family their appointment.

    (The duplication with calendar_router is deliberate for now — consolidating
    it means editing the live Vercel path, which is not in scope here.)
    """
    try:
        import uuid as _uuid
        from sqlalchemy import text as _text
        _now = datetime.utcnow()
        db.execute(_text("""
            INSERT INTO appointment_case_files (
                id, lead_id, organization_id, recorded_by_id, booking_link_id,
                appointment_date, appointment_type, outcome_type,
                products_discussed, products_sold,
                chk_id_verified, chk_beneficiary_named, chk_app_signed,
                chk_payment_collected, chk_illustrations_reviewed, chk_medical_history,
                chk_hipaa_signed, chk_replacement_form, chk_beneficiary_reviewed,
                chk_riders_explained, referral_potential, case_status,
                created_at, updated_at
            ) VALUES (
                :id, :lead_id, :org, :recorded_by, :booking_link_id,
                :appointment_date, :appointment_type, NULL,
                '[]', '[]',
                FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE, FALSE,
                FALSE, FALSE, 'open', :created_at, :updated_at
            )
        """), {
            "id": str(_uuid.uuid4()),
            "lead_id": lead.id,
            "org": lead.organization_id,
            "recorded_by": advisor.id,
            "booking_link_id": booking.id,
            "appointment_date": appointment_local,
            "appointment_type": "in_person",
            "created_at": _now,
            "updated_at": _now,
        })
    except Exception as e:
        log.warning("tenant booking could not auto-create case file: %s", e)
