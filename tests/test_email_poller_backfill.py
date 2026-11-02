"""Issue #459 - one-time, bounded backfill for the cursorless advisor poller."""
from datetime import datetime, timedelta, timezone

from app.services import email_poller_service as eps

NOW = datetime(2026, 11, 2, 4, 30, tzinfo=timezone.utc)


def test_backfill_unset_or_bad_is_ignored(monkeypatch):
    monkeypatch.delenv(eps.BACKFILL_ENV, raising=False)
    assert eps.backfill_since(NOW) is None
    for bad in ("", "  ", "yesterday", "2026-11-03T00:00:00Z", "2026-10-30T00:00:00Z"):
        monkeypatch.setenv(eps.BACKFILL_ENV, bad)
        assert eps.backfill_since(NOW) is None, bad


def test_backfill_within_24h_is_used(monkeypatch):
    monkeypatch.setenv(eps.BACKFILL_ENV, "2026-11-01T23:30:00Z")
    assert eps.backfill_since(NOW) == datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc)


class _Resp:
    def __init__(self, data, code=200):
        self._d, self.status_code, self.text = data, code, ""

    def json(self):
        return self._d


def test_fetch_follows_every_page_and_uses_since(monkeypatch):
    import httpx
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, params))
        if params is not None:
            return _Resp({"value": [{"id": "a"}], "@odata.nextLink": "https://graph/next1"})
        if url.endswith("next1"):
            return _Resp({"value": [{"id": "b"}], "@odata.nextLink": "https://graph/next2"})
        return _Resp({"value": [{"id": "c"}]})
    monkeypatch.setattr(httpx, "get", fake_get)
    since = NOW - timedelta(hours=5)
    out = eps._fetch_recent_emails("tok", since_at=since)
    assert [e["id"] for e in out] == ["a", "b", "c"]
    assert since.isoformat() in calls[0][1]["$filter"]


def test_fetch_page_failure_is_a_failure_not_empty(monkeypatch):
    import httpx

    def fake_get(url, headers=None, params=None, timeout=None):
        if params is not None:
            return _Resp({"value": [{"id": "a"}], "@odata.nextLink": "https://graph/next1"})
        return _Resp({}, code=503)
    monkeypatch.setattr(httpx, "get", fake_get)
    assert eps._fetch_recent_emails("tok") is None
