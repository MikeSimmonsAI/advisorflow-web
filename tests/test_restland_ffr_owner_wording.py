"""Owner, Nov 17: the Restland File Review opening, in his words - and
{tomorrow_offer} asks "What time tomorrow works best for you, X or Y?" with
two times really open; {full_name} stands in for "Mr./Mrs. ___"."""
from datetime import datetime

from app.services.programs import booking_policy as BP
from app.services.programs import identity as I
from app.services.programs import restland_ffr as F

NOW = datetime(2026, 11, 17, 19, 0)          # Tuesday evening, Central


def _days(slots_by_day, error=False):
    def fake(db, advisor, d, policy, now_local=None, busy_reader=None):
        if error:
            return {"calendar_error": "cannot read"}
        return {"slots": ["%sT%s:00" % (d.isoformat(), t) for t in slots_by_day.get(d.isoformat(), [])]}
    return fake


def test_two_times_tomorrow_spread_across_the_day(monkeypatch):
    monkeypatch.setattr(BP, "day_slots", _days({"2026-11-18": ["09:00", "10:30", "12:00", "13:30", "15:00"]}))
    out = BP.tomorrow_offer(None, object(), {"x": 1}, now_local=NOW)
    assert out == "What time tomorrow works best for you, 9:00 AM or 12:00 PM?"


def test_a_full_tomorrow_names_the_next_open_day(monkeypatch):
    monkeypatch.setattr(BP, "day_slots", _days({"2026-11-18": ["16:00"], "2026-11-19": ["10:00", "14:30"]}))
    out = BP.tomorrow_offer(None, object(), {"x": 1}, now_local=NOW)
    assert out == "What time Thursday works best for you, 10:00 AM or 2:30 PM?"


def test_never_a_time_it_could_not_verify(monkeypatch):
    monkeypatch.setattr(BP, "day_slots", _days({}, error=True))
    assert BP.tomorrow_offer(None, object(), {"x": 1}, now_local=NOW) == \
        "What day and time work best for you to come in?"
    assert BP.tomorrow_offer(None, None, {"x": 1}, now_local=NOW) == \
        "What day and time work best for you to come in?"


def test_the_greeting_uses_the_familys_full_name():
    assert I.full_name("andra", "BOGDAN") == "Andra Bogdan"
    assert I.full_name("adel", None) == "Adel"
    assert I.full_name(None, "") == "there"


def test_the_opening_text_is_the_owners_words():
    t = I.render(F.SMS[1], {"full_name": "Andra Bogdan",
                            "tomorrow_offer": "What time tomorrow works best for you, 10:00 AM or 2:30 PM?"})
    assert t.startswith("Hi Andra Bogdan, this is Mike with Restland Cemetery and Funeral Home.")
    assert "we have a few things that must be taken care of with every family here at Restland" in t
    assert "review your file, get you your Personal Planning Guide and have you fill out your Permission " \
           "to Access form" in t
    assert "What time tomorrow works best for you, 10:00 AM or 2:30 PM?" in t
    assert t.endswith("Reply STOP to opt out.")
    assert F.segments(t)["segments"] <= 3
    subject, body = F.EMAIL[1]
    assert "Permission to Access form" in body and "{tomorrow_offer}" in body and "{full_name}" in body
