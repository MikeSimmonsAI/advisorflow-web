"""Staff alert email goes out from the Microsoft 365 mailbox first.

WHY (2026-11-20): staff alerts - "texted back", "HUMAN HANDOFF", "NOT HANDLED
YET", "Appointment booked" - were almost half of everything sent through the
email service (Resend), whose free plan stops at 100 emails a day. Restland's
launch pushed the total past that every day. These alerts go to two or three
of our own people, which is ordinary one-to-one mail and exactly what a 365
mailbox is for.

HOW: the alert's From address (the brand's resolved sending identity, e.g.
support@evosyspro.live) is looked up among the connected 365 mailboxes. When
it is connected with permission to send, the alert goes out from it through
Microsoft Graph. Otherwise - or when Microsoft refuses or cannot be reached -
the alert goes through the email service exactly as before.

A STAFF ALERT IS NEVER LOST TO THIS CHANGE. Unlike program email to families
(programs/mailbox_send.py, which fails closed on purpose), an alert falls back
to the email service, because a missed "a family needs a person" costs more
than which pipe it travelled through.

Switch: STAFF_ALERTS_VIA_MAILBOX=0 sends every alert through the email
service again (default on).
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional

log = logging.getLogger(__name__)


def enabled() -> bool:
    return (os.environ.get("STAFF_ALERTS_VIA_MAILBOX") or "1").strip().lower() not in ("0", "false", "no", "off")


def _via_mailbox(db, address: str, to: str, subject: str, html: str,
                 reply_to: Optional[str]) -> Optional[Dict]:
    from app.services import inbound_mailbox_service as IMS
    box = IMS.sending_mailbox(db, address)
    if box is None:
        return None
    return IMS.send_mail(db, box, to_email=to, subject=subject, body_html=html, reply_to=reply_to)


def send(to: str, subject: str, html: str, *, org=None, db=None, **provider_kwargs) -> Dict:
    """Send one staff alert email. Same result shape as
    email_service.send_email_via_provider. `org` is the sending identity the
    caller already resolved; its from_email decides which mailbox is used.
    `db`: the caller's session (the caller commits). Without one, a short
    session of its own is opened and committed so a refreshed Microsoft
    sign-in is kept."""
    address = (getattr(org, "from_email", None) or "").strip().lower()
    reply_to = getattr(org, "reply_to_email", None)
    if enabled() and address and to:
        res = None
        try:
            if db is not None:
                res = _via_mailbox(db, address, to, subject, html, reply_to)
            else:
                from app.deps import SessionLocal
                own = SessionLocal()
                try:
                    res = _via_mailbox(own, address, to, subject, html, reply_to)
                    own.commit()
                finally:
                    own.close()
        except Exception as exc:  # noqa: BLE001 - any trouble here falls back, never loses the alert
            log.warning("staff alert via 365 mailbox %s raised %s; using the email service",
                        address, type(exc).__name__)
            res = None
        if res is not None and res.get("success"):
            return res
        if res is not None:
            log.warning("staff alert via 365 mailbox %s refused (%s); using the email service",
                        address, (res.get("error") or "")[:200])
    from app.services.email_service import send_email_via_provider
    return send_email_via_provider(to, subject, html, org=org, **provider_kwargs)
