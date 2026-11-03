"""#482 Phase 2: email fallback tells the truth about the email sequence (a
touch held by the email quality check is NOT "continuing"; Email 1 is never
next once sent; bookings and replies stop it), the queue shows each family's
next step and the families/messages breakdown, carrier guidance can be
recorded per code without ever retrying anything, and only managers act.
Synthetic data; nothing is sent."""
from datetime import datetime, timedelta

import pytest

from app.models.models import BookingLink, Message, Reply
from app.models.program_models import ProgramEmailTouch
from app.services import sms_recovery as R
from app.services import sms_recovery_policy as P
from app.services.programs import email_touches as ET
from tests.test_cadence_engine import _lead
from tests.test_outreach_program import _admin, _h, _lead_for, program  # noqa: F401


def _touch(db, org, lead, n, status, *, at, reason=None):
    db.add(ProgramEmailTouch(organization_id=org.id, lead_id=lead.id, touch_number=n, status=status,
                             attempted_at=at, reason=reason, campaign_family="central_alabama_ms"))
    db.commit()


def _fail(db, lead, adv, sid, code="30006", hours_ago=2, status="undelivered"):
    m = Message(lead_id=lead.id, sender_id=adv.id, body="Hello", twilio_sid=sid, send_source="cadence",
                sent_at=datetime.utcnow() - timedelta(hours=hours_ago), delivery_status=status, error_code=code)
    db.add(m)
    db.commit()
    return m


def _held(db, org, adv, ref, email):
    lead = _lead_for(db, org, adv, ref)
    lead.email = email
    db.commit()
    item = R.record_failure(db, _fail(db, lead, adv, "SM" + ref), source="callback")
    db.commit()
    return lead, item


def test_email_held_by_the_quality_check_is_not_reported_as_carrying_on(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead, item = _held(db, org, sample_advisor, "L001", "fam@gmail.com")
    _touch(db, org, lead, 1, "blocked", at=datetime.utcnow() - timedelta(hours=3),
           reason="held by quality check: no postal address on file for the location")
    fb = R.email_fallback(db, lead)
    assert fb["eligible"] is False and fb["blocked_by_check"] is True
    assert "no postal address" in fb["reason"]
    assert fb["sequence"]["next_touch"] == 1 and fb["sequence"]["status"] == "blocked"
    with pytest.raises(R.ActionRefused, match="no postal address"):
        R.route_email(db, item, "u1")


def test_after_email_1_the_next_touch_is_2_on_the_followup_rule(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead, _item = _held(db, org, sample_advisor, "L001", "fam@gmail.com")
    sent = datetime.utcnow() - timedelta(hours=10)
    _touch(db, org, lead, 1, "sent", at=sent)
    fb = R.email_fallback(db, lead)
    assert fb["eligible"] is True
    q = fb["sequence"]
    assert q["sent"] == 1 and q["next_touch"] == 2                     # never Email 1 again
    due = datetime.fromisoformat(q["next_due_at"].rstrip("Z"))
    assert abs((due - (sent + timedelta(days=ET.followup_days()))).total_seconds()) < 2
    for n in range(2, ET.max_touches() + 1):
        _touch(db, org, lead, n, "sent", at=sent + timedelta(days=n))
    fb = R.email_fallback(db, lead)
    assert fb["eligible"] is False and fb["sequence"]["status"] == "finished"


@pytest.mark.parametrize("stop", ["booked", "replied"])
def test_a_booking_or_a_reply_ends_the_email_continuation(program, sample_advisor, stop):
    db, org = program["db"], program["org"]
    lead, _item = _held(db, org, sample_advisor, "L001", "fam@gmail.com")
    if stop == "booked":
        db.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id, token="tok-p2", status="booked"))
    else:
        db.add(Reply(lead_id=lead.id, body="Call me", source="email", received_at=datetime.utcnow()))
    db.commit()
    assert R.email_fallback(db, lead)["eligible"] is False


def test_queue_gives_a_next_step_and_counts_families_and_messages_separately(client, db_session, sample_org,
                                                                          sample_advisor):
    admin = _admin(db_session, sample_org, email="p2-admin@example.com")
    held = _lead(db_session, sample_org, sample_advisor, phone="12145555001")
    R.record_failure(db_session, _fail(db_session, held, sample_advisor, "SMp2h"), source="callback")
    never = _lead(db_session, sample_org, sample_advisor, phone="12145555002")
    for i in range(3):
        _fail(db_session, never, sample_advisor, "SMp2n%d" % i, code="30003", hours_ago=60 - i * 20)
    back = _lead(db_session, sample_org, sample_advisor, phone="12145555003")
    _fail(db_session, back, sample_advisor, "SMp2b", code="30007", hours_ago=30)
    _fail(db_session, back, sample_advisor, "SMp2b2", code=None, hours_ago=5, status="delivered")
    db_session.commit()
    body = client.get("/sms-recovery/queue?kind=all", headers=_h(db_session, admin)).json()
    rows = {r["lead_id"]: r for r in body["rows"]}
    assert "Verify the number" in rows[held.id]["next_step"]
    assert rows[held.id]["hold_reason"].startswith("Carrier could not deliver")
    assert "carrier evidence" in rows[never.id]["next_step"] and rows[never.id]["repeated"] is True
    assert rows[back.id]["delivered_after_failure"] is True and "delivered" in rows[back.id]["next_step"]
    m = body["metrics"]
    assert m["families_by_code"] == {"30006": 1, "30003": 1, "30007": 1}
    assert m["failed_by_code"]["30003"] == 3 and m["messages_failed"] == 5
    assert m["one_time_families"] == 2 and m["repeated_families"] == 1
    assert m["needs_carrier_evidence"] == 1 and m["recovered_families"] == 1
    assert m["awaiting_verification"] == 1 and m["no_channel"] == 1           # held, no email at all


def test_carrier_guidance_is_recorded_per_code_and_never_retries(client, db_session, sample_org, sample_advisor):
    admin = _admin(db_session, sample_org, email="p2-pol@example.com")
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145555004")
    item = R.record_failure(db_session, _fail(db_session, lead, sample_advisor, "SMp2p"), source="callback")
    db_session.commit()
    texts_before = db_session.query(Message).count()
    bad = client.put("/sms-recovery/policy", headers=_h(db_session, admin),
                     json={"rules": {"30003": {"action": "continue_schedule", "retry": {"max_attempts": 99}}},
                           "note": "x"})
    assert bad.status_code == 422
    ok = client.put("/sms-recovery/policy", headers=_h(db_session, admin), json={
        "rules": {"30003": {"action": "continue_schedule", "evidence": "Twilio ticket #29953372 (hypothetical)",
                            "retry": {"max_attempts": 2, "cooldown_hours": 24}},
                  "30006": {"action": "hold_for_review", "retry": {"max_attempts": 1, "cooldown_hours": 48,
                                                                  "requires_verified_capability": True}}},
        "note": "carrier guidance recorded"})
    assert ok.status_code == 200
    rules = P.effective(db_session, sample_org.id)[1]
    assert rules["30003"]["retry"]["enabled"] is False and rules["30006"]["retry"]["max_attempts"] == 1
    assert rules["30005"]["action"] == P.CONTINUE_SCHEDULE and rules["30007"]["action"] == P.INVESTIGATE
    db_session.refresh(item)
    assert item.open_lead_id == lead.id                                   # the hold is untouched
    assert db_session.query(Message).count() == texts_before             # nothing sent


def test_only_managers_may_act_on_a_hold(client, db_session, sample_org, sample_advisor):
    from tests.test_cadence_engine import _lead as mk
    lead = mk(db_session, sample_org, sample_advisor, phone="12145555005")
    item = R.record_failure(db_session, _fail(db_session, lead, sample_advisor, "SMp2a"), source="callback")
    db_session.commit()
    sample_advisor.role = "advisor"
    db_session.commit()
    r = client.post("/sms-recovery/items/%s/action" % item.id, headers=_h(db_session, sample_advisor),
                    json={"action": "mark_capable", "note": "x"})
    assert r.status_code == 403
    assert client.get("/sms-recovery/evidence.csv", headers=_h(db_session, sample_advisor)).status_code == 403
    db_session.refresh(item)
    assert item.review_status == "open"
