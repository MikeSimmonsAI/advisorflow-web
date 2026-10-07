"""Control Room relay endpoint + service: read-only, God-only, fail-closed, no-cache."""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from app.models.models import User
from app.services import relay_control_room as rcr
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
T0 = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)
_n = [0]


def _user(db, role):
    u = User(email="relay%d@example.com" % next(_SEQ), password_hash=hash_password("x"),
             full_name="P", role=role, must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _cm(login, body, m):
    _n[0] += 1
    return {"id": _n[0], "user": {"login": login}, "author_association": "OWNER",
            "created_at": (T0 + timedelta(minutes=m)).isoformat(), "body": body}


def _active_comments(rid="a"):
    return [
        _cm("MikeSimmonsAI", "[RELAY:DIRECTIVE]\nrelay_run_id: %s\nparent_run_id: -\nPROJECT: P\nBRANCH: platform-dev" % rid, 0),
        _cm("github-actions[bot]", "[RELAY:ACK] relay_run_id: %s\nparent_run_id: -\nproject: P\nbranch: platform-dev" % rid, 1),
    ]


NOW = T0 + timedelta(minutes=5)


def test_working_when_acked_and_not_terminal():
    s = rcr.get_state(lambda: _active_comments(), lambda: [], NOW)
    assert s["available"] is True
    assert s["worker"]["display"] == "Working"
    assert s["worker"]["actions_run_id"] is None
    assert "percent" not in str(s).lower() and "eta" not in s


def test_null_head_sha_does_not_crash():
    runs = [{"id": 99, "relay_run_id": "a", "status": "in_progress", "head_sha": None,
             "updated_at": T0.isoformat()}]
    s = rcr.get_state(lambda: _active_comments(), lambda: runs, NOW)
    assert s["worker"]["actions_run_id"] == 99
    assert s["worker"]["checkpoint_sha"] == ""


def test_github_failure_fails_closed():
    def boom():
        raise rcr.RelayUnavailable("down")
    s = rcr.get_state(boom, lambda: [], NOW)
    assert s["available"] is False
    assert s["worker"]["state"] == "unavailable"
    assert s["worker"]["display"] != "Working"


def test_unexpected_error_fails_closed():
    def boom():
        raise ValueError("x")
    assert rcr.get_state(boom, lambda: [], NOW)["available"] is False


def test_runs_failure_still_reports_without_actions_id():
    def bad():
        raise rcr.RelayUnavailable("runs down")
    s = rcr.get_state(lambda: _active_comments(), bad, NOW)
    assert s["available"] is True and s["worker"]["actions_run_id"] is None


def test_missing_credentials_unavailable(monkeypatch):
    for k in ("RELAY_READ_TOKEN", "GITHUB_TOKEN", "RELAY_REPOSITORY", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(k, raising=False)
    assert rcr.get_state(now=NOW)["available"] is False


def test_endpoint_god_only_and_no_store(client, db_session, monkeypatch):
    monkeypatch.setattr(rcr, "_fetch_comments", lambda: _active_comments())
    monkeypatch.setattr(rcr, "_fetch_runs", lambda: [])
    assert client.get("/god/relay/state").status_code in (401, 403)
    assert client.get("/god/relay/state", headers=_user(db_session, "advisor")).status_code == 403
    r = client.get("/god/relay/state?_=123", headers=_user(db_session, "god_admin"))
    assert r.status_code == 200
    assert r.json()["worker"]["relay_run_id"] == "a"
    cc = r.headers["cache-control"]
    assert "no-store" in cc and "no-cache" in cc
    assert r.headers["pragma"] == "no-cache" and r.headers["expires"] == "0"


def test_endpoint_unavailable_payload_is_200_and_not_working(client, db_session, monkeypatch):
    def boom():
        raise rcr.RelayUnavailable("down")
    monkeypatch.setattr(rcr, "_fetch_comments", boom)
    r = client.get("/god/relay/state", headers=_user(db_session, "god_admin"))
    assert r.status_code == 200 and r.json()["available"] is False


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_mutation_routes(client, db_session, method):
    r = getattr(client, method)("/god/relay/state", headers=_user(db_session, "god_admin"))
    assert r.status_code == 405
