"""SCI appointment lengths agree everywhere: visits 60 minutes, phone 30 -
the times offered, the booking record and the calendar event."""
from datetime import date, datetime, timedelta
from unittest.mock import patch

from app.models.models import BookingLink
from app.routers import calendar_router as CR
from app.services.programs import appointments as A
from tests.test_outreach_program import _lead_for, program  # noqa: F401 (fixture)

DAY = date.today() + timedelta(days=30)
while DAY.weekday() >= 5:
    DAY += timedelta(days=1)


def _slots(db, advisor, minutes):
    with patch.object(CR, "_booking_window_busy", return_value=([], None)):
        return CR.compute_day_slots(db, advisor, DAY, program_visit=True, minutes=minutes)["slots"]


def _book(db, lead, advisor, h, m, dur):
    db.add(BookingLink(lead_id=lead.id, user_id=advisor.id, token="t%d%d%d" % (h, m, dur),
                       status="booked", booked_time=datetime(DAY.year, DAY.month, DAY.day, h, m),
                       appt_duration=dur))
    db.commit()


def test_minutes_by_kind():
    assert A.minutes_for(A.VISIT) == 60 and A.minutes_for(A.PHONE) == 30 and A.minutes_for(None) == 60


def test_visits_on_the_hour_phone_on_the_half_hour(program, sample_advisor):
    db = program["db"]
    visit = _slots(db, sample_advisor, 60)
    phone = _slots(db, sample_advisor, 30)
    assert all(s.endswith(":00:00") for s in visit) and "%sT16:00:00" % DAY.isoformat() in visit
    assert "%sT10:30:00" % DAY.isoformat() in phone and "%sT16:30:00" % DAY.isoformat() in phone


def test_a_booking_holds_exactly_its_own_length(program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    _book(db, lead, sample_advisor, 10, 0, 60)          # 10:00-11:00 visit
    _book(db, lead, sample_advisor, 13, 0, 30)          # 13:00-13:30 phone
    d = DAY.isoformat()
    phone = _slots(db, sample_advisor, 30)
    assert "%sT10:30:00" % d not in phone and "%sT11:00:00" % d in phone
    assert "%sT13:00:00" % d not in phone and "%sT13:30:00" % d in phone
    visit = _slots(db, sample_advisor, 60)
    assert "%sT13:00:00" % d not in visit and "%sT14:00:00" % d in visit and "%sT11:00:00" % d in visit


def test_event_length_follows_the_booking():
    class B:
        appt_duration = 60
    assert CR._event_minutes(B(), {}) == 60
    B.appt_duration = None
    assert CR._event_minutes(B(), {"duration_minutes": 30}) == 30
    assert CR._event_minutes(None, {}) == 30


def test_book_records_the_length_and_sends_it_to_the_calendar(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.booking_user_id = sample_advisor.id
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    slot = datetime(DAY.year, DAY.month, DAY.day, 9, 0)
    monkeypatch.setattr(A, "still_free", lambda *a, **k: True)
    seen = {}

    async def fake_webhook(req, db_):
        p = await req.json()
        seen.update(p)
        link = db_.query(BookingLink).filter_by(token=p["booking_token"]).one()
        seen["dur"] = link.appt_duration
        link.status = "booked"
        db_.commit()
    monkeypatch.setattr(CR, "booking_confirmed_webhook", fake_webhook)
    res = A.book(db, prog, lead, slot, A.PHONE)
    assert res["ok"] and seen["dur"] == 30 and seen["duration_minutes"] == 30
