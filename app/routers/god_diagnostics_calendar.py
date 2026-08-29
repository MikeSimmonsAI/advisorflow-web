"""Read-only answers to "whose calendar is this org actually using?"

Written because the question could not be answered without one. Greenland's
canonical advisor has BOTH Microsoft 365 and Google connected, and the provider
registry prefers Microsoft. So "we fixed the Google calendar" could be true and
completely irrelevant at the same time: availability would still be read from
Outlook and bookings still written there. Nothing in the product said so.

This endpoint reports STATE ONLY. It never returns a token, never returns a
refresh token's contents, and never triggers a write. `CalendarConnection` is
already modelled as state-without-secrets; this exposes exactly that plus the
registry's own resolution, so the answer shown is the answer production uses
rather than a second implementation that can drift from it.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import Organization, User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/god", tags=["AdvisorFlow Command Center"])


def _connection_rows(db: Session, user_id: str):
    try:
        from app.models.calendar_models import CalendarConnection
        rows = (db.query(CalendarConnection)
                .filter(CalendarConnection.user_id == user_id).all())
    except Exception:
        log.exception("calendar-diagnostics: could not read connections for %s",
                      user_id)
        return []
    out = []
    for r in rows:
        out.append({
            "provider": r.provider,
            "is_connected": bool(r.is_connected),
            "calendar_scope_ok": bool(r.calendar_scope_ok),
            # The mailbox we actually reached — this is the OAuth owner, and
            # the single most important field on the page. A calendar can be
            # "connected" to the wrong Google account and look perfect.
            "account_email": r.account_email,
            "calendar_id": r.calendar_id,
            "connected_at": r.connected_at.isoformat() if r.connected_at else None,
            "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
            "last_attempt_at": r.last_attempt_at.isoformat() if r.last_attempt_at else None,
            "last_error": r.last_error,
            "last_error_at": r.last_error_at.isoformat() if r.last_error_at else None,
            "failure_count": r.failure_count,
        })
    return out


def _advisor_report(db: Session, u: User, org: Organization) -> dict:
    from app.services.calendar_providers import (
        configured_provider_key, get_provider, is_external_calendar,
        resolve_provider_key)

    rep = {
        "user_id": u.id,
        "full_name": u.full_name,
        "email": u.email,
        "role": u.role,
        "is_active": bool(u.is_active),
        "booking_timezone": getattr(u, "booking_timezone", None),
        # The user-row flags the UI shows. Kept separate from the connection
        # rows on purpose: when the two disagree, that disagreement IS the bug.
        "user_flags": {
            "google_calendar_connected": bool(getattr(u, "google_calendar_connected", False)),
            "microsoft_365_connected": bool(getattr(u, "microsoft_365_connected", False)),
        },
        "tokens_present": {
            "google": bool(getattr(u, "google_oauth_refresh_token_encrypted", None)),
            "microsoft": bool(getattr(u, "microsoft_oauth_refresh_token_encrypted", None)),
        },
        "user_google_calendar_id": getattr(u, "google_calendar_id", None),
        "connections": _connection_rows(db, u.id),
    }

    try:
        cfg_key, cfg_src = configured_provider_key(db, u)
        rep["configured_provider"] = cfg_key
        rep["configured_provider_source"] = cfg_src
        rep["advisor_calendar_provider"] = getattr(u, "calendar_provider", None)
        rep["org_calendar_provider"] = getattr(org, "calendar_provider", None)
    except Exception as e:
        rep["configured_provider"] = None
        rep["configured_provider_error"] = str(e)

    try:
        key = resolve_provider_key(db, u)
        rep["resolved_provider"] = key
        rep["reads_external_calendar"] = bool(is_external_calendar(key))
    except Exception as e:
        rep["resolved_provider"] = None
        rep["resolve_error"] = str(e)
        rep["reads_external_calendar"] = False

    try:
        prov = get_provider(db, u, org)
        ready, why = prov.is_ready()
        rep["provider_ready"] = bool(ready)
        rep["provider_not_ready_reason"] = None if ready else (why or "unknown")
        rep["provider_resolved_key"] = getattr(prov, "resolved_key", None)
        rep["provider_calendar_id"] = None
        try:
            # Private, but this is the value the write actually targets and
            # there is no public accessor. Guarded so a provider without one
            # degrades to None instead of failing the whole report.
            rep["provider_calendar_id"] = prov._calendar_id()
        except Exception:
            pass
    except Exception as e:
        rep["provider_ready"] = False
        rep["provider_not_ready_reason"] = str(e)

    # The sentence a human needs. Everything above is evidence for it.
    cfg = rep.get("configured_provider")
    if cfg and rep.get("provider_resolved_key") not in (None, cfg):
        rep["verdict"] = (
            "CONFIGURED FOR %s BUT NOT USABLE. It resolved to %s instead, which "
            "means the chosen calendar has no live grant. Availability will "
            "report calendar_unavailable rather than silently using the other "
            "provider." % (cfg, rep.get("provider_resolved_key")))
    elif not rep.get("reads_external_calendar"):
        rep["verdict"] = ("NO EXTERNAL CALENDAR. Availability cannot be read; "
                          "this advisor would appear free at every hour.")
    elif not rep.get("provider_ready"):
        rep["verdict"] = ("Resolved to %s but the provider is not ready: %s"
                          % (rep.get("resolved_provider"),
                             rep.get("provider_not_ready_reason")))
    else:
        rep["verdict"] = ("Availability is read from and bookings are written "
                          "to %s (calendar %s)."
                          % (rep.get("resolved_provider"),
                             rep.get("provider_calendar_id") or "default"))
    return rep


@router.get("/calendar-diagnostics")
def god_calendar_diagnostics(
    organization_id: str = Query(..., description="Tenant organization to inspect."),
    user_id: Optional[str] = Query(None, description="Limit to one advisor."),
    db: Session = Depends(get_db),
    god: User = Depends(require_god),
):
    """Which calendar this organization's advisors actually use. Read-only.

    Returns state only — no tokens, no secrets, and no write of any kind.
    """
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    q = db.query(User).filter(User.organization_id == organization_id)
    if user_id:
        q = q.filter(User.id == user_id)
    users = q.order_by(User.full_name).all()
    if user_id and not users:
        raise HTTPException(status_code=404,
                            detail="User not found in that organization.")

    log.info("AUDIT: GOD_CALENDAR_DIAGNOSTICS | admin=%s | org=%s | user=%s",
             god.email, organization_id, user_id or "all")

    reports = [_advisor_report(db, u, org) for u in users]

    # The registry's preference order, stated rather than implied. An advisor
    # with two live connections uses the first one in this tuple, which is the
    # detail that makes "we fixed Google" a possibly-irrelevant sentence.
    try:
        from app.services.calendar_providers import PREFERENCE
        preference = list(PREFERENCE)
    except Exception:
        preference = []

    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "organization_calendar_provider": getattr(org, "calendar_provider", None),
        "provider_preference_order": preference,
        "preference_note": ("Only consulted when no calendar_provider is "
                            "configured on the advisor or the organization."),
        "advisor_count": len(reports),
        "advisors": reports,
    }


@router.get("/email-diagnostics")
def god_email_diagnostics(
    organization_id: str = Query(..., description="Tenant organization to inspect."),
    db: Session = Depends(get_db),
    god: User = Depends(require_god),
):
    """What address this organization's mail actually leaves under. Read-only.

    SENDS NOTHING. The existing `/email/system-check` answers a similar
    question by delivering a live test message, which makes it unusable for an
    audit: you cannot ask "what would happen?" without it happening. This
    reports the resolved identity, the environment's presence (never values of
    secrets), and which domains the sending provider has actually verified —
    the last one being the difference between a configured from-address and a
    deliverable one.
    """
    import os

    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")

    from app.services.public_identity import identity_for_org
    ident = identity_for_org(db, organization_id)
    out = {"resolved": ident.as_dict()}

    resend_key = (ident.resend_api_key or os.environ.get("RESEND_API_KEY", "")).strip()
    out["env"] = {
        # Presence only. The value of a key is never reported.
        "RESEND_API_KEY_set": bool(os.environ.get("RESEND_API_KEY", "").strip()),
        # An address is not a secret, and this one is the whole reason a
        # customer's mail can go out under the wrong brand.
        "EMAIL_FROM_ADDRESS": os.environ.get("EMAIL_FROM_ADDRESS", "").strip() or None,
        "org_resend_key_set": bool(ident.resend_api_key),
    }

    # Which domains can actually send. A from-address on an unverified domain
    # is configuration that looks correct and delivers nothing.
    verified = None
    domain_ok = None
    err = None
    if resend_key:
        try:
            import httpx
            resp = httpx.get("https://api.resend.com/domains",
                             headers={"Authorization": "Bearer %s" % resend_key},
                             timeout=10)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                verified = sorted({d.get("name", "") for d in data
                                   if d.get("status") == "verified"})
                addr = ident.from_email or ""
                dom = addr.split("@")[-1].lower() if "@" in addr else ""
                domain_ok = bool(dom) and any(
                    dom == n.lower() or dom.endswith("." + n.lower())
                    for n in verified)
            else:
                err = "Resend returned %s" % resp.status_code
        except Exception as e:  # network, library, anything
            err = str(e)
    else:
        err = "no Resend API key available to query verified domains"

    out["sending_domains"] = {
        "verified": verified,
        "resolved_from_domain_is_verified": domain_ok,
        "error": err,
    }

    advisors = (db.query(User)
                .filter(User.organization_id == organization_id,
                        User.is_active.is_(True)).all())
    out["advisors"] = [{
        "user_id": u.id,
        "full_name": u.full_name,
        "email": u.email,
        "notification_email": getattr(u, "notification_email", None),
        "microsoft_365_connected": bool(getattr(u, "microsoft_365_connected", False)),
    } for u in advisors]

    log.info("AUDIT: GOD_EMAIL_DIAGNOSTICS | admin=%s | org=%s",
             god.email, organization_id)
    return out
