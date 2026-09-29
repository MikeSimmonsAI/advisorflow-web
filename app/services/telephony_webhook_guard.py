"""Signature validation for Twilio VOICE webhooks - per account, fail closed.

The SMS webhooks resolve the signing account from AccountSid
(app/utils/twilio_webhook_guard.py). Voice webhooks used to validate only
against the platform TWILIO_AUTH_TOKEN, which cannot verify a call placed from
an organization's own Twilio account. This does both, in order:

  1. AccountSid names an account we hold credentials for -> verify the
     X-Twilio-Signature with THAT account's token (twilio_webhook_guard's
     resolver and verifier, unchanged).
  2. Otherwise -> the existing platform-token validator
     (twilio_security.validate_twilio_webhook), which itself fails closed.

A request that neither verifies is a 403 with no side effects. Callers run
this BEFORE touching the database.

The returned account also carries the organization it belongs to; handlers use
`assert_org_matches` so a validly signed request from Org A cannot act on Org
B's call, voicemail or number.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)


class VerifiedVoiceRequest:
    def __init__(self, params: dict, account_sid: Optional[str],
                 organization_id: Optional[str], platform_token: bool):
        self.params = params
        self.account_sid = account_sid
        self.organization_id = organization_id
        self.platform_token = platform_token

    def get(self, key: str, default: str = "") -> str:
        v = self.params.get(key)
        return default if v is None else str(v)


async def verify_voice_webhook(request: Request, db) -> VerifiedVoiceRequest:
    from app.utils.twilio_webhook_guard import resolve_account_by_sid
    from app.utils.twilio_security import validate_twilio_webhook, verify_signature_or_403
    try:
        form = await request.form()
        params = {k: str(v) for k, v in form.items()}
    except Exception:                                        # noqa: BLE001
        params = {}
    account_sid = (params.get("AccountSid") or "").strip()
    resolved = resolve_account_by_sid(db, account_sid) if account_sid else None
    if resolved is not None:
        verify_signature_or_403(request, resolved.auth_token, params)
        if resolved.source == "platform":
            return VerifiedVoiceRequest(params, account_sid, None, platform_token=True)
        org_id = resolved.organization_id
        return VerifiedVoiceRequest(params, account_sid, org_id, platform_token=False)
    await validate_twilio_webhook(request)
    return VerifiedVoiceRequest(params, account_sid or None, None, platform_token=True)


def assert_org_matches(verified: VerifiedVoiceRequest, organization_id: Optional[str]) -> None:
    """A tenant account may only act on its own organization's rows. The
    platform token (brand/platform pool numbers) is trusted platform-wide."""
    if verified.platform_token:
        return
    if organization_id is None:
        return
    # FAIL CLOSED: a tenant-signed request whose account resolves to no
    # organization (e.g. an advisor with no home org) proves nothing about
    # which tenant it may act on.
    if not verified.organization_id or verified.organization_id != organization_id:
        log.warning("voice webhook: account %s (org %s) tried to act on org %s",
                    verified.account_sid, verified.organization_id, organization_id)
        raise HTTPException(status_code=403, detail="Forbidden")
