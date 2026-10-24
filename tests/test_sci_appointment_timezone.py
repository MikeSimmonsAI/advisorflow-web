"""P0 Oct 21: SCI appointment times shown 5 hours early in Metric Drill-down.

BookingLink.booked_time is stored as the booking calendar's LOCAL wall time
(app/services/booking_time.py). The drill-down stamped it "Z" (UTC), so a
9:00 AM CT visit showed as 4:00 AM and a 2:00 PM visit as 9:00 AM. Every SCI
screen now sends the stored wall time with its own zone's offset; stored
times are never changed."""
from datetime import datetime

import pytest

from app.models.models import BookingLink, User
from app.services.programs import sci_analytics as SA
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import setter  # noqa: F401 (fixture)

BOSTICK = datetime(2026, 10, 22, 9, 0)     # family confirmed "Thursday, October 22 at 9:00 AM" (CDT)
WALTER = datetime(2026, 10, 22, 14, 0)     # family confirmed "Thursday, October 22 at 2:00 PM" (CDT)


def _book(db, lead, advisor, when, n):
    b = BookingLink(lead_id=lead.id, user_id=advisor.id, status="booked", token="tz-%d-%s" % (n, lead.id),
                    booked_time=when, calendar_event_id="evt-%d" % n, appt_duration=60, appt_label="Planning Visit")
    db.add(b)
    db.commit()
    return b


def _families(setter, sample_advisor, zone=None):
    db, org = setter["db"], setter["org"]
    if zone:
        sample_advisor.booking_timezone = zone
        db.commit()
    a = _lead_for(db, org, sample_advisor, "L001", first="William", last="Bostick")
    b = _lead_for(db, org, sample_advisor, "L002", phone="2145550102", first="Walter", last="Johnson",
                  email="walter@example.com")
    for lead in (a, b):
        lead.is_test = False
    db.commit()
    return a, b


def _drill(client, db, admin):
    SA._CACHE.clear() if hasattr(SA, "_CACHE") else None
    r = client.get("/program/command/analytics/records?metric=appointments&range=all", headers=_h(db, admin))
    assert r.status_code == 200, r.text
    return {i["name"]: i for i in r.json()["items"]}


def test_drill_down_shows_the_time_the_family_was_given(client, setter, sample_advisor):
    db, admin = setter["db"], setter["admin"]
    a, b = _families(setter, sample_advisor)
    _book(db, a, sample_advisor, BOSTICK, 1)
    _book(db, b, sample_advisor, WALTER, 2)
    rows = _drill(client, db, admin)
    assert rows["William Bostick"]["appt_at"] == "2026-10-22T09:00:00-05:00"
    assert rows["Walter Johnson"]["appt_at"] == "2026-10-22T14:00:00-05:00"
    assert "9:00 AM CT" in rows["William Bostick"]["text"] and "2:00 PM CT" in rows["Walter Johnson"]["text"]
    for r in rows.values():
        assert not r["appt_at"].endswith("Z")


def test_family_record_shows_the_same_time(client, setter, sample_advisor):
    db, admin = setter["db"], setter["admin"]
    a, b = _families(setter, sample_advisor)
    _book(db, a, sample_advisor, BOSTICK, 1)
    _book(db, b, sample_advisor, WALTER, 2)
    fa = client.get("/program/command/family/%s" % a.id, headers=_h(db, admin)).json()["booking"]
    fb = client.get("/program/command/family/%s" % b.id, headers=_h(db, admin)).json()["booking"]
    assert fa["when"] == "2026-10-22T09:00:00-05:00" and fa["when_text"] == "Thursday, October 22 at 9 AM CDT"
    assert fb["when"] == "2026-10-22T14:00:00-05:00" and fb["when_text"] == "Thursday, October 22 at 2 PM CDT"


def test_appointments_list_and_conversation_carry_offsets(client, setter, sample_advisor):
    from app.services.programs import ai_setter as S
    db, admin = setter["db"], setter["admin"]
    a, _ = _families(setter, sample_advisor)
    _book(db, a, sample_advisor, BOSTICK, 1)
    items = client.get("/program/appointments?when=all", headers=_h(db, admin)).json()["items"]
    assert [i["when"] for i in items if i["lead_id"] == a.id] == ["2026-10-22T09:00:00-05:00"]
    conv = S.conversation_for(db, a)
    conv.booked_time = BOSTICK
    db.commit()
    from app.routers.program_router import _conv_json
    assert _conv_json(db, conv)["booked_time"] == "2026-10-22T09:00:00-05:00"


def test_after_the_dst_change_the_offset_follows_the_date(client, setter, sample_advisor):
    """Nov 1 2026: CDT -> CST. 9:00 AM stays 9:00 AM; only the offset changes."""
    db, admin = setter["db"], setter["admin"]
    a, b = _families(setter, sample_advisor)
    _book(db, a, sample_advisor, datetime(2026, 11, 2, 9, 0), 1)          # CST
    _book(db, b, sample_advisor, datetime(2026, 11, 1, 1, 30), 2)         # ambiguous hour: first (CDT) reading
    rows = _drill(client, db, admin)
    assert rows["William Bostick"]["appt_at"] == "2026-11-02T09:00:00-06:00"
    assert rows["Walter Johnson"]["appt_at"].startswith("2026-11-01T01:30:00-0")
    fa = client.get("/program/command/family/%s" % a.id, headers=_h(db, admin)).json()["booking"]
    assert fa["when_text"] == "Monday, November 2 at 9 AM CST"


@pytest.mark.parametrize("zone,expected", [("America/New_York", "-04:00"), ("America/Denver", "-06:00"),
                                           ("America/Phoenix", "-07:00"), ("America/Los_Angeles", "-07:00")])
def test_other_booking_zones_use_their_own_offset(client, setter, sample_advisor, zone, expected):
    db, admin = setter["db"], setter["admin"]
    a, _ = _families(setter, sample_advisor, zone=zone)
    _book(db, a, sample_advisor, BOSTICK, 1)
    assert _drill(client, db, admin)["William Bostick"]["appt_at"] == "2026-10-22T09:00:00" + expected


def test_stored_times_are_never_changed(client, setter, sample_advisor):
    db, admin = setter["db"], setter["admin"]
    a, b = _families(setter, sample_advisor)
    ba, bb = _book(db, a, sample_advisor, BOSTICK, 1), _book(db, b, sample_advisor, WALTER, 2)
    _drill(client, db, admin)
    client.get("/program/command/family/%s" % a.id, headers=_h(db, admin))
    client.get("/program/appointments?when=all", headers=_h(db, admin))
    db.refresh(ba)
    db.refresh(bb)
    assert ba.booked_time.replace(tzinfo=None) == BOSTICK and bb.booked_time.replace(tzinfo=None) == WALTER
