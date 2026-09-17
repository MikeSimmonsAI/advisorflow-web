"""THE INBOUND BOOKING TRANSACTION, end to end and adversarially.

WHAT THIS SUITE IS REALLY DEFENDING.

`/public-booking/*` is unauthenticated and reachable by anyone on the internet,
and it puts meetings on real employees' calendars. Two classes of failure matter
more than everything else here:

  1. A STRANGER DECIDING WHO IS INVOLVED. If any of brand, salesperson,
     leadership, participants or meeting type could be influenced by the request
     body, an outsider could book a meeting onto any employee's calendar, or
     book a rep from one brand into another brand's pipeline. The spoofing
     section below sends every one of those fields and asserts they change
     nothing - they are not validated-and-rejected, they are ignored, which is
     why they are absent from the request model entirely.

  2. A RETRY BECOMING A SECOND BOOKING. A browser posts, the connection dies,
     the visitor presses the button again. Without idempotency they now hold two
     meetings, two Zoom rooms, two confirmation emails and two entries on three
     calendars - and the duplicate is indistinguishable from a real second
     booking. The idempotency section asserts one of everything, including
     under a simulated lost response and under two simultaneous submissions.

NOTHING HERE TOUCHES A REAL PROVIDER. Zoom and calendar sync are exercised
through the platform's own registries with nothing configured, which is the
production-safe state and the same one the shipped build runs in; email goes
through the staff outbound gate, which is off by default. Every assertion about
"sent" is therefore an assertion about what the platform DECIDED, not about what
left the building.
"""

import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity, OpportunityEvent, DiscoveryRecord,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP, STAGE_PROSPECT,
)
from app.models.scheduling_models import (
    AvailabilityWindow, AvailabilityBlock, MeetingType, SalesAppointment,
    AppointmentParticipant, AppointmentReminder,
    APPT_SCHEDULED, CONF_PENDING, BLOCK_TIME_OFF,
    LEADERSHIP_REPORTING_CHAIN, BOOKING_SOURCE_PUBLIC_WEB,
    REMINDER_24H, REMINDER_1H, REMINDER_SKIPPED, REMINDER_PENDING,
    REMINDER_SUPPRESSED, REMINDER_SENT,
)
from app.services import availability as av
from app.services import public_booking as pb
from app.services import sales_booking_codes as codes
from app.services import sales_appointment_reminders as reminders
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)
TZ = "America/Chicago"


# ── fixtures ────────────────────────────────────────────────────────────────

def _user(db, name):
    u = User(organization_id=None, email="pb%d@test.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name,
             role="advisor", must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _seat(db, user, brand, role=ROLE_SALES_REP, reports_to=None):
    m = Membership(user_id=user.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=brand.id, role=role, is_active=True,
                   reports_to_user_id=(reports_to.id if reports_to else None))
    db.add(m)
    db.commit()
    return m


def _profile(db, user, days=range(5)):
    prof = av.get_or_create_profile(db, user, default_timezone=TZ)
    prof.timezone = TZ
    prof.min_notice_minutes = 0
    prof.buffer_before_minutes = 0
    prof.buffer_after_minutes = 0
    prof.booking_horizon_days = 365
    prof.accepts_bookings = True
    db.query(AvailabilityWindow).filter(
        AvailabilityWindow.profile_id == prof.id).delete(synchronize_session=False)
    for dow in days:
        db.add(AvailabilityWindow(profile_id=prof.id, day_of_week=dow,
                                  start_minute=9 * 60, end_minute=17 * 60))
    db.commit()
    return prof


def _monday(offset_days=7):
    d = (datetime.utcnow() + timedelta(days=offset_days)).date()
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d


@pytest.fixture()
def site(db_session):
    """A fully configured brand: platform, intake org, sales org, chain, types."""
    n = next(_SEQ)
    platform = Platform(name="Acme Suite", slug="pb-plat-%d" % n)
    db_session.add(platform)
    db_session.commit()

    org = Organization(name="Acme Intake", slug="pb-intake-%d" % n,
                       platform_id=platform.id, industry="general")
    db_session.add(org)
    db_session.commit()
    platform.public_intake_organization_id = org.id
    db_session.commit()

    bso = BrandSalesOrg(platform_id=platform.id, name="Acme Sales",
                        slug="pb-bso-%d" % n, timezone=TZ)
    db_session.add(bso)
    db_session.commit()

    senior = _user(db_session, "Senior Leader")
    direct = _user(db_session, "Direct Manager")
    rep = _user(db_session, "Assigned Rep")
    _seat(db_session, senior, bso, ROLE_SALES_MANAGER)
    _seat(db_session, direct, bso, ROLE_SALES_MANAGER, reports_to=senior)
    rep_seat = _seat(db_session, rep, bso, ROLE_SALES_REP, reports_to=direct)
    for u in (senior, direct, rep):
        _profile(db_session, u)

    mt = MeetingType(
        brand_sales_org_id=bso.id, key="discovery_demo", name="Discovery + Demo",
        duration_minutes=60, required_slots="opportunity_owner,sales_manager",
        requires_video=True, is_active=True, public_bookable=True,
        leadership_policy=LEADERSHIP_REPORTING_CHAIN, owner_required=True,
        leadership_minimum=1, leadership_depth=2, include_additional_leaders=True)
    internal = MeetingType(
        brand_sales_org_id=bso.id, key="internal", name="Internal Sales Meeting",
        duration_minutes=30, required_slots="any_rep", is_internal=True,
        is_active=True, public_bookable=False)
    db_session.add_all([mt, internal])
    db_session.commit()

    code = codes.issue_code(db_session, rep_seat)
    db_session.commit()
    return {"platform": platform, "org": org, "bso": bso, "mt": mt,
            "internal": internal, "rep": rep, "direct": direct,
            "senior": senior, "rep_seat": rep_seat, "code": code}


def _slots(db, site, day=None):
    day = day or _monday()
    return pb.public_slots(db, site["bso"], site["mt"], site["rep"],
                           av.local_to_utc(day, 0, TZ),
                           av.local_to_utc(day, 23 * 60, TZ))


FORM = {
    "full_name": "Dana Prospect", "company": "Prospect Co",
    "email": "dana@prospect.example", "phone": "+12145550199",
    "industry": "Home services", "primary_challenge": "lead_followup",
    "primary_challenge_label": "Following up with leads consistently",
    "primary_challenge_detail": None, "current_system": "Spreadsheets",
    "locations": "3", "lead_volume": "400", "notes": None,
    "prospect_timezone": "America/New_York",
}


def _book(db, site, starts_at=None, form=None, key=None, owner=None):
    if starts_at is None:
        starts_at = _slots(db, site)["slots"][0]["starts_at"]
    return pb.book(db, platform=site["platform"], bso=site["bso"],
                   intake_org=site["org"], meeting_type=site["mt"],
                   owner=owner or site["rep"], owner_source=codes.OWNER_FROM_CODE,
                   starts_at=starts_at, form=dict(form or FORM),
                   idempotency_key=key)


# ═══════════════════════════════════════════════════════════════════════════
# Booking codes
# ═══════════════════════════════════════════════════════════════════════════

def test_a_valid_code_resolves_to_its_salesperson(db_session, site):
    out = codes.resolve_inbound_owner(db_session, site["bso"], site["code"])
    assert out["ok"] and out["owner"].id == site["rep"].id
    assert out["source"] == codes.OWNER_FROM_CODE


def test_the_code_carries_nothing_about_the_person(site):
    """An enumerable or reversible public id hands out the org chart."""
    code = site["code"]
    assert site["rep"].id not in code
    assert site["rep"].email.split("@")[0] not in code
    assert "rep" not in code.lower()
    assert len(code) >= 24


def test_an_unknown_code_is_refused_and_does_not_fall_back(db_session, site):
    """A typo'd link must not silently route to the brand's default owner.

    Somebody who followed a specific person's link is booking with that person.
    Quietly giving them somebody else is worse than refusing.
    """
    site["bso"].default_inbound_owner_user_id = site["direct"].id
    db_session.commit()
    out = codes.resolve_inbound_owner(db_session, site["bso"], "not-a-real-code")
    assert out["ok"] is False
    assert out["owner"] is None
    assert out["status"] == codes.CODE_UNKNOWN


def test_a_revoked_code_stops_working_without_removing_the_seat(db_session, site):
    codes.revoke_code(db_session, site["rep_seat"])
    db_session.commit()
    out = codes.resolve_inbound_owner(db_session, site["bso"], site["code"])
    assert out["ok"] is False and out["status"] == codes.CODE_REVOKED
    # The seat is untouched: they still sell here, their link just died.
    assert site["rep_seat"].is_active is True


def test_an_inactive_seat_cannot_take_bookings_even_with_a_live_code(
        db_session, site):
    """Somebody who left stops taking bookings whether or not anyone remembered
    to revoke their link. Two separate facts, both checked."""
    site["rep_seat"].is_active = False
    db_session.commit()
    out = codes.resolve_inbound_owner(db_session, site["bso"], site["code"])
    assert out["ok"] is False and out["status"] == codes.CODE_INACTIVE


def test_a_code_cannot_be_replayed_against_another_brand(db_session, site):
    other = BrandSalesOrg(platform_id=site["platform"].id, name="Other Sales",
                          slug="pb-other-%d" % next(_SEQ), timezone=TZ)
    db_session.add(other)
    db_session.commit()
    out = codes.resolve_inbound_owner(db_session, other, site["code"])
    assert out["ok"] is False and out["status"] == codes.CODE_WRONG_BRAND


def test_every_code_failure_tells_the_public_the_same_thing(db_session, site):
    """Distinguishing "revoked" from "no such code" tells an outsider which
    codes exist."""
    codes.revoke_code(db_session, site["rep_seat"])
    db_session.commit()
    a = codes.resolve_inbound_owner(db_session, site["bso"], site["code"])
    b = codes.resolve_inbound_owner(db_session, site["bso"], "nope")
    assert a["message"] == b["message"] == codes.PUBLIC_REFUSAL


def test_rotation_keeps_history_and_kills_the_old_link(db_session, site):
    result = _book(db_session, site, key="rotate-1")
    assert result.ok
    old = site["code"]
    new = codes.issue_code(db_session, site["rep_seat"], rotate=True)
    db_session.commit()
    assert new != old
    assert codes.resolve_inbound_owner(db_session, site["bso"], old)["ok"] is False
    assert codes.resolve_inbound_owner(db_session, site["bso"], new)["ok"] is True
    # The appointment is untouched: it records the OWNER, never the code.
    assert db_session.query(SalesAppointment).filter(
        SalesAppointment.id == result.appointment.id).first() is not None


# ── the no-code path ────────────────────────────────────────────────────────

def test_no_code_uses_the_brands_configured_default_owner(db_session, site):
    site["bso"].default_inbound_owner_user_id = site["rep"].id
    db_session.commit()
    out = codes.resolve_inbound_owner(db_session, site["bso"], None)
    assert out["ok"] and out["owner"].id == site["rep"].id
    assert out["source"] == codes.OWNER_FROM_DEFAULT


def test_no_code_and_no_default_owner_fails_closed(db_session, site):
    """THE BRANCH THAT MUST NOT EXIST is one that picks somebody.

    An arbitrary assignment puts a stranger's meeting on a real person's
    calendar and a real prospect in a pipeline nobody is watching, silently.
    """
    assert site["bso"].default_inbound_owner_user_id is None
    out = codes.resolve_inbound_owner(db_session, site["bso"], None)
    assert out["ok"] is False
    assert out["status"] == codes.OWNER_NOT_CONFIGURED
    assert out["owner"] is None


def test_a_default_owner_who_left_is_a_different_problem(db_session, site):
    site["bso"].default_inbound_owner_user_id = site["rep"].id
    site["rep_seat"].is_active = False
    db_session.commit()
    out = codes.resolve_inbound_owner(db_session, site["bso"], None)
    assert out["status"] == codes.OWNER_DEFAULT_INVALID, (
        "'configured but gone' and 'never configured' need different fixes and "
        "must not share a status")


# ═══════════════════════════════════════════════════════════════════════════
# Meeting type exposure
# ═══════════════════════════════════════════════════════════════════════════

def test_only_public_bookable_types_are_offered(db_session, site):
    keys = [m.key for m in pb.public_meeting_types(db_session, site["bso"])]
    assert keys == ["discovery_demo"]
    assert "internal" not in keys, (
        "an internal meeting type was offered to the public - this is how a "
        "stranger books onto a management team's calendar")


def test_naming_a_non_public_type_resolves_to_nothing_not_the_default(
        db_session, site):
    """Silently substituting a different type books somebody into a meeting
    nobody offered them."""
    assert pb.resolve_meeting_type(db_session, site["bso"], "internal") is None
    assert pb.resolve_meeting_type(db_session, site["bso"], "nope") is None
    assert pb.resolve_meeting_type(db_session, site["bso"], None).key == "discovery_demo"


# ═══════════════════════════════════════════════════════════════════════════
# The booking transaction
# ═══════════════════════════════════════════════════════════════════════════

def test_a_booking_creates_exactly_one_of_everything(db_session, site):
    result = _book(db_session, site, key="one-of-each")
    assert result.ok and result.status == pb.BOOK_OK

    assert db_session.query(SalesAppointment).filter(
        SalesAppointment.brand_sales_org_id == site["bso"].id).count() == 1
    assert db_session.query(Opportunity).filter(
        Opportunity.brand_sales_org_id == site["bso"].id).count() == 1
    parts = db_session.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == result.appointment.id).all()
    assert len(parts) == len({p.user_id for p in parts})
    assert site["rep"].id in {p.user_id for p in parts}


def test_the_prospect_is_already_in_the_pipeline(db_session, site):
    result = _book(db_session, site, key="pipeline")
    opp = result.opportunity
    assert opp.stage == STAGE_PROSPECT
    assert opp.status == "open"
    assert opp.owner_user_id == site["rep"].id
    assert opp.company_name == "Prospect Co"
    assert opp.email == "dana@prospect.example"
    assert opp.industry == "Home services"
    assert opp.timezone == "America/New_York"
    assert "Website" in (opp.source or "")


def test_the_source_carries_the_brands_own_name(db_session, site):
    """Not one brand's wording compiled into core code."""
    result = _book(db_session, site, key="source")
    assert result.opportunity.source == "Acme Suite Website — Discovery + Demo"


def test_the_primary_challenge_lands_in_discovery_not_a_new_model(
        db_session, site):
    result = _book(db_session, site, key="discovery")
    rec = db_session.query(DiscoveryRecord).filter(
        DiscoveryRecord.opportunity_id == result.opportunity.id).first()
    assert rec is not None
    assert "Following up with leads consistently" in rec.bottlenecks
    assert "Spreadsheets" in rec.current_tools
    assert "Locations: 3" in rec.opportunity_notes


def test_the_appointment_records_how_it_arrived_and_forges_no_actor(
        db_session, site):
    result = _book(db_session, site, key="provenance")
    appt = result.appointment
    assert appt.booking_source == BOOKING_SOURCE_PUBLIC_WEB
    assert appt.created_by is None, (
        "a website visitor is not a user; naming one of our people as the "
        "creator would be a forged audit actor")
    event = db_session.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == result.opportunity.id,
        OpportunityEvent.event_type == "appointment_booked").first()
    assert event is not None and event.actor_user_id is None


def test_the_participants_are_the_quorums_and_only_the_quorums(db_session, site):
    result = _book(db_session, site, key="participants")
    ids = {p.user_id for p in db_session.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == result.appointment.id).all()}
    assert site["rep"].id in ids
    assert ids <= {site["rep"].id, site["direct"].id, site["senior"].id}


def test_a_busy_leader_is_not_put_on_the_calendar(db_session, site):
    """Existing in the chain is not the same as being free at that time."""
    day = _monday()
    prof = av.get_or_create_profile(db_session, site["senior"])
    db_session.add(AvailabilityBlock(
        profile_id=prof.id, kind=BLOCK_TIME_OFF, label="PTO",
        starts_at=av.local_to_utc(day, 0, TZ),
        ends_at=av.local_to_utc(day, 23 * 60 + 59, TZ)))
    db_session.commit()

    slot = _slots(db_session, site, day)["slots"][0]
    result = _book(db_session, site, starts_at=slot["starts_at"], key="busy-leader")
    ids = {p.user_id for p in db_session.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == result.appointment.id).all()}
    assert site["senior"].id not in ids
    assert site["direct"].id in ids


def test_booking_a_time_that_is_not_offered_is_refused(db_session, site):
    """3am on a Sunday is not on the list, and asking for it directly must not
    work - the slot list is the contract, not a suggestion."""
    day = _monday()
    off_hours = av.local_to_utc(day, 3 * 60, TZ)
    result = _book(db_session, site, starts_at=off_hours, key="off-hours")
    assert result.ok is False
    assert result.status == pb.BOOK_SLOT_TAKEN


def test_a_slot_taken_between_offer_and_booking_is_refused_cleanly(
        db_session, site):
    day = _monday()
    slot = _slots(db_session, site, day)["slots"][0]

    # Everything in the chain becomes busy after the visitor saw the slot.
    for who in (site["direct"], site["senior"]):
        prof = av.get_or_create_profile(db_session, who)
        db_session.add(AvailabilityBlock(
            profile_id=prof.id, kind=BLOCK_TIME_OFF, label="PTO",
            starts_at=av.local_to_utc(day, 0, TZ),
            ends_at=av.local_to_utc(day, 23 * 60 + 59, TZ)))
    db_session.commit()

    result = _book(db_session, site, starts_at=slot["starts_at"], key="gone")
    assert result.ok is False
    assert result.status == pb.BOOK_SLOT_TAKEN
    assert result.message == pb.SLOT_GONE_MESSAGE
    assert db_session.query(SalesAppointment).count() == 0
    assert db_session.query(Opportunity).count() == 0, (
        "a refused booking left a pipeline record behind")


def test_a_booking_in_the_past_is_refused(db_session, site):
    result = _book(db_session, site,
                   starts_at=datetime.utcnow() - timedelta(days=1), key="past")
    assert result.ok is False and result.status == pb.BOOK_INVALID


# ═══════════════════════════════════════════════════════════════════════════
# Idempotency
# ═══════════════════════════════════════════════════════════════════════════

def test_a_resubmission_returns_the_original_and_creates_nothing(
        db_session, site):
    """THE LOST-RESPONSE CASE. The server did the work; the answer never
    arrived; the visitor pressed the button again."""
    first = _book(db_session, site, key="same-key")
    assert first.ok and first.created

    second = _book(db_session, site, key="same-key")
    assert second.ok
    assert second.status == pb.BOOK_IDEMPOTENT
    assert second.created is False
    assert second.appointment.id == first.appointment.id

    assert db_session.query(SalesAppointment).count() == 1
    assert db_session.query(Opportunity).count() == 1
    assert db_session.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == first.appointment.id).count() \
        == db_session.query(AppointmentParticipant).count()


def test_a_replay_does_not_re_run_the_side_effects(db_session, site):
    first = _book(db_session, site, key="side-effects")
    before = db_session.query(AppointmentReminder).count()
    events_before = db_session.query(OpportunityEvent).count()

    second = _book(db_session, site, key="side-effects")
    assert second.status == pb.BOOK_IDEMPOTENT
    assert db_session.query(AppointmentReminder).count() == before
    assert db_session.query(OpportunityEvent).count() == events_before, (
        "a retry wrote a second timeline entry - the confirmation and "
        "notification chains would have fired twice too")


def test_two_different_submissions_are_two_bookings(db_session, site):
    """Idempotency must not merge a genuine second booking."""
    slots = _slots(db_session, site)["slots"]
    a = _book(db_session, site, starts_at=slots[0]["starts_at"], key="sub-a")
    b = _book(db_session, site, starts_at=slots[4]["starts_at"], key="sub-b")
    assert a.appointment.id != b.appointment.id
    assert db_session.query(SalesAppointment).count() == 2


def test_the_same_prospect_booking_twice_reuses_the_opportunity(db_session, site):
    slots = _slots(db_session, site)["slots"]
    a = _book(db_session, site, starts_at=slots[0]["starts_at"], key="opp-a")
    b = _book(db_session, site, starts_at=slots[4]["starts_at"], key="opp-b")
    assert a.opportunity.id == b.opportunity.id
    assert b.artifacts["opportunity_created"] is False
    assert db_session.query(Opportunity).count() == 1


def test_a_different_person_at_the_same_company_is_a_separate_deal(
        db_session, site):
    """Matching on company alone merges two conversations into one, which is how
    a rep loses a deal to a colleague's record."""
    slots = _slots(db_session, site)["slots"]
    _book(db_session, site, starts_at=slots[0]["starts_at"], key="p1")
    other = dict(FORM, full_name="Sam Second", email="sam@prospect.example")
    _book(db_session, site, starts_at=slots[4]["starts_at"], form=other, key="p2")
    assert db_session.query(Opportunity).count() == 2


def test_an_existing_deal_is_never_reassigned_by_a_website_booking(
        db_session, site):
    """A public URL must not be a way to take another rep's deal."""
    first = _book(db_session, site, key="owned")
    original_owner = first.opportunity.owner_user_id

    other_rep = _user(db_session, "Other Rep")
    _seat(db_session, other_rep, site["bso"], ROLE_SALES_REP,
          reports_to=site["direct"])
    _profile(db_session, other_rep)

    slots = _slots(db_session, site)["slots"]
    second = pb.book(db_session, platform=site["platform"], bso=site["bso"],
                     intake_org=site["org"], meeting_type=site["mt"],
                     owner=other_rep, owner_source=codes.OWNER_FROM_CODE,
                     starts_at=slots[4]["starts_at"], form=dict(FORM),
                     idempotency_key="owned-2")
    assert second.ok
    assert second.opportunity.id == first.opportunity.id
    assert second.opportunity.owner_user_id == original_owner


# ═══════════════════════════════════════════════════════════════════════════
# Provider, calendar, confirmation
# ═══════════════════════════════════════════════════════════════════════════

def test_no_provider_configured_is_recorded_and_does_not_lose_the_booking(
        db_session, site):
    """A Zoom outage must produce a meeting flagged for attention, never a lost
    booking and never a 500 to the person who just booked."""
    result = _book(db_session, site, key="no-zoom")
    assert result.ok
    assert result.artifacts["meeting"]["ok"] is False
    assert result.artifacts["meeting"].get("reason") == "not_configured"
    assert result.appointment.meeting_url is None


def test_the_confirmation_waits_for_a_real_join_link(db_session, site):
    """"You're booked, here's your link" with no link is worse than silence:
    the prospect has nothing to click and no reason to expect better."""
    result = _book(db_session, site, key="await-link")
    conf = result.artifacts["confirmation"]
    assert conf["sent"] is False
    assert conf["status"] == "pending_meeting_link"
    assert result.appointment.demo_confirmation_sent_at is None


def test_a_non_video_type_does_not_wait_for_a_link(db_session, site):
    site["mt"].requires_video = False
    db_session.commit()
    result = _book(db_session, site, key="no-video")
    assert result.artifacts["confirmation"]["status"] != "pending_meeting_link"


def test_delivery_disabled_is_reported_as_itself_not_as_a_failure(
        db_session, site, monkeypatch):
    """The gate being off is the shipped state, not a fault, and an operations
    view must never confuse the two."""
    site["mt"].requires_video = False
    db_session.commit()
    result = _book(db_session, site, key="gated")
    assert result.artifacts["confirmation"]["status"] == "delivery_disabled"
    assert result.artifacts["confirmation"]["sent"] is False


def test_nothing_reaches_a_mail_provider(db_session, site, monkeypatch):
    """The blunt assertion. If the gate ever stops holding, this fails."""
    calls = []
    import app.services.email_service as es
    monkeypatch.setattr(es, "send_email_via_provider",
                        lambda *a, **k: calls.append((a, k)) or {"success": True})
    site["mt"].requires_video = False
    db_session.commit()
    _book(db_session, site, key="no-send")
    assert calls == [], "an email left the building during a test"


# ═══════════════════════════════════════════════════════════════════════════
# Internal notification
# ═══════════════════════════════════════════════════════════════════════════

def test_the_internal_team_is_notified_exactly_once(db_session, site):
    result = _book(db_session, site, key="notify")
    events = db_session.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == result.opportunity.id,
        OpportunityEvent.event_type == "inbound_booking_notified").all()
    assert len(events) == 1
    notification = result.artifacts["notification"]
    assert notification["timeline"] is True
    assert site["rep"].email in notification["recipients"]


def test_a_leader_who_is_not_attending_is_not_notified(db_session, site):
    day = _monday()
    prof = av.get_or_create_profile(db_session, site["senior"])
    db_session.add(AvailabilityBlock(
        profile_id=prof.id, kind=BLOCK_TIME_OFF, label="PTO",
        starts_at=av.local_to_utc(day, 0, TZ),
        ends_at=av.local_to_utc(day, 23 * 60 + 59, TZ)))
    db_session.commit()
    slot = _slots(db_session, site, day)["slots"][0]
    result = _book(db_session, site, starts_at=slot["starts_at"], key="notify-2")
    assert site["senior"].email not in result.artifacts["notification"]["recipients"]


def test_the_notification_carries_what_the_rep_needs_before_the_call(
        db_session, site):
    result = _book(db_session, site, key="notify-3")
    event = db_session.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == result.opportunity.id,
        OpportunityEvent.event_type == "inbound_booking_notified").first()
    assert "Prospect Co" in event.detail
    assert "Following up with leads consistently" in event.detail
    assert "America/New_York" in event.detail


# ═══════════════════════════════════════════════════════════════════════════
# Input hygiene
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("payload", [
    "<script>alert(1)</script>Acme",
    "Acme<img src=x onerror=alert(1)>",
    "Acme\r\nBcc: attacker@evil.example",
    "Acme\x00Corp",
])
def test_markup_and_control_characters_never_reach_the_database(
        db_session, site, payload):
    """Templates escape as well. This is about what gets STORED and then read by
    staff across a dozen internal surfaces for the life of the deal."""
    form = dict(FORM, company=payload)
    result = _book(db_session, site, form=form, key="xss-%s" % hash(payload))
    stored = result.opportunity.company_name
    assert "<" not in stored and ">" not in stored
    assert "\r" not in stored and "\n" not in stored and "\x00" not in stored
    assert "Acme" in stored


def test_a_malformed_timezone_is_dropped_rather_than_stored(db_session, site):
    """`zoneinfo` raises on an unknown key, and a raise inside availability
    computation would turn a typo into a 500 on a public page."""
    assert pb.valid_timezone("Mars/Olympus") is None
    assert pb.valid_timezone("'; DROP TABLE users;--") is None
    assert pb.valid_timezone("America/New_York") == "America/New_York"
    result = _book(db_session, site, form=dict(FORM, prospect_timezone=None),
                   key="tz")
    assert result.appointment.prospect_timezone is None


def test_an_unknown_challenge_value_is_kept_as_text_not_treated_as_a_key(
        db_session, site):
    assert "definitely_not_an_option" not in pb.CHALLENGE_VALUES


def test_the_form_schema_does_not_ask_for_a_package(db_session):
    """Discovery exists to work out which package fits. Asking first asks the
    prospect to do the job they booked the call to have done."""
    schema = pb.form_schema()
    assert schema["package_of_interest"] is False
    assert "package" not in " ".join(schema["required"])
    assert len(schema["primary_challenge_options"]) == 12


# ═══════════════════════════════════════════════════════════════════════════
# Reminders
# ═══════════════════════════════════════════════════════════════════════════

def test_a_booking_schedules_both_reminders(db_session, site):
    result = _book(db_session, site, key="rem-1")
    rows = {r.kind: r for r in db_session.query(AppointmentReminder).filter(
        AppointmentReminder.appointment_id == result.appointment.id).all()}
    assert set(rows) == {REMINDER_24H, REMINDER_1H}
    assert rows[REMINDER_24H].status == REMINDER_PENDING
    assert rows[REMINDER_1H].status == REMINDER_PENDING
    assert rows[REMINDER_24H].scheduled_for == result.appointment.starts_at - timedelta(hours=24)


def test_a_late_booking_skips_the_24_hour_reminder(db_session, site):
    """It never had a moment, which is different from having missed one - and a
    gap in the table would be indistinguishable from a job that never ran."""
    soon = datetime.utcnow() + timedelta(hours=3)
    appt = SalesAppointment(
        brand_sales_org_id=site["bso"].id, title="Soon", starts_at=soon,
        ends_at=soon + timedelta(hours=1), timezone=TZ, status=APPT_SCHEDULED,
        confirmation_status=CONF_PENDING, prospect_email="p@x.example")
    db_session.add(appt)
    db_session.commit()
    reminders.schedule_for(db_session, appt)
    db_session.commit()

    rows = {r.kind: r for r in db_session.query(AppointmentReminder).filter(
        AppointmentReminder.appointment_id == appt.id).all()}
    assert rows[REMINDER_24H].status == REMINDER_SKIPPED
    assert "had no moment" in rows[REMINDER_24H].detail
    assert rows[REMINDER_1H].status == REMINDER_PENDING


def test_two_messages_are_never_stacked_on_the_customer(db_session, site):
    """A meeting booked just over 24 hours out would otherwise send "you're
    booked" and "your meeting is tomorrow" within minutes of each other.

    The two reminders are 23 hours apart by construction and can never crowd
    each other; the message they CAN crowd is the confirmation, which is why the
    rule is measured from the booking rather than between the reminders.
    """
    soon = datetime.utcnow() + timedelta(hours=24, minutes=20)
    appt = SalesAppointment(
        brand_sales_org_id=site["bso"].id, title="Edge", starts_at=soon,
        ends_at=soon + timedelta(hours=1), timezone=TZ, status=APPT_SCHEDULED,
        confirmation_status=CONF_PENDING, prospect_email="p@x.example")
    db_session.add(appt)
    db_session.commit()
    reminders.schedule_for(db_session, appt)
    db_session.commit()
    rows = {r.kind: r for r in db_session.query(AppointmentReminder).filter(
        AppointmentReminder.appointment_id == appt.id).all()}
    assert rows[REMINDER_24H].status == REMINDER_SUPPRESSED
    assert rows[REMINDER_1H].status == REMINDER_PENDING


def test_each_reminder_is_sent_exactly_once_however_often_the_job_runs(
        db_session, site):
    result = _book(db_session, site, key="rem-once")
    appt = result.appointment
    row = db_session.query(AppointmentReminder).filter(
        AppointmentReminder.appointment_id == appt.id,
        AppointmentReminder.kind == REMINDER_24H).first()
    when = row.scheduled_for + timedelta(minutes=5)

    first = reminders.process_due(db_session, now=when, send=False)
    second = reminders.process_due(db_session, now=when, send=False)
    third = reminders.process_due(db_session, now=when + timedelta(minutes=10),
                                  send=False)
    assert first["sent"] == 1
    assert second["sent"] == 0 and third["sent"] == 0
    db_session.refresh(row)
    assert row.status == REMINDER_SENT


def test_a_reschedule_moves_the_reminders_to_the_new_time(db_session, site):
    result = _book(db_session, site, key="resched")
    appt = result.appointment
    old_target = appt.starts_at

    appt.starts_at = appt.starts_at + timedelta(days=2)
    appt.ends_at = appt.ends_at + timedelta(days=2)
    db_session.commit()
    reminders.schedule_for(db_session, appt)
    db_session.commit()

    rows = db_session.query(AppointmentReminder).filter(
        AppointmentReminder.appointment_id == appt.id).all()
    stale = [r for r in rows if r.target_starts_at == old_target]
    fresh = [r for r in rows if r.target_starts_at == appt.starts_at]
    assert all(r.status == REMINDER_SUPPRESSED for r in stale)
    assert {r.kind for r in fresh} == {REMINDER_24H, REMINDER_1H}
    assert all(r.status == REMINDER_PENDING for r in fresh)


def test_a_cancelled_meeting_sends_no_future_reminder(db_session, site):
    result = _book(db_session, site, key="cancel")
    appt = result.appointment
    appt.status = "cancelled"
    db_session.commit()
    reminders.cancel_for(db_session, appt)
    db_session.commit()

    rows = db_session.query(AppointmentReminder).filter(
        AppointmentReminder.appointment_id == appt.id).all()
    assert all(r.status == REMINDER_SUPPRESSED for r in rows)
    row = rows[0]
    report = reminders.process_due(db_session,
                                   now=(row.scheduled_for or datetime.utcnow())
                                   + timedelta(minutes=5), send=False)
    assert report["sent"] == 0


def test_a_reminder_missed_by_hours_is_skipped_not_sent_late(db_session, site):
    """"Your meeting is tomorrow" arriving two hours beforehand is not a
    reminder, it is a confusing second message."""
    result = _book(db_session, site, key="late")
    row = db_session.query(AppointmentReminder).filter(
        AppointmentReminder.appointment_id == result.appointment.id,
        AppointmentReminder.kind == REMINDER_1H).first()
    report = reminders.process_due(db_session,
                                   now=row.scheduled_for + timedelta(hours=4),
                                   send=False)
    db_session.refresh(row)
    assert row.status == REMINDER_SKIPPED
    assert report["sent"] == 0


def test_the_reminder_states_the_time_in_the_prospects_own_zone(db_session, site):
    result = _book(db_session, site, key="rem-tz")
    subject, body = reminders.render(db_session, result.appointment, 24)
    assert "your time" in body
    assert "America/Chicago" in body


def test_a_reminder_with_no_link_offers_no_dead_button(db_session, site):
    result = _book(db_session, site, key="rem-nolink")
    assert result.appointment.meeting_url is None
    _subject, body = reminders.render(db_session, result.appointment, 1)
    assert "Join the meeting" not in body
    assert "confirmation email" in body
