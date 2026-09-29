"""Recording a message AFTER the provider accepted it - without ever turning a
delivered message into an error.

Once Twilio or the email provider has accepted a message it is gone: the
person will receive it. If saving our record of it then fails, the worst
possible response is a 500, because the user reads "it failed" and presses
Send again (2026-09-29: Joshua Shronce's email went out, the page said
"Something went wrong", and a retry would have mailed him twice).

So: try to save; on failure roll back and try once more with the row alone;
if that fails too, log CRITICAL with everything needed to reconstruct it and
report `recorded: False` to the caller, which still answers "sent".
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from sqlalchemy.orm import Session

log = logging.getLogger("send_record")


def record_after_send(db: Session, build_row: Callable[[], object], *, lead=None,
                      channel: str, provider_id: Optional[str] = None,
                      on_saved: Optional[Callable[[object], None]] = None) -> Optional[object]:
    """Save the sent-message row. Returns the row, or None if it could not be saved."""
    from datetime import datetime
    for attempt in (1, 2):
        try:
            row = build_row()
            db.add(row)
            if lead is not None:
                lead.status = "sent"
                lead.last_messaged_at = datetime.utcnow()
            db.commit()
            if on_saved:
                on_saved(row)
            return row
        except Exception:  # noqa: BLE001 - the message is already delivered
            db.rollback()
            if attempt == 1:
                log.exception("saving the %s record failed after the provider accepted it "
                              "(lead=%s provider_id=%s); retrying once",
                              channel, getattr(lead, "id", None), provider_id)
                if lead is not None:
                    try:
                        db.refresh(lead)
                    except Exception:  # noqa: BLE001
                        pass
                continue
            log.critical("SENT BUT NOT RECORDED: %s to lead=%s provider_id=%s - the message "
                         "was delivered to the provider; add the record by hand",
                         channel, getattr(lead, "id", None), provider_id, exc_info=True)
    return None
