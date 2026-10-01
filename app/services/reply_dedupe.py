"""Is this inbound email already a Reply on the lead?

Both email readers (an advisor's own Microsoft 365 inbox, and the shared
sending mailbox) re-read every message several times: the windows overlap on
purpose, a tag can fail to stick, and the same message can arrive through both
readers. They used to answer "already stored?" with (lead_id, body) alone.

That is wrong for the replies that matter most. A family that answers "Yes" to
Monday's email and "Yes" to Thursday's sent two replies; the second one matched
the first on body and was dropped - permanently, with nothing in any log.

The same message, however many times or through whichever reader it is seen,
carries the same receivedDateTime. So a duplicate is the same lead, same body,
same channel AND received within a few minutes of the stored one.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

SAME_MESSAGE_WINDOW = timedelta(minutes=5)


def find_duplicate_email_reply(db: Session, lead_id: str, body: str,
                               received_at: Optional[datetime]):
    from app.models.models import Reply
    q = db.query(Reply).filter(Reply.lead_id == lead_id, Reply.body == body,
                               Reply.source == "email")
    if received_at is None:
        return q.first()
    if received_at.tzinfo is not None:
        # Stored times are naive UTC; an aware time from a caller must not raise.
        from datetime import timezone as _tz
        received_at = received_at.astimezone(_tz.utc).replace(tzinfo=None)
    lo, hi = received_at - SAME_MESSAGE_WINDOW, received_at + SAME_MESSAGE_WINDOW
    for r in q.all():
        # A stored row with no time cannot be told apart; keep the old answer.
        if r.received_at is None or lo <= r.received_at <= hi:
            return r
    return None
