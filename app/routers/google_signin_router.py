"""/auth/google/* - Sign in with Google (issue #430). Off unless configured;
see app/services/google_signin_service.py for the full security notes."""
import logging
from typing import Optional
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.limiter import limiter
from app.models.models import User
from app.routers.auth_router import TokenResponse, _detect_platform_slug
from app.services import google_signin_service as G
from app.services.auth_service import create_access_token
from app.services.workspace_access import user_authorized_platform_slugs

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google", tags=["auth-google"])


def _request_origin(request: Request) -> Optional[str]:
    o = request.headers.get("origin")
    if o:
        return o.rstrip("/")
    ref = request.headers.get("referer")
    if ref:
        p = urlparse(ref)
        if p.scheme and p.netloc:
            return "%s://%s" % (p.scheme, p.netloc)
    return None


def _fallback_origin() -> str:
    return G.allowed_origins()[0] if G.allowed_origins() else G.DEFAULT_ORIGINS[0]


def _backend_base() -> str:
    p = urlparse(G.settings()["redirect_uri"])
    return "%s://%s" % (p.scheme, p.netloc)


def _secure_cookie() -> bool:
    return G.settings()["redirect_uri"].startswith("https://")


@router.get("/config")
def google_config(request: Request):
    """Public, no secrets: may this page show the Google button?"""
    return {"enabled": G.is_enabled() and G.origin_allowed(_request_origin(request))}


@router.get("/start")
@limiter.limit("30/minute")
def google_start(request: Request, origin: Optional[str] = None, next: Optional[str] = None,
                 intent: Optional[str] = None, db: Session = Depends(get_db)):
    if not G.is_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        url, browser = G.start(db, origin=origin or "", next_path=next, intent=intent,
                               client_ip=request.client.host if request.client else None)
    except G.GoogleSigninError as e:
        dest = (origin.rstrip("/") if G.origin_allowed(origin) else _fallback_origin())
        page = "/settings" if intent else "/login"
        return RedirectResponse("%s%s?google_error=%s" % (dest, page, quote(e.code)), status_code=303)
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(G.COOKIE_NAME, browser, max_age=600, httponly=True, secure=_secure_cookie(),
                    samesite="lax", path="/auth/google")
    return resp


@router.get("/callback")
@limiter.limit("30/minute")
def google_callback(request: Request, state: Optional[str] = None, code: Optional[str] = None,
                    error: Optional[str] = None, db: Session = Depends(get_db)):
    try:
        t = G.resolve_txn_for_callback(db, state or "")
    except G.GoogleSigninError as e:
        _log.info("AUDIT: google callback refused before resolution: %s", e)
        resp = RedirectResponse("%s/login?google_error=failed" % _fallback_origin(), status_code=303)
        resp.delete_cookie(G.COOKIE_NAME, path="/auth/google")
        return resp
    origin = t.return_origin
    page = "/settings" if t.purpose == G.PURPOSE_LINK else "/login"
    try:
        if error:
            raise G.GoogleSigninError("cancelled" if error == "access_denied" else "failed", "provider error %s" % error[:40])
        purpose, user, _claims = G.complete(db, t, code=code or "", browser_cookie=request.cookies.get(G.COOKIE_NAME))
        if purpose == G.PURPOSE_LINK:
            dest = "%s/settings?google=linked" % origin
        else:
            ticket = G.issue_ticket(db, user, origin)
            frag = "ticket=" + quote(ticket, safe="")
            if t.next_path:
                frag += "&next=" + quote(t.next_path, safe="")
            dest = "%s/auth/google/complete#%s" % (origin, frag)
    except G.GoogleSigninError as e:
        _log.info("AUDIT: google %s refused txn=%s code=%s (%s)", t.purpose, t.id, e.code, e)
        dest = "%s%s?google_error=%s" % (origin, page, quote(e.code))
    resp = RedirectResponse(dest, status_code=303)
    resp.delete_cookie(G.COOKIE_NAME, path="/auth/google")
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


class ExchangeBody(BaseModel):
    ticket: str


@router.post("/exchange", response_model=TokenResponse)
@limiter.limit("30/minute")
def google_exchange(request: Request, body: ExchangeBody, db: Session = Depends(get_db)):
    if not G.is_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    origin = _request_origin(request)
    if not G.origin_allowed(origin):
        raise HTTPException(status_code=401, detail="Google sign-in could not be completed.")
    try:
        user = G.redeem_ticket(db, body.ticket, origin)
    except G.GoogleSigninError:
        raise HTTPException(status_code=401, detail="Google sign-in could not be completed. Please try again.")
    # THE SAME BRAND CHECK AS THE PASSWORD LOGIN: an account may only sign in on
    # the platforms it belongs to; god_admin is governed by GOOGLE_SIGNIN_ALLOW_GOD.
    if user.role != "god_admin":
        plat = _detect_platform_slug(request)
        if plat == "advisorflow":
            raise HTTPException(status_code=401, detail="Google sign-in could not be completed.")
        if plat is not None and plat not in user_authorized_platform_slugs(user, db):
            raise HTTPException(status_code=401, detail="Google sign-in could not be completed.")
    try:
        from app.services import workspace_access
        workspace_access.backfill_from_legacy_column(db, user=user)
    except Exception:                                   # noqa: BLE001
        _log.warning("workspace backfill at google sign-in failed for user=%s", user.id, exc_info=True)
    token = create_access_token(user, db, request=request)
    _log.info("AUDIT: google sign-in succeeded user=%s", user.id)
    return TokenResponse(access_token=token, role=user.role, full_name=user.full_name,
                         organization_id=user.organization_id, must_change_password=user.must_change_password)


class IntentBody(BaseModel):
    origin: Optional[str] = None
    next: Optional[str] = None


@router.get("/link")
def google_link_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return G.link_status(db, user)


@router.post("/link/intent")
@limiter.limit("20/minute")
def google_link_intent(request: Request, body: IntentBody, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    origin = body.origin or _request_origin(request)
    try:
        handle = G.create_link_intent(db, user, origin or "", body.next)
    except G.GoogleSigninError as e:
        status = 404 if e.code == "disabled" else 409 if e.code == "already_linked" else 403
        raise HTTPException(status_code=status, detail={"disabled": "Google sign-in is not turned on.",
                                                        "already_linked": "A Google account is already connected.",
                                                        "not_allowed": "Google sign-in is not available for this account.",
                                                        "origin": "This address may not start Google sign-in."}.get(e.code, "Not available."))
    return {"url": "%s/auth/google/start?intent=%s" % (_backend_base(), quote(handle, safe=""))}


@router.delete("/link")
def google_unlink(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"unlinked": G.unlink(db, user)}
