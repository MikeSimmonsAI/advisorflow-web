"""#405 end to end through the REAL SCI screens' API. Owner rule (Oct 26):
every appointment gets ONE survey automatically 24 hours after it ENDS - no
staff action. The Appointments buttons are optional: Completed / Follow-up
needed are for reporting; No-show / Cancelled stop the survey. Synthetic records; the gateways
are fakes, nothing reaches a provider."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.models import BookingFollowup, BookingLink, Lead, LeadOutcome, Organization, User
from app.services import survey_dispatch as SD
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401
from tests.test_sci_405_survey_repair import FakeGateways, on  # noqa: F401

CT = ZoneInfo("America/Chicago")


def _central_now():
    return datetime.now(CT).replace(tzinfo=None, microsecond=0)


def _visit(db, lead, advisor, *, starts_ago=timedelta(hours=3), minutes=60, status="booked"):
    bl = BookingLink(lead_id=lead.id, user_id=advisor.id, status=status,
                     booked_time=_central_now() - starts_ago, appt_duration=minutes)
    db.add(bl)
    db.commit()
    return bl


def _appts(client, db, user, when="all"):
    r = client.get("/program/appointments?when=%s" % when, headers=_h(db, user))
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()["items"]}


def _mark(client, db, user, booking_id, attendance, **extra):
    return client.post("/program/appointments/%s/outcome" % booking_id, headers=_h(db, user),
                       json=dict(attendance=attendance, **extra))


def test_appointment_surveys_itself_24_hours_after_it_ends_with_no_staff_action(client, program, on):
    """Owner rule: ONE APPOINTMENT -> 24 HOURS AFTER IT ENDS -> ONE SURVEY."""
    db, org, admin = program["db"], program["org"], program["admin"]
    gw = FakeGateways(on, db)
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    lead.status = "booked"
    db.commit()
    bl = _visit(db, lead, admin, starts_ago=timedelta(minutes=80))       # ended 20 minutes ago
    row = _appts(client, db, admin)[bl.id]
    assert row["attendance"] is None and row["survey_note"] == "Survey 24 hours after the visit"
    now = datetime.utcnow()
    assert SD.run(db, now)["sent"] == 0                                    # 20 minutes after: no
    assert SD.run(db, now + timedelta(hours=23, minutes=39))["sent"] == 0  # 23 h 59 m after the end: no
    assert gw.texts == [] and gw.emails == []
    assert SD.run(db, now + timedelta(hours=23, minutes=41))["sent"] == 1  # 24 h 1 m: yes, nobody touched it
    assert SD.run(db, now + timedelta(hours=30))["sent"] == 0              # never again
    assert len(gw.texts) + len(gw.emails) == 1
    db.refresh(bl)
    db.refresh(lead)
    assert bl.status == "booked" and lead.status == "booked"               # booking and pipeline untouched
    assert _appts(client, db, admin)[bl.id]["survey_note"] == "Survey sent"


def test_optional_completed_button_is_saved_for_reporting(client, program, on):
    db, org, admin = program["db"], program["org"], program["admin"]
    gw = FakeGateways(on, db)
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = _visit(db, lead, admin, starts_ago=timedelta(minutes=80))
    row = _appts(client, db, admin)[bl.id]
    assert row["started"] and row["ended"] and row["can_record"]
    r = _mark(client, db, admin, bl.id, "completed", location_id=row["location_id"])
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "Completed" and r.json()["survey_note"] == "Survey 24 hours after the visit"
    out = db.query(LeadOutcome).filter_by(booking_link_id=bl.id).one()
    assert out.lead_id == lead.id and out.attendance == "completed" and out.attendance_set_by == admin.id
    assert out.location_id == row["location_id"] and out.location_id and out.attendance_set_at
    assert _appts(client, db, admin)[bl.id]["attendance_label"] == "Completed"
    # Completed does not speed it up: still exactly 24 h after the end
    assert SD.run(db, datetime.utcnow() + timedelta(hours=2))["sent"] == 0
    assert SD.run(db, datetime.utcnow() + timedelta(hours=24))["sent"] == 1
    assert _mark(client, db, admin, bl.id, "no_show").status_code == 409     # locked once surveyed


@pytest.mark.parametrize("attendance", ["no_show", "cancelled"])
def test_no_show_or_cancelled_stops_the_survey(client, program, on, attendance):
    db, org, admin = program["db"], program["org"], program["admin"]
    gw = FakeGateways(on, db)
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = _visit(db, lead, admin)
    assert _mark(client, db, admin, bl.id, attendance).status_code == 200
    assert SD.run(db, datetime.utcnow() + timedelta(hours=26))["sent"] == 0
    assert gw.texts == [] and gw.emails == [] and db.query(BookingFollowup).count() == 0
    assert _appts(client, db, admin)[bl.id]["survey_note"].startswith("No survey")


def test_follow_up_needed_still_gets_the_survey(client, program, on):
    db, org, admin = program["db"], program["org"], program["admin"]
    gw = FakeGateways(on, db)
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = _visit(db, lead, admin)
    assert _mark(client, db, admin, bl.id, "follow_up_needed").status_code == 200
    assert SD.run(db, datetime.utcnow() + timedelta(hours=23))["sent"] == 1


def test_completed_is_refused_before_the_visit_is_over(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    upcoming = _visit(db, lead, admin, starts_ago=-timedelta(hours=4))
    during = _visit(db, lead, admin, starts_ago=timedelta(minutes=20))
    r = _mark(client, db, admin, upcoming.id, "completed")
    assert r.status_code == 422 and "hasn't happened yet" in r.json()["detail"]
    assert _mark(client, db, admin, upcoming.id, "no_show").status_code == 422
    r = _mark(client, db, admin, during.id, "completed")
    assert r.status_code == 422 and "once the visit is over" in r.json()["detail"]
    items = _appts(client, db, admin, "all")
    assert items[upcoming.id]["can_record"] is False and items[during.id]["ended"] is False
    assert db.query(LeadOutcome).count() == 0


def test_double_tap_saves_once_and_outcome_can_be_corrected_before_a_survey(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = _visit(db, lead, admin)
    assert _mark(client, db, admin, bl.id, "no_show").json()["unchanged"] is False
    assert _mark(client, db, admin, bl.id, "no_show").json()["unchanged"] is True
    assert _mark(client, db, admin, bl.id, "completed").json()["attendance"] == "completed"
    assert db.query(LeadOutcome).filter_by(booking_link_id=bl.id).count() == 1


def test_bad_value_and_wrong_location_refused(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = _visit(db, lead, admin)
    assert _mark(client, db, admin, bl.id, "attended").status_code == 422
    r = _mark(client, db, admin, bl.id, "completed", location_id="some-other-location")
    assert r.status_code == 409 and "different location" in r.json()["detail"]
    assert db.query(LeadOutcome).count() == 0


def test_another_organizations_visit_cannot_be_touched(client, program, db_session):
    db, org, admin = program["db"], program["org"], program["admin"]
    other = Organization(name="Other Funeral Co", slug="other-fc", plan="standard", industry="funeral")
    db.add(other)
    db.flush()
    other_user = User(organization_id=other.id, email="o@other.example", password_hash="x", full_name="O",
                      role="org_admin", must_change_password=False)
    db.add(other_user)
    db.flush()
    other_lead = Lead(organization_id=other.id, first_name="Pat", last_name="Elsewhere", phone="12145550999",
                      status="booked")
    db.add(other_lead)
    db.commit()
    theirs = _visit(db, other_lead, other_user)
    # SCI staff HAVE a program, so this exercises the organization filter in
    # visit_outcome.record(): another org's booking is simply not found.
    assert _mark(client, db, admin, theirs.id, "completed").status_code == 404
    # The other org's admin has no SCI program at all: refused before any lookup.
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    ours = _visit(db, lead, admin)
    assert _mark(client, db, other_user, ours.id, "completed").status_code in (403, 404)
    assert db.query(LeadOutcome).count() == 0


def test_a_plain_advisor_cannot_record(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    adv = User(organization_id=org.id, email="adv@sci.example", password_hash="x", full_name="Adv",
               role="advisor", must_change_password=False)
    db.add(adv)
    db.commit()
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = _visit(db, lead, admin)
    assert _mark(client, db, adv, bl.id, "completed").status_code == 403
    assert client.post("/program/appointments/%s/outcome" % bl.id, json={"attendance": "completed"}).status_code == 401


def test_walter_2pm_visit_never_surveyed_early_then_once_at_24_hours(program, on):
    """Walter Johnson's exact day: 2-3 PM CT Oct 22. Never at 10:27 AM; one
    survey at 3 PM CT Oct 23, with no staff action."""
    db, org, admin = program["db"], program["org"], program["admin"]
    gw = FakeGateways(on, db)
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="walter", email="wj@example.com")
    bl = BookingLink(lead_id=lead.id, user_id=admin.id, status="booked", booked_time=datetime(2026, 10, 22, 14, 0),
                     appt_duration=60)
    db.add(bl)
    db.commit()
    assert SD.run(db, datetime(2026, 10, 22, 15, 27))["sent"] == 0             # 10:27 AM CT Oct 22
    assert SD.run(db, datetime(2026, 10, 22, 21, 1))["sent"] == 0              # 4:01 PM CT (old 1-hour rule)
    assert SD.run(db, datetime(2026, 10, 23, 19, 59))["sent"] == 0             # 2:59 PM CT Oct 23
    assert gw.texts == [] and gw.emails == []
    assert SD.run(db, datetime(2026, 10, 23, 20, 0))["sent"] == 1              # 3:00 PM CT Oct 23
    assert SD.run(db, datetime(2026, 10, 23, 23, 0))["sent"] == 0


def test_sending_switched_off_means_nothing_even_when_completed(client, program, monkeypatch):
    db, org, admin = program["db"], program["org"], program["admin"]
    monkeypatch.delenv("SURVEY_SEND_ENABLED", raising=False)
    gw = FakeGateways(monkeypatch, db)
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = _visit(db, lead, admin)
    assert _mark(client, db, admin, bl.id, "completed").json()["survey_note"] == "Surveys are switched off"
    assert SD.run(db, datetime.utcnow() + timedelta(hours=26))["sent"] == 0 and gw.texts == []


# ── review follow-ups ───────────────────────────────────────────────────────

def test_no_show_marked_after_listing_is_never_surveyed(client, program, on):
    """A pass lists the visit; staff mark it No-show before the pass reaches
    it: the claim itself re-checks and sends nothing."""
    db, org, admin = program["db"], program["org"], program["admin"]
    gw = FakeGateways(on, db)
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = _visit(db, lead, admin)
    later = datetime.utcnow() + timedelta(hours=26)
    listed = SD.candidates(db, later)
    assert [b.id for b in listed] == [bl.id]
    assert _mark(client, db, admin, bl.id, "no_show").status_code == 200
    assert SD.claim(db, listed[0], later) is None
    assert gw.texts == [] and gw.emails == [] and db.query(BookingFollowup).count() == 0


def test_a_checklist_only_outcome_never_hides_or_overrides_attendance(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = _visit(db, lead, admin)
    assert _mark(client, db, admin, bl.id, "completed").status_code == 200
    db.add(LeadOutcome(lead_id=lead.id, recorded_by_id=admin.id, booking_link_id=bl.id, has_marker=True))
    db.commit()
    assert _appts(client, db, admin)[bl.id]["attendance"] == "completed"
    assert SD.attendance(db, bl) == "completed"


def test_old_outcomes_api_cannot_mark_completed_early(client, program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    during = _visit(db, lead, admin, starts_ago=timedelta(minutes=10))
    r = client.post("/outcomes/", headers=_h(db, admin),
                    json={"lead_id": lead.id, "booking_link_id": during.id, "attendance": "completed"})
    assert r.status_code == 422 and "once the visit is over" in r.json()["detail"]


def test_survey_held_if_outcome_changes_after_the_claim(program, on):
    db, org, admin = program["db"], program["org"], program["admin"]
    gw = FakeGateways(on, db)
    lead = _lead_for(db, org, admin, "L007", phone="2145550107", first="rosa", email="rd@example.com")
    bl = _visit(db, lead, admin)
    row = LeadOutcome(lead_id=lead.id, recorded_by_id=admin.id, booking_link_id=bl.id, attendance="completed",
                      attendance_set_at=datetime.utcnow() - timedelta(hours=1))
    db.add(row)
    db.commit()
    later = datetime.utcnow() + timedelta(hours=26)
    fu = SD.claim(db, bl, later)
    assert fu is not None
    row.attendance, row.attendance_set_at = "no_show", datetime.utcnow()
    db.commit()
    from app.models.models import User
    assert SD.deliver(db, fu, bl, lead, db.query(User).filter_by(id=admin.id).one()) == "held"
    assert gw.texts == [] and gw.emails == []
