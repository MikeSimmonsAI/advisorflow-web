"""THE PUBLIC ENDPOINTS, from the outside — including what they must not do.

These three routes are unauthenticated and reachable by anyone, and they put
meetings on real employees' calendars. This file is written from the position of
somebody who wants to abuse that.

THE TWO PROPERTIES BEING DEFENDED

  1. NOTHING IN THE REQUEST DECIDES WHO IS INVOLVED. The spoofing section sends
     organization_id, brand_sales_org_id, salesperson_user_id, owner_user_id,
     participant_user_ids, meeting_url and appointment_id, and asserts the
     booking is byte-for-byte what it would have been without them. They are not
     validated and rejected - they are absent from the request model, so there
     is nothing to probe and nothing to smuggle.

  2. THE RESPONSES TEACH AN OUTSIDER NOTHING. No internal ids, no names beyond
     the salesperson whose link the visitor followed, no calendar event titles,
     no leadership, and one identical refusal for every kind of bad code -
     because "that code was revoked" versus "no such code" tells somebody which
     codes exist.
"""

import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (
    AvailabilityWindow, MeetingType, SalesAppointment, AppointmentParticipant,
    LEADERSHIP_REPORTING_CHAIN,
)
from app.services import availability as av
from app.services import sales_booking_codes as codes
from app.services.auth_service import hash_password

_SEQ = itertools.count(1)
TZ = "America/Chicago"


@pytest.fixture()
def site(db_session):
    n = next(_SEQ)
    platform = Platform(name="Acme Suite", slug="api-plat-%d" % n)
    db_session.add(platform)
    db_session.commit()
    org = Organization(name="Acme Intake", slug="api-intake-%d" % n,
                       platform_id=platform.id, industry="general")
    db_session.add(org)
    db_session.commit()
    platform.public_intake_organization_id = org.id
    db_session.commit()

    bso = BrandSalesOrg(platform_id=platform.id, name="Acme Sales",
                        slug="api-bso-%d" % n, timezone=TZ)
    db_session.add(bso)
    db_session.commit()

    def _u(name):
        u = User(organization_id=None, email="api%d@t.live" % next(_SEQ),
                 password_hash=hash_password("x"), full_name=name,
                 role="advisor", must_change_password=False)
        db_session.add(u)
        db_session.commit()
        return u

    def _seat(u, role, reports_to=None):
        m = Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                       scope_id=bso.id, role=role, is_active=True,
                       reports_to_user_id=(reports_to.id if reports_to else None))
        db_session.add(m)
        db_session.commit()
        return m

    def _prof(u):
        p = av.get_or_create_profile(db_session, u, default_timezone=TZ)
        p.timezone = TZ
        p.min_notice_minutes = 0
        p.buffer_before_minutes = p.buffer_after_minutes = 0
        p.booking_horizon_days = 365
        db_session.query(AvailabilityWindow).filter(
            AvailabilityWindow.profile_id == p.id).delete(synchronize_session=False)
        for dow in range(5):
            db_session.add(AvailabilityWindow(profile_id=p.id, day_of_week=dow,
                                              start_minute=9 * 60,
                                              end_minute=17 * 60))
        db_session.commit()

    senior, direct, rep = _u("Senior Leader"), _u("Direct Manager"), _u("Assigned Rep")
    _seat(senior, ROLE_SALES_MANAGER)
    _seat(direct, ROLE_SALES_MANAGER, senior)
    rep_seat = _seat(rep, ROLE_SALES_REP, direct)
    for u in (senior, direct, rep):
        _prof(u)

    db_session.add(MeetingType(
        brand_sales_org_id=bso.id, key="discovery_demo", name="Discovery + Demo",
        duration_minutes=60, requires_video=False, is_active=True,
        public_bookable=True, leadership_policy=LEADERSHIP_REPORTING_CHAIN,
        owner_required=True, leadership_minimum=1, leadership_depth=2,
        include_additional_leaders=True))
    db_session.add(MeetingType(
        brand_sales_org_id=bso.id, key="internal", name="Internal Sales Meeting",
        duration_minutes=30, is_internal=True, is_active=True,
        public_bookable=False))
    db_session.commit()

    code = codes.issue_code(db_session, rep_seat)
    db_session.commit()
    return {"platform": platform, "bso": bso, "rep": rep, "direct": direct,
            "senior": senior, "rep_seat": rep_seat, "code": code,
            "slug": platform.slug}


def _body(**over):
    payload = {
        "full_name": "Dana Prospect", "company": "Prospect Co",
        "email": "dana@prospect.example", "phone": "+12145550199",
        "industry": "Home services", "primary_challenge": "lead_followup",
        "timezone": "America/New_York", "submission_id": "sub-%d" % next(_SEQ),
    }
    payload.update(over)
    return payload


def _first_slot(client, site):
    r = client.get("/public-booking/%s/slots?code=%s" % (site["slug"], site["code"]))
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]
    assert slots, "no slots offered"
    return slots[0]["start_utc"]


# ═══════════════════════════════════════════════════════════════════════════
# 1. resolve
# ═══════════════════════════════════════════════════════════════════════════

def test_meeting_describes_what_is_on_offer(client, site):
    r = client.get("/public-booking/%s/meeting?code=%s" % (site["slug"], site["code"]))
    assert r.status_code == 200
    body = r.json()
    assert body["meeting"]["key"] == "discovery_demo"
    assert body["meeting"]["duration_minutes"] == 60
    assert body["salesperson"]["name"] == "Assigned Rep"
    assert body["assigned_via"] == "link"
    assert body["bookable"] is True


def test_the_form_schema_is_served_by_the_backend(client, site):
    """So the wording on the page and the values that arrive back are the same
    strings by construction, not by two people editing two files."""
    body = client.get("/public-booking/%s/meeting" % site["slug"],
                      params={"code": site["code"]}).json()
    options = body["form"]["primary_challenge_options"]
    assert len(options) == 12
    assert options[-1]["value"] == "other"
    assert body["form"]["package_of_interest"] is False


def test_no_internal_identifier_appears_anywhere_in_the_payload(client, site):
    """The blunt assertion. Any id in a public response is an id somebody can
    enumerate."""
    raw = client.get("/public-booking/%s/meeting?code=%s"
                     % (site["slug"], site["code"])).text
    for secret in (site["rep"].id, site["direct"].id, site["senior"].id,
                   site["bso"].id, site["rep"].email, site["direct"].email):
        assert secret not in raw, "a public response leaked %r" % secret


def test_no_leadership_is_named_to_the_public(client, site):
    """A booking page that listed a rep's managers would publish the company's
    reporting structure."""
    raw = client.get("/public-booking/%s/meeting?code=%s"
                     % (site["slug"], site["code"])).text
    assert "Direct Manager" not in raw
    assert "Senior Leader" not in raw


def test_an_unknown_site_is_a_flat_404(client):
    r = client.get("/public-booking/no-such-brand/meeting")
    assert r.status_code == 404


def test_a_rep_with_no_chain_is_reported_as_unbookable_not_as_an_empty_diary(
        client, db_session, site):
    m = db_session.query(Membership).filter(
        Membership.user_id == site["rep"].id).first()
    m.reports_to_user_id = None
    db_session.commit()
    body = client.get("/public-booking/%s/meeting?code=%s"
                      % (site["slug"], site["code"])).json()
    assert body["bookable"] is False
    assert body["message"]
    # The internal reason names people and must not travel.
    assert "manager" not in body["message"].lower() or "reporting" not in body["message"].lower()


# ═══════════════════════════════════════════════════════════════════════════
# 2. slots
# ═══════════════════════════════════════════════════════════════════════════

def test_slots_come_back_in_utc_and_in_both_local_zones(client, site):
    body = client.get("/public-booking/%s/slots" % site["slug"],
                      params={"code": site["code"],
                              "timezone": "America/New_York"}).json()
    slot = body["slots"][0]
    assert slot["start_utc"].endswith("Z")
    assert slot["meeting_timezone"] == TZ
    assert slot["visitor_timezone"] == "America/New_York"
    # An hour apart, which is what makes sending both worth doing.
    assert slot["start_meeting_local"] != slot["start_visitor_local"]


def test_slots_carry_no_participant_information(client, site):
    raw = client.get("/public-booking/%s/slots?code=%s"
                     % (site["slug"], site["code"])).text
    for secret in (site["rep"].id, site["direct"].id, site["senior"].id):
        assert secret not in raw
    assert "leader" not in raw.lower()


def test_a_malformed_timezone_does_not_500_a_public_page(client, site):
    r = client.get("/public-booking/%s/slots" % site["slug"],
                   params={"code": site["code"], "timezone": "Mars/Olympus"})
    assert r.status_code == 200
    assert r.json().get("visitor_timezone") is None


def test_a_malformed_date_range_falls_back_rather_than_failing(client, site):
    r = client.get("/public-booking/%s/slots" % site["slug"],
                   params={"code": site["code"], "from": "not-a-date",
                           "to": "also-not"})
    assert r.status_code == 200


def test_an_enormous_window_is_clamped(client, site):
    """An unbounded range is a free way to make the server compute a year of
    availability for three people."""
    body = client.get("/public-booking/%s/slots" % site["slug"],
                      params={"code": site["code"],
                              "from": datetime.utcnow().date().isoformat(),
                              "to": (datetime.utcnow() + timedelta(days=900)
                                     ).date().isoformat()}).json()
    span = (datetime.fromisoformat(body["window"]["to"])
            - datetime.fromisoformat(body["window"]["from"])).days
    assert span <= 60


# ═══════════════════════════════════════════════════════════════════════════
# 3. book
# ═══════════════════════════════════════════════════════════════════════════

def test_a_booking_returns_a_thin_confirmation(client, db_session, site):
    start = _first_slot(client, site)
    r = client.post("/public-booking/%s/book" % site["slug"],
                    json=_body(code=site["code"], start_utc=start))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "booked"
    assert body["already_booked"] is False
    assert body["duration_minutes"] == 60
    assert body["reference"]
    assert "confirmation_email" in body
    # Nothing about who else is attending, and no host link.
    raw = r.text
    for secret in (site["direct"].id, site["senior"].id, site["direct"].email):
        assert secret not in raw


def test_the_confirmation_reports_the_true_email_state(client, site):
    """Promising an email that is not coming is how a real booking looks
    broken."""
    start = _first_slot(client, site)
    body = client.post("/public-booking/%s/book" % site["slug"],
                       json=_body(code=site["code"], start_utc=start)).json()
    assert body["confirmation_email"]["sent"] is False
    assert body["confirmation_email"]["status"] == "delivery_disabled"


def test_resubmitting_returns_the_same_booking(client, db_session, site):
    start = _first_slot(client, site)
    payload = _body(code=site["code"], start_utc=start)
    first = client.post("/public-booking/%s/book" % site["slug"], json=payload)
    second = client.post("/public-booking/%s/book" % site["slug"], json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["reference"] == first.json()["reference"]
    assert second.json()["already_booked"] is True
    assert db_session.query(SalesAppointment).count() == 1


def test_a_taken_slot_is_409_with_something_the_visitor_can_act_on(
        client, db_session, site):
    start = _first_slot(client, site)
    client.post("/public-booking/%s/book" % site["slug"],
                json=_body(code=site["code"], start_utc=start))
    r = client.post("/public-booking/%s/book" % site["slug"],
                    json=_body(code=site["code"], start_utc=start))
    assert r.status_code == 409
    assert "another" in r.json()["detail"].lower()


def test_a_bad_code_is_refused_identically_however_it_is_bad(client, db_session, site):
    start = _first_slot(client, site)
    unknown = client.post("/public-booking/%s/book" % site["slug"],
                          json=_body(code="nope", start_utc=start))
    codes.revoke_code(db_session, site["rep_seat"])
    db_session.commit()
    revoked = client.post("/public-booking/%s/book" % site["slug"],
                          json=_body(code=site["code"], start_utc=start))
    assert unknown.status_code == revoked.status_code == 404
    assert unknown.json()["detail"] == revoked.json()["detail"]


def test_no_code_and_no_default_owner_is_503_not_a_random_assignment(
        client, db_session, site):
    r = client.post("/public-booking/%s/book" % site["slug"],
                    json=_body(start_utc=(datetime.utcnow() + timedelta(days=9)
                                          ).replace(microsecond=0).isoformat() + "Z"))
    assert r.status_code == 503
    assert db_session.query(SalesAppointment).count() == 0
    assert db_session.query(Opportunity).count() == 0


def test_an_unparseable_start_time_is_422(client, site):
    r = client.post("/public-booking/%s/book" % site["slug"],
                    json=_body(code=site["code"], start_utc="whenever"))
    assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# SPOOFING — the fields that must not exist
# ═══════════════════════════════════════════════════════════════════════════

def test_every_identity_field_an_attacker_would_send_is_ignored(
        client, db_session, site):
    """THE CENTRAL SECURITY ASSERTION OF THIS FEATURE.

    Send all of them at once, pointed at people and objects the caller has no
    business naming, and assert the booking is exactly what it would have been
    without any of them.
    """
    outsider = User(organization_id=None, email="outsider@t.live",
                    password_hash=hash_password("x"), full_name="Outsider",
                    role="advisor", must_change_password=False)
    db_session.add(outsider)
    db_session.commit()

    start = _first_slot(client, site)
    r = client.post("/public-booking/%s/book" % site["slug"], json=_body(
        code=site["code"], start_utc=start,
        organization_id="some-other-org",
        brand_sales_org_id="some-other-brand",
        salesperson_user_id=outsider.id,
        owner_user_id=outsider.id,
        user_id=outsider.id,
        participant_user_ids=[outsider.id],
        required_user_ids=[outsider.id],
        meeting_url="https://evil.example/join",
        meeting_provider="evil",
        appointment_id="stolen-appointment-id",
        opportunity_id="stolen-opportunity-id",
        created_by=outsider.id,
        booking_source="internal",
        status="completed",
    ))
    assert r.status_code == 201, r.text

    appt = db_session.query(SalesAppointment).one()
    assert appt.brand_sales_org_id == site["bso"].id
    assert appt.id != "stolen-appointment-id"
    assert appt.meeting_url is None, "a caller-supplied meeting URL was stored"
    assert appt.created_by is None
    assert appt.booking_source == "public_web"
    assert appt.status == "scheduled"

    parts = {p.user_id for p in db_session.query(AppointmentParticipant).all()}
    assert outsider.id not in parts, (
        "a request body put a stranger on an employee's calendar")
    assert site["rep"].id in parts

    opp = db_session.query(Opportunity).one()
    assert opp.brand_sales_org_id == site["bso"].id
    assert opp.owner_user_id == site["rep"].id


def test_a_code_from_another_brand_cannot_book_here(client, db_session, site):
    """Two fully configured brands. A code minted in one is offered to the
    other's website."""
    n = next(_SEQ)
    other_platform = Platform(name="Other Brand", slug="api-other-%d" % n)
    db_session.add(other_platform)
    db_session.commit()
    other_org = Organization(name="Other Intake", slug="api-oint-%d" % n,
                             platform_id=other_platform.id, industry="general")
    db_session.add(other_org)
    db_session.commit()
    other_platform.public_intake_organization_id = other_org.id
    other_bso = BrandSalesOrg(platform_id=other_platform.id, name="Other Sales",
                              slug="api-obso-%d" % n, timezone=TZ)
    db_session.add(other_bso)
    db_session.commit()

    r = client.post("/public-booking/%s/book" % other_platform.slug,
                    json=_body(code=site["code"],
                               start_utc=(datetime.utcnow() + timedelta(days=9)
                                          ).replace(microsecond=0).isoformat() + "Z"))
    assert r.status_code in (404, 503)
    assert db_session.query(SalesAppointment).count() == 0


def test_an_internal_meeting_type_cannot_be_booked_from_the_internet(
        client, db_session, site):
    """public_bookable defaults FALSE precisely so this is impossible."""
    start = _first_slot(client, site)
    r = client.post("/public-booking/%s/book" % site["slug"],
                    json=_body(code=site["code"], start_utc=start,
                               meeting_type="internal"))
    assert r.status_code == 503
    assert db_session.query(SalesAppointment).count() == 0


def test_script_tags_in_every_text_field_are_stripped_before_storage(
        client, db_session, site):
    start = _first_slot(client, site)
    evil = "<script>alert(1)</script>"
    r = client.post("/public-booking/%s/book" % site["slug"], json=_body(
        code=site["code"], start_utc=start,
        full_name=evil + "Dana", company=evil + "Acme",
        notes=evil + "note", current_system=evil + "sys"))
    assert r.status_code == 201
    opp = db_session.query(Opportunity).one()
    appt = db_session.query(SalesAppointment).one()
    for value in (opp.company_name, opp.contact_name, appt.prospect_name,
                  appt.notes or ""):
        assert "<script>" not in value and "</script>" not in value


def test_a_header_injection_attempt_in_the_email_field_is_rejected(
        client, db_session, site):
    start = _first_slot(client, site)
    r = client.post("/public-booking/%s/book" % site["slug"],
                    json=_body(code=site["code"], start_utc=start,
                               email="a@b.example\r\nBcc: attacker@evil.example"))
    assert r.status_code == 422
    assert db_session.query(SalesAppointment).count() == 0


def test_the_request_model_has_no_identity_fields_at_all(client):
    """Asserted against the model rather than its behaviour.

    Behaviour tests prove the fields are ignored TODAY. This proves nobody has
    added one - because a field that exists is a field somebody will eventually
    wire up.
    """
    from app.routers.public_booking_router import BookRequest
    forbidden = {"organization_id", "brand_sales_org_id", "owner_user_id",
                 "salesperson_user_id", "user_id", "participant_user_ids",
                 "required_user_ids", "meeting_url", "appointment_id",
                 "opportunity_id", "created_by", "booking_source", "status",
                 "platform_id"}
    assert set(BookRequest.model_fields) & forbidden == set()
