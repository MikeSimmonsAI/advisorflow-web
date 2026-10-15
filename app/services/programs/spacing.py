"""SCI TEXT SPACING - one rule, every automated sending path.

Oct 13-14: 23 families got two texts less than 20 hours apart. Every one was
the cadence's touch 1 followed by touch 2: touch 1 was held overnight by quiet
hours, and touch 2 - scheduled from the day the cadence STARTED - was already
due, so it fired right behind it. The cadence now waits the schedule's gap
after the last text it sent (#135). This module closes the rest:

  * The gap is measured from the last text that actually went to the family,
    from ANY path - not only from the cadence's own touch log.
  * send_sms refuses an SCI text from any automated path (cadence, campaign,
    bulk, AI, unrecorded) inside the gap, as a backstop no path can skip.

Not spacing-limited, on purpose: a person texting one family by hand (MANUAL),
a human-approved queue item (AUTO_SEND), an appointment confirmation
(APPOINTMENT_FOLLOWUP), the booking link after a call (VOICE_BOOKING_LINK), and
any reply after the FAMILY has written back since our last text. Those are
conversations, not repeated outreach.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

MIN_GAP = timedelta(hours=20)
TOO_SOON = "TOO_SOON"

try:
    from app.services import send_source as _ss
    CONVERSATION_SOURCES = frozenset({_ss.MANUAL, _ss.AUTO_SEND, _ss.APPOINTMENT_FOLLOWUP,
                                      _ss.VOICE_BOOKING_LINK})
except Exception:                                        # noqa: BLE001
    CONVERSATION_SOURCES = frozenset({"manual", "auto_send", "appointment_followup", "voice_booking_link"})


def _naive(d):
    return d.replace(tzinfo=None) if d is not None and getattr(d, "tzinfo", None) is not None else d


def last_text_at(db: Session, lead_id: str) -> Optional[datetime]:
    """When the last text actually went to this family, by any path."""
    from app.models.models import Message
    return _naive(db.query(func.max(Message.sent_at)).filter(Message.lead_id == lead_id).scalar())


def family_replied_since(db: Session, lead_id: str, since: datetime) -> bool:
    """A PERSON wrote back after `since`. An automatic reply (driving / away)
    or an opt-out does not open a conversation."""
    from app.models.program_models import ProgramResponse
    return db.query(ProgramResponse.id).filter(
        ProgramResponse.lead_id == lead_id, ProgramResponse.received_at > since,
        ProgramResponse.response_class.notin_(("auto_reply", "opt_out"))).first() is not None


def earliest_next(db: Session, lead_id: str, gap: timedelta = MIN_GAP) -> Optional[datetime]:
    """The earliest an automated text may go to this family, or None (no limit)."""
    last = last_text_at(db, lead_id)
    return (last + max(gap, MIN_GAP)) if last is not None else None


def refusal(db: Session, lead, send_source: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """None when this text may go now; otherwise why not."""
    if send_source in CONVERSATION_SOURCES:
        return None
    now = _naive(now) or datetime.utcnow()
    last = last_text_at(db, lead.id)
    if last is None or now - last >= MIN_GAP:
        return None
    if family_replied_since(db, lead.id, last):
        return None
    return "%s: last text %.1f hours ago; automated texts wait %d hours" % (
        TOO_SOON, (now - last).total_seconds() / 3600, MIN_GAP.total_seconds() // 3600)
