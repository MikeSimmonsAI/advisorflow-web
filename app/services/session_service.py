"""The one place a login session is created, rotated, looked up or ended.

Every caller that used to write `users.session_token` directly now comes
through here. That matters more than it sounds: the old column was written in
six places (login, refresh, logout, change-password, force-logout, reset) and
read in one, and the read was the only thing keeping them consistent. Rows have
the same failure mode unless the writes are funnelled, so they are.

READ THE CONTRACT BEFORE CHANGING ANYTHING HERE:

  * A row is LIVE when it is neither revoked nor past `expires_at`.
  * `deps.get_current_user` trusts a row over the legacy column, ALWAYS. If a
    row exists for the presented jti, that row decides — a revoked row is a
    refusal even when `users.session_token` still happens to match.
  * Rotation is in place, on the caller's own row. It never creates a second
    row and never touches another device's.
  * Nothing here consults or confers authority. Roles and memberships are
    somebody else's job and stay that way.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.session_models import (CLIENT_TYPES, CLIENT_UNKNOWN,
                                       REVOKE_ADMIN, REVOKE_LOGOUT,
                                       REVOKE_LOGOUT_ALL,
                                       REVOKE_PASSWORD_CHANGE, REVOKE_REPLACED,
                                       UserSession)

_log = logging.getLogger(__name__)

# Matches auth_service.TOKEN_EXPIRY_HOURS. Imported lazily in _expiry() rather
# than at module scope because auth_service imports nothing from here and the
# reverse import would be a cycle waiting for the first person who adds one.
_DEFAULT_EXPIRY_HOURS = 24

# `last_seen_at` is display only. Writing it on literally every authenticated
# request would add a commit to the hot path of a 603-route API to keep a
# timestamp that is read on one screen. Once a minute is plenty.
_TOUCH_INTERVAL_SECONDS = 60

# A caller-supplied string that ends up in a database column gets a ceiling.
_MAX_FIELD = 200
_MAX_UA = 500


def _expiry_hours() -> int:
    try:
        from app.services.auth_service import TOKEN_EXPIRY_HOURS
        return int(TOKEN_EXPIRY_HOURS)
    except Exception:      # pragma: no cover - only if auth_service is broken
        return _DEFAULT_EXPIRY_HOURS


def _expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=_expiry_hours())


def _clip(value: Optional[str], limit: int = _MAX_FIELD) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    return value[:limit]


def _normalise_client(client: Optional[str]) -> str:
    c = (client or "").strip().lower()
    return c if c in CLIENT_TYPES else CLIENT_UNKNOWN


# ── creation ────────────────────────────────────────────────────────────────

def start_session(db: Session, user: User, *, jti: Optional[str] = None,
                  client: Optional[str] = None,
                  device_id: Optional[str] = None,
                  device_name: Optional[str] = None,
                  app_version: Optional[str] = None,
                  ip_address: Optional[str] = None,
                  user_agent: Optional[str] = None,
                  commit: bool = True) -> UserSession:
    """Open a session for this user and return the row.

    ONE ROW PER INSTALL, NOT ONE PER SIGN-IN. When the caller identifies its
    install with `device_id`, that install's previous live rows are revoked as
    `replaced` first. Without it, a phone that reinstalls or signs in again
    every few weeks accumulates live credentials nobody can see or account for,
    and "sign out my other devices" stops meaning anything.

    A caller with no `device_id` — a browser, a script, a test — simply gets a
    new row, because there is nothing to match it to and guessing would revoke
    the wrong one.
    """
    jti = jti or str(uuid.uuid4())
    device_id = _clip(device_id)

    if device_id:
        prior = (db.query(UserSession)
                 .filter(UserSession.user_id == user.id,
                         UserSession.device_id == device_id,
                         UserSession.revoked_at.is_(None))
                 .all())
        for row in prior:
            row.revoked_at = datetime.now(timezone.utc)
            row.revoked_reason = REVOKE_REPLACED
            db.add(row)

    row = UserSession(
        user_id=user.id,
        jti=jti,
        client=_normalise_client(client),
        device_id=device_id,
        device_name=_clip(device_name),
        app_version=_clip(app_version, 50),
        ip_address=_clip(ip_address, 64),
        user_agent=_clip(user_agent, _MAX_UA),
        created_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
        expires_at=_expiry(),
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


# ── lookup ──────────────────────────────────────────────────────────────────

def find_by_jti(db: Session, jti: str) -> Optional[UserSession]:
    """The row backing this credential, live or not.

    Returning revoked rows is deliberate and is the whole reason `deps` can be
    strict: "a row exists and it is dead" and "no row exists at all" are
    different answers and must not be collapsed into one.
    """
    if not jti:
        return None
    return db.query(UserSession).filter(UserSession.jti == jti).first()


def user_has_sessions(db: Session, user_id: str) -> bool:
    """Has this user EVER had a row in this table — live, revoked or expired?

    THIS IS THE GUARD ON THE LEGACY COLUMN, and it is narrower than it looks.

    `deps.get_current_user` falls back to `users.session_token` when the
    presented jti names no row, so that a token minted before this table
    existed keeps working across the deploy. Left unqualified, that fallback
    says: "any jti equal to the column authenticates, row or no row" — and a
    jti can stop having a row in two ways that have nothing to do with being
    pre-migration.

      * A REFRESH RACE. Two rotations of one row interleave; the row keeps the
        second jti while the column keeps the first. The first token then has
        no row, matches the column, and authenticates — a second live
        credential out of one session, and one that revoking the row does not
        kill.
      * A DELETED ROW. `demo_environment` deletes `user_sessions` rows when it
        resets a demo, and any future retention sweep will delete expired and
        revoked ones. Deleting a revoked row must not be the thing that brings
        its token back to life.

    So the column is honoured only for a user with NO rows at all, which is
    exactly the pre-migration case it was added for. The moment a user has
    signed in once since this table shipped, their sessions are rows and the
    column is bookkeeping.
    """
    return db.query(UserSession.id).filter(
        UserSession.user_id == user_id).first() is not None


def live_sessions_for_user(db: Session, user_id: str) -> List[UserSession]:
    rows = (db.query(UserSession)
            .filter(UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None))
            .order_by(UserSession.created_at.desc())
            .all())
    return [r for r in rows if r.is_live]


def touch(db: Session, row: UserSession) -> None:
    """Record that the session was used, at most once a minute.

    Never raises. A bookkeeping timestamp must not be able to fail an otherwise
    valid request, so a write error here is logged and swallowed — the session
    is still valid, we just do not know exactly when it was last used.
    """
    try:
        now = datetime.now(timezone.utc)
        last = UserSession._aware(row.last_seen_at)
        if last is not None and (now - last).total_seconds() < _TOUCH_INTERVAL_SECONDS:
            return
        row.last_seen_at = now
        db.add(row)
        db.commit()
    except Exception:                      # pragma: no cover - defensive
        _log.debug("session touch failed for %s", getattr(row, "id", "?"),
                   exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass


# ── rotation ────────────────────────────────────────────────────────────────

def rotate(db: Session, row: UserSession, *, commit: bool = True) -> str:
    """Give this session a new jti and a fresh expiry. Returns the new jti.

    THIS IS WHAT MAKES REFRESH DEVICE-LOCAL. The desktop refreshing every 30
    minutes rewrites its own row and nothing else, so the phone's row — and the
    token in the phone's keychain — are exactly as valid afterwards as before.

    A dead session cannot rotate. Allowing it would mean a revoked credential
    could mint a live one by asking politely, which is the failure the whole
    revocation mechanism exists to prevent.
    """
    if not row.is_live:
        raise ValueError("Cannot rotate a revoked or expired session.")
    new_jti = str(uuid.uuid4())
    row.jti = new_jti
    row.expires_at = _expiry()
    row.last_seen_at = datetime.now(timezone.utc)
    db.add(row)
    if commit:
        db.commit()
    return new_jti


# ── revocation ──────────────────────────────────────────────────────────────

def revoke(db: Session, row: UserSession, reason: str = REVOKE_LOGOUT,
           *, commit: bool = True) -> None:
    """End one session. Idempotent — re-revoking keeps the original reason."""
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        row.revoked_reason = reason
        db.add(row)
        _deactivate_tokens_for_session(db, row.id)
        if commit:
            db.commit()


def revoke_all_for_user(db: Session, user_id: str, reason: str = REVOKE_ADMIN,
                        *, except_session_id: Optional[str] = None,
                        commit: bool = True) -> int:
    """End every live session for this user. Returns how many were ended.

    THE KILL SWITCH. Force-logout, account deactivation, a password change and
    an administrative reset all land here, and each of them ALSO clears
    `users.session_token` at its own call site so that a pre-migration token —
    one minted before this table existed, with no row of its own — dies with
    everything else. Multi-device support that quietly dropped either half of
    that would be a security regression wearing a feature's clothes.
    """
    rows = (db.query(UserSession)
            .filter(UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None))
            .all())
    now = datetime.now(timezone.utc)
    count = 0
    for row in rows:
        if except_session_id and row.id == except_session_id:
            continue
        row.revoked_at = now
        row.revoked_reason = reason
        db.add(row)
        _deactivate_tokens_for_session(db, row.id)
        count += 1
    if commit:
        db.commit()
    return count


def revoke_by_id(db: Session, user_id: str, session_id: str,
                 reason: str = REVOKE_LOGOUT) -> bool:
    """Revoke ONE of this user's own sessions by row id.

    Scoped by `user_id` in the same query rather than loaded-then-checked, so a
    caller naming somebody else's session id gets "not found" and learns
    nothing about whether it exists.
    """
    row = (db.query(UserSession)
           .filter(UserSession.id == session_id,
                   UserSession.user_id == user_id)
           .first())
    if row is None:
        return False
    revoke(db, row, reason)
    return True


# ── retention ───────────────────────────────────────────────────────────────

# How long a dead session stays readable before it is swept. Revoked rows are
# the only record of WHY a device stopped working, and "my phone signed itself
# out last week" is a support question whose answer is `revoked_reason`. Thirty
# days keeps that answerable; keeping them forever turns an auth table into an
# unbounded log.
SESSION_RETENTION_DAYS = int(os.environ.get("SESSION_RETENTION_DAYS", "30"))

# A ceiling per pass, so the sweep is a short bounded DELETE rather than one
# that takes a lock proportional to however long nobody ran it.
SESSION_PURGE_BATCH = int(os.environ.get("SESSION_PURGE_BATCH", "2000"))


def purge_dead_sessions(db: Session, *, retain_days: Optional[int] = None,
                        batch_limit: Optional[int] = None) -> int:
    """Delete sessions that have been revoked or expired for long enough.

    WHAT IT WILL NOT DO, AND WHY EACH ONE MATTERS:

      * IT NEVER DELETES A LIVE ROW. The filter is positive — revoked before
        the cutoff, or expired before the cutoff — not "everything except what
        I think is live". A sweep that can sign somebody out is worse than a
        table that grows.
      * IT NEVER DELETES A RECENT ONE. `revoked_reason` is the only record of
        why a device stopped working, so the retention window is the support
        window.
      * IT IS BOUNDED. At most `batch_limit` rows per pass, selected by id
        first so the DELETE names its rows explicitly instead of scanning to
        find them.

    Deleting a row no longer resurrects its token: `deps` honours the legacy
    column only for a user with no rows at all (see `user_has_sessions`). That
    ordering is what makes this safe to run at all.
    """
    days = SESSION_RETENTION_DAYS if retain_days is None else int(retain_days)
    limit = SESSION_PURGE_BATCH if batch_limit is None else int(batch_limit)
    if days < 1 or limit < 1:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ids = [row[0] for row in
           db.query(UserSession.id)
           .filter(or_(and_(UserSession.revoked_at.isnot(None),
                            UserSession.revoked_at < cutoff),
                       and_(UserSession.revoked_at.is_(None),
                            UserSession.expires_at < cutoff)))
           .limit(limit).all()]
    if not ids:
        return 0

    try:
        from app.models.device_models import DevicePushToken
        (db.query(DevicePushToken)
         .filter(DevicePushToken.session_id.in_(ids))
         .update({DevicePushToken.session_id: None},
                 synchronize_session=False))
    except Exception:                      # pragma: no cover - defensive
        _log.debug("push token detach skipped during session purge",
                   exc_info=True)

    deleted = (db.query(UserSession)
               .filter(UserSession.id.in_(ids))
               .delete(synchronize_session=False))
    db.commit()
    return int(deleted or 0)


def _deactivate_tokens_for_session(db: Session, session_id: str) -> None:
    """Stop pushing to the device whose session just ended.

    Best-effort by design: push registration is a convenience and must never be
    the reason a logout fails. Wrapped because `device_push_tokens` may not
    exist yet on a database that has not restarted since this shipped.
    """
    try:
        from app.models.device_models import DevicePushToken
        rows = (db.query(DevicePushToken)
                .filter(DevicePushToken.session_id == session_id,
                        DevicePushToken.is_active.is_(True))
                .all())
        for t in rows:
            t.is_active = False
            t.revoked_at = datetime.now(timezone.utc)
            db.add(t)
    except Exception:                      # pragma: no cover - defensive
        _log.debug("push token deactivation skipped for session %s",
                   session_id, exc_info=True)


# ── request-shaped helpers ──────────────────────────────────────────────────

# The headers a client uses to describe itself. Native apps are not subject to
# CORS, but these are added to main.py's BROWSER_HEADERS anyway so the web
# client can adopt them later without a preflight failure — the exact trap
# X-Workspace-Id fell into once already.
HEADER_CLIENT = "X-Client-Platform"
HEADER_DEVICE_ID = "X-Device-Id"
HEADER_DEVICE_NAME = "X-Device-Name"
HEADER_APP_VERSION = "X-App-Version"

CLIENT_HEADERS = (HEADER_CLIENT, HEADER_DEVICE_ID, HEADER_DEVICE_NAME,
                  HEADER_APP_VERSION)


def describe_client(request) -> dict:
    """Read the device description off a request. All of it is optional.

    None of these values is trusted for anything. They label a row so a person
    can recognise their own devices in a list, and a caller who lies about them
    has lied about a label.
    """
    if request is None:
        return {}
    h = request.headers
    return {
        "client": h.get(HEADER_CLIENT),
        "device_id": h.get(HEADER_DEVICE_ID),
        "device_name": h.get(HEADER_DEVICE_NAME),
        "app_version": h.get(HEADER_APP_VERSION),
        "ip_address": request.client.host if request.client else None,
        "user_agent": h.get("user-agent"),
    }
