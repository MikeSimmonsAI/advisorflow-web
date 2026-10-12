"""An SCI survey names the funeral home or cemetery the family dealt with, shows
only that location's phone, and capitalizes the family's name."""
from app.models.models import BookingFollowup, BookingLink
from app.models.program_models import LocationProfile
from app.services.survey_identity import location_identity, proper_name
from tests.test_outreach_program import _lead_for, program  # noqa: F401


def _survey(db, lead, advisor):
    bl = BookingLink(lead_id=lead.id, user_id=advisor.id)
    db.add(bl)
    db.flush()
    f = BookingFollowup(booking_link_id=bl.id, lead_id=lead.id, advisor_id=advisor.id)
    db.add(f)
    db.commit()
    return f.survey_token


def test_proper_name():
    assert proper_name("jackson") == "Jackson" and proper_name("ROSA") == "Rosa"
    assert proper_name("McDonald") == "McDonald" and proper_name("  mary  ann ") == "Mary Ann"
    assert proper_name(None) == ""


def test_survey_page_names_the_location_and_its_phone_only(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    prof = db.query(LocationProfile).filter_by(organization_id=org.id,
                                               official_name="Alabama Heritage Funeral Home").one()
    prof.facility_phone = "+12565550199"
    db.commit()
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    assert location_identity(db, lead) == {"name": "Alabama Heritage Funeral Home",
                                           "phone": "+12565550199", "resolved": True}
    r = client.get("/survey/%s/context" % _survey(db, lead, admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["business_name"] == "Alabama Heritage Funeral Home"
    assert body["branding"]["name"] == "Alabama Heritage Funeral Home"
    assert body["business_phone"] == "+12565550199" and body["branding"]["phone"] == "+12565550199"
    assert body["first_name"] == "Rosa"
    assert "Service Corporation" not in r.text


def test_location_without_a_phone_shows_no_phone(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="ROSA", email="rd@example.com")
    body = client.get("/survey/%s/context" % _survey(db, lead, admin)).json()
    assert body["business_name"] == "Alabama Heritage Funeral Home"
    assert not body["business_phone"] and not body["branding"]["phone"]


def test_unresolved_location_gets_no_name(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L006", phone="2145550106", first="Lee", email="lm@example.com")
    assert location_identity(db, lead) == {"name": None, "phone": None, "resolved": False}
    body = client.get("/survey/%s/context" % _survey(db, lead, admin)).json()
    assert body["business_name"] == "" and not body["business_phone"]
