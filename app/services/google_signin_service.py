"""SIGN IN WITH GOOGLE (issue #430) - OpenID Connect, authorization-code flow.

OFF BY DEFAULT. Nothing here runs, and the login page shows no Google button,
until ALL of these are set on the backend:

    GOOGLE_SIGNIN_ENABLED=1
    GOOGLE_SIGNIN_CLIENT_ID=<OAuth client id>        (may be the same Google Cloud
    GOOGLE_SIGNIN_CLIENT_SECRET=<OAuth client secret>  client as the calendar one)
    GOOGLE_SIGNIN_REDIRECT_URI=https://<backend>/auth/google/callback  (registered in Google Cloud)

Optional:
    GOOGLE_SIGNIN_ALLOWED_ORIGINS  comma list of app origins allowed to start it and be
                                   returned to (default https://app.evosyspro.live)
    GOOGLE_SIGNIN_ALLOW_GOD=1      let the platform owner (god_admin) use Google too
                                   (default: password only for god_admin)

This is NOT the calendar/Gmail integration. Those grant API access to a user's
Google data; this proves who is signing in and grants nothing on Google.

SECURITY PROPERTIES
  - state "<id>.<secret>": the secret is stored as a digest; single use; 10 min.
  - the starting browser gets an HttpOnly cookie whose digest is on the row; a
    callback from any other browser is refused (login CSRF).
  - PKCE (S256) and an OIDC nonce bound to the row.
  - the ID token is verified server-side with Google's published keys:
    signature, issuer, audience (our client id), expiry; then nonce and
    email_verified.
  - a Google account signs in ONLY if a signed-in user linked it from their own
    settings (UserGoogleIdentity by `sub`). An email match never links or signs in.
  - the session token is collected with a POST using a 2-minute single-use
    ticket from the URL fragment; it is never put in a URL.
  - the same checks as the password login then apply: active account, platform
    (brand) authorisation, god_admin policy. The role is whatever the account
    already has - Google cannot raise it.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from urllib.parse import urlencode, urlparse

from sqlalchemy.orm import Session

from app.models.google_signin_models import (PURPOSE_LINK, PURPOSE_LINK_INTENT, PURPOSE_LOGIN,
                                             GoogleLoginTicket, GoogleSigninTransaction, UserGoogleIdentity)
from app.models.models import User

_log = logging.getLogger(__name__)

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
COOKIE_NAME = "evs_gsi"
DEFAULT_ORIGINS = ("https://app.evosyspro.live",)


class GoogleSigninError(Exception):
    """One short, non-specific code; the browser only ever learns this code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


# ── configuration ─────────────────────────────────────────────────────────
def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def allowed_origins() -> Tuple[str, ...]:
    raw = os.environ.get("GOOGLE_SIGNIN_ALLOWED_ORIGINS")
    if not raw:
        return DEFAULT_ORIGINS
    out = []
    for o in raw.split(","):
        o = o.strip().rstrip("/")
        p = urlparse(o)
        if p.scheme in ("https", "http") and p.netloc and not p.path and not p.query:
            out.append(o)
    return tuple(out)


def settings() -> dict:
    return {
        "enabled_flag": _flag("GOOGLE_SIGNIN_ENABLED"),
        "client_id": (os.environ.get("GOOGLE_SIGNIN_CLIENT_ID") or "").strip(),
        "client_secret": (os.environ.get("GOOGLE_SIGNIN_CLIENT_SECRET") or "").strip(),
        "redirect_uri": (os.environ.get("GOOGLE_SIGNIN_REDIRECT_URI") or "").strip(),
        "allow_god": _flag("GOOGLE_SIGNIN_ALLOW_GOD"),
    }


def missing_config() -> list:
    s = settings()
    miss = []
    if not s["enabled_flag"]:
        miss.append("GOOGLE_SIGNIN_ENABLED")
    for k in ("client_id", "client_secret", "redirect_uri"):
        if not s[k]:
            miss.append("GOOGLE_SIGNIN_" + k.upper())
    return miss


def is_enabled() -> bool:
    return not missing_config()


def origin_allowed(origin: Optional[str]) -> bool:
    return bool(origin) and origin.rstrip("/") in allowed_origins()


# ── helpers ───────────────────────────────────────────────────────────────
def _digest(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def safe_next(path: Optional[str]) -> Optional[str]:
    """Only a same-site absolute path; anything else (//evil, http://, \\) is dropped."""
    if not path or not isinstance(path, str) or len(path) > 500:
        return None
    if not path.startswith("/") or path.startswith("//") or "\\" in path or "\n" in path or "\r" in path:
        return None
    return path


def _sweep(db: Session) -> None:
    try:
        cutoff = _now() - timedelta(hours=24)
        db.query(GoogleSigninTransaction).filter(GoogleSigninTransaction.expires_at < cutoff).delete(synchronize_session=False)
        db.query(GoogleLoginTicket).filter(GoogleLoginTicket.expires_at < cutoff).delete(synchronize_session=False)
        db.commit()
    except Exception:                                   # noqa: BLE001
        db.rollback()


def _god_blocked(user: User) -> bool:
    return user.role == "god_admin" and not settings()["allow_god"]


# ── linking: a signed-in user asks to connect their Google account ─────────
def create_link_intent(db: Session, user: User, origin: str, next_path: Optional[str]) -> str:
    """A short-lived handle the app opens as a top-level page to start linking."""
    if not is_enabled():
        raise GoogleSigninError("disabled")
    if not origin_allowed(origin):
        raise GoogleSigninError("origin")
    if _god_blocked(user):
        raise GoogleSigninError("not_allowed")
    if db.query(UserGoogleIdentity).filter(UserGoogleIdentity.user_id == user.id).first():
        raise GoogleSigninError("already_linked")
    secret = secrets.token_urlsafe(32)
    t = GoogleSigninTransaction(secret_hash=_digest(secret), purpose=PURPOSE_LINK_INTENT, user_id=user.id,
                                return_origin=origin.rstrip("/"), next_path=safe_next(next_path),
                                expires_at=GoogleSigninTransaction.default_expiry(minutes=2))
    db.add(t); db.commit()
    _log.info("AUDIT: google link intent created user=%s txn=%s", user.id, t.id)
    return "%s.%s" % (t.id, secret)


def _spend(db: Session, handle: str, purposes) -> GoogleSigninTransaction:
    if not handle or not isinstance(handle, str) or "." not in handle:
        raise GoogleSigninError("failed", "malformed handle")
    tid, _, secret = handle.partition(".")
    t = db.query(GoogleSigninTransaction).filter(GoogleSigninTransaction.id == tid).first()
    if t is None or not hmac.compare_digest(t.secret_hash or "", _digest(secret)):
        raise GoogleSigninError("failed", "unknown or tampered handle")
    if t.purpose not in purposes:
        raise GoogleSigninError("failed", "wrong purpose")
    if t.consumed_at is not None:
        raise GoogleSigninError("failed", "replayed")
    if _aware(t.expires_at) < _now():
        raise GoogleSigninError("expired")
    # conditional update: the second presenter of the same handle loses
    n = (db.query(GoogleSigninTransaction)
         .filter(GoogleSigninTransaction.id == t.id, GoogleSigninTransaction.consumed_at.is_(None))
         .update({GoogleSigninTransaction.consumed_at: _now()}, synchronize_session=False))
    db.commit()
    if n != 1:
        raise GoogleSigninError("failed", "replayed")
    db.refresh(t)
    return t


# ── start: build the Google URL and the browser-binding cookie ─────────────
def start(db: Session, *, origin: str, next_path: Optional[str] = None, intent: Optional[str] = None,
          client_ip: Optional[str] = None) -> Tuple[str, str]:
    """Return (google_authorize_url, browser_cookie_value)."""
    if not is_enabled():
        raise GoogleSigninError("disabled")
    s = settings()
    purpose, user_id = PURPOSE_LOGIN, None
    if intent:
        it = _spend(db, intent, (PURPOSE_LINK_INTENT,))
        purpose, user_id, origin, next_path = PURPOSE_LINK, it.user_id, it.return_origin, it.next_path
    if not origin_allowed(origin):
        raise GoogleSigninError("origin")
    _sweep(db)
    secret = secrets.token_urlsafe(32)
    browser = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    t = GoogleSigninTransaction(secret_hash=_digest(secret), browser_hash=_digest(browser), purpose=purpose, user_id=user_id,
                                nonce=nonce, code_verifier=verifier, return_origin=origin.rstrip("/"),
                                next_path=safe_next(next_path), created_ip=client_ip,
                                expires_at=GoogleSigninTransaction.default_expiry())
    db.add(t); db.commit()
    q = {"client_id": s["client_id"], "redirect_uri": s["redirect_uri"], "response_type": "code",
         "scope": "openid email profile", "state": "%s.%s" % (t.id, secret), "nonce": nonce,
         "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
         "access_type": "online", "include_granted_scopes": "false"}
    _log.info("AUDIT: google sign-in started purpose=%s txn=%s", purpose, t.id)
    return AUTHORIZE_URL + "?" + urlencode(q), browser


# ── callback ──────────────────────────────────────────────────────────────
def _exchange_code(code: str, verifier: str) -> dict:
    """Server-to-server code exchange. Isolated so tests can replace it."""
    import requests
    s = settings()
    r = requests.post(TOKEN_URL, data={"code": code, "client_id": s["client_id"], "client_secret": s["client_secret"],
                                       "redirect_uri": s["redirect_uri"], "grant_type": "authorization_code",
                                       "code_verifier": verifier}, timeout=10)
    if r.status_code != 200:
        raise GoogleSigninError("failed", "token exchange %s" % r.status_code)
    return r.json()


def _verify_id_token(raw: str) -> dict:
    """Signature (Google JWKS), audience, expiry and issuer, via google-auth."""
    from google.auth.transport import requests as ga_requests
    from google.oauth2 import id_token
    try:
        claims = id_token.verify_oauth2_token(raw, ga_requests.Request(), audience=settings()["client_id"], clock_skew_in_seconds=10)
    except Exception as e:                              # noqa: BLE001  (expired, bad signature, wrong audience...)
        raise GoogleSigninError("failed", "id token rejected: %s" % type(e).__name__)
    if claims.get("iss") not in ISSUERS:
        raise GoogleSigninError("failed", "issuer")
    return claims


def resolve_txn_for_callback(db: Session, state: str) -> GoogleSigninTransaction:
    return _spend(db, state, (PURPOSE_LOGIN, PURPOSE_LINK))


def complete(db: Session, t: GoogleSigninTransaction, *, code: str, browser_cookie: Optional[str]) -> Tuple[str, Optional[User], dict]:
    """Finish a spent transaction. Returns (purpose, user, claims). Raises GoogleSigninError."""
    if not t.browser_hash or not browser_cookie or not hmac.compare_digest(t.browser_hash, _digest(browser_cookie)):
        _log.warning("AUDIT: google callback from a different browser txn=%s", t.id)
        raise GoogleSigninError("failed", "browser binding")
    if not code:
        raise GoogleSigninError("failed", "no code")
    tokens = _exchange_code(code, t.code_verifier)
    raw = tokens.get("id_token")
    if not raw:
        raise GoogleSigninError("failed", "no id token")
    claims = _verify_id_token(raw)
    if not t.nonce or not hmac.compare_digest(str(claims.get("nonce") or ""), t.nonce):
        raise GoogleSigninError("failed", "nonce")
    if claims.get("email_verified") is not True and str(claims.get("email_verified")).lower() != "true":
        raise GoogleSigninError("unverified")
    sub = str(claims.get("sub") or "")
    if not sub:
        raise GoogleSigninError("failed", "no sub")

    if t.purpose == PURPOSE_LINK:
        user = db.query(User).filter(User.id == t.user_id).first()
        if user is None or not user.is_active or _god_blocked(user):
            raise GoogleSigninError("not_allowed")
        other = db.query(UserGoogleIdentity).filter(UserGoogleIdentity.google_sub == sub).first()
        if other is not None and other.user_id != user.id:
            raise GoogleSigninError("already_linked")
        mine = db.query(UserGoogleIdentity).filter(UserGoogleIdentity.user_id == user.id).first()
        if mine is not None and mine.google_sub != sub:
            raise GoogleSigninError("already_linked")
        if mine is None:
            db.add(UserGoogleIdentity(user_id=user.id, google_sub=sub, email_at_link=(claims.get("email") or "")[:320]))
            db.commit()
        _log.info("AUDIT: google account linked user=%s", user.id)
        return PURPOSE_LINK, user, claims

    link = db.query(UserGoogleIdentity).filter(UserGoogleIdentity.google_sub == sub).first()
    if link is None:
        _log.info("AUDIT: google sign-in refused: account not linked txn=%s", t.id)
        raise GoogleSigninError("not_linked")
    user = db.query(User).filter(User.id == link.user_id).first()
    if user is None or not user.is_active or _god_blocked(user):
        raise GoogleSigninError("not_linked")      # same answer as unknown: no enumeration
    return PURPOSE_LOGIN, user, claims


def issue_ticket(db: Session, user: User, origin: str) -> str:
    secret = secrets.token_urlsafe(32)
    k = GoogleLoginTicket(secret_hash=_digest(secret), user_id=user.id, return_origin=origin.rstrip("/"),
                          expires_at=_now() + timedelta(minutes=2))
    db.add(k); db.commit()
    return "%s.%s" % (k.id, secret)


def redeem_ticket(db: Session, ticket: str, origin: Optional[str]) -> User:
    if not ticket or "." not in ticket:
        raise GoogleSigninError("failed", "malformed ticket")
    kid, _, secret = ticket.partition(".")
    k = db.query(GoogleLoginTicket).filter(GoogleLoginTicket.id == kid).first()
    if k is None or not hmac.compare_digest(k.secret_hash or "", _digest(secret)):
        raise GoogleSigninError("failed", "unknown ticket")
    if k.consumed_at is not None or _aware(k.expires_at) < _now():
        raise GoogleSigninError("expired")
    if origin and origin.rstrip("/") != k.return_origin:
        raise GoogleSigninError("failed", "ticket presented from another origin")
    n = (db.query(GoogleLoginTicket).filter(GoogleLoginTicket.id == k.id, GoogleLoginTicket.consumed_at.is_(None))
         .update({GoogleLoginTicket.consumed_at: _now()}, synchronize_session=False))
    db.commit()
    if n != 1:
        raise GoogleSigninError("failed", "replayed ticket")
    user = db.query(User).filter(User.id == k.user_id).first()
    if user is None or not user.is_active or _god_blocked(user):
        raise GoogleSigninError("not_linked")
    link = db.query(UserGoogleIdentity).filter(UserGoogleIdentity.user_id == user.id).first()
    if link is None:                                  # unlinked between callback and redeem
        raise GoogleSigninError("not_linked")
    link.last_login_at = _now(); db.commit()
    return user


def link_status(db: Session, user: User) -> dict:
    link = db.query(UserGoogleIdentity).filter(UserGoogleIdentity.user_id == user.id).first()
    return {"enabled": is_enabled(), "allowed": not _god_blocked(user), "linked": link is not None,
            "email": link.email_at_link if link else None,
            "linked_at": _aware(link.linked_at).isoformat() if link and link.linked_at else None}


def unlink(db: Session, user: User) -> bool:
    n = db.query(UserGoogleIdentity).filter(UserGoogleIdentity.user_id == user.id).delete(synchronize_session=False)
    db.commit()
    if n:
        _log.info("AUDIT: google account unlinked user=%s", user.id)
    return bool(n)
