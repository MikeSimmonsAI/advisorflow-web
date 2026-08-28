"""
Retell voice lifecycle webhooks.

A NEW FILE ON PURPOSE. `app/routers/voice_router.py` holds the legacy Twilio
voice stack, which is deliberately fail-closed pending the voice architecture
decision and must not be touched. Nothing here imports it.

THE ORDER IS THE SECURITY MODEL:

    raw body read BEFORE any parsing
      -> verify X-Retell-Signature (HMAC over raw bytes + replay window)
      -> provider_call_id -> our VoiceCall row
      -> organization/lead read FROM THAT ROW
      -> ownership enforced against the payload's claims
      -> business logic

Every failure short-circuits with 403 and zero side effects. This is the same
discipline the SMS webhooks were hardened to on 2026-08-28 after production was
found accepting an unsigned forgery with HTTP 200.

ON `metadata`: Retell echoes back whatever we sent, including
`organization_id`. It is CORRELATION, NEVER AUTHORIZATION. Anything that made a
round trip through a third party can be forged. So the org comes from our
stored row, and if the payload disagrees the request is refused rather than
reconciled.

WHY `await request.body()` IS CORRECT HERE (and was not for Twilio): Retell
posts JSON, so nothing has consumed the stream. The `request.form()` workaround
in `twilio_webhook_guard` was specific to endpoints declaring `Form(...)`,
which these do not. The raw bytes are also mandatory — re-serialised JSON has
different whitespace and key order and would never match the HMAC.
"""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import Lead, VoiceCall
from app.services.comms import PROVIDER_RETELL, resolve_api_key, voice_provider_for_key
from app.services.comms.base import (
    EVENT_ANALYZED, EVENT_ENDED, EVENT_STARTED, EVENT_TRANSCRIPT,
    EVENT_UNKNOWN, TRANSFER_EVENTS,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/voice/retell", tags=["voice-webhooks"])

_FORBIDDEN = "Forbidden"


def _deny(reason: str, **ctx) -> HTTPException:
    """One rejection shape. Detail goes to the log, never to the caller — a
    webhook that explained itself would tell an attacker which of signature,
    call id or ownership they got wrong, and turn this into an oracle for
    which call ids exist."""
    log.warning("retell_webhook: DENIED (%s) %s", reason,
                " ".join("%s=%s" % (k, v) for k, v in ctx.items()))
    return HTTPException(status_code=403, detail=_FORBIDDEN)


@router.post("/webhook")
async def retell_webhook(request: Request, db: Session = Depends(get_db)):
    # 1. RAW BODY FIRST. Nothing may parse before this.
    raw = await request.body()
    signature = request.headers.get("X-Retell-Signature", "")

    # 2. AUTHENTICATE. The API key is the signing key — Retell has no separate
    #    webhook secret. An unconfigured key means we cannot verify, so we
    #    refuse; it never means "allow".
    api_key = resolve_api_key(None)
    provider = voice_provider_for_key(PROVIDER_RETELL, api_key=api_key)
    if not provider.verify_webhook(raw, signature):
        raise _deny("signature", path=request.url.path)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:                                             # noqa: BLE001
        raise _deny("unparseable body after valid signature")

    event = provider.parse_event(payload)
    if event.kind == EVENT_UNKNOWN or not event.provider_call_id:
        # Authenticated but not something we act on. 200 so Retell stops
        # retrying; nothing is written.
        return {"status": "ignored"}

    # 3. RESOLVE OUR OWN RECORD. This — not the payload — is the identity.
    call = (db.query(VoiceCall)
            .filter(VoiceCall.provider_call_id == event.provider_call_id)
            .first())
    if call is None:
        # Unknown call: no row to mutate, and we will not create one from an
        # external assertion that a call exists.
        raise _deny("unknown provider_call_id",
                    provider_call_id=event.provider_call_id)

    # 4. OWNERSHIP. metadata is a claim; the row is the fact.
    claimed_org = (event.metadata or {}).get("organization_id")
    if claimed_org and claimed_org != call.organization_id:
        raise _deny("cross-org metadata", provider_call_id=event.provider_call_id,
                    claimed=claimed_org, actual=call.organization_id)
    claimed_lead = (event.metadata or {}).get("lead_id")
    if claimed_lead and claimed_lead != call.lead_id:
        raise _deny("cross-lead metadata", provider_call_id=event.provider_call_id)

    # 5. BUSINESS LOGIC. Only now.
    _apply(db, call, event)
    return {"status": "ok", "call_id": call.id, "event": event.kind}


def _apply(db: Session, call: VoiceCall, event) -> None:
    """Fold one event into the call row.

    Every branch is idempotent by construction — assignments, not increments —
    because Retell retries and a duplicate delivery must not double-count
    anything or re-fire a side effect.
    """
    kind = event.kind

    if kind in TRANSFER_EVENTS:
        call.transfer_requested = True
        call.transfer_status = TRANSFER_EVENTS[kind]
        if event.transfer_destination:
            call.transfer_destination = event.transfer_destination
        if kind == "transfer_bridged":
            call.outcome = "escalated"
        db.commit()
        return

    if kind == EVENT_STARTED:
        call.status = "in_progress"
        call.started_at = event.started_at or call.started_at or datetime.utcnow()
        # Retell reports a call as started once it is connected, so this is
        # also the earliest honest "answered" signal we get.
        call.answered_at = call.answered_at or call.started_at
        if event.provider_status:
            call.twilio_status = None  # not Twilio's; keep that column clean
        db.commit()
        return

    if kind == EVENT_TRANSCRIPT:
        # Partial transcript. Only ever grows.
        if event.transcript:
            call.transcript = event.transcript
            db.commit()
        return

    if kind in (EVENT_ENDED, EVENT_ANALYZED):
        call.status = "completed"
        call.ended_at = event.ended_at or call.ended_at or datetime.utcnow()
        if event.started_at and not call.started_at:
            call.started_at = event.started_at
        if event.duration_seconds is not None:
            call.duration_seconds = event.duration_seconds
        if event.disconnect_reason:
            call.disconnect_reason = event.disconnect_reason
        if event.transcript:
            call.transcript = event.transcript
        if event.recording_url:
            call.recording_url = event.recording_url
        if event.summary:
            call.summary = event.summary
        if event.analysis is not None:
            try:
                call.analysis_json = json.dumps(event.analysis)[:20000]
            except Exception:                                     # noqa: BLE001
                pass

        _map_outcome(db, call, event)
        _correlate_booking(db, call)
        db.commit()
        return


def _map_outcome(db: Session, call: VoiceCall, event) -> None:
    """Post-call analysis -> EvoSys state.

    Only the real File Check outcomes, in priority order. A giant speculative
    disposition taxonomy would be guesses; these are the ones the conversation
    actually produces.

    Note the tri-state discipline: `is True` throughout. A field the agent does
    not define parses as None, and None must never be read as a decision —
    especially for opt-out, where a false positive silently suppresses a real
    customer forever.
    """
    lead = db.query(Lead).filter(Lead.id == call.lead_id).first()

    # Opt-out is checked first and is the only branch that writes outside this
    # call's own row, because it is the only one that must survive the call.
    if event.opted_out is True:
        call.outcome = "opted_out"
        if lead is not None and lead.phone:
            from app.models.models import SuppressionSource
            from app.services.compliance_service import add_suppression_entry
            try:
                add_suppression_entry(
                    db, call.organization_id, lead.phone,
                    reason="Opted out during AI voice call %s" % call.id,
                    source=SuppressionSource.VOICE_OPT_OUT,
                )
            except Exception as exc:                              # noqa: BLE001
                # Never let a suppression write failure lose the webhook, but
                # do make it loud — this one matters.
                log.error("voice opt-out suppression write failed for call %s: %s",
                          call.id, exc)
            lead.status = "dnc"
            try:
                from app.models.models import CadenceStatus
                from app.services.cadence_service import stop_cadence_for_lead
                stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_DNC)
            except Exception:                                     # noqa: BLE001
                pass
        return

    if event.wrong_number is True:
        call.outcome = "wrong_number"
        return

    if event.appointment_booked is True:
        call.outcome = "booked"
        if lead is not None:
            lead.status = "booked"
        return

    if event.callback_requested is True:
        call.outcome = "callback_requested"
        if event.callback_at:
            call.callback_at = event.callback_at
        return

    if event.voicemail is True:
        call.outcome = "no_answer"
        call.voicemail_left = True
        return

    if event.interested is False:
        call.outcome = "not_interested"
        return

    if event.reached_person is False:
        call.outcome = "no_answer"
        return

    if not call.outcome:
        call.outcome = "completed"


def _correlate_booking(db: Session, call: VoiceCall) -> None:
    """Tie this call to any appointment booked during it.

    This needs no agent change and no new API. The tenant Retell bridge already
    uses `external_ref` as its idempotency key, and the shipped agent
    configuration already tells it to send the Retell call id there. So the
    booking the agent made mid-conversation is already labelled with the same
    id this webhook arrives under — we only have to look it up.
    """
    if not call.provider_call_id or call.booking_link_id:
        return
    try:
        from app.models.integration_models import IntegrationRequestLog
        row = (db.query(IntegrationRequestLog)
               .filter(IntegrationRequestLog.external_ref == call.provider_call_id,
                       IntegrationRequestLog.organization_id == call.organization_id,
                       IntegrationRequestLog.success.is_(True),
                       IntegrationRequestLog.booking_link_id.isnot(None))
               .order_by(IntegrationRequestLog.occurred_at.desc())
               .first())
        if row is not None:
            call.booking_link_id = row.booking_link_id
            if call.outcome in (None, "completed"):
                call.outcome = "booked"
    except Exception as exc:                                      # noqa: BLE001
        log.warning("booking correlation failed for call %s: %s", call.id, exc)
