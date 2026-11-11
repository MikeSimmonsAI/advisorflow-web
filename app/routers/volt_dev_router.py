"""VOLT development relay endpoints (Issue #571).

    Console  /volt/dev/{org_id}/...      signed-in principal (EvoSys login)
    API      /volt/dev/api/...           principal's personal access key
                                         (ChatGPT custom action; OpenAPI at
                                         /volt/dev/api/openapi.json)
    Runner   /volt/dev/runner/...        GitHub Actions OIDC identity token

See app/services/volt/devrelay.py for the rules.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import User
from app.limiter import limiter
from app.services.volt import devdispatch as D
from app.services.volt import devrelay as R
from app.services.volt import mail

console = APIRouter(prefix="/volt/dev", tags=["volt-dev"])
api = APIRouter(prefix="/volt/dev/api", tags=["volt-dev-api"])
runner = APIRouter(prefix="/volt/dev/runner", tags=["volt-dev-runner"])


def _deliver(db, rows):
    for r in rows or []:
        try:
            mail.deliver(db, r)
        except Exception:  # noqa: BLE001 - a notice never breaks the relay
            pass


# ── console (signed-in principal) ───────────────────────────────────────────

@console.get("/{org_id}/me")
def dev_me(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return {"principal": R.is_principal(db, user, org_id), "name": user.full_name or user.email}


def _refresh(db, org_id):
    """On every look at the jobs: re-send a missed start, and check whether
    merged work is actually running in production."""
    rows = []
    for j in D.verify_deploys(db, org_id):
        rows += R.notify(db, j, "deployed")
    D.resend_stale(db, org_id)
    db.commit()
    _deliver(db, rows)


@console.get("/{org_id}/jobs")
def dev_jobs(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    R.require_principal(db, user, org_id)
    _refresh(db, org_id)
    return {"items": R.list_jobs(db, org_id), "statuses": R.STATUS_LABELS, "instant_start": D.configured()}


@console.post("/{org_id}/jobs")
def dev_submit(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    R.require_principal(db, user, org_id)
    job, created = R.submit(db, org_id, user, body, via="console")
    db.commit()
    if created:
        D.dispatch(db, job, "start")
        db.commit()
    return dict(R.job_dict(db, job, events=True), created=created)


@console.get("/{org_id}/jobs/{ref}")
def dev_job(org_id: str, ref: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    R.require_principal(db, user, org_id)
    _refresh(db, org_id)
    return R.job_dict(db, R.by_ref(db, org_id, ref), events=True)


@console.post("/{org_id}/jobs/{ref}/{action}")
def dev_action(org_id: str, ref: str, action: str, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    R.require_principal(db, user, org_id)
    return _act(db, org_id, ref, action, user)


def _act(db, org_id, ref, action, user, expected_sha=None):
    job = R.by_ref(db, org_id, ref)
    before = job.status
    if action == "approve-merge":
        out, purpose = R.approve_merge(db, job, user, expected_sha), "merge"
    elif action == "retry":
        out, purpose = R.retry(db, job, user), "retry"
    elif action == "cancel":
        out, purpose = R.cancel(db, job, user), None
    elif action == "rollback":
        out, purpose = R.request_revert(db, org_id, job, user), "rollback"
    elif action == "dispatch":
        out, purpose = job, ("merge" if job.status == "merge_requested" else "resend")
        if not D.needs_runner(job):
            raise HTTPException(status_code=409, detail={"message": "This job is not waiting for the runner.",
                                                         "code": "not_waiting"})
    else:
        raise HTTPException(status_code=404, detail="Unknown action.")
    db.commit()
    # Start the runner only when this call actually changed something (a
    # repeated approve/rollback is a no-op and sends nothing).
    changed = action == "dispatch" or out.id != job.id or out.status != before
    if purpose and changed and D.needs_runner(out):
        D.dispatch(db, out, purpose)
        db.commit()
    return R.job_dict(db, out, events=True)


@console.get("/{org_id}/tokens")
def dev_tokens(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    R.require_principal(db, user, org_id)
    return {"items": R.list_tokens(db, org_id, user)}


@console.post("/{org_id}/tokens")
def dev_token_new(org_id: str, body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    R.require_principal(db, user, org_id)
    out = R.create_token(db, org_id, user, (body or {}).get("name") or "ChatGPT")
    db.commit()
    return out


@console.delete("/{org_id}/tokens/{token_id}")
def dev_token_revoke(org_id: str, token_id: str, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    R.require_principal(db, user, org_id)
    R.revoke_token(db, org_id, user, token_id)
    db.commit()
    return {"revoked": True}


# ── API for the ChatGPT action (personal access key) ────────────────────────

def _key_user(authorization: str = Header(default="")):
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


@api.get("/openapi.json", include_in_schema=False)
def openapi_doc(request: Request):
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[len("http://"):]
    return {
        "openapi": "3.1.0",
        "info": {"title": "VOLT Marketing development", "version": "1.0",
                 "description": "Submit and track VOLT Marketing development requests. Every call acts as the "
                                "person who owns the access key."},
        "servers": [{"url": base}],
        "components": {"securitySchemes": {
                           "key": {"type": "http", "scheme": "bearer",
                                   "description": "A VOLT sign-in token (Connect ChatGPT) or a personal access key."},
                           "volt": {"type": "oauth2", "flows": {"authorizationCode": {
                               "authorizationUrl": base + "/volt/dev/oauth/authorize",
                               "tokenUrl": base + "/volt/dev/oauth/token",
                               "refreshUrl": base + "/volt/dev/oauth/token",
                               "scopes": {"volt.dev": "VOLT Marketing development as you"}}}}},
                       "schemas": {"Job": {"type": "object", "properties": {
                           "ref": {"type": "string"}, "title": {"type": "string"}, "status": {"type": "string"},
                           "status_label": {"type": "string"}, "status_detail": {"type": "string"},
                           "pr_url": {"type": "string"}, "tests_summary": {"type": "string"},
                           "preview_url": {"type": "string"}}}}},
        "security": [{"volt": ["volt.dev"]}, {"key": []}],
        "paths": {
            "/volt/dev/api/jobs": {
                "get": {"operationId": "listJobs", "summary": "List recent VOLT development jobs and their status",
                        "responses": {"200": {"description": "Jobs"}}},
                "post": {"operationId": "submitJob", "summary": "Submit a VOLT Marketing change request",
                         "requestBody": {"required": True, "content": {"application/json": {"schema": {
                             "type": "object", "required": ["title", "request"], "properties": {
                                 "title": {"type": "string", "description": "Short title, up to 140 characters"},
                                 "request": {"type": "string", "description": "What to change, in detail"},
                                 "idempotency_key": {"type": "string",
                                                     "description": "Any unique text; resending the same key returns the same job"}}}}}},
                         "responses": {"200": {"description": "The job"}}}},
            "/volt/dev/api/jobs/{ref}": {
                "get": {"operationId": "getJob", "summary": "Status and timeline of one job (DEV-n)",
                        "parameters": [{"name": "ref", "in": "path", "required": True, "schema": {"type": "string"}}],
                        "responses": {"200": {"description": "The job"}}}},
            "/volt/dev/api/status": {
                "get": {"operationId": "relayStatus", "summary": "Who you are and whether the GitHub connection works",
                        "responses": {"200": {"description": "Status"}}}},
            "/volt/dev/api/jobs/{ref}/review": {
                "get": {"operationId": "getJobReview",
                        "summary": "Changed files with their diff and test results at the tested commit (review before approving)",
                        "parameters": [{"name": "ref", "in": "path", "required": True, "schema": {"type": "string"}}],
                        "responses": {"200": {"description": "Review material"}}}},
            "/volt/dev/api/jobs/{ref}/{action}": {
                "post": {"operationId": "jobAction",
                         "summary": "approve-merge (ship the tested change to production - ONLY after the person confirms the job and tested commit), retry, cancel, rollback, or dispatch (re-send a start request)",
                         "x-openai-isConsequential": True,
                         "parameters": [{"name": "ref", "in": "path", "required": True, "schema": {"type": "string"}},
                                        {"name": "action", "in": "path", "required": True, "schema": {
                                            "type": "string", "enum": ["approve-merge", "retry", "cancel", "rollback", "dispatch"]}}],
                         "requestBody": {"required": False, "content": {"application/json": {"schema": {
                             "type": "object", "properties": {
                                 "tested_commit": {"type": "string", "description": "approve-merge only: the job's tested commit SHA the person confirmed"},
                                 "confirm_production": {"type": "boolean", "description": "approve-merge only: true after the person confirmed this exact release"}}}}}},
                         "responses": {"200": {"description": "The job"}}}},
        },
    }


@api.get("/jobs")
@limiter.limit("60/minute")
def api_jobs(request: Request, db: Session = Depends(get_db), raw: str = Depends(_key_user)):
    user, org_id = R.token_user(db, raw)
    db.commit()
    _refresh(db, org_id)
    return {"items": R.list_jobs(db, org_id, 20)}


@api.post("/jobs")
@limiter.limit("20/minute")
def api_submit(request: Request, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
               raw: str = Depends(_key_user), idempotency_key: str = Header(default="")):
    user, org_id = R.token_user(db, raw)
    if idempotency_key and not body.get("idempotency_key"):
        body = dict(body, idempotency_key=idempotency_key)
    job, created = R.submit(db, org_id, user, body, via="api")
    db.commit()
    if created:
        D.dispatch(db, job, "start")
        db.commit()
    return dict(R.job_dict(db, job, events=True), created=created)


@api.get("/jobs/{ref}")
@limiter.limit("120/minute")
def api_job(request: Request, ref: str, db: Session = Depends(get_db), raw: str = Depends(_key_user)):
    user, org_id = R.token_user(db, raw)
    db.commit()
    _refresh(db, org_id)
    return R.job_dict(db, R.by_ref(db, org_id, ref), events=True)


@api.get("/status")
@limiter.limit("30/minute")
def api_status(request: Request, db: Session = Depends(get_db), raw: str = Depends(_key_user)):
    user, org_id = R.token_user(db, raw)
    db.commit()
    return {"you": user.full_name or user.email, "email": user.email, "volt_development_principal": True,
            "instant_start_configured": D.configured(), "github": D.check_github()}


@api.get("/jobs/{ref}/review")
@limiter.limit("60/minute")
def api_review(request: Request, ref: str, db: Session = Depends(get_db), raw: str = Depends(_key_user)):
    user, org_id = R.token_user(db, raw)
    db.commit()
    job = R.by_ref(db, org_id, ref)
    j = R.job_dict(db, job)
    return {"ref": j["ref"], "status_label": j["status_label"], "tests_passed": j["tests_passed"],
            "tests_summary": j["tests_summary"], "head_sha": job.head_sha, "review": D.review(job)}


@api.post("/jobs/{ref}/{action}")
@limiter.limit("30/minute")
def api_action(request: Request, ref: str, action: str, body: Dict[str, Any] = Body(default={}),
               db: Session = Depends(get_db), raw: str = Depends(_key_user)):
    user, org_id = R.token_user(db, raw)
    sha = None
    if action == "approve-merge":
        # From outside VOLT Operations, approval names the exact commit reviewed.
        sha = str((body or {}).get("tested_commit") or "").strip()
        if len(sha) < 7 or (body or {}).get("confirm_production") is not True:
            raise HTTPException(status_code=422, detail={
                "message": "To release, send tested_commit (the job's tested commit) and confirm_production: true, "
                           "after the person confirmed this exact release.", "code": "confirmation_required"})
    return _act(db, org_id, ref, action, user, expected_sha=sha)


# ── runner (GitHub Actions OIDC) ────────────────────────────────────────────

def _claims(db, authorization: str):
    tok = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    return R.verify_runner(db, tok)


@runner.post("/next")
def runner_next(db: Session = Depends(get_db), authorization: str = Header(default="")):
    claims = _claims(db, authorization)
    out = R.runner_next(db, claims)
    db.commit()
    return out


@runner.post("/jobs/{job_id}/report")
def runner_report(job_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                  authorization: str = Header(default="")):
    claims = _claims(db, authorization)
    job, status = R.runner_report(db, claims, job_id, body)
    rows = R.notify(db, job, status) if status else []
    if status == "deployed" and job.revert_of is None:
        pass
    db.commit()
    _deliver(db, rows)
    return {"ok": True, "status": job.status}
