"""Survey answers land on the person's record and on the SCI dashboard; 1-3 stars are alerts."""
from app.models.models import BookingFollowup, BookingLink, SurveyResponse
from app.models.program_models import LocationProfile
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401


def _survey(db, lead, advisor):
    bl = BookingLink(lead_id=lead.id, user_id=advisor.id)
    db.add(bl)
    db.flush()
    f = BookingFollowup(booking_link_id=bl.id, lead_id=lead.id, advisor_id=advisor.id)
    db.add(f)
    db.commit()
    return f.survey_token


def test_answer_is_kept_with_location_and_share_ok(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    tok = _survey(db, lead, admin)
    r = client.post("/survey/%s" % tok, json={"rating": 5, "feedback": "Lovely", "share_ok": True})
    assert r.status_code == 200, r.text
    row = db.query(SurveyResponse).filter_by(lead_id=lead.id).one()
    prof = db.query(LocationProfile).filter_by(organization_id=org.id,
                                               official_name="Alabama Heritage Funeral Home").one()
    assert row.share_ok is True and row.location_id == prof.location_id
    res = client.get("/survey/results/%s" % lead.id, headers=_h(db, admin)).json()
    assert res["responses"][0]["location"] == "Alabama Heritage Funeral Home"
    assert res["responses"][0]["share_ok"] is True


def test_low_rating_is_a_dashboard_alert_until_handled(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    h = _h(db, admin)
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="Rosa", email="rd@example.com")
    client.post("/survey/%s" % _survey(db, lead, admin), json={"rating": 2, "feedback": "Waited too long"})
    s = client.get("/program/surveys", headers=h).json()
    assert s["summary"]["count"] == 1 and s["summary"]["average"] == 2
    assert len(s["alerts"]) == 1 and s["alerts"][0]["location"] == "Alabama Heritage Funeral Home"
    assert client.get("/program/dashboard", headers=h).json()["attention"]["survey_alerts"] == 1
    rid = s["alerts"][0]["id"]
    done = client.post("/program/surveys/%s/handled" % rid, headers=h)
    assert done.status_code == 200 and done.json()["alert"] is False
    assert client.get("/program/surveys", headers=h).json()["alerts"] == []
    assert client.get("/program/dashboard", headers=h).json()["attention"]["survey_alerts"] == 0
