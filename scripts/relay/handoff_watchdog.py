#!/usr/bin/env python3
"""Detect a missed ChatGPT handoff after a trusted terminal Claude relay status.

Runs from GitHub Actions on a 15-minute schedule. It does NOT execute work and
does NOT create a new Claude directive. It only prepares a fresh relay wake-up
payload when:
  * the latest trusted terminal Claude status is at least N minutes old, and
  * ChatGPT has not posted either a child [RELAY:DIRECTIVE] or a
    [RELAY:CHATGPT_REVIEW] for that terminal run.

The workflow then updates the existing relay-signal branch/PR so the normal
ChatGPT event watcher gets another chance to handle the exact same terminal
status. Standard library only.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

from relay_guard import (
    DIRECTIVE,
    REVIEW,
    TERMINAL_STATUSES,
    _csv,
    _login,
    parse,
    status_of,
    validate_status,
)

DEFAULT_AUTOMATION = "github-actions[bot],claude[bot]"


def api(path: str):
    url = f"{os.environ.get('GITHUB_API_URL','https://api.github.com')}/repos/{os.environ['GITHUB_REPOSITORY']}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def all_comments(issue: int):
    out = []
    page = 1
    while True:
        batch = api(f"/issues/{issue}/comments?per_page=100&page={page}") or []
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1


def age_minutes(comment: dict) -> float:
    raw = comment.get("created_at") or ""
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0


def handled_after(comments, terminal_comment, run_id: str) -> bool:
    tid = int(terminal_comment.get("id") or 0)
    for c in comments:
        if int(c.get("id") or 0) <= tid:
            continue
        p = parse(c.get("body") or "")
        if p["kind"] == DIRECTIVE and p.get("parent_run_id") == run_id:
            return True
        if p["kind"] == REVIEW and p.get("run_id") == run_id:
            return True
    return False


def main() -> int:
    issue = int(os.environ.get("RELAY_ISSUE") or 1)
    threshold = int(os.environ.get("HANDOFF_STALE_MINUTES") or 10)
    branches = _csv("RELAY_ALLOWED_BRANCHES", "sci-program,wholesale-nightly,platform-dev")
    automation = _csv("RELAY_STATUS_ACTORS", DEFAULT_AUTOMATION)
    actors = _csv("RELAY_AUTHORIZED_ACTORS", "MikeSimmonsAI")
    status_actors = automation + [a for a in actors if a not in automation]
    comments = all_comments(issue)

    terminal = None
    info = None
    for c in reversed(comments):
        if status_of(c.get("body") or "") not in TERMINAL_STATUSES:
            continue
        ok, _, candidate = validate_status(
            c,
            comments,
            issue=issue,
            issue_number=issue,
            status_actors=status_actors,
            automation=automation,
            branches=branches,
        )
        if ok:
            terminal, info = c, candidate
            break

    if not terminal or not info:
        print("handoff watchdog: no trusted terminal status")
        return 0

    rid = info["run_id"]
    age = age_minutes(terminal)
    if handled_after(comments, terminal, rid):
        print(f"handoff watchdog: {rid} already handled")
        return 0
    if age < threshold:
        print(f"handoff watchdog: {rid} only {age:.1f}m old (< {threshold}m)")
        return 0

    payload = {
        "relay_run_id": rid,
        "parent_run_id": info.get("parent_run_id") or "",
        "project": info.get("project") or "",
        "status": info["status"],
        "branch": info["branch"],
        "timestamp_ct": datetime.now(timezone.utc).isoformat(),
        "issue": issue,
        "status_comment_id": info.get("comment_id"),
        "watchdog_retry": True,
        "watchdog_retry_utc": datetime.now(timezone.utc).isoformat(),
    }
    path = os.path.join(os.environ.get("RUNNER_TEMP", "."), "relay-watchdog.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write("resignal=true\n")
            f.write(f"run_id={rid}\n")
    print(f"handoff watchdog: MISSED HANDOFF {rid}, terminal age {age:.1f}m; re-signal required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
