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


def resend_stale(db: Session, org_id: str) -> int:
    """Recovery for a missed event: a job waiting for the runner whose last
    start request is missing, failed or older than 10 minutes is re-sent -
    one dispatch covers all waiting work (the runner keeps going until none is
    left)."""
    if not configured():
        return 0
    waiting = [j for j in db.query(VoltDevJob).filter(VoltDevJob.organization_id == org_id,
                                                      VoltDevJob.status.in_(("queued", "merge_requested"))).all()
               if needs_runner(j)]
    now = C.now()
    stale = [j for j in waiting if j.dispatched_at is None or j.dispatch_state != "dispatched"
             or now - j.dispatched_at > RESEND_AFTER]
    if not stale:
        return 0
    if any(j.dispatched_at and now - j.dispatched_at <= RESEND_AFTER and j.dispatch_state == "dispatched" for j in waiting):
        return 0                                          # a fresh request is already on its way
    oldest = min(stale, key=lambda j: j.created_at)
    return 1 if dispatch(db, oldest, "resend") == "dispatched" else 0


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
