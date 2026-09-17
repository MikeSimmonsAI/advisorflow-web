"""
The public Discovery / Demo booking API.

THIS IS THE CONTRACT THE PUBLIC WEBSITE CONSUMES. The website is built and
deployed separately; this router is the only thing it talks to, and the shapes
below are what it may rely on.

    GET  /public-booking/{platform_slug}/meeting      what is on offer + form schema
    GET  /public-booking/{platform_slug}/slots        bookable times
    POST /public-booking/{platform_slug}/book         take the booking

THE SECURITY POSTURE, STATED ONCE
---------------------------------
These three endpoints are unauthenticated and reachable by anyone. Everything
that decides WHO a booking involves is therefore resolved server-side:

    the brand         from the path slug, verified against a configured platform
    the salesperson   from an opaque code, resolved against THAT brand only
    the leadership    from the brand's own org chart
    the participants  from the availability engine, at the moment of booking
    the meeting type  from the brand's public_bookable types only

The request body carries prospect details, a chosen time, and an idempotency
key. NOTHING ELSE IN IT AFFECTS WHO IS INVOLVED. There is deliberately no field
for an organization id, a brand sales org id, a user id, a participant list, a
meeting URL or an appointment id - not validated-and-rejected, simply absent, so
there is nothing to smuggle.

WHAT THE RESPONSES WITHHOLD
---------------------------
No internal user ids, no names of people who are not the assigned salesperson,
no calendar event titles, no indication of WHY a time is unavailable, and one
identical refusal message for every kind of bad booking code. Availability is
the only thing a visitor learns, and a visitor able to diff two reps' free time
is a visitor mapping the sales team's week.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.deps import get_db
from app.limiter import limiter
from app.models.scheduling_models import DEFAULT_TIMEZONE
from app.services import availability as av
from app.services import leadership_chain as lc
from app.services import public_booking as pb
from app.services import public_intake
from app.services import sales_booking_codes as codes

log = logging.getLogger(__name__)

router = APIRouter(prefix="/public-booking", tags=["public-booking"])

# How far ahead a visitor may look in one request. Bounded because an
# unbounded range is a free way to make the server compute a year of
# availability for four people, and because no prospect books eleven months out.
MAX_WINDOW_DAYS = 60
DEFAULT_WINDOW_DAYS = 21

# ONE MESSAGE FOR EVERY CONFIGURATION PROBLEM, facing the public. A visitor does
# not need to know whether a rep has no manager or the brand has no default
# owner, and telling them is telling an outsider about the inside.
PUBLIC_UNAVAILABLE = ("Online booking is not available right now. Please contact "
                      "us and we will arrange a time.")


# ── resolution shared by all three endpoints ────────────────────────────────

def _resolve(db: Session, platform_slug: str, request: Request):
    """(platform, intake org, brand sales org) or a 404/503.

    The brand comes from the PATH, verified against a configured platform, with
    the Origin header as the secondary signal - exactly as every other public
    intake endpoint on this platform resolves its destination. The browser never
    names an organization.
    """
    try:
        platform, org = public_intake.resolve_public_intake(
            db, platform_slug=platform_slug,
            origin=request.headers.get("origin"))
    except public_intake.IntakeDestinationError as exc:
        log.info("public booking: unresolved destination: %s", exc)
        raise HTTPException(status_code=404, detail="Unknown site.")

    bso = pb.brand_sales_org_for_platform(db, platform)
    if bso is None:
        raise HTTPException(status_code=503, detail=PUBLIC_UNAVAILABLE)
    return platform, org, bso


def _meeting_type_or_503(db: Session, bso, key: Optional[str]):
    mt = pb.resolve_meeting_type(db, bso, key)
    if mt is None:
        raise HTTPException(status_code=503, detail=PUBLIC_UNAVAILABLE)
    return mt


def _owner_or_refuse(db: Session, bso, code: Optional[str]):
    """The salesperson, or an HTTP refusal that says nothing useful to a prober.

    A BAD CODE IS 404 AND A MISSING CONFIGURATION IS 503, deliberately: the
    first is something the visitor can fix by going to the main booking page,
    the second is something only the brand can fix. Both carry a generic
    message; the specific reason is logged, not returned.
    """
    resolved = codes.resolve_inbound_owner(db, bso, code)
    if resolved["ok"]:
        return resolved
    log.info("public booking: owner unresolved (%s) for brand %s",
             resolved.get("status"), bso.id)
    if code:
        raise HTTPException(status_code=404, detail=codes.PUBLIC_REFUSAL)
    raise HTTPException(status_code=503, detail=PUBLIC_UNAVAILABLE)


def _window(db: Session, mt, from_date: Optional[str], to_date: Optional[str],
            now: datetime) -> tuple:
    """The UTC range to search, clamped.

    Dates rather than instants because a visitor picks days. Parsed leniently
    and clamped hard: a malformed date becomes the default window instead of a
    500 on a public page.
    """
    def _parse(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)[:10]).date()
        except Exception:                                        # noqa: BLE001
            return None

    start = _parse(from_date) or now.date()
    end = _parse(to_date) or (start + timedelta(days=DEFAULT_WINDOW_DAYS))
    if end < start:
        end = start + timedelta(days=DEFAULT_WINDOW_DAYS)
    if (end - start).days > MAX_WINDOW_DAYS:
        end = start + timedelta(days=MAX_WINDOW_DAYS)
    return (datetime.combine(start, datetime.min.time()),
            datetime.combine(end, datetime.max.time()))


def _meeting_public_out(platform, bso, mt, owner, owner_source) -> dict:
    """What a stranger may know about the meeting they are booking.

    The salesperson's NAME is included and their email and id are not. The name
    is the point - a prospect following somebody's personal link expects to see
    that person - and it is already public in the signature that carried the
    link. The id is what would let somebody enumerate the team.

    NO LEADERSHIP IS NAMED. Who else attends is decided by the org chart and the
    calendar, and a public page that listed a rep's managers would publish the
    reporting structure of the company.
    """
    return {
        "brand": {"name": platform.name, "slug": platform.slug},
        "meeting": {
            "key": mt.key,
            "name": mt.name,
            "description": mt.description,
            "duration_minutes": mt.duration_minutes,
            "is_video": bool(mt.requires_video),
            "timezone": bso.timezone or DEFAULT_TIMEZONE,
        },
        "salesperson": ({"name": owner.full_name} if owner is not None else None),
        "assigned_via": ("link" if owner_source == codes.OWNER_FROM_CODE
                         else "general"),
    }


# ══════════════════════════════════════════════════════════════════════════
# 1. RESOLVE — what is on offer, and what the form should ask
# ══════════════════════════════════════════════════════════════════════════

@router.get("/{platform_slug}/meeting")
@limiter.shared_limit(public_intake.PUBLIC_INTAKE_LIMIT,
                      scope=public_intake.PUBLIC_INTAKE_SCOPE)
def public_meeting(platform_slug: str, request: Request,
                   code: Optional[str] = Query(None),
                   meeting_type: Optional[str] = Query(None),
                   timezone: Optional[str] = Query(None),
                   db: Session = Depends(get_db)):
    """Everything the booking page needs before it draws anything.

    Returns the meeting on offer, who it is with, the form schema, and whether
    bookings can be taken at all - so the page can show an honest "contact us"
    state instead of an empty calendar when a brand is misconfigured.
    """
    platform, _org, bso = _resolve(db, platform_slug, request)
    mt = _meeting_type_or_503(db, bso, meeting_type)
    resolved = _owner_or_refuse(db, bso, code)
    owner = resolved["owner"]

    # Whether this rep CAN be booked, without computing any availability.
    # Distinguishing "no configuration" from "a busy fortnight" here means the
    # page can say the right thing immediately.
    from app.services import leadership_quorum as lq
    policy = lq.QuorumPolicy.from_meeting_type(mt)
    chain = lc.resolve(db, owner.id, bso.id, policy.depth if policy.active else 0)
    bookable = chain.ok and (not policy.active or chain.satisfies(policy.minimum))

    out = _meeting_public_out(platform, bso, mt, owner, resolved["source"])
    out["bookable"] = bool(bookable)
    out["form"] = pb.form_schema()
    out["visitor_timezone"] = pb.valid_timezone(timezone)
    out["types"] = [{"key": t.key, "name": t.name,
                     "duration_minutes": t.duration_minutes}
                    for t in pb.public_meeting_types(db, bso)]
    if not bookable:
        # The REASON is deliberately not the chain's internal message, which
        # names people and describes the company's org chart.
        out["message"] = PUBLIC_UNAVAILABLE
    return out


# ══════════════════════════════════════════════════════════════════════════
# 2. AVAILABILITY
# ══════════════════════════════════════════════════════════════════════════

@router.get("/{platform_slug}/slots")
@limiter.shared_limit(public_intake.PUBLIC_INTAKE_LIMIT,
                      scope=public_intake.PUBLIC_INTAKE_SCOPE)
def public_slots(platform_slug: str, request: Request,
                 code: Optional[str] = Query(None),
                 meeting_type: Optional[str] = Query(None),
                 timezone: Optional[str] = Query(None),
                 from_date: Optional[str] = Query(None, alias="from"),
                 to_date: Optional[str] = Query(None, alias="to"),
                 db: Session = Depends(get_db)):
    """Bookable times. Only times where the quorum is satisfiable are returned.

    Every returned slot is one the platform is prepared to honour at the moment
    it was computed. It re-checks at booking anyway, because a visitor can leave
    this page open for an hour.
    """
    platform, _org, bso = _resolve(db, platform_slug, request)
    mt = _meeting_type_or_503(db, bso, meeting_type)
    resolved = _owner_or_refuse(db, bso, code)
    owner = resolved["owner"]

    now = datetime.utcnow()
    start_utc, end_utc = _window(db, mt, from_date, to_date, now)
    visitor_tz = pb.valid_timezone(timezone)
    meeting_tz = bso.timezone or DEFAULT_TIMEZONE

    found = pb.public_slots(db, bso, mt, owner, start_utc, end_utc, now_utc=now)

    out = _meeting_public_out(platform, bso, mt, owner, resolved["source"])
    out["slots"] = pb.public_slot_payload(found, visitor_tz, meeting_tz)
    out["visitor_timezone"] = visitor_tz
    out["window"] = {"from": start_utc.date().isoformat(),
                     "to": end_utc.date().isoformat()}
    if not out["slots"]:
        # ONE MESSAGE WHATEVER THE CAUSE. Whether the rep's chain is
        # misconfigured or their fortnight is simply full is an internal fact;
        # `found["reason"]` names people and never leaves the server.
        out["message"] = ("There are no open times in this range. Try a later "
                          "date range, or contact us and we will arrange one.")
    return out


# ══════════════════════════════════════════════════════════════════════════
# 3. BOOK
# ══════════════════════════════════════════════════════════════════════════

class BookRequest(BaseModel):
    """What the website may send. Note what is NOT here.

    There is no organization_id, brand_sales_org_id, salesperson user id,
    participant list, meeting URL or appointment id. They are absent rather than
    rejected, so there is no field to probe and nothing to smuggle - the server
    derives every one of those itself.
    """
    model_config = ConfigDict(extra="ignore")

    # identity of the booking
    code: Optional[str] = Field(None, max_length=128)
    meeting_type: Optional[str] = Field(None, max_length=64)
    start_utc: str = Field(..., max_length=40)

    # the prospect
    full_name: str = Field(..., max_length=200)
    company: str = Field(..., max_length=200)
    email: str = Field(..., max_length=200)
    phone: Optional[str] = Field(None, max_length=60)
    industry: Optional[str] = Field(None, max_length=120)
    primary_challenge: Optional[str] = Field(None, max_length=64)
    primary_challenge_detail: Optional[str] = Field(None, max_length=1000)

    # optional context
    current_system: Optional[str] = Field(None, max_length=200)
    locations: Optional[str] = Field(None, max_length=120)
    lead_volume: Optional[str] = Field(None, max_length=120)
    notes: Optional[str] = Field(None, max_length=2000)

    timezone: Optional[str] = Field(None, max_length=64)
    # THE RETRY GUARD. Generated by the page, stable across resubmissions of the
    # same form, different for a genuinely new booking.
    submission_id: Optional[str] = Field(None, max_length=128)

    page_url: Optional[str] = Field(None, max_length=500)
    referrer: Optional[str] = Field(None, max_length=500)


@router.post("/{platform_slug}/book", status_code=201)
@limiter.shared_limit(public_intake.PUBLIC_INTAKE_LIMIT,
                      scope=public_intake.PUBLIC_INTAKE_SCOPE)
def public_book(platform_slug: str, payload: BookRequest, request: Request,
                db: Session = Depends(get_db)):
    """Take the booking.

    Returns 201 for a new booking and 200 for a replay of one already made, so
    the page can tell the two apart without either being an error. A time that
    has gone is 409 with an actionable message; a misconfiguration is 503.
    """
    platform, org, bso = _resolve(db, platform_slug, request)
    mt = _meeting_type_or_503(db, bso, payload.meeting_type)
    resolved = _owner_or_refuse(db, bso, payload.code)

    starts_at = _parse_start(payload.start_utc)
    form = _clean_form(payload)
    if not form.get("email"):
        raise HTTPException(status_code=422,
                            detail="A valid email address is required.")

    meta = {
        "ip": getattr(getattr(request, "client", None), "host", None),
        "user_agent": request.headers.get("user-agent"),
        "page_url": pb.clean_text(payload.page_url, 500),
        "referrer": pb.clean_text(payload.referrer, 500),
    }

    result = pb.book(db, platform=platform, bso=bso, intake_org=org,
                     meeting_type=mt, owner=resolved["owner"],
                     owner_source=resolved["source"], starts_at=starts_at,
                     form=form, idempotency_key=pb.clean_text(
                         payload.submission_id, 128),
                     request_meta=meta)

    if not result.ok:
        if result.status == pb.BOOK_SLOT_TAKEN:
            raise HTTPException(status_code=409, detail=result.message)
        if result.status == pb.BOOK_NOT_CONFIGURED:
            # The service's message can name people; the public one cannot.
            log.info("public booking refused (%s): %s", result.status,
                     result.message)
            raise HTTPException(status_code=503, detail=PUBLIC_UNAVAILABLE)
        raise HTTPException(status_code=422,
                            detail=result.message or "That booking is not valid.")

    return _booking_out(db, bso, result)


def _parse_start(value: str) -> datetime:
    """A UTC instant from the website. Naive UTC internally, per the time model."""
    raw = (value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:                                            # noqa: BLE001
        raise HTTPException(status_code=422,
                            detail="That start time could not be understood.")
    if parsed.tzinfo is not None:
        from datetime import timezone as _tz
        parsed = parsed.astimezone(_tz.utc).replace(tzinfo=None)
    return parsed.replace(second=0, microsecond=0)


def _clean_form(payload: BookRequest) -> Dict[str, Any]:
    """Everything a stranger typed, stripped before it is stored anywhere.

    The templates escape as well; this is about what LANDS IN THE DATABASE and
    is then read by staff across a dozen internal surfaces for the life of the
    deal. See public_booking.clean_text.
    """
    challenge = (payload.primary_challenge or "").strip()
    if challenge and challenge not in pb.CHALLENGE_VALUES:
        # An unknown value is kept as free text rather than rejected - a form
        # that refuses a prospect's answer teaches them to stop answering - but
        # it is never treated as one of the known keys.
        label = pb.clean_text(challenge, 200)
        challenge = "other"
    else:
        label = pb.CHALLENGE_LABELS.get(challenge)

    return {
        "full_name": pb.clean_text(payload.full_name, 200),
        "company": pb.clean_text(payload.company, 200),
        "email": pb.clean_email(payload.email),
        "phone": pb.clean_text(payload.phone, 60),
        "industry": pb.clean_text(payload.industry, 120),
        "primary_challenge": challenge or None,
        "primary_challenge_label": label,
        "primary_challenge_detail": pb.clean_text(payload.primary_challenge_detail, 1000),
        "current_system": pb.clean_text(payload.current_system, 200),
        "locations": pb.clean_text(payload.locations, 120),
        "lead_volume": pb.clean_text(payload.lead_volume, 120),
        "notes": pb.clean_text(payload.notes, 2000),
        "prospect_timezone": pb.valid_timezone(payload.timezone),
    }


def _booking_out(db: Session, bso, result: pb.BookingResult) -> dict:
    """The confirmation payload. Deliberately thin.

    It carries the reference the visitor should quote, the time in both zones,
    and the join link IF ONE EXISTS. It does not carry participant ids, the
    opportunity id, the host URL, or anything about who else is attending.

    `confirmation_email` reports the true state. When delivery is disabled, or
    the join link is not ready, the page must say "you're booked, details are on
    their way" rather than "check your inbox" - promising an email that is not
    coming is how a real booking looks like a broken one.
    """
    appt = result.appointment
    meeting_tz = appt.timezone or DEFAULT_TIMEZONE
    out = {
        "status": result.status,
        "already_booked": result.status == pb.BOOK_IDEMPOTENT,
        "reference": appt.id,
        "start_utc": appt.starts_at.replace(microsecond=0).isoformat() + "Z",
        "end_utc": appt.ends_at.replace(microsecond=0).isoformat() + "Z",
        "meeting_timezone": meeting_tz,
        "start_meeting_local": av.utc_to_local(
            appt.starts_at, meeting_tz).replace(microsecond=0).isoformat(),
        "duration_minutes": int(
            (appt.ends_at - appt.starts_at).total_seconds() // 60),
        "join_url": appt.meeting_url or None,
    }
    if appt.prospect_timezone:
        out["visitor_timezone"] = appt.prospect_timezone
        out["start_visitor_local"] = av.utc_to_local(
            appt.starts_at, appt.prospect_timezone).replace(
                microsecond=0).isoformat()
    artifacts = result.artifacts or {}
    confirmation = artifacts.get("confirmation") or {}
    out["confirmation_email"] = {
        "sent": bool(confirmation.get("sent")),
        "status": confirmation.get("status", "unknown"),
    }
    return out
