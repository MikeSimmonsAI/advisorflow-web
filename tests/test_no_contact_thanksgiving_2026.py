"""Owner, Nov 23 4:29 PM: Thanksgiving (Thu Nov 26, 2026) - every system off,
no communication of any kind."""
from datetime import date, datetime

from app.services import no_contact_days as NCD
from app.services.programs import outreach_slots as OS

THU_NOON_UTC = datetime(2026, 11, 26, 18, 0)        # noon Central
WED_NOON_UTC = datetime(2026, 11, 25, 18, 0)


def test_thanksgiving_is_a_no_contact_day_in_central_time():
    assert NCD.quiet_on(date(2026, 11, 26)) and not NCD.quiet_on(date(2026, 11, 27))
    assert NCD.quiet_now(THU_NOON_UTC) and not NCD.quiet_now(WED_NOON_UTC)
    assert NCD.quiet_now(datetime(2026, 11, 26, 6, 30))          # 12:30 AM Thursday Central
    assert not NCD.quiet_now(datetime(2026, 11, 26, 5, 30))      # 11:30 PM Wednesday Central
    assert not NCD.quiet_now(datetime(2026, 11, 27, 6, 30))      # 12:30 AM Friday Central


def test_reminders_stay_held_until_830_friday_morning():
    assert NCD.holding_reminders(datetime(2026, 11, 27, 13, 0))      # 7:00 AM Friday
    assert not NCD.holding_reminders(datetime(2026, 11, 27, 14, 45))  # 8:45 AM Friday


def test_every_workspace_is_closed_and_no_program_text_slot_opens():
    assert OS.closed_for_org(None, "any-org-id", THU_NOON_UTC)        # SCI, Restland, every workspace
    cfg = dict(OS.DEFAULTS)
    assert OS.closed_on(date(2026, 11, 26), cfg) and OS.day_slots(date(2026, 11, 26), cfg, "text") == []
    assert OS.closed_on(date(2026, 11, 26), None)


def test_more_days_can_be_added_without_a_deploy(monkeypatch):
    monkeypatch.setenv("NO_CONTACT_DATES", "2026-12-25, 2027-01-01")
    assert NCD.quiet_on(date(2026, 12, 25)) and NCD.quiet_on(date(2027, 1, 1))


def test_no_appointment_times_on_thanksgiving():
    from types import SimpleNamespace
    from app.services.programs import booking_policy as BP
    pol = BP.parse({"starts": ["10:00", "13:00"], "duration": 45, "gap_after": 45, "days": "0123456"})
    res = BP.day_slots(None, SimpleNamespace(id="a"), date(2026, 11, 26), pol, today=date(2026, 11, 23),
                       busy_reader=lambda *a, **k: ([], None))
    assert res["slots"] == [] and "holiday" in res["reason"]
