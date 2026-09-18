"""
Inbound Discovery / Demo booking: the whole transaction, in one place.

WHAT THIS IS FOR
----------------
A visitor on a brand's public website picks a time and fills in a form. By the
time this module returns, all of the following is true and consistent:

    the prospect exists as a website lead, deduped the way every other public
        form on the site dedupes
    the prospect exists as an OPPORTUNITY in the brand's sales pipeline, owned
        by a real salesperson
    exactly one SalesAppointment exists, with exactly the participants the
        quorum selected for that specific slot
    a video meeting has been provisioned by the brand's configured provider
    the internal calendars have been pushed
    the customer confirmation is owed, and carries a REAL join link
    the internal team has been notified
    the reminder schedule is known
    the whole thing is on the opportunity's timeline

and a retry produces none of it a second time.

THE THINGS THIS MODULE REFUSES TO TRUST
---------------------------------------
Everything the browser says about identity. The brand comes from the URL and
the Origin header, resolved server-side; the salesperson comes from an opaque
code resolved against THAT brand; the leadership comes from the org chart; the
participants come from the availability engine at the moment of booking. The
request body carries prospect details and a chosen time, and nothing else that
matters.

That is not defensive programming for its own sake. A public booking endpoint
that accepted a participant list would let anyone on the internet put a meeting
on any employee's calendar, and one that accepted a brand id would let a code
from one brand book into another's pipeline.

WHY THE ORDER IS THE ORDER
--------------------------
Side effects are ordered so that the ones that cannot be undone happen last and
the ones that can refuse happen first:

    resolve and validate      → can refuse, costs nothing
    recheck the slot          → can refuse, costs nothing
    write lead + opportunity  → in the database, reversible by rollback
    write appointment         → COMMIT. From here the meeting exists.
    provision video           → external, records its own failure
    sync calendars            → external, records its own failure
    confirmation + notify     → external, records its own failure
    reminder schedule         → local

Nothing after the commit can un-book the meeting. That is deliberate, and it
matches what the internal booking path already does: a Zoom outage must produce
a meeting flagged "needs attention", never a lost booking and never a 500 to
the person who just booked it.

THE ONE PLACE THAT RULE BENDS is the customer confirmation, which deliberately
does NOT go out when the artifacts it would describe do not exist. Telling a
prospect "you're booked, here's your link" when there is no link is worse than
telling them nothing.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Opportunity, OpportunityEvent, DiscoveryRecord,
    STAGE_PROSPECT,
)
from app.models.scheduling_models import (
    MeetingType, SalesAppointment, AppointmentParticipant,
    APPT_SCHEDULED, CONF_PENDING, ATTEND_UNKNOWN, DEFAULT_TIMEZONE,
    BOOKING_SOURCE_PUBLIC_WEB, SLOT_OPPORTUNITY_OWNER, SLOT_SALES_MANAGER,
)
from app.services import availability as av
from app.services import leadership_chain as lc
from app.services import leadership_quorum as lq
from app.services import sales_booking_codes as codes

log = logging.getLogger(__name__)


# ── the public form's vocabulary ────────────────────────────────────────────
#
# SERVED BY THE BACKEND, NOT TYPED INTO THE WEBSITE. The public page renders
# this list, so the wording on the form and the values that arrive back are the
# same strings by construction. A hand-maintained copy in PHP drifts the first
# time somebody edits one of them, and the drift shows up as unreadable
# analytics rather than as an error.
#
# `other` is last and always present: a fixed list that cannot express a
# prospect's actual problem teaches them that the form is not listening.

CHALLENGE_OPTIONS = [
    ("lead_followup",        "Following up with leads consistently"),
    ("response_speed",       "Responding fast enough"),
    ("more_appointments",    "Getting more appointments / booked meetings"),
    ("reengage_old_leads",   "Re-engaging old or inactive leads"),
    ("pipeline_management",  "Managing the sales process / pipeline"),
    ("team_accountability",  "Keeping the team accountable / organized"),
    ("customer_comms",       "Customer communication / follow-up"),
    ("manual_work",          "Too much manual work / not enough automation"),
    ("reporting",            "Reporting / knowing what is happening"),
    ("multi_location",       "Managing multiple locations / teams"),
    ("tools_not_working",    "Current CRM/tools not working"),
    ("other",                "Something else"),
]
CHALLENGE_VALUES = {k for k, _ in CHALLENGE_OPTIONS}
CHALLENGE_LABELS = dict(CHALLENGE_OPTIONS)

# NO "PACKAGE OF INTEREST". Discovery exists to work out which package fits;
# asking the prospect to pick one first asks them to do the job they booked the
# call to have done, and the answer is noise in the pipeline either way.

# Optional context the form may collect. Free text, no validation beyond length -
# these are notes for the person taking the call, not fields anything queries.
OPTIONAL_CONTEXT_FIELDS = ("current_system", "locations", "lead_volume", "notes")


# ── outcomes ────────────────────────────────────────────────────────────────

BOOK_OK             = "booked"
BOOK_IDEMPOTENT     = "already_booked"
BOOK_SLOT_TAKEN     = "slot_unavailable"
BOOK_NOT_CONFIGURED = "not_configured"
BOOK_INVALID        = "invalid_request"

# What a PUBLIC caller is told when the slot went away. Deliberately actionable
# and deliberately vague about whose calendar changed - a visitor learning that
# a named employee just became busy is a small privacy leak with no upside.
SLOT_GONE_MESSAGE = ("That time was taken while you were filling in the form. "
                     "Please pick another one.")


@dataclass
class BookingResult:
    ok: bool
    status: str
    appointment: Optional[SalesAppointment] = None
    opportunity: Optional[Opportunity] = None
    lead_id: Optional[str] = None
    created: bool = False
    message: Optional[str] = None
    # What happened to each side effect, for the response and for the report.
    artifacts: Dict[str, Any] = field(default_factory=dict)


# ── brand resolution ────────────────────────────────────────────────────────

def brand_sales_org_for_platform(db: Session,
                                 platform: Platform) -> Optional[BrandSalesOrg]:
    """The sales team that sells this brand.

    One active brand sales org per platform is the shape the product has. If a
    platform ever has two, this returns the oldest rather than guessing, and the
    caller's configuration error surfaces as "not configured" rather than as
    bookings landing in whichever one the database happened to return first.

    A DEMONSTRATION SALES ORG IS NEVER THE ANSWER, whatever its age.

    Standing up a brand's demo environment creates a second BrandSalesOrg on the
    SAME platform - `<Brand> Sales (Demonstration)`, is_demo=True - alongside the
    real one. Both are active, because the demo environment is meant to work. So
    "oldest active wins" was a race between a real sales team and a synthetic
    one, decided by which happened to be created first, and the losing case
    routes a real prospect from the public website to a demonstration team
    stocked with invented people.

    Excluding is_demo here is the same rule the column was written for: it is
    only ever the basis of a refusal, and nothing is granted because it is true.
    A platform whose ONLY sales org is the demo one resolves to None, and public
    booking reports "not configured" - which is correct. A demonstration team
    must not take a real meeting.
    """
    return (db.query(BrandSalesOrg)
            .filter(BrandSalesOrg.platform_id == platform.id,
                    BrandSalesOrg.is_active.is_(True),
                    BrandSalesOrg.is_demo.is_(False))
            .order_by(BrandSalesOrg.created_at.asc())
            .first())


def public_meeting_types(db: Session, bso: BrandSalesOrg) -> List[MeetingType]:
    """Only what the brand has deliberately opened to the internet.

    `public_bookable` defaults FALSE on every row, so a brand that has not opted
    anything in offers nothing rather than offering everything. That default is
    what stops "Internal Sales Meeting" being bookable from a marketing page.
    """
    return (db.query(MeetingType)
            .filter(MeetingType.brand_sales_org_id == bso.id,
                    MeetingType.is_active.is_(True),
                    MeetingType.public_bookable.is_(True))
            .order_by(MeetingType.sort_order.asc())
            .all())


def resolve_meeting_type(db: Session, bso: BrandSalesOrg,
                         key: Optional[str] = None) -> Optional[MeetingType]:
    """The requested public meeting type, or the brand's first one.

    A key that names a type which is NOT public_bookable resolves to None - it
    is not silently replaced with the default, because a request naming a
    specific type and getting a different one back is how a visitor ends up
    booked into something nobody offered them.
    """
    types = public_meeting_types(db, bso)
    if not types:
        return None
    if key:
        for mt in types:
            if mt.key == key:
                return mt
        return None
    return types[0]


# ── input hygiene ───────────────────────────────────────────────────────────

_TAG = re.compile(r"<[^>]*>")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(value: Optional[str], limit: int = 500) -> Optional[str]:
    """Strip markup and control characters from anything a stranger typed.

    THIS IS NOT THE ONLY DEFENCE AND MUST NOT BE TREATED AS ONE. The templates
    that render these values escape them, and that escaping is what actually
    stops an injection. Stripping here is about what gets STORED: a company name
    containing a <script> tag is not a company name, it will be read by staff in
    a dozen internal surfaces over the life of the deal, and every one of those
    surfaces would have to be right about escaping forever.

    Angle brackets are removed rather than escaped, deliberately. Storing
    `&lt;b&gt;` means somebody eventually renders it raw and the prospect's name
    reads as markup.
    """
    if value is None:
        return None
    text = _TAG.sub(" ", str(value))
    text = _CTRL.sub("", text)
    text = " ".join(text.split())
    text = text.strip()
    if not text:
        return None
    return text[:limit]


def clean_email(value: Optional[str]) -> Optional[str]:
    v = clean_text(value, 180)
    if not v:
        return None
    v = v.lower()
    # Not validation - the form validates. This refuses the shapes that would
    # corrupt a header if anything ever concatenated it into one.
    if "\n" in v or "\r" in v or " " in v or "@" not in v:
        return None
    return v


def valid_timezone(name: Optional[str]) -> Optional[str]:
    """A timezone the platform can actually resolve, or None.

    A malformed zone from a browser must not reach the scheduler: `zoneinfo`
    raises on an unknown key, and a raise inside availability computation would
    turn a typo in a query string into a 500 on a public page.
    """
    if not name:
        return None
    name = str(name).strip()
    if not name or len(name) > 64:
        return None
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return name
    except Exception:                                            # noqa: BLE001
        return None


_TEXT_LIMITS = {
    "full_name": 200, "company": 200, "phone": 60, "industry": 120,
    "primary_challenge": 64, "primary_challenge_label": 200,
    "primary_challenge_detail": 1000, "current_system": 200,
    "locations": 120, "lead_volume": 120, "notes": 2000,
}


def sanitize_form(form: Dict[str, Any]) -> Dict[str, Any]:
    """Every free-text field a stranger supplied, cleaned. Idempotent."""
    out = dict(form or {})
    for key, limit in _TEXT_LIMITS.items():
        if key in out:
            out[key] = clean_text(out.get(key), limit)
    if "email" in out:
        out["email"] = clean_email(out.get("email"))
    if "prospect_timezone" in out:
        out["prospect_timezone"] = valid_timezone(out.get("prospect_timezone"))
    return out


# ── the opportunity ─────────────────────────────────────────────────────────

def find_opportunity(db: Session, bso: BrandSalesOrg, *, email: Optional[str],
                     company: Optional[str]) -> Optional[Opportunity]:
    """An OPEN opportunity for this prospect in this brand, if one exists.

    EMAIL FIRST, AND EMAIL ALONE WHEN IT MATCHES. Two people at the same company
    booking two calls is two conversations; the same person booking twice is one.
    Matching on company alone would merge the first case, which is how a rep
    loses a deal to a colleague's record.

    Company is a fallback only when no email was given at all, and even then it
    is scoped to open deals - a company that bought two years ago and comes back
    is a new opportunity, not a reopening of a closed one.

    Deliberately NOT matching on phone: a shared switchboard number is common
    and would merge unrelated people at the same business.
    """
    q = db.query(Opportunity).filter(
        Opportunity.brand_sales_org_id == bso.id,
        Opportunity.status == "open")
    if email:
        hit = q.filter(Opportunity.email.isnot(None)).all()
        for o in hit:
            if (o.email or "").strip().lower() == email:
                return o
        return None
    if company:
        needle = company.strip().lower()
        for o in q.all():
            if (o.company_name or "").strip().lower() == needle:
                return o
    return None


def upsert_opportunity(db: Session, bso: BrandSalesOrg, owner: User,
                       form: Dict[str, Any], source: str,
                       now: Optional[datetime] = None) -> tuple:
    """Create or update the pipeline record. Returns (opportunity, created).

    AN EXISTING OPPORTUNITY IS UPDATED, NEVER DUPLICATED, AND NEVER REASSIGNED.

    The owner is not changed on an existing deal even if the visitor arrived
    through a different rep's link. A prospect already belongs to whoever is
    working them; a booking through somebody else's link is a fact worth
    recording on the timeline - which it is, below - but it is not a
    reassignment, and letting it be one turns a public URL into a way to take
    another rep's deal.

    The stage is not moved backwards either. Somebody at proposal stage booking
    another demo is still at proposal stage.
    """
    now = now or datetime.utcnow()
    email = form.get("email")
    company = form.get("company")

    opp = find_opportunity(db, bso, email=email, company=company)
    created = opp is None

    if created:
        opp = Opportunity(
            brand_sales_org_id=bso.id,
            owner_user_id=owner.id,
            company_name=company or (form.get("full_name") or "New prospect"),
            contact_name=form.get("full_name"),
            email=email,
            phone=form.get("phone"),
            industry=form.get("industry"),
            # CAPTURED, NEVER ASSUMED. The Opportunity model carries an explicit
            # note about this: a hardcoded timezone was a real defect here once.
            # NULL when the visitor's browser did not tell us.
            timezone=form.get("prospect_timezone"),
            stage=STAGE_PROSPECT,
            status="open",
            source=source,
            created_at=now, updated_at=now,
            stage_changed_at=now,
        )
        db.add(opp)
        db.flush()
    else:
        # Fill gaps; never overwrite something a human has since corrected with
        # something a web form guessed.
        if not opp.contact_name and form.get("full_name"):
            opp.contact_name = form["full_name"]
        if not opp.email and email:
            opp.email = email
        if not opp.phone and form.get("phone"):
            opp.phone = form["phone"]
        if not opp.industry and form.get("industry"):
            opp.industry = form["industry"]
        if not opp.timezone and form.get("prospect_timezone"):
            opp.timezone = form["prospect_timezone"]
        if not opp.source:
            opp.source = source
        opp.updated_at = now

    _record_discovery(db, opp, form, now)
    return opp, created


def _record_discovery(db: Session, opp: Opportunity, form: Dict[str, Any],
                      now: datetime) -> None:
    """The form's answers, in the discovery record that already exists for them.

    NO NEW MODEL. `DiscoveryRecord.bottlenecks` is literally "Bottlenecks /
    challenges", `current_tools` is "Current systems / tools", and
    `opportunity_notes` is "Additional notes". The public form is a short
    discovery questionnaire, so it belongs where discovery answers belong -
    which is also where the demo builder and the proposal already read from.

    APPENDS, NEVER REPLACES. A rep who has already filled discovery in properly
    must not have it overwritten because the prospect re-booked.
    """
    challenge = form.get("primary_challenge_label")
    if not challenge and not any(form.get(f) for f in OPTIONAL_CONTEXT_FIELDS):
        return

    rec = (db.query(DiscoveryRecord)
           .filter(DiscoveryRecord.opportunity_id == opp.id).first())
    if rec is None:
        rec = DiscoveryRecord(opportunity_id=opp.id, created_at=now)
        db.add(rec)
        db.flush()

    stamp = now.strftime("%b %d, %Y")
    if challenge:
        line = "[Website, %s] %s" % (stamp, challenge)
        if form.get("primary_challenge_detail"):
            line += " — %s" % form["primary_challenge_detail"]
        rec.bottlenecks = ("%s\n%s" % (rec.bottlenecks, line)).strip() \
            if rec.bottlenecks else line
    if form.get("current_system"):
        line = "[Website, %s] %s" % (stamp, form["current_system"])
        rec.current_tools = ("%s\n%s" % (rec.current_tools, line)).strip() \
            if rec.current_tools else line

    extra = []
    for key in ("locations", "lead_volume", "notes"):
        if form.get(key):
            extra.append("%s: %s" % (key.replace("_", " ").title(), form[key]))
    if extra:
        line = "[Website, %s] %s" % (stamp, " · ".join(extra))
        rec.opportunity_notes = ("%s\n%s" % (rec.opportunity_notes, line)).strip() \
            if rec.opportunity_notes else line
    rec.updated_at = now


# ── idempotency ─────────────────────────────────────────────────────────────

def existing_booking(db: Session, bso: BrandSalesOrg,
                     key: Optional[str]) -> Optional[SalesAppointment]:
    """The appointment this submission already produced, if it did.

    Looked up BEFORE anything else happens, so a retry costs one query and
    produces no side effects at all. The unique index is what covers the
    remaining gap - two submissions arriving close enough together that both get
    past this check - by making the second INSERT fail rather than succeed.
    """
    if not key:
        return None
    return (db.query(SalesAppointment)
            .filter(SalesAppointment.brand_sales_org_id == bso.id,
                    SalesAppointment.booking_idempotency_key == key)
            .first())


# ── availability, as a public caller sees it ────────────────────────────────

def public_slots(db: Session, bso: BrandSalesOrg, mt: MeetingType,
                 owner: User, start_utc: datetime, end_utc: datetime,
                 now_utc: Optional[datetime] = None,
                 limit: int = 200) -> dict:
    """Bookable openings for this rep and this meeting type.

    Returns the internal shape. `public_slot_payload` below is what strips it
    for the wire - the two are separate functions on purpose, because the
    booking path needs the participant list that the public payload must not
    contain.
    """
    policy = lq.QuorumPolicy.from_meeting_type(mt)
    return lq.resolve_and_find(db, owner.id, bso.id, policy, start_utc, end_utc,
                               mt.duration_minutes or 30, now_utc=now_utc,
                               limit=limit)


def public_slot_payload(found: dict, visitor_tz: Optional[str],
                        meeting_tz: str) -> List[dict]:
    """Slots, stripped to what a stranger may see.

    WHAT IS DELIBERATELY ABSENT: user ids, names, how many leaders were free,
    which leaders they were, and any hint of why other times are missing. A
    public availability response that carried participant ids would let anybody
    enumerate the sales team and map the org chart by diffing one rep's slots
    against another's.

    Times go out in UTC ISO form plus a rendering in both the visitor's zone and
    the meeting's own zone. Sending only a local string would make the page's
    correctness depend on the browser agreeing with the server about the
    offset - which is exactly what breaks across a DST boundary.
    """
    out = []
    for s in found.get("slots", []):
        row = {
            "start_utc": s["starts_at"].replace(microsecond=0).isoformat() + "Z",
            "end_utc": s["ends_at"].replace(microsecond=0).isoformat() + "Z",
            "meeting_timezone": meeting_tz,
            "start_meeting_local":
                av.utc_to_local(s["starts_at"], meeting_tz).replace(
                    microsecond=0).isoformat(),
        }
        if visitor_tz:
            row["visitor_timezone"] = visitor_tz
            row["start_visitor_local"] = av.utc_to_local(
                s["starts_at"], visitor_tz).replace(microsecond=0).isoformat()
        out.append(row)
    return out


def form_schema() -> dict:
    """What the public page should render, served by the backend.

    So the wording on the form and the values that arrive back are the same
    strings by construction rather than by two people remembering to edit two
    files.
    """
    return {
        "required": ["full_name", "company", "email", "phone", "industry",
                     "primary_challenge"],
        "primary_challenge_options": [{"value": v, "label": l}
                                      for v, l in CHALLENGE_OPTIONS],
        "optional_context_fields": list(OPTIONAL_CONTEXT_FIELDS),
        # Stated so the website does not add one back. Discovery decides the
        # package; asking first asks the prospect to do the job they booked the
        # call to have done.
        "package_of_interest": False,
    }


# ── the booking transaction ─────────────────────────────────────────────────

def _slot_for(found: dict, starts_at: datetime) -> Optional[dict]:
    """The offered slot matching this exact start, or None.

    EXACT MATCH ON PURPOSE. The participant list is part of the slot, chosen
    from free/busy at the moment the slot was computed. Accepting a nearby time
    and reusing a neighbouring slot's participants is how a leader who was busy
    at 2pm ends up on the 2pm invitation.
    """
    for s in found.get("slots", []):
        if s["starts_at"] == starts_at:
            return s
    return None


def book(db: Session, *, platform: Platform, bso: BrandSalesOrg,
         intake_org, meeting_type: MeetingType, owner: User,
         owner_source: str, starts_at: datetime, form: Dict[str, Any],
         idempotency_key: Optional[str] = None,
         now: Optional[datetime] = None,
         request_meta: Optional[dict] = None) -> BookingResult:
    """One website booking, end to end.

    Everything about identity has already been resolved by the caller from the
    server's own view of the world - brand from the URL and Origin, owner from
    an opaque code checked against THAT brand. This function re-derives
    leadership, availability and participants itself and trusts nothing else.
    """
    now = now or datetime.utcnow()
    request_meta = request_meta or {}

    # ── SANITIZE HERE TOO, NOT ONLY AT THE ROUTER ───────────────────────────
    # The router cleans what it parses, and this repeats it. That is not
    # redundancy for its own sake: "the service is safe as long as every caller
    # remembers" is exactly the kind of guarantee that holds until the second
    # caller arrives. Cleaning is idempotent, so running it twice costs nothing
    # and the service is safe on its own terms.
    form = sanitize_form(form)

    # ── 0. the retry guard, before any side effect ──────────────────────────
    prior = existing_booking(db, bso, idempotency_key)
    if prior is not None:
        return BookingResult(
            ok=True, status=BOOK_IDEMPOTENT, appointment=prior, created=False,
            opportunity=(db.query(Opportunity)
                         .filter(Opportunity.id == prior.opportunity_id).first()
                         if prior.opportunity_id else None),
            message="This booking was already made.",
            artifacts={"replayed": True})

    duration = meeting_type.duration_minutes or 30
    ends_at = starts_at + timedelta(minutes=duration)
    if ends_at <= now:
        return BookingResult(ok=False, status=BOOK_INVALID,
                             message="That time is in the past.")

    # ── 1. RE-RESOLVE AND RE-CHECK. The slot list the visitor is looking at
    #      was computed against an external-busy cache with a ten-minute TTL and
    #      may have been on their screen for far longer than that while they
    #      typed. This recomputes it now, against the same engine, and requires
    #      the chosen start to still be among the answers.
    found = public_slots(db, bso, meeting_type, owner,
                         starts_at - timedelta(hours=2),
                         ends_at + timedelta(hours=2), now_utc=now, limit=0)
    if found.get("chain_status") and found["chain_status"] != lc.CHAIN_OK:
        return BookingResult(ok=False, status=BOOK_NOT_CONFIGURED,
                             message=found.get("reason") or
                             "This booking cannot be scheduled right now.")
    slot = _slot_for(found, starts_at)
    if slot is None:
        return BookingResult(ok=False, status=BOOK_SLOT_TAKEN,
                             message=SLOT_GONE_MESSAGE)

    participant_ids = list(slot["participant_user_ids"])
    participants = (db.query(User).filter(User.id.in_(participant_ids)).all()
                    if participant_ids else [])
    by_id = {u.id: u for u in participants}
    participants = [by_id[i] for i in participant_ids if i in by_id]
    if not participants:
        return BookingResult(ok=False, status=BOOK_NOT_CONFIGURED,
                             message="No participants could be resolved.")

    # ── 2. THE SAME TWO REFUSALS THE INTERNAL PATH MAKES. Not a re-check of
    #      what step 1 just did: find_conflicts sees our own appointments at
    #      row level, and the external refresh bypasses the cache TTL to re-read
    #      the outside calendars. Between them they close the window that the
    #      slot search alone cannot.
    conflicts = av.find_conflicts(db, [u.id for u in participants],
                                  starts_at, ends_at)
    if conflicts:
        return BookingResult(ok=False, status=BOOK_SLOT_TAKEN,
                             message=SLOT_GONE_MESSAGE)

    try:
        from app.services import external_busy as extbusy
        extbusy.refresh_many(db, participants, starts_at - timedelta(hours=1),
                             ends_at + timedelta(hours=1), force=True)
        if extbusy.external_conflicts(db, [u.id for u in participants],
                                      starts_at, ends_at):
            return BookingResult(ok=False, status=BOOK_SLOT_TAKEN,
                                 message=SLOT_GONE_MESSAGE)
    except Exception:                                            # noqa: BLE001
        # FAILING TO READ A CALENDAR IS NOT A REFUSAL, exactly as the internal
        # booking path already decides. A Microsoft outage must not stop a brand
        # taking inbound meetings; it degrades us to the state every booking was
        # in before external busy existed. Refusing here would hand a vendor a
        # veto over the brand's inbound pipeline.
        log.warning("external busy refresh failed during public booking",
                    exc_info=True)

    # ── 3. the prospect, in both places they belong ─────────────────────────
    lead_id = _capture_website_lead(db, platform, intake_org, form, request_meta)

    opp, opp_created = upsert_opportunity(db, bso, owner, form,
                                          source=website_source_label(platform),
                                          now=now)

    # ── 4. the appointment ──────────────────────────────────────────────────
    tz = (meeting_type.brand_sales_org_id and bso.timezone) or DEFAULT_TIMEZONE
    title = "%s · %s" % (meeting_type.name, opp.company_name)

    appt = SalesAppointment(
        brand_sales_org_id=bso.id,
        opportunity_id=opp.id,
        meeting_type_id=meeting_type.id,
        title=title,
        starts_at=starts_at, ends_at=ends_at, timezone=tz,
        status=APPT_SCHEDULED,
        prospect_name=form.get("full_name"),
        prospect_company=opp.company_name,
        prospect_email=form.get("email"),
        prospect_phone=form.get("phone"),
        prospect_timezone=form.get("prospect_timezone"),
        confirmation_status=CONF_PENDING,
        booking_source=BOOKING_SOURCE_PUBLIC_WEB,
        booking_idempotency_key=idempotency_key,
        notes=_booking_note(form, owner_source),
        # created_by stays NULL. NOBODY AUTHENTICATED CREATED THIS, and naming
        # the salesperson here would be a forged actor - the same rule the audit
        # convention applies everywhere else in this codebase.
        created_by=None,
        created_at=now, updated_at=now,
    )
    db.add(appt)
    db.flush()

    owner_id = slot.get("owner_user_id")
    for u in participants:
        prof = av.get_or_create_profile(db, u)
        bs, be = av.buffered_window(prof, starts_at, ends_at)
        db.add(AppointmentParticipant(
            appointment_id=appt.id, user_id=u.id,
            role_slot=(SLOT_OPPORTUNITY_OWNER if u.id == owner_id
                       else SLOT_SALES_MANAGER),
            is_required=True, attendance_status=ATTEND_UNKNOWN,
            busy_start_at=bs, busy_end_at=be, is_blocking=True))

    db.add(OpportunityEvent(
        opportunity_id=opp.id,
        event_type="appointment_booked",
        summary="%s booked from the website" % meeting_type.name,
        detail="%s · %s" % (
            av.utc_to_local(starts_at, tz).strftime("%b %d, %Y %I:%M %p"),
            ", ".join(u.full_name or u.email for u in participants)),
        # NO ACTOR. A website visitor is not a user, and writing one of our own
        # people here would be a forged audit actor. An event with no human
        # behind it says so.
        actor_user_id=None,
        occurred_at=now))

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        text = str(exc).lower()
        # THE RETRY THAT GOT PAST STEP 0. Two submissions of the same form,
        # close enough together that both read "no existing booking". The unique
        # index refused the second, which is the correct outcome - and the
        # correct RESPONSE is the first one's result, not an error.
        if idempotency_key and ("idempotency" in text or "uq_sales_appt" in text):
            prior = existing_booking(db, bso, idempotency_key)
            if prior is not None:
                return BookingResult(ok=True, status=BOOK_IDEMPOTENT,
                                     appointment=prior, created=False,
                                     message="This booking was already made.",
                                     artifacts={"replayed": True,
                                                "race": True})
        # The participant exclusion constraint: somebody booked one of these
        # people between our checks and our commit. One wins; this one fails
        # honestly.
        if "no_overlap" in text or "exclusion" in text:
            return BookingResult(ok=False, status=BOOK_SLOT_TAKEN,
                                 message=SLOT_GONE_MESSAGE)
        raise
    db.refresh(appt)

    # ── 5. everything after the commit. THE MEETING NOW EXISTS. Nothing below
    #      can un-book it, and nothing below may raise past this function.
    artifacts = _fulfil(db, appt, opp, participants, now=now)
    artifacts["owner_source"] = owner_source
    artifacts["opportunity_created"] = opp_created
    artifacts["lead_id"] = lead_id

    return BookingResult(ok=True, status=BOOK_OK, appointment=appt,
                         opportunity=opp, lead_id=lead_id, created=True,
                         artifacts=artifacts)


# ── the side effects, each of which owns its own failure ────────────────────

def website_source_label(platform: Platform) -> str:
    """The opportunity's `source`, brand-aware and reusable.

    "Website — Discovery + Demo" is the shape, with the brand's own name in
    front of it rather than one brand's wording compiled into the platform. A
    literal in core code here is exactly what makes the capability un-reusable
    by the next brand.
    """
    name = (getattr(platform, "name", None) or "Website").strip()
    return "%s Website — Discovery + Demo" % name


def _booking_note(form: Dict[str, Any], owner_source: str) -> str:
    """What the rep sees on the appointment before they join the call."""
    bits = []
    if form.get("primary_challenge_label"):
        bits.append("Primary challenge: %s" % form["primary_challenge_label"])
    if form.get("primary_challenge_detail"):
        bits.append("In their words: %s" % form["primary_challenge_detail"])
    for key in OPTIONAL_CONTEXT_FIELDS:
        if key != "notes" and form.get(key):
            bits.append("%s: %s" % (key.replace("_", " ").title(), form[key]))
    if form.get("notes"):
        bits.append("Notes: %s" % form["notes"])
    bits.append("Booked from the website (%s)."
                % ("salesperson link" if owner_source == codes.OWNER_FROM_CODE
                   else "general enquiry"))
    return "\n".join(bits)


def _capture_website_lead(db: Session, platform: Platform, intake_org,
                          form: Dict[str, Any],
                          request_meta: Dict[str, Any]) -> Optional[str]:
    """The marketing-site lead record, through the path that already exists.

    REUSED RATHER THAN REPLACED. `public_capture` owns website dedupe, consent
    evidence, the note format and custom-field merging, and every other public
    form on every brand site goes through it. A booking that wrote its own lead
    row would be a second capture path with its own dedupe rules, and the two
    would disagree about whether a returning visitor is the same person.

    Best effort, deliberately: the pipeline record is the Opportunity, and a
    failure to also file a marketing lead must not cost the brand a booked
    meeting. The failure is logged, not swallowed silently.
    """
    if intake_org is None:
        return None
    try:
        from app.services import public_capture as pc
        first, last = pc.split_name(form.get("full_name"))
        sub = pc.Submission(
            kind=pc.KIND_DEMO,
            first_name=first, last_name=last,
            email=form.get("email"), phone=form.get("phone"),
            company=form.get("company"), industry=form.get("industry"),
            message=form.get("primary_challenge_label"),
            page_url=request_meta.get("page_url"),
            referrer=request_meta.get("referrer"),
            utm=request_meta.get("utm") or {},
            extra={k: v for k, v in form.items()
                   if k in OPTIONAL_CONTEXT_FIELDS and v},
            ip=request_meta.get("ip"),
            user_agent=request_meta.get("user_agent"),
        )
        return pc.capture(db, platform=platform, org=intake_org,
                          sub=sub).get("lead_id")
    except Exception:                                            # noqa: BLE001
        log.exception("website lead capture failed during a public booking; "
                      "the opportunity and appointment are unaffected")
        return None


def _fulfil(db: Session, appt: SalesAppointment, opp: Opportunity,
            participants: List[User], now: datetime) -> dict:
    """Video, calendars, confirmation, notification, reminders.

    Each step records its own outcome and none of them may raise past here. The
    meeting is already committed; an outage downstream produces a booking
    flagged for attention, never a lost booking.
    """
    out = {}

    # ── video ───────────────────────────────────────────────────────────────
    # THE EXISTING ARCHITECTURE, NOT A SECOND ZOOM INTEGRATION. `ensure_meeting`
    # is idempotent, brand-scoped, keeps the host URL away from anything
    # prospect-facing, and writes the attendee link onto the appointment where
    # calendar sync and the confirmation both already read it.
    try:
        from app.services import appointment_meetings
        out["meeting"] = appointment_meetings.ensure_meeting(db, appt, now=now)
    except Exception as exc:                                     # noqa: BLE001
        log.exception("video provisioning failed for public booking %s", appt.id)
        out["meeting"] = {"ok": False, "reason": "exception", "error": str(exc)[:200]}

    # ── calendars ───────────────────────────────────────────────────────────
    #
    # THE .ICS FALLBACK IS AN EMAIL, AND THAT MATTERS HERE.
    #
    # When a participant has no Microsoft or Google calendar connected, the sync
    # registry falls back to `calendar_providers/ics.py`, which delivers the
    # invitation as an email with an .ics attachment. For a rep clicking "book"
    # inside the product that is exactly right - they asked for it, and they are
    # watching. For an INBOUND booking nobody asked and nobody is watching: a
    # stranger on the website would be able to cause mail to be sent, at any
    # hour, through a path that does not pass the platform's outbound gate.
    #
    # So the fallback is held to the same staff gate as every other mail this
    # flow can produce. Participants with a REAL provider connected are synced
    # regardless - an API call to Microsoft Graph is not an email and is not
    # what the gate governs.
    out["calendar"] = _sync_calendars(db, appt, participants)

    # ── customer confirmation ───────────────────────────────────────────────
    out["confirmation"] = _confirm(db, appt)

    # ── internal notification ───────────────────────────────────────────────
    out["notification"] = notify_internal(db, appt, opp, participants, now=now)

    # ── reminders ───────────────────────────────────────────────────────────
    try:
        from app.services import sales_appointment_reminders as reminders
        out["reminders"] = reminders.schedule_for(db, appt, now=now)
    except Exception as exc:                                     # noqa: BLE001
        log.exception("reminder scheduling failed for public booking %s", appt.id)
        out["reminders"] = {"ok": False, "error": str(exc)[:200]}

    try:
        db.commit()
    except Exception:                                            # noqa: BLE001
        db.rollback()
        log.exception("could not persist fulfilment state for %s", appt.id)
    return out


def _sync_calendars(db: Session, appt: SalesAppointment,
                    participants: List[User]) -> dict:
    """Push the internal calendars, without letting the .ics fallback send mail
    that this deployment has not enabled.

    Returns the usual sync summary, plus `email_invites` saying what happened to
    the participants whose only route is an emailed invitation - so an
    operations view can tell "not connected" from "we chose not to send".
    """
    try:
        from app.services import appointment_sync
        from app.services import calendar_providers as reg
        from app.services import outbound_email_gate as gate
        from app.services.appointment_sync import PROVIDER_ICS
    except Exception as exc:                                     # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}

    try:
        # THE INTERNAL SWITCH GOVERNS THIS, not the confirmation one. An emailed
        # .ics invitation goes to the assigned salesperson and the booked
        # leadership - the same people the internal notification goes to, and
        # nobody else. It is one notification to one set of people, differing
        # only in whether it carries an attachment, so it shares their switch
        # rather than having a fourth of its own.
        mail_allowed = gate.source_enabled(gate.PUBLIC_BOOKING_INTERNAL)
    except Exception:                                            # noqa: BLE001
        mail_allowed = False

    if mail_allowed:
        try:
            return appointment_sync.summarize(
                appointment_sync.sync_appointment(db, appt))
        except Exception as exc:                                 # noqa: BLE001
            log.exception("calendar sync failed for public booking %s", appt.id)
            return {"ok": False, "error": str(exc)[:200]}

    # Mail is off. Sync only the participants who have a real provider.
    email_only = []
    for u in participants:
        try:
            provider = reg.get_provider(db, u)
            key = getattr(provider, "resolved_key", None) or PROVIDER_ICS
        except Exception:                                        # noqa: BLE001
            key = PROVIDER_ICS
        if key == PROVIDER_ICS:
            email_only.append(u.id)

    if len(email_only) == len(participants):
        return {"ok": True, "synced": 0, "skipped": len(participants),
                "email_invites": "delivery_disabled",
                "detail": ("No participant has a calendar connected, and emailed "
                           "invitations are disabled in this deployment.")}
    try:
        summary = appointment_sync.summarize(
            appointment_sync.sync_appointment(db, appt))
    except Exception as exc:                                     # noqa: BLE001
        log.exception("calendar sync failed for public booking %s", appt.id)
        return {"ok": False, "error": str(exc)[:200]}
    summary["email_invites"] = "delivery_disabled"
    summary["email_only_participants"] = email_only
    return summary


def _confirm(db: Session, appt: SalesAppointment) -> dict:
    """The one branded confirmation - and the rule about when it may go.

    IT WAITS FOR THE ARTIFACTS IT WOULD DESCRIBE. A Discovery + Demo is a video
    call, and a confirmation that says "you're booked" while carrying no join
    link is worse than no confirmation at all: the prospect has nothing to click
    at the appointed hour and no reason to believe a second email will be any
    better. The public response says `pending` instead, and the website tells
    the visitor their booking is confirmed and details are coming - which is
    true.
    """
    mt = (db.query(MeetingType)
          .filter(MeetingType.id == appt.meeting_type_id).first()
          if appt.meeting_type_id else None)
    needs_video = bool(getattr(mt, "requires_video", False))

    if needs_video and not appt.meeting_url:
        return {"sent": False, "status": "pending_meeting_link",
                "reason": "The video meeting has not been provisioned yet, so "
                          "the confirmation would carry no join link."}
    if not appt.prospect_email:
        return {"sent": False, "status": "no_recipient"}

    try:
        from app.services import demo_confirmation
        from app.services import outbound_email_gate
        demo_confirmation.send(
            db, appt,
            gate_source=outbound_email_gate.PUBLIC_BOOKING_CONFIRMATION)
        return {"sent": True, "status": "sent"}
    except Exception as exc:                                     # noqa: BLE001
        # DISABLED DELIVERY IS THE EXPECTED OUTCOME IN THIS BUILD, not a fault.
        # This path runs OUTBOUND_EMAIL_PUBLIC_BOOKING_CONFIRMATION, which is
        # off by default and raises - so the confirmation is prepared and
        # recorded as owed, and no customer is emailed until somebody
        # deliberately turns that one variable on. Reported separately from a
        # real failure so the two are never confused in an operations view.
        from app.services.outbound_email_gate import EmailSendDisabled
        if isinstance(exc, EmailSendDisabled):
            return {"sent": False, "status": "delivery_disabled",
                    "reason": str(exc)[:200]}
        log.exception("confirmation failed for public booking %s", appt.id)
        return {"sent": False, "status": "failed", "reason": str(exc)[:200]}


# ── internal notification ───────────────────────────────────────────────────

def notify_internal(db: Session, appt: SalesAppointment, opp: Opportunity,
                    participants: List[User],
                    now: Optional[datetime] = None) -> dict:
    """Tell the people who have to be in the room.

    WHO. The assigned salesperson and the leadership participant(s) the quorum
    actually booked - the people whose calendars now carry this meeting.
    Deliberately NOT the whole resolved reporting chain: a leader who was busy
    and therefore is not attending does not need a notification about a meeting
    they are not in, and sending one teaches everybody to filter these out.

    Whether a brand should be able to opt into notifying the full chain is a
    real question and it is recorded in the report as a follow-up rather than
    guessed at here - it needs a configuration surface and a decision about
    default noise, and hardcoding "notify everyone" is precisely how these
    become ignored.

    IN-APP IS THE OPPORTUNITY TIMELINE, which already exists, is already what
    the Sales Workspace reads, and is already append-only. A second notification
    store for the sales domain would be a parallel system - and the customer-
    tenant `Notification` table is not it: that model is keyed to a Lead in a
    customer organization, and reaching across the brand-sales / customer-tenant
    boundary is the one thing the sales models are emphatic about not doing.

    EMAIL IS PREPARED, NOT SENT, in this build - same staff gate as the customer
    confirmation, off by default.
    """
    now = now or datetime.utcnow()
    result = {"timeline": False, "email": {"sent": False, "status": "skipped"},
              "recipients": []}

    try:
        db.add(OpportunityEvent(
            opportunity_id=opp.id,
            event_type="inbound_booking_notified",
            summary="Inbound booking assigned to %s" % (
                (participants[0].full_name or participants[0].email)
                if participants else "the sales team"),
            detail=_notification_text(appt, opp, participants),
            actor_user_id=None,
            occurred_at=now))
        db.flush()
        result["timeline"] = True
    except Exception:                                            # noqa: BLE001
        log.exception("could not write the inbound booking timeline event")

    recipients = [u.email for u in participants if u.email]
    result["recipients"] = recipients
    if not recipients:
        return result

    try:
        from app.services import outbound_email_gate
        from app.services.email_service import send_email_via_provider
        from app.services.appointment_invites import brand_identity, _SendingOrg
        ident = brand_identity(db, appt)
        subject = "New %s booked — %s" % (appt.title.split(" · ")[0],
                                          opp.company_name)
        body = _notification_html(db, appt, opp, participants)
        identity = _SendingOrg(ident.get("from_email"),
                               from_name=ident.get("name"))
        sent = []
        for address in recipients:
            outbound_email_gate.gate_transactional_email(
                address, purpose="inbound sales booking",
                source=outbound_email_gate.PUBLIC_BOOKING_INTERNAL)
            send_email_via_provider(to_email=address, subject=subject,
                                    body_html=body, org=identity,
                                    message_type="inbound_sales_booking",
                                    template_id="sales.inbound_booking")
            sent.append(address)
        result["email"] = {"sent": True, "status": "sent", "to": sent}
    except Exception as exc:                                     # noqa: BLE001
        from app.services.outbound_email_gate import EmailSendDisabled
        if isinstance(exc, EmailSendDisabled):
            result["email"] = {"sent": False, "status": "delivery_disabled",
                               "reason": str(exc)[:200]}
        else:
            log.exception("internal booking notification failed for %s", appt.id)
            result["email"] = {"sent": False, "status": "failed",
                               "reason": str(exc)[:200]}
    return result


def _notification_text(appt: SalesAppointment, opp: Opportunity,
                       participants: List[User]) -> str:
    when = av.utc_to_local(appt.starts_at, appt.timezone or DEFAULT_TIMEZONE)
    lines = [
        "%s — %s" % (opp.company_name, appt.prospect_name or ""),
        "%s (%s)" % (when.strftime("%a %b %d, %Y %I:%M %p"),
                     appt.timezone or DEFAULT_TIMEZONE),
        "Attending: %s" % ", ".join(u.full_name or u.email for u in participants),
    ]
    if appt.prospect_timezone:
        lines.append("Prospect timezone: %s" % appt.prospect_timezone)
    if appt.notes:
        lines.append(appt.notes)
    return "\n".join(l for l in lines if l.strip())


def _notification_html(db: Session, appt: SalesAppointment, opp: Opportunity,
                       participants: List[User]) -> str:
    """The internal email body.

    Carries the join link because the people receiving it are the ones who have
    to join. It never carries the HOST link - `appointment_meetings` keeps that
    separate for exactly this reason, and an internal email is still an email
    that can be forwarded.
    """
    from html import escape
    when = av.utc_to_local(appt.starts_at, appt.timezone or DEFAULT_TIMEZONE)
    rows = [
        ("Company", opp.company_name),
        ("Contact", appt.prospect_name),
        ("Email", appt.prospect_email),
        ("Phone", appt.prospect_phone),
        ("Industry", opp.industry),
        ("When", "%s (%s)" % (when.strftime("%a %b %d, %Y %I:%M %p"),
                              appt.timezone or DEFAULT_TIMEZONE)),
        ("Prospect timezone", appt.prospect_timezone),
        ("Attending", ", ".join(u.full_name or u.email for u in participants)),
    ]
    body = "".join(
        "<tr><td style='padding:4px 12px 4px 0;color:#666'>%s</td>"
        "<td style='padding:4px 0'><strong>%s</strong></td></tr>"
        % (escape(str(label)), escape(str(value)))
        for label, value in rows if value)
    notes = ("<p style='white-space:pre-wrap'>%s</p>" % escape(appt.notes)
             if appt.notes else "")
    join = ("<p><a href='%s'>Join link</a></p>" % escape(appt.meeting_url)
            if appt.meeting_url else
            "<p style='color:#b00'>No meeting link yet.</p>")
    return ("<h2>New booking from the website</h2>"
            "<table style='border-collapse:collapse'>%s</table>%s%s"
            % (body, notes, join))
