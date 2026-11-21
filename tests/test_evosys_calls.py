"""EvoSys Sales Calls: book through the real scheduler, book without sending,
availability check, day view, detail, notes, other-brand isolation."""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.scheduling_models import SalesAppointment
from app.routers import evosys_sales_router as R
from app.services.evosys_sales import calls as CL
from app.services.evosys_sales import booking_page as BP
from app.services.evosys_sales import outreach as O
from app.services.evosys_sales import sms_inbound as SI

from tests.test_sales_workspace import platform, brand, manager, _make_sales_user, _add_membership, ROLE_SALES_MANAGER, _SEQ  # noqa: F401
from tests.test_evosys_sales_texting import _ready, _lead, _brand_env  # noqa: F401


def _soon(days=2, hour=21):
    return (datetime.utcnow() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


@pytest.fixture
def world(db_session, brand, manager, monkeypatch):
    _ready(db_session, brand)
    _add_membership(db_session, manager, brand, ROLE_SALES_MANAGER)
    import app.routers.sales_scheduling_router as SR
    sent = {"invite": 0, "texts": [], "alerts": []}
    monkeypatch.setattr(SR.apinvite, "send_prospect_invitation",
                        lambda db, a, kind="invite": sent.__setitem__("invite", sent["invite"] + 1) or {"ok": True})
    monkeypatch.setattr(SR.apmeet, "ensure_meeting", lambda db, a, now=None: {"ok": True})
    monkeypatch.setattr(SR.apsync, "sync_appointment", lambda db, a, organizer=None: {"ok": True})
    monkeypatch.setattr(O, "_send_text", lambda to, body, s: sent["texts"].append(body) or {"success": True})
    monkeypatch.setattr(SI, "notify_owner", lambda *a: sent["alerts"].append(a[1]))
    monkeypatch.setattr(BP, "zoom_link", lambda db, i: "https://zoom.test/j/1")
    return R.Ctx(manager, brand.id, True), sent


def test_book_and_send_vs_book_without_sending(db_session, brand, manager, world):
    c, sent = world
    o = _lead(db_session, brand, manager, "Pretty Clean", phone="2145550601", email="pc@example.test")
    out = R.calls_book(R.CallBookIn(lead_id=o.id, starts_at=_soon(), host_id=manager.id, send=True), c, db_session)
    a = db_session.get(SalesAppointment, out["id"])
    assert a.title == "Business Growth Call" and int((a.ends_at - a.starts_at).total_seconds()) == 1800
    assert sent["invite"] == 1 and sent["texts"] and "zoom.test" in sent["texts"][0] and out["texted"]
    o2 = _lead(db_session, brand, manager, "Quiet Co", phone="2145550602", email="q@example.test")
    R.calls_book(R.CallBookIn(lead_id=o2.id, starts_at=_soon(3), host_id=manager.id, send=False), c, db_session)
    assert sent["invite"] == 1 and len(sent["texts"]) == 1                # nothing went to Quiet Co


def test_double_booking_is_refused_and_check_says_why(db_session, brand, manager, world):
    c, _ = world
    o = _lead(db_session, brand, manager, "One", phone="2145550603")
    t = _soon(4)
    R.calls_book(R.CallBookIn(lead_id=o.id, starts_at=t, host_id=manager.id, send=False), c, db_session)
    chk = R.calls_check(R.CallCheckIn(starts_at=t + timedelta(minutes=15), host_id=manager.id), c, db_session)
    assert chk["available"] is False and chk["conflicts"]
    o2 = _lead(db_session, brand, manager, "Two", phone="2145550604")
    with pytest.raises(HTTPException) as e:
        R.calls_book(R.CallBookIn(lead_id=o2.id, starts_at=t, host_id=manager.id, send=False), c, db_session)
    assert e.value.status_code == 409
    assert R.calls_check(R.CallCheckIn(starts_at=datetime.utcnow() - timedelta(hours=1)), c, db_session)["available"] is False


def test_guest_booking_lists_details_notes_and_isolation(db_session, brand, manager, world):
    c, _ = world
    out = R.calls_book(R.CallBookIn(guest_name="Walk In", guest_email="w@example.test", starts_at=_soon(5),
                                    host_id=manager.id, send=False), c, db_session)
    assert R.calls_list("upcoming", c, db_session)["calls"][0]["id"] == out["id"]
    d = R.calls_detail(out["id"], c, db_session)
    assert d["call"]["prospect"]["email"] == "w@example.test" and d["replays"]["items"] == []
    R.calls_notes(out["id"], R.CallNotesIn(notes="Bring pricing"), c, db_session)
    assert db_session.get(SalesAppointment, out["id"]).notes == "Bring pricing"
    other = R.Ctx(manager, "some-other-brand", True)
    with pytest.raises(HTTPException):
        R.calls_detail(out["id"], other, db_session)
    with pytest.raises(HTTPException):
        R.calls_book(R.CallBookIn(starts_at=_soon(6), host_id=manager.id), c, db_session)   # no lead, no guest


def test_day_view_and_meta(db_session, brand, manager, world):
    c, _ = world
    o = _lead(db_session, brand, manager, "Day Co", phone="2145550605")
    t = _soon(2, 21)
    R.calls_book(R.CallBookIn(lead_id=o.id, starts_at=t, host_id=manager.id, send=False), c, db_session)
    from app.services import availability as av
    d = av.utc_to_local(t, "America/Chicago").date()
    out = R.calls_day(d, manager.id, c, db_session)
    assert any(i["kind"] == "call" for i in out["items"]) and out["timezone"] == "America/Chicago"
    assert R.calls_meta(c, db_session)["title"] == "Business Growth Call"


def test_settings_read_out(db_session, brand, manager, world):
    c, _ = world
    s = O.get_settings(db_session, brand.id)
    s.alert_phone, s.alert_also = "+18435328405", [{"name": "Michael Schlueter", "phone": "+15403927776"}]
    db_session.commit()
    out = R.calls_settings(manager.id, c, db_session)
    assert out["timezone"] and out["gap_between_calls_minutes"] == 15
    assert out["alerts"][0]["who"].startswith("Mike") and out["alerts"][1]["who"] == "Michael Schlueter"
    assert "ready" in out["zoom"] and "ready" in out["calling"]
