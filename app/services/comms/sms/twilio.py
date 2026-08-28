"""
TwilioSmsProvider — a WRAPPER over the existing SMS service.

READ THIS BEFORE CHANGING ANYTHING HERE.

Twilio SMS is live, carrying thousands of production messages, and its
security was hardened on 2026-08-28 (commit `a049261`): per-advisor-then-per-org
credential resolution, and fail-closed webhook signature validation against the
sending account's own token. None of that is reimplemented here and none of it
may be bypassed here.

This class exists for exactly one reason: so the communications control plane
can address SMS and voice through the same shape. It adds no behaviour. Every
call delegates to `app.services.sms_service`, which remains the authority on:

  * which Twilio account sends (advisor credentials, then org credentials)
  * the from-number and caller id
  * attaching `status_callback` so delivery receipts come back
  * writing the `Message` row

Eligibility and suppression are NOT checked here. They are decided upstream in
EvoSys for every channel at once — putting a second check here would create a
second place for the rules to drift, which is the precise failure this
architecture exists to prevent.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.services.comms.base import SmsProvider, SmsRequest, SmsResult

log = logging.getLogger(__name__)


class TwilioSmsProvider(SmsProvider):
    key = "twilio"

    def __init__(self, db: Session, advisor):
        self.db = db
        self.advisor = advisor

    def is_ready(self) -> Tuple[bool, Optional[str]]:
        """Can this advisor actually send?

        Mirrors `sms_service._resolve_twilio_creds`'s order rather than
        second-guessing it: advisor credentials first, then the org's shared
        number.
        """
        adv = self.advisor
        if getattr(adv, "twilio_account_sid", None) and getattr(
                adv, "twilio_auth_token_encrypted", None):
            return True, None
        org_id = getattr(adv, "organization_id", None)
        if org_id:
            from app.models.models import Organization
            org = self.db.query(Organization).filter(
                Organization.id == org_id).first()
            if org and org.org_twilio_account_sid and \
                    org.org_twilio_auth_token_encrypted and \
                    org.org_twilio_phone_number:
                return True, None
        return False, "No Twilio credentials for this advisor or their organization."

    def send(self, req: SmsRequest) -> SmsResult:
        """Delegate to the existing send path. Never raises."""
        try:
            from app.models.models import Lead
            from app.services.sms_service import send_sms

            if not req.lead_id:
                return SmsResult.failure(
                    "no_lead",
                    "This provider sends to a Lead; the existing service owns "
                    "phone resolution and the Message row.")
            lead = self.db.query(Lead).filter(Lead.id == req.lead_id).first()
            if lead is None:
                return SmsResult.failure("no_lead", "Lead not found.")

            msg = send_sms(self.db, self.advisor, lead, req.body)
            return SmsResult(ok=True,
                             provider_message_id=getattr(msg, "twilio_sid", None))
        except Exception as exc:                                  # noqa: BLE001
            # Contract: a provider never raises into its caller.
            log.warning("TwilioSmsProvider.send failed: %s", exc)
            return SmsResult.failure("send_failed", str(exc)[:300])
