"""ChatGPT ↔ VOLT development relay (Issue #571, 2026-11-10).

    /.well-known/oauth-authorization-server        OAuth metadata (RFC 8414)
    /.well-known/oauth-protected-resource[/...]    MCP resource metadata (RFC 9728)
    /volt/dev/oauth/register                       ChatGPT registers itself (RFC 7591)
    /volt/dev/oauth/authorize                      -> voltmarketing.live/volt-dev/connect
    /volt/dev/oauth/request/{id}[/decision]        the consent page (signed-in EvoSys user)
    /volt/dev/oauth/token | /revoke                code + PKCE -> tokens; refresh; revoke
    /volt/dev/mcp                                  MCP server (Streamable HTTP, JSON responses)

The MCP tools are thin wrappers over the same functions the Development
screen and the OpenAPI action use (devrelay / devdispatch / volt_dev_router),
so every rule - principal check, idempotency, dispatch-once, exact-commit
approval, VOLT-only scope - is the same rule wherever the request comes from.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.limiter import limiter
from app.models.models import User
from app.services.volt import devdispatch as D
from app.services.volt import devoauth as O
from app.services.volt import devrelay as R

log = logging.getLogger(__name__)

wellknown = APIRouter(tags=["volt-dev-oauth"])
oauth = APIRouter(prefix="/volt/dev/oauth", tags=["volt-dev-oauth"])
mcp = APIRouter(prefix="/volt/dev", tags=["volt-dev-mcp"])

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


# ── discovery ──────────────────────────────────────────────────────────────

@wellknown.get("/.well-known/oauth-authorization-server", include_in_schema=False)
@wellknown.get("/.well-known/oauth-authorization-server/volt/dev/mcp", include_in_schema=False)
@wellknown.get("/.well-known/openid-configuration", include_in_schema=False)
def as_metadata():
    return O.server_metadata()


@wellknown.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@wellknown.get("/.well-known/oauth-protected-resource/volt/dev/mcp", include_in_schema=False)
def pr_metadata():
    return O.resource_metadata()


# ── OAuth ──────────────────────────────────────────────────────────────────

def _oauth_json(exc: HTTPException) -> JSONResponse:
    d = exc.detail if isinstance(exc.detail, dict) else {"error": "invalid_request", "error_description": str(exc.detail)}
    return JSONResponse(d, status_code=exc.status_code, headers=NO_STORE)


async def _form(request: Request) -> Dict[str, str]:
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            data = {}
        return {k: str(v) for k, v in (data or {}).items() if v is not None}
    form = await request.form()
    return {k: str(v) for k, v in form.items()}


def _basic(request: Request) -> Optional[tuple]:
    h = request.headers.get("authorization") or ""
    if not h.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(h[6:].strip()).decode()
        cid, _, secret = raw.partition(":")
        from urllib.parse import unquote
        return unquote(cid), unquote(secret)
    except Exception:  # noqa: BLE001
        return None


@oauth.post("/register")
@limiter.limit("10/minute")
async def register(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
        out = O.register_client(db, body if isinstance(body, dict) else {})
    except HTTPException as e:
        return _oauth_json(e)
    except ValueError:
        return JSONResponse({"error": "invalid_client_metadata", "error_description": "Send JSON."}, status_code=400)
    db.commit()
    return JSONResponse(out, status_code=201, headers=NO_STORE)


@oauth.get("/authorize")
@limiter.limit("30/minute")
def authorize(request: Request, db: Session = Depends(get_db)):
    try:
        url = O.start(db, dict(request.query_params))
    except HTTPException as e:
        return _oauth_json(e)
    db.commit()
    return RedirectResponse(url, status_code=302)


@oauth.get("/request/{request_id}")
def consent_info(request_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return O.describe(db, request_id, user)


@oauth.post("/request/{request_id}/decision")
@limiter.limit("20/minute")
def consent_decide(request: Request, request_id: str, body: Dict[str, Any] = Body(default={}),
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = O.decide(db, request_id, user, bool((body or {}).get("allow")))
    db.commit()
    return out


@oauth.post("/token")
@limiter.limit("60/minute")
async def token(request: Request, db: Session = Depends(get_db)):
    form = await _form(request)
    try:
        out = O.token(db, form, _basic(request))
    except HTTPException as e:
        db.commit()            # a replayed code's revocations must stick
        return _oauth_json(e)
    db.commit()
    return JSONResponse(out, headers=NO_STORE)


@oauth.post("/revoke")
@limiter.limit("30/minute")
async def revoke(request: Request, db: Session = Depends(get_db)):
    O.revoke(db, await _form(request))
    db.commit()
    return Response(status_code=200)


# ── MCP ────────────────────────────────────────────────────────────────────

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "VOLT Marketing development relay. Each call acts as the signed-in VOLT principal. "
    "Workflow: submit_change_request -> the backend immediately asks GitHub to start the runner -> "
    "Claude writes the change on its own branch -> tests and build run -> status 'ready_to_test' (PR ready). "
    "Use get_change_request to follow progress and get_change_review for the diff and test results. "
    "NEVER call approve_release unless the person has, in this conversation, explicitly confirmed shipping "
    "that exact job and tested commit to production after seeing them. 'Dispatched' only means GitHub "
    "accepted the start request; 'Running' means the runner claimed the job; 'Deployed - verified running' "
    "means production was checked. Report statuses exactly as returned; never claim more."
)


def _schema(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


REF = {"type": "string", "description": "The job reference, e.g. DEV-3 (or just 3)."}
TOOLS = [
    {"name": "relay_status", "title": "Who am I / is the relay working",
     "description": "Shows who you are signed in as, and whether the backend's GitHub connection works "
                    "(instant start). Starts nothing.",
     "inputSchema": _schema({}, []), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "my_latest_requests", "title": "My latest VOLT requests",
     "description": "The VOLT change requests YOU submitted, newest first, each with its stage, a plain summary, "
                    "the latest time and the next action. Use for 'show my latest VOLT requests'.",
     "inputSchema": _schema({}, []), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "list_change_requests", "title": "All VOLT change requests",
     "description": "Recent VOLT development jobs from both authorized people, with their stage.",
     "inputSchema": _schema({}, []), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "what_is_blocking", "title": "What is blocking a job",
     "description": "Why a job is not moving: its stage, the recorded blocker or failure reason, and what to do next.",
     "inputSchema": _schema({"ref": {"type": "string", "description": "The job reference, e.g. DEV-3."}}, ["ref"]),
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "submit_change_request", "title": "Submit a VOLT change request",
     "description": "Ask for a change to VOLT Marketing (recruiting site, VOLT Operations, VOLT emails). "
                    "Starts the runner immediately. Submitting the identical title and text again returns "
                    "the same job instead of making a duplicate.",
     "inputSchema": _schema({
         "title": {"type": "string", "maxLength": 140, "description": "Short title."},
         "request": {"type": "string", "minLength": 10, "maxLength": 8000,
                     "description": "What to change, in detail, including where it appears."},
         "idempotency_key": {"type": "string", "maxLength": 80,
                             "description": "Optional. Any unique text; the same key never makes a second job."}},
         ["title", "request"]),
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "get_change_request", "title": "Status of one change request",
     "description": "Check one job: stage, plain summary, latest time, next action, timeline, test results, "
                    "pull request, GitHub run and the tested commit. Use for 'check DEV-n'.",
     "inputSchema": _schema({"ref": REF}, ["ref"]), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "get_change_review", "title": "Review a change (diff and tests)",
     "description": "Show what changed (each file's diff), the test and build results, and the exact tested "
                    "commit - the material to present before asking for release approval. There is no live "
                    "preview site for VOLT branches; this and the pull request are the review.",
     "inputSchema": _schema({"ref": REF}, ["ref"]), "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "retry_change_request", "title": "Retry a failed change request",
     "description": "Run a failed, test-failed or canceled job again (starts the runner).",
     "inputSchema": _schema({"ref": REF}, ["ref"]),
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "cancel_change_request", "title": "Cancel a change request",
     "description": "Cancel a queued or not-yet-approved job.",
     "inputSchema": _schema({"ref": REF}, ["ref"]),
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}},
    {"name": "resend_start", "title": "Send the start request again",
     "description": "Re-send the GitHub start request for a job that is waiting for the runner.",
     "inputSchema": _schema({"ref": REF}, ["ref"]),
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "approve_release", "title": "Approve a tested change for PRODUCTION",
     "description": "Ships a tested change to the live VOLT site. Only call this after the person has seen the "
                    "job reference and tested commit (from get_change_request / get_change_review) and explicitly "
                    "said to release that exact commit. Refused unless tested_commit is the job's current tested "
                    "commit and its tests passed.",
     "inputSchema": _schema({
         "ref": REF,
         "tested_commit": {"type": "string", "minLength": 7, "maxLength": 40,
                           "description": "The tested commit SHA shown for the job (at least 7 characters)."},
         "confirm_production": {"type": "boolean", "const": True,
                                "description": "Must be true: the person confirmed releasing this commit to production."}},
         ["ref", "tested_commit", "confirm_production"]),
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True}},
]


DASHBOARD = "https://app.evosyspro.live/volt-ops/%s/dev?job=%s"


def _job_view(j: Dict[str, Any], org_id: Optional[str] = None) -> Dict[str, Any]:
    """The same authoritative record the Development dashboard shows: the
    shared `progress` reading (stage, summary, blocker, next action, latest
    time) first, then the evidence behind it."""
    keep = ("ref", "title", "status", "status_label", "requested_by", "via", "created_at", "updated_at",
            "attempt", "dispatch", "branch", "pr_number", "pr_url", "head_sha", "tests_passed", "tests_summary",
            "changed_files", "run_url", "merge_approved_by", "merge_sha", "deploy_targets", "deploy_detail",
            "deployed_at", "can", "events")
    p = j.get("progress") or {}
    out = {"ref": j.get("ref"), "stage": p.get("stage"), "summary": p.get("summary"),
           "blocking": p.get("blocker"), "next_action": p.get("next_action"), "latest_at": p.get("latest_at"),
           "tested_commit": p.get("tested_commit"), "release_ready": p.get("release_ready")}
    out.update({k: j.get(k) for k in keep if k in j})
    if org_id:
        out["dashboard_url"] = DASHBOARD % (org_id, j.get("number"))
    out["preview"] = ("No live preview site exists for VOLT branches. Review material: get_change_review "
                      "(diff + tests) and the pull request." if j.get("pr_url") else None)
    if out.get("release_ready"):
        out["next"] = ("To release: show the person the change (get_change_review) and ask them to confirm "
                       "releasing %s at commit %s." % (j["ref"], (j.get("head_sha") or "")[:12]))
    return out


def _auth(db: Session, request: Request):
    h = request.headers.get("authorization") or ""
    raw = h[7:].strip() if h.lower().startswith("bearer ") else ""
    return R.token_user(db, raw)


def _call(db: Session, user: User, org_id: str, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from app.routers.volt_dev_router import _act, _refresh
    if name == "relay_status":
        return relay_status(db, user, org_id)
    if name == "list_change_requests":
        _refresh(db, org_id)
        return {"jobs": [_job_view(j, org_id) for j in R.list_jobs(db, org_id, 20)]}
    if name == "my_latest_requests":
        _refresh(db, org_id)
        from app.models.volt_models import VoltDevJob
        rows = (db.query(VoltDevJob).filter(VoltDevJob.organization_id == org_id, VoltDevJob.requested_by == user.id)
                .order_by(VoltDevJob.number.desc()).limit(10).all())
        return {"you": user.full_name or user.email,
                "jobs": [_job_view(R.job_dict(db, j), org_id) for j in rows],
                "note": None if rows else "You have not submitted any VOLT requests yet."}
    if name == "what_is_blocking":
        _refresh(db, org_id)
        v = _job_view(R.job_dict(db, R.by_ref(db, org_id, args.get("ref")), events=True), org_id)
        return {k: v.get(k) for k in ("ref", "stage", "summary", "blocking", "next_action", "latest_at",
                                       "status_label", "dispatch", "run_url", "dashboard_url")}
    if name == "submit_change_request":
        body = {"title": args.get("title"), "request": args.get("request"),
                "idempotency_key": args.get("idempotency_key")}
        if not body["idempotency_key"]:
            # Identical text from the same person is the same request (a model retry
            # must not make a second job).
            body["idempotency_key"] = "chatgpt-" + hashlib.sha256(
                ("%s\n%s" % (str(body["title"]).strip(), str(body["request"]).strip())).encode()).hexdigest()[:40]
        job, created = R.submit(db, org_id, user, body, via="chatgpt")
        db.commit()
        if created:
            D.dispatch(db, job, "start")
            db.commit()
        out = _job_view(R.job_dict(db, job, events=True), org_id)
        out["created"] = created
        out["note"] = ("New job. 'Dispatched' means GitHub accepted the start request; it becomes 'Running' when "
                       "the runner claims it." if created else "This exact request already exists; nothing new was sent.")
        return out
    if name == "get_change_request":
        _refresh(db, org_id)
        return _job_view(R.job_dict(db, R.by_ref(db, org_id, args.get("ref")), events=True), org_id)
    if name == "get_change_review":
        job = R.by_ref(db, org_id, args.get("ref"))
        j = R.job_dict(db, job)
        return {"ref": j["ref"], "stage": j["progress"]["stage"], "summary": j["progress"]["summary"],
                "tested_commit": job.head_sha, "release_ready": j["progress"]["release_ready"],
                "tests_passed": j["tests_passed"], "tests_summary": j["tests_summary"], "pr_url": j["pr_url"],
                "run_url": j["run_url"], "review": D.review(job), "dashboard_url": DASHBOARD % (org_id, job.number)}
    if name == "retry_change_request":
        return _job_view(_act(db, org_id, args.get("ref"), "retry", user), org_id)
    if name == "cancel_change_request":
        return _job_view(_act(db, org_id, args.get("ref"), "cancel", user), org_id)
    if name == "resend_start":
        return _job_view(_act(db, org_id, args.get("ref"), "dispatch", user), org_id)
    if name == "approve_release":
        if args.get("confirm_production") is not True:
            R._err(422, "confirm_production must be true, after the person confirmed this exact release.", "not_confirmed")
        sha = str(args.get("tested_commit") or "")
        if len(sha.strip()) < 7:
            R._err(422, "Give the tested commit (at least 7 characters).", "commit_required")
        out = _job_view(_act(db, org_id, args.get("ref"), "approve-merge", user, expected_sha=sha), org_id)
        out["note"] = ("Approved for production at %s. The runner merges only this commit; 'Deployed - verified "
                       "running' appears after production is checked." % (out.get("head_sha") or "")[:12])
        return out
    raise KeyError(name)


def _mask(e: str) -> str:
    local, _, dom = (e or "").partition("@")
    return (local[:2] + "***@" + dom) if dom else "***"


def relay_status(db: Session, user: User, org_id: str) -> Dict[str, Any]:
    """Who is signed in, and what the relay can prove about its own health."""
    from app.models.volt_models import VoltDevRunnerSeen
    row = db.get(VoltDevRunnerSeen, "runner")
    from app.services.volt import common as C
    return {"you": user.full_name or user.email, "email": user.email, "volt_development_principal": True,
            "instant_start_configured": D.configured(), "github": D.check_github(),
            "runner_last_seen": None if row is None or row.last_seen_at is None else {
                "at": C.iso(row.last_seen_at), "started_by": row.event, "reason": row.reason,
                "claude_credential_present": row.claude_credentials},
            "email_notices_to": [_mask(e) for e in R.notify_list()],
            "dashboard_url": "https://app.evosyspro.live/volt-ops/%s/dev" % org_id}


def _rpc_error(mid, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _handle(db: Session, user: User, org_id: str, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if mid is None:                       # a notification: no reply
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "volt-development", "title": "VOLT Marketing development", "version": "1.0"},
            "instructions": INSTRUCTIONS}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return _rpc_error(mid, -32602, "Unknown tool: %s" % name)
        try:
            data = _call(db, user, org_id, name, args)
            err = False
        except HTTPException as e:
            db.rollback()
            d = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
            data, err = {"error": d.get("message") or d.get("error_description") or str(d), "code": d.get("code"),
                         "http_status": e.status_code}, True
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": json.dumps(data, default=str)}],
            "structuredContent": data, "isError": err}}
    return _rpc_error(mid, -32601, "Method not found: %s" % method)


def _unauthorized(detail: str) -> JSONResponse:
    meta = O.issuer() + "/.well-known/oauth-protected-resource/volt/dev/mcp"
    return JSONResponse({"error": "invalid_token", "error_description": detail}, status_code=401,
                        headers={"WWW-Authenticate": 'Bearer resource_metadata="%s", error="invalid_token", '
                                                     'scope="%s"' % (meta, O.SCOPE)})


@mcp.post("/mcp")
@limiter.limit("120/minute")
async def mcp_post(request: Request, db: Session = Depends(get_db)):
    try:
        user, org_id = _auth(db, request)
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
        if e.status_code == 401:
            return _unauthorized(d.get("message") or "Sign in required.")
        return JSONResponse({"error": "forbidden", "error_description": d.get("message")}, status_code=e.status_code)
    db.commit()                            # last_used_at
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
    if isinstance(payload, list):
        replies = [r for r in (_handle(db, user, org_id, m) for m in payload if isinstance(m, dict)) if r]
        return JSONResponse(replies) if replies else Response(status_code=202)
    if not isinstance(payload, dict):
        return JSONResponse(_rpc_error(None, -32600, "Invalid request"), status_code=400)
    reply = _handle(db, user, org_id, payload)
    return JSONResponse(reply) if reply else Response(status_code=202)


@mcp.get("/mcp")
def mcp_get():
    # No server-initiated stream: every reply comes back on the POST.
    return Response(status_code=405, headers={"Allow": "POST"})


@mcp.delete("/mcp")
def mcp_delete():
    return Response(status_code=405, headers={"Allow": "POST"})
