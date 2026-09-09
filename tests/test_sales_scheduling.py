"""
Sales Scheduling test suite — SALES-03 (scheduling half).

Covers the 22 routes in app/routers/sales_scheduling_router.py:
  GET  /sales/meeting-types
  GET  /sales/availability/me
  PUT  /sales/availability/me
  POST /sales/availability/time-off
  DELETE /sales/availability/time-off/{block_id}
  GET  /sales/availability/team
  POST /sales/availability/find
  POST /sales/appointments
  GET  /sales/appointments
  GET  /sales/appointments/confirm/{token}       ← public, GET changes NOTHING
  POST /sales/appointments/confirm/{token}       ← public, records answer
  GET  /sales/appointments/confirm/{token}/context
  POST /sales/appointments/confirm/{token}/respond
  GET  /sales/appointments/{appt_id}
  POST /sales/appointments/{appt_id}/confirmation
  POST /sales/appointments/{appt_id}/cancel
  … (resync, host-link, video, resend — auth guard only)

WHAT THIS SUITE DEFENDS
-----------------------
1. Every authenticated route returns 401 for anonymous callers.
2. A JWT holder with NO sales membership gets 403 from every authenticated route.
3. A rep from Brand A CANNOT list or read Brand B's appointments.
4. POST /sales/appointments refuses participants not in the booking brand.
5. GET /sales/appointments/confirm/{token} is side-effect-free: fetching it
   twice does NOT change confirmation_status (scanner-safe).
6. POST /sales/appointments/confirm/{token} records confirm / decline correctly.
7. An invalid token returns a 200 error page on the HTML confirm GET/POST;
   the JSON /context endpoint returns {"ok": False} on a bad token.
8. Cancelled appointments surface "cancelled" on the confirmation page.
"""

import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (
    SalesAppointment, AppointmentParticipant,
    APPT_SCHEDULED, APPT_CANCELLED,
    CONF_PENDING, CONF_CONFIRMED, CONF_DECLINED,
    CONF_SRC_STAFF_MANUAL,
)
from app.models.calendar_models import AppointmentConfirmationToken
from app.services.auth_service import hash_password, create_access_token

_SEQ = itertools.count(1)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture()
def platform(db_session):
    p = Platform(name="EvoSys Pro", slug="sched-plat-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def brand(db_session, platform):
    b = BrandSalesOrg(
        platform_id=platform.id,
        name="EvoSys Sales",
        slug="sched-brand-%d" % next(_SEQ),
    )
    db_session.add(b)
    db_session.commit()
    return b


@pytest.fixture()
def brand_b(db_session, platform):
    b = BrandSalesOrg(
        platform_id=platform.id,
        name="EvoSys Sales B",
        slug="sched-brand-b-%d" % next(_SEQ),
    )
    db_session.add(b)
    db_session.commit()
    return b


def _make_user(db, email=None):
    email = email or "sched%d@test.live" % next(_SEQ)
    u = User(
        organization_id=None,
        email=email,
        password_hash=hash_password("x"),
        full_name="Sched User",
        role="advisor",
        must_change_password=False,
    )
    db.add(u)
    db.commit()
    return u


def _add_membership(db, user, brand, role):
    m = Membership(
        user_id=user.id,
        scope_type=SCOPE_BRAND_SALES_ORG,
        scope_id=brand.id,
        role=role,
        is_active=True,
    )
    db.add(m)
    db.commit()
    return m


def _headers(user, db):
    token = create_access_token(user, db)
    return {"Authorization": "Bearer " + token}


def _future(minutes=120):
    return (datetime.utcnow() + timedelta(minutes=minutes)).isoformat()


def _make_appt(db, brand, starts_minutes_from_now=120, duration=30):
    starts_at = datetime.utcnow() + timedelta(minutes=starts_minutes_from_now)
    ends_at = starts_at + timedelta(minutes=duration)
    appt = SalesAppointment(
        brand_sales_org_id=brand.id,
        title="Test Meeting",
        starts_at=starts_at,
        ends_at=ends_at,
        status=APPT_SCHEDULED,
        confirmation_status=CONF_PENDING,
    )
    db.add(appt)
    db.commit()
    return appt


def _make_token(db, appt):
    import secrets
    tok = AppointmentConfirmationToken(
        appointment_id=appt.id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.utcnow() + timedelta(days=7),
    )
    db.add(tok)
    db.commit()
    return tok


# ═══════════════════════════════════════════════════════════════════════
# 1. Auth guards — anon + non-member
# ═══════════════════════════════════════════════════════════════════════

_AUTHENTICATED_ROUTES = [
    ("GET",    "/sales/meeting-types"),
    ("GET",    "/sales/availability/me"),
    ("PUT",    "/sales/availability/me"),
    ("POST",   "/sales/availability/time-off"),
    ("DELETE", "/sales/availability/time-off/nonexistent"),
    ("GET",    "/sales/availability/team"),
    ("POST",   "/sales/availability/find"),
    ("POST",   "/sales/appointments"),
    ("GET",    "/sales/appointments"),
    ("GET",    "/sales/appointments/nonexistent"),
    ("POST",   "/sales/appointments/nonexistent/confirmation"),
    ("POST",   "/sales/appointments/nonexistent/cancel"),
    ("POST",   "/sales/appointments/nonexistent/resync"),
    ("GET",    "/sales/appointments/nonexistent/host-link"),
    ("POST",   "/sales/appointments/nonexistent/video/retry"),
    ("POST",   "/sales/appointments/nonexistent/resend-invitation"),
    ("GET",    "/sales/video/status"),
]


def _call(client, method, path, headers=None):
    """Dispatch a request, omitting json body for methods that can't take it."""
    kwargs = {"headers": headers} if headers else {}
    if method in ("POST", "PUT", "PATCH"):
        kwargs["json"] = {}
    return getattr(client, method.lower())(path, **kwargs)


@pytest.mark.parametrize("method,path", _AUTHENTICATED_ROUTES)
def test_anon_gets_401(client, method, path):
    r = _call(client, method, path)
    assert r.status_code == 401, (
        "%s %s returned %d for anon, expected 401" % (method, path, r.status_code))


@pytest.mark.parametrize("method,path", _AUTHENTICATED_ROUTES)
def test_no_sales_membership_gets_403(client, db_session, method, path):
    user = _make_user(db_session)  # no membership at all
    r = _call(client, method, path, headers=_headers(user, db_session))
    assert r.status_code in (403, 404), (
        "%s %s returned %d for member-less user, expected 403/404" % (
            method, path, r.status_code))


# ═══════════════════════════════════════════════════════════════════════
# 2. Meeting types
# ═══════════════════════════════════════════════════════════════════════

def test_meeting_types_returns_list_for_member(client, db_session, brand):
    rep = _make_user(db_session)
    _add_membership(db_session, rep, brand, ROLE_SALES_REP)
    r = client.get(
        "/sales/meeting-types",
        params={"brand_sales_org_id": brand.id},
        headers=_headers(rep, db_session),
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_meeting_types_refused_for_wrong_brand(client, db_session, brand, brand_b):
    rep_a = _make_user(db_session)
    _add_membership(db_session, rep_a, brand, ROLE_SALES_REP)
    r = client.get(
        "/sales/meeting-types",
        params={"brand_sales_org_id": brand_b.id},
        headers=_headers(rep_a, db_session),
    )
    assert r.status_code in (403, 404)


# ═══════════════════════════════════════════════════════════════════════
# 3. My availability round-trip
# ═══════════════════════════════════════════════════════════════════════

def test_get_and_put_my_availability(client, db_session, brand):
    rep = _make_user(db_session)
    _add_membership(db_session, rep, brand, ROLE_SALES_REP)
    h = _headers(rep, db_session)
    # GET — should always 200 (creates a default profile if missing)
    r = client.get("/sales/availability/me",
                   params={"brand_sales_org_id": brand.id}, headers=h)
    assert r.status_code == 200
    data = r.json()
    assert "windows" in data

    # PUT — update timezone
    payload = dict(data)
    payload["timezone"] = "America/Chicago"
    r2 = client.put("/sales/availability/me",
                    params={"brand_sales_org_id": brand.id},
                    json=payload, headers=h)
    assert r2.status_code == 200
    assert r2.json()["timezone"] == "America/Chicago"


# ═══════════════════════════════════════════════════════════════════════
# 4. Appointment list — brand isolation
# ═══════════════════════════════════════════════════════════════════════

def test_appointments_list_brand_isolation(client, db_session, brand, brand_b):
    """A rep from Brand A listing appointments for Brand B gets 403/404."""
    rep_a = _make_user(db_session)
    _add_membership(db_session, rep_a, brand, ROLE_SALES_REP)

    r = client.get(
        "/sales/appointments",
        params={"brand_sales_org_id": brand_b.id},
        headers=_headers(rep_a, db_session),
    )
    assert r.status_code in (403, 404)


def test_appointments_list_own_brand_ok(client, db_session, brand):
    manager = _make_user(db_session)
    _add_membership(db_session, manager, brand, ROLE_SALES_MANAGER)

    r = client.get(
        "/sales/appointments",
        params={"brand_sales_org_id": brand.id},
        headers=_headers(manager, db_session),
    )
    assert r.status_code == 200
    body = r.json()
    assert "appointments" in body or isinstance(body, list)


# ═══════════════════════════════════════════════════════════════════════
# 5. Create appointment — cross-brand participant rejection
# ═══════════════════════════════════════════════════════════════════════

def test_create_appointment_rejects_cross_brand_participant(
        client, db_session, brand, brand_b):
    """A participant from Brand B cannot be added to a Brand A appointment."""
    manager = _make_user(db_session)
    _add_membership(db_session, manager, brand, ROLE_SALES_MANAGER)

    outsider = _make_user(db_session)
    _add_membership(db_session, outsider, brand_b, ROLE_SALES_REP)

    payload = {
        "brand_sales_org_id": brand.id,
        "title": "Cross-brand test",
        "starts_at": _future(120),
        "duration_minutes": 30,
        "required_user_ids": [outsider.id],   # not a member of brand
        "optional_user_ids": [],
    }
    r = client.post("/sales/appointments", json=payload, headers=_headers(manager, db_session))
    assert r.status_code == 400, (
        "Expected 400 for cross-brand participant, got %d" % r.status_code)


def test_create_appointment_requires_at_least_one_participant(
        client, db_session, brand):
    manager = _make_user(db_session)
    _add_membership(db_session, manager, brand, ROLE_SALES_MANAGER)

    payload = {
        "brand_sales_org_id": brand.id,
        "title": "No participants",
        "starts_at": _future(120),
        "duration_minutes": 30,
        "required_user_ids": [],
        "optional_user_ids": [],
    }
    r = client.post("/sales/appointments", json=payload, headers=_headers(manager, db_session))
    assert r.status_code == 400


def test_create_appointment_refuses_past_start(client, db_session, brand):
    manager = _make_user(db_session)
    _add_membership(db_session, manager, brand, ROLE_SALES_MANAGER)

    past = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    payload = {
        "brand_sales_org_id": brand.id,
        "title": "Past meeting",
        "starts_at": past,
        "duration_minutes": 30,
        "required_user_ids": [manager.id],
        "optional_user_ids": [],
    }
    r = client.post("/sales/appointments", json=payload, headers=_headers(manager, db_session))
    assert r.status_code == 400


# ═══════════════════════════════════════════════════════════════════════
# 6. Confirmation token — public, GET is side-effect-free
# ═══════════════════════════════════════════════════════════════════════

def test_confirm_get_renders_page_for_valid_token(client, db_session, brand):
    """GET /sales/appointments/confirm/{token} returns an HTML page."""
    appt = _make_appt(db_session, brand)
    tok = _make_token(db_session, appt)

    r = client.get("/sales/appointments/confirm/%s" % tok.token)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_confirm_get_does_not_change_status(client, db_session, brand):
    """Fetching the confirmation page TWICE leaves confirmation_status unchanged.

    Corporate link-scanners (Safe Links, Proofpoint) pre-fetch every URL in a
    delivered email. If a GET changed state, a scanner would auto-confirm the
    meeting before the prospect ever opened it.
    """
    appt = _make_appt(db_session, brand)
    tok = _make_token(db_session, appt)

    client.get("/sales/appointments/confirm/%s" % tok.token)
    client.get("/sales/appointments/confirm/%s" % tok.token)

    db_session.refresh(appt)
    assert appt.confirmation_status == CONF_PENDING, (
        "Two GETs changed confirmation_status to %r — GET must stay side-effect-free"
        % appt.confirmation_status)


def test_confirm_get_invalid_token_returns_200_error_page(client):
    """An invalid token still returns 200 HTML — not a 4xx that exposes internals."""
    r = client.get("/sales/appointments/confirm/not-a-real-token")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_confirm_get_cancelled_appt_shows_cancelled(client, db_session, brand):
    appt = _make_appt(db_session, brand)
    appt.status = APPT_CANCELLED
    db_session.commit()
    tok = _make_token(db_session, appt)

    r = client.get("/sales/appointments/confirm/%s" % tok.token)
    assert r.status_code == 200
    assert "cancel" in r.text.lower()


# ═══════════════════════════════════════════════════════════════════════
# 7. Confirmation token — POST records answer
# ═══════════════════════════════════════════════════════════════════════

def test_confirm_post_records_confirmed(client, db_session, brand):
    appt = _make_appt(db_session, brand)
    tok = _make_token(db_session, appt)

    r = client.post(
        "/sales/appointments/confirm/%s" % tok.token,
        data={"action": "confirm"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    db_session.refresh(appt)
    assert appt.confirmation_status == CONF_CONFIRMED, (
        "Expected CONF_CONFIRMED after confirm POST, got %r" % appt.confirmation_status)


def test_confirm_post_records_declined(client, db_session, brand):
    appt = _make_appt(db_session, brand)
    tok = _make_token(db_session, appt)

    r = client.post(
        "/sales/appointments/confirm/%s" % tok.token,
        data={"action": "decline"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    db_session.refresh(appt)
    assert appt.confirmation_status == CONF_DECLINED, (
        "Expected CONF_DECLINED after decline POST, got %r" % appt.confirmation_status)


def test_confirm_post_invalid_token_returns_200_error_page(client):
    r = client.post(
        "/sales/appointments/confirm/not-a-real-token",
        data={"action": "confirm"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200


def test_confirm_post_cancelled_appt_does_not_change_status(client, db_session, brand):
    appt = _make_appt(db_session, brand)
    appt.status = APPT_CANCELLED
    db_session.commit()
    tok = _make_token(db_session, appt)

    client.post(
        "/sales/appointments/confirm/%s" % tok.token,
        data={"action": "confirm"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    db_session.refresh(appt)
    assert appt.confirmation_status == CONF_PENDING, (
        "Posting confirm on a cancelled appt should not change confirmation_status")


# ═══════════════════════════════════════════════════════════════════════
# 8. JSON /context endpoint (branded confirmation page backend)
# ═══════════════════════════════════════════════════════════════════════

def test_confirm_context_valid_token(client, db_session, brand):
    appt = _make_appt(db_session, brand)
    tok = _make_token(db_session, appt)

    r = client.get("/sales/appointments/confirm/%s/context" % tok.token)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "title" in body
    assert "when" in body
    assert "cancelled" in body


def test_confirm_context_invalid_token(client):
    r = client.get("/sales/appointments/confirm/bad-token/context")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False


def test_confirm_context_does_not_expose_internal_ids(client, db_session, brand):
    """The context response must not leak internal appointment or opportunity IDs."""
    appt = _make_appt(db_session, brand)
    tok = _make_token(db_session, appt)

    r = client.get("/sales/appointments/confirm/%s/context" % tok.token)
    assert r.status_code == 200
    body = r.json()
    # These fields must not be present
    assert "appointment_id" not in body
    assert "opportunity_id" not in body
    assert "brand_sales_org_id" not in body


def test_confirm_context_no_auth_required(client, db_session, brand):
    """The context endpoint is intentionally unauthenticated — the token IS the auth."""
    appt = _make_appt(db_session, brand)
    tok = _make_token(db_session, appt)

    r = client.get("/sales/appointments/confirm/%s/context" % tok.token)
    # Must work with no Authorization header at all
    assert r.status_code == 200
    assert r.json()["ok"] is True
