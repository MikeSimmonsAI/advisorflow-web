#!/usr/bin/env python3
"""Detect active relay runs that have stopped reporting progress.

Read-only by design: no automatic restart, no duplicate directives.
Emit GitHub Actions warning and fail the check when a run is stale.
"""
import os
import sys
from datetime import datetime, timezone
from handoff_watchdog import all_comments
from relay_guard import ACK, STATUS, TERMINAL_STATUSES, _csv, _login, parse, status_of

def stale_runs(comments, now, threshold_minutes=20, automation=None, status_actors=None):
    automation = automation or ["github-actions[bot]", "claude[bot]"]
    status_actors = status_actors or automation
    runs = {}
    for c in sorted(comments, key=lambda x: int(x.get("id") or 0)):
        p = parse(c.get("body") or "")
        rid = p["run_id"]
        if not rid:
            continue
        author = _login(c)
        if p["kind"] == ACK and author in automation:
            runs.setdefault(rid, {"branch": p["branch"], "last": c.get("created_at"), "terminal": False})
        elif rid in runs and p["kind"] == STATUS and author in status_actors and p["branch"] == runs[rid]["branch"]:
            runs[rid]["last"] = c.get("created_at")
            if status_of(c.get("body") or "") in TERMINAL_STATUSES:
                runs[rid]["terminal"] = True
    result = []
    for rid, item in runs.items():
        if item["terminal"] or not item["last"]:
            continue
        age = (now - datetime.fromisoformat(item["last"].replace("Z", "+00:00"))).total_seconds() / 60
        if age >= threshold_minutes:
            result.append((rid, item["branch"], round(age, 1)))
    return result

def main():
    issue = int(os.getenv("RELAY_ISSUE", "1"))
    threshold = int(os.getenv("ACTIVE_STALL_MINUTES", "20"))
    automation = _csv("RELAY_STATUS_ACTORS", "github-actions[bot],claude[bot]")
    actors = _csv("RELAY_AUTHORIZED_ACTORS", "MikeSimmonsAI")
    stalled = stale_runs(all_comments(issue), datetime.now(timezone.utc), threshold, automation, automation + actors)
    if not stalled:
        print("Active relay watchdog: no stale acknowledged runs")
        return 0
    for rid, branch, age in stalled:
        print(f"::warning title=Relay stalled::{rid} on {branch} has no heartbeat for {age} minutes")
    print("::error::Active relay stall detected; investigate before resuming. No automatic duplicate run created.")
    return 1

if __name__ == "__main__":
    sys.exit(main())
