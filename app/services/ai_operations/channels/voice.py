"""VOICE — THE ARCHITECTURE IS REAL AND THE ACTION DOES NOT HAPPEN.

WHAT EXISTS. A voice channel in the vocabulary; a voice permission on the
lead; a voice feature on the organization; voice eligibility answered by the
same engine as SMS and email; a voice tool in T6's registry that an employee
can hold authority for; a call-disposition model (`completed`, `no_answer`,
`busy`, `voicemail`, `transferred_to_human`, `callback_requested`, `failed`);
a duration and cost model; transcript and summary references; inbound call
routing through the same inbound resolver; and a simulated adapter the
lifecycle proofs actually drive.

WHAT DOES NOT EXIST. A way to place a call. `LiveVoiceAdapter.send` refuses
unconditionally, before any provider is resolved and regardless of how any
customer, brand or employee is configured. There is no provider SDK imported
in this module, no credential read, and no code path that dials — so the
statement "no AI voice call can be placed by any configuration of this
deployment" is a property of the code rather than a promise about a flag.

WHY BUILD IT AT ALL, THEN. Because the alternative is discovering at
activation time that voice needs its own identity, its own eligibility, its
own audit and its own handoff — and building a parallel voice universe under
time pressure, which is how the second, weaker copy of every safety gate gets
written. The architecture is here so that turning voice on later is an
operational decision about one adapter, not a second engineering project.

TURNING IT ON LATER, HONESTLY: implement `send` against a provider behind
this same interface, set AI_OPERATIONS_LIVE_SEND and AI_WORKFORCE_LIVE_VOICE,
raise `DEFAULT_VOICE_DAILY_CAP` above zero, and — this is the part that is
not a flag — satisfy the call-disclosure requirements for the jurisdictions
the customer operates in. That last item is a legal question this platform
does not answer on a customer's behalf.
"""

import logging
import time

from sqlalchemy.orm import Session

from app.services.ai_operations import constants as C
from app.services.ai_operations import flags
from app.services.ai_operations.channels.base import (ChannelAdapter,
                                                      SendRequest, SendResult)

_log = logging.getLogger(__name__)


class LiveVoiceAdapter(ChannelAdapter):
    """Registered, resolvable, and refuses every call.

    Deliberately NOT omitted from the registry. An absent adapter would make
    a voice request fail with "no provider configured", which reads like a
    setup problem somebody should fix; this refuses with
    `live_voice_disabled`, which is the truth and is auditable as a decision.
    """

    key = "voice_disabled"
    channel = C.CHANNEL_VOICE
    reaches_outside = True

    def send(self, db: Session, req: SendRequest) -> SendResult:
        started = time.time()
        _log.warning("ai_operations: voice call REFUSED for thread %s — "
                     "live voice is disabled platform-wide in this build",
                     req.thread_id)
        return SendResult(
            outcome=C.P_REJECTED, provider=self.key, simulated=False,
            denial_code=C.D_LIVE_VOICE_DISABLED,
            error=("Live AI voice is disabled platform-wide in this build. "
                   "No configuration of any customer, brand or employee "
                   "places a call."),
            duration_ms=int((time.time() - started) * 1000),
            detail={"live_voice_enabled": flags.live_voice_enabled()})


def voice_outcome_to_state(disposition: str) -> str:
    """Where a call's disposition leaves the communication.

    The mapping is here rather than in the orchestrator because it is the one
    piece of genuinely voice-shaped logic in the system: a voicemail is a
    delivered message that expects no immediate reply, a no-answer is a
    failure worth retrying, and a request to be called back is a follow-up
    rather than an end.
    """
    return {
        C.V_COMPLETED: C.SENT,
        C.V_VOICEMAIL: C.SENT,
        C.V_TRANSFERRED: C.HANDOFF_REQUIRED,
        C.V_CALLBACK_REQUESTED: C.FOLLOWUP_SCHEDULED,
        C.V_NO_ANSWER: C.FAILED,
        C.V_BUSY: C.FAILED,
        C.V_FAILED: C.FAILED,
    }.get(disposition or "", C.FAILED)
