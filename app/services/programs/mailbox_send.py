"""Program email through the program's own Microsoft 365 mailbox.

WHY (2026-10-12 inbox tests): the same short, plain email from
support@evosyspro.live landed in Yahoo's SPAM when it went out through the
email service (Resend) and in Yahoo's INBOX when it went out from the
Microsoft 365 mailbox itself. So a program can name a connected mailbox
(OutreachProgram.send_via_mailbox) and its email - campaign touches, manual
emails to its contacts, inbox placement checks - goes out from that mailbox
through Microsoft Graph instead.

FAIL CLOSED: a program that names a mailbox which is not connected (or was
connected without permission to send) gets a failed send with the reason. It
never quietly falls back to the other path, because the whole point of the
setting is which path is used.

Microsoft limits how much a mailbox may send; the program email runner's
daily cap (PROGRAM_EMAIL_TOUCH_DAILY_CAP) is what keeps volume small.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy.orm import Session


def configured_address(prog) -> Optional[str]:
    addr = (getattr(prog, "send_via_mailbox", None) or "").strip().lower()
    return addr or None


def send(db: Session, prog, *, to_email: str, subject: str, body_html: str,
         reply_to: Optional[str] = None, attachments: Optional[List[dict]] = None) -> Optional[Dict]:
    """Send through the program's mailbox. None = this program doesn't use one
    (the caller uses its normal path)."""
    addr = configured_address(prog) if prog is not None else None
    if not addr:
        return None
    from app.services import inbound_mailbox_service as _ims
    box = _ims.sending_mailbox(db, addr)
    if box is None:
        return {"success": False, "provider_message_id": None, "provider": "microsoft365",
                "from_email": addr, "audit_bcc_applied": False,
                "error": ("The Microsoft 365 sending mailbox %s is not connected. Connect it in "
                          "God Mode > Email Diagnostics." % addr)}
    return _ims.send_mail(db, box, to_email=to_email, subject=subject, body_html=body_html,
                          reply_to=reply_to, attachments=attachments)
