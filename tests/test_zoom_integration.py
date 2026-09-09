"""
Zoom meeting integration — what the seams guarantee.

WHAT THESE TESTS DEFEND
-----------------------
1. /sales/video/status returns the right state without exposing credentials.
2. ensure_meeting_types backfills requires_video on pre-Checkpoint-4 rows.
3. Booking a requires_video appointment calls the provider and stores the result.
4. Cancellation calls the provider and clears the join_url.
5. The host URL never appears in an appointment response or serialised output.
6. One brand's meetings are invisible to another brand's query.

The Zoom HTTP client is never reached. Tests inject a mock via the provider
registry's test seam (register_provider / reset_providers), so no real Zoom
credentials are needed and no network calls are made.
"""
import itertools
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.models.models import User, Platform
from app.models.sales_models import (
    BrandSalesOrg, Membership,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_REP, ROLE_SALES_MANAGER,
)
from app.models.scheduling_models import MeetingType, SalesAppointment
from app.models.meeting_models import AppointmentMeeting, MEET_CREATED, MEET_CANCELLED
from app.services.auth_service import hash_password, create_access_token
from app.services.meeting_providers import (
    PROVIDER_ZOOM, register_provider, reset_providers,
)
from app.services.meeting_providers.base import MeetingResult

_SEQ = itertools.count(1)


# ── helpers ──────────────────────────────────────────────────────────────────

def _platform(db):
    p = Platform(name="EvoSys Pro", slug="evo-%d" % next(_SEQ))
    db.add(p); db.commit()
    return p


def _brand(db, platform=None):
    if platform is None:
        platform = _platform(db)
    b = BrandSalesOrg(platform_id=platform.id,
                      name="EvoSys Sales %d" % next(_SEQ),
                      slug="evo-sales-%d" % next(_SEQ))
    db.add(b); db.commit()
    return b


def _user(db, brand_id=None, role=ROLE_SALES_REP):
    n = next(_SEQ)
    u = User(organization_id=None,
             email="zoom_user_%d@evosyspro.live" % n,
             password_hash=hash_password("x"),
             full_name="Zoom User %d" % n,
             role="advisor",
             must_change_password=False)
    db.add(u); db.commit()
    if brand_id:
        db.add(Membership(user_id=u.id,
                          scope_type=SCOPE_BRAND_SALES_ORG,
                          scope_id=brand_id,
                          role=role,
                          is_active=True))
        db.commit()
    return u


def _token_headers(user, db):
    tok = create_access_token(user, db)
    return {"Authorization": "Bearer " + tok}


def _ok_result(**kw):
    """A MeetingResult that looks like a successful Zoom creation."""
    defaults = dict(
        ok=True,
        provider_meeting_id="zoom_mtg_%d" % next(_SEQ),
        join_url="https://zoom.us/j/99999?pwd=TEST",
        host_url="https://zoom.us/s/99999?zak=HOST_TOKEN",
        passcode="pass123",
    )
    defaults.update(kw)
    return MeetingResult(**defaults)


def _mock_provider(create_result=None, verify_result=None):
    """A mock ZoomProvider that records calls without touching the network."""
    prov = MagicMock()
    prov.is_ready.return_value = (True, None)
    prov.create_meeting.return_value = create_result or _ok_result()
    prov.update_meeting.return_value = create_result or _ok_result()
    prov.cancel_meeting.return_value = MeetingResult(ok=True)
    prov.verify.return_value = verify_result or MeetingResult(
        ok=True,
        error_message="Connected — host identity and API scope confirmed.",
    )
    return prov


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def brand(db_session):
    return _brand(db_session)


@pytest.fixture()
def rep(db_session, brand):
    return _user(db_session, brand.id, ROLE_SALES_REP)


@pytest.fixture()
def rep_headers(db_session, rep):
    return _token_headers(rep, db_session)


@pytest.fixture(autouse=True)
def _reset_providers():
    """Ensure test provider overrides never bleed between tests."""
    yield
    reset_providers()


# ── 1. /sales/video/status ───────────────────────────────────────────────────

def test_video_status_not_configured(client, rep_headers, monkeypatch):
    """When credentials are absent the state must be not_configured."""
    for var in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)

    not_ready = MagicMock()
    not_ready.is_ready.return_value = (False, "not configured")
    register_provider(PROVIDER_ZOOM, lambda _: not_ready)

    resp = client.get("/sales/video/status", headers=rep_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "not_configured"
    assert "host_url" not in data
    assert data.get("setup_hint")


def test_video_status_ready_no_verify(client, rep_headers):
    """Credentials present, no verify requested → state=ready."""
    register_provider(PROVIDER_ZOOM, _mock_provider)

    resp = client.get("/sales/video/status", headers=rep_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "ready"
    # Host URL must never appear in this response
    assert "host_url" not in data


def test_video_status_verify_success(client, rep_headers):
    """verify=true, provider confirms scope → state=ready, detail confirms."""
    mock = _mock_provider(verify_result=MeetingResult(
        ok=True,
        error_message="Connected — host identity and API scope confirmed."))
    register_provider(PROVIDER_ZOOM, lambda _: mock)

    resp = client.get("/sales/video/status?verify=true", headers=rep_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "ready"
    assert "confirmed" in data["detail"].lower()
    assert "host_url" not in data


def test_video_status_verify_failure(client, rep_headers):
    """verify=true, provider rejects → state=error, setup_hint present."""
    mock = _mock_provider(verify_result=MeetingResult(
        ok=False,
        error_message="Invalid credentials or scope missing."))
    register_provider(PROVIDER_ZOOM, lambda _: mock)

    resp = client.get("/sales/video/status?verify=true", headers=rep_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "error"
    assert data.get("setup_hint")


def test_video_status_requires_auth(client):
    """Unauthenticated request must be refused."""
    resp = client.get("/sales/video/status")
    assert resp.status_code in (401, 403)


def test_video_status_shows_meeting_types(client, rep_headers):
    """Response includes meeting types with their requires_video flag.
    Customer-facing types must be True; internal meeting must be False."""
    register_provider(PROVIDER_ZOOM, _mock_provider)

    resp = client.get("/sales/video/status", headers=rep_headers)
    assert resp.status_code == 200
    types = resp.json()["meeting_types"]
    assert len(types) > 0

    video_types = [t for t in types if t["requires_video"]]
    no_video_types = [t for t in types if not t["requires_video"]]
    assert len(video_types) > 0, "Customer-facing types must require video"
    internal_names = [t["name"].lower() for t in no_video_types]
    assert any("internal" in n for n in internal_names), (
        "Internal Sales Meeting must not require video; no_video: %s" % no_video_types)


# ── 2. requires_video backfill ───────────────────────────────────────────────

def test_requires_video_backfill_on_untouched_rows(db_session, brand):
    """A meeting type seeded before Checkpoint 4 (requires_video=False,
    updated_at == created_at) gets requires_video=True on next ensure call."""
    from app.services.meeting_roles import ensure_meeting_types

    now = datetime.utcnow()
    mt = MeetingType(
        brand_sales_org_id=brand.id,
        key="discovery",
        name="Discovery",
        duration_minutes=30,
        requires_video=False,
        sort_order=1,
        created_at=now,
        updated_at=now,  # untouched: timestamps match
    )
    db_session.add(mt)
    db_session.commit()

    types = ensure_meeting_types(db_session, brand.id)
    db_session.commit()

    discovery = next(t for t in types if t.key == "discovery")
    assert discovery.requires_video is True, (
        "ensure_meeting_types must backfill requires_video on untouched rows")


def test_requires_video_not_overwritten_when_user_edited_row(db_session, brand):
    """A row the user deliberately edited (updated_at > created_at) must not
    have its requires_video reset — the user's explicit choice wins."""
    from app.services.meeting_roles import ensure_meeting_types

    now = datetime.utcnow()
    mt = MeetingType(
        brand_sales_org_id=brand.id,
        key="discovery",
        name="Discovery",
        duration_minutes=30,
        requires_video=False,
        sort_order=1,
        created_at=now - timedelta(days=10),
        updated_at=now,  # user edited this row
    )
    db_session.add(mt)
    db_session.commit()

    ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    db_session.refresh(mt)

    assert mt.requires_video is False, (
        "User-edited rows must not be overwritten by the backfill")


# ── 3. Appointment creation provisions a Zoom meeting ────────────────────────

def test_ensure_meeting_creates_provider_meeting(db_session, brand, rep):
    """ensure_meeting on a requires_video appointment creates an
    AppointmentMeeting row and writes join_url onto the appointment."""
    from app.services.appointment_meetings import ensure_meeting
    from app.services.meeting_roles import ensure_meeting_types

    mock = _mock_provider()
    register_provider(PROVIDER_ZOOM, lambda _: mock)

    types = ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    discovery_type = next(t for t in types if t.key == "discovery")

    appt = SalesAppointment(
        brand_sales_org_id=brand.id,
        meeting_type_id=discovery_type.id,
        title="Test Discovery Call",
        starts_at=datetime.utcnow() + timedelta(days=1),
        ends_at=datetime.utcnow() + timedelta(days=1, minutes=30),
        status="scheduled",
    )
    db_session.add(appt); db_session.commit()

    ensure_meeting(db_session, appt)

    db_session.refresh(appt)
    assert appt.meeting_url is not None
    assert "zoom.us" in (appt.meeting_url or "")

    row = db_session.query(AppointmentMeeting).filter(
        AppointmentMeeting.appointment_id == appt.id).first()
    assert row is not None
    assert row.status == MEET_CREATED
    assert row.join_url is not None
    # Host URL must be encrypted at rest — the plain zak token must not appear
    assert row.host_url_encrypted is not None
    assert "HOST_TOKEN" not in (row.host_url_encrypted or ""), (
        "Host URL must be encrypted; plain token found in host_url_encrypted")


def test_ensure_meeting_is_idempotent(db_session, brand, rep):
    """Calling ensure_meeting twice must not create two Zoom meetings."""
    from app.services.appointment_meetings import ensure_meeting
    from app.services.meeting_roles import ensure_meeting_types

    mock = _mock_provider()
    register_provider(PROVIDER_ZOOM, lambda _: mock)

    types = ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    discovery_type = next(t for t in types if t.key == "discovery")

    appt = SalesAppointment(
        brand_sales_org_id=brand.id,
        meeting_type_id=discovery_type.id,
        title="Idempotent Test",
        starts_at=datetime.utcnow() + timedelta(days=2),
        ends_at=datetime.utcnow() + timedelta(days=2, minutes=30),
        status="scheduled",
    )
    db_session.add(appt); db_session.commit()

    ensure_meeting(db_session, appt)
    ensure_meeting(db_session, appt)

    count = db_session.query(AppointmentMeeting).filter(
        AppointmentMeeting.appointment_id == appt.id).count()
    assert count == 1
    assert mock.create_meeting.call_count == 1, (
        "Second call must update, not create a new meeting")


def test_no_zoom_for_non_video_type(db_session, brand, rep):
    """Meeting type with requires_video=False must not provision Zoom."""
    from app.services.appointment_meetings import ensure_meeting
    from app.services.meeting_roles import ensure_meeting_types

    mock = _mock_provider()
    register_provider(PROVIDER_ZOOM, lambda _: mock)

    types = ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    internal_type = next(t for t in types if t.key == "internal")
    assert not internal_type.requires_video

    appt = SalesAppointment(
        brand_sales_org_id=brand.id,
        meeting_type_id=internal_type.id,
        title="Internal Sync",
        starts_at=datetime.utcnow() + timedelta(days=1),
        ends_at=datetime.utcnow() + timedelta(days=1, minutes=30),
        status="scheduled",
    )
    db_session.add(appt); db_session.commit()

    ensure_meeting(db_session, appt)

    assert mock.create_meeting.call_count == 0
    assert appt.meeting_url is None


# ── 4. Cancellation ──────────────────────────────────────────────────────────

def test_cancel_meeting_clears_join_url_and_calls_provider(db_session, brand, rep):
    """cancel_meeting must call the provider and clear join_url on the appointment."""
    from app.services.appointment_meetings import ensure_meeting, cancel_meeting
    from app.services.meeting_roles import ensure_meeting_types

    mock = _mock_provider()
    register_provider(PROVIDER_ZOOM, lambda _: mock)

    types = ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    discovery_type = next(t for t in types if t.key == "discovery")

    appt = SalesAppointment(
        brand_sales_org_id=brand.id,
        meeting_type_id=discovery_type.id,
        title="Cancellation Test",
        starts_at=datetime.utcnow() + timedelta(days=1),
        ends_at=datetime.utcnow() + timedelta(days=1, minutes=30),
        status="scheduled",
    )
    db_session.add(appt); db_session.commit()

    ensure_meeting(db_session, appt)
    db_session.refresh(appt)
    assert appt.meeting_url is not None

    appt.status = "cancelled"
    db_session.commit()
    cancel_meeting(db_session, appt, reason="Test cancellation")

    db_session.refresh(appt)
    assert appt.meeting_url is None
    assert mock.cancel_meeting.call_count == 1

    row = db_session.query(AppointmentMeeting).filter(
        AppointmentMeeting.appointment_id == appt.id).first()
    assert row.status == MEET_CANCELLED


# ── 5. Host URL never leaks ──────────────────────────────────────────────────

def test_host_url_encrypted_at_rest(db_session, brand, rep):
    """The Zoom start_url token must be encrypted before storage.
    The plain zak token must not appear in host_url_encrypted."""
    from app.services.appointment_meetings import ensure_meeting
    from app.services.meeting_roles import ensure_meeting_types

    host_token = "zak_THIS_IS_THE_HOST_SECRET_NEVER_STORE_PLAIN"
    mock = _mock_provider(create_result=MeetingResult(
        ok=True,
        provider_meeting_id="zoom_999",
        join_url="https://zoom.us/j/999",
        host_url="https://zoom.us/s/999?zak=" + host_token,
    ))
    register_provider(PROVIDER_ZOOM, lambda _: mock)

    types = ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    discovery_type = next(t for t in types if t.key == "discovery")

    appt = SalesAppointment(
        brand_sales_org_id=brand.id,
        meeting_type_id=discovery_type.id,
        title="Host URL encryption test",
        starts_at=datetime.utcnow() + timedelta(days=1),
        ends_at=datetime.utcnow() + timedelta(days=1, minutes=30),
        status="scheduled",
    )
    db_session.add(appt); db_session.commit()
    ensure_meeting(db_session, appt)

    row = db_session.query(AppointmentMeeting).filter(
        AppointmentMeeting.appointment_id == appt.id).first()
    assert host_token not in (row.host_url_encrypted or ""), (
        "Host URL stored as plain text — Fernet encryption must be applied")


def test_meeting_out_has_no_host_url_field(db_session, brand, rep):
    """meeting_out() must never include a host_url key.
    The host link is returned only by the participant-gated /host-link endpoint."""
    from app.services.appointment_meetings import (
        ensure_meeting, meeting_out, get_meeting_row,
    )
    from app.services.meeting_roles import ensure_meeting_types

    mock = _mock_provider()
    register_provider(PROVIDER_ZOOM, lambda _: mock)

    types = ensure_meeting_types(db_session, brand.id)
    db_session.commit()
    discovery_type = next(t for t in types if t.key == "discovery")

    appt = SalesAppointment(
        brand_sales_org_id=brand.id,
        meeting_type_id=discovery_type.id,
        title="Serialiser safety test",
        starts_at=datetime.utcnow() + timedelta(days=1),
        ends_at=datetime.utcnow() + timedelta(days=1, minutes=30),
        status="scheduled",
    )
    db_session.add(appt); db_session.commit()
    ensure_meeting(db_session, appt)

    row = get_meeting_row(db_session, appt.id)
    out = meeting_out(row)
    assert "host_url" not in out, (
        "meeting_out must never include host_url; got keys: %s" % list(out.keys()))


# ── 6. Tenant isolation ───────────────────────────────────────────────────────

def test_meeting_row_is_scoped_to_its_brand(db_session):
    """An AppointmentMeeting created for brand A must not be found when
    querying by brand B's id."""
    from app.services.appointment_meetings import ensure_meeting
    from app.services.meeting_roles import ensure_meeting_types

    plat = _platform(db_session)
    brand_a = _brand(db_session, plat)
    brand_b = _brand(db_session, plat)

    mock = _mock_provider()
    register_provider(PROVIDER_ZOOM, lambda _: mock)

    types_a = ensure_meeting_types(db_session, brand_a.id)
    db_session.commit()
    disc_a = next(t for t in types_a if t.key == "discovery")

    appt = SalesAppointment(
        brand_sales_org_id=brand_a.id,
        meeting_type_id=disc_a.id,
        title="Brand A meeting",
        starts_at=datetime.utcnow() + timedelta(days=1),
        ends_at=datetime.utcnow() + timedelta(days=1, minutes=30),
        status="scheduled",
    )
    db_session.add(appt); db_session.commit()
    ensure_meeting(db_session, appt)

    brand_b_rows = (db_session.query(AppointmentMeeting)
                   .filter(AppointmentMeeting.brand_sales_org_id == brand_b.id)
                   .all())
    assert len(brand_b_rows) == 0, (
        "Brand B must not see Brand A's meeting rows; "
        "found %d rows" % len(brand_b_rows))
