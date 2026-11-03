# -*- coding: utf-8 -*-
"""#452 Phase 3: client booking setup, EvoSys booking onto the client's calendar,
failed writes, retries without duplicates, reschedule keeping identity.
Calendar provider and slot engine are stubbed; nothing real is written."""
from datetime import datetime

import pytest
from fastapi import HTTPException

from app.models.models import BookingLink, User
from app.services.acquisition import calendar_setup as CAL
from app.services.acquisition import common as C
from app.services.acquisition import engine as E
from tests.test_acquisition_outreach import _prospect, cal, setup  # noqa: F401


def _client_user(db, org):
    from app.services.auth_service import hash_password
    u = User(email="cal-%s@client.example" % org.id[:6], full_name="Client Booker", role="org_admin",
             organization_id=org.id, password_hash=hash_password("x-unused-x"), is_active=True)
    db.add(u)
    db.commit()
    return u


def _interested_real(db, org, god):
    opp = _prospect(db, org, god, None, practice=False)
    E.owner_move(db, opp, god, {"stage": C.INTERESTED, "note": "Agreed to Tuesday 10am by phone"})
    return opp


@pytest.fixture()
def ready(setup, cal):
    db, org, god = setup
    u = _client_user(db, org)
    CAL.update(db, org, god, {"booking_user_id": u.id, "appointment_minutes": 45})
    return db, org, god, u, cal


def test_setup_only_accepts_the_clients_own_logins(setup, cal):
    db, org, god = setup
    with pytest.raises(HTTPException):
        CAL.update(db, org, god, {"booking_user_id": god.id})            # not a user of this client
    with pytest.raises(HTTPException):
        CAL.update(db, org, god, {"timezone": "Mars/Base"})
    with pytest.raises(HTTPException):
        CAL.update(db, org, god, {"booking_windows": {"mon": [["17:00", "09:00"]]}})
    u = _client_user(db, org)
    cfg = CAL.update(db, org, god, {"booking_user_id": u.id, "booking_windows": {"tue": [["09:00", "12:00"]]},
                                    "min_notice_hours": 24, "meeting_location": "At the prospect's office"})
    assert cfg["ready"] is True and cfg["booking_user"]["id"] == u.id
    assert cfg["booking_windows"] == {"tue": [["09:00", "12:00"]]}


def test_client_rules_narrow_real_slots():
    class P:
        min_notice_hours = 24
        booking_windows = '{"tue": [["10:00", "11:00"]]}'
    now = datetime(2026, 11, 9, 15, 0)                                     # Mon 9am Central
    slots = ["2026-11-09T16:00:00", "2026-11-10T10:00:00", "2026-11-10T11:00:00", "2026-11-11T10:00:00"]
    assert CAL.filter_slots(P(), slots, now=now) == ["2026-11-10T10:00:00"]


def test_evosys_books_onto_the_client_calendar(ready):
    db, org, god, u, state = ready
    opp = _interested_real(db, org, god)
    with pytest.raises(HTTPException):
        CAL.book(db, org, opp, god, {"slot": "2026-11-10T10:00:00"})     # must confirm with the prospect
    with pytest.raises(HTTPException):
        CAL.book(db, org, opp, god, {"slot": "2026-11-10T14:00:00", "confirmed_with_prospect": True})
    out = CAL.book(db, org, opp, god, {"slot": "2026-11-10T10:00:00", "confirmed_with_prospect": True})
    db.refresh(opp)
    assert out["calendar_synced"] is True and opp.stage == C.BOOKED and opp.booked_at is not None
    assert state["pushed"][0][0] == u.id                                  # the CLIENT's calendar
    item = E.list_client(db, org.id)[0]
    assert item["appointment"]["calendar_synced"] is True and "email" not in item
    with pytest.raises(HTTPException):
        CAL.book(db, org, opp, god, {"slot": "2026-11-10T11:00:00", "confirmed_with_prospect": True})


def test_failed_calendar_write_books_nothing_and_retry_reuses_the_row(ready, monkeypatch):
    from app.services import tenant_scheduling
    db, org, god, u, state = ready
    opp = _interested_real(db, org, god)
    monkeypatch.setattr(tenant_scheduling, "_push_to_calendar", lambda *a, **k: {"ok": False, "error": "token_expired"})
    with pytest.raises(HTTPException) as e:
        CAL.book(db, org, opp, god, {"slot": "2026-11-10T10:00:00", "confirmed_with_prospect": True})
    assert e.value.status_code == 502 and "token_expired" in e.value.detail
    db.refresh(opp)
    assert opp.stage == C.INTERESTED and opp.booked_at is None and E.list_client(db, org.id) == []
    rows = db.query(BookingLink).filter(BookingLink.lead_id == opp.lead_id).all()
    assert [r.status for r in rows] == ["sync_failed"]

    def ok(db_, advisor, org_, booking, lead, *a):
        booking.calendar_event_id = "evt-retry"
        return {"ok": True}
    monkeypatch.setattr(tenant_scheduling, "_push_to_calendar", ok)
    CAL.book(db, org, opp, god, {"slot": "2026-11-10T10:00:00", "confirmed_with_prospect": True})
    rows = db.query(BookingLink).filter(BookingLink.lead_id == opp.lead_id).all()
    assert len(rows) == 1 and rows[0].status == "booked"                  # same row, no duplicate


def test_reschedule_moves_the_same_appointment(ready, monkeypatch):
    from app.services import calendar_providers as reg
    db, org, god, u, state = ready
    opp = _interested_real(db, org, god)
    CAL.book(db, org, opp, god, {"slot": "2026-11-10T10:00:00", "confirmed_with_prospect": True})
    db.refresh(opp)
    link_id = opp.booking_link_id
    calls = []

    class Prov:
        def update_event(self, eid, payload):
            calls.append(eid)
            return type("R", (), {"ok": True, "error_code": None})()
    monkeypatch.setattr(reg, "get_provider", lambda db_, user, org_: Prov())
    CAL.reschedule(db, org, opp, god, {"slot": "2026-11-10T11:00:00", "note": "Owner asked"})
    link = db.query(BookingLink).get(link_id)
    assert calls == ["evt-1"] and link.booked_time == datetime(2026, 11, 10, 11, 0)
    hist = [h["label"] for h in __import__("app.services.acquisition.outcomes", fromlist=["x"]).customer_history(db, opp)]
    assert "Appointment rescheduled" in hist
    assert len(E.list_client(db, org.id)) == 1                            # still ONE delivered appointment

    class Bad:
        def update_event(self, eid, payload):
            return type("R", (), {"ok": False, "error_code": "calendar_conflict"})()
    monkeypatch.setattr(reg, "get_provider", lambda db_, user, org_: Bad())
    with pytest.raises(HTTPException):
        CAL.reschedule(db, org, opp, god, {"slot": "2026-11-10T10:00:00"})
    db.refresh(link)
    assert link.booked_time == datetime(2026, 11, 10, 11, 0)              # unchanged on failure
