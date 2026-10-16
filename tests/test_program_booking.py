"""SCI booking: each family gets its own link to a page under its location's name,
with a phone-call-or-visit choice; nothing is minted without a booking user."""
from app.models.models import BookingLink, User
from app.models.program_models import LocationProfile, OutreachProgram
from app.services.programs import identity
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401


def _booking_user(db, org):
    u = User(organization_id=org.id, email="booker@example.com", password_hash="x",
             full_name="Booker", role="org_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def test_no_booking_user_means_no_link(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    f = identity._with_booking(db, lead, "Hi {first_name}. {booking_line}", {"first_name": "Rosa"})
    assert f["booking_line"] == ""
    assert db.query(BookingLink).filter_by(lead_id=lead.id).count() == 0
    # a text that does not use a link mints none
    f2 = identity._with_booking(db, lead, "Hi {first_name}.", {"first_name": "Rosa"})
    assert f2["booking_line"] == ""


def test_settings_set_booking_user(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    u = _booking_user(db, org)
    h = _h(db, admin)
    bad = client.patch("/program/settings", json={"booking_user_id": "nope"}, headers=h)
    assert bad.status_code == 422
    ok = client.patch("/program/settings", json={"booking_user_id": u.id}, headers=h)
    assert ok.status_code == 200, ok.text
    assert db.query(OutreachProgram).filter_by(organization_id=org.id).one().booking_user_id == u.id


def test_one_link_per_family_and_page_shows_location(client, program, monkeypatch):
    monkeypatch.setenv("SCI_SMS_LINKS", "on")                # links switched on for this check
    db, org, admin = program["db"], program["org"], program["admin"]
    u = _booking_user(db, org)
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    prog.booking_user_id = u.id
    prof = db.query(LocationProfile).filter_by(organization_id=org.id,
                                               official_name="Alabama Heritage Funeral Home").one()
    prof.facility_phone = "+12565550199"
    db.commit()
    from app.services import public_identity
    monkeypatch.setattr(public_identity, "booking_url", lambda db, org_id, token: "https://app.example.test/book/" + token)
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    a = identity._with_booking(db, lead, "{booking_line}", {})
    b = identity._with_booking(db, lead, "{booking_line}", {})
    assert a["booking_line"].startswith("Pick a time that works for you: https://app.example.test/book/")
    assert a["booking_line"] == b["booking_line"]
    links = db.query(BookingLink).filter_by(lead_id=lead.id).all()
    assert len(links) == 1 and links[0].user_id == u.id
    page = client.get("/calendar/booking/%s" % links[0].token).json()
    assert page["org_name"] == "Alabama Heritage Funeral Home" and page["org_phone"] == "+12565550199"
    assert page["branding"]["name"] == "Alabama Heritage Funeral Home"
    assert page["meeting_options"] is None
    assert page["appt_label"] == "Visit at Alabama Heritage Funeral Home" and page["appt_duration"] == 60
    assert "Service Corporation" not in str(page)
