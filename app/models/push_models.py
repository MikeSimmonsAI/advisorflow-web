"""WEB PUSH (VAPID) FOR THE MOBILE/PWA SHELL.

Two tables, both new, both additive:

  push_subscriptions   one browser push endpoint, owned by ONE user, recorded
                       with the workspace it was enabled in.
  push_events          an outbox: "tell user U that something happened". One
                       row per (event, target user), deduplicated by an
                       idempotency key so a webhook retry or a double call can
                       never buzz a phone twice.

This is separate from `device_push_tokens` (Expo, native app) on purpose: a Web
Push subscription is not a token, it is an endpoint URL plus two keys, and it
is revoked by the push service answering 404/410 rather than by an Expo error
code.

WHAT AN EVENT MAY CARRY
-----------------------
A short GENERIC title ("New reply on a lead"), a same-app URL, and ids. Never a
lead's name, phone number, email or message body. A push renders on a locked
screen, outside every authorisation boundary; the app fetches the real record
through the authorised API after the tap. `push_events` stores exactly what
would be sent, so the outbox itself holds no customer content either.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (Column, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)

from app.models.models import Base


def _uuid():
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc)


# push_events.status
EVENT_PENDING = "pending"
EVENT_SENT = "sent"
EVENT_PARTIAL = "partial"            # some subscriptions failed
EVENT_FAILED = "failed"
EVENT_NO_SUBSCRIPTIONS = "no_subscriptions"
EVENT_NOT_CONFIGURED = "not_configured"
EVENT_STATUSES = (EVENT_PENDING, EVENT_SENT, EVENT_PARTIAL, EVENT_FAILED,
                  EVENT_NO_SUBSCRIPTIONS, EVENT_NOT_CONFIGURED)

# push_events.type
TYPE_REPLY = "reply_received"
TYPE_HOT_REPLY = "hot_reply"
TYPE_OFFER_ASSIGNED = "offer_assigned"
TYPE_TEST = "test"
TYPE_GENERIC = "notification"


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    # The workspace the person was in when they enabled push. Delivery only
    # uses subscriptions recorded for the event's workspace (or none recorded).
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True, index=True)

    # An endpoint is one browser profile. It belongs to ONE user at a time: a
    # shared browser that signs in as somebody else MOVES the row (see
    # web_push_service.subscribe) instead of holding two owners.
    endpoint = Column(Text, nullable=False)
    endpoint_hash = Column(String(64), nullable=False, unique=True, index=True)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    user_agent = Column(String(300), nullable=True)
    platform = Column(String(20), nullable=True, default="web")

    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
    last_success_at = Column(DateTime, nullable=True)
    last_failure_at = Column(DateTime, nullable=True)
    failure_count = Column(Integer, nullable=False, default=0)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String(80), nullable=True)

    __table_args__ = (
        Index("ix_push_sub_user_live", "user_id", "revoked_at"),
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def to_public_dict(self) -> dict:
        """What the owner sees. The endpoint and keys are delivery credentials
        and are never returned."""
        return {
            "id": self.id,
            "organization_id": self.organization_id,
            "user_agent": self.user_agent,
            "platform": self.platform,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "last_success_at": self.last_success_at,
            "failure_count": self.failure_count or 0,
            "revoked_at": self.revoked_at,
            "revoked_reason": self.revoked_reason,
        }


class PushEvent(Base):
    __tablename__ = "push_events"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True, index=True)
    target_user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(String(40), nullable=False, default=TYPE_GENERIC)

    # PAYLOAD SUMMARY — exactly what is sent, nothing else.
    title = Column(String(80), nullable=False)
    url = Column(String(200), nullable=True)
    notification_id = Column(String, nullable=True)
    record_type = Column(String(40), nullable=True)
    record_id = Column(String, nullable=True)

    status = Column(String(20), nullable=False, default=EVENT_PENDING, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String(300), nullable=True)
    idempotency_key = Column(String(200), nullable=False)

    created_at = Column(DateTime, default=_now)
    last_attempt_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_push_event_idem"),
        Index("ix_push_event_status_created", "status", "created_at"),
    )

    def to_public_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "title": self.title, "url": self.url,
            "status": self.status, "attempts": self.attempts or 0,
            "last_error": self.last_error, "created_at": self.created_at,
            "sent_at": self.sent_at,
        }
