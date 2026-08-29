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

        _classify_answer(call, event)
        _map_outcome(db, call, event)
        _correlate_booking(db, call)
        db.commit()
        return


def _classify_answer(call: VoiceCall, event) -> None:
    """Was there a person on the line, and does this dial spend a conversation.

    Written on the same event that closes the call, before `_map_outcome`, so
    the attempt counter and the disposition can never disagree about the same
    row. `outcome` says what came of the call; this says whether anybody
    answered it, and the two are not the same question - "no_answer" was
    already being used for a voicemail that we DID reach.

    The provider's verdict is preferred and the transcript is the fallback. The
    call that made this necessary reached a full mailbox and Retell reported
    nothing, because voicemail detection was off on the agent; the greeting is
    right there in the transcript, so it is read.
    """
    from app.services.voice_attempt_policy import (classify_answer,
                                                   is_live_conversation)
    provider_said = None
    for attr in ("answered_by", "answered_by_machine", "voicemail"):
        v = getattr(event, attr, None)
        if v is True and attr in ("answered_by_machine", "voicemail"):
            provider_said = "voicemail"
            break
        if isinstance(v, str) and v.strip():
            provider_said = v
            break

    call.answered_by = classify_answer(
        disconnect_reason=call.disconnect_reason,
        duration_seconds=call.duration_seconds,
        transcript=call.transcript,
        provider_answered_by=provider_said,
        failed=(call.status == "failed"),
    )
    call.is_live_conversation = is_live_conversation(call.answered_by)


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
        # Its own disposition now. Reaching a machine and nobody picking up
        # were both "no_answer", which made the two indistinguishable in the
        # console and in every report built on it - and they call for different
        # follow-up: a voicemail was heard, a no-answer was not.
        call.outcome = "voicemail"
        call.voicemail_left = True
        return

    if event.interested is False:
        call.outcome = "not_interested"
        return

    if event.reached_person is False:
        call.outcome = "no_answer"
        return

    # The agent's analysis said nothing decisive. Fall back to what the line
    # itself did, which `_classify_answer` has already worked out - otherwise a
    # voicemail Retell did not flag is filed as a completed conversation, which
    # is exactly how a thirteen-second call to a full mailbox came to look like
    # a successful contact.
    answered = (getattr(call, "answered_by", None) or "").strip().lower()
    if answered in ("voicemail", "no_answer", "busy", "failed"):
        call.outcome = answered
        if answered == "voicemail":
            call.voicemail_left = True
        return

    if not call.outcome:
        call.outcome = "completed"


def _correlate_booking(db: Session, call: VoiceCall) -> None:
    """Tie this call to any appointment booked during it.

    The tenant Retell bridge uses `external_ref` as its idempotency key, so a
    booking made mid-conversation can carry the same id this webhook arrives
    under and we only have to look it up.

    CORRECTION, 2026-08-29. An earlier version of this docstring claimed the
    shipped agent configuration "already tells it to send the Retell call id
    there". It did not. The deployed `book_appointment` schema asked for a
    phone-number-and-date string, so no booking ever correlated and this
    function silently found nothing — the failure mode of a join key that is
    merely *assumed* to match. The agent now sets `external_ref` from Retell's
    built-in `{{call_id}}`, which is the same value `provider_call_id` is
    populated from in `RetellVoiceProvider.start_call` and in every webhook
    payload (`call.call_id`).

    THE ORGANIZATION FILTER IS NOT DECORATION. `external_ref` is unique per
    credential, not globally, so two tenants can legitimately hold rows with
    the same value. Matching on the id alone would let one funeral home's call
    adopt another's booking.
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
