"""
RetellVoiceProvider — outbound calls + webhook authentication.

Implements Retell's documented contracts. No SDK: outbound is a single POST and
`httpx` is already a production dependency, so adding a vendor package would
buy nothing and cost a dependency. The signature check is the fully specified
scheme below, verified against Retell's docs — the same approach
`app/utils/twilio_security.py` already takes for Twilio.

OUTBOUND
    POST https://api.retellai.com/v2/create-phone-call
    Authorization: Bearer <api key>
    { from_number, to_number, override_agent_id, metadata,
      retell_llm_dynamic_variables }
    -> 201 { call_id, agent_id, call_status, ... }

WEBHOOK AUTHENTICATION
    Header:  X-Retell-Signature: v=<unix_ms>,d=<hex digest>
    Digest:  HMAC-SHA256( raw_body + timestamp , api_key )
    Window:  timestamp must be within 5 minutes

    Two things about that are easy to get wrong and are load-bearing:

    1. There is NO separate webhook secret. The API key IS the signing key.
       Do not invent a `RETELL_WEBHOOK_SECRET` — there is nothing to put in it.

    2. The digest covers the RAW body bytes. Re-serialising the parsed JSON
       changes whitespace and key order and the HMAC will never match. The
       router therefore reads `await request.body()` BEFORE parsing, and hands
       those exact bytes here.

    The replay window is not decoration: without it a captured valid webhook
    could be re-sent forever. Rejecting on a stale timestamp costs nothing —
    Retell retries.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app.services.comms.base import (
    EVENT_ANALYZED, EVENT_ENDED, EVENT_STARTED, EVENT_TRANSCRIPT,
    EVENT_UNKNOWN, TRANSFER_EVENTS, VoiceCallRequest, VoiceCallResult,
    VoiceEvent, VoiceProvider,
)

log = logging.getLogger(__name__)

RETELL_API_BASE = "https://api.retellai.com"
CREATE_PHONE_CALL_PATH = "/v2/create-phone-call"

# Retell's documented tolerance.
SIGNATURE_MAX_AGE_SECONDS = 5 * 60
_SIG_RE = re.compile(r"^v=(\d+),d=([0-9a-fA-F]+)$")

# Retell event name -> our neutral vocabulary. Anything absent is UNKNOWN and
# the router ignores it rather than guessing.
_EVENT_MAP = {
    "call_started": EVENT_STARTED,
    "call_ended": EVENT_ENDED,
    "call_analyzed": EVENT_ANALYZED,
    "transcript_updated": EVENT_TRANSCRIPT,
    "transfer_started": "transfer_started",
    "transfer_bridged": "transfer_bridged",
    "transfer_cancelled": "transfer_cancelled",
    "transfer_ended": "transfer_ended",
}


def _ms_to_dt(value) -> Optional[datetime]:
    """Retell timestamps are unix milliseconds. Stored naive-UTC to match the
    rest of `voice_calls`."""
    try:
        if value in (None, ""):
            return None
        return datetime.utcfromtimestamp(int(value) / 1000.0)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _truthy(value) -> Optional[bool]:
    """Tri-state. None means the analysis did not say — which is genuinely
    different from it saying False, and the difference decides whether we
    write a suppression row."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "y", "1"):
            return True
        if low in ("false", "no", "n", "0"):
            return False
    return None


def _coerce_version(raw: Any) -> Optional[int]:
    """A pinned agent version, or None if it cannot be trusted.

    STRICT ON PURPOSE. Everything ambiguous returns None so the caller refuses
    the call: None itself, an empty string, a bool (True is an int in Python and
    would otherwise pin version 1), a float, a negative number, anything
    unparseable. A version we are unsure about is worse than no call, because a
    wrong version is a different conversation with a real person.

    A clean numeric string IS accepted — configuration arrives from a form and a
    JSON body as well as from the column, and "3" is not ambiguous.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, str):
        txt = raw.strip()
        if not txt or not txt.isdigit():
            return None
        try:
            return int(txt)
        except ValueError:
            return None
    return None


class RetellVoiceProvider(VoiceProvider):
    key = "retell"

    def __init__(self, api_key: Optional[str] = None,
                 base_url: str = RETELL_API_BASE, timeout: float = 20.0):
        self.api_key = (api_key or "").strip() or None
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ── readiness ────────────────────────────────────────────────────────────

    def is_ready(self) -> Tuple[bool, Optional[str]]:
        if not self.api_key:
            return False, ("RETELL_API_KEY is not configured on this service "
                           "(and no per-organization key is set).")
        return True, None

    # ── outbound ─────────────────────────────────────────────────────────────

    def start_call(self, req: VoiceCallRequest) -> VoiceCallResult:
        ready, why = self.is_ready()
        if not ready:
            return VoiceCallResult.failure("not_configured", why or "")

        # NAME THE VERSION, NOT JUST THE AGENT.
        #
        # `override_agent_id` alone tells Retell "use this agent" and leaves the
        # version to the vendor, which resolves to whatever is newest —
        # INCLUDING AN UNPUBLISHED DRAFT. The first live File Check call proved
        # that: the number's outbound binding was V1, and the call ran V3, a
        # draft that had never been reviewed or published.
        #
        # So a pinned version is required, and a missing or unusable one is
        # REFUSED here rather than dropped from the body. Omitting it would fall
        # straight back into the vendor-chooses behaviour this exists to end,
        # and it would do so silently, on a real call to a real family.
        #
        # The value comes from `VoiceAgentConfig.agent_version`. No version
        # number is written in this module — the agent id and the outbound
        # number are already configuration, and the version belongs with them.
        version = _coerce_version(req.agent_version)
        if version is None:
            return VoiceCallResult.failure(
                "no_agent_version",
                "No usable agent version is pinned for this configuration "
                "(got %r). Set VoiceAgentConfig.agent_version to the published "
                "version this organization is approved to run; refusing rather "
                "than letting the provider pick." % (req.agent_version,))

        body: Dict[str, Any] = {
            "from_number": req.from_number,
            "to_number": req.to_number,
            "override_agent_version": version,
        }
        if req.agent_id:
            body["override_agent_id"] = req.agent_id
        if req.metadata:
            body["metadata"] = dict(req.metadata)
        if req.dynamic_variables:
            # Retell requires string values here.
            body["retell_llm_dynamic_variables"] = {
                str(k): ("" if v is None else str(v))
                for k, v in req.dynamic_variables.items()
            }

        try:
            import httpx
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    self.base_url + CREATE_PHONE_CALL_PATH,
                    json=body,
                    headers={"Authorization": "Bearer %s" % self.api_key,
                             "Content-Type": "application/json"},
                )
        except Exception as exc:                                  # noqa: BLE001
            # Never raise into the orchestrator: it has an uncommitted
            # VoiceCall row to mark failed and leave recoverable.
            log.warning("retell start_call transport error: %s", exc)
            return VoiceCallResult.failure("transport", str(exc)[:300])

        if resp.status_code not in (200, 201):
            # The vendor's message is safe to keep (it describes OUR request),
            # but truncate it and never echo headers — the key is in those.
            detail = (resp.text or "")[:300]
            log.warning("retell start_call rejected: HTTP %s %s",
                        resp.status_code, detail)
            return VoiceCallResult.failure("http_%s" % resp.status_code, detail)

        try:
            data = resp.json()
        except Exception:                                         # noqa: BLE001
            return VoiceCallResult.failure("bad_response",
                                           "Retell returned a non-JSON body.")

        call_id = data.get("call_id")
        if not call_id:
            return VoiceCallResult.failure(
                "bad_response", "Retell accepted the call but returned no call_id.")
        return VoiceCallResult(ok=True, provider_call_id=call_id,
                               provider_status=data.get("call_status"))

    # ── webhook authentication ───────────────────────────────────────────────

    def verify_webhook(self, raw_body: bytes, signature: str,
                       now: Optional[datetime] = None) -> bool:
        """HMAC-SHA256(raw_body + timestamp, api_key), with a replay window.

        Returns False on every failure mode — unconfigured key, malformed
        header, stale timestamp, wrong digest — so the caller has exactly one
        rejection path and nothing can 'succeed' by accident.
        """
        if not self.api_key:
            log.warning("retell verify_webhook: no API key configured — refusing")
            return False
        if not signature:
            return False

        m = _SIG_RE.match(signature.strip())
        if not m:
            log.warning("retell verify_webhook: malformed signature header")
            return False
        ts_raw, digest = m.group(1), m.group(2)

        # Replay window.
        try:
            sent_at = datetime.utcfromtimestamp(int(ts_raw) / 1000.0)
        except (ValueError, OSError, OverflowError):
            return False
        current = now or datetime.utcnow()
        if current.tzinfo is not None:
            current = current.astimezone(timezone.utc).replace(tzinfo=None)
        if abs((current - sent_at).total_seconds()) > SIGNATURE_MAX_AGE_SECONDS:
            log.warning("retell verify_webhook: timestamp outside the %ss window",
                        SIGNATURE_MAX_AGE_SECONDS)
            return False

        if not isinstance(raw_body, (bytes, bytearray)):
            raw_body = str(raw_body).encode("utf-8")
        signed = bytes(raw_body) + ts_raw.encode("utf-8")
        expected = hmac.new(self.api_key.encode("utf-8"), signed,
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, digest.lower())

    # ── event normalisation ──────────────────────────────────────────────────

    def parse_event(self, payload: Dict[str, Any]) -> VoiceEvent:
        """Retell payload -> neutral `VoiceEvent`. Never raises."""
        try:
            return self._parse(payload)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("retell parse_event failed: %s", exc)
            return VoiceEvent(kind=EVENT_UNKNOWN, raw={})

    def _parse(self, payload: Dict[str, Any]) -> VoiceEvent:
        raw_event = (payload.get("event") or "").strip()
        kind = _EVENT_MAP.get(raw_event, EVENT_UNKNOWN)
        call = payload.get("call") or {}

        ev = VoiceEvent(
            kind=kind,
            provider_call_id=call.get("call_id"),
            metadata=call.get("metadata") or {},
            provider_status=call.get("call_status"),
            disconnect_reason=call.get("disconnection_reason"),
            started_at=_ms_to_dt(call.get("start_timestamp")),
            ended_at=_ms_to_dt(call.get("end_timestamp")),
            transcript=call.get("transcript"),
            recording_url=call.get("recording_url"),
            transfer_destination=(payload.get("transfer_destination")
                                  or call.get("transfer_destination")),
            raw=payload,
        )

        if ev.started_at and ev.ended_at:
            ev.duration_seconds = max(
                0, int((ev.ended_at - ev.started_at).total_seconds()))

        if kind in TRANSFER_EVENTS:
            return ev

        analysis = call.get("call_analysis") or {}
        if analysis:
            ev.analysis = analysis
            ev.summary = analysis.get("call_summary")
            custom = analysis.get("custom_analysis_data") or {}

            # The agent's own post-call fields. Read permissively by name
            # because the agent's analysis schema is configured in Retell, not
            # here — and deliberately tri-state, so a field the agent does not
            # define stays None instead of becoming a confident False.
            ev.reached_person = _truthy(
                custom.get("reached_person", custom.get("reached_human")))
            ev.voicemail = _truthy(
                custom.get("voicemail", custom.get("reached_voicemail")))
            ev.opted_out = _truthy(
                custom.get("opted_out", custom.get("do_not_call")))
            ev.wrong_number = _truthy(custom.get("wrong_number"))
            ev.interested = _truthy(custom.get("interested"))
            ev.appointment_booked = _truthy(
                custom.get("appointment_booked", custom.get("booked")))
            ev.callback_requested = _truthy(custom.get("callback_requested"))

            cb = custom.get("callback_at") or custom.get("callback_time")
            if cb:
                ev.callback_at = _parse_iso(cb)

            if ev.summary is None and analysis.get("summary"):
                ev.summary = analysis.get("summary")
        return ev


def _parse_iso(value) -> Optional[datetime]:
    """Best-effort ISO-8601 -> naive UTC. Returns None rather than raising: a
    callback time we cannot read must not lose us the whole webhook."""
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:                                             # noqa: BLE001
        return None
