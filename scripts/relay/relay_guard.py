#!/usr/bin/env python3
"""Relay guard: decides whether a GitHub comment is a NEW, AUTHORIZED relay
directive that Claude should execute - exactly once.

Used by .github/workflows/claude-relay.yml (decide) and
.github/workflows/relay-status-event.yml (status). Standard library only.

MARKERS (first matching line wins; case-sensitive):
    [RELAY:DIRECTIVE]          ChatGPT/Mike -> Claude   (the ONLY thing that wakes Claude)
    [RELAY:ACK]                Claude run accepted       (written by this guard)
    [RELAY:CLAUDE_STATUS]      Claude -> ChatGPT         (never wakes Claude)
    [RELAY:CHATGPT_REVIEW]     ChatGPT review note       (never wakes Claude)
    [RELAY:APPROVAL_REQUIRED]  needs Mike                (never wakes Claude)

A directive is executed only when ALL hold:
  * the comment is on the relay issue (RELAY_ISSUE, default 1);
  * its author is in RELAY_AUTHORIZED_ACTORS (default MikeSimmonsAI) AND the
    author is the repository OWNER/MEMBER/COLLABORATOR;
  * it carries [RELAY:DIRECTIVE] and no status/ack/review marker;
  * its relay_run_id (or, if absent, "c<comment id>") has no [RELAY:ACK] or
    [RELAY:CLAUDE_STATUS] yet on the issue - so a re-delivered event, an edit
    or a re-run never executes the same directive twice;
  * the branch it names is allowed (RELAY_ALLOWED_BRANCHES, default
    sci-program) - main is never a relay target.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

DIRECTIVE = "[RELAY:DIRECTIVE]"
ACK = "[RELAY:ACK]"
STATUS = "[RELAY:CLAUDE_STATUS]"
REVIEW = "[RELAY:CHATGPT_REVIEW]"
APPROVAL = "[RELAY:APPROVAL_REQUIRED]"
NON_DIRECTIVE = (ACK, STATUS, REVIEW, APPROVAL)
TERMINAL_STATUSES = ("COMPLETED", "BLOCKED", "APPROVAL_REQUIRED")
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
NEVER_BRANCHES = ("main", "master")

_FIELD = re.compile(r"^\s*([A-Z][A-Z _/]{1,40}?)\s*:\s*(.*)$")
_RUN_ID = re.compile(r"relay_run_id\s*[:=]\s*([A-Za-z0-9._-]{3,80})", re.I)


def _csv(name: str, default: str) -> List[str]:
    return [x.strip() for x in (os.environ.get(name) or default).split(",") if x.strip()]


def parse(body: str) -> Dict:
    """Marker kind, KEY: value fields and relay_run_id of one comment."""
    text = body or ""
    kind = None
    for m in (STATUS, ACK, REVIEW, APPROVAL, DIRECTIVE):
        if m in text:
            kind = m
            break
    fields: Dict[str, str] = {}
    for line in text.splitlines():
        m = _FIELD.match(line)
        if m and m.group(1).strip() not in fields:
            fields[m.group(1).strip()] = m.group(2).strip()
    rid = _RUN_ID.search(text)
    return {"kind": kind, "fields": fields, "run_id": rid.group(1) if rid else None}


def branch_of(fields: Dict[str, str], allowed: Iterable[str]) -> Optional[str]:
    """The allowed branch the directive names (BRANCH:, else ENVIRONMENT:), or None."""
    allowed = [b for b in allowed if b not in NEVER_BRANCHES]
    for key in ("BRANCH", "ENVIRONMENT"):
        val = fields.get(key, "")
        for b in allowed:
            if re.search(r"(^|[\s/;,(])%s($|[\s/;,)])" % re.escape(b), val):
                return b
    return allowed[0] if allowed else None


def handled_run_ids(comments: Iterable[Dict]) -> set:
    done = set()
    for c in comments:
        p = parse(c.get("body") or "")
        if p["kind"] in (ACK, STATUS) and p["run_id"]:
            done.add(p["run_id"])
    return done


def decide(event: Dict, comments: Iterable[Dict], *, issue: int, actors: List[str],
           branches: List[str]) -> Tuple[bool, str, Dict]:
    """(run?, reason, info). Pure - no network."""
    c = event.get("comment") or {}
    iss = event.get("issue") or {}
    if (event.get("action") or "created") != "created":
        return False, "not a new comment", {}
    if int(iss.get("number") or 0) != issue:
        return False, "not the relay issue", {}
    if "pull_request" in iss:
        return False, "pull request comment", {}
    login = ((c.get("user") or {}).get("login") or "")
    if login not in actors:
        return False, "author %r is not an authorized relay actor" % login, {}
    if (c.get("author_association") or "") not in TRUSTED_ASSOCIATIONS:
        return False, "author association %r is not trusted" % c.get("author_association"), {}
    body = c.get("body") or ""
    p = parse(body)
    if p["kind"] != DIRECTIVE or any(m in body for m in NON_DIRECTIVE):
        return False, "not a relay directive", {}
    run_id = p["run_id"] or "c%s" % c.get("id")
    if run_id in handled_run_ids(comments):
        return False, "relay_run_id %s already handled (duplicate suppressed)" % run_id, {"run_id": run_id}
    br = branch_of(p["fields"], branches)
    if not br:
        return False, "no allowed branch", {"run_id": run_id}
    return True, "ok", {"run_id": run_id, "branch": br, "project": p["fields"].get("PROJECT", ""),
                        "comment_id": c.get("id"), "parent_run_id": p["fields"].get("PARENT_RUN_ID", "")}


def status_of(body: str) -> Optional[str]:
    """STATUS value of a [RELAY:CLAUDE_STATUS] comment, upper-cased, or None."""
    p = parse(body)
    if p["kind"] != STATUS:
        return None
    raw = (p["fields"].get("STATUS") or "").upper()
    for s in ("APPROVAL_REQUIRED", "COMPLETED", "BLOCKED", "WORKING"):
        if s in raw:
            return s
    return raw or None


def ct_now() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %H:%M CT")
    except Exception:                                    # noqa: BLE001
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── GitHub plumbing (workflow use only) ─────────────────────────────────────

def _api(method: str, path: str, data: Optional[Dict] = None):
    url = "%s/repos/%s%s" % (os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                             os.environ["GITHUB_REPOSITORY"], path)
    req = urllib.request.Request(url, method=method, data=json.dumps(data).encode() if data else None,
                                 headers={"Authorization": "Bearer %s" % os.environ["GITHUB_TOKEN"],
                                          "Accept": "application/vnd.github+json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def _all_comments(issue: int) -> List[Dict]:
    out, page = [], 1
    while True:
        batch = _api("GET", "/issues/%d/comments?per_page=100&page=%d" % (issue, page))
        out.extend(batch or [])
        if not batch or len(batch) < 100:
            return out
        page += 1


def _output(**kv):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join("%s=%s\n" % (k, str(v).replace("\n", " ")) for k, v in kv.items())
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(lines)
    sys.stdout.write(lines)


def main(argv: List[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "decide"
    event = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
    issue = int(os.environ.get("RELAY_ISSUE") or 1)
    if cmd == "decide":
        actors = _csv("RELAY_AUTHORIZED_ACTORS", "MikeSimmonsAI")
        branches = _csv("RELAY_ALLOWED_BRANCHES", "sci-program")
        run, reason, info = decide(event, _all_comments(issue), issue=issue, actors=actors, branches=branches)
        print("relay guard: %s - %s" % ("RUN" if run else "SKIP", reason))
        if run:
            # Claim the run BEFORE Claude starts: a re-delivered event now sees the ACK.
            _api("POST", "/issues/%d/comments" % issue, {"body": (
                "%s relay_run_id: %s\nparent_run_id: %s\nproject: %s\nbranch: %s\ntimestamp: %s\n"
                "actor: claude-relay-action\nstatus: ACCEPTED (Claude is starting)"
                % (ACK, info["run_id"], info["parent_run_id"] or "-", info["project"] or "-",
                   info["branch"], ct_now()))})
            directive = (event.get("comment") or {}).get("body") or ""
            # Outside the checkout: the working branch is checked out after this.
            with open(os.path.join(os.environ.get("RUNNER_TEMP", "."), "relay-directive.md"),
                      "w", encoding="utf-8") as f:
                f.write(directive)
        _output(run=str(run).lower(), reason=reason, run_id=info.get("run_id", ""),
                branch=info.get("branch", ""), started_ct=ct_now())
        return 0
    if cmd == "status":
        body = (event.get("comment") or {}).get("body") or ""
        st = status_of(body)
        _output(status=st or "", terminal=str(st in TERMINAL_STATUSES).lower(),
                run_id=parse(body)["run_id"] or "")
        return 0
    print("usage: relay_guard.py decide|status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
