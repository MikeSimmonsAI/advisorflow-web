# -*- coding: utf-8 -*-
"""The Command Center's "at risk" signal.

The board calls a deal at risk for exactly two reasons, and both are read off
the record rather than predicted: a closing date that has already passed, and
an event log that has carried nothing about the deal for a week. These tests
pin that down, because the temptation with a risk column is always to start
scoring things nobody can audit.
"""
from datetime import date, datetime, timedelta

from app.services.wholesale_service import QUIET_DAYS, _deal_risk

TODAY = date(2026, 9, 23)


class FakeDeal(object):
    def __init__(self, closing_date=None, created_at=None):
        self.closing_date = closing_date
        self.created_at = created_at


def test_a_deal_with_recent_activity_is_not_at_risk():
    deal = FakeDeal(created_at=datetime(2026, 1, 1))
    recent = datetime(2026, 9, 22, 9, 0)
    assert _deal_risk(deal, recent, TODAY) is None


def test_silence_past_the_threshold_reads_as_stalled():
    deal = FakeDeal(created_at=datetime(2026, 1, 1))
    quiet_since = datetime(2026, 9, 23) - timedelta(days=QUIET_DAYS)
    risk = _deal_risk(deal, quiet_since, TODAY)
    assert risk is not None
    assert risk["level"] == "stalled"
    assert risk["days"] == QUIET_DAYS
    assert str(QUIET_DAYS) in risk["label"]


def test_one_day_short_of_the_threshold_is_still_quiet_not_stalled():
    deal = FakeDeal(created_at=datetime(2026, 1, 1))
    quiet_since = datetime(2026, 9, 23) - timedelta(days=QUIET_DAYS - 1)
    assert _deal_risk(deal, quiet_since, TODAY) is None


def test_a_passed_closing_date_outranks_silence():
    # Active today, but the closing date went by three days ago. The overdue
    # closing is the thing a person needs to see, not "it has been busy".
    deal = FakeDeal(closing_date=date(2026, 9, 20),
                    created_at=datetime(2026, 1, 1))
    risk = _deal_risk(deal, datetime(2026, 9, 23, 8, 0), TODAY)
    assert risk["level"] == "overdue"
    assert risk["days"] == 3
    assert "3 days ago" in risk["label"]


def test_a_closing_date_one_day_past_is_written_in_the_singular():
    deal = FakeDeal(closing_date=date(2026, 9, 22))
    risk = _deal_risk(deal, None, TODAY)
    assert risk["label"].endswith("1 day ago")


def test_a_future_closing_date_is_not_a_risk_by_itself():
    deal = FakeDeal(closing_date=date(2026, 10, 15),
                    created_at=datetime(2026, 9, 22))
    assert _deal_risk(deal, datetime(2026, 9, 22), TODAY) is None


def test_a_deal_with_no_history_at_all_falls_back_to_when_it_was_created():
    # Nothing has ever been logged against it, so the age of the deal itself
    # is the only honest measure of how long it has been sitting.
    deal = FakeDeal(created_at=datetime(2026, 8, 1))
    risk = _deal_risk(deal, None, TODAY)
    assert risk["level"] == "stalled"
    assert risk["days"] == 53


def test_a_deal_with_no_dates_at_all_is_not_invented_into_a_risk():
    assert _deal_risk(FakeDeal(), None, TODAY) is None
