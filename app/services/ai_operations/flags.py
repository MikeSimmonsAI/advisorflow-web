"""DARK LAUNCH — the switches, and the fact that none of them is on.

THREE INDEPENDENT BRAKES, AND NONE OF THEM CAN BE RELEASED BY CONFIGURATION
A CUSTOMER OWNS.

    AI_OPERATIONS_ENABLED      Is this layer allowed to do anything at all?
                               Default FALSE. With it off, every operation
                               refuses with `ai_operations_disabled` before
                               any other question is asked, so deploying this
                               code changes the behaviour of exactly nothing.

    AI_OPERATIONS_LIVE_SEND    May a real provider adapter be resolved?
                               Default FALSE. With it off, `channels.resolve`
                               returns the SIMULATED adapter for every channel
                               and every organization, whatever the activation
                               stage says. This is belt AND braces: T6's
                               activation stage already refuses executing
                               tools outside CONTROLLED/ACTIVE, and this
                               refuses the adapter as well, so two independent
                               mistakes are needed to reach a person.

    AI_WORKFORCE_LIVE_VOICE    May an AI voice call be placed? Default FALSE,
                               read through T6's own `activation` module when
                               it is present so there is ONE voice switch in
                               the deployment rather than two that can
                               disagree.

WHY ENVIRONMENT VARIABLES AND NOT A TABLE. Because the case these exist for
includes "the database is the thing that has gone wrong". An operator who
needs the AI workforce silent must be able to make it silent by restarting a
service with one variable set, without reaching God Mode and without a
migration. That is also why the only direction they work in is OFF: a
variable can stop execution, and nothing here can start it.

WHAT IS DELIBERATELY NOT HERE. No per-customer enablement. A customer's own
stage lives in `ai_workforce_activations` (T6) where it is an auditable row
with a name and a timestamp against it; a customer who could switch
themselves on from an environment variable would be a customer nobody
approved.
"""

import logging
import os
from typing import Dict

_log = logging.getLogger(__name__)

ENV_ENABLED = "AI_OPERATIONS_ENABLED"
ENV_LIVE_SEND = "AI_OPERATIONS_LIVE_SEND"
ENV_KILL = "AI_OPERATIONS_KILL"
ENV_LIVE_VOICE = "AI_WORKFORCE_LIVE_VOICE"
ENV_WORKFORCE_KILL = "AI_WORKFORCE_KILL"


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def operations_enabled() -> bool:
    """May this layer run at all? FALSE in this build."""
    if kill_engaged():
        return False
    return _flag(ENV_ENABLED, False)


def kill_engaged() -> bool:
    """The environment-level brake. Checked FIRST, everywhere.

    Two variables are honoured rather than one: this layer's own kill and
    T6's `AI_WORKFORCE_KILL`. Stopping the workforce must stop its reach too,
    and an operator in an incident should not have to know there are two
    layers to stop.
    """
    return _flag(ENV_KILL, False) or _flag(ENV_WORKFORCE_KILL, False)


def live_send_enabled() -> bool:
    """May a real (non-simulated) channel adapter be resolved? FALSE here."""
    if kill_engaged():
        return False
    return _flag(ENV_ENABLED, False) and _flag(ENV_LIVE_SEND, False)


def live_voice_enabled() -> bool:
    """May an AI voice call be placed anywhere in this deployment? FALSE.

    Read through T6's activation module when it is importable, so the
    deployment has ONE voice switch. When T6 is not on this branch the local
    reading of the same variable is used — and it defaults to False, which is
    the same answer.
    """
    if kill_engaged() or not live_send_enabled():
        return False
    try:
        from app.services.workforce import activation as wf_activation
        return bool(wf_activation.live_voice_enabled())
    except Exception:                                        # noqa: BLE001
        return _flag(ENV_LIVE_VOICE, False)


def state() -> Dict[str, object]:
    """The whole switch state, for the operations console and the audit.

    Rendered verbatim on the God screen: "why is nothing sending" is a
    question with a factual answer, and this is it.
    """
    return {
        "operations_enabled": operations_enabled(),
        "live_send_enabled": live_send_enabled(),
        "live_voice_enabled": live_voice_enabled(),
        "kill_engaged": kill_engaged(),
        "variables": {
            ENV_ENABLED: os.environ.get(ENV_ENABLED),
            ENV_LIVE_SEND: os.environ.get(ENV_LIVE_SEND),
            ENV_KILL: os.environ.get(ENV_KILL),
            ENV_LIVE_VOICE: os.environ.get(ENV_LIVE_VOICE),
            ENV_WORKFORCE_KILL: os.environ.get(ENV_WORKFORCE_KILL),
        },
        "explanation": (
            "AI Operations ships dark. Every operation refuses while "
            "AI_OPERATIONS_ENABLED is unset, every channel resolves to the "
            "simulated adapter while AI_OPERATIONS_LIVE_SEND is unset, and no "
            "configuration of any customer, brand or employee places a voice "
            "call in this build."
        ),
    }
