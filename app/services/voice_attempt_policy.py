"""How many times we may ring one family, and what counts as a try.

This replaced a module-level `MAX_CALL_ATTEMPTS = 3` in the orchestrator. Two
separate things were wrong with that constant, and they pulled in opposite
directions.

TOO RIGID. It counted `voice_calls` rows, so once a lead reached three there
was no supported remedy: not a setting, not a console action, nothing short of
deleting call history or editing code and deploying. A customer must be
operable without a shell, a seed script, or a developer, and a pilot that
cannot be unblocked is neither.

TOO BLUNT. Every row counted the same. A thirteen-second call that reached a
full voicemail box consumed a family's third and final attempt without a word
being exchanged. "We have spoken to this family three times and they are not
interested" and "this family has been out three times" are different facts, and
only the first is a reason to stop.

So there are two caps now, resolved through the same hierarchy:

    LIVE CONVERSATIONS   how often a person may actually be spoken to
    DIALS                how often the phone may ring at all

and a cooldown, so a permitted retry cannot become a redial loop.

RESOLUTION ORDER — most specific wins, and NULL means "not set here":

    campaign  →  use case (the voice agent config)  →  organization  →  system

NOTHING HERE CAN PRODUCE AN UNLIMITED PATH. Every level is clamped to
`HARD_CEILING` on the way out, a value of 0 or less is rejected as
unconfigured rather than read as "never call", and a level that fails to load
is skipped rather than treated as permission. A misconfiguration makes this
stricter, never looser.
"""
import logging
from typing import Optional, Tuple

log = logging.getLogger(__name__)

# The system defaults. These are what every organization gets until somebody
# deliberately says otherwise, and they are the values the platform ran on
# before this module existed.
DEFAULT_MAX_CALL_ATTEMPTS = 3       # live conversations
DEFAULT_MAX_DIAL_ATTEMPTS = 6       # dials, including voicemail and no-answer
DEFAULT_REDIAL_COOLDOWN_MINUTES = 60

# The ceiling no configuration may exceed, at any level, for any customer.
#
# This is the line between "a customer can tune their own persistence" and
# "a customer can be configured into harassing a family". A number typed with
# an extra zero in a settings field must not become forty calls; it becomes
# this. Raising it is a deliberate code change with a reason attached, which is
# exactly the friction it should have.
HARD_CEILING_CALL_ATTEMPTS = 10
HARD_CEILING_DIAL_ATTEMPTS = 20
HARD_CEILING_COOLDOWN_MINUTES = 60 * 24 * 7

# What `answered_by` may hold. The vocabulary is fixed here so the webhook, the
# orchestrator and the console cannot drift into three spellings of the same
# outcome.
ANSWERED_HUMAN = "human"
ANSWERED_VOICEMAIL = "voicemail"
ANSWERED_NO_ANSWER = "no_answer"
ANSWERED_BUSY = "busy"
ANSWERED_FAILED = "failed"
ANSWERED_UNKNOWN = "unknown"

ANSWERED_BY_VALUES = (ANSWERED_HUMAN, ANSWERED_VOICEMAIL, ANSWERED_NO_ANSWER,
                      ANSWERED_BUSY, ANSWERED_FAILED, ANSWERED_UNKNOWN)

# Only a human on the line is a conversation. Everything else is a dial.
LIVE_ANSWERED_BY = (ANSWERED_HUMAN,)


class AttemptPolicy(object):
    """The resolved numbers, plus where each one came from.

    `source` exists so "why won't it call?" is answerable from the API response
    rather than by reading four tables. It is never a secret.
    """

    __slots__ = ("max_call_attempts", "max_dial_attempts",
                 "redial_cooldown_minutes", "source")

    def __init__(self, max_call_attempts, max_dial_attempts,
                 redial_cooldown_minutes, source=None):
        self.max_call_attempts = max_call_attempts
        self.max_dial_attempts = max_dial_attempts
        self.redial_cooldown_minutes = redial_cooldown_minutes
        self.source = source or {}

    def as_dict(self):
        return {
            "max_call_attempts": self.max_call_attempts,
            "max_dial_attempts": self.max_dial_attempts,
            "redial_cooldown_minutes": self.redial_cooldown_minutes,
            "source": dict(self.source),
        }


def _positive_int(value) -> Optional[int]:
    """A usable override, or None.

    Zero and negatives are rejected rather than honoured. A settings field left
    at 0 is far more likely to be "nobody filled this in" than "never call this
    family again" - and there is already an explicit way to say the latter,
    which is the suppression list. Reading 0 as a ban would put a permanent
    do-not-call in a numeric field nobody thinks of as one.
    """
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _clamp(value: int, ceiling: int) -> int:
    return value if value <= ceiling else ceiling


def _first(levels, field) -> Tuple[Optional[int], Optional[str]]:
    """The first level that names `field`, and which level that was."""
    for label, obj in levels:
        if obj is None:
            continue
        n = _positive_int(getattr(obj, field, None))
        if n is not None:
            return n, label
    return None, None


def resolve_attempt_policy(db, organization_id: str, config=None,
                           campaign=None) -> AttemptPolicy:
    """The attempt policy in force for one call.

    `config` is the VoiceAgentConfig (the use case) and `campaign` the
    VoiceCallCampaign, both optional - a one-off call has neither and lands on
    the organization's own settings, or the system default.

    NEVER RAISES. A database this cannot read yields the system defaults, which
    are the strictest sensible answer; failing open here would mean a transient
    error briefly removing a family's protection.
    """
    org = None
    if organization_id:
        try:
            from app.models.models import Organization
            org = (db.query(Organization)
                   .filter(Organization.id == organization_id).first())
        except Exception:
            log.exception("attempt policy: could not read organization %s",
                          organization_id)

    levels = (("campaign", campaign), ("use_case", config),
              ("organization", org))

    calls, calls_src = _first(levels, "max_call_attempts")
    dials, dials_src = _first(levels, "max_dial_attempts")
    cool = _positive_int(getattr(org, "redial_cooldown_minutes", None)) if org else None

    source = {
        "max_call_attempts": calls_src or "system",
        "max_dial_attempts": dials_src or "system",
        "redial_cooldown_minutes": "organization" if cool else "system",
    }

    calls = _clamp(calls or DEFAULT_MAX_CALL_ATTEMPTS, HARD_CEILING_CALL_ATTEMPTS)
    dials = _clamp(dials or DEFAULT_MAX_DIAL_ATTEMPTS, HARD_CEILING_DIAL_ATTEMPTS)
    cool = _clamp(cool or DEFAULT_REDIAL_COOLDOWN_MINUTES,
                  HARD_CEILING_COOLDOWN_MINUTES)

    # A dial cap below the conversation cap would make the conversation cap
    # unreachable and the reported reason misleading. Raise the dial cap to
    # match rather than silently lowering what the customer asked for.
    if dials < calls:
        dials = calls
        source["max_dial_attempts"] = source["max_dial_attempts"] + "+raised_to_calls"

    return AttemptPolicy(calls, dials, cool, source)


def is_live_conversation(answered_by: Optional[str]) -> bool:
    """Did a person speak to us.

    NULL is True. Every `voice_calls` row written before `answered_by` existed
    has no value, and treating those as conversations keeps the cap exactly as
    strict for existing leads as it was the day before this shipped. A
    migration must not quietly hand anybody extra attempts against families
    who have already been called.
    """
    if answered_by is None:
        return True
    return str(answered_by).strip().lower() in LIVE_ANSWERED_BY


def classify_answer(*, disconnect_reason=None, duration_seconds=None,
                    transcript=None, provider_answered_by=None,
                    failed=False) -> str:
    """What the provider's call record says happened, in our vocabulary.

    The provider's own answering-machine verdict wins when it gives one -
    Retell reports `voicemail` on the call record once voicemail detection is
    enabled on the agent, and it hears the line, which we do not.

    The fallbacks below are for providers and calls that report nothing. They
    are deliberately conservative: anything not positively identified as a
    machine is `unknown`, which `is_live_conversation` counts as a real
    conversation. Guessing "voicemail" from a short call would hand a family's
    attempts back on the strength of a hunch.
    """
    if failed:
        return ANSWERED_FAILED

    if provider_answered_by:
        p = str(provider_answered_by).strip().lower()
        if p in ANSWERED_BY_VALUES:
            return p
        if "machine" in p or "voicemail" in p or "voice_mail" in p:
            return ANSWERED_VOICEMAIL
        if "human" in p or "person" in p:
            return ANSWERED_HUMAN

    reason = (disconnect_reason or "").strip().lower()
    if reason in ("dial_no_answer", "no_answer", "no-answer"):
        return ANSWERED_NO_ANSWER
    if reason in ("dial_busy", "busy"):
        return ANSWERED_BUSY
    if reason in ("dial_failed", "error", "failed"):
        return ANSWERED_FAILED
    if "voicemail" in reason or "machine" in reason:
        return ANSWERED_VOICEMAIL

    # A voicemail greeting is one long uninterrupted utterance addressed to
    # nobody, and the transcript shows it. This catches the case Retell's own
    # detection missed on the call that started all this: "the mailbox is full
    # and cannot accept any messages at this time."
    text = (transcript or "").strip().lower()
    if text:
        markers = ("mailbox is full", "leave a message", "leave your message",
                   "after the tone", "at the tone", "is not available",
                   "please record your message", "has a voice mailbox",
                   "not available right now", "record your message")
        if any(m in text for m in markers):
            return ANSWERED_VOICEMAIL

    if not text and (duration_seconds or 0) <= 0:
        return ANSWERED_NO_ANSWER

    return ANSWERED_UNKNOWN
