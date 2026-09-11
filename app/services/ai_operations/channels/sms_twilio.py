"""SMS — THE PLATFORM'S OWN SENDING PATH, BEHIND AN ADAPTER.

THIS ADAPTER DOES NOT TALK TO TWILIO. It calls `sms_service.send_sms`, which
is the one function in this codebase that does, and which already resolves
the organization's credentials and number, applies the A2P content policy,
re-checks DNC and the suppression list, attaches the delivery status callback
and writes the `messages` row the rest of the product reads.

WHY REUSE RATHER THAN REIMPLEMENT. Every send path that grew its own copy of
those rules is a path that drifts, and the drift is always in the same
direction — the newest path is the one missing the newest rule. An AI
employee that sent through its own Twilio client would be exactly that path,
and the first thing it would miss is whatever gate is added next month.

WHAT THIS ADAPTER ADDS. A provider-neutral result, an explicit sending
identity, and the refusal to invent one: `send_sms` needs a User whose
credentials and number resolve, and when no such person can be established
this adapter REFUSES rather than picking somebody. Sending a family a text
that appears to come from an advisor who has never heard of them is a real
harm, and "any advisor in the org" is how that happens.

THE AI EMPLOYEE IS NOT A USER. The sending identity is the human whose number
the text comes from — the lead's assigned advisor, or the person configured
to receive this employee's handoffs. That is a platform fact about whose
phone number is on the message, not an authority claim: authority was settled
before this adapter was reached.
"""

import logging
import time
from typing import Optional

from sqlalchemy.orm import Session

from app.services.ai_operations import budget
from app.services.ai_operations import constants as C
from app.services.ai_operations.channels.base import (ChannelAdapter,
                                                      SendRequest, SendResult)

_log = logging.getLogger(__name__)


def resolve_sending_user(db: Session, *, lead, organization_id: str,
                         preferred_user_id: Optional[str] = None):
    """Whose number does this text come from? None means refuse.

    In order: the person explicitly configured for this employee, then the
    lead's own assigned advisor. NOT "any user in the organization" — see the
    module header.
    """
    from app.models.models import User
    for candidate_id in (preferred_user_id,
                         getattr(lead, "assigned_to_id", None)):
        if not candidate_id:
            continue
        user = (db.query(User)
                .filter(User.id == candidate_id,
                        User.organization_id == organization_id)
                .first())
        if user is not None:
            return user
    return None


class TwilioSMSAdapter(ChannelAdapter):
    """The live SMS adapter. Reachable only in an executing activation stage
    with live sending explicitly enabled — see `channels.resolve`."""

    key = "twilio_sms"
    channel = C.CHANNEL_SMS
    reaches_outside = True

    def send(self, db: Session, req: SendRequest) -> SendResult:
        started = time.time()
        if req.sending_user is None:
            return SendResult(
                outcome=C.P_REJECTED, provider=self.key, simulated=False,
                denial_code=C.D_PROVIDER_UNAVAILABLE,
                error=("No sending identity could be established for this "
                       "organization, so no number can legitimately appear "
                       "as the sender."),
                duration_ms=int((time.time() - started) * 1000))
        if req.lead is None:
            return SendResult(
                outcome=C.P_REJECTED, provider=self.key, simulated=False,
                denial_code=C.D_RECORD_NOT_FOUND,
                error="No contact record was supplied to the SMS adapter.",
                duration_ms=int((time.time() - started) * 1000))

        try:
            from app.services import sms_service
            message = sms_service.send_sms(
                db, req.sending_user, req.lead, req.body,
                include_booking_link=False)
        except ValueError as exc:
            # sms_service raises ValueError for its own compliance refusals.
            # They are reported as a REJECTION rather than a failure: nothing
            # went wrong, something was correctly refused, and a retry would
            # be refused identically.
            return SendResult(
                outcome=C.P_REJECTED, provider=self.key, simulated=False,
                denial_code=C.D_INELIGIBLE, error=str(exc),
                duration_ms=int((time.time() - started) * 1000))
        except Exception as exc:                             # noqa: BLE001
            _log.warning("ai_operations: SMS provider error (%s)", exc)
            return SendResult(
                outcome=C.P_FAILED, provider=self.key, simulated=False,
                error=str(exc)[:300],
                duration_ms=int((time.time() - started) * 1000))

        return SendResult(
            outcome=C.P_ACCEPTED, provider=self.key, simulated=False,
            provider_message_id=getattr(message, "twilio_sid", None),
            provider_status=getattr(message, "twilio_status", None),
            platform_record_type="message",
            platform_record_id=getattr(message, "id", None),
            estimated_cost_usd=budget.estimate_cost(
                C.CHANNEL_SMS, segments=max(1, len(req.body or "") // 153 + 1)),
            duration_ms=int((time.time() - started) * 1000))
