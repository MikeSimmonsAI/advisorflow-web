"""EvoSys Pro Mobile program workspace: the Dashboard's numbers and queue, the
Replies list and the launcher count - same rules and scope as SCI Command.
Synthetic data only; nothing reaches a provider."""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.models import BookingLink, Message, Organization
from app.models.program_models import BookingPageEvent, LocationProfile, ProgramSourceRecord
from app.services.programs import responses as R
from app.services.programs import setup
from tests.test_outreach_program import _admin, _h, _lead_for, program  # noqa: F401 (fixture)


def _reply(db, lead, text):
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, lead, text, "sms")


def _local_now():
    from app.routers.sci_command_router import _local_now as ln
    return ln()


def _booking(db, lead, advisor, when):
    b = BookingLink(lead_id=lead.id, user_id=advisor.id, token=uuid.uuid4().hex, status="booked", booked_time=when)
    db.add(b)
    db.commit()
    return b


def test_dashboard_numbers_queue_and_appointments(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    hot = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    other = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Preston", email="p@example.com")
    test = _lead_for(db, org, sample_advisor, "L004", phone="2145550104", first="Synthetic", email="s@example.com")
    test.is_test = True
    db.commit()
    _reply(db, hot, "Can I schedule a visit this week?")
    _reply(db, test, "Can I schedule a visit?")
    db.add(Message(lead_id=other.id, sender_id=sample_advisor.id, body="hello", sent_at=datetime.utcnow()))
    db.add(Message(lead_id=test.id, sender_id=sample_advisor.id, body="hello", sent_at=datetime.utcnow()))
    db.add(Message(lead_id=other.id, sender_id=sample_advisor.id, body="old",
                   sent_at=datetime.utcnow() - timedelta(days=3)))
    now = _local_now()
    later_today = now.replace(hour=23, minute=0, second=0, microsecond=0)
    b_today = _booking(db, other, sample_advisor, later_today if later_today > now else now + timedelta(minutes=5))
    # booked today, for next week: counts as booked today, not as taking place today
    b_next = _booking(db, hot, sample_advisor, now + timedelta(days=7))
    db.add(BookingPageEvent(organization_id=org.id, booking_link_id=b_next.id, lead_id=hot.id, event="booked"))
    # booked last week (its event is old): not booked today
    db.add(BookingPageEvent(organization_id=org.id, booking_link_id=b_today.id, lead_id=other.id, event="booked",
                            created_at=datetime.utcnow() - timedelta(days=6)))
    db.commit()
    h = _h(db, program["admin"])
    s = client.get("/program/command/mobile/summary", headers=h).json()
    assert s["stats"] == {"sent_today": {"total": 1, "texts": 1, "emails": 0}, "replies_today": 1, "booked_today": 1}
    assert s["needs_reply"]["count"] == 1 and [i["name"] for i in s["needs_reply"]["items"]] == ["Lester Testcase"]
    assert s["taking_place_today"] == 1
    assert [a["lead_id"] for a in s["upcoming"]] == [other.id, hot.id]
    assert s["time_zone"] == "America/Chicago"
    # The same queue rule as SCI Command's "Needs reply" filter.
    desk = client.get("/program/command?filter=needs_reply", headers=h).json()
    assert desk["filter_counts"]["needs_reply"] == s["needs_reply"]["count"]
    assert client.get("/program/command/mobile/count", headers=h).json()["needs_reply"] == 1
    r = client.get("/program/command/mobile/replies", headers=h).json()
    assert r["needs_reply"] == 1 and r["items"][0]["name"] == "Lester Testcase" and r["items"][0]["needs_reply"]
    assert "Synthetic Testcase" not in [i["name"] for i in r["items"]]


def test_answered_reply_leaves_the_queue_and_the_count(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    _reply(db, lead, "Can I schedule a visit this week?")
    h = _h(db, program["admin"])
    rid = client.get("/program/command/mobile/replies", headers=h).json()["items"][0]["response_id"]
    from app.models.program_models import ProgramResponse
    R.mark(db, db.query(ProgramResponse).get(rid), "responded", program["admin"])
    db.commit()
    assert client.get("/program/command/mobile/count", headers=h).json()["needs_reply"] == 0
    s = client.get("/program/command/mobile/summary", headers=h).json()
    assert s["needs_reply"] == {"count": 0, "items": []}


def test_location_scope_and_another_workspaces_location(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    _reply(db, lead, "Can I schedule a visit this week?")
    h = _h(db, program["admin"])
    rec = db.query(ProgramSourceRecord).filter_by(organization_id=org.id, lead_id=lead.id).one()
    mine = rec.location_id
    others = [p.location_id for p in db.query(LocationProfile).filter_by(organization_id=org.id)
              if p.location_id != mine and not p.is_review_bucket]
    assert client.get("/program/command/mobile/summary?location_id=%s" % mine, headers=h).json()["needs_reply"]["count"] == 1
    assert client.get("/program/command/mobile/summary?location_id=%s" % others[0], headers=h).json()["needs_reply"]["count"] == 0
    assert client.get("/program/command/mobile/count?location_id=not-ours", headers=h).status_code == 404


def test_another_tenant_never_sees_these_conversations(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    _reply(db, lead, "Can I schedule a visit this week?")
    other_org = Organization(name="Other Program", slug="other-program-" + uuid.uuid4().hex[:6])
    db.add(other_org)
    db.commit()
    stranger = _admin(db, other_org, email="other-admin@example.com")
    hs = _h(db, stranger)
    # No program in their own workspace: refused, and nothing of this one leaks.
    r = client.get("/program/command/mobile/summary", headers=hs)
    assert r.status_code == 404 and "Lester" not in r.text
    setup.ensure_program(db, other_org, name="Other Program", primary_contact_name="X", primary_contact_title="Y")
    db.commit()
    for path in ("/program/command/mobile/summary", "/program/command/mobile/replies"):
        body = client.get(path, headers=hs).text
        assert "Lester" not in body
    assert client.get("/program/command/mobile/count", headers=hs).json() == {"organization_id": other_org.id,
                                                                            "needs_reply": 0}
    # A conversation id typed into the thread URL is refused for the other tenant.
    assert client.get("/program/command/family/%s" % lead.id, headers=hs).status_code == 404


def test_signed_out_is_refused(client):
    assert client.get("/program/command/mobile/summary").status_code in (401, 403)
