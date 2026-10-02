"""ONE TEXT, ONE SEND - a double-tap must not text a family twice.

The manual email send already holds a database lease while it sends
(email_router._claim_send). The two manual SMS doors - POST /sms/send (lead
page) and POST /communications/send (inbox, phone) - had nothing: a
double-click, a double-tap on a phone, or a retry after a slow response sent
the same text twice, from any instance.

The lease key is the lead plus a hash of the exact message text. It is NOT
released when the send succeeds: for DUPLICATE_WINDOW_S the identical text to
the same lead is refused as a duplicate. It IS released when the send fails,
so the person can fix the problem and try again at once. A different message
to the same lead is never affected.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from sqlalchemy.orm import Session

SCOPE = "sms.manual_send"
DUPLICATE_WINDOW_S = 30
DUPLICATE_DETAIL = ("This exact message was just sent to this person (or is still sending). "
                    "Wait a moment before sending it again.")


def _key(lead_id: str, body: str) -> str:
    digest = hashlib.sha256((body or "").strip().lower().encode("utf-8")).hexdigest()[:24]
    return "%s|%s" % (lead_id, digest)


def claim(db: Session, lead_id: str, body: str) -> Optional[str]:
    from app.services import action_lease
    return action_lease.acquire(db, SCOPE, _key(lead_id, body), ttl_seconds=DUPLICATE_WINDOW_S)


def release_after_failure(db: Session, lead_id: str, body: str, token: Optional[str]) -> None:
    from app.services import action_lease
    action_lease.release(db, SCOPE, _key(lead_id, body), token)
