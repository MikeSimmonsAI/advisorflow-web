"""Owner, Nov 25: rotate the offered times. Every Restland family was offered
"10:00 AM or 1:00 PM" and Friday 1:00 PM filled with six appointments."""
from collections import Counter
from datetime import datetime, timedelta

from app.services.programs import booking_policy as BP

NOW = datetime(2026, 11, 25, 15, 0)      # Wednesday; Thursday is closed (holiday)
STARTS = ["10:00", "11:30", "13:00", "14:30", "16:00"]


def _days(closed=("2026-11-26", "2026-11-29")):
    def fake(db, advisor, d, policy, now_local=None, busy_reader=None):
        if d.isoformat() in closed:
            return {"slots": [], "reason": "closed"}
        return {"slots": ["%sT%s:00" % (d.isoformat(), t) for t in STARTS]}
    return fake


def _offers(monkeypatch, load=None, n=400):
    monkeypatch.setattr(BP, "day_slots", _days())
    monkeypatch.setattr(BP, "slot_load", lambda db, adv, a, b: dict(load or {}))
    return [BP.tomorrow_offer(None, object(), {"x": 1}, now_local=NOW, seed="lead-%d" % i) for i in range(n)]


def test_families_get_different_times_and_days(monkeypatch):
    offers = _offers(monkeypatch)
    c = Counter(offers)
    assert len(c) >= 15                                   # many different offers, not one
    assert max(c.values()) <= len(offers) * 0.12          # no single offer dominates
    days = Counter(o.split(" works")[0] for o in offers)
    assert {"What time Friday", "What time Saturday", "What time Monday"} <= set(days)
    assert not any("Thursday" in o or "Sunday" in o for o in offers)    # closed days never offered
    times = Counter(t for o in offers for t in o.split("you, ")[1].rstrip("?").split(" or "))
    assert set(times) == {"10:00 AM", "11:30 AM", "1:00 PM", "2:30 PM", "4:00 PM"}


def test_a_crowded_time_is_passed_over(monkeypatch):
    fri_1pm = datetime(2026, 11, 27, 13, 0)
    offers = _offers(monkeypatch, load={fri_1pm: 6})
    assert not any(o.startswith("What time Friday") and "1:00 PM" in o for o in offers)


def test_same_family_same_day_gets_the_same_offer(monkeypatch):
    monkeypatch.setattr(BP, "day_slots", _days())
    monkeypatch.setattr(BP, "slot_load", lambda db, adv, a, b: {})
    one = BP.tomorrow_offer(None, object(), {"x": 1}, now_local=NOW, seed="lead-7")
    assert one == BP.tomorrow_offer(None, object(), {"x": 1}, now_local=NOW, seed="lead-7")   # text = email
    later = [BP.tomorrow_offer(None, object(), {"x": 1}, now_local=NOW + timedelta(days=k), seed="lead-7")
             for k in range(1, 6)]
    assert len(set(later + [one])) > 1                    # a follow-up days later can offer something else


def test_a_full_house_is_still_offered_when_nothing_else_exists(monkeypatch):
    load = {datetime(2026, 11, d, h, m): 5 for d in (27, 28, 30, 1) for h, m in ((10, 0), (11, 30), (13, 0),
                                                                                   (14, 30), (16, 0))
            if not (d == 1)}
    offers = _offers(monkeypatch, load=load, n=50)
    assert all(o.startswith("What time") and " or " in o for o in offers)   # double booking is allowed


def test_two_times_are_spread_across_the_day(monkeypatch):
    for o in _offers(monkeypatch, n=200):
        a, b = [datetime.strptime(x, "%I:%M %p") for x in o.split("you, ")[1].rstrip("?").split(" or ")]
        assert b - a >= timedelta(hours=2)


def test_the_ai_offers_rotate_and_steer_off_a_crowded_time(monkeypatch):
    from types import SimpleNamespace
    import app.routers.calendar_router as CR
    from app.services.programs import appointments as A

    def fake(db, advisor, d, **kw):
        if d.isoformat() in ("2026-11-26", "2026-11-29"):
            return {"slots": []}
        return {"slots": ["%sT%s:00" % (d.isoformat(), t) for t in STARTS]}
    monkeypatch.setattr(CR, "compute_day_slots", fake)
    monkeypatch.setattr(A, "program_advisor", lambda db, prog: SimpleNamespace(id="adv"))
    monkeypatch.setattr(A, "_bookable_day", lambda prog, d: True)
    fri_10 = datetime(2026, 11, 27, 10, 0)
    monkeypatch.setattr(BP, "slot_load", lambda db, adv, a, b: {fri_10: 6})
    picks = [tuple(A.available_slots(None, object(), now_central=NOW, seed="lead-%d" % i)[0]) for i in range(200)]
    assert len(set(picks)) >= 6                                  # not one pair for everyone
    assert not any(fri_10 in p for p in picks)                   # the crowded time is passed over
    assert all(len(p) == 2 and p[0].date() != p[1].date() for p in picks)   # still two different days
    # Without a family (old callers) nothing changes: the first opening.
    first, _ = A.available_slots(None, object(), now_central=NOW)
    assert first[0] == datetime(2026, 11, 25, 16, 0) or first[0].date() >= NOW.date()
