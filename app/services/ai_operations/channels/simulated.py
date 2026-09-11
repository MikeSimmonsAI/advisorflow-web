"""THE SIMULATED ADAPTERS — the real engine, against a provider that isn't.

WHAT MAKES A SIMULATION HONEST. These adapters are reached through the SAME
orchestrator, the same authority checks, the same eligibility gate, the same
state machine, the same idempotency keys and the same audit as a live send.
The ONLY difference is the last inch: instead of handing the message to
Twilio, the adapter records that it would have. A simulation that skipped the
gates would prove nothing, because the thing worth proving is that the gates
work.

DETERMINISM IS A FEATURE. `provider_message_id` is derived from the content
and the thread rather than randomly generated, so a test can assert on it and
a replayed scenario produces the same references twice. A simulator that
produced different ids each run would make "did this send twice?" unanswerable
in exactly the tests that exist to answer it.

FAILURE IS SIMULABLE, because an engine that has only ever been tested
against a provider that always works has not been tested. `FailingAdapter`
and `TimingOutAdapter` are first-class and the adversarial harness uses them
to prove the retry path, the consecutive-failure ceiling and the
"provider failed after it actually delivered" case.
"""

import time
from typing import List, Optional

from sqlalchemy.orm import Session

from app.services.ai_operations import audit
from app.services.ai_operations import budget
from app.services.ai_operations import constants as C
from app.services.ai_operations.channels.base import (ChannelAdapter,
                                                      SendRequest, SendResult)


def _reference(req: SendRequest, prefix: str) -> str:
    """A deterministic, obviously-fake provider reference."""
    seed = "|".join([req.thread_id or "", req.channel or "",
                     req.to_address or "", req.subject or "",
                     req.body or "", req.idempotency_key or ""])
    return "%s_%s" % (prefix, audit.digest(seed) or "0")


class SimulatedAdapter(ChannelAdapter):
    """Accepts anything, reaches nobody, records exactly what it saw."""

    key = "simulated"
    reaches_outside = False

    def __init__(self, channel: str):
        self.channel = channel
        self.key = "simulated_%s" % channel
        # Every request this adapter handled, in memory, for assertions in
        # the simulator and the harness. Deliberately NOT persisted: the
        # durable record is the `ai_communications` row, and a second store
        # of message bodies is a second retention problem.
        self.outbox: List[SendRequest] = []

    def send(self, db: Session, req: SendRequest) -> SendResult:
        started = time.time()
        self.outbox.append(req)
        seconds = 0
        disposition = None
        if self.channel == C.CHANNEL_VOICE:
            # A synthetic call that behaves like a real one: a short
            # conversation with a disposition, so the voice architecture is
            # exercised end to end without a call being placed.
            seconds = min(int(req.max_seconds or 0), 45)
            disposition = C.V_COMPLETED
        return SendResult(
            outcome=C.P_SIMULATED, provider=self.key,
            provider_message_id=_reference(req, "sim"),
            provider_status="simulated", simulated=True,
            duration_ms=int((time.time() - started) * 1000),
            estimated_cost_usd=budget.estimate_cost(self.channel,
                                                    seconds=seconds),
            voice_disposition=disposition, voice_seconds=seconds or None,
            detail={"note": "No message left this process."})


class ScriptedAdapter(SimulatedAdapter):
    """A simulated adapter whose outcomes are chosen in advance.

    Used by the harness to drive the paths a well-behaved provider never
    produces: a hard rejection, a bounce, a call that goes to voicemail.
    Outcomes are consumed in order and the last one repeats, so a scenario
    can say "fail twice then succeed" without counting calls.
    """

    key = "scripted"

    def __init__(self, channel: str, outcomes: List[str],
                 dispositions: Optional[List[str]] = None):
        super().__init__(channel)
        self.key = "scripted_%s" % channel
        self._outcomes = list(outcomes) or [C.P_SIMULATED]
        self._dispositions = list(dispositions or [])
        self._index = 0

    def _next(self, seq, default):
        if not seq:
            return default
        value = seq[min(self._index, len(seq) - 1)]
        return value

    def send(self, db: Session, req: SendRequest) -> SendResult:
        result = super().send(db, req)
        outcome = self._next(self._outcomes, C.P_SIMULATED)
        disposition = self._next(self._dispositions, None)
        self._index += 1
        result.outcome = outcome
        if outcome in (C.P_FAILED, C.P_REJECTED):
            result.error = "scripted %s" % outcome
            result.estimated_cost_usd = 0.0
        if outcome == C.P_TIMEOUT:
            result.error = "scripted timeout"
            result.provider_message_id = None
            result.estimated_cost_usd = 0.0
        if disposition:
            result.voice_disposition = disposition
        return result


class FailingAdapter(SimulatedAdapter):
    """Always fails. The provider-failure path has to be exercised."""

    def send(self, db: Session, req: SendRequest) -> SendResult:
        result = super().send(db, req)
        result.outcome = C.P_FAILED
        result.provider_message_id = None
        result.error = "simulated provider failure"
        result.estimated_cost_usd = 0.0
        return result


class TimingOutAdapter(SimulatedAdapter):
    """Never answers inside the budget.

    THE DANGEROUS CASE, and the reason this exists: a timeout does not mean
    the message was not sent. It means we do not know. The orchestrator
    therefore records the attempt under its idempotency key BEFORE calling
    the adapter, so a retry after a timeout is refused as a duplicate rather
    than sending a second text to a family that already got the first one.
    """

    def send(self, db: Session, req: SendRequest) -> SendResult:
        result = super().send(db, req)
        result.outcome = C.P_TIMEOUT
        result.provider_message_id = None
        result.error = ("the provider did not answer within %ds"
                        % C.PROVIDER_TIMEOUT_SECONDS)
        result.estimated_cost_usd = 0.0
        return result
