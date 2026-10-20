"""Booking-integrity reproductions for the SCI appointment setter.
Real calendar engine (compute_day_slots) - no slot monkeypatching.
Each test asserts the CORRECT behaviour, so a failure == defect reproduced."""
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.models import BookingLink
from app.models.program_models import ProgramConversation
from app.services.programs import ai_setter, appointments as A
from app.services.programs import responses as R
from tests.test_outreach_program import _lead_for, program  # noqa: F401
from tests.test_sci_appointment_setter import _Fake, _d, _ai_turns



@pytest.fixture
def real(program, sample_advisor, monkeypatch):
    db, prog = program["db"], program["prog"]
    prog.booking_user_id = sample_advisor.id
    prog.ai_setter_mode = "draft"
    db.commit()
    monkeypatch.setenv("AI_BACKGROUND_AUTOMATION_ENABLED", "true")
    from app.services import ai_gateway
    ai_gateway._reset_for_tests()
    return program


def _family(db, org, adv, sid="L001", first="Ollie"):
    lead = _lead_for(db, org, adv, sid, first=first)
    lead.phone = None            # no outbound text anywhere; booking path still real
    db.commit()
    return lead


def _offer(db, lead, slots):
    conv = ai_setter.conversation_for(db, lead)
    conv.state = "offered"
    conv.offered_slots = json.dumps([s.isoformat() for s in slots])
    conv.offered_at = datetime.utcnow()
    db.commit()
    return conv


def _reply(db, lead, text, fake):
    with patch.object(ai_setter, "_get_client", return_value=fake), \
         patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        R.on_inbound(db, lead, text, "sms")
    return ai_setter.conversation_for(db, lead, create=False)


def _booked(db, lead):
    return db.query(BookingLink).filter(BookingLink.lead_id == lead.id,
                                        BookingLink.status.in_(("booked", "confirmed"))).all()


def _today():
    return A._today_central().date()


def _next(weekday, min_ahead=1):
    d = _today() + timedelta(days=min_ahead)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


# 1. A stale offered time that is now in the past still books.
def test_past_offered_time_is_not_booked(real, sample_advisor):
    db = real["db"]
    lead = _family(db, real["org"], sample_advisor)
    d = _today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    past = datetime(d.year, d.month, d.day, 10, 0)
    future = datetime.combine(_next(2, 2), datetime.min.time()).replace(hour=14)
    _offer(db, lead, [past, future])
    conv = _reply(db, lead, "%s at 10 works" % past.strftime("%A"), _Fake([_d("book", "", slot_choice=1)]))
    assert not _booked(db, lead), "booked an appointment in the past: %s" % past
    assert conv.state != "booked"


# 2. A weekend time named by the family/AI is booked (engine has no weekday rule).
def test_weekend_requested_time_is_not_booked(real, sample_advisor):
    db = real["db"]
    lead = _family(db, real["org"], sample_advisor)
    sat = _next(5)
    want = datetime(sat.year, sat.month, sat.day, 10, 0)
    _reply(db, lead, "Can you do Saturday at 10?",
           _Fake([_d("book", "", requested_time=want.isoformat()), _d("offer_times", "Would {TIMES} work?")]))
    assert not _booked(db, lead), "booked on a Saturday"


# 3. More than 14 days out (beyond the booking window) still books.
def test_beyond_booking_window_is_not_booked(real, sample_advisor):
    db = real["db"]
    far = _next(1, 40)
    want = datetime(far.year, far.month, far.day, 10, 0)
    assert A.still_free(db, real["prog"], want, A.VISIT) is False


# 4. The family asked a QUESTION ("could you do Thursday morning?") and the AI's
#    own clock time is booked.
def test_a_question_about_a_day_part_does_not_book_an_ai_chosen_hour(real, sample_advisor):
    db = real["db"]
    lead = _family(db, real["org"], sample_advisor)
    thu = _next(3, 2)
    ai_pick = datetime(thu.year, thu.month, thu.day, 11, 0)
    _reply(db, lead, "Could you do Thursday morning?",
           _Fake([_d("book", "", requested_time=ai_pick.isoformat()), _d("offer_times", "Would {TIMES} work?")]))
    assert not _booked(db, lead), "booked %s - a time the family never named" % ai_pick


# 5. The family REJECTS the first offered day; the AI's slot_choice=1 still books it.
def test_rejected_day_is_not_booked(real, sample_advisor):
    db = real["db"]
    lead = _family(db, real["org"], sample_advisor)
    mon, tue = _next(0, 2), _next(1, 2)
    s1 = datetime(mon.year, mon.month, mon.day, 10, 0)
    s2 = datetime(tue.year, tue.month, tue.day, 14, 0)
    _offer(db, lead, sorted([s1, s2]))
    first = sorted([s1, s2])[0]
    conv = _reply(db, lead, "%s doesn't work for me, sorry" % first.strftime("%A"),
                  _Fake([_d("book", "", slot_choice=1)]))
    assert not _booked(db, lead), "booked %s after the family said that day doesn't work" % first


# 6. Booking page webhook accepts a time another family already holds.
def test_booking_page_cannot_take_a_held_time(real, sample_advisor):
    db, prog = real["db"], real["prog"]
    a = _family(db, real["org"], sample_advisor, "L001", "Alice")
    b = _family(db, real["org"], sample_advisor, "L002", "Bob")
    day = _next(2, 2)
    slot = datetime(day.year, day.month, day.day, 10, 0)
    assert A.book(db, prog, a, slot, A.VISIT)["status"] == "confirmed"
    assert slot.strftime("%Y-%m-%dT%H:%M:00") not in A.compute_slots_for_test(db, prog, day) \
        if hasattr(A, "compute_slots_for_test") else True
    from app.routers.calendar_router import booking_confirmed_webhook
    from app.services.sms_service import get_or_create_booking_link
    link_b = get_or_create_booking_link(db, b, sample_advisor)
    db.commit()
    payload = {"booking_token": link_b.token, "slot_display": slot.strftime("%Y-%m-%dT%H:%M:00"),
               "lead_name": "Bob", "lead_phone": ""}
    try:
        asyncio.run(booking_confirmed_webhook(A._InternalRequest(payload), db))
    except Exception:                        # a 409 refusal is the correct outcome
        db.rollback()
    db.refresh(link_b)
    assert link_b.status not in ("booked", "confirmed"), \
        "family B confirmed for %s, already held by family A (B status=%s booked_time=%s)" % (
            slot, link_b.status, link_b.booked_time)


# 7. A booking-page booking with no external calendar never stores booked_time,
#    so the slot is never held and can be offered/booked again.
def test_booking_page_booking_holds_its_slot(real, sample_advisor):
    db, prog = real["db"], real["prog"]
    b = _family(db, real["org"], sample_advisor, "L002", "Bob")
    day = _next(2, 2)
    slot = datetime(day.year, day.month, day.day, 10, 0)
    from app.routers.calendar_router import booking_confirmed_webhook
    from app.services.sms_service import get_or_create_booking_link
    link_b = get_or_create_booking_link(db, b, sample_advisor)
    db.commit()
    asyncio.run(booking_confirmed_webhook(A._InternalRequest(
        {"booking_token": link_b.token, "slot_display": slot.strftime("%Y-%m-%dT%H:%M:00")}), db))
    db.refresh(link_b)
    assert link_b.status == "booked"
    assert link_b.booked_time == slot, "booked_time not stored (=%s)" % link_b.booked_time
    assert A.still_free(db, prog, slot, A.VISIT) is False, "the booked time is still offered as free"


# 8. A booked family asking to move their visit is told the time "just filled up".
def test_reschedule_request_is_not_answered_with_a_false_taken_message(real, sample_advisor):
    db, prog = real["db"], real["prog"]
    lead = _family(db, real["org"], sample_advisor)
    d1, d2 = _next(1, 2), _next(3, 2)
    s1 = datetime(d1.year, d1.month, d1.day, 10, 0)
    s2 = datetime(d2.year, d2.month, d2.day, 14, 0)
    assert A.book(db, prog, lead, s1, A.VISIT)["status"] == "confirmed"
    conv = _offer(db, lead, [s1, s2])
    conv.state = "offered"
    db.commit()
    conv = _reply(db, lead, "Can we move it to %s at 2 instead?" % s2.strftime("%A"),
                  _Fake([_d("book", "", slot_choice=2)]))
    last = _ai_turns(db, conv)[-1].text or ""
    assert "just filled up" not in last, "told the family %r while %s is free and their own visit blocks it" % (
        last, s2)


# 9. Opted-out (dnc) family is still booked by A.book (used by setter and /sci/schedule).
def test_opted_out_family_is_not_booked(real, sample_advisor):
    db, prog = real["db"], real["prog"]
    lead = _family(db, real["org"], sample_advisor)
    lead.status = "dnc"
    db.commit()
    day = _next(2, 2)
    res = A.book(db, prog, lead, datetime(day.year, day.month, day.day, 10, 0), A.VISIT)
    assert not res.get("ok"), "an opted-out family was booked: %s" % res


# 10. Booked times are LOCAL wall time; the program appointments list and the
#     SCI journey label them as UTC.
def test_booked_time_is_shown_as_the_local_time_booked(real, sample_advisor):
    db, prog = real["db"], real["prog"]
    lead = _family(db, real["org"], sample_advisor)
    day = _next(2, 2)
    slot = datetime(day.year, day.month, day.day, 10, 0)
    assert A.book(db, prog, lead, slot, A.VISIT)["status"] == "confirmed"
    link = _booked(db, lead)[0]
    link.calendar_event_id = "evt-1"
    db.commit()
    from app.services.programs import sci_analytics
    j = sci_analytics.journey(db, lead.organization_id, lead.id)
    txt = json.dumps(j, default=str)
    assert "10:00 AM CT" in txt, "journey does not show 10:00 AM CT; shows: %s" % [
        e.get("detail") for e in j.get("events", j.get("timeline", [])) if "booked" in str(e.get("kind"))]


def test_program_appointments_list_marks_local_time_as_utc(real, sample_advisor, client):
    from tests.test_outreach_program import _h
    db, prog = real["db"], real["prog"]
    lead = _family(db, real["org"], sample_advisor)
    day = _next(2, 2)
    slot = datetime(day.year, day.month, day.day, 10, 0)
    assert A.book(db, prog, lead, slot, A.VISIT)["status"] == "confirmed"
    r = client.get("/program/appointments", headers=_h(db, real["admin"])).json()
    when = r["items"][0]["when"]
    assert not when.endswith("Z"), "10:00 Central sent to the browser as %s (UTC) -> shows 5 AM CDT/4 AM CST" % when


# 11. DST: offers around Nov 1 2026 (run under faketime) are on-the-hour 9-4 local weekdays.
def test_slots_around_dst_change(real, sample_advisor):
    db, prog = real["db"], real["prog"]
    slots, why = A.available_slots(db, prog, want=4)
    now_c = A._today_central().replace(tzinfo=None)
    assert slots, why
    for s in slots:
        assert s.weekday() < 5 and 9 <= s.hour <= 16 and s.minute == 0 and s > now_c, s
