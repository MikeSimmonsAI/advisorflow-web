#!/usr/bin/env python3
"""Relay state - the single authoritative, evidence-only view of Issue #1 for the Control Room.

Standard library only; pure functions covered by tests/test_relay_state.py.
Reuses relay_guard's parser and trust rules so the display can never trust a
comment the guard would reject.

    python3 scripts/relay/relay_state.py comments.json [runs.json]   # -> JSON on stdout

`comments`: Issue #1 comments (id, created_at, user.login, body).
`runs`   : optional GitHub Actions runs (id, status, conclusion, created_at,
           updated_at, head_branch, head_sha, and optional relay_run_id).

RULES
  * Working is claimed ONLY for an ACKed run with no trusted terminal status, not
    superseded by a child directive, and whose Actions run (if known) is not
    terminal. A directive comment alone is at most "Queued".
  * Terminal / superseded runs go to history and never show as Working.
  * Stages are evidence-backed; there is no percentage and no ETA.
  * Lease is 30 minutes: beyond it without a terminal status -> HUNG.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relay_guard as g  # noqa: E402

LEASE_MINUTES = 30
STAGES = ("Queued", "Accepted", "Working", "Commit/Push", "Deploy/Verify", "Reviewed/Complete")
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def _ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _mins(a: Optional[datetime], b: Optional[datetime]) -> Optional[int]:
    return None if a is None or b is None else max(0, int((b - a).total_seconds() // 60))


def _f(fields: Dict[str, str], *keys: str) -> str:
    for k in keys:
        v = (fields.get(k) or "").strip()
        if v and v != "-":
            return v
    return ""


def _sha(text: str) -> str:
    m = _SHA.search(text or "")
    return m.group(0) if m else ""


def build(comments: Iterable[Dict], runs: Optional[Iterable[Dict]], now: datetime, *,
          actors: Optional[List[str]] = None, branches: Optional[List[str]] = None,
          automation: Optional[List[str]] = None) -> Dict:
    actors = actors or ["MikeSimmonsAI"]
    branches = branches or ["sci-program", "wholesale-nightly", "platform-dev"]
    automation = automation or g.DEFAULT_AUTOMATION.split(",")
    status_actors = automation + [a for a in actors if a not in automation]
    now = now.astimezone(timezone.utc)
    cs = sorted(comments, key=lambda c: int(c.get("id") or 0))
    ackmap = g.acks(cs, automation)

    runs_by_rid: Dict[str, Dict] = {}
    for r in runs or []:
        if r.get("relay_run_id"):
            runs_by_rid[r["relay_run_id"]] = r

    # Per-run record from trusted comments only.
    rec: Dict[str, Dict] = {}

    def get(rid: str) -> Dict:
        return rec.setdefault(rid, {"run_id": rid, "directive": None, "ack": None, "updates": [],
                                    "terminal": None, "order": 0})

    for c in cs:
        p, login = g.parse(c.get("body") or ""), g._login(c)
        rid, cid = p["run_id"], int(c.get("id") or 0)
        if not rid:
            continue
        if p["kind"] == g.DIRECTIVE and login in actors and (c.get("author_association") or "OWNER") in g.TRUSTED_ASSOCIATIONS \
                and g.allowed_branch(p["branch"], branches):
            r = get(rid)
            r["directive"] = r["directive"] or c
            r["order"] = r["order"] or cid
        elif p["kind"] == g.ACK and login in automation and rid in ackmap and r_first_ack(rec, rid, c):
            get(rid)["ack"] = c
            get(rid)["order"] = get(rid)["order"] or cid
        elif p["kind"] == g.STATUS and login in status_actors and rid in ackmap:
            st = g.status_of(c.get("body") or "")
            ack = ackmap[rid]
            if st is None or p["branch"] != ack.get("branch"):
                continue
            r = get(rid)
            if st in g.TERMINAL_STATUSES:
                r["terminal"] = r["terminal"] or (st, c)       # first terminal wins, like the guard
            elif r["terminal"] is None:
                r["updates"].append(c)

    # Lineage: a run is superseded when any run names it as parent.
    children: Dict[str, str] = {}
    for rid, a in ackmap.items():
        if a.get("parent_run_id") and a["parent_run_id"] not in children:
            children[a["parent_run_id"]] = rid
    for rid, r in rec.items():
        if r["directive"] and rid not in ackmap:
            par = g.parse(r["directive"].get("body") or "")["parent_run_id"]
            if par:
                children.setdefault(par, rid)

    def card(r: Dict) -> Dict:
        rid = r["run_id"]
        ack, term = r["ack"], r["terminal"]
        src = (term[1] if term else (r["updates"][-1] if r["updates"] else (ack or r["directive"])))
        p = g.parse(src.get("body") or "")
        fields = p["fields"]
        lastp = g.parse((r["updates"][-1] if r["updates"] else src).get("body") or "")
        started = _ts((ack or r["directive"] or {}).get("created_at"))
        ended = _ts(term[1].get("created_at")) if term else None
        last_update = _ts(src.get("created_at"))
        run = runs_by_rid.get(rid)
        commits = _f(fields, "COMMITS")
        sha = _sha(commits) or (run or {}).get("head_sha", "")[:7]
        sha_age = _mins(_ts((run or {}).get("updated_at")), now) if sha and run else None
        status = term[0] if term else None
        out = {
            "relay_run_id": rid, "project": _f(fields, "PROJECT") or (ackmap.get(rid) or {}).get("project", ""),
            "actor": (g._login(ack) if ack else "") or "claude-relay-action",
            "branch": p["branch"] or (ackmap.get(rid) or {}).get("branch") or "",
            "actions_run_id": (run or {}).get("id"), "started_at": (ack or r["directive"] or {}).get("created_at"),
            "last_update_at": src.get("created_at"), "checkpoint_sha": sha, "checkpoint_age_min": sha_age,
            "parent_run_id": (ackmap.get(rid) or {}).get("parent_run_id") or "",
            "elapsed_min": _mins(started, ended or now), "status": status or "",
            "since_update_min": _mins(last_update, now),
            "result_summary": _f(fields, "COMPLETED"),
            "blocked_reason": _f(fields, "BLOCKERS", "APPROVAL REQUIRED") if status in ("BLOCKED", "APPROVAL_REQUIRED") else "",
            "next_action": _f(fields, "NEXT RECOMMENDED ACTION"),
        }
        return out

    def stage(r: Dict, sha: str) -> str:
        if r["terminal"]:
            return "Reviewed/Complete"
        if not r["ack"]:
            return "Queued"
        last = g.parse((r["updates"][-1] if r["updates"] else r["ack"]).get("body") or "")["fields"]
        if _f(last, "LIVE VERIFICATION"):
            return "Deploy/Verify"
        if sha or _f(last, "COMMITS"):
            return "Commit/Push"
        return "Working" if r["updates"] or runs_by_rid.get(r["run_id"], {}).get("status") == "in_progress" else "Accepted"

    active, history = [], []
    for rid, r in rec.items():
        if not (r["ack"] or r["directive"]):
            continue
        cd = card(r)
        run = runs_by_rid.get(rid)
        if r["terminal"]:
            cd.update(state="terminal", display=cd["status"])
            history.append(cd)
        elif rid in children:
            cd.update(state="superseded", display="SUPERSEDED", superseded_by=children[rid])
            history.append(cd)
        elif run and run.get("status") == "completed":
            # Actions finished but relay never reported terminal: mismatch, NOT working.
            cd.update(state="mismatch", display="RELAY_STATE_MISMATCH",
                      health="Actions run is %s but no trusted terminal relay status exists"
                             % (run.get("conclusion") or "completed"))
            history.append(cd)
        elif not r["ack"]:
            cd.update(state="queued", display="Queued", stage="Queued")
            active.append((r, cd))
        else:
            cd.update(state="active", display="Working")
            cd["stage"] = stage(r, cd["checkpoint_sha"])
            el = cd["elapsed_min"]
            if el is not None and el > LEASE_MINUTES:
                cd.update(health="STALE/HUNG", display="STALE/HUNG",
                          health_detail="in progress %d min, past the %d-minute lease, no terminal status"
                                        % (el, LEASE_MINUTES))
            else:
                cd["health"] = "ok"
            active.append((r, cd))

    active.sort(key=lambda t: t[0]["order"], reverse=True)
    history.sort(key=lambda c: c["last_update_at"] or "", reverse=True)
    primary = active[0][1] if active else None
    queued_behind = [c for _, c in active[1:]]
    # Newest trusted lineage drives Suggested Next: the newest run with a next action.
    newest = max(rec.values(), key=lambda r: r["order"], default=None)
    suggested = ""
    if primary:
        suggested = primary.get("next_action") or ""
    elif newest and newest["terminal"]:
        suggested = _f(g.parse(newest["terminal"][1].get("body") or "")["fields"], "NEXT RECOMMENDED ACTION")
    day = now.strftime("%Y-%m-%d")
    done = [c for c in history if c["state"] == "terminal" and c["status"] == "COMPLETED"
            and (c["last_update_at"] or "").startswith(day)]
    return {"generated_at": now.isoformat(), "lease_minutes": LEASE_MINUTES, "stages": list(STAGES),
            "worker": primary or {"state": "idle", "display": "Idle - no active Claude worker"},
            "queued_behind": queued_behind, "history": history, "completed_today": len(done),
            "suggested_next": suggested}


def r_first_ack(rec: Dict, rid: str, c: Dict) -> bool:
    return not (rec.get(rid) and rec[rid]["ack"])


if __name__ == "__main__":
    import json
    cm = json.load(open(sys.argv[1], encoding="utf-8"))
    rn = json.load(open(sys.argv[2], encoding="utf-8")) if len(sys.argv) > 2 else None
    print(json.dumps(build(cm, rn, datetime.now(timezone.utc)), indent=2))
