"""Independent-review follow-ups to the booking-integrity fixes (Oct 20): a
past visit is not a booking to move, the booking page re-checks at the length
it offered, and a lost race reads as 'taken'."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

from app.models.models import BookingLink
from app.services.programs import appointments as A
from tests.test_outreach_program import _lead_for, program  # noqa: F401
from tests.test_sci_appointment_setter import _Fake, _d, _ai_turns
from tests.test_sci_booking_integrity import real, _family, _offer, _reply, _booked, _next, _today  # noqa: F401


# A. a past visit (status stays "booked" forever) -> already_booked -> family told
#    "You're currently set for <last week>"
def test_past_visit_already_booked_text(real, sample_advisor):
    db, prog = real["db"], real["prog"]
    lead = _family(db, real["org"], sample_advisor)
    d = _today() - timedelta(days=7)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    past = datetime(d.year, d.month, d.day, 10, 0)
    db.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked", token="past-tok-1",
                       booked_time=past, appt_duration=60))
    db.commit()
    d2 = _next(3, 2)
    s2 = datetime(d2.year, d2.month, d2.day, 14, 0)
    d1 = _next(1, 2)
    s1 = datetime(d1.year, d1.month, d1.day, 10, 0)
    conv = _offer(db, lead, sorted([s1, s2]))
    conv = _reply(db, lead, "%s at 2 works" % s2.strftime("%A"), _Fake([_d("book", "", slot_choice=2)]))
    last = _ai_turns(db, conv)[-1].text or ""
    print("STATE", conv.state, "TEXT", last)
    assert past.strftime("%B") + " %d" % past.day not in last, last


# B. page-created link has appt_duration 30 (from _detect_appt_type) -> webhook re-check
#    uses 30 minutes, while /slots offered 60-minute visits: a 10:00 visit is accepted
#    while another family holds a 10:30 phone call.
def test_page_recheck_uses_wrong_length(real, sample_advisor):
    db, prog = real["db"], real["prog"]
    a = _family(db, real["org"], sample_advisor, "L001", "Alice")
    b = _family(db, real["org"], sample_advisor, "L002", "Bob")
    day = _next(2, 2)
    phone = datetime(day.year, day.month, day.day, 10, 30)
    assert A.book(db, prog, a, phone, A.PHONE)["status"] == "confirmed"
    from app.routers.calendar_router import booking_confirmed_webhook, compute_day_slots
    page = compute_day_slots(db, sample_advisor, day, program_visit=True)
    assert "%sT10:00:00" % day.isoformat() not in page["slots"]          # page would NOT offer 10:00
    from app.services.sms_service import get_or_create_booking_link
    link_b = get_or_create_booking_link(db, b, sample_advisor)
    print("page link appt_duration", link_b.appt_duration)
    db.commit()
    slot = datetime(day.year, day.month, day.day, 10, 0)
    try:
        asyncio.run(booking_confirmed_webhook(A._InternalRequest(
            {"booking_token": link_b.token, "slot_display": slot.strftime("%Y-%m-%dT%H:%M:00")}), db))
    except Exception as e:
        print("refused", e)
        db.rollback()
    db.refresh(link_b)
    assert link_b.status not in ("booked", "confirmed"), "overlapping 10:00 visit accepted over 10:30 call"


# C. a race lost at the webhook (409) reaches the setter as status "error" -> handoff
#    "booking failed", not "that time just filled up".
def test_webhook_409_maps_to_error(real, sample_advisor, monkeypatch):
    db, prog = real["db"], real["prog"]
    a = _family(db, real["org"], sample_advisor, "L001", "Alice")
    b = _family(db, real["org"], sample_advisor, "L002", "Bob")
    day = _next(2, 2)
    slot = datetime(day.year, day.month, day.day, 10, 0)
    assert A.book(db, prog, a, slot, A.VISIT)["status"] == "confirmed"
    monkeypatch.setattr(A, "still_free", lambda *a_, **k: True)   # pre-check passed just before the other family won
    res = A.book(db, prog, b, slot, A.VISIT)
    print("RES", res)
    assert res["status"] == "taken", res
