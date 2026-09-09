"""PUSH DEVICE REGISTRATION.

A row here says "this user, on this install, can be reached at this push
token". It is the only new state push needs; the notification CONTENT still
comes from the existing `notifications` table, and the delivery path is
`app/services/push_service.py`.

TWO PROPERTIES THIS TABLE EXISTS TO ENFORCE
-------------------------------------------
1. OWNERSHIP. A token belongs to exactly one user at a time. Phones get handed
   over, reassigned and reset; if the same push token were left attached to a
   previous owner, that person's notifications would arrive on somebody else's
   lock screen. So registering a token that is already recorded against a
   different user MOVES it rather than duplicating it, and the move is what the
   uniqueness constraint on `token` guarantees.

2. SCOPE. `active_context` records which experience the install was in when it
   registered. It is used to decide what NOT to send — an owner switched into a
   customer workspace should not receive a platform-wide exception alert
   addressed to a different context — and never to decide what a user may READ.
   Authority for the underlying record is re-checked when the app opens the
   notification, exactly as it is for any other request.

WHAT A PUSH PAYLOAD MAY CARRY
-----------------------------
Ids and a short title. Never lead names, never message bodies, never amounts.
The device fetches the real record through the normal authorised endpoint after
the tap. A push that carries data is a push that leaks it — to a lock screen,
to a notification mirror on a laptop, to whoever is holding the phone.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, String,
                        Text, UniqueConstraint)

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


PLATFORM_IOS = "ios"
PLATFORM_ANDROID = "android"
PLATFORM_WEB = "web"
DEVICE_PLATFORMS = (PLATFORM_IOS, PLATFORM_ANDROID, PLATFORM_WEB)

# Transport. Expo's push service is the only one wired today; the column exists
# so a later APNs/FCM direct path does not need a migration to coexist with it.
PROVIDER_EXPO = "expo"
PUSH_PROVIDERS = (PROVIDER_EXPO,)


class DevicePushToken(Base):
    __tablename__ = "device_push_tokens"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)

    # The session that registered it. Revoking a session should stop pushing to
    # the device that session belonged to, and without this column there is no
    # way to know which device that was.
    session_id = Column(String, nullable=True, index=True)

    token = Column(String, nullable=False, unique=True, index=True)
    provider = Column(String, nullable=False, default=PROVIDER_EXPO)
    platform = Column(String, nullable=False, default=PLATFORM_IOS)

    device_id = Column(String, nullable=True, index=True)
    device_name = Column(String, nullable=True)
    app_version = Column(String, nullable=True)

    # Which experience this install was last in: "sales", "manager", "advisor",
    # "executive", "platform", or a workspace org id for a customer context.
    active_context = Column(String, nullable=True)
    active_scope_id = Column(String, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_seen_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    # The provider told us this token is dead (uninstalled, reset). Kept rather
    # than deleted so a re-register can reuse the row and so a delivery failure
    # is explainable after the fact.
    failure_reason = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("token", name="uq_device_push_token"),
        Index("ix_device_push_user_active", "user_id", "is_active"),
    )

    def to_public_dict(self) -> dict:
        """What the holder sees about their own registered devices.

        The token itself is omitted. It is a delivery credential; anybody who
        holds it can be spoofed into by nobody, but it is still not something a
        read endpoint should hand back.
        """
        return {
            "id": self.id,
            "platform": self.platform,
            "provider": self.provider,
            "device_name": self.device_name,
            "app_version": self.app_version,
            "active_context": self.active_context,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
        }
