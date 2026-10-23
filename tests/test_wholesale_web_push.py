"""Phone alerts for the Wholesale field team (web push).

The server makes its own key pair, the field app's bell alerts ride along as
phone alerts with a generic title, and a field login may turn them on."""
import pytest

from app.models.models import NotificationType
from app.models.push_models import PushEvent, WebPushVapidKey
from app.services import web_push_service as wps


@pytest.fixture(autouse=True)
def _mount_router(client):
    from app.main import app
    if not any(getattr(r, "path", "").startswith("/push/") for r in app.routes):
        from app.routers.push_router import router
        app.include_router(router)
    wps._AUTO_CACHE.clear()
    yield
    wps._AUTO_CACHE.clear()


class _Shared:
    """app.deps.SessionLocal stand-in: the test's own session, never closed."""
    def __init__(self, db):
        self.db = db

    def __call__(self):
        return self

    def __getattr__(self, k):
        return getattr(self.db, k)

    def close(self):
        pass


@pytest.fixture()
def auto_keys(monkeypatch, db_session):
    for k in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WEB_PUSH_AUTO_KEYS", "1")
    import app.deps
    monkeypatch.setattr(app.deps, "SessionLocal", _Shared(db_session))
    return db_session


def test_made_keys_are_ones_a_push_service_accepts():
    from py_vapid import Vapid
    pair = wps._make_keypair()
    assert "=" not in pair["public_key"] and "=" not in pair["private_key"]
    v = Vapid.from_string(private_key=pair["private_key"])
    headers = v.sign({"aud": "https://fcm.googleapis.com", "sub": wps.DEFAULT_SUBJECT})
    assert headers.get("Authorization", "").startswith("vapid ")


def test_the_server_makes_its_keys_once_and_keeps_them(auto_keys):
    first = wps.vapid_settings()
    assert first["public_key"] and first["private_key"] and first["subject"] == wps.DEFAULT_SUBJECT
    row = auto_keys.query(WebPushVapidKey).one()
    assert row.private_key_enc != first["private_key"]        # stored encrypted
    wps._AUTO_CACHE.clear()
    again = wps.vapid_settings()
    assert again["public_key"] == first["public_key"]
    assert again["private_key"] == first["private_key"]
    assert auto_keys.query(WebPushVapidKey).count() == 1


def test_keys_set_by_hand_win(auto_keys, monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BHandSet")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "hand-set")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:ops@example.test")
    assert wps.vapid_settings()["public_key"] == "BHandSet"
    assert auto_keys.query(WebPushVapidKey).count() == 0


def test_config_reports_configured_with_made_keys(client, auto_keys):
    r = client.get("/push/config")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True and body["public_key"]
    assert "private" not in str(body).lower()


def test_a_field_alert_goes_to_the_phone_without_the_address(db_session, sample_advisor):
    from app.services import wholesale_notify
    n = wholesale_notify.notify(
        db_session, sample_advisor.organization_id, kind=NotificationType.WHOLESALE_APPOINTMENT,
        message="New appointment: 123 Main St, Tue 2:00 PM", link="/wholesale/appointments",
        assignee_id=sample_advisor.id)
    assert n >= 1
    db_session.flush()     # queued in the caller's transaction
    evs = db_session.query(PushEvent).filter(PushEvent.target_user_id == sample_advisor.id).all()
    assert len(evs) == 1
    ev = evs[0]
    assert ev.title == "Appointment update"
    assert ev.url == "/wholesale/appointments"
    payload = wps.payload_for(ev)
    assert "Main" not in str(payload) and "123" not in str(payload)


def test_an_odd_link_falls_back_to_the_appointments_screen(db_session, sample_advisor):
    from app.services import wholesale_notify
    wholesale_notify.notify(
        db_session, sample_advisor.organization_id, kind=NotificationType.WHOLESALE_OFFER,
        message="Offer approved", link="https://evil.example/x", assignee_id=sample_advisor.id)
    db_session.flush()
    ev = db_session.query(PushEvent).filter(PushEvent.target_user_id == sample_advisor.id).one()
    assert ev.title == "Offer update" and ev.url == "/wholesale/appointments"


def test_a_field_login_can_turn_phone_alerts_on(client, admin_auth_headers, auth_headers, sample_advisor):
    r = client.post("/wholesale/team/members", headers=admin_auth_headers,
                    json={"user_id": sample_advisor.id, "role": "field_specialist", "confirm_lock": True})
    assert r.status_code in (200, 201), r.text[:300]
    assert client.get("/push/config", headers=auth_headers).status_code == 200
    assert client.get("/push/status", headers=auth_headers).status_code == 200
    # still fenced everywhere else
    assert client.get("/leads/", headers=auth_headers).status_code == 403
