"""VOLT development relay (Issue #571, 2026-11-09).

Two authorized people - Mike and his designated collaborator - can ask for a
VOLT Marketing change, watch it, approve it and ship it, with equal authority
and without a GitHub or Claude account.

HOW A REQUEST TRAVELS
---------------------
  1. A principal submits it: the VOLT Operations "Development" screen (signed
     in with their own EvoSys login), or a ChatGPT custom action holding that
     person's personal access key. Both land here as a VoltDevJob (DEV-n),
     with an idempotency key so a double submit is one job.
  2. The runner - .github/workflows/volt-dev.yml on GitHub Actions - asks
     /volt/dev/runner/next for work. It proves who it is with GitHub's own
     signed OIDC identity token (no shared secret anywhere): this repository,
     that exact workflow file on main. Each token is used once (jti).
  3. The runner leases the job, runs Claude on a fresh branch volt-dev/DEV-n,
     refuses anything that touches files outside VOLT, runs the VOLT tests and
     the frontend build, opens a PR, and reports every step back here. Only
     the run that holds the lease can move the job.
  4. Either principal approves the merge for the EXACT tested commit. The
     runner merges it only if the PR head is still that commit and the scope
     check still passes. Render deploys main.
  5. The runner confirms the deploy against what is actually running (the
     backend's /version, the frontend's build-info.json) - not a timer.
  6. Rollback is a revert job: same path, same checks.

WHO
---
A principal is an active login that is either the platform owner (god_admin)
or named in the deployment's VOLT_DEV_PRINCIPALS AND an active VOLT org admin
of this tenant. Nothing here grants access to any other workspace; the runner
cannot merge a change outside the VOLT file scope.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.volt_models import VoltDevEvent, VoltDevJob, VoltDevNonce, VoltDevToken, VoltProgram
from app.services.volt import common as C

REPO = os.environ.get("VOLT_DEV_REPO", "MikeSimmonsAI/advisorflow-web")
WORKFLOW = ".github/workflows/volt-dev.yml"
AUDIENCE = "volt-dev-relay"
ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = ISSUER + "/.well-known/jwks"
LEASE = timedelta(minutes=60)

# Files a relay change may touch. Anything else makes the job fail closed.
SCOPE = (
    re.compile(r"^app/services/volt/"),
    re.compile(r"^app/routers/volt_router\.py$"),
    re.compile(r"^frontend/src/pages/volt/"),
    re.compile(r"^frontend/public/brands/volt/"),
    re.compile(r"^tests/test_volt[^/]*\.py$"),
    re.compile(r"^docs/volt/"),
)

STATUS_LABELS = {
    "queued": "Submitted", "claude_working": "Running - Claude working", "coding_completed": "Running - testing",
    "tests_failed": "Tests failed", "ready_to_test": "PR ready - tests passed", "merge_requested": "Approved - merging",
    "merged": "Merged - deploying", "deployed": "Deployed - verified running", "failed": "Failed",
    "canceled": "Canceled",
}


def display_status(job) -> str:
    """The truthful one-line state, including whether the runner was asked."""
    if job.status in ("queued", "merge_requested") and job.lease_run_id is None:
        base = STATUS_LABELS[job.status]
        if job.dispatch_state == "dispatched":
            return base + " - dispatched, waiting for the runner to start"
        if job.dispatch_state == "failed":
            return base + " - start request failed; it will be re-sent"
        if job.dispatch_state == "not_configured":
            return base + " - waiting for the scheduled check (instant start not set up)"
        return base
    return STATUS_LABELS.get(job.status, job.status)
TERMINAL = ("deployed", "failed", "canceled", "tests_failed")
# What the runner may report, from which states.
RUNNER_MOVES = {
    "claude_working": ("queued",),
    "coding_completed": ("claude_working",),
    "tests_failed": ("claude_working", "coding_completed"),
    "ready_to_test": ("coding_completed", "claude_working"),
    "failed": ("queued", "claude_working", "coding_completed", "merge_requested"),
    "merged": ("merge_requested",),
    "deployed": ("merged",),
}


def _err(status: int, msg: str, code: str):
    raise HTTPException(status_code=status, detail={"message": msg, "code": code})


# ── principals ──────────────────────────────────────────────────────────────

def named_principals() -> List[str]:
    return [e.strip().lower() for e in (os.environ.get("VOLT_DEV_PRINCIPALS") or "").split(",") if e.strip()]


def is_principal(db: Session, user: Optional[User], org_id: str) -> bool:
    if user is None or not getattr(user, "is_active", False):
        return False
    if user.role == "god_admin":
        return True
    if (user.email or "").lower() not in named_principals():
        return False
    from app.services.volt import access
    return access.actor_level(db, user, org_id) in ("owner", "admin")


def require_principal(db: Session, user: User, org_id: str) -> VoltProgram:
    prog = C.program_for_org(db, org_id)
    if not is_principal(db, user, org_id):
        _err(403, "VOLT development is limited to its two authorized people.", "not_principal")
    return prog


# ── personal access keys (ChatGPT action) ───────────────────────────────────

def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_token(db: Session, org_id: str, user: User, name: str) -> Dict[str, Any]:
    raw = "vdt_" + secrets.token_urlsafe(32)
    t = VoltDevToken(organization_id=org_id, user_id=user.id, name=(name or "ChatGPT").strip()[:80] or "ChatGPT",
                     prefix=raw[:10], token_hash=_hash(raw))
    db.add(t)
    db.flush()
    _audit_user(db, None, user, "token_created", detail="%s (%s…)" % (t.name, t.prefix))
    return {"id": t.id, "name": t.name, "prefix": t.prefix, "token": raw}


def token_user(db: Session, raw: str) -> tuple:
    """(user, org_id) for a valid, unrevoked key whose owner is still a principal."""
    if not raw or not (raw.startswith("vdt_") or raw.startswith("vdo_")):
        _err(401, "Sign in to VOLT development first (Connect ChatGPT), or use a personal access key.", "bad_token")
    t = db.query(VoltDevToken).filter(VoltDevToken.token_hash == _hash(raw)).first()
    if t is None or t.revoked_at is not None:
        _err(401, "This connection or key is not valid (it may have been revoked). Connect again.", "bad_token")
    if (t.kind or "key") == "oauth" and (t.expires_at is None or t.expires_at < C.now()):
        _err(401, "This sign-in expired. ChatGPT renews it automatically; if not, connect again.", "token_expired")
    user = db.get(User, t.user_id)
    if not is_principal(db, user, t.organization_id):
        _err(403, "This key's owner is no longer authorized for VOLT development.", "not_principal")
    t.last_used_at = C.now()
    return user, t.organization_id


def list_tokens(db: Session, org_id: str, user: User) -> List[Dict[str, Any]]:
    rows = (db.query(VoltDevToken).filter(VoltDevToken.organization_id == org_id, VoltDevToken.user_id == user.id)
            .order_by(VoltDevToken.created_at.desc()).all())
    return [{"id": t.id, "name": t.name, "prefix": t.prefix, "kind": t.kind or "key",
             "created_at": C.iso(t.created_at), "last_used_at": C.iso(t.last_used_at),
             "revoked": t.revoked_at is not None} for t in rows]


def revoke_token(db: Session, org_id: str, user: User, token_id: str) -> None:
    t = db.get(VoltDevToken, token_id)
    if t is None or t.organization_id != org_id or t.user_id != user.id:
        _err(404, "Key not found.", "not_found")
    if t.revoked_at is None:
        t.revoked_at = C.now()
        _audit_user(db, None, user, "token_revoked", detail=t.prefix)


# ── jobs ────────────────────────────────────────────────────────────────────

def _audit_user(db, job: Optional[VoltDevJob], user: Optional[User], action: str, status: Optional[str] = None,
                detail: Optional[str] = None, kind: str = "user") -> None:
    if job is None:
        return                                 # key events are recorded on the person's next job view
    db.add(VoltDevEvent(job_id=job.id, actor_user_id=user.id if user else None, actor_kind=kind,
                        action=action, status=status, detail=(detail or None) and str(detail)[:4000]))


def _set(db, job: VoltDevJob, status: str, detail: Optional[str], *, user=None, kind="user", action=None):
    job.status, job.status_detail = status, (detail or None) and str(detail)[:4000]
    _audit_user(db, job, user, action or ("status:" + status), status=status, detail=detail, kind=kind)


def _next_number(db, org_id: str) -> int:
    return (db.query(func.max(VoltDevJob.number)).filter(VoltDevJob.organization_id == org_id).scalar() or 0) + 1


def submit(db: Session, org_id: str, user: User, body: Dict[str, Any], via: str = "console") -> tuple:
    title = str(body.get("title") or "").strip()
    request = str(body.get("request") or "").strip()
    if not title or len(title) > 140:
        _err(422, "Give the change a short title (up to 140 characters).", "bad_title")
    if len(request) < 10 or len(request) > 8000:
        _err(422, "Describe the change in 10 to 8000 characters.", "bad_request")
    key = str(body.get("idempotency_key") or "").strip()[:80] or None
    if key:
        found = (db.query(VoltDevJob).filter(VoltDevJob.organization_id == org_id, VoltDevJob.requested_by == user.id,
                                             VoltDevJob.idempotency_key == key).first())
        if found is not None:
            return found, False                 # the same submission again: one job
    job = VoltDevJob(organization_id=org_id, number=_next_number(db, org_id), kind="change", title=title,
                     request=request, requested_by=user.id, via=via, idempotency_key=key, status="queued")
    db.add(job)
    db.flush()
    _audit_user(db, job, user, "submitted", status="queued", detail="via %s" % via)
    return job, True


def by_ref(db: Session, org_id: str, ref: str) -> VoltDevJob:
    ref = str(ref or "").strip().upper().replace("DEV-", "")
    q = db.query(VoltDevJob).filter(VoltDevJob.organization_id == org_id)
    job = q.filter(VoltDevJob.number == int(ref)).first() if ref.isdigit() else q.filter(VoltDevJob.id == ref.lower()).first()
    if job is None:
        _err(404, "Development job not found.", "not_found")
    return job


def retry(db: Session, job: VoltDevJob, user: User) -> VoltDevJob:
    if job.status not in ("failed", "tests_failed", "canceled"):
        _err(409, "Only a failed or canceled job can be retried.", "not_retryable")
    job.attempt += 1
    job.lease_run_id = job.lease_expires_at = None
    job.tests_passed = job.tests_summary = None
    job.pr_number = job.pr_url = job.head_sha = job.branch = job.preview_url = None
    job.merge_requested_by = job.merge_requested_at = job.merge_approved_sha = None
    _set(db, job, "queued", "Retry #%d" % job.attempt, user=user, action="retry")
    return job


def cancel(db: Session, job: VoltDevJob, user: User) -> VoltDevJob:
    if job.status not in ("queued", "ready_to_test", "tests_failed"):
        _err(409, "Only a queued or not-yet-merged job can be canceled.", "not_cancelable")
    job.lease_run_id = job.lease_expires_at = None
    _set(db, job, "canceled", "Canceled", user=user, action="cancel")
    return job


def _sha_matches(full: Optional[str], given: str) -> bool:
    g = (given or "").strip().lower()
    return bool(full) and len(g) >= 7 and re.fullmatch(r"[0-9a-f]{7,40}", g) is not None and full.lower().startswith(g)


def approve_merge(db: Session, job: VoltDevJob, user: User, expected_sha: Optional[str] = None) -> VoltDevJob:
    """Approve shipping THIS tested commit. The runner merges it only if the PR
    still points at exactly this commit. `expected_sha` (required from ChatGPT)
    is the commit the person reviewed; anything else is refused."""
    if expected_sha is not None and not _sha_matches(job.merge_approved_sha if job.status == "merge_requested"
                                                     else job.head_sha, expected_sha):
        _err(409, "DEV-%d's tested commit is %s, not %s. Review the current result before approving."
             % (job.number, (job.head_sha or "none")[:12], str(expected_sha)[:12]), "commit_mismatch")
    if job.status == "merge_requested":
        return job
    if job.status != "ready_to_test" or not job.tests_passed or not job.head_sha or not job.pr_number:
        _err(409, "Only a job whose tests passed and whose PR is open can be merged.", "not_ready")
    job.merge_requested_by, job.merge_requested_at, job.merge_approved_sha = user.id, C.now(), job.head_sha
    _set(db, job, "merge_requested", "Approved for production at %s" % job.head_sha[:7], user=user,
         action="approve_merge")
    return job


def request_revert(db: Session, org_id: str, job: VoltDevJob, user: User) -> VoltDevJob:
    if job.status not in ("merged", "deployed") or not job.merge_sha:
        _err(409, "Only a merged change can be rolled back.", "not_revertable")
    existing = (db.query(VoltDevJob).filter(VoltDevJob.revert_of == job.id,
                                            VoltDevJob.status.notin_(("failed", "canceled"))).first())
    if existing is not None:
        return existing
    rj = VoltDevJob(organization_id=org_id, number=_next_number(db, org_id), kind="revert",
                    title="Roll back DEV-%d: %s" % (job.number, job.title)[:140],
                    request="Revert merge commit %s (DEV-%d)." % (job.merge_sha, job.number),
                    requested_by=user.id, via="console", status="queued", revert_of=job.id)
    db.add(rj)
    db.flush()
    _audit_user(db, rj, user, "rollback_requested", status="queued", detail="of DEV-%d" % job.number)
    return rj


def job_dict(db: Session, job: VoltDevJob, events: bool = False) -> Dict[str, Any]:
    who = db.get(User, job.requested_by) if job.requested_by else None
    appr = db.get(User, job.merge_requested_by) if job.merge_requested_by else None
    out = {"id": job.id, "ref": "DEV-%d" % job.number, "number": job.number, "kind": job.kind, "title": job.title,
           "request": job.request, "status": job.status, "status_label": display_status(job),
           "dispatch": {"state": job.dispatch_state, "purpose": job.dispatch_purpose,
                        "at": C.iso(job.dispatched_at), "error": job.dispatch_error, "count": job.dispatch_count or 0},
           "status_detail": job.status_detail, "attempt": job.attempt, "via": job.via,
           "requested_by": (who.full_name or who.email) if who else None,
           "created_at": C.iso(job.created_at), "updated_at": C.iso(job.updated_at),
           "branch": job.branch, "pr_number": job.pr_number, "pr_url": job.pr_url, "head_sha": job.head_sha,
           "tests_passed": job.tests_passed, "tests_summary": job.tests_summary,
           "changed_files": json.loads(job.changed_files) if job.changed_files else [],
           "preview_url": job.preview_url, "run_url": job.run_url,
           "merge_approved_by": (appr.full_name or appr.email) if appr else None,
           "merge_requested_at": C.iso(job.merge_requested_at), "merge_sha": job.merge_sha,
           "merged_at": C.iso(job.merged_at), "deploy_targets": job.deploy_targets,
           "deployed_at": C.iso(job.deployed_at), "deploy_detail": job.deploy_detail,
           "revert_of": job.revert_of,
           "can": {"approve_merge": job.status == "ready_to_test" and bool(job.tests_passed),
                   "retry": job.status in ("failed", "tests_failed", "canceled"),
                   "cancel": job.status in ("queued", "ready_to_test", "tests_failed"),
                   "rollback": job.status in ("merged", "deployed") and bool(job.merge_sha)}}
    if events:
        names = {}
        rows = db.query(VoltDevEvent).filter(VoltDevEvent.job_id == job.id).order_by(VoltDevEvent.at.asc()).all()
        for e in rows:
            if e.actor_user_id and e.actor_user_id not in names:
                u = db.get(User, e.actor_user_id)
                names[e.actor_user_id] = (u.full_name or u.email) if u else "Someone"
        out["events"] = [{"at": C.iso(e.at), "action": e.action, "status": e.status, "detail": e.detail,
                          "actor": names.get(e.actor_user_id) if e.actor_kind == "user" else
                          ("GitHub runner" if e.actor_kind == "runner" else "System")} for e in rows]
    return out


def list_jobs(db: Session, org_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = (db.query(VoltDevJob).filter(VoltDevJob.organization_id == org_id)
            .order_by(VoltDevJob.number.desc()).limit(max(1, min(limit, 200))).all())
    return [job_dict(db, j) for j in rows]


# ── the runner (GitHub Actions) ─────────────────────────────────────────────

_JWKS = None


def verify_runner(db: Session, bearer: str) -> Dict[str, Any]:
    """Verify a GitHub Actions OIDC token: signed by GitHub, for this audience,
    from this repository's volt-dev workflow on main, never seen before."""
    import jwt
    global _JWKS
    if not bearer:
        _err(401, "Runner identity token required.", "runner_auth")
    try:
        if _JWKS is None:
            _JWKS = jwt.PyJWKClient(JWKS_URL, cache_keys=True, lifespan=3600)
        key = _JWKS.get_signing_key_from_jwt(bearer).key
        claims = jwt.decode(bearer, key, algorithms=["RS256"], audience=AUDIENCE, issuer=ISSUER,
                            options={"require": ["exp", "iat", "jti", "repository", "job_workflow_ref"]})
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - any verification failure is a refusal
        _err(401, "Runner identity could not be verified (%s)." % type(e).__name__, "runner_auth")
    return check_claims(db, claims)


def check_claims(db: Session, claims: Dict[str, Any]) -> Dict[str, Any]:
    want_wf = "%s/%s@refs/heads/main" % (REPO, WORKFLOW)
    if claims.get("repository") != REPO or claims.get("job_workflow_ref") != want_wf \
            or claims.get("ref") != "refs/heads/main" \
            or claims.get("event_name") not in ("schedule", "workflow_dispatch"):
        _err(403, "This runner is not the VOLT development workflow on main.", "runner_scope")
    jti = str(claims.get("jti") or "")
    if not jti or db.get(VoltDevNonce, jti) is not None:
        _err(409, "This runner token was already used.", "runner_replay")
    db.add(VoltDevNonce(jti=jti))
    db.query(VoltDevNonce).filter(VoltDevNonce.used_at < datetime.utcnow() - timedelta(days=2)).delete(
        synchronize_session=False)
    db.flush()
    return claims


def _expire_leases(db: Session) -> None:
    now = C.now()
    for j in (db.query(VoltDevJob).filter(VoltDevJob.status.in_(("claude_working", "coding_completed")),
                                          VoltDevJob.lease_expires_at < now).all()):
        j.lease_run_id = j.lease_expires_at = None
        _set(db, j, "failed", "The runner stopped before finishing (lease expired). Retry to run it again.",
             kind="system", action="lease_expired")


def runner_next(db: Session, claims: Dict[str, Any]) -> Dict[str, Any]:
    """Hand the runner ONE thing to do: merges first, then the oldest queued
    job (VOLT tenants only). Plus the merged jobs awaiting deploy proof."""
    _expire_leases(db)
    run_id = str(claims.get("run_id") or "")
    run_url = "https://github.com/%s/actions/runs/%s" % (REPO, run_id) if run_id else None
    deploy_watch = [{"id": j.id, "ref": "DEV-%d" % j.number, "merge_sha": j.merge_sha,
                     "targets": j.deploy_targets or ""}
                    for j in db.query(VoltDevJob).filter(VoltDevJob.status == "merged").all()]
    def _more(exclude):
        return (db.query(VoltDevJob).filter(VoltDevJob.status.in_(("queued", "merge_requested")),
                                            VoltDevJob.lease_run_id.is_(None), VoltDevJob.id != exclude).count())
    m = (db.query(VoltDevJob).filter(VoltDevJob.status == "merge_requested", VoltDevJob.lease_run_id.is_(None))
         .order_by(VoltDevJob.merge_requested_at.asc()).first())
    if m is not None:
        m.lease_run_id, m.lease_expires_at = run_id, C.now() + LEASE
        _audit_user(db, m, None, "runner_merging", status=m.status, detail=run_url, kind="runner")
        return {"action": "merge", "job": _runner_job(m), "deploy_watch": deploy_watch, "more": _more(m.id)}
    q = (db.query(VoltDevJob).filter(VoltDevJob.status == "queued").order_by(VoltDevJob.created_at.asc())
         .with_for_update(skip_locked=True).first())
    if q is not None:
        q.lease_run_id, q.lease_expires_at, q.run_url = run_id, C.now() + LEASE, run_url
        q.branch = "volt-dev/dev-%d-a%d" % (q.number, q.attempt)
        if q.kind == "revert":
            orig = db.get(VoltDevJob, q.revert_of)
            payload = dict(_runner_job(q), revert_sha=orig.merge_sha if orig else None)
            _set(db, q, "claude_working", "Preparing the rollback", kind="runner", action="runner_claimed")
            return {"action": "revert", "job": payload, "deploy_watch": deploy_watch, "more": _more(q.id)}
        _set(db, q, "claude_working", "The runner started; Claude is working on it", kind="runner",
             action="runner_claimed")
        return {"action": "code", "job": _runner_job(q), "deploy_watch": deploy_watch, "more": _more(q.id)}
    return {"action": "none", "deploy_watch": deploy_watch, "more": 0}


def _runner_job(j: VoltDevJob) -> Dict[str, Any]:
    return {"id": j.id, "ref": "DEV-%d" % j.number, "kind": j.kind, "title": j.title, "request": j.request,
            "branch": j.branch, "pr_number": j.pr_number, "approved_sha": j.merge_approved_sha,
            "attempt": j.attempt}


def in_scope(paths: List[str]) -> List[str]:
    """The paths that are NOT allowed (empty list = in scope)."""
    return [p for p in paths if not any(rx.match(p) for rx in SCOPE)]


def deploy_targets(paths: List[str]) -> str:
    t = []
    if any(p.startswith("app/") or p.startswith("requirements") for p in paths):
        t.append("backend")
    if any(p.startswith("frontend/") and not p.startswith("frontend/dist/") for p in paths):
        t.append("frontend")
    return ",".join(t)


def runner_report(db: Session, claims: Dict[str, Any], job_id: str, body: Dict[str, Any]) -> tuple:
    """Apply one progress report. Returns (job, new_status_or_None)."""
    job = db.get(VoltDevJob, job_id)
    if job is None:
        _err(404, "Job not found.", "not_found")
    run_id = str(claims.get("run_id") or "")
    status = str(body.get("status") or "")
    if job.status == status:
        return job, None                           # a repeated report is a no-op
    if status == "deployed":
        if job.status != "merged":
            return job, None                       # already recorded: a repeat is a no-op
    elif job.lease_run_id != run_id:
        _err(409, "This run does not hold the job.", "not_leaseholder")
    if status not in RUNNER_MOVES:
        _err(422, "Unknown status.", "bad_status")
    if job.status == status:
        return job, None                           # duplicate report
    if job.status not in RUNNER_MOVES[status]:
        _err(409, "Can't move %s from %s to %s." % ("DEV-%d" % job.number, job.status, status), "bad_transition")
    files = body.get("changed_files")
    if isinstance(files, list):
        files = [str(f) for f in files][:500]
        job.changed_files = json.dumps(files)
        bad = in_scope(files)
        if bad and status not in ("failed", "tests_failed"):
            _err(409, "Changes outside VOLT scope: %s" % ", ".join(bad[:5]), "out_of_scope")
        job.deploy_targets = deploy_targets(files)
    for k in ("branch", "pr_url", "head_sha", "preview_url", "tests_summary", "merge_sha"):
        if body.get(k):
            setattr(job, k, str(body[k])[:4000])
    if body.get("pr_number"):
        job.pr_number = int(body["pr_number"])
    if "tests_passed" in body:
        job.tests_passed = bool(body["tests_passed"])
    if status == "ready_to_test" and not (job.tests_passed and job.pr_number and job.head_sha):
        _err(409, "Ready to test needs passing tests, a PR and its commit.", "not_ready")
    if status == "merged":
        if not job.merge_sha or body.get("merged_head_sha") != job.merge_approved_sha:
            _err(409, "Merged commit does not match the approved commit.", "sha_mismatch")
        job.merged_at = C.now()
        if not job.deploy_targets:
            pass
    if status == "deployed":
        job.deployed_at = C.now()
        job.deploy_detail = str(body.get("deploy_detail") or "")[:1000] or None
    if status in ("ready_to_test", "tests_failed", "failed", "merged", "deployed"):
        job.lease_run_id = job.lease_expires_at = None
    _set(db, job, status, body.get("detail") or STATUS_LABELS.get(status), kind="runner",
         action="runner:" + status)
    if status == "merged" and not job.deploy_targets:
        job.deployed_at = C.now()
        job.deploy_detail = "No running service changed (tests/docs only)."
        _set(db, job, "deployed", job.deploy_detail, kind="system", action="no_deploy_needed")
        status = "deployed"
    return job, status


# ── notifications ───────────────────────────────────────────────────────────

NOTIFY = {"ready_to_test": "is ready to test", "tests_failed": "failed its tests", "failed": "failed",
          "merged": "was merged and is deploying", "deployed": "is live in production"}


def notify_list() -> List[str]:
    raw = os.environ.get("VOLT_DEV_NOTIFY") or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def notify(db: Session, job: VoltDevJob, status: str) -> List[Any]:
    """Email both principals at the moments that need them. One email per
    job, status and attempt (the mail queue dedupes on that key)."""
    if status not in NOTIFY:
        return []
    from app.services.volt import mail
    prog = C.program_for_org(db, job.organization_id)
    link = "%s/volt-ops/%s/dev?job=%s" % ("https://app.evosyspro.live", job.organization_id, job.number)
    paras = ["<b>DEV-%d: %s</b> %s." % (job.number, mail._e(job.title), NOTIFY[status])]
    if job.status_detail:
        paras.append(mail._e(job.status_detail))
    if status == "ready_to_test":
        paras.append("Tests passed. Review the change and approve it for production in VOLT Operations → Development.")
    if status == "deployed" and job.deploy_detail:
        paras.append(mail._e(job.deploy_detail))
    rows = []
    for to in notify_list():
        rows.append(mail.queue(db, prog, kind="dev_" + status, key="dev:%s:%s:%d:%s" % (job.id, status, job.attempt, to),
                               to=to, subject="VOLT DEV-%d %s" % (job.number, NOTIFY[status]),
                               html=mail._page(prog, "VOLT development update", paras, ("Open Development", link))))
    return [r for r in rows if r is not None]
