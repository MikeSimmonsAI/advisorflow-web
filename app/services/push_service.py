"""Push delivery for the EvoSys Pro mobile app.

WHAT THIS IS AND IS NOT
-----------------------
This is a DISPATCHER, not a notification system. The platform already has one:
`notifications` rows, created by notification_service, read by the web
NotificationBell. This module takes something that already happened and taps a
device on the shoulder about it. If it fails, the notification still exists and
the app still shows it on next load — push is the fastest way to learn, never
the only way.

THE PAYLOAD RULE, WHICH IS NOT NEGOTIABLE
-----------------------------------------
A push carries a SHORT TITLE, A CATEGORY AND IDS. Never a lead's name, never a
message body, never an amount, never an organisation's name. Two reasons, and
the second is the one people forget:

  1. A push renders on a locked screen, in a notification mirror on somebody's
     laptop, and in whatever notification history the OS keeps. None of those
     places is inside the authorisation boundary.
  2. Delivery is asynchronous. Authority is checked when the notification is
     CREATED, but a device can receive it minutes later — after a membership was
     revoked, after a workspace was switched, after a phone changed hands. The
     tap therefore fetches the record through the normal authorised endpoint and
     the server decides then. Possession of an id in a payload grants nothing,
     exactly as it grants nothing in a deep link.

`build_payload` is the only way to construct one, and it drops anything not on
the allowed key list rather than trusting callers to remember.

CONFIGURATION
-------------
Expo's push service needs no secret for unauthenticated sends, but sending is
still OFF unless EXPO_PUSH_ENABLED is truthy. A background job that quietly
starts talking to a third party the first time it is deployed is not a thing
this codebase should do by accident, and a staging environment sharing a
database with real device tokens would otherwise wake real phones.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.device_models import (DevicePushToken, PROVIDER_EXPO)

_log = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_EXPO_BATCH = 100          # Expo's documented maximum per request
_HTTP_TIMEOUT = 10


def push_enabled() -> bool:
    return str(os.environ.get("EXPO_PUSH_ENABLED", "")).strip().lower() in (
        "1", "true", "yes", "on")


# ── categories ──────────────────────────────────────────────────────────────
#
# One per thing worth interrupting somebody for. The app maps a category to a
# destination; the server never sends a URL, because a URL in a payload is an
# instruction and these are notifications.
CAT_APPOINTMENT = "appointment"
CAT_LEAD = "lead"
CAT_PROPOSAL = "proposal"
CAT_APPROVAL = "approval"
CAT_COMPENSATION = "compensation"
CAT_EXCEPTION = "exception"
CAT_SYSTEM = "system"
CATEGORIES = (CAT_APPOINTMENT, CAT_LEAD, CAT_PROPOSAL, CAT_APPROVAL,
              CAT_COMPENSATION, CAT_EXCEPTION, CAT_SYSTEM)

# The ONLY keys allowed in the data envelope. Anything else is dropped by
# build_payload — an allow-list, because a deny-list of PII fields is a list
# somebody eventually forgets to extend.
_ALLOWED_DATA_KEYS = ("category", "record_type", "record_id",
                      "notification_id", "context", "scope_id")


def build_payload(*, category: str, title: str,
                  record_type: Optional[str] = None,
                  record_id: Optional[str] = None,
                  notification_id: Optional[str] = None,
                  context: Optional[str] = None,
                  scope_id: Optional[str] = None,
                  body: Optional[str] = None,
                  **ignored: Any) -> Dict[str, Any]:
    """Assemble a payload that cannot carry content.

    `**ignored` SWALLOWS AND DROPS, IT DOES NOT RAISE. Every caller is
    mid-transaction on something that matters more than a badge — a booked
    appointment, a sent proposal — and a dispatcher that raises TypeError
    because somebody helpfully passed `lead_name=` would take that transaction
    down with it. So an unrecognised field is discarded silently and the
    allow-list below is the enforcement, not the signature.

    `title` and the optional `body` are the only free text, and both are meant
    to be generic — "New reply on a lead", not "Reply from Angela Ruiz". They
    are truncated hard, because a long free-text field is where content ends up
    hiding.
    """
    cat = category if category in CATEGORIES else CAT_SYSTEM
    data = {
        "category": cat,
        "record_type": record_type,
        "record_id": record_id,
        "notification_id": notification_id,
        "context": context,
        "scope_id": scope_id,
    }
    data = {k: v for k, v in data.items()
            if k in _ALLOWED_DATA_KEYS and v is not None}
    payload = {"title": (title or "EvoSys Pro")[:80], "data": data}
    if body:
        payload["body"] = body[:120]
    return payload


# ── registration ────────────────────────────────────────────────────────────

def register_token(db: Session, user, *, token: str, platform: str,
                   session_id: Optional[str] = None,
                   device_id: Optional[str] = None,
                   device_name: Optional[str] = None,
                   app_version: Optional[str] = None,
                   active_context: Optional[str] = None,
                   active_scope_id: Optional[str] = None,
                   provider: str = PROVIDER_EXPO) -> DevicePushToken:
    """Record (or re-home) a push token for this user.

    A TOKEN MOVES; IT IS NEVER DUPLICATED. Phones get reset, sold and handed to
    a new hire, and the push token can survive that. If the row exists under a
    different user, it is reassigned here — the alternative is one physical
    device holding two live registrations and receiving two people's
    notifications, which is a data leak with a lock screen for a viewport.
    """
    now = datetime.now(timezone.utc)
    row = (db.query(DevicePushToken)
           .filter(DevicePushToken.token == token)
           .first())
    if row is None:
        row = DevicePushToken(token=token)
        db.add(row)
    elif row.user_id and row.user_id != user.id:
        _log.info("push token reassigned from user=%s to user=%s",
                  row.user_id, user.id)

    row.user_id = user.id
    row.session_id = session_id
    row.provider = provider
    row.platform = platform
    row.device_id = device_id
    row.device_name = device_name
    row.app_version = app_version
    row.active_context = active_context
    row.active_scope_id = active_scope_id
    row.is_active = True
    row.revoked_at = None
    row.failure_reason = None
    row.last_seen_at = now
    db.commit()
    db.refresh(row)
    return row


def deactivate_token(db: Session, user, token: str) -> bool:
    """Stop pushing to a token, scoped to its owner.

    Filtered on user_id in the same query rather than loaded-then-checked, so
    naming somebody else's token is a no-op that reveals nothing about whether
    it exists.
    """
    row = (db.query(DevicePushToken)
           .filter(DevicePushToken.token == token,
                   DevicePushToken.user_id == user.id)
           .first())
    if row is None:
        return False
    row.is_active = False
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True


def tokens_for_user(db: Session, user_id: str) -> List[DevicePushToken]:
    return (db.query(DevicePushToken)
            .filter(DevicePushToken.user_id == user_id,
                    DevicePushToken.is_active.is_(True))
            .all())


# ── delivery ────────────────────────────────────────────────────────────────

def send_to_user(db: Session, user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Deliver one payload to every active device of ONE user.

    Returns a report rather than raising. A notification that cannot be pushed
    is a degraded experience; a notification that takes down the request that
    created it is an outage, and the caller is usually mid-transaction on
    something that matters more than a badge.
    """
    rows = tokens_for_user(db, user_id)
    if not rows:
        return {"sent": 0, "skipped": "no_devices"}
    if not push_enabled():
        # Not an error and not silence: the payload was built and addressed, and
        # the only missing piece is deployment configuration.
        _log.info("push suppressed (EXPO_PUSH_ENABLED off): user=%s category=%s",
                  user_id, payload.get("data", {}).get("category"))
        return {"sent": 0, "skipped": "push_disabled", "devices": len(rows)}
    return _dispatch(db, rows, payload)


def _dispatch(db: Session, rows: Iterable[DevicePushToken],
              payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = list(rows)
    messages = [{
        "to": r.token,
        "title": payload.get("title"),
        "body": payload.get("body", ""),
        "data": payload.get("data", {}),
        "sound": "default",
        "priority": "high",
    } for r in rows]

    sent, failed = 0, 0
    try:
        import requests
    except Exception:                       # pragma: no cover - requests is a dep
        _log.warning("requests unavailable; push not sent")
        return {"sent": 0, "skipped": "no_http_client"}

    for i in range(0, len(messages), _EXPO_BATCH):
        chunk = messages[i:i + _EXPO_BATCH]
        try:
            resp = requests.post(
                EXPO_PUSH_URL,
                data=json.dumps(chunk),
                headers={"Content-Type": "application/json",
                         "Accept": "application/json"},
                timeout=_HTTP_TIMEOUT,
            )
            body = resp.json() if resp.content else {}
            results = body.get("data", []) if isinstance(body, dict) else []
            for row, result in zip(rows[i:i + _EXPO_BATCH], results):
                if isinstance(result, dict) and result.get("status") == "ok":
                    sent += 1
                    continue
                failed += 1
                detail = (result or {}).get("details", {}) if isinstance(result, dict) else {}
                # DeviceNotRegistered is the provider telling us the app is
                # gone. Retrying it forever is how a token table fills with
                # addresses nobody lives at.
                if detail.get("error") == "DeviceNotRegistered":
                    row.is_active = False
                    row.revoked_at = datetime.now(timezone.utc)
                    row.failure_reason = "DeviceNotRegistered"
                    db.add(row)
            db.commit()
        except Exception as exc:            # pragma: no cover - network path
            failed += len(chunk)
            _log.warning("push batch failed: %s", exc)

    return {"sent": sent, "failed": failed, "devices": len(rows)}


def notify(db: Session, user_id: str, *, category: str, title: str,
           **kwargs) -> Dict[str, Any]:
    """Convenience entry point: build a compliant payload and deliver it."""
    return send_to_user(db, user_id,
                        build_payload(category=category, title=title, **kwargs))
