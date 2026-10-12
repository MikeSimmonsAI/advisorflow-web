"""Control Room relay switch: off unless a read credential is configured."""
from app.models.models import User
from app.services import relay_control as rc
from app.services.auth_service import create_access_token, hash_password


def _god(db):
    u = User(organization_id=None, email="relayoff@evosyspro.live", password_hash=hash_password("Pass12345!"),
             full_name="G", role="god_admin", must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _clear(monkeypatch):
    for k in ("RELAY_ENABLED", "RELAY_GITHUB_READ_TOKEN", "RELAY_GITHUB_WRITE_TOKEN",
              "RELAY_READ_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)


def test_off_without_credential(monkeypatch):
    _clear(monkeypatch)
    assert rc.relay_is_on() is False


def test_on_with_read_credential(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RELAY_GITHUB_READ_TOKEN", "x" * 8)
    assert rc.relay_is_on() is True


def test_flag_forces_either_way(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RELAY_ENABLED", "1")
    assert rc.relay_is_on() is True
    monkeypatch.setenv("RELAY_GITHUB_READ_TOKEN", "x" * 8)
    monkeypatch.setenv("RELAY_ENABLED", "0")
    assert rc.relay_is_on() is False


def test_state_and_worker_never_call_github_when_off(client, db_session, monkeypatch):
    _clear(monkeypatch)
    god_headers = _god(db_session)

    def boom(*a, **k):
        raise AssertionError("GitHub must not be called while the relay is off")
    monkeypatch.setattr(rc, "fetch_comments", boom)
    monkeypatch.setattr(rc, "_gh", boom)
    for path in ("/god/relay/state", "/god/relay/worker"):
        r = client.get(path, headers=god_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["relay_off"] is True and body["available"] is False
        assert "Relay is off" in body["message"]
