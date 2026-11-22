"""Owner, Nov 16: Restland sends texts and campaign emails 8:30 AM - 7:00 PM
Central every day. Its own hours replace the 8:30 AM - 7:45 PM Eastern clock
for its families; the family's own 8 AM - 9 PM local clock still holds; SCI
and every other program keep the Eastern clock."""
from datetime import datetime

from zoneinfo import ZoneInfo

from app.models.models import Lead
from app.services import contact_hours as CH
from app.services.programs import email_touches as ET
from app.services.programs import outreach_slots as OS
from app.services.programs import restland_launch as RL
from tests.test_outreach_program import _h
from tests.test_restland_booking_cadence import configured, prog_of, rl  # noqa: F401

CT = ZoneInfo("America/Chicago")


def utc(h, mi, d=17):
    return datetime(2026, 11, d, h, mi, tzinfo=CT).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _launched(rl):
    configured(rl)
    RL.launch(rl["db"], rl["org"], "launch-1", booking_user_id=rl["mike"].id, calendar_user_id=None,
              start_local="2026-11-17T08:30", now=utc(17, 0, d=16))
    OS.clear_cache()


def _dallas(rl):
    return rl["db"].query(Lead).filter(Lead.organization_id == rl["org"].id,
                                       Lead.phone.isnot(None)).first()


def test_restland_texts_run_until_7pm_central_every_day(rl):
    _launched(rl)
    lead = _dallas(rl)
    assert CH.is_permitted(lead, utc(18, 50))           # 6:50 PM Central: past 7:45 PM Eastern, now allowed
    assert CH.is_permitted(lead, utc(18, 50, d=21))     # Saturday too
    assert not CH.is_permitted(lead, utc(12, 0, d=22))  # Sunday: nothing goes out (owner, Nov 22)
    assert not CH.is_permitted(lead, utc(7, 45))        # the family's own 8 AM floor still holds
    cfg = OS.config_for_org(rl["db"], rl["org"].id)
    assert (cfg["open"], cfg["close"]) == ("08:30", "19:00")
    assert OS.permits(utc(8, 30), cfg) and OS.permits(utc(18, 59), cfg)
    assert not OS.permits(utc(8, 29), cfg) and not OS.permits(utc(19, 0), cfg)


def test_restland_emails_follow_the_same_hours(rl):
    _launched(rl)
    db, org = rl["db"], rl["org"].id
    assert ET.in_sending_hours(db, org, utc(18, 50))
    assert not ET.in_sending_hours(db, org, utc(19, 5))
    assert ET.in_sending_hours(db, org, utc(8, 31))


def test_a_program_without_its_own_hours_keeps_the_eastern_clock(rl):
    lead = _dallas(rl)                                   # not launched: no schedule of its own
    OS.clear_cache()
    assert not CH.is_permitted(lead, utc(18, 50))        # 7:50 PM Eastern


def test_managers_set_the_hours_and_bad_hours_are_refused(client, rl):
    _launched(rl)
    db, mike = rl["db"], rl["mike"]
    mike.must_change_password = False
    db.commit()
    r = client.post("/program/command/outreach-hours", headers=_h(db, mike), json={"open": "09:00", "close": "18:30"})
    assert r.status_code == 200, r.text
    assert r.json()["open"] == "09:00" and r.json()["close"] == "18:30"
    assert client.get("/program/command/outreach-hours", headers=_h(db, mike)).json()["close"] == "18:30"
    for bad in ({"open": "07:00", "close": "19:00"}, {"open": "19:00", "close": "08:30"},
                {"open": "8:30", "close": "19:00"}, {"open": "08:30", "close": "22:00"}):
        assert client.post("/program/command/outreach-hours", headers=_h(db, mike), json=bad).status_code == 422
    assert client.post("/program/command/outreach-hours", headers=_h(db, mike),
                       json={"open": "08:30", "close": "19:00"}).json()["close"] == "19:00"
