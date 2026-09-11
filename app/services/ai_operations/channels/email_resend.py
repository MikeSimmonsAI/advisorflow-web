"""EMAIL — the platform's own sender, behind an adapter.

SAME REASONING AS THE SMS ADAPTER. `email_service.send_email` resolves the
organization's sending identity through `public_identity.sending_identity_for_org`,
which walks organization → platform → verified registry and REFUSES rather
than guessing a from-address. An AI employee that built its own Resend call
would be the path that guesses, and a branded email from the wrong brand is
the kind of mistake a customer notices first and forgives last.

THE PLATFORM RECORD. A successful live send also writes an `email_messages`
row so the lead's timeline shows the message — but only when a sending USER
exists, because that column is NOT NULL and inventing a sender to satisfy a
constraint is how an advisor gets credited with mail they never wrote. With
no sender, the operations record (`ai_communications`) is the only record,
and the result says so rather than silently omitting it.
"""

import logging
import time

from sqlalchemy.orm import Session

from app.services.ai_operations import budget
from app.services.ai_operations import constants as C
from app.services.ai_operations.channels.base import (ChannelAdapter,
                                                      SendRequest, SendResult)

_log = logging.getLogger(__name__)


class ResendEmailAdapter(ChannelAdapter):
    """The live email adapter. Reachable only in an executing activation
    stage with live sending explicitly enabled."""

    key = "resend_email"
    channel = C.CHANNEL_EMAIL
    reaches_outside = True

    def send(self, db: Session, req: SendRequest) -> SendResult:
        started = time.time()
        if not req.to_address:
            return SendResult(
                outcome=C.P_REJECTED, provider=self.key, simulated=False,
                denial_code=C.D_RECORD_NOT_FOUND,
                error="No recipient address was supplied.",
                duration_ms=int((time.time() - started) * 1000))

        to_name = ""
        if req.lead is not None:
            to_name = " ".join(
                [getattr(req.lead, "first_name", "") or "",
                 getattr(req.lead, "last_name", "") or ""]).strip()

        try:
            from app.services import email_service
            result = email_service.send_email(
                db, req.organization_id, req.to_address, to_name,
                req.subject or "", req.body or "")
        except Exception as exc:                             # noqa: BLE001
            _log.warning("ai_operations: email provider error (%s)", exc)
            return SendResult(
                outcome=C.P_FAILED, provider=self.key, simulated=False,
                error=str(exc)[:300],
                duration_ms=int((time.time() - started) * 1000))

        if not (result or {}).get("success"):
            return SendResult(
                outcome=C.P_FAILED, provider=self.key, simulated=False,
                error=str((result or {}).get("error") or "unknown")[:300],
                duration_ms=int((time.time() - started) * 1000))

        platform_id = None
        if req.lead is not None and req.sending_user is not None:
            try:
                from app.models.models import EmailMessage
                row = EmailMessage(
                    lead_id=req.lead.id, sender_id=req.sending_user.id,
                    subject=req.subject or "", body_html=req.body or "",
                    provider_message_id=result.get("provider_message_id"),
                    status="sent")
                db.add(row)
                db.flush()
                platform_id = row.id
            except Exception as exc:                         # noqa: BLE001
                # The mail has already gone. A failure to record it must not
                # be reported as a failure to send it — that reading is how a
                # second copy gets sent.
                _log.warning("ai_operations: email sent but not recorded on "
                             "the lead timeline (%s)", exc)

        return SendResult(
            outcome=C.P_ACCEPTED, provider=self.key, simulated=False,
            provider_message_id=result.get("provider_message_id"),
            provider_status="sent",
            platform_record_type="email_message" if platform_id else None,
            platform_record_id=platform_id,
            estimated_cost_usd=budget.estimate_cost(C.CHANNEL_EMAIL),
            duration_ms=int((time.time() - started) * 1000),
            detail={"timeline_recorded": bool(platform_id)})
