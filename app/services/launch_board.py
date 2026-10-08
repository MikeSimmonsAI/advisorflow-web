"""Control Room launch board - dynamic project portfolio (stdlib only).

Projects are data, not code: add, prioritise, approve, archive without a deploy.
Rules enforced here (pure, no network):
  * history is append-only; archiving never deletes a project or its events
  * evidence (source/test/deployment/verification) is a recorded fact with a ref,
    never a percentage; a kind with no ref can not be marked verified
  * working status (live relay evidence) is kept apart from last completed status
  * completed tasks are counted apart from a completed product; a product is only
    "complete" when a human marked it AND all four evidence kinds are verified
  * only approved, non-archived projects are queue-eligible; nothing runs itself
"""
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

LANES = ("active", "backlog", "archived")
EVIDENCE_KINDS = ("source", "test", "deployment", "verification")
EVIDENCE_STATES = ("none", "claimed", "verified")
_LOCK = threading.Lock()


class BoardError(ValueError):
    pass


def _now(now: Optional[datetime]) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def default_path() -> str:
    return os.environ.get("LAUNCH_BOARD_PATH") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "launch_board.json")


def load(path: Optional[str] = None) -> Dict:
    """Missing file = empty board. An unreadable/corrupt file raises (fail closed)."""
    path = path or default_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"projects": [], "next_id": 1}
    except (OSError, ValueError) as e:
        raise BoardError("launch board file unreadable: %s" % type(e).__name__)
    if not (isinstance(data, dict) and isinstance(data.get("projects"), list) and isinstance(data.get("next_id"), int)):
        raise BoardError("launch board file has an unexpected shape")
    return data


def save(board: Dict, path: Optional[str] = None) -> None:
    path = path or default_path()
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(board, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _event(p: Dict, actor: str, action: str, detail: str, now) -> None:
    p["history"].append({"at": _now(now), "actor": actor or "unknown", "action": action, "detail": detail})


def _find(board: Dict, pid: int) -> Dict:
    for p in board["projects"]:
        if p["id"] == pid:
            return p
    raise BoardError("project %s not found" % pid)


def add_project(board: Dict, name: str, summary: str = "", actor: str = "", priority: Optional[int] = None,
                now: Optional[datetime] = None) -> Dict:
    name = (name or "").strip()
    if not name:
        raise BoardError("name is required")
    if any(p["name"].lower() == name.lower() for p in board["projects"]):
        raise BoardError("a project named %r already exists" % name)
    if priority is None:
        priority = 1 + max([p["priority"] for p in board["projects"]] or [0])
    elif isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
        raise BoardError("priority must be a positive integer (1 = highest)")
    evidence = dict((k, {"state": "none", "ref": None, "at": None}) for k in EVIDENCE_KINDS)
    p = {"id": board["next_id"], "name": name, "summary": (summary or "").strip(), "lane": "backlog",
         "priority": priority, "approved": False, "approved_by": None, "product_marked_complete": False,
         "tasks_completed": 0, "last_completed": None, "created_at": _now(now),
         "evidence": evidence, "history": []}
    board["next_id"] += 1
    board["projects"].append(p)
    _event(p, actor, "created", "added to backlog (unapproved)", now)
    return p


def set_lane(board: Dict, pid: int, lane: str, actor: str = "", now=None) -> Dict:
    if lane not in LANES:
        raise BoardError("lane must be one of %s" % ", ".join(LANES))
    p = _find(board, pid)
    if lane == "active" and not p["approved"]:
        raise BoardError("approve the project before making it active")
    old, p["lane"] = p["lane"], lane
    _event(p, actor, "lane", "%s -> %s" % (old, lane), now)
    return p


def set_priority(board: Dict, pid: int, priority: int, actor: str = "", now=None) -> Dict:
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 1:
        raise BoardError("priority must be a positive integer (1 = highest)")
    p = _find(board, pid)
    old, p["priority"] = p["priority"], priority
    _event(p, actor, "priority", "%s -> %s" % (old, priority), now)
    return p


def approve(board: Dict, pid: int, actor: str, now=None) -> Dict:
    if not (actor or "").strip():
        raise BoardError("approval needs a named approver")
    p = _find(board, pid)
    p["approved"], p["approved_by"] = True, actor
    _event(p, actor, "approved", "approved for queueing", now)
    return p


def revoke_approval(board: Dict, pid: int, actor: str = "", now=None) -> Dict:
    p = _find(board, pid)
    p["approved"], p["approved_by"] = False, None
    if p["lane"] == "active":
        p["lane"] = "backlog"
    _event(p, actor, "approval_revoked", "approval removed; not queue-eligible", now)
    return p


def record_evidence(board: Dict, pid: int, kind: str, state: str, ref: str = "", actor: str = "", now=None) -> Dict:
    if kind not in EVIDENCE_KINDS:
        raise BoardError("evidence kind must be one of %s" % ", ".join(EVIDENCE_KINDS))
    if state not in EVIDENCE_STATES:
        raise BoardError("evidence state must be one of %s" % ", ".join(EVIDENCE_STATES))
    ref = (ref or "").strip()
    if state != "none" and not ref:
        raise BoardError("evidence needs a reference (commit, run URL, test output id)")
    p = _find(board, pid)
    p["evidence"][kind] = {"state": state, "ref": ref or None, "at": _now(now) if state != "none" else None}
    _event(p, actor, "evidence", "%s=%s %s" % (kind, state, ref), now)
    return p


def complete_task(board: Dict, pid: int, summary: str, actor: str = "", now=None) -> Dict:
    """A finished task. This never completes the product."""
    p = _find(board, pid)
    p["tasks_completed"] += 1
    p["last_completed"] = {"at": _now(now), "summary": (summary or "").strip()}
    _event(p, actor, "task_completed", (summary or "").strip(), now)
    return p


def mark_product_complete(board: Dict, pid: int, complete: bool, actor: str = "", now=None) -> Dict:
    p = _find(board, pid)
    if complete:
        missing = [k for k in EVIDENCE_KINDS if p["evidence"][k]["state"] != "verified"]
        if missing:
            raise BoardError("cannot mark product complete; unverified evidence: %s" % ", ".join(missing))
    p["product_marked_complete"] = bool(complete)
    _event(p, actor, "product_complete", str(bool(complete)), now)
    return p


def product_state(p: Dict) -> str:
    ev = [p["evidence"][k]["state"] for k in EVIDENCE_KINDS]
    if p["product_marked_complete"] and all(s == "verified" for s in ev):
        return "complete"
    return "in_progress" if (p["tasks_completed"] or any(s != "none" for s in ev)) else "not_started"


_ROW_KEYS = ("id", "name", "summary", "lane", "priority", "approved", "approved_by",
             "tasks_completed", "last_completed", "evidence")


def view(board: Dict, live: Optional[Dict] = None) -> Dict:
    """Lanes plus queue. `live` maps project name -> working status taken from relay
    evidence; absent means "no live evidence", never a guessed Working."""
    live = live or {}
    lanes: Dict[str, List[Dict]] = dict((l, []) for l in LANES)
    for p in sorted(board["projects"], key=lambda x: (x["priority"], x["id"])):
        row = dict((k, p[k]) for k in _ROW_KEYS)
        row["working_status"] = live.get(p["name"], "no live evidence")
        row["product_state"] = product_state(p)
        row["product_complete"] = row["product_state"] == "complete"
        row["queue_eligible"] = p["approved"] and p["lane"] != "archived"
        row["history"] = list(p["history"])
        lanes[p["lane"]].append(row)
    queue = [r for r in lanes["active"] + lanes["backlog"] if r["queue_eligible"]]
    queue.sort(key=lambda r: (r["priority"], r["id"]))
    rows = [r for l in lanes.values() for r in l]
    return {"lanes": lanes,
            "queue": [{"id": r["id"], "name": r["name"], "priority": r["priority"]} for r in queue],
            "summary": {"active": len(lanes["active"]), "backlog": len(lanes["backlog"]),
                        "archived": len(lanes["archived"]),
                        "products_complete": sum(1 for r in rows if r["product_complete"]),
                        "tasks_completed": sum(r["tasks_completed"] for r in rows)}}


def mutate(fn, path: Optional[str] = None):
    """Load, apply fn(board), save atomically under a lock; a refusal saves nothing."""
    with _LOCK:
        board = load(path)
        result = fn(board)
        save(board, path)
        return result
