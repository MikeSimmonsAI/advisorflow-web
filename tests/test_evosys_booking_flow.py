"""EvoSys Sales public booking page (Business Growth Call): what the page shows
comes from the saved appointment, the Zoom link is that appointment's own
attendee link, the calendar file is the same event as the invite, retries
don't double-book, and one lead's link never reaches another lead's call."""
from datetime import datetime, timedelta

import pytest

from app.models.meeting_models import AppointmentMeeting
from app.models.scheduling_models import SalesAppointment
from app.models.sales_models import OpportunityEvent
from app.services.evosys_sales import booking_page as BP
from app.services.evosys_sales import outreach as O
from app.services.evosys_sales import sms_inbound as IN

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, manager, manager_headers, _make_sales_user, _add_membership, _token_headers, _SEQ,
)
from tests.test_evosys_sales_texting import _lead, _ready  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch, brand):
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", brand.id)
    monkeypatch.setattr(O, "_send_text", lambda *a: pytest.fail("no real texts in tests"))
    monkeypatch.setattr(IN, "notify_owner", lambda *a: None)


def _appt(db, brand, opp, start, email=None, notes=None):
    a = SalesAppointment(brand_sales_org_id=brand.id, opportunity_id=opp.id, title="Business Growth Call",
                         starts_at=start, ends_at=start + timedelta(minutes=30), status="scheduled",
                         prospect_email=email, notes=notes)
    db.add(a); db.commit()
    return a


def _room(db, a, url, status="created", attempts=1, age=timedelta(0)):
    m = AppointmentMeeting(appointment_id=a.id, brand_sales_org_id=a.brand_sales_org_id, provider="zoom",
                           status=status, join_url=url, attempts=attempts,
                           created_at=datetime.utcnow() - age, updated_at=datetime.utcnow() - age)
    db.add(m); db.commit()
    return m


T0 = (datetime.utcnow() + timedelta(days=3)).replace(hour=21, minute=0, second=0, microsecond=0)


def test_confirmation_is_the_saved_appointment_with_its_own_zoom_link(db_session, brand, manager):
    a_opp = _lead(db_session, brand, manager, "Alpha Clean", phone="2145550201")
    b_opp = _lead(db_session, brand, manager, "Beta Clean", phone="2145550202")
    a = _appt(db_session, brand, a_opp, T0)
    b = _appt(db_session, brand, b_opp, T0 + timedelta(hours=1))
    _room(db_session, a, "https://zoom.us/j/111?pwd=a")
    _room(db_session, b, "https://zoom.us/j/222?pwd=b")
    out = BP.booked_out(db_session, BP.current(db_session, a_opp))
    assert out["starts_at"] == T0.isoformat() + "Z" and out["duration_minutes"] == 30
    assert out["title"] == "Business Growth Call"
    assert out["zoom_status"] == "ready" and out["zoom_url"] == "https://zoom.us/j/111?pwd=a"


def test_zoom_pending_then_failed_never_shows_a_fake_link(db_session, brand, manager, monkeypatch):
    from app.services import appointment_meetings as apmeet
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Gamma Clean", phone="2145550203")
    a = _appt(db_session, brand, o, T0)
    m = _room(db_session, a, None, status="pending", attempts=1)
    tried = []
    monkeypatch.setattr(apmeet, "ensure_meeting", lambda db, appt, now=None: tried.append(appt.id) or {"ok": False})
    z = BP.zoom_state(db_session, a)
    assert z == {"zoom_status": "preparing", "zoom_url": None} and not tried      # too soon to retry
    m.updated_at = datetime.utcnow() - timedelta(minutes=5); db_session.commit()
    assert BP.zoom_state(db_session, a)["zoom_status"] == "preparing" and tried == [a.id]
    m.status, m.attempts, m.join_url = "failed", BP.ZOOM_RETRY_MAX, "https://zoom.us/j/stale"
    db_session.commit()
    alerts = []
    monkeypatch.setattr(IN, "notify_owner", lambda s, line, subj, html: alerts.append(line))
    for _ in range(2):
        z = BP.zoom_state(db_session, a)
        assert z == {"zoom_status": "unavailable", "zoom_url": None}
    assert len(alerts) == 1 and "ZOOM LINK FAILED" in alerts[0]
    assert db_session.query(OpportunityEvent).filter_by(opportunity_id=o.id, event_type="zoom_link_failed").count() == 1
    # a room that keeps failing without counting attempts (Zoom not connected) also stops "preparing"
    o2 = _lead(db_session, brand, manager, "Gamma Two", phone="2145550213")
    a2 = _appt(db_session, brand, o2, T0 + timedelta(hours=3))
    _room(db_session, a2, None, status="failed", attempts=0, age=timedelta(minutes=11))
    assert BP.zoom_state(db_session, a2)["zoom_status"] == "unavailable" and len(alerts) == 2


def test_calendar_file_is_the_same_event_as_the_invite(db_session, brand, manager):
    from app.services.ics_builder import ics_uid
    o = _lead(db_session, brand, manager, "Clean; Co, LLC", phone="2145550204")
    a = _appt(db_session, brand, o, T0, email="owner@clean.example", notes="INTERNAL: price objection")
    _room(db_session, a, "https://zoom.us/j/333?pwd=c")
    a.rescheduled_count = 2; db_session.commit()
    ics = BP.ics_for(db_session, o, a, now=datetime(2026, 11, 18, 12, 0))
    flat = ics.replace("\r\n ", "")
    assert "UID:" + ics_uid(a.id, "owner@clean.example") in flat and "SEQUENCE:2" in flat
    assert "DTSTART:" + T0.strftime("%Y%m%dT%H%M%SZ") in flat
    assert "DTEND:" + (T0 + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ") in flat
    assert "SUMMARY:Business Growth Call with Mike Simmons (EvoSys Pro)" in flat
    assert "https://zoom.us/j/333?pwd=c" in flat and "Clean\; Co\\, LLC" in flat
    assert "INTERNAL" not in flat and "METHOD:PUBLISH" in flat and "\r\n" in ics


def test_double_click_or_retry_books_once(db_session, brand, manager, monkeypatch):
    import app.routers.sales_scheduling_router as SR
    o = _lead(db_session, brand, manager, "Delta Clean", phone="2145550205")
    monkeypatch.setattr(BP, "slots", lambda db, opp, start=None, days=7, exclude_appt_id=None:
                        {"slots": [T0.isoformat() + "Z", (T0 + timedelta(hours=1)).isoformat() + "Z"]})
    monkeypatch.setattr(BP, "_after_booking", lambda *a, **k: None)
    monkeypatch.setattr(BP, "zoom_state", lambda db, a, now=None: {"zoom_status": "preparing", "zoom_url": None})

    def mk(body, user, db):
        a = SalesAppointment(brand_sales_org_id=body.brand_sales_org_id, opportunity_id=body.opportunity_id,
                             title=body.title, starts_at=body.starts_at, ends_at=body.starts_at + timedelta(minutes=30),
                             status="scheduled")
        db.add(a); db.commit()
        return {"id": a.id}
    monkeypatch.setattr(SR, "create_appointment", mk)
    first = BP.book(db_session, o, T0)
    again = BP.book(db_session, o, T0)
    other = BP.book(db_session, o, T0 + timedelta(hours=1))
    assert first["status"] == "booked" and not first["already_booked"]
    assert again["already_booked"] and other["already_booked"] and other["starts_at"] == T0.isoformat() + "Z"
    assert db_session.query(SalesAppointment).filter_by(opportunity_id=o.id).count() == 1
    # a reschedule to the time already held changes nothing
    monkeypatch.setattr(SR, "reschedule_appointment", lambda *a, **k: pytest.fail("should not move"))
    assert BP.reschedule(db_session, o, T0)["already_booked"]


def test_a_link_only_reaches_its_own_call(db_session, brand, manager):
    from fastapi import HTTPException
    from app.routers import evosys_sales_router as R
    a_opp = _lead(db_session, brand, manager, "Echo Clean", phone="2145550206")
    b_opp = _lead(db_session, brand, manager, "Foxtrot Clean", phone="2145550207")
    _appt(db_session, brand, a_opp, T0)
    b = _appt(db_session, brand, b_opp, T0 + timedelta(hours=2))
    _room(db_session, b, "https://zoom.us/j/444?pwd=b")
    tok_a = BP.url_for(a_opp.id).rsplit("/", 1)[1]
    r = R.booking_ics(tok_a, db_session)
    body = r.body.decode()
    assert r.media_type.startswith("text/calendar") and "444" not in body
    assert "DTSTART:" + T0.strftime("%Y%m%dT%H%M%SZ") in body
    for bad in (tok_a[:-2] + "xx", b.id, "nope"):
        with pytest.raises(HTTPException) as e:
            R.booking_ics(bad, db_session)
        assert e.value.status_code == 404
    with pytest.raises(HTTPException):
        R.booking_ics(BP.url_for(_lead(db_session, brand, manager, "No Call Co", phone="2145550208").id)
                      .rsplit("/", 1)[1], db_session)


def test_central_time_follows_daylight_saving():
    assert BP._ct(datetime(2026, 11, 23, 16, 0)) == "Mon Nov 23 at 10:00 AM Central"   # CST, UTC-6
    assert BP._ct(datetime(2027, 3, 15, 16, 0)) == "Mon Mar 15 at 11:00 AM Central"    # CDT, UTC-5


def test_page_uses_icons_not_emoji_and_only_promises_what_is_true():
    html = BP.render("tok", "Pretty Clean", 30)
    for e in ("\U0001F4C5", "\U0001F4C6", "\U0001F3A5", "\U0001F552", "⚖", "⚲"):
        assert e not in html
    assert 'aria-hidden="true"' in html and 'rel="noopener noreferrer"' in html
    assert "Your Zoom link is being prepared" in html and "/event.ics" in html
    # the calendar claim is only rendered with a real link
    assert "ready?'<p class=\"foot\">Zoom link included in your calendar event.</p>'" in html
