"""
Twilio Webhook Guard — per-account signature validation, fail closed.
=====================================================================

WHY THIS EXISTS
---------------
Production accepted an UNSIGNED, forged POST to /sms/webhook/status-callback
with HTTP 200 on 2026-08-28. The cause was two layers deep:

  1. `TWILIO_AUTH_TOKEN` was declared `sync: false` in render.yaml, so Render
     prompted for it and it was never filled in. `validate_twilio_webhook`
     then logged a warning and RETURNED WITHOUT VALIDATING — fail open.

  2. Setting that variable would not have fixed it either. This platform does
     not have one Twilio account. Credentials are per-advisor and per-org,
     encrypted at rest:

         User.twilio_account_sid / User.twilio_auth_token_encrypted
         Organization.org_twilio_account_sid / org_twilio_auth_token_encrypted

     Twilio signs each callback with the auth token of the account that SENT
     the message. A single global token could only ever validate one account's
     traffic and would 403 every other org — trading "accepts everything" for
     "rejects most things".

So the token cannot be chosen at deploy time. It has to be resolved per
request, from the account that actually sent the message.

THE ORDER MATTERS
-----------------
Resolve (read-only) -> validate signature -> only then run business logic.
Nothing in this module writes to the database. The callers must not mutate
anything before `guard_*` returns.

FAIL CLOSED, ALWAYS
-------------------
403 with zero side effects when ANY of these hold:
  * no AccountSid on the request
  * AccountSid matches no advisor or organization we hold credentials for
  * the stored auth token is missing or will not decrypt
  * the X-Twilio-Signature header is absent
  * the signature does not verify against that account's token
  * the referenced Message / destination number belongs to a DIFFERENT
    Twilio account than the one that signed the request (cross-org attempt)

There is deliberately no global-token fallback and no "skip if unconfigured"
path. If this module cannot prove who sent the request, the request dies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.models.models import Lead, Message, Organization, User
from app.utils.crypto import decrypt_value
from app.utils.twilio_security import verify_signature_or_403

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedTwilioAccount:
    """The Twilio account a request claims to come from, with its real token."""
    account_sid: str
    auth_token: str
    source: str                      # "advisor:<id>" or "org:<id>"
    advisor_id: str | None = None
    organization_id: str | None = None


def _deny(reason: str, **ctx) -> HTTPException:
    """One shape for every rejection.

    The log line carries the detail; the RESPONSE never does. A webhook
    rejection that explained itself would tell an attacker which of
    AccountSid / MessageSid / signature they got wrong, and turn this endpoint
    into an oracle for which SIDs exist.
    """
    logger.warning("twilio_guard: DENIED (%s) %s", reason,
                   " ".join(f"{k}={v}" for k, v in ctx.items()))
    return HTTPException(status_code=403, detail="Forbidden")


def _token_from_advisor(advisor: User) -> str | None:
    if not (advisor.twilio_account_sid and advisor.twilio_auth_token_encrypted):
        return None
    try:
        return decrypt_value(advisor.twilio_auth_token_encrypted) or None
    except Exception as exc:                                  # pragma: no cover
        logger.warning("twilio_guard: advisor %s token will not decrypt: %s",
                       advisor.id, exc)
        return None


def _token_from_org(org: Organization) -> str | None:
    if not (org.org_twilio_account_sid and org.org_twilio_auth_token_encrypted):
        return None
    try:
        return decrypt_value(org.org_twilio_auth_token_encrypted) or None
    except Exception as exc:                                  # pragma: no cover
        logger.warning("twilio_guard: org %s token will not decrypt: %s",
                       org.id, exc)
        return None


def resolve_account_by_sid(db: Session, account_sid: str
                           ) -> ResolvedTwilioAccount | None:
    """AccountSid -> the credential we hold for it. READ ONLY.

    AccountSid is the strongest stable identifier Twilio gives us: it is on
    every webhook, it names the account that signed the request, and unlike
    MessageSid it does not depend on us already having a row for the message.

    Advisor-level credentials are checked before org-level, mirroring
    sms_service._resolve_twilio_creds exactly — the account that SENT is the
    account that SIGNS, so resolution here must follow the same order the send
    path used or the tokens will not correspond.

    This ordering also covers the org-credential model, where an advisor holds
    only an assigned NUMBER and the organization holds the account. Such an
    advisor has no twilio_account_sid, so the first query cannot match them and
    resolution falls through to the organization — which is the account that
    actually signed. Nothing here needs to know which number was used; the
    AccountSid alone decides, and there is one account per organization.
    """
    if not account_sid:
        return None

    advisor = db.query(User).filter(
        User.twilio_account_sid == account_sid
    ).first()
    if advisor:
        token = _token_from_advisor(advisor)
        if token:
            return ResolvedTwilioAccount(
                account_sid=account_sid, auth_token=token,
                source=f"advisor:{advisor.id}", advisor_id=advisor.id,
                organization_id=advisor.organization_id)

    org = db.query(Organization).filter(
        Organization.org_twilio_account_sid == account_sid
    ).first()
    if org:
        token = _token_from_org(org)
        if token:
            return ResolvedTwilioAccount(
                account_sid=account_sid, auth_token=token,
                source=f"org:{org.id}", organization_id=org.id)

    return None


def account_sids_for_advisor(db: Session, advisor: User) -> set[str]:
    """Every Twilio account SID that could legitimately send for this advisor.

    Their own, plus their organization's shared account — the same two places
    the send path draws from.
    """
    sids: set[str] = set()
    if advisor.twilio_account_sid:
        sids.add(advisor.twilio_account_sid)
    if advisor.organization_id:
        org = db.query(Organization).filter(
            Organization.id == advisor.organization_id
        ).first()
        if org and org.org_twilio_account_sid:
            sids.add(org.org_twilio_account_sid)
    return sids


async def _form_params(request: Request) -> dict:
    """The POST params Twilio signed.

    `request.form()` and NOT `request.body()`: these endpoints declare their
    fields as `Form(...)`, so FastAPI has already consumed the stream by the
    time we run. Starlette caches `_form` but only caches `_body` when body()
    did the reading, so body() would hand back b"" and the signature would be
    computed over the bare URL with no params — which can never match.
    """
    try:
        form = await request.form()
        return {k: str(v) for k, v in form.items()}
    except Exception:                                         # pragma: no cover
        return {}


async def guard_status_callback(request: Request, db: Session
                                ) -> ResolvedTwilioAccount:
    """Authenticate a delivery-receipt callback. Returns on success, else 403.

    MUST be called before the handler touches delivery_status,
    twilio_status, or delivery_status_at. A forged callback carrying a REAL
    MessageSid has to leave that row byte-for-byte unchanged, and the only way
    to guarantee that is to raise before any assignment happens.
    """
    params = await _form_params(request)
    account_sid = (params.get("AccountSid") or "").strip()
    message_sid = (params.get("MessageSid") or "").strip()

    if not account_sid:
        raise _deny("no AccountSid on request", path=request.url.path)

    resolved = resolve_account_by_sid(db, account_sid)
    if not resolved:
        raise _deny("AccountSid matches no stored credential",
                    account_sid=account_sid)

    # Signature first, ownership second. Checking ownership before the HMAC
    # would let an unauthenticated caller probe which MessageSids exist.
    verify_signature_or_403(request, resolved.auth_token, params)

    # CROSS-ORG GATE. The signature only proves "some account we know signed
    # this". It does NOT prove that account owns the message being updated.
    # Without this, Org A — holding a perfectly valid token — could POST a
    # status callback for Org B's MessageSid and rewrite B's delivery state.
    if message_sid:
        msg = db.query(Message).filter(
            Message.twilio_sid == message_sid
        ).first()
        if msg is not None:
            owner_sids = _account_sids_owning_message(db, msg)
            if owner_sids and account_sid not in owner_sids:
                raise _deny("cross-account status callback",
                            account_sid=account_sid, message_sid=message_sid,
                            owner_sids=",".join(sorted(owner_sids)))

    return resolved


def _account_sids_owning_message(db: Session, msg: Message) -> set[str]:
    """Which Twilio accounts could legitimately have sent this Message."""
    sids: set[str] = set()
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    if sender:
        sids |= account_sids_for_advisor(db, sender)
    # The lead's org is authoritative for org-level sends even when the
    # sender's own org field has since changed.
    lead = db.query(Lead).filter(Lead.id == msg.lead_id).first()
    if lead and lead.organization_id:
        org = db.query(Organization).filter(
            Organization.id == lead.organization_id
        ).first()
        if org and org.org_twilio_account_sid:
            sids.add(org.org_twilio_account_sid)
    return sids


async def guard_inbound(request: Request, db: Session) -> ResolvedTwilioAccount:
    """Authenticate an inbound SMS webhook. Returns on success, else 403.

    P0. A forged inbound message must never be able to create a Reply, stop a
    cadence, flip lead state, add a DNC/suppression entry, or trigger the AI
    pipeline. Every one of those is downstream of this call, so this raises
    before the handler reaches any of them.
    """
    params = await _form_params(request)
    account_sid = (params.get("AccountSid") or "").strip()
    to_number = (params.get("To") or "").strip()

    if not account_sid:
        raise _deny("no AccountSid on request", path=request.url.path)

    resolved = resolve_account_by_sid(db, account_sid)
    if not resolved:
        raise _deny("AccountSid matches no stored credential",
                    account_sid=account_sid)

    verify_signature_or_403(request, resolved.auth_token, params)

    # CROSS-ORG GATE. The destination number identifies whose inbox this is.
    # A validly-signed request from Org A must not be able to inject a reply
    # into Org B by naming B's Twilio number as `To`.
    if to_number:
        from app.services.dedup_service import normalize_phone
        normalized = normalize_phone(to_number)
        owner = db.query(User).filter(
            User.twilio_phone_number == normalized
        ).first()
        if owner is None and normalized != to_number:
            owner = db.query(User).filter(
                User.twilio_phone_number == to_number
            ).first()
        if owner is not None:
            allowed = account_sids_for_advisor(db, owner)
            if allowed and account_sid not in allowed:
                raise _deny("cross-account inbound webhook",
                            account_sid=account_sid, to=normalized,
                            owner_advisor=owner.id)

    return resolved
