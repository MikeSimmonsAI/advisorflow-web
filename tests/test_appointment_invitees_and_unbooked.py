"""Nov 16: every program appointment invites the program's guests, and a family
told a time by a person with nothing booked pages the team."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.models import BookingLink, Message
from app.models.program_models import ProgramAlert
from app.services import calendar_service as CS
from app.services import send_source as ss
from app.services.programs import unbooked_confirmations as U
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)

GUESTS = ["info.evosyspro@example.com", "advisor@example.com", "partner@example.com"]


class Cal:
    def __init__(self):
        self.calls = []

    def events(self):
        return self

    def insert(self, calendarId, body, **kw):
        self.calls.append(("insert", body, kw))
        return SimpleNamespace(execute=lambda: {"id": "ev-1", "htmlLink": "x"})

    def delete(self, calendarId, eventId, **kw):
        self.calls.append(("delete", eventId, kw))
        return SimpleNamespace(execute=lambda: None)


def _booking(db, prog, lead, advisor):
    advisor.google_calendar_connected = True
    advisor.google_calendar_id = "cal"
    b = BookingLink(lead_id=lead.id, user_id=advisor.id, status="pending")
    db.add(b)
    db.commit()
    return b


def test_settings_save_and_show_invitees(client, program):
    db, admin = program["db"], program["admin"]
    r = client.patch("/program/settings", headers=_h(db, admin),
                     json={"appointment_invitees": GUESTS + [GUESTS[0].upper(), " "]})
    assert r.status_code == 200, r.text
    assert client.get("/program/me", headers=_h(db, admin)).json()["appointment_invitees"] == GUESTS
    bad = client.patch("/program/settings", headers=_h(db, admin), json={"appointment_invitees": ["nope"]})
    assert bad.status_code == 422


def test_event_invites_guests_and_cancel_tells_them(program, monkeypatch):
    db, org, admin, prog = program["db"], program["org"], program["admin"], program["prog"]
    prog.appointment_invitees = json.dumps(GUESTS)
    lead = _lead_for(db, org, admin, "L001")
    cal = Cal()
    monkeypatch.setattr(CS, "_get_calendar_service", lambda u: cal)
    b = _booking(db, prog, lead, admin)
    res = CS.create_calendar_event_for_booking(db, b, datetime(2030, 1, 10, 14, 30), 45)
    assert res["success"], res
    kind, body, kw = cal.calls[0]
    assert [a["email"] for a in body["attendees"]] == GUESTS and kw == {"sendUpdates": "all"}
    CS.cancel_calendar_event(db, b)
    assert cal.calls[1][0] == "delete" and cal.calls[1][2] == {"sendUpdates": "all"}


def test_no_guests_no_invites(program, monkeypatch):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L001")
    cal = Cal()
    monkeypatch.setattr(CS, "_get_calendar_service", lambda u: cal)
    b = _booking(db, program["prog"], lead, admin)
    CS.create_calendar_event_for_booking(db, b, datetime(2030, 1, 10, 14, 30), 45)
    assert "attendees" not in cal.calls[0][1] and cal.calls[0][2] == {}


@pytest.mark.parametrize("text,yes", [
    ("Adel we will see you Thursday at Nov 19 at 2:30pm. - Mike Simmons, Restland Reply STOP to opt out.", True),
    ("You're all set for tomorrow at 10am!", True),
    ("I have you down for Wed 11/18 at 3:00 PM.", True),
    ("Would Thu, Nov 19 at 2:30 PM work for a visit?", False),          # an offer, not a confirmation
    ("Great, see you soon!", False),                                    # no day or time
    ("What's your availability on Wednesday? I have 11:00 am open", False),
])
def test_what_counts_as_telling_a_time(text, yes):
    assert U.confirms_a_time(text) is yes


def _manual(db, lead, advisor, body, minutes_ago=20):
    m = Message(lead_id=lead.id, sender_id=advisor.id, body=body, send_source=ss.MANUAL,
                sent_at=datetime.utcnow() - timedelta(minutes=minutes_ago))
    db.add(m)
    db.commit()
    return m


def test_told_a_time_with_nothing_booked_pages_the_team_once(program):
    db, org, admin, prog = program["db"], program["org"], program["admin"], program["prog"]
    prog.alert_phone, prog.alert_email, prog.staff_sms_alerts_enabled = "+12145550199", "owner@example.com", True
    lead = _lead_for(db, org, admin, "L001")
    _manual(db, lead, admin, "We will see you Thursday at 2:30pm.")
    found = U.find(db, org.id)
    assert [f["lead_id"] for f in found] == [lead.id]
    assert U.alert_all(db) == 1
    rows = db.query(ProgramAlert).filter(ProgramAlert.kind == "unbooked").all()
    assert rows and all(r.lead_id == lead.id for r in rows)
    assert any("NOT BOOKED" in (r.message or "") or "nothing booked" in (r.message or "") for r in rows)
    assert U.alert_all(db) == 0                                         # once per message


def test_booked_offer_recent_or_test_family_is_quiet(program):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, admin, "L001")
    _manual(db, lead, admin, "See you Friday at 10am", minutes_ago=1)   # still inside the grace
    assert U.find(db, org.id) == []
    _manual(db, lead, admin, "See you Friday at 10am", minutes_ago=30)
    db.add(BookingLink(lead_id=lead.id, user_id=admin.id, status="booked",
                       booked_time=datetime.utcnow() + timedelta(days=2)))
    db.commit()
    assert U.find(db, org.id) == []
    other = _lead_for(db, org, admin, "L003", phone="2145550103", first="Pat", last="Sampleton",
                      email="shared@example.com")
    other.is_test = True
    _manual(db, other, admin, "See you Friday at 10am")
    assert U.find(db, org.id) == []
