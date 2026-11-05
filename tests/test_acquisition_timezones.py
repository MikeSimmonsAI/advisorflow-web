# -*- coding: utf-8 -*-
"""#452 appointments in each company's own IANA time zone, DST-safe. No network."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.acquisition import acq_time as T
from app.services.acquisition import engine as E

ZONES = {"America/Chicago": "Central", "America/New_York": "Eastern", "America/Denver": "Mountain",
         "America/Los_Angeles": "Pacific"}


def _p(tz):
    return SimpleNamespace(timezone=tz, min_notice_hours=None, booking_windows=None)


class _Booker:
    id = "booker-x"


def _no_busy(db, booker, d):
    return [], None


@pytest.mark.parametrize("tz", list(ZONES))
def test_each_zone_offers_its_own_9_to_5(tz, db_session):
    now = datetime(2026, 11, 9, 12, 0)                     # Mon 12:00 UTC
    day = date(2026, 11, 10)                                # Tuesday
    out = T.slots_for(db_session, _Booker(), _p(tz), day, now=now, busy_fn=_no_busy)
    assert out["timezone"] == tz
    assert out["slots"][0] == "2026-11-10T09:00:00" and out["slots"][-1] == "2026-11-10T16:00:00"


def test_unknown_zone_falls_back_to_central():
    assert T.company_tz(_p("Mars/Olympus")) == "America/Chicago"
    assert T.company_tz(_p(None)) == "America/Chicago"


def test_busy_time_blocks_the_right_local_hour_in_every_zone(db_session):
    # A calendar block 11:00-12:00 CHICAGO wall time (busy is reported in Chicago time).
    def busy(db, booker, d):
        return [(datetime(d.year, d.month, d.day, 11), datetime(d.year, d.month, d.day, 12))], None
    now = datetime(2026, 11, 9, 12, 0)
    day = date(2026, 11, 10)
    blocked = {"America/Chicago": "11:00", "America/New_York": "12:00", "America/Denver": "10:00",
               "America/Los_Angeles": "09:00"}
    for tz, hm in blocked.items():
        out = T.slots_for(db_session, _Booker(), _p(tz), day, now=now, busy_fn=busy)
        assert "2026-11-10T%s:00" % hm not in out["slots"], tz
        assert len(out["slots"]) == 7, tz


def test_dst_round_trips_are_exact():
    # US DST ends Sun Nov 1 2026, starts Sun Mar 8 2026.
    for tz in ZONES:
        for local in (datetime(2026, 10, 30, 14), datetime(2026, 11, 2, 14), datetime(2026, 3, 6, 9),
                      datetime(2026, 3, 9, 9)):
            assert T.from_engine(T.to_engine(local, tz), tz) == local, (tz, local)
    # 2 PM Central is 19:00 UTC in daylight time and 20:00 UTC in standard time.
    assert T.utc_of(datetime(2026, 10, 30, 14), "America/Chicago") == datetime(2026, 10, 30, 19)
    assert T.utc_of(datetime(2026, 11, 2, 14), "America/Chicago") == datetime(2026, 11, 2, 20)
    assert T.label(datetime(2026, 11, 2, 14), "America/Los_Angeles").endswith("PST")
    assert T.label(datetime(2026, 10, 30, 14), "America/Los_Angeles").endswith("PDT")


def test_appointment_time_is_sent_as_the_true_utc_instant():
    # Stored 2 PM Chicago wall time on Thu Nov 5 2026 (CST) = 20:00 UTC.
    assert E._appt_iso(datetime(2026, 11, 5, 14, 0)) == "2026-11-05T20:00:00Z"
    assert E._appt_iso(None) is None


def test_weekend_past_and_far_dates_offer_nothing(db_session):
    now = datetime(2026, 11, 9, 12, 0)
    for d, why in ((date(2026, 11, 14), "Weekend"), (date(2026, 11, 2), "past"), (date(2026, 12, 30), "14 days")):
        out = T.slots_for(db_session, _Booker(), _p("America/New_York"), d, now=now, busy_fn=_no_busy)
        assert out["slots"] == [] and why in out["reason"]


def test_unreadable_calendar_fails_closed(db_session):
    out = T.slots_for(db_session, _Booker(), _p("America/Denver"), date(2026, 11, 10),
                      now=datetime(2026, 11, 9, 12), busy_fn=lambda db, b, d: ([], "unreadable"))
    assert out["slots"] == [] and "temporarily unavailable" in out["reason"]
