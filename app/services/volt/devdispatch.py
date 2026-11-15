"""VOLT development relay - EVENT-DRIVEN START and DEPLOY PROOF (#571, 2026-11-09).

GitHub's scheduled workflows in this repository run hours apart (a 15-minute
schedule fires every ~3-4 hours), so a schedule cannot start work promptly.
Instead, every event that needs the runner sends a workflow_dispatch for
.github/workflows/volt-dev.yml right away:

    submit, approve-merge, retry, rollback   -> dispatch
    a finished run with more work waiting    -> the runner dispatches itself
    a queued job nobody started (missed event, GitHub hiccup) -> re-sent the next
                                                time anyone views the jobs
                                                (at most every 10 minutes)

The ONLY credential is VOLT_DEV_GITHUB_TOKEN in the backend's Render
environment: a fine-grained token for this one repository with
    Actions: Read and write   (send workflow_dispatch)
    Contents: Read-only       (compare commits for deploy proof)
    Metadata: Read-only       (always required)
It is never returned to a browser, written to a log, or stored in the DB.

What each state proves
----------------------
    dispatched   GitHub answered 204 to the dispatch request. NOT proof a run
                 started.
    running      the runner (proven by its GitHub OIDC identity) claimed the job.
    deployed     the running backend (/version, this process's own commit) and
                 the served frontend (build-info.json) both contain the merge
                 commit, as GitHub's compare API reports.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.volt_models import VoltDevEvent, VoltDevJob
from app.services.volt import common as C
from app.services.volt import devrelay as R

log = logging.getLogger(__name__)
API = "https://api.github.com"
FRONTEND = os.environ.get("VOLT_PUBLIC_ORIGIN", "https://voltmarketing.live").rstrip("/")
RESEND_AFTER = timedelta(minutes=10)
DEPLOY_CHECK_EVERY = timedelta(seconds=45)


def _token() -> str:
    return (os.environ.get("VOLT_DEV_GITHUB_TOKEN") or "").strip()


def configured() -> bool:
    return bool(_token())


def _gh(method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: int = 15):
    """(status, json|None, short_error). Never includes the token anywhere."""
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + _token(), "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "volt-dev-relay",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None), None
    except urllib.error.HTTPError as e:
        try:
            msg = (json.loads(e.read() or b"{}").get("message") or "")[:200]
        except Exception:  # noqa: BLE001
            msg = ""
        return e.code, None, "GitHub answered %d%s" % (e.code, (": " + msg) if msg else "")
    except Exception as e:  # noqa: BLE001 - network failures are recorded, never raised
        return 0, None, "GitHub unreachable (%s)" % type(e).__name__


def dispatch(db: Session, job: VoltDevJob, purpose: str) -> str:
    """Ask GitHub to start the runner now. Records the outcome on the job and
    its timeline. Returns dispatched | failed | not_configured. Never raises."""
    now = C.now()
    if not configured():
        job.dispatch_state, job.dispatch_purpose = "not_configured", purpose
        job.dispatch_error = "Instant start is not set up (no GitHub token on the server); the slow scheduled check will pick it up."
        db.add(VoltDevEvent(job_id=job.id, actor_kind="system", action="dispatch_not_configured", status=job.status,
                            detail=job.dispatch_error))
        return "not_configured"
    status, _body, err = _gh("POST", "/repos/%s/actions/workflows/volt-dev.yml/dispatches" % R.REPO,
                             {"ref": "main", "inputs": {"reason": "DEV-%d %s (attempt %d)" % (job.number, purpose, job.attempt)}})
    job.dispatch_count = (job.dispatch_count or 0) + 1
    job.dispatch_purpose, job.dispatched_at = purpose, now
    if status == 204:
        job.dispatch_state, job.dispatch_error = "dispatched", None
        db.add(VoltDevEvent(job_id=job.id, actor_kind="system", action="dispatched", status=job.status,
                            detail="GitHub accepted the start request (%s). Waiting for the runner to pick it up." % purpose))
        return "dispatched"
    job.dispatch_state, job.dispatch_error = "failed", err
    log.warning("volt dev dispatch failed for DEV-%s: %s", job.number, err)
    db.add(VoltDevEvent(job_id=job.id, actor_kind="system", action="dispatch_failed", status=job.status, detail=err))
    return "failed"


def needs_runner(job: VoltDevJob) -> bool:
    return job.status in ("queued", "merge_requested") and job.lease_run_id is None


MAX_SENDS = 6                     # start requests per job before it is marked Blocked


def _due(j: VoltDevJob, now) -> bool:
    """Bounded backoff. A failed request is retried after 0, 1, 3, 7, 15 min;
    a request GitHub accepted but no runner answered, every 10 minutes."""
    if j.dispatched_at is None or j.dispatch_state is None:
        return True                                       # saved, but the start was never sent (lost)
    n = max(1, j.dispatch_count or 1)
    if j.dispatch_state == "failed":
        wait = timedelta(minutes=min(2 ** (n - 1) - 1, 15))
    else:
        wait = RESEND_AFTER
    return now - j.dispatched_at >= wait


def resend_stale(db: Session, org_id: Optional[str] = None) -> Dict[str, Any]:
    """Recovery for a missed or failed start. Runs from the backend's own
    background pass every 2 minutes (no one has to open anything) and when a
    job list is viewed. One dispatch covers all waiting work: the runner keeps
    going until none is left. After MAX_SENDS unanswered requests the job is
    marked Blocked with the reason, and stops costing anything.
    Returns {"sent": n, "blocked": [jobs]}."""
    out: Dict[str, Any] = {"sent": 0, "blocked": []}
    if not configured():
        return out
    q = db.query(VoltDevJob).filter(VoltDevJob.status.in_(("queued", "merge_requested")),
                                    VoltDevJob.blocked_reason.is_(None))
    if org_id:
        q = q.filter(VoltDevJob.organization_id == org_id)
    waiting = sorted([j for j in q.all() if needs_runner(j)], key=lambda j: j.created_at)
    now = C.now()
    if any(j.dispatch_state == "dispatched" and j.dispatched_at and now - j.dispatched_at < RESEND_AFTER
           for j in waiting) and not any(j.dispatched_at is None for j in waiting):
        return out                                        # a fresh request is already on its way
    for j in waiting:
        if not _due(j, now):
            continue
        if (j.dispatch_count or 0) >= MAX_SENDS:
            j.blocked_reason = ("GitHub did not start the runner after %d start requests (last: %s). "
                                "Check the repository's Actions settings, then send the start request again."
                                % (j.dispatch_count, j.dispatch_error or "accepted, never picked up"))
            db.add(VoltDevEvent(job_id=j.id, actor_kind="system", action="blocked", status=j.status,
                                detail=j.blocked_reason))
            out["blocked"].append(j)
            continue
        if dispatch(db, j, "resend") == "dispatched":
            out["sent"] += 1
        break                                             # one request per pass
    return out


def selftest(db: Session) -> Dict[str, Any]:
    """Prove the saved token works without waiting for a real job: a read
    check now, and - only until the runner has been seen started by a
    workflow_dispatch - ONE dispatch with no job (the run finds no work and
    reports whether a Claude credential exists). Once per process at most."""
    from app.models.volt_models import VoltDevRunnerSeen
    row = db.get(VoltDevRunnerSeen, "runner")
    if row is None:
        row = VoltDevRunnerSeen(id="runner")
        db.add(row)
    chk = check_github()
    row.token_check_at, row.token_check = C.now(), json.dumps(chk)[:2000]
    log.info("volt dev relay token check: ok=%s detail=%s", chk.get("ok"), chk.get("detail"))
    out = {"token_ok": chk.get("ok")}
    if chk.get("ok") and row.event != "workflow_dispatch":
        st, _b, err = _gh("POST", "/repos/%s/actions/workflows/volt-dev.yml/dispatches" % R.REPO,
                          {"ref": "main", "inputs": {"reason": "token check (no job)"}})
        row.selftest_dispatch_at = C.now()
        out["selftest_dispatch"] = st
        log.info("volt dev relay self-test dispatch: GitHub answered %s%s", st, (" (%s)" % err) if err else "")
    return out


# ── deploy proof from the backend itself ───────────────────────────────────

def _running_commits() -> Dict[str, Optional[str]]:
    backend = (os.environ.get("RENDER_GIT_COMMIT") or "").strip() or None
    frontend = None
    try:
        req = urllib.request.Request(FRONTEND + "/build-info.json", headers={"Cache-Control": "no-cache",
                                                                             "User-Agent": "volt-dev-relay"})
        with urllib.request.urlopen(req, timeout=10) as r:
            frontend = (json.loads(r.read()) or {}).get("commit")
    except Exception:  # noqa: BLE001
        frontend = None
    return {"backend": backend, "frontend": frontend if frontend and frontend != "unknown" else None}


def _contains(merge_sha: str, running: Optional[str]) -> bool:
    if not running:
        return False
    if running == merge_sha:
        return True
    status, body, _err = _gh("GET", "/repos/%s/compare/%s...%s" % (R.REPO, merge_sha, running))
    return status == 200 and (body or {}).get("status") in ("identical", "ahead")


def verify_deploys(db: Session, org_id: str) -> list:
    """Mark merged jobs deployed when production is ACTUALLY running them.
    Returns the jobs that just became deployed."""
    done = []
    merged = (db.query(VoltDevJob).filter(VoltDevJob.organization_id == org_id, VoltDevJob.status == "merged").all())
    if not merged or not configured():
        return done
    now = C.now()
    running = None
    for j in merged:
        if j.deploy_checked_at and now - j.deploy_checked_at < DEPLOY_CHECK_EVERY:
            continue
        j.deploy_checked_at = now
        running = running or _running_commits()
        need = [t for t in (j.deploy_targets or "").split(",") if t]
        if j.merge_sha and need and all(_contains(j.merge_sha, running.get(t)) for t in need):
            j.status, j.deployed_at = "deployed", now
            j.deploy_detail = "Verified running in production: " + ", ".join(
                "%s %s" % (t, (running.get(t) or "")[:7]) for t in need)
            j.status_detail = j.deploy_detail
            db.add(VoltDevEvent(job_id=j.id, actor_kind="system", action="deploy_verified", status="deployed",
                                detail=j.deploy_detail))
            done.append(j)
    return done


# ── 2026-11-10: a live token check and the review material for ChatGPT ──────

_CHECK = {"at": None, "out": None}


def check_github() -> Dict[str, Any]:
    """Does the saved token actually work? Reads this workflow's definition and
    its recent runs (Actions: read) - it starts nothing. Cached for 60 s.
    The write half (dispatch) is proven by the first real job's 204."""
    now = C.now()
    if _CHECK["at"] and now - _CHECK["at"] < timedelta(seconds=60):
        return _CHECK["out"]
    if not configured():
        out = {"configured": False, "ok": False, "detail": "VOLT_DEV_GITHUB_TOKEN is not set on the backend."}
    else:
        st, data, err = _gh("GET", "/repos/%s/actions/workflows/volt-dev.yml" % R.REPO)
        if st == 200:
            st2, runs, err2 = _gh("GET", "/repos/%s/actions/workflows/volt-dev.yml/runs?per_page=1" % R.REPO)
            last = ((runs or {}).get("workflow_runs") or [{}])[0] if st2 == 200 else {}
            out = {"configured": True, "ok": True, "workflow_state": (data or {}).get("state"),
                   "detail": "The token can read the relay workflow.",
                   "last_run": {"event": last.get("event"), "status": last.get("status"),
                                "conclusion": last.get("conclusion"), "created_at": last.get("created_at"),
                                "url": last.get("html_url")} if last else None}
        else:
            out = {"configured": True, "ok": False, "detail": err or ("GitHub answered %d" % st)}
    _CHECK["at"], _CHECK["out"] = now, out
    return out


# ── release gate (2026-11-14) ─────────────────────────────────────────────
# Why this was rewritten: DEV-6 / PR #611 was refused with "GitHub merge
# status: unstable" although GitHub's only required check had passed and no
# review is required. "unstable" only means that some NON-required check is
# not green - here, workflows GitHub held at "waiting for approval" because a
# bot opened the PR, and installed apps that never report on PRs. The gate now
# reads the rules GitHub actually enforces on main, blocks on those (and on any
# check that genuinely FAILED), re-checks a transient "unknown", and says
# exactly what blocks, where it comes from, what to do, and when it checked.
# GitHub still enforces every rule at merge time (the runner merges through
# the merge API, pinned to the approved commit) - this gate never bypasses it.

FALLBACK_REQUIRED = ("Redesign owner approval",)
_OK_CONCLUSIONS = ("success", "neutral", "skipped")
_BAD_CONCLUSIONS = ("failure", "timed_out", "cancelled", "startup_failure", "stale")


def _ct(dt) -> str:
    """A stored UTC time shown in Central time, with CST/CDT."""
    from datetime import timezone
    from zoneinfo import ZoneInfo
    d = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return d.astimezone(ZoneInfo("America/Chicago")).strftime("%b %-d, %Y %-I:%M %p %Z")


def required_rules(base: str = "main") -> Dict[str, Any]:
    """What GitHub enforces on the base branch: required check names, required
    approving reviews, whether the branch must be up to date. Read live from
    the branch rules (rulesets + legacy protection); falls back to the known
    required check if the rules cannot be read."""
    st, rules, err = _gh("GET", "/repos/%s/rules/branches/%s" % (R.REPO, base))
    out = {"checks": [], "reviews": 0, "strict": False, "source": "GitHub branch rules for %s" % base}
    if st != 200 or not isinstance(rules, list):
        out.update(checks=list(FALLBACK_REQUIRED), source="fallback list (GitHub rules unreadable: %s)" % (err or st))
        return out
    for r in rules:
        p = r.get("parameters") or {}
        if r.get("type") == "required_status_checks":
            out["checks"] += [c.get("context") for c in p.get("required_status_checks") or [] if c.get("context")]
            out["strict"] = out["strict"] or bool(p.get("strict_required_status_checks_policy"))
        elif r.get("type") == "pull_request":
            out["reviews"] = max(out["reviews"], int(p.get("required_approving_review_count") or 0))
    return out


def approve_held_runs(head_sha: str) -> List[str]:
    """Workflows on a relay-opened PR are held by GitHub ("action_required")
    because a bot opened it. Approving them lets the normal CI (e.g. the VOLT
    test suite) run on the exact tested commit. Returns the workflow names."""
    st, data, _ = _gh("GET", "/repos/%s/actions/runs?head_sha=%s&status=action_required&per_page=20" % (R.REPO, head_sha))
    names = []
    for run in (data or {}).get("workflow_runs", []) if st == 200 else []:
        st2, _b, _e = _gh("POST", "/repos/%s/actions/runs/%s/approve" % (R.REPO, run["id"]))
        if st2 in (200, 201, 204):
            names.append(run.get("name") or str(run["id"]))
    return names


def release_check(job: VoltDevJob, poll: int = 3) -> Dict[str, Any]:
    """{"ok": bool, "blocker", "source", "next_action", "checked_at", "notes"}.
    `ok` is True only on positive evidence; an unknown answer is never success."""
    now = C.now()
    out: Dict[str, Any] = {"ok": False, "blocker": None, "source": None, "next_action": None,
                           "checked_at": C.iso(now), "checked_at_central": _ct(now), "notes": []}

    def block(blocker, source, nxt):
        out.update(blocker=blocker, source=source, next_action=nxt)
        return out

    root = "/repos/%s" % R.REPO
    if not configured():
        return block("The backend cannot reach GitHub.", "VOLT backend configuration", "Set VOLT_DEV_GITHUB_TOKEN.")
    pr = None
    for i in range(max(1, poll)):
        st, pr, err = _gh("GET", "%s/pulls/%d" % (root, job.pr_number))
        if st != 200:
            return block("GitHub did not answer about PR #%d (%s)." % (job.pr_number, err or st), "GitHub API",
                         "Try again in a minute; nothing is stuck.")
        if pr.get("mergeable") is not None and pr.get("mergeable_state") != "unknown":
            break
        if i < poll - 1:
            import time
            time.sleep(1.5)
    if pr["head"]["sha"] != job.head_sha:
        return block("The PR now points at %s, not the tested commit %s." % (pr["head"]["sha"][:12], (job.head_sha or "")[:12]),
                     "Pull request #%d" % job.pr_number, "Retry the job so the new commit is tested, then approve it.")
    if pr.get("merged"):
        return block("PR #%d is already merged." % job.pr_number, "Pull request", "Nothing to approve.")
    if pr["state"] != "open" or pr.get("draft"):
        return block("PR #%d is %s." % (job.pr_number, "a draft" if pr.get("draft") else pr["state"]),
                     "Pull request #%d" % job.pr_number, "Reopen it or mark it ready, or retry the job.")
    if pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
        return block("PR #%d conflicts with main." % job.pr_number, "GitHub merge check",
                     "Retry the job so Claude rebuilds it on the current main, then approve the new commit.")
    if pr.get("mergeable") is None or pr.get("mergeable_state") == "unknown":
        return block("GitHub is still working out whether PR #%d can merge." % job.pr_number, "GitHub (temporary)",
                     "Check again in a minute. This clears by itself.")

    rules = required_rules(pr["base"]["ref"])
    st, data, err = _gh("GET", "%s/commits/%s/check-runs?per_page=100" % (root, job.head_sha))
    if st != 200:
        return block("Cannot read the checks on %s (%s)." % (job.head_sha[:12], err or st), "GitHub API",
                     "Try again in a minute.")
    runs = data.get("check_runs", [])
    st2, combined, _ = _gh("GET", "%s/commits/%s/status" % (root, job.head_sha))
    statuses = (combined or {}).get("statuses", []) if st2 == 200 else []
    latest: Dict[str, Dict[str, Any]] = {}
    for r in runs:                                       # newest run per name wins
        if r["name"] not in latest or (r.get("started_at") or "") > (latest[r["name"]].get("started_at") or ""):
            latest[r["name"]] = r
    for name in rules["checks"]:
        r = latest.get(name)
        stc = next((x for x in statuses if x.get("context") == name), None)
        if r is None and stc is None:
            return block("Required check \"%s\" has not reported on %s." % (name, job.head_sha[:12]),
                         rules["source"], "Wait for it, or open the PR's Checks tab if it never starts.")
        if r is not None and r["status"] != "completed":
            return block("Required check \"%s\" is still running." % name, rules["source"], "Wait for it to finish.")
        concl = r["conclusion"] if r is not None else {"success": "success", "pending": None}.get(stc["state"], stc["state"])
        if concl not in _OK_CONCLUSIONS:
            return block("Required check \"%s\": %s." % (name, concl or "pending"), rules["source"],
                         "Open the check on the PR, fix the cause, and retry the job.")
    failed = [n for n, r in latest.items() if r["status"] == "completed" and r["conclusion"] in _BAD_CONCLUSIONS]
    failed += [x["context"] for x in statuses if x.get("state") in ("failure", "error") and x["context"] not in failed]
    if failed:
        return block("Check failed on the tested commit: %s." % ", ".join(sorted(failed)[:4]), "GitHub checks",
                     "Open the failing check, fix the cause, and retry the job.")
    if rules["reviews"]:
        st3, reviews, _ = _gh("GET", "%s/pulls/%d/reviews?per_page=100" % (root, job.pr_number))
        approved = {rv["user"]["login"] for rv in (reviews or []) if rv.get("state") == "APPROVED"
                    and rv.get("commit_id") == job.head_sha} if st3 == 200 else set()
        if len(approved) < rules["reviews"]:
            return block("GitHub requires %d approving review(s); %d on this commit." % (rules["reviews"], len(approved)),
                         rules["source"], "A repository collaborator approves the PR on GitHub.")
    if rules["strict"] and pr.get("mergeable_state") == "behind":
        return block("Main requires the PR to be up to date and it is behind.", rules["source"],
                     "Retry the job so it is rebuilt on the current main.")
    if pr.get("mergeable_state") == "blocked":
        return block("GitHub reports the merge as blocked by a rule the relay cannot see.", "GitHub branch rules",
                     "Open PR #%d on GitHub to see the rule." % job.pr_number)
    pending = sorted(n for n, r in latest.items() if r["status"] != "completed" or r["conclusion"] == "action_required")
    if pending:
        out["notes"].append("Not required and not failing (informational): %s." % ", ".join(pending[:6]))
    out["notes"].append("Required by GitHub: %s; reviews required: %d (%s)." % (
        ", ".join(rules["checks"]) or "no checks", rules["reviews"], rules["source"]))
    out["ok"] = True
    return out


def release_blocker(job: VoltDevJob) -> Optional[str]:
    """Kept for callers that want one line: None when release can proceed."""
    chk = release_check(job)
    if chk["ok"]:
        return None
    return "%s Source: %s. Next: %s (checked %s)" % (chk["blocker"], chk["source"], chk["next_action"],
                                                     chk["checked_at_central"])


# ── automatic release policy (routine, low-risk changes) ────────────────────
#
# A job is released WITHOUT a person's approval only when every check passes:
#   * tests passed on the runner and the PR still points at that exact commit;
#   * GitHub's own release rules pass (release_check: required checks, reviews,
#     conflicts, no failed check) - protections are never bypassed;
#   * EVERY changed file is a low-risk file: the Consumer Leads screens and
#     customer website, VOLT styles/images, VOLT docs, and VOLT tests that only
#     ADD lines (a change that removes an existing test line needs a person).
#     The Development and VOLT Operations screens are NOT on this list. Any
#     backend file (auth, permissions,
#     data, migrations, secrets, intake and consent rules, other businesses)
#     means a person approves, as before;
#   * the diff touches none of the sensitive words below (intake switch,
#     consent wording, team/permission settings, secrets, outreach);
#   * it is not a rollback, and the switch VOLT_DEV_AUTO_RELEASE is not "off".
AUTO_RELEASE_FILES = (
    re.compile(r"^frontend/src/pages/volt/(?:ConsumerLeadsWorkspace|VoltLeads)\.jsx\Z"),
    re.compile(r"^frontend/src/pages/volt/[A-Za-z0-9_-]+\.css\Z"),
    re.compile(r"^frontend/public/brands/volt/"),
    re.compile(r"^docs/volt/"),
    re.compile(r"^tests/test_volt[^/]*\.py\Z"),
)
AUTO_RELEASE_STOP_WORDS = ("intake_enabled", "toggleintake", "/settings", "/grants", "consent", "password", "secret",
                           "token", "api_key", "apikey", "/preview", "is_test", "send_message", "sms",
                           "twilio", "dangerouslysetinnerhtml", "eval(", "af_token", "manager", "volt_dev_")


def auto_release_enabled() -> bool:
    return (os.environ.get("VOLT_DEV_AUTO_RELEASE") or "on").strip().lower() not in ("off", "0", "false", "no")


def auto_release_check(job: VoltDevJob, *, files_diff: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """{"eligible": bool, "passed": [...], "reason": str|None}. Never approves
    anything itself."""
    passed: List[str] = []

    def no(reason):
        return {"eligible": False, "passed": passed, "reason": reason}
    if not auto_release_enabled():
        return no("Automatic release is switched off (VOLT_DEV_AUTO_RELEASE=off).")
    if job.kind == "revert":
        return no("Rollbacks are approved by a person.")
    if job.status != "ready_to_test" or not (job.tests_passed and job.head_sha and job.pr_number):
        return no("Not ready: tests must pass on an open PR.")
    passed.append("tests passed at %s" % job.head_sha[:7])
    # GitHub's own file list for the PR is what counts (not only the runner's).
    d = files_diff if files_diff is not None else review(job, max_chars=400000)
    if not d.get("available"):
        return no("Could not read the change from GitHub (%s), so a person reviews it." % d.get("detail", "no detail"))
    files = [f.get("file") or "" for f in d.get("files") or []]
    if not files or len(files) >= 100:
        return no("Could not read the full file list, so a person reviews it.")
    risky = [f for f in files if not any(rx.match(f) for rx in AUTO_RELEASE_FILES)]
    if risky:
        return no("Needs a person: changes %s (outside the low-risk screens/docs/tests)." % ", ".join(risky[:5]))
    if any(f.get("diff_truncated") or (not f.get("diff") and f.get("status") != "removed") for f in d["files"]):
        return no("Part of the change could not be read (too large or binary), so a person reviews it.")
    weakened = [f["file"] for f in d["files"] if (f.get("file") or "").startswith("tests/")
                and (f.get("status") == "removed" or any(l.startswith("-") and not l.startswith("---")
                                                         for l in (f.get("diff") or "").splitlines()))]
    if weakened:
        return no("Needs a person: existing tests changed or removed in %s." % ", ".join(weakened[:3]))
    passed.append("only low-risk files (%d)" % len(files))
    changed = "\n".join(line for f in d["files"] for line in (f.get("diff") or "").splitlines()
                        if line[:1] in "+-" and not line.startswith(("+++", "---"))).lower()
    hit = [w for w in AUTO_RELEASE_STOP_WORDS if w in changed]
    if hit:
        return no("Needs a person: the change touches %s." % ", ".join(hit[:5]))
    passed.append("no permission, consent, intake, secret or outreach changes")
    chk = release_check(job)
    if not chk["ok"]:
        return no("GitHub release rules not met: %s (%s). Next: %s" % (chk["blocker"], chk["source"], chk["next_action"]))
    passed.append("GitHub required checks and rules pass (checked %s)" % chk["checked_at_central"])
    return {"eligible": True, "passed": passed, "reason": None}


def auto_release_pass(db: Session) -> List[VoltDevJob]:
    """Approves eligible ready jobs (once per tested commit) and starts the
    pinned-commit merge. Jobs that are not eligible get ONE event saying why,
    so the timeline shows that a person is needed."""
    out = []
    if not configured() or not auto_release_enabled():
        return out
    for j in db.query(VoltDevJob).filter(VoltDevJob.status == "ready_to_test", VoltDevJob.pr_number.isnot(None)).all():
        key = "%s:%s" % (j.id, j.head_sha)
        if key in _SEEN["auto_release"]:
            continue
        res = auto_release_check(j)
        if not res["eligible"] and res["reason"] and res["reason"].startswith("GitHub release rules"):
            continue                       # checks still running: look again next pass
        _SEEN["auto_release"].add(key)
        if res["eligible"]:
            R.approve_by_policy(db, j, res["passed"])
            db.flush()
            out.append(j)
        else:
            db.add(VoltDevEvent(job_id=j.id, actor_kind="system", action="auto_release_skipped", status=j.status,
                                detail="Waiting for a person's approval. %s" % res["reason"]))
    return out


def review(job: VoltDevJob, max_chars: int = 14000) -> Dict[str, Any]:
    """The change itself, for review outside GitHub: each changed file with its
    diff, from the job's PR at the exact tested commit. There is no hosted
    preview for VOLT branches (Render PR previews are off), so this plus the
    test results is the review material."""
    if not job.pr_number:
        return {"available": False, "detail": "No pull request yet - the change has not been written."}
    if not configured():
        return {"available": False, "detail": "The backend cannot read GitHub (token not set)."}
    st, files, err = _gh("GET", "/repos/%s/pulls/%d/files?per_page=100" % (R.REPO, job.pr_number))
    if st != 200 or not isinstance(files, list):
        return {"available": False, "detail": err or ("GitHub answered %d" % st)}
    out, used = [], 0
    for f in files:
        patch = f.get("patch") or ""
        room = max(0, max_chars - used)
        clipped = patch[:room]
        used += len(clipped)
        out.append({"file": f.get("filename"), "status": f.get("status"), "additions": f.get("additions"),
                    "deletions": f.get("deletions"), "diff": clipped,
                    "diff_truncated": len(clipped) < len(patch)})
    return {"available": True, "pr_url": job.pr_url, "tested_commit": job.head_sha, "files": out,
            "preview": "No live preview site exists for VOLT branches; review the diff and tests here, "
                       "or open the pull request."}


# ── GitHub-side events the relay must not miss (2026-11-14) ─────────────────

_SEEN: Dict[str, Any] = {"approved_runs": set(), "pr_checked": {}, "auto_release": set()}
PR_CHECK_EVERY = timedelta(minutes=2)


def reconcile_github(db: Session) -> Dict[str, list]:
    """For jobs waiting on review or merge:
    - approve workflow runs GitHub held because a bot opened the PR, so the
      normal CI runs on the tested commit (once per commit);
    - if the PR was merged ON GITHUB (not through the relay's approve button),
      record it truthfully - who merged, when, which commit - so the job moves to
      Merged / deploy verification instead of sitting at Ready to review."""
    out: Dict[str, list] = {"approved": [], "merged": []}
    if not configured():
        return out
    now = C.now()
    jobs = (db.query(VoltDevJob).filter(VoltDevJob.status.in_(("ready_to_test", "merge_requested")),
                                        VoltDevJob.pr_number.isnot(None), VoltDevJob.lease_run_id.is_(None)).all())
    for j in jobs:
        if j.head_sha and j.head_sha not in _SEEN["approved_runs"]:
            _SEEN["approved_runs"].add(j.head_sha)
            names = approve_held_runs(j.head_sha)
            if names:
                db.add(VoltDevEvent(job_id=j.id, actor_kind="system", action="ci_started", status=j.status,
                                    detail="Started GitHub checks held for approval: %s" % ", ".join(names)))
                out["approved"].append(j)
        last = _SEEN["pr_checked"].get(j.id)
        if last and now - last < PR_CHECK_EVERY:
            continue
        _SEEN["pr_checked"][j.id] = now
        st, pr, _ = _gh("GET", "/repos/%s/pulls/%d" % (R.REPO, j.pr_number))
        if st != 200 or not pr.get("merged"):
            continue
        same = pr["head"]["sha"] == j.head_sha
        by = (pr.get("merged_by") or {}).get("login") or "someone"
        j.merge_sha = pr.get("merge_commit_sha")
        j.merge_approved_sha = j.merge_approved_sha or (j.head_sha if same else None)
        j.merged_at = now
        detail = ("Merged on GitHub by %s (not through the relay's approve button) as %s%s." % (
            by, (j.merge_sha or "")[:12], "" if same else "; WARNING: the merged head %s is not the tested commit %s"
            % (pr["head"]["sha"][:12], (j.head_sha or "")[:12])))
        j.status, j.status_detail = "merged", detail
        db.add(VoltDevEvent(job_id=j.id, actor_kind="system", action="merged_on_github", status="merged", detail=detail))
        if not (j.deploy_targets or ""):
            j.status, j.deployed_at = "deployed", now
            j.deploy_detail = j.status_detail = "No running service changed (tests/docs only)."
            db.add(VoltDevEvent(job_id=j.id, actor_kind="system", action="no_deploy_needed", status="deployed",
                                detail=j.deploy_detail))
        out["merged"].append(j)
    return out


# ── the backend's own watch (every 2 minutes; nobody has to open a page) ────

_SELFTEST = {"done": False}


def watch_pass(db: Session) -> Dict[str, Any]:
    """Expire dead leases, verify deploys, re-send missed starts with backoff
    (Blocked after MAX_SENDS), and - once per process - check the GitHub token.
    Sends the matching notices. Never raises."""
    from app.services.volt import mail
    rows, out = [], {}
    try:
        expired = R._expire_leases(db)
        for j in expired:
            rows += R.notify(db, j, "failed")
        orgs = {o for (o,) in db.query(VoltDevJob.organization_id).filter(VoltDevJob.status == "merged").distinct()}
        deployed = []
        for org in orgs:
            deployed += verify_deploys(db, org)
        for j in deployed:
            rows += R.notify(db, j, "deployed")
        released = auto_release_pass(db)
        db.commit()
        for j in released:
            dispatch(db, j, "merge")
        out_auto = len(released)
        gh_events = reconcile_github(db)
        for j in gh_events["merged"]:
            rows += R.notify(db, j, j.status)
        out["merged_on_github"] = len(gh_events["merged"])
        out["ci_started"] = len(gh_events["approved"])
        rs = resend_stale(db)
        for j in rs["blocked"]:
            rows += R.notify(db, j, "blocked")
        out = {"expired": len(expired), "deployed": len(deployed), "resent": rs["sent"], "blocked": len(rs["blocked"]),
               "auto_released": out_auto}
        if not _SELFTEST["done"]:
            _SELFTEST["done"] = True
            if configured():
                out["selftest"] = selftest(db)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("volt dev watch pass failed")
        return {"error": True}
    for r in rows:
        try:
            mail.deliver(db, r)
        except Exception:  # noqa: BLE001
            pass
    return out
