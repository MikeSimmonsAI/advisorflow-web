"""
Smoke tests for the new Calendar + Sales Workspace scheduling surfaces.

Deliberately thin: these prove the new routes are REACHABLE and return the
shape the two screens read. The behavioural suite — intersection, buffers,
DST, concurrency, privacy, outcome lifecycle, reconciliation — lives in
test_calendar_scheduling.py.

This file exists because "the endpoint 500s on an empty brand" is the failure
you want to find before writing six hundred lines of frontend against it, and
an empty brand is exactly the state a new one is in.
"""
import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password, create_access_token

_SEQ = itertools.count(1)


@pytest.fixture()
def brand(db_session):
    p = Platform(name="EvoSys Pro", slug="cs-plat-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    b = BrandSalesOrg(platform_id=p.id, name="EvoSys Sales",
                      slug="cs-brand-%d" % next(_SEQ))
    db_session.add(b)
    db_session.commit()
    return b


def _user(db, brand, role, email=None):
    u = User(organization_id=None,
             email=email or "cs%d@test.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name="CS User",
             role="advisor", must_change_password=False)
    db.add(u)
    db.commit()
    db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=brand.id, role=role, is_active=True))
    db.commit()
    return u


def _h(u, db):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


# ── the calendar view ───────────────────────────────────────────────────────

def test_calendar_view_responds_on_an_empty_brand(client, db_session, brand):
    """A brand with no appointments is a normal state, not an error.

    Every new brand starts here, and a 500 on first load is the worst possible
    first impression of a calendar.
    """
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.get("/sales/calendar/view", headers=_h(mgr, db_session))
    assert r.status_code == 200, r.text
    body = r.json()
    # The contract the screen reads. Asserted by KEY rather than by value so a
    # panel cannot silently disappear from the payload.
    for key in ("appointments", "people", "agenda_today", "attention",
                "upcoming", "meeting_types", "locations", "range",
                "external_visibility", "sync_status", "external_included"):
        assert key in body, "calendar view is missing '%s'" % key
    assert body["appointments"] == []
    assert body["is_manager"] is True


def test_calendar_view_requires_auth(client):
    assert client.get("/sales/calendar/view").status_code == 401


def test_calendar_view_narrows_a_rep_instead_of_refusing(client, db_session, brand):
    """A rep who lands on the team calendar gets their OWN week, not a 403.

    The screen is useful either way, and a permission error rendered on a
    landing page punishes somebody who did nothing but click a nav item.
    """
    rep = _user(db_session, brand, ROLE_SALES_REP)
    r = client.get("/sales/calendar/view?scope=team", headers=_h(rep, db_session))
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "mine"
    assert r.json()["is_manager"] is False


def test_calendar_view_rejects_an_absurd_range(client, db_session, brand):
    """A range wide enough to hammer every provider is refused with a reason."""
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.get("/sales/calendar/view?date_from=2026-01-01&date_to=2027-01-01",
                   headers=_h(mgr, db_session))
    assert r.status_code == 400
    assert "at a time" in r.json()["detail"]


def test_calendar_view_rejects_a_backwards_range(client, db_session, brand):
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.get("/sales/calendar/view?date_from=2026-09-20&date_to=2026-09-10",
                   headers=_h(mgr, db_session))
    assert r.status_code == 400


# ── sync status ─────────────────────────────────────────────────────────────

def test_sync_status_reports_not_connected_with_a_reason(client, db_session, brand):
    """No meaningless green dots: an unconnected provider says so, and says
    what it means for the caller."""
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.get("/sales/calendar/sync-status", headers=_h(mgr, db_session))
    assert r.status_code == 200, r.text
    body = r.json()
    assert {p["provider"] for p in body["mine"]} == {"microsoft", "google"}
    for p in body["mine"]:
        assert p["state"] == "not_connected"
        # A state with no explanation is the thing the brief forbids.
        assert p["detail"]
        assert p["action"] == "connect"
    assert body["any_attention"] is False


def test_sync_status_requires_auth(client):
    assert client.get("/sales/calendar/sync-status").status_code == 401


# ── conflicts ───────────────────────────────────────────────────────────────

def test_conflicts_queue_is_manager_only(client, db_session, brand):
    rep = _user(db_session, brand, ROLE_SALES_REP)
    assert client.get("/sales/calendar/conflicts",
                      headers=_h(rep, db_session)).status_code == 403


def test_conflicts_queue_is_empty_by_default(client, db_session, brand):
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.get("/sales/calendar/conflicts", headers=_h(mgr, db_session))
    assert r.status_code == 200, r.text
    assert r.json()["conflicts"] == []


# ── outcome queue ───────────────────────────────────────────────────────────

def test_pending_outcome_route_is_not_swallowed_by_the_id_route(client, db_session, brand):
    """ROUTE ORDER REGRESSION GUARD.

    `/sales/appointments/pending-outcome` must be matched by its own handler,
    not resolved as appt_id="pending-outcome". If somebody later moves the
    declaration below `/appointments/{appt_id}`, this test fails with a 404
    or a 403 about another representative's meeting — which is exactly the
    confusing symptom the ordering comment in the router is there to prevent.
    """
    rep = _user(db_session, brand, ROLE_SALES_REP)
    r = client.get("/sales/appointments/pending-outcome", headers=_h(rep, db_session))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "mine"
    assert body["appointments"] == []


def test_completion_facts_is_manager_only_and_honest(client, db_session, brand):
    """The T9 contract. With nothing recorded, the rate is None — not zero.

    Zero would be a claim ("no meetings completed"); None is the truth ("no
    verdicts recorded"). Reporting the first when the second is the case is
    the whole failure this endpoint exists to end.
    """
    rep = _user(db_session, brand, ROLE_SALES_REP)
    assert client.get("/sales/appointments/completion-facts",
                      headers=_h(rep, db_session)).status_code == 403

    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.get("/sales/appointments/completion-facts", headers=_h(mgr, db_session))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["completion_rate"] is None
    assert body["authoritative"] is True
    assert body["inferred_from_clock"] is False
    assert body["unrecorded"] == 0


# ── team availability, with the layers the new grid draws ───────────────────

def test_team_availability_returns_the_new_layers(client, db_session, brand):
    """Image 4's grid needs working hours, blocked time, PTO and external busy
    as separate bands — not just free/busy."""
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.get("/sales/availability/team", headers=_h(mgr, db_session))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["members"], "the manager themselves is a brand member"
    m = body["members"][0]
    for key in ("free", "busy", "working", "blocked", "time_off",
                "external_busy", "external"):
        assert key in m, "member layer '%s' is missing" % key
    # A default profile is Mon-Fri 9-5 with a lunch, so a weekday has both.
    assert isinstance(m["working"], list)
    assert isinstance(m["blocked"], list)
    assert "external_visibility" in body
    assert "sync_status" in body


def test_team_availability_states_that_external_was_not_checked(client, db_session, brand):
    """With no calendar connected, the payload must SAY so rather than present
    an unverified column with the same confidence as a verified one."""
    mgr = _user(db_session, brand, ROLE_SALES_MANAGER)
    r = client.get("/sales/availability/team", headers=_h(mgr, db_session))
    body = r.json()
    ext = body["members"][0]["external"]
    assert ext["external_checked"] is False
    assert ext["state"] == "not_connected"
    assert ext["message"]
    assert body["external_visibility"]["complete"] is False
