"""Control Room: read the ChatGPT <-> Claude relay and carry Mike's direction.

Issue #1 and the `relay-signal` branch stay the authoritative audit sources.
This module only (a) normalizes issue comments into plain-English events and a
current-state summary, and (b) records Mike's raw direction as a distinct
`[RELAY:MIKE_INPUT]` comment plus a wake-up signal for ChatGPT Work.

Hard rules encoded here:
  * Mike's text is NEVER a `[RELAY:DIRECTIVE]`. Only ChatGPT Work, after review,
    writes a directive, so raw input cannot wake Claude. Any relay marker inside
    the raw text is defused before it is written.
  * The GitHub write token is read from the server environment only and is never
    returned, logged or placed in an event payload.
  * No GitHub token for writing -> `setup_required`, monitoring keeps working.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

MIKE_INPUT = "[RELAY:MIKE_INPUT]"
DIRECTIVE = "[RELAY:DIRECTIVE]"
ACK = "[RELAY:ACK]"
STATUS = "[RELAY:CLAUDE_STATUS]"
REVIEW = "[RELAY:CHATGPT_REVIEW]"
APPROVAL = "[RELAY:APPROVAL_REQUIRED]"
_MARKERS = (MIKE_INPUT, DIRECTIVE, ACK, STATUS, REVIEW, APPROVAL)

MODES = {
    "next_priority": ("Next priority", 0),
    "after_current": ("After current task", 1),
    "stop_after_checkpoint": ("Stop after safe checkpoint", 2),
}
DEFAULT_MODE = "next_priority"
MAX_TEXT = 2000
DUPLICATE_WINDOW_MIN = 10
STALE_HOURS = 2.5
VALID_STATUSES = ("WORKING", "COMPLETED", "BLOCKED", "APPROVAL_REQUIRED")
ISSUE = 1

_SECRET = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAC[0-9a-f]{32}\b)")
_FIELD = re.compile(r"^\s*([A-Za-z][A-Za-z _/]{1,40}?)\s*:\s*(.*)$")


# ── parsing ──────────────────────────────────────────────────────────────────

def _fields(body: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (body or "").splitlines():
        m = _FIELD.match(line)
        if m:
            k = m.group(1).strip().upper().replace(" ", "_")
            out.setdefault(k, m.group(2).strip())
    return out


def _kind(body: str) -> Optional[str]:
    first = (body or "").lstrip().split("\n", 1)[0].strip()
    for m in _MARKERS:
        if first.startswith(m):
            return m
    return None


def _ts(c: Dict) -> Optional[datetime]:
    raw = c.get("created_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _login(c: Dict) -> str:
    return (c.get("user") or {}).get("login") or ""


def _short(text: str, n: int = 140) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def ct_str(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M CT")
    except Exception:                                    # noqa: BLE001
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def elapsed_str(start: Optional[datetime], end: Optional[datetime]) -> str:
    if not start or not end:
        return ""
    s = max(0, int((end - start).total_seconds()))
    h, rem = divmod(s, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def normalize_events(comments: List[Dict]) -> List[Dict]:
    """Issue comments -> ordered, de-duplicated, human-readable events.

    Order is by comment id (GitHub ids are monotonic); duplicates by id are
    dropped. One ACK yields two entries (GitHub accepted, Claude started).
    """
    seen, rows = set(), []
    for c in sorted((c for c in comments if c.get("id") is not None), key=lambda c: int(c["id"])):
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        rows.append(c)

    events: List[Dict] = []

    def add(c, kind, title, detail="", **extra):
        events.append({
            "key": f"{c['id']}:{kind}", "kind": kind, "title": title, "detail": detail,
            "at": (_ts(c).isoformat() if _ts(c) else None), "at_ct": ct_str(_ts(c)),
            "run_id": extra.pop("run_id", None), "status": extra.pop("status", None),
            "technical": {"comment_id": c["id"], "author": _login(c), "url": c.get("html_url"),
                          **extra},
        })

    for c in rows:
        body = c.get("body") or ""
        k = _kind(body)
        f = _fields(body)
        run = f.get("RELAY_RUN_ID")
        if k == MIKE_INPUT:
            add(c, "mike_direction", "Mike gave direction", _short(f.get("DIRECTION", "")),
                run_id=run, mode=f.get("MODE", DEFAULT_MODE), input_id=f.get("INPUT_ID"))
        elif k == DIRECTIVE:
            nxt = f.get("PARENT_RUN_ID", "-") not in ("", "-")
            proj = f.get("PROJECT", "")
            add(c, "next_directive" if nxt else "chatgpt_directive",
                "ChatGPT sent Claude the next task" if nxt else "ChatGPT sent Claude a directive",
                _short(f.get("OBJECTIVE", "") or proj), run_id=run, project=proj,
                parent_run_id=f.get("PARENT_RUN_ID"), branch=f.get("BRANCH"))
        elif k == ACK:
            add(c, "github_accepted", "GitHub accepted the task", "", run_id=run, branch=f.get("BRANCH"))
            add(c, "claude_started", "Claude started", "", run_id=run, branch=f.get("BRANCH"))
        elif k == STATUS:
            st = (re.split(r"[\s(,;|]+", f.get("STATUS", "").upper())[0] if f.get("STATUS") else "")
            if st not in VALID_STATUSES:
                continue
            detail = _short(f.get("COMPLETED") or f.get("BLOCKERS") or "")
            if st == "WORKING":
                add(c, "claude_progress", "Claude is making progress", detail, run_id=run, status=st)
            elif st == "APPROVAL_REQUIRED":
                add(c, "approval_gate", "Mike approval needed",
                    _short(f.get("APPROVAL_REQUIRED") or detail), run_id=run, status=st,
                    risk=f.get("PRODUCTION_IMPACT"), why=f.get("BLOCKERS"))
            else:
                word = "finished" if st == "COMPLETED" else "hit a blocker"
                add(c, "claude_complete", f"Claude {word}", detail, run_id=run, status=st,
                    project=f.get("PROJECT"), branch=f.get("BRANCH"), next_action=f.get("NEXT_RECOMMENDED_ACTION"))
        elif k == REVIEW:
            add(c, "chatgpt_review", "ChatGPT reviewed", _short(re.sub(r"^\[RELAY:[A-Z_]+\]\s*", "", body)),
                run_id=run, input_id=f.get("INPUT_ID"), parent_run_id=f.get("PARENT_RUN_ID"))
        elif k == APPROVAL:
            add(c, "approval_gate", "Mike approval needed",
                _short(f.get("DECISION") or f.get("APPROVAL_REQUIRED") or ""), run_id=run,
                risk=f.get("RISK") or f.get("PRODUCTION_IMPACT"), why=f.get("WHY") or f.get("REASON"))
    return events


# ── state ────────────────────────────────────────────────────────────────────

def _find(events, *kinds):
    return [e for e in events if e["kind"] in kinds]


def open_approval(events: List[Dict]) -> Optional[Dict]:
    """The newest approval gate not answered by a later Mike input or directive."""
    gates = _find(events, "approval_gate")
    if not gates:
        return None
    last = gates[-1]
    idx = events.index(last)
    if any(e["kind"] in ("mike_direction", "chatgpt_directive", "next_directive")
           for e in events[idx + 1:]):
        return None
    t = last["technical"]
    return {
        "decision": last["detail"] or "A decision is needed from Mike.",
        "why": t.get("why") or "The relay stopped at an approval gate and will not continue on its own.",
        "risk": t.get("risk") or "Not stated. Nothing proceeds until you answer.",
        "run_id": last.get("run_id"), "at_ct": last["at_ct"], "comment_id": t.get("comment_id"),
        "choices": [
            {"id": "approve", "label": "Approve", "text": "APPROVE: " + (last["detail"] or "the pending decision")},
            {"id": "decline", "label": "Decline", "text": "DECLINE: " + (last["detail"] or "the pending decision")},
        ],
    }


def pending_inputs(events: List[Dict]) -> List[Dict]:
    """Mike inputs ChatGPT has not reviewed yet, ordered by mode rank then arrival."""
    reviewed = {e["technical"].get("input_id") for e in _find(events, "chatgpt_review")
                if e["technical"].get("input_id")}
    last_review_idx = max((i for i, e in enumerate(events) if e["kind"] == "chatgpt_review"), default=-1)
    out = []
    for i, e in enumerate(events):
        if e["kind"] != "mike_direction":
            continue
        iid = e["technical"].get("input_id")
        if iid in reviewed:
            continue
        # A review that names no input still means ChatGPT has seen everything before it.
        if i < last_review_idx and not any(
                r["technical"].get("input_id") for r in _find(events[i:], "chatgpt_review")):
            continue
        mode = e["technical"].get("mode") if e["technical"].get("mode") in MODES else DEFAULT_MODE
        out.append({"input_id": iid, "text": e["detail"], "mode": mode,
                    "mode_label": MODES[mode][0], "at_ct": e["at_ct"], "_i": i})
    out.sort(key=lambda q: (MODES[q["mode"]][1], q["_i"]))
    for q in out:
        q.pop("_i")
    return out


def compute_state(events: List[Dict], now: Optional[datetime] = None) -> Dict:
    now = now or datetime.now(timezone.utc)

    def parse(e):
        return datetime.fromisoformat(e["at"]) if e and e.get("at") else None

    directives = _find(events, "chatgpt_directive", "next_directive")
    gate = open_approval(events)
    state = {"actor": "Idle", "status": "Idle", "task": "Nothing is running.", "project": "",
             "branch": "", "started_ct": "", "elapsed": "", "last_update_ct": "",
             "next_action": "Waiting for the next direction.", "run_id": None}
    if events:
        state["last_update_ct"] = events[-1]["at_ct"]
    if not directives:
        return {**state, "needs_mike": gate}

    d = directives[-1]
    run = d.get("run_id")
    after = events[events.index(d) + 1:]
    mine = [e for e in after if e.get("run_id") == run]
    state.update(project=d["technical"].get("project") or "", branch=d["technical"].get("branch") or "",
                 task=d["detail"] or "Current directive", run_id=run)
    ack = next((e for e in mine if e["kind"] == "claude_started"), None)
    started = parse(ack) or parse(d)
    state["started_ct"] = ct_str(started)
    terminal = next((e for e in mine if e["kind"] in ("claude_complete", "approval_gate")), None)
    reviewed_after = terminal and any(e["kind"] == "chatgpt_review"
                                      for e in events[events.index(terminal) + 1:])
    newer_input = pending_inputs(events)

    if terminal is None:
        age = (now - started).total_seconds() / 3600 if started else 0
        state["elapsed"] = elapsed_str(started, now)
        if age > STALE_HOURS and ack:
            state.update(actor="Claude", status="Blocked",
                         next_action="No final report after 2.5 hours. ChatGPT will treat this run as blocked.")
        elif ack:
            state.update(actor="Claude", status="Working",
                         next_action="Claude will report back when this task is done.")
        else:
            state.update(actor="Claude", status="Working",
                         next_action="GitHub is waking Claude for this task.")
    else:
        state["elapsed"] = elapsed_str(started, parse(terminal))
        if terminal["kind"] == "approval_gate":
            state.update(actor="Waiting on Mike", status="Approval Needed",
                         next_action="Mike must approve or decline. Nothing continues until then.")
        else:
            st = terminal["status"]
            state["status"] = "Complete" if st == "COMPLETED" else "Blocked"
            if reviewed_after:
                state.update(actor="Idle", next_action="ChatGPT has reviewed this. Waiting for the next task.")
            else:
                state.update(actor="ChatGPT", next_action="ChatGPT is reviewing the result and choosing the next step.")
    if gate:
        state.update(actor="Waiting on Mike", status="Approval Needed",
                     next_action="Mike must approve or decline. Nothing continues until then.")
    elif newer_input and state["actor"] == "Idle":
        state.update(actor="ChatGPT", status="Working",
                     next_action="ChatGPT is reading Mike's direction and will decide what Claude does next.")
    return {**state, "needs_mike": gate}


def build_queue(events: List[Dict], state: Dict, now: Optional[datetime] = None) -> Dict:
    now = now or datetime.now(timezone.utc)
    today = ct_str(now)[:10]
    done = [{"title": e["detail"] or e["title"], "at_ct": e["at_ct"]}
            for e in _find(events, "claude_complete") if e["status"] == "COMPLETED" and e["at_ct"][:10] == today]
    attention = [{"title": e["title"] + (": " + e["detail"] if e["detail"] else ""), "at_ct": e["at_ct"],
                  "kind": "approval" if e["kind"] == "approval_gate" else "blocked"}
                 for e in events if (e["kind"] == "approval_gate" or (e["kind"] == "claude_complete"
                                                                      and e["status"] == "BLOCKED"))][-5:]
    if state.get("needs_mike") is None:
        attention = [a for a in attention if a["kind"] != "approval"]
    return {"current": state["task"] if state["actor"] != "Idle" else None,
            "queued": pending_inputs(events), "completed_today": done, "attention": attention}


def notifications(events: List[Dict], state: Dict) -> List[Dict]:
    """Plain-English lines for the notification strip (newest last, max 6)."""
    out = []
    for e in events:
        proj = e["technical"].get("project") or ""
        if e["kind"] == "claude_complete":
            verb = "finished" if e["status"] == "COMPLETED" else "hit a blocker on"
            out.append(f"Claude {verb}: {e['detail'] or proj or 'its task'}. ChatGPT is reviewing the next step.")
        elif e["kind"] in ("chatgpt_directive", "next_directive"):
            out.append(f"ChatGPT sent Claude the next task: {e['detail']}.")
        elif e["kind"] == "approval_gate":
            out.append(f"Mike approval needed: {e['detail'] or 'see the Needs Mike panel'}.")
        elif e["kind"] == "mike_direction":
            out.append(f"Mike gave direction: {e['detail']}. ChatGPT will review it first.")
    return [{"text": t} for t in out[-6:]]


def snapshot(comments: List[Dict], now: Optional[datetime] = None) -> Dict:
    events = normalize_events(comments)
    state = compute_state(events, now)
    return {"state": state, "needs_mike": state.get("needs_mike"), "events": events,
            "queue": build_queue(events, state, now), "notifications": notifications(events, state)}


# ── GitHub I/O (server side only) ───────────────────────────────────────────

def _repo() -> str:
    return os.environ.get("RELAY_GITHUB_REPO", "MikeSimmonsAI/advisorflow-web")


def read_token() -> Optional[str]:
    return os.environ.get("RELAY_GITHUB_READ_TOKEN") or os.environ.get("RELAY_GITHUB_WRITE_TOKEN") or None


def write_token() -> Optional[str]:
    return os.environ.get("RELAY_GITHUB_WRITE_TOKEN") or None


def _gh(method: str, path: str, token: Optional[str], data: Optional[Dict] = None):
    req = urllib.request.Request(
        "https://api.github.com/repos/%s%s" % (_repo(), path),
        data=json.dumps(data).encode() if data is not None else None, method=method,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "advisorflow-control-room",
                 **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=10) as r:        # noqa: S310 - fixed https host
        return json.loads(r.read().decode() or "null")


_cache: Dict = {"at": 0.0, "comments": None}
_lock = threading.Lock()
CACHE_SECONDS = 15


def fetch_comments(force: bool = False) -> List[Dict]:
    """All comments on issue #1, cached briefly so polling stays cheap."""
    with _lock:
        if not force and _cache["comments"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["comments"]
    out, page = [], 1
    while page <= 20:
        batch = _gh("GET", f"/issues/{ISSUE}/comments?per_page=100&page={page}", read_token())
        out += batch or []
        if not batch or len(batch) < 100:
            break
        page += 1
    with _lock:
        _cache.update(at=time.time(), comments=out)
    return out


def invalidate_cache() -> None:
    with _lock:
        _cache.update(at=0.0, comments=None)


# ── Mike's direction ────────────────────────────────────────────────────────

class DirectionError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def defuse(text: str) -> str:
    """Remove any relay marker from raw text so it can never act as one."""
    out = text
    for m in _MARKERS:
        out = re.sub(re.escape(m), "[marker removed]", out, flags=re.I)
    return re.sub(r"\[RELAY:[A-Za-z_]+\]", "[marker removed]", out, flags=re.I)


def input_fingerprint(text: str, mode: str) -> str:
    return hashlib.sha256(f"{mode}|{' '.join(text.lower().split())}".encode()).hexdigest()[:12]


def validate_direction(text: str, mode: str) -> str:
    clean = (text or "").strip()
    if not clean:
        raise DirectionError("empty", "Type what you want first.")
    if len(clean) > MAX_TEXT:
        raise DirectionError("too_long", f"Keep it under {MAX_TEXT} characters.")
    if mode not in MODES:
        raise DirectionError("bad_mode", "Pick one of the direction modes.")
    if _SECRET.search(clean):
        raise DirectionError("secret", "That looks like a password or key. Remove it; never paste secrets here.")
    return defuse(clean)


def is_duplicate(events: List[Dict], fingerprint: str, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=DUPLICATE_WINDOW_MIN)
    for e in _find(events, "mike_direction"):
        iid = e["technical"].get("input_id") or ""
        if iid.endswith("-" + fingerprint) and e.get("at") and datetime.fromisoformat(e["at"]) >= cutoff:
            return True
    return False


def build_input_comment(input_id: str, text: str, mode: str, who: str, ct: str) -> str:
    """The audit record. Deliberately has no [RELAY:DIRECTIVE] marker and no
    BRANCH field, so the Claude workflow can never accept it."""
    return "\n".join([
        MIKE_INPUT,
        f"input_id: {input_id}",
        f"MODE: {mode}",
        f"FROM: {who}",
        f"TIME CT: {ct}",
        "FOR: ChatGPT Work (review first; this is NOT a directive and does not wake Claude)",
        "DIRECTION: " + text.replace("\n", " "),
    ])


def signal_payload(input_id: str, mode: str, ct: str) -> Dict:
    """Wake-up for ChatGPT Work only. No raw text, no secrets, no customer data."""
    return {"relay_run_id": input_id, "parent_run_id": "", "project": "Control Room",
            "status": "MIKE_INPUT", "kind": "mike_input", "mode": mode, "branch": "sci-program",
            "timestamp_ct": ct, "issue": ISSUE, "status_comment_id": None}


def _write_signal(payload: Dict, token: str) -> None:
    path = "/contents/.relay/chatgpt-wakeup.json"
    sha = None
    try:
        sha = _gh("GET", path + "?ref=relay-signal", token).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    body = {"message": "relay signal: " + payload["relay_run_id"], "branch": "relay-signal",
            "content": b64encode(json.dumps(payload, indent=1).encode()).decode()}
    if sha:
        body["sha"] = sha
    _gh("PUT", path, token, body)


def submit_direction(text: str, mode: str, who: str, *, events: Optional[List[Dict]] = None,
                     now: Optional[datetime] = None) -> Dict:
    """Validate, record in issue #1, then signal ChatGPT. Never signals Claude."""
    clean = validate_direction(text, mode)
    token = write_token()
    if not token:
        raise DirectionError("setup_required",
                             "SETUP REQUIRED: the server has no GitHub write credential yet.", 503)
    if events is None:
        events = normalize_events(fetch_comments(force=True))
    fp = input_fingerprint(clean, mode)
    if is_duplicate(events, fp, now):
        raise DirectionError("duplicate", "You already sent this. ChatGPT has it.", 409)
    now = now or datetime.now(timezone.utc)
    input_id = "mike-%s-%s" % (now.astimezone(timezone.utc).strftime("%Y%m%d%H%M"), fp)
    ct = ct_str(now)
    try:
        _gh("POST", f"/issues/{ISSUE}/comments", token,
            {"body": build_input_comment(input_id, clean, mode, who, ct)})
    except Exception as e:                                # noqa: BLE001
        log.warning("relay direction: audit write failed: %s", type(e).__name__)
        raise DirectionError("github_error", "GitHub did not accept the note. Nothing was sent.", 502)
    signalled = True
    try:
        _write_signal(signal_payload(input_id, mode, ct), token)
    except Exception as e:                                # noqa: BLE001
        signalled = False
        log.warning("relay direction: wake-up signal failed: %s", type(e).__name__)
    invalidate_cache()
    return {"input_id": input_id, "recorded": True, "chatgpt_signalled": signalled, "mode": mode}
