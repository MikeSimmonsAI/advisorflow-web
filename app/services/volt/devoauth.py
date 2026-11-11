"""Connect ChatGPT to the VOLT development relay (Issue #571, 2026-11-10).

WHY THIS EXISTS
---------------
A personal key pasted into a custom GPT is shared by everyone who uses that
GPT, and custom GPTs stop running on Dec 11, 2026 (OpenAI's retirement of
GPTs in favour of plugins). ChatGPT's supported, lasting way to act for one
person is a plugin built from a custom MCP server that signs the person in
with OAuth. A GPT Action can use the same OAuth sign-in until GPTs retire.

So VOLT is its own small OAuth 2.1 authorization server, for this relay only:

  1. ChatGPT registers itself (RFC 7591) or uses a hand-made client. It may
     only send people back to a ChatGPT callback (REDIRECT_HOSTS).
  2. ChatGPT sends the person to /volt/dev/oauth/authorize. That records the
     request and forwards to https://voltmarketing.live/volt-dev/connect,
     where the person signs in with THEIR OWN EvoSys login and clicks Allow.
     Only an authorized principal (devrelay.is_principal) can allow.
  3. The page returns a one-time code (5 minutes, PKCE S256 required for
     public clients). /volt/dev/oauth/token swaps it for an access token
     (vdo_, 1 hour) and a refresh token (vdr_, 90 days, rotated on every use).
  4. Every call with that token acts as that person, re-checked as a principal
     on every call. Revoking it on the Development screen ends it at once.

Nothing here is a password: VOLT never sees the person's ChatGPT credentials
and ChatGPT never sees the person's VOLT password. Tokens are stored as
SHA-256 hashes only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from datetime import timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.volt_models import VoltDevOAuthClient, VoltDevOAuthGrant, VoltDevToken, VoltProgram
from app.services.volt import common as C
from app.services.volt import devrelay as R

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=90)
REQUEST_TTL = timedelta(minutes=15)
CODE_TTL = timedelta(minutes=5)
SCOPE = "volt.dev"


def issuer() -> str:
    return (os.environ.get("VOLT_API_PUBLIC_URL") or "https://api.voltmarketing.live").rstrip("/")


def consent_base() -> str:
    return (os.environ.get("VOLT_FRONTEND_PUBLIC_URL") or "https://voltmarketing.live").rstrip("/")


def redirect_hosts() -> List[str]:
    raw = os.environ.get("VOLT_DEV_OAUTH_REDIRECT_HOSTS") or "chatgpt.com,chat.openai.com"
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _oauth_err(status: int, error: str, description: str):
    raise HTTPException(status_code=status, detail={"error": error, "error_description": description})


def redirect_allowed(uri: str) -> bool:
    try:
        u = urlparse(uri)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme == "https" and host in redirect_hosts():
        return True
    # Local development and tests only, and only when configured: http loopback.
    return u.scheme == "http" and host in ("localhost", "127.0.0.1") and host in redirect_hosts()


# ── metadata ───────────────────────────────────────────────────────────────

def server_metadata() -> Dict[str, Any]:
    base = issuer()
    return {
        "issuer": base,
        "authorization_endpoint": base + "/volt/dev/oauth/authorize",
        "token_endpoint": base + "/volt/dev/oauth/token",
        "registration_endpoint": base + "/volt/dev/oauth/register",
        "revocation_endpoint": base + "/volt/dev/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "scopes_supported": [SCOPE],
        "service_documentation": consent_base() + "/volt-dev/connect",
    }


def resource_metadata() -> Dict[str, Any]:
    base = issuer()
    return {"resource": base + "/volt/dev/mcp", "authorization_servers": [base],
            "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"],
            "resource_name": "VOLT Marketing development"}


# ── clients ────────────────────────────────────────────────────────────────

def register_client(db: Session, body: Dict[str, Any]) -> Dict[str, Any]:
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > 10:
        _oauth_err(400, "invalid_redirect_uri", "redirect_uris must list 1 to 10 callback URLs.")
    uris = [str(u)[:500] for u in uris]
    bad = [u for u in uris if not redirect_allowed(u)]
    if bad:
        _oauth_err(400, "invalid_redirect_uri", "Only ChatGPT callback URLs can be registered.")
    method = str(body.get("token_endpoint_auth_method") or "none")
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        _oauth_err(400, "invalid_client_metadata", "Unsupported token_endpoint_auth_method.")
    cid = "vdc_" + secrets.token_urlsafe(18)
    secret = "vds_" + secrets.token_urlsafe(32) if method != "none" else None
    name = (str(body.get("client_name") or "ChatGPT").strip() or "ChatGPT")[:80]
    db.add(VoltDevOAuthClient(id=cid, name=name, secret_hash=_hash(secret) if secret else None,
                              redirect_uris=json.dumps(uris)))
    db.flush()
    out = {"client_id": cid, "client_name": name, "redirect_uris": uris,
           "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
           "token_endpoint_auth_method": method, "client_id_issued_at": int(C.now().timestamp())}
    if secret:
        out["client_secret"] = secret
        out["client_secret_expires_at"] = 0
    return out


def _client(db: Session, client_id: str) -> VoltDevOAuthClient:
    c = db.get(VoltDevOAuthClient, str(client_id or "")[:100])
    if c is None:
        _oauth_err(401, "invalid_client", "Unknown client.")
    return c


def _client_auth(db: Session, client_id: str, client_secret: Optional[str]) -> VoltDevOAuthClient:
    c = _client(db, client_id)
    if c.secret_hash and not (client_secret and secrets.compare_digest(_hash(client_secret), c.secret_hash)):
        _oauth_err(401, "invalid_client", "Client authentication failed.")
    return c


# ── authorize → consent page → code ────────────────────────────────────────

def start(db: Session, q: Dict[str, str]) -> str:
    """Validate ChatGPT's authorize request; return where to send the browser.
    A bad client or callback is NOT redirected (that would be an open redirect)."""
    c = _client(db, q.get("client_id"))
    redirect_uri = q.get("redirect_uri") or ""
    allowed = json.loads(c.redirect_uris or "[]")
    if redirect_uri not in allowed:
        if len(allowed) == 1 and not redirect_uri:
            redirect_uri = allowed[0]
        else:
            _oauth_err(400, "invalid_request", "redirect_uri is not registered for this client.")
    state = q.get("state")

    def back(error: str, desc: str) -> str:
        p = {"error": error, "error_description": desc}
        if state:
            p["state"] = state
        return redirect_uri + ("&" if "?" in redirect_uri else "?") + urlencode(p)

    if q.get("response_type") != "code":
        return back("unsupported_response_type", "Only response_type=code is supported.")
    challenge = q.get("code_challenge") or None
    if challenge and (q.get("code_challenge_method") or "plain") != "S256":
        return back("invalid_request", "PKCE must use S256.")
    if not challenge and not c.secret_hash:
        return back("invalid_request", "PKCE (S256) is required.")
    g = VoltDevOAuthGrant(id=secrets.token_urlsafe(24), client_id=c.id, redirect_uri=redirect_uri,
                          state=(state or None) and state[:1000], code_challenge=challenge,
                          scope=SCOPE, expires_at=C.now() + REQUEST_TTL)
    db.add(g)
    db.flush()
    return consent_base() + "/volt-dev/connect?request=" + g.id


def _grant(db: Session, request_id: str) -> VoltDevOAuthGrant:
    g = db.get(VoltDevOAuthGrant, str(request_id or "")[:100])
    if g is None or g.expires_at < C.now() or g.decision is not None:
        R._err(410, "This connection request has expired or was already answered. Start again from ChatGPT.",
               "request_gone")
    return g


def volt_org_for(db: Session, user: User) -> Optional[str]:
    """The VOLT tenant this person may develop for (there is one VOLT program)."""
    for prog in db.query(VoltProgram).all():
        if R.is_principal(db, user, prog.organization_id):
            return prog.organization_id
    return None


def describe(db: Session, request_id: str, user: User) -> Dict[str, Any]:
    g = _grant(db, request_id)
    c = db.get(VoltDevOAuthClient, g.client_id)
    org = volt_org_for(db, user)
    host = urlparse(g.redirect_uri).hostname or ""
    return {"client": c.name if c else "ChatGPT", "returns_to": host, "you": user.full_name or user.email,
            "email": user.email, "authorized": org is not None,
            "can": ["Submit VOLT Marketing change requests as you",
                    "Check status, test results, changed files and the code diff",
                    "Retry, cancel or re-send the start of a job",
                    "Approve a tested change for production - only the exact commit you name, after you confirm"],
            "expires_in_minutes": max(0, int((g.expires_at - C.now()).total_seconds() // 60))}


def decide(db: Session, request_id: str, user: User, allow: bool) -> Dict[str, Any]:
    g = _grant(db, request_id)
    p: Dict[str, str] = {}
    if not allow:
        g.decision = "denied"
        p = {"error": "access_denied", "error_description": "The person declined."}
    else:
        org = volt_org_for(db, user)
        if org is None:
            R._err(403, "Only VOLT's authorized development principals can connect ChatGPT.", "not_principal")
        code = secrets.token_urlsafe(32)
        g.decision, g.user_id, g.organization_id = "allowed", user.id, org
        g.code_hash, g.code_expires_at = _hash(code), C.now() + CODE_TTL
        p = {"code": code}
    if g.state:
        p["state"] = g.state
    return {"redirect": g.redirect_uri + ("&" if "?" in g.redirect_uri else "?") + urlencode(p)}


# ── token endpoint ─────────────────────────────────────────────────────────

def _issue(db: Session, user_id: str, org_id: str, client_id: str, name: str) -> Dict[str, Any]:
    access = "vdo_" + secrets.token_urlsafe(32)
    refresh = "vdr_" + secrets.token_urlsafe(40)
    now = C.now()
    db.add(VoltDevToken(organization_id=org_id, user_id=user_id, name=name, prefix=access[:10],
                        token_hash=_hash(access), kind="oauth", client_id=client_id,
                        expires_at=now + ACCESS_TTL, refresh_hash=_hash(refresh),
                        refresh_expires_at=now + REFRESH_TTL))
    db.flush()
    return {"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
            "refresh_token": refresh, "scope": SCOPE}


def _verify_pkce(challenge: Optional[str], verifier: Optional[str]) -> bool:
    if not challenge:
        return True
    if not verifier:
        return False
    calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return secrets.compare_digest(calc, challenge)


def token(db: Session, form: Dict[str, str], basic: Optional[tuple]) -> Dict[str, Any]:
    client_id = form.get("client_id") or (basic[0] if basic else "")
    client_secret = form.get("client_secret") or (basic[1] if basic else None)
    c = _client_auth(db, client_id, client_secret)
    gt = form.get("grant_type")
    if gt == "authorization_code":
        code = form.get("code") or ""
        g = db.query(VoltDevOAuthGrant).filter(VoltDevOAuthGrant.code_hash == _hash(code)).first() if code else None
        if g is None or g.client_id != c.id or g.decision != "allowed":
            _oauth_err(400, "invalid_grant", "Unknown or invalid code.")
        if g.used_at is not None:
            # A replayed code: kill everything it produced (RFC 6749 §4.1.2).
            for t in db.query(VoltDevToken).filter(VoltDevToken.client_id == c.id, VoltDevToken.user_id == g.user_id,
                                                   VoltDevToken.created_at >= g.used_at,
                                                   VoltDevToken.revoked_at.is_(None)).all():
                t.revoked_at = C.now()
            db.flush()
            _oauth_err(400, "invalid_grant", "This code was already used.")
        if g.code_expires_at < C.now():
            _oauth_err(400, "invalid_grant", "The code expired. Connect again.")
        if form.get("redirect_uri") and form.get("redirect_uri") != g.redirect_uri:
            _oauth_err(400, "invalid_grant", "redirect_uri does not match.")
        if not _verify_pkce(g.code_challenge, form.get("code_verifier")):
            _oauth_err(400, "invalid_grant", "PKCE verification failed.")
        g.used_at = C.now()
        user = db.get(User, g.user_id)
        if not R.is_principal(db, user, g.organization_id):
            _oauth_err(400, "invalid_grant", "This person is no longer authorized for VOLT development.")
        return _issue(db, user.id, g.organization_id, c.id, "%s (connected by sign-in)" % c.name)
    if gt == "refresh_token":
        raw = form.get("refresh_token") or ""
        t = db.query(VoltDevToken).filter(VoltDevToken.refresh_hash == _hash(raw)).first() if raw else None
        if (t is None or t.client_id != c.id or t.revoked_at is not None
                or (t.refresh_expires_at and t.refresh_expires_at < C.now())):
            _oauth_err(400, "invalid_grant", "This connection was revoked or expired. Connect again.")
        user = db.get(User, t.user_id)
        if not R.is_principal(db, user, t.organization_id):
            t.revoked_at = C.now()
            db.flush()
            _oauth_err(400, "invalid_grant", "This person is no longer authorized for VOLT development.")
        # Rotate in place: the old access and refresh tokens stop working, and
        # the connection stays ONE row (one thing to see and to revoke).
        access = "vdo_" + secrets.token_urlsafe(32)
        refresh = "vdr_" + secrets.token_urlsafe(40)
        t.token_hash, t.prefix, t.expires_at = _hash(access), access[:10], C.now() + ACCESS_TTL
        t.refresh_hash, t.refresh_expires_at = _hash(refresh), C.now() + REFRESH_TTL
        db.flush()
        return {"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
                "refresh_token": refresh, "scope": SCOPE}
    _oauth_err(400, "unsupported_grant_type", "Use authorization_code or refresh_token.")


def revoke(db: Session, form: Dict[str, str]) -> None:
    raw = form.get("token") or ""
    if not raw:
        return
    h = _hash(raw)
    t = (db.query(VoltDevToken).filter((VoltDevToken.token_hash == h) | (VoltDevToken.refresh_hash == h)).first())
    if t is not None and t.revoked_at is None:
        t.revoked_at = C.now()
