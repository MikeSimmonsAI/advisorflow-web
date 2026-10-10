"""Control Room relay state - READ-ONLY view of the automatic agent relay.

Reads Issue #1 comments and recent Actions runs from GitHub (GET only), then hands
them to scripts/relay/relay_state.py, the single evidence-only state machine.

FAIL CLOSED: any failure to read GitHub returns an `unavailable` payload. It never
returns a cached or guessed worker, so the page can never show a stale "Working".
This module performs no GitHub mutation.
"""
import json
import logging
import os
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_RELAY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", "relay")
if _RELAY_DIR not in sys.path:
    sys.path.insert(0, _RELAY_DIR)
import relay_state  # noqa: E402

ISSUE_NUMBER = 1
DEFAULT_REPOSITORY = "MikeSimmonsAI/advisorflow-web"
POLL_SECONDS = 20
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


class RelayUnavailable(Exception):
    pass


def _credentials():
    """(token, repo) for the GitHub GET. Accepts the names the SCI staging backend
    already uses for the Control Room (RELAY_GITHUB_READ_TOKEN / RELAY_GITHUB_REPO,
    see app/services/relay_control.py) as well as the generic ones. The token value
    is never returned to a caller of this module or logged."""
    env = os.environ.get
    token = (env("RELAY_READ_TOKEN") or env("RELAY_GITHUB_READ_TOKEN") or env("RELAY_GITHUB_WRITE_TOKEN")
             or env("GITHUB_TOKEN"))
    repo = env("RELAY_REPOSITORY") or env("RELAY_GITHUB_REPO") or env("GITHUB_REPOSITORY")
    if token and not repo:
        repo = DEFAULT_REPOSITORY   # same default as relay_control._repo()
    return token, repo


def _get(path: str):
    """GitHub GET. The only HTTP method this module ever uses."""
    token, repo = _credentials()
    if not token or not repo:
        raise RelayUnavailable("GitHub read credentials are not configured")
    url = "%s/repos/%s%s" % (os.environ.get("GITHUB_API_URL", "https://api.github.com"), repo, path)
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": "Bearer %s" % token, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"null")
    except Exception as e:  # noqa: BLE001 - every failure must fail closed
        raise RelayUnavailable("GitHub read failed: %s" % type(e).__name__) from e


def _fetch_comments() -> List[Dict]:
    out, page = [], 1
    while page <= 20:
        batch = _get("/issues/%d/comments?per_page=100&page=%d" % (ISSUE_NUMBER, page)) or []
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1
    return out


def _fetch_runs() -> List[Dict]:
    data = _get("/actions/runs?per_page=30") or {}
    return data.get("workflow_runs") or []


def unavailable(reason: str, now: Optional[datetime] = None) -> Dict:
    now = now or datetime.now(timezone.utc)
    return {"available": False, "generated_at": now.isoformat(), "reason": reason,
            "lease_minutes": relay_state.LEASE_MINUTES, "poll_seconds": POLL_SECONDS,
            "worker": {"state": "unavailable", "display": "Relay status unavailable"},
            "queued_behind": [], "history": [], "completed_today": 0, "suggested_next": "",
            # The overnight runner publishes its own heartbeat; a relay outage
            # must not hide whether IT is alive.
            "desktop_runner": desktop_runner_state(now=now)}


def get_state(fetch_comments: Callable[[], List[Dict]] = None, fetch_runs: Callable[[], List[Dict]] = None,
              now: Optional[datetime] = None) -> Dict:
    now = now or datetime.now(timezone.utc)
    try:
        comments = (fetch_comments or _fetch_comments)()
        try:
            runs = (fetch_runs or _fetch_runs)()
        except RelayUnavailable:
            runs = None   # runs are corroboration only; Actions run ID stays null
        state = relay_state.build(comments, runs, now)
    except RelayUnavailable as e:
        return unavailable(str(e), now)
    except Exception:  # noqa: BLE001
        log.exception("relay control room state failed")
        return unavailable("Relay state could not be computed", now)
    state.update(available=True, poll_seconds=POLL_SECONDS)
    state["desktop_runner"] = desktop_runner_state(now=now)
    return state


HEARTBEAT_PATH = "handoff/overnight/heartbeat.json"
HEARTBEAT_BRANCH = "overnight-status"


def _fetch_heartbeat() -> Optional[Dict]:
    """The overnight runner's own heartbeat file (GitHub contents GET)."""
    import base64
    data = _get("/contents/%s?ref=%s" % (HEARTBEAT_PATH, HEARTBEAT_BRANCH)) or {}
    raw = base64.b64decode(data.get("content") or "").decode("utf-8-sig")
    return json.loads(raw) if raw.strip() else None


def desktop_runner_state(fetch: Callable[[], Optional[Dict]] = None, now: Optional[datetime] = None) -> Dict:
    """Separate from the relay worker: a missing or unreadable heartbeat reads
    `unknown`, never `Working`, and never makes the relay itself unavailable."""
    now = now or datetime.now(timezone.utc)
    try:
        hb = (fetch or _fetch_heartbeat)()
    except Exception:  # noqa: BLE001 - fail closed to unknown
        return {"state": "unknown", "display": "Desktop runner heartbeat unavailable", "health": "unknown"}
    return relay_state.desktop_runner(hb, now)
