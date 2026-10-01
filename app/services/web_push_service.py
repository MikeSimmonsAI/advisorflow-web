"""Web Push (VAPID) delivery for the mobile/PWA shell.

WHY A SEPARATE MODULE FROM push_service.py
------------------------------------------
`push_service.py` is the Expo dispatcher for the native app (device tokens,
EXPO_PUSH_ENABLED). Browser push is a different transport — an endpoint URL
plus p256dh/auth keys, signed with a VAPID key pair, revoked by the push
service answering 404/410. This module reuses push_service's payload rule (a
short generic title and ids, nothing else) and adds the outbox.

THE FLOW
--------
1. Something real happens that already creates an in-app `notifications` row
   (an email reply on an assigned lead, a hot SMS reply). The creator calls
   `enqueue_for_notification(db, notification, ...)` — one line, no commit.
2. That writes ONE `push_events` row per target user, keyed by an idempotency
   key derived from the notification, so retries never double-buzz.
3. `deliver_pending` (background loop, see the S15 snippet) or `send_test`
   delivers. When VAPID keys are absent or pywebpush is not installed, delivery
   is SKIPPED and the event is marked `not_configured` — nothing is sent and
   nothing pretends it was.

CONFIGURATION (env, never code): VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY,
VAPID_SUBJECT (a mailto: or https: contact). All three plus an importable
pywebpush = configured. Anything less = configured:false.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.push_models import (
    EVENT_FAILED, EVENT_NO_SUBSCRIPTIONS, EVENT_NOT_CONFIGURED, EVENT_PARTIAL,
    EVENT_PENDING, EVENT_SENT, TYPE_GENERIC, TYPE_HOT_REPLY, TYPE_REPLY,
    TYPE_TEST, PushEvent, PushSubscription)

_log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
TTL_SECONDS = 3600
DEFAULT_URL = "/m"

# Generic titles. The ONLY free text a push carries. No names, no bodies.
TITLES = {
    TYPE_HOT_REPLY: "Hot reply on one of your leads",
    TYPE_REPLY: "New reply on one of your leads",
    TYPE_TEST: "Test alert",
    TYPE_GENERIC: "New activity",
}


class PushSendError(Exception):
    """Normalised delivery failure. `status_code` is the push service's HTTP
    status when there was one (404/410 = subscription gone)."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


# A sender: (subscription_info, data_json, private_key, claims, ttl) -> None,
# raising PushSendError on failure.
Sender = Callable[[dict, str, str, dict, int], None]


def _now():
    return datetime.now(timezone.utc)


# ── configuration ───────────────────────────────────────────────────────────

def vapid_settings() -> Dict[str, Optional[str]]:
    def _env(k):
        v = (os.environ.get(k) or "").strip()
        return v or None
    return {"public_key": _env("VAPID_PUBLIC_KEY"),
            "private_key": _env("VAPID_PRIVATE_KEY"),
            "subject": _env("VAPID_SUBJECT")}


def sender_available() -> bool:
    try:
        import pywebpush  # noqa: F401
        return True
    except Exception:
        return False


def keys_present() -> bool:
    s = vapid_settings()
    return bool(s["public_key"] and s["private_key"] and s["subject"])


def is_configured() -> bool:
    return keys_present() and sender_available()


def config_status() -> dict:
    """Public config. Never includes the private key."""
    configured = is_configured()
    reason = None
    if not keys_present():
        reason = "VAPID keys are not set on this server."
    elif not sender_available():
        reason = "The web push library is not installed on this server."
    pub = vapid_settings()["public_key"] if configured else None
    return {"configured": configured, "enabled": configured,
            "vapid_public_key": pub, "public_key": pub, "reason": reason}


def _pywebpush_sender(sub_info: dict, data: str, private_key: str,
                      claims: dict, ttl: int) -> None:  # pragma: no cover - needs lib
    from pywebpush import WebPushException, webpush
    try:
        webpush(subscription_info=sub_info, data=data,
                vapid_private_key=private_key, vapid_claims=dict(claims), ttl=ttl,
                timeout=10)
    except WebPushException as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        raise PushSendError(str(exc)[:200], code)
    except Exception as exc:
        raise PushSendError(str(exc)[:200], None)


# ── subscriptions ───────────────────────────────────────────────────────────

def _hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def subscribe(db: Session, user, *, organization_id: Optional[str], endpoint: str,
              p256dh: str, auth: str, user_agent: Optional[str] = None,
              platform: str = "web") -> PushSubscription:
    """Record (or re-home) this browser's endpoint for THIS user.

    An endpoint is one browser profile. If it is already recorded for another
    user (shared browser, new sign-in) it MOVES — the previous owner's alerts
    must not keep arriving on a browser somebody else is now using."""
    h = _hash(endpoint)
    row = db.query(PushSubscription).filter(PushSubscription.endpoint_hash == h).first()
    if row is None:
        row = PushSubscription(endpoint=endpoint, endpoint_hash=h)
        db.add(row)
    elif row.user_id and row.user_id != user.id:
        _log.info("web push endpoint moved between users")
    row.user_id = user.id
    row.organization_id = organization_id
    row.p256dh = p256dh
    row.auth = auth
    row.user_agent = (user_agent or "")[:300] or None
    row.platform = (platform or "web")[:20]
    row.revoked_at = None
    row.revoked_reason = None
    row.failure_count = 0
    db.commit()
    db.refresh(row)
    return row


def unsubscribe(db: Session, user, *, endpoint: Optional[str] = None,
                subscription_id: Optional[str] = None,
                endpoint_hash: Optional[str] = None) -> int:
    """Revoke the caller's own subscription(s). Filtered on user_id in the same
    query: naming somebody else's endpoint or id is a no-op."""
    q = db.query(PushSubscription).filter(PushSubscription.user_id == user.id,
                                          PushSubscription.revoked_at.is_(None))
    if subscription_id:
        q = q.filter(PushSubscription.id == subscription_id)
    elif endpoint:
        q = q.filter(PushSubscription.endpoint_hash == _hash(endpoint))
    elif endpoint_hash:
        q = q.filter(PushSubscription.endpoint_hash == endpoint_hash)
    else:
        return 0
    n = 0
    for row in q.all():
        row.revoked_at = _now()
        row.revoked_reason = "user_unsubscribed"
        n += 1
    db.commit()
    return n


def live_subscriptions(db: Session, user_id: str,
                       organization_id: Optional[str] = None,
                       any_workspace: bool = False) -> List[PushSubscription]:
    q = db.query(PushSubscription).filter(PushSubscription.user_id == user_id,
                                          PushSubscription.revoked_at.is_(None))
    rows = q.all()
    if any_workspace:
        return rows
    # TENANT SCOPE: a subscription enabled in workspace A does not receive
    # workspace B's alerts. A row with no recorded workspace receives its
    # owner's alerts (legacy single-workspace user).
    return [r for r in rows if r.organization_id in (None, organization_id)]


# ── outbox ──────────────────────────────────────────────────────────────────

def enqueue(db: Session, *, target_user_id: str, organization_id: Optional[str],
            event_type: str, idempotency_key: str, title: Optional[str] = None,
            url: Optional[str] = None, notification_id: Optional[str] = None,
            record_type: Optional[str] = None, record_id: Optional[str] = None
            ) -> Optional[PushEvent]:
    """Add one outbox row. Idempotent on `idempotency_key`. Does NOT commit —
    the caller's transaction owns it, so a rolled-back reply leaves no push.
    Returns the existing row on a repeat."""
    if not target_user_id or not idempotency_key:
        return None
    key = idempotency_key[:200]
    existing = db.query(PushEvent).filter(PushEvent.idempotency_key == key).first()
    if existing is None:
        # Also catch a row added earlier in this same unflushed session.
        for obj in db.new:
            if isinstance(obj, PushEvent) and obj.idempotency_key == key:
                return obj
    if existing is not None:
        return existing
    safe_url = url if (isinstance(url, str) and url.startswith("/m")) else DEFAULT_URL
    ev = PushEvent(id=str(uuid.uuid4()), organization_id=organization_id,
                   target_user_id=target_user_id, type=event_type,
                   title=(title or TITLES.get(event_type) or TITLES[TYPE_GENERIC])[:80],
                   url=safe_url[:200], notification_id=notification_id,
                   record_type=record_type, record_id=record_id,
                   status=EVENT_PENDING, attempts=0, idempotency_key=key)
    db.add(ev)
    return ev


def enqueue_for_notification(db: Session, notification, *, organization_id: Optional[str] = None,
                             event_type: Optional[str] = None) -> Optional[PushEvent]:
    """THE GENERIC HOOK. Call right after adding an in-app Notification row.
    A push is a delivery of that notification, never a new source of truth.
    Never raises — a missing push must never break the caller."""
    try:
        if notification is None or not getattr(notification, "user_id", None):
            return None
        if not getattr(notification, "id", None):
            notification.id = str(uuid.uuid4())
        ntype = getattr(getattr(notification, "type", None), "value",
                        getattr(notification, "type", None))
        etype = event_type or {"hot_reply": TYPE_HOT_REPLY,
                               "reply_received": TYPE_REPLY}.get(ntype, TYPE_GENERIC)
        lead_id = getattr(notification, "lead_id", None)
        url = ("/m/conversations/%s" % lead_id) if lead_id else "/m/notifications"
        org_id = organization_id
        if org_id is None and lead_id:
            from app.models.models import Lead
            lead = db.query(Lead).filter(Lead.id == lead_id).first()
            org_id = getattr(lead, "organization_id", None)
        return enqueue(db, target_user_id=notification.user_id, organization_id=org_id,
                       event_type=etype, idempotency_key="notif:%s" % notification.id,
                       url=url, notification_id=notification.id,
                       record_type="lead" if lead_id else None, record_id=lead_id)
    except Exception:  # noqa: BLE001
        _log.exception("web push enqueue failed")
        return None


def payload_for(ev: PushEvent) -> dict:
    """The exact JSON the service worker receives (see frontend/public/sw.js).
    Allow-listed keys only."""
    return {"title": ev.title, "url": ev.url or DEFAULT_URL,
            "notification_id": ev.notification_id, "event_id": ev.id,
            "workspace_id": ev.organization_id, "type": ev.type}


# ── delivery ────────────────────────────────────────────────────────────────

def deliver_event(db: Session, ev: PushEvent, *, sender: Optional[Sender] = None,
                  subscriptions: Optional[List[PushSubscription]] = None) -> dict:
    """Deliver one event. A `sender` may be injected (tests); otherwise the
    pywebpush adapter is used ONLY when configured. Commits."""
    ev.last_attempt_at = _now()
    if sender is None:
        if not is_configured():
            ev.status = EVENT_NOT_CONFIGURED
            ev.last_error = "Web push is not configured on this server."
            db.commit()
            return {"status": ev.status, "sent": 0}
        sender = _pywebpush_sender
    subs = subscriptions if subscriptions is not None else live_subscriptions(
        db, ev.target_user_id, ev.organization_id)
    if not subs:
        ev.status = EVENT_NO_SUBSCRIPTIONS
        db.commit()
        return {"status": ev.status, "sent": 0}
    s = vapid_settings()
    claims = {"sub": s["subject"] or "mailto:not-configured@invalid"}
    data = json.dumps(payload_for(ev))
    ev.attempts = (ev.attempts or 0) + 1
    sent = failed = revoked = 0
    errors = []
    for sub in subs:
        info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
        try:
            sender(info, data, s["private_key"] or "", claims, TTL_SECONDS)
            sent += 1
            sub.last_success_at = _now()
            sub.failure_count = 0
        except PushSendError as exc:
            failed += 1
            sub.last_failure_at = _now()
            sub.failure_count = (sub.failure_count or 0) + 1
            if exc.status_code in (404, 410):
                sub.revoked_at = _now()
                sub.revoked_reason = "expired_%s" % exc.status_code
                revoked += 1
            errors.append("%s" % (exc.status_code or "error"))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            sub.last_failure_at = _now()
            sub.failure_count = (sub.failure_count or 0) + 1
            errors.append(type(exc).__name__)
    if sent and not failed:
        ev.status, ev.sent_at, ev.last_error = EVENT_SENT, _now(), None
    elif sent:
        ev.status, ev.sent_at = EVENT_PARTIAL, _now()
        ev.last_error = ("failed: " + ",".join(errors))[:300]
    else:
        transient = failed > revoked
        ev.last_error = ("failed: " + ",".join(errors))[:300]
        ev.status = EVENT_PENDING if (transient and ev.attempts < MAX_ATTEMPTS) else EVENT_FAILED
    db.commit()
    return {"status": ev.status, "sent": sent, "failed": failed, "revoked": revoked}


def deliver_pending(db: Session, *, limit: int = 50, sender: Optional[Sender] = None) -> dict:
    """One outbox pass. Without configuration (and no injected sender) this
    does nothing at all — pending rows stay pending so enabling keys later
    does not flood people with stale alerts older than the TTL."""
    if sender is None and not is_configured():
        return {"skipped": "not_configured", "processed": 0}
    rows = (db.query(PushEvent).filter(PushEvent.status == EVENT_PENDING)
            .order_by(PushEvent.created_at.asc()).limit(limit).all())
    cutoff = _now().timestamp() - TTL_SECONDS
    out = {"processed": 0, "sent": 0, "expired": 0}
    for ev in rows:
        created = ev.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created is not None and created.timestamp() < cutoff:
            ev.status, ev.last_error = EVENT_FAILED, "expired before delivery"
            out["expired"] += 1
            continue
        r = deliver_event(db, ev, sender=sender)
        out["processed"] += 1
        out["sent"] += r.get("sent", 0)
    db.commit()
    return out


def send_test(db: Session, user, *, organization_id: Optional[str],
              sender: Optional[Sender] = None) -> dict:
    """A test alert to the CALLER's own live subscriptions only."""
    subs = live_subscriptions(db, user.id, any_workspace=True)
    ev = enqueue(db, target_user_id=user.id, organization_id=organization_id,
                 event_type=TYPE_TEST, idempotency_key="test:%s:%s" % (user.id, uuid.uuid4()),
                 url="/m/more")
    db.flush()
    result = deliver_event(db, ev, sender=sender, subscriptions=subs)
    result["event_id"] = ev.id
    return result


def status_for(db: Session, user) -> dict:
    subs = (db.query(PushSubscription).filter(PushSubscription.user_id == user.id)
            .order_by(PushSubscription.created_at.desc()).all())
    recent = (db.query(PushEvent).filter(PushEvent.target_user_id == user.id)
              .order_by(PushEvent.created_at.desc()).limit(10).all())
    cfg = config_status()
    return {"configured": cfg["configured"], "reason": cfg["reason"],
            "subscriptions": [s.to_public_dict() for s in subs],
            "active_subscriptions": sum(1 for s in subs if s.revoked_at is None),
            "recent_events": [e.to_public_dict() for e in recent]}
