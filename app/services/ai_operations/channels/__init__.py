"""CHANNEL RESOLUTION — which adapter, and the four questions before it.

`resolve()` is the only place an adapter is chosen, and it answers NO in four
independent ways before it answers with a live one:

    1. Is live sending enabled in this deployment at all?     flags
    2. Is the employee's activation stage one where a tool
       that reaches a real person may run?                    T6 activation
    3. Was this employee LOADED from T6, or merely declared?  contracts
    4. Is there a live adapter for this channel at all?       this registry

Any one of those saying no resolves the SIMULATED adapter — not an error, and
not a silent skip. The operation proceeds through the entire engine and is
recorded as simulated, which is what makes a dark launch observable: an
operator can watch exactly what the workforce WOULD have done, with real
eligibility answers and real state transitions, and nothing leaves the
building.

THE THIRD QUESTION IS THE ONE THAT IS EASY TO MISS. A declared employee
context — the kind the synthetic profiles and the evaluation harness build
when T6 is not deployed — can assert any authority it likes. If a declared
context could reach a live adapter, then a test fixture, a demo script or a
mistaken seeder could text a real family. It cannot: `source != "t6"` forces
simulation, always, and no flag overrides it.

REGISTRATION IS A FUNCTION CALL, NOT A TABLE. Same decision as
`app/services/calendar_providers`: a registry a typo can silently empty is a
registry that resolves to nothing, and "nothing" for a channel means an
outage nobody configured.
"""

import logging
from typing import Callable, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.services.ai_operations import constants as C
from app.services.ai_operations import contracts, flags
from app.services.ai_operations.channels.base import (AdapterUnavailable,
                                                      ChannelAdapter,
                                                      SendRequest, SendResult)
from app.services.ai_operations.channels.email_resend import ResendEmailAdapter
from app.services.ai_operations.channels.simulated import (FailingAdapter,
                                                           ScriptedAdapter,
                                                           SimulatedAdapter,
                                                           TimingOutAdapter)
from app.services.ai_operations.channels.sms_twilio import TwilioSMSAdapter
from app.services.ai_operations.channels.voice import LiveVoiceAdapter

_log = logging.getLogger(__name__)

__all__ = [
    "AdapterUnavailable", "ChannelAdapter", "SendRequest", "SendResult",
    "SimulatedAdapter", "ScriptedAdapter", "FailingAdapter",
    "TimingOutAdapter", "resolve", "register_live", "register_simulated",
    "reset_adapters", "describe",
]


_LIVE_FACTORIES: Dict[str, Callable[[], ChannelAdapter]] = {
    C.CHANNEL_SMS: TwilioSMSAdapter,
    C.CHANNEL_EMAIL: ResendEmailAdapter,
    C.CHANNEL_VOICE: LiveVoiceAdapter,
}

# Simulated adapters are singletons per channel so a scenario can inspect the
# outbox after driving a lifecycle. Replaced wholesale by `register_simulated`
# when the harness wants a scripted or failing provider.
_SIMULATED: Dict[str, ChannelAdapter] = {}


def _simulated_for(channel: str) -> ChannelAdapter:
    adapter = _SIMULATED.get(channel)
    if adapter is None:
        adapter = SimulatedAdapter(channel)
        _SIMULATED[channel] = adapter
    return adapter


def register_live(channel: str, factory: Callable[[], ChannelAdapter]) -> None:
    """Swap the live implementation for a channel. Deployment-level surgery,
    not per-customer configuration."""
    _LIVE_FACTORIES[channel] = factory


def register_simulated(channel: str, adapter: ChannelAdapter) -> None:
    """Install a specific simulated adapter — scripted, failing, timing out.
    Used by the evaluation harness to drive provider behaviour that a healthy
    provider never produces."""
    _SIMULATED[channel] = adapter


def reset_adapters() -> None:
    """Forget every installed simulated adapter.

    Tests share a process. A scripted failure left installed by one test is a
    mysterious failure in the next one, which is a debugging afternoon nobody
    needs to spend twice.
    """
    _SIMULATED.clear()


def resolve(db: Session, ctx: contracts.EmployeeContext, channel: str, *,
            reaches_outside: bool = True) -> Tuple[ChannelAdapter, str]:
    """(adapter, why) — the adapter for this employee on this channel.

    `why` is a human sentence explaining the choice, recorded on the action
    so "why was this simulated" never needs reconstructing.
    """
    if channel not in C.ALL_CHANNELS:
        raise AdapterUnavailable(channel, "'%s' is not a supported channel."
                                 % channel)

    if not reaches_outside:
        return _simulated_for(channel), (
            "The operation does not reach outside, so no provider is used.")

    if not flags.live_send_enabled():
        return _simulated_for(channel), (
            "Live sending is disabled for this deployment; the simulated "
            "provider was used and nothing left this process.")

    if ctx.is_declared:
        return _simulated_for(channel), (
            "This employee context was declared rather than loaded from the "
            "workforce engine, so it may only ever reach the simulated "
            "provider.")

    if not ctx.may_execute:
        return _simulated_for(channel), (
            "The employee's activation stage (%s) does not permit an action "
            "that reaches a real person." % ctx.activation_state)

    if channel == C.CHANNEL_VOICE and not flags.live_voice_enabled():
        # The disabled voice adapter is returned deliberately rather than a
        # simulated one: a voice request in an executing stage should be
        # REFUSED and audited as refused, not quietly simulated, because a
        # customer who believes calls are going out must be told they are not.
        return _LIVE_FACTORIES[C.CHANNEL_VOICE](), (
            "Live AI voice is disabled platform-wide; the call was refused.")

    factory = _LIVE_FACTORIES.get(channel)
    if factory is None:
        raise AdapterUnavailable(
            channel, "No live provider is registered for %s." % channel)
    return factory(), ("Live provider for %s." % channel)


def describe(db: Optional[Session] = None) -> Dict:
    """What each channel would resolve to right now, for the console."""
    return {
        "live_send_enabled": flags.live_send_enabled(),
        "live_voice_enabled": flags.live_voice_enabled(),
        "channels": {
            channel: {
                "live_adapter": _LIVE_FACTORIES[channel].__name__
                if hasattr(_LIVE_FACTORIES[channel], "__name__")
                else str(_LIVE_FACTORIES[channel]),
                "simulated_adapter": _simulated_for(channel).key,
            }
            for channel in C.ALL_CHANNELS
        },
    }
