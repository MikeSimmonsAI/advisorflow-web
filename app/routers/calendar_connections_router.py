"""
Per-user calendar connection state, for the Sales Workspace UI.

A CONNECTION BELONGS TO A USER, NOT TO A TENANT. Every endpoint here reads and
writes only `current_user`'s own rows. There is deliberately no org_id in any
query and no way to name someone else's user id: a sales manager may see that a
rep's calendar sync failed on a shared meeting, but must never be able to
inspect or revoke that rep's personal Microsoft account connection.

WHAT THIS ROUTER DOES NOT DO
----------------------------
It does not start OAuth. The Microsoft and Google authorization flows already
exist (`microsoft_router`, `calendar_router`) and are the single place consent
is requested — adding a second entry point would mean two sets of redirect URIs
and two places to get the scopes wrong. This router reports STATE and hands the
UI the existing connect URLs.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User
from app.models.calendar_models import (
    CalendarConnection, ExternalBusyBlock,
    PROVIDER_MICROSOFT, PROVIDER_GOOGLE, PROVIDER_LABELS, PROVIDERS,
)
from app.services import calendar_providers as reg
from app.services import external_busy as eb

log = logging.getLogger(__name__)

# WHO MAY CALL THIS, AND WHY IT WIDENED.
#
# It was `require_sales_member`, because the Sales Workspace was the first UI
# that needed it. But a funeral home's advisor has exactly the same question -
# "which calendar am I on, is it working, how do I disconnect it?" - and had no
# way to ask it: they could START an OAuth flow and never see the result or
# undo it. That is how an advisor ends up connected to Outlook without meaning
# to and with no button to change it.
#
# Widening the audience is safe here because it changes nothing about SCOPE:
# every endpoint below reads and writes `user.id` only, there is no org_id in
# any query, and no endpoint accepts someone else's user id. A person managing
# their own calendar connection is not a privileged action - it is the same
# person who granted the consent withdrawing it.
_caller = get_current_user

# One set of endpoints, mounted twice by main.py - under /sales/calendar, which
# the Sales Workspace already calls, and under /me/calendar for everyone else.
# The prefix therefore lives at the mount, not here. A second implementation is
# how two answers to the same question drift apart.
router = APIRouter(tags=["calendar-connections"])

# Where the UI sends someone to grant access. These are the EXISTING flows.
CONNECT_PATHS = {
    PROVIDER_MICROSOFT: "/microsoft/oauth/authorize",
    PROVIDER_GOOGLE:    "/calendar/oauth/authorize",
}


def _token_field(provider: str) -> str:
    return ("microsoft_oauth_refresh_token_encrypted" if provider == PROVIDER_MICROSOFT
            else "google_oauth_refresh_token_encrypted")


def _conn_out(user: User, provider: str, conn: Optional[CalendarConnection]) -> dict:
    """One provider's state for one user.

    `has_token` and `is_connected` are reported SEPARATELY on purpose. A user
    can hold a live Microsoft token that works for email and not for calendar,
    because the calendar scope was added after they consented. Collapsing those
    into one boolean is exactly how a UI ends up claiming CONNECTED while every
    sync 403s.
    """
    has_token = bool(getattr(user, _token_field(provider), None))
    scope_ok = bool(conn and conn.calendar_scope_ok)
    connected = bool(conn and conn.is_connected)

    if connected and scope_ok:
        state, detail = "connected", None
    elif has_token and not scope_ok:
        state = "reconnect_required"
        detail = ("Connected for email, but not yet for calendar. Reconnect to "
                  "grant calendar access.")
    elif has_token:
        state, detail = "reconnect_required", "Reconnect to restore calendar access."
    else:
        state, detail = "not_connected", None

    return {
        "provider": provider,
        "label": PROVIDER_LABELS.get(provider, provider),
        "state": state,
        "detail": detail,
        "is_connected": connected,
        "has_token": has_token,
        "calendar_scope_ok": scope_ok,
        "account_email": conn.account_email if conn else None,
        "connected_at": conn.connected_at if conn else None,
        "last_sync_at": conn.last_sync_at if conn else None,
        "last_attempt_at": conn.last_attempt_at if conn else None,
        "last_error": conn.last_error if conn else None,
        "failure_count": (conn.failure_count or 0) if conn else 0,
        "connect_url": CONNECT_PATHS.get(provider),
    }


@router.get("/connections")
def list_my_connections(user: User = Depends(_caller),
                        db: Session = Depends(get_db)):
    """Every provider, whether connected or not.

    Returns a row for each provider rather than only the connected ones, so the
    UI renders a complete, honest picture — "Google Calendar · not connected" is
    information the user needs in order to connect it.
    """
    rows = {c.provider: c for c in db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == user.id).all()}
    connections = [_conn_out(user, p, rows.get(p)) for p in PROVIDERS]
    active = reg.resolve_provider_key(db, user)

    # The DELIBERATE choice, reported separately from what is actually in use.
    # When an organization is configured for Google and Google cannot be read,
    # those two answers differ - and the UI needs to be able to say so rather
    # than showing whichever one happens to be convenient.
    configured, configured_source = (None, None)
    try:
        configured, configured_source = reg.configured_provider_key(db, user)
    except Exception:
        log.warning("connections: could not read the configured provider", exc_info=True)

    return {
        "connections": connections,
        "configured_provider": configured,
        "configured_provider_source": configured_source,
        # What WOULD be used for this user's next meeting. The .ics fallback is
        # named plainly rather than shown as an absence.
        "active_provider": active,
        "active_label": PROVIDER_LABELS.get(active, active),
        "uses_email_fallback": not reg.is_external_calendar(active),
        "fallback_explainer": (
            "Meetings are emailed to you as calendar invitations. Connect a "
            "calendar to have them added automatically and to have your existing "
            "commitments considered when the team looks for a shared time."),
    }


@router.post("/connections/{provider}/test")
def test_my_connection(provider: str,
                       user: User = Depends(_caller),
                       db: Session = Depends(get_db)):
    """Actually read the calendar and report what happened.

    A real round-trip, not a token-presence check. The whole failure mode this
    checkpoint exists to prevent is a UI that says CONNECTED because a row says
    so, while every write silently 403s. The only way to know is to ask.
    """
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown calendar provider.")
    if not getattr(user, _token_field(provider), None):
        raise HTTPException(status_code=400,
                            detail="%s is not connected." % PROVIDER_LABELS.get(provider, provider))

    now = datetime.utcnow()
    report = eb.refresh_external_busy(db, user, now, now + timedelta(days=7),
                                      now_utc=now, force=True)
    db.commit()

    conn = (db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == user.id,
                    CalendarConnection.provider == provider).first())
    ok = bool(report.get("refreshed"))
    return {
        "ok": ok,
        "provider": provider,
        "message": ("We read your calendar successfully." if ok else
                    "We could not read your calendar. "
                    + ("Reconnect to grant calendar access."
                       if report.get("needs_reauth") else
                       "This may be temporary — try again shortly.")),
        "busy_blocks_found": report.get("count", 0),
        "error_code": report.get("error"),
        "connection": _conn_out(user, provider, conn),
    }


@router.post("/connections/{provider}/disconnect")
def disconnect_my_calendar(provider: str,
                           user: User = Depends(_caller),
                           db: Session = Depends(get_db)):
    """Stop using this calendar. Only ever the CALLER'S own.

    Clears the stored refresh token and the cached busy blocks. Existing
    appointments keep their `external_event_id` deliberately: those events are
    still sitting on the person's real calendar, and discarding the ids would
    make a later cancellation unable to withdraw them.

    Does NOT revoke consent at the provider — that is the user's to do in their
    Microsoft or Google account, and a button here claiming to have done it
    would be a lie about someone else's system.
    """
    if provider not in PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown calendar provider.")

    setattr(user, _token_field(provider), None)
    if provider == PROVIDER_GOOGLE and hasattr(user, "google_calendar_connected"):
        user.google_calendar_connected = False

    conn = (db.query(CalendarConnection)
            .filter(CalendarConnection.user_id == user.id,
                    CalendarConnection.provider == provider).first())
    now = datetime.utcnow()
    if conn is not None:
        conn.is_connected = False
        conn.calendar_scope_ok = False
        conn.disconnected_at = now
        conn.last_error = None
        conn.busy_window_start = None
        conn.busy_window_end = None
        conn.busy_fetched_at = None

    # Their private commitments must not linger in our database after they
    # have withdrawn access to the calendar those commitments came from.
    db.query(ExternalBusyBlock).filter(
        ExternalBusyBlock.user_id == user.id,
        ExternalBusyBlock.provider == provider).delete(synchronize_session=False)

    db.commit()

    # FAILS CLOSED, AND SAYS SO.
    #
    # Disconnecting the provider the organization is configured for does NOT
    # hand scheduling to the other one. `configured_provider_key` still answers
    # with the deliberate choice, and availability then reports "unreadable"
    # rather than offering a family every working hour against a calendar
    # nobody can see. That is the correct outcome and it is also a surprising
    # one, so it is stated here rather than discovered later.
    configured, source = (None, None)
    try:
        configured, source = reg.configured_provider_key(db, user)
    except Exception:
        log.warning("disconnect: could not read the configured provider", exc_info=True)

    warning = None
    if configured and configured == provider:
        warning = (
            "%s is still the calendar this %s is configured to use, so "
            "scheduling will now report availability as unavailable rather "
            "than falling back to another calendar. Reconnect it, or change "
            "the scheduling calendar in Settings."
            % (PROVIDER_LABELS.get(provider, provider),
               "advisor" if source == "advisor" else "organization")
        )

    return {"ok": True, "connection": _conn_out(user, provider, conn),
            "configured_provider": configured,
            "configured_provider_source": source,
            "warning": warning,
            "note": "Disconnected. Meetings already on your calendar were left "
                    "in place. To fully revoke access, remove AdvisorFlow in "
                    "your %s account settings."
                    % PROVIDER_LABELS.get(provider, provider)}
