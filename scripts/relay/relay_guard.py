#!/usr/bin/env python3
"""Relay guard - the ONLY gate between GitHub comments and automatic action.

Used by .github/workflows/claude-relay.yml and .github/workflows/relay-status-event.yml.
Standard library only; every decision is a pure function covered by
tests/test_relay_guard.py, and the CLI is thin GitHub plumbing around them.

MARKERS
    [RELAY:DIRECTIVE]          ChatGPT/Mike -> Claude. The ONLY marker that wakes Claude.
    [RELAY:ACK]                posted by the guard (automation identity) when it accepts a run
    [RELAY:CLAUDE_STATUS]      Claude -> ChatGPT. Never wakes Claude.
    [RELAY:CHATGPT_REVIEW]     ChatGPT note. Never wakes anything.
    [RELAY:APPROVAL_REQUIRED]  needs Mike. Never wakes anything.

DIRECTIVE accepted only when: relay issue; new comment; author in
RELAY_AUTHORIZED_ACTORS with OWNER/MEMBER/COLLABORATOR association; carries the
directive marker and no other marker; names BRANCH: in RELAY_ALLOWED_BRANCHES
(main/master are refused even if listed); its relay_run_id has never been
ACKed or reported; and its parent_run_id has not already produced a child
directive (one review -> at most one next directive).

CLAUDE_STATUS trusted only when: relay issue; author is an automation identity
(RELAY_STATUS_ACTORS) or MikeSimmonsAI; names a relay_run_id that has a prior
ACK posted by an automation identity; its BRANCH matches the ACKed branch and
is allowed; STATUS is WORKING|COMPLETED|BLOCKED|APPROVAL_REQUIRED; and - for a
terminal status - no earlier trusted terminal status exists for that run.
One trusted terminal status produces at most ONE ChatGPT wake-up signal.
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
VALID_STATUSES = ("WORKING", "COMPLETED", "BLOCKED", "APPROVAL_REQUIRED")
TERMINAL_STATUSES = ("COMPLETED", "BLOCKED", "APPROVAL_REQUIRED")
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
NEVER_BRANCHES = ("main", "master")
DEFAULT_AUTOMATION = "github-actions[bot],claude[bot]"

_FIELD = re.compile(r"^\s*([A-Z][A-Z _/]{1,40}?)\s*:\s*(.*)$")
_RUN_ID = re.compile(r"(?<![a-z_])relay_run_id\s*[:=]\s*([A-Za-z0-9._-]{1,80})", re.I)
_PARENT = re.compile(r"parent_run_id\s*[:=]\s*([A-Za-z0-9._-]{1,80})", re.I)
_BRANCH = re.compile(r"^\s*branch\s*:\s*([A-Za-z0-9._/-]+)", re.I | re.M)


def _csv(name: str, default: str) -> List[str]:
    return [x.strip() for x in (os.environ.get(name) or default).split(",") if x.strip()]


def parse(body: str) -> Dict:
    """Marker kind, UPPERCASE 'KEY: value' fields, relay/parent run ids, branch."""
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
    rid, par, br = _RUN_ID.search(text), _PARENT.search(text), _BRANCH.search(text)
    parent = par.group(1) if par else None
    return {"kind": kind, "fields": fields, "run_id": rid.group(1) if rid else None,
            "parent_run_id": None if parent in (None, "-") else parent,
            "branch": br.group(1).strip() if br else None}


def allowed_branch(name: Optional[str], allowed: Iterable[str]) -> bool:
    return bool(name) and name not in NEVER_BRANCHES and name in [b for b in allowed if b not in NEVER_BRANCHES]


def _login(c: Dict) -> str:
    return ((c.get("user") or {}).get("login") or "")


def _before(comments: Iterable[Dict], cid) -> List[Dict]:
    """Comments posted before comment `cid` (all of them when cid is None)."""
    if cid is None:
        return list(comments)
    return [c for c in comments if c.get("id") is not None and int(c["id"]) < int(cid)]


def acks(comments: Iterable[Dict], automation: List[str]) -> Dict[str, Dict]:
    """run_id -> the FIRST ACK posted by an automation identity (forged ACKs ignored)."""
    out: Dict[str, Dict] = {}
    for c in comments:
        p = parse(c.get("body") or "")
        if p["kind"] == ACK and p["run_id"] and _login(c) in automation and p["run_id"] not in out:
            out[p["run_id"]] = {"branch": p["branch"], "parent_run_id": p["parent_run_id"],
                                "project": p["fields"].get("PROJECT") or _field_lc(c.get("body"), "project")}
    return out


def _field_lc(body: Optional[str], key: str) -> str:
    m = re.search(r"^\s*%s\s*:\s*(.*)$" % re.escape(key), body or "", re.I | re.M)
    return m.group(1).strip() if m else ""


def status_of(body: str) -> Optional[str]:
    """Exact STATUS of a status comment if it is one of the four values, else None."""
    p = parse(body)
    if p["kind"] != STATUS:
        return None
    raw = (p["fields"].get("STATUS") or "").strip().upper()
    first = re.split(r"[\s(,;|]+", raw)[0] if raw else ""
    return first if first in VALID_STATUSES else None


# ── directives ───────────────────────────────────────────────────────────────

def decide(event: Dict, comments: Iterable[Dict], *, issue: int, actors: List[str],
           branches: List[str], automation: Optional[List[str]] = None) -> Tuple[bool, str, Dict]:
    """(run?, reason, info) for a directive comment event. Pure."""
    automation = automation or _csv("RELAY_STATUS_ACTORS", DEFAULT_AUTOMATION)
    c = event.get("comment") or {}
    iss = event.get("issue") or {}
    if (event.get("action") or "created") != "created":
        return False, "not a new comment", {}
    if int(iss.get("number") or 0) != issue:
        return False, "not the relay issue", {}
    if "pull_request" in iss:
        return False, "pull request comment", {}
    login = _login(c)
    if login not in actors:
        return False, "author %r is not an authorized relay actor" % login, {}
    if (c.get("author_association") or "") not in TRUSTED_ASSOCIATIONS:
        return False, "author association %r is not trusted" % c.get("author_association"), {}
    body = c.get("body") or ""
    p = parse(body)
    if p["kind"] != DIRECTIVE or any(m in body for m in NON_DIRECTIVE):
        return False, "not a relay directive", {}
    run_id = p["run_id"] or "c%s" % c.get("id")
    comments = list(comments)
    seen = set(acks(comments, automation))
    seen |= {parse(x.get("body") or "")["run_id"] for x in comments
             if parse(x.get("body") or "")["kind"] == STATUS}
    if run_id in seen:
        return False, "relay_run_id %s already handled (duplicate suppressed)" % run_id, {"run_id": run_id}
    if p["parent_run_id"] and any(a.get("parent_run_id") == p["parent_run_id"]
                                  for a in acks(comments, automation).values()):
        return False, "parent_run_id %s already produced a directive (one review -> one directive)" \
            % p["parent_run_id"], {"run_id": run_id}
    br = p["branch"]
    if not br:
        return False, "directive names no BRANCH", {"run_id": run_id}
    if not allowed_branch(br, branches):
        return False, "branch %r is not an allowed relay branch" % br, {"run_id": run_id}
    return True, "ok", {"run_id": run_id, "branch": br, "project": p["fields"].get("PROJECT", ""),
                        "comment_id": c.get("id"), "parent_run_id": p["parent_run_id"] or ""}


def ack_body(info: Dict, ct: str) -> str:
    return ("%s relay_run_id: %s\nparent_run_id: %s\nproject: %s\nbranch: %s\ntimestamp: %s\n"
            "actor: claude-relay-action\nstatus: ACCEPTED (Claude is starting)"
            % (ACK, info["run_id"], info.get("parent_run_id") or "-", info.get("project") or "-",
               info["branch"], ct))


# ── Claude status ────────────────────────────────────────────────────────────

def validate_status(comment: Dict, comments: Iterable[Dict], *, issue: int, issue_number: int,
                    status_actors: List[str], automation: List[str], branches: List[str]) -> Tuple[bool, str, Dict]:
    """(trusted?, reason, info) for one [RELAY:CLAUDE_STATUS] comment. Pure."""
    if int(issue_number or 0) != issue:
        return False, "not the relay issue", {}
    body = comment.get("body") or ""
    p = parse(body)
    if p["kind"] != STATUS or DIRECTIVE in body:
        return False, "not a Claude status", {}
    login = _login(comment)
    if login not in status_actors:
        return False, "status author %r is not trusted" % login, {}
    st = status_of(body)
    if st is None:
        return False, "STATUS is not one of %s" % "|".join(VALID_STATUSES), {}
    run_id = p["run_id"]
    if not run_id:
        return False, "status names no relay_run_id", {}
    prior = _before(comments, comment.get("id"))
    ack = acks(prior, automation).get(run_id)
    if ack is None:
        return False, "relay_run_id %s has no prior ACK - forged or unknown run" % run_id, {"run_id": run_id}
    br = p["branch"]
    if not allowed_branch(br, branches) or br != ack.get("branch"):
        return False, "status branch %r does not match the ACKed branch %r" % (br, ack.get("branch")), \
            {"run_id": run_id}
    info = {"run_id": run_id, "status": st, "terminal": st in TERMINAL_STATUSES, "branch": br,
            "parent_run_id": ack.get("parent_run_id") or "", "project": ack.get("project") or "",
            "comment_id": comment.get("id")}
    if info["terminal"]:
        for c in prior:
            if status_of(c.get("body") or "") in TERMINAL_STATUSES and \
                    parse(c.get("body") or "")["run_id"] == run_id and _login(c) in status_actors:
                return False, "run %s already reported a terminal status (duplicate ignored)" % run_id, info
    return True, "ok", info


def fallback_status(run_id: str, branch: str, started_ct: str, now_ct: str, outcome: str) -> str:
    """The terminal status the workflow posts when Claude ended without one."""
    return ("%s\nrelay_run_id: %s\nSTATUS: BLOCKED\nPROJECT: -\nBRANCH: %s\nSTART TIME CT: %s\n"
            "CURRENT TIME CT: %s\nCOMPLETED: -\nBLOCKERS: Claude relay action failed before normal completion "
            "(step outcome: %s).\nNEXT RECOMMENDED ACTION: inspect the workflow run/log and resume the same objective "
            "with a new relay_run_id.\nPRODUCTION IMPACT: none\nDO NOT TOUCH CONFIRMATION: main and production untouched"
            % (STATUS, run_id, branch, started_ct, now_ct, outcome or "unknown"))


def needs_fallback(run_id: str, comments: Iterable[Dict], status_actors: List[str]) -> bool:
    """True when no TERMINAL status exists for this run (a WORKING one does not count)."""
    for c in comments:
        if _login(c) in status_actors and parse(c.get("body") or "")["run_id"] == run_id \
                and status_of(c.get("body") or "") in TERMINAL_STATUSES:
            return False
    return True


# ── ChatGPT wake-up signal ───────────────────────────────────────────────────

def signal_payload(info: Dict, ct: str, issue: int) -> Dict:
    """The ONLY content of .relay/chatgpt-wakeup.json - no secrets, no customer data."""
    return {"relay_run_id": info["run_id"], "parent_run_id": info.get("parent_run_id") or "",
            "project": info.get("project") or "", "status": info["status"], "branch": info["branch"],
            "timestamp_ct": ct, "issue": issue, "status_comment_id": info.get("comment_id")}


def should_signal(existing: Optional[Dict], payload: Dict) -> bool:
    """At most ONE wake-up per (run, terminal status)."""
    if payload.get("status") not in TERMINAL_STATUSES:
        return False
    if existing and existing.get("relay_run_id") == payload["relay_run_id"] \
            and existing.get("status") == payload["status"]:
        return False
    return True


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


def _write_payload(payload: Dict):
    with open(os.path.join(os.environ.get("RUNNER_TEMP", "."), "relay-signal.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def main(argv: List[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "decide"
    issue = int(os.environ.get("RELAY_ISSUE") or 1)
    actors = _csv("RELAY_AUTHORIZED_ACTORS", "MikeSimmonsAI")
    branches = _csv("RELAY_ALLOWED_BRANCHES", "sci-program")
    automation = _csv("RELAY_STATUS_ACTORS", DEFAULT_AUTOMATION)
    status_actors = automation + [a for a in actors if a not in automation]

    if cmd == "decide":
        event = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
        run, reason, info = decide(event, _all_comments(issue), issue=issue, actors=actors,
                                   branches=branches, automation=automation)
        print("relay guard: %s - %s" % ("RUN" if run else "SKIP", reason))
        if run:
            # Claim the run BEFORE Claude starts: a re-delivered event now sees the ACK.
            _api("POST", "/issues/%d/comments" % issue, {"body": ack_body(info, ct_now())})
            with open(os.path.join(os.environ.get("RUNNER_TEMP", "."), "relay-directive.md"),
                      "w", encoding="utf-8") as f:
                f.write((event.get("comment") or {}).get("body") or "")
        _output(run=str(run).lower(), reason=reason, run_id=info.get("run_id", ""),
                branch=info.get("branch", ""), started_ct=ct_now())
        return 0

    if cmd == "status":            # a status comment arrived (relay-status-event.yml)
        event = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
        c = event.get("comment") or {}
        ok, reason, info = validate_status(c, _all_comments(issue), issue=issue,
                                           issue_number=(event.get("issue") or {}).get("number"),
                                           status_actors=status_actors, automation=automation, branches=branches)
        print("relay status: %s - %s" % ("TRUSTED" if ok else "REJECTED", reason))
        if ok and info["terminal"]:
            _write_payload(signal_payload(info, ct_now(), issue))
        _output(trusted=str(ok).lower(), terminal=str(bool(ok and info.get("terminal"))).lower(),
                status=info.get("status", ""), run_id=info.get("run_id", ""), reason=reason)
        return 0

    if cmd == "finalize":          # end of the Claude job: never let a run vanish
        run_id, branch = os.environ["RELAY_RUN_ID"], os.environ["RELAY_BRANCH"]
        comments = _all_comments(issue)
        posted = False
        if needs_fallback(run_id, comments, status_actors):
            _api("POST", "/issues/%d/comments" % issue, {"body": fallback_status(
                run_id, branch, os.environ.get("RELAY_STARTED_CT", ""), ct_now(),
                os.environ.get("CLAUDE_OUTCOME", ""))})
            posted = True
            comments = _all_comments(issue)
        # Process the run's terminal status here too: statuses posted with the
        # job token do not trigger relay-status-event.yml.
        terminal = [c for c in comments if parse(c.get("body") or "")["run_id"] == run_id
                    and status_of(c.get("body") or "") in TERMINAL_STATUSES]
        result = {"trusted": False}
        for c in terminal[:1]:
            ok, reason, info = validate_status(c, comments, issue=issue, issue_number=issue,
                                               status_actors=status_actors, automation=automation,
                                               branches=branches)
            print("relay finalize: %s - %s" % ("TRUSTED" if ok else "REJECTED", reason))
            if ok:
                _write_payload(signal_payload(info, ct_now(), issue))
                result = {"trusted": True, "status": info["status"]}
        _output(fallback_posted=str(posted).lower(), trusted=str(result["trusted"]).lower(),
                status=result.get("status", ""))
        return 0

    if cmd == "should-signal":     # compare the payload with the relay-signal branch's file
        new = json.load(open(argv[2], encoding="utf-8"))
        old = None
        if len(argv) > 3 and os.path.exists(argv[3]):
            try:
                old = json.load(open(argv[3], encoding="utf-8"))
            except ValueError:
                old = None
        _output(signal=str(should_signal(old, new)).lower())
        return 0

    print("usage: relay_guard.py decide|status|finalize|should-signal", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
