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
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Any, Dict, Optional

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
        rs = resend_stale(db)
        for j in rs["blocked"]:
            rows += R.notify(db, j, "blocked")
        out = {"expired": len(expired), "deployed": len(deployed), "resent": rs["sent"], "blocked": len(rs["blocked"])}
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
