"""
THE GATE IN FRONT OF THE EMAIL SENDERS THAT DO NOT EXIST YET.

Five call sites import and call `_send_email_via_graph`, a symbol that is
defined nowhere in this codebase. Every call raised ImportError, every
ImportError was caught by a bare `except Exception` that logged and moved on,
and so four features - bulk AI email, the pipeline auto-reply, the
post-appointment thank-you and the voice booking-link email - have never sent
anything and have never said so. Two of them incremented counters and set
"sent" flags before the attempt, so the database has been recording outbound
activity that never happened.

This module does not fix that. It makes the failure honest, and it puts the
authoritative compliance gate in front of each path BEFORE the sender is
restored, so that the day the sender is wired in there is nothing left to
remember. That ordering is deliberate: a send path that acquires a working
provider call before it acquires a consent check is a path that can mail a
family who said stop.

HOW THIS READS AT A CALL SITE. `gate_lead_email` always raises. Either

  * ValueError - the family may not be emailed. Raised by
    check_compliance_preflight, with the reason in the message. This is a
    refusal, not a fault, and a caller should record it as a block.
  * DemoBoundaryViolation - the lead belongs to a demonstration tenant and no
    provider call may be made for it at all.
  * EmailSendDisabled - the gate passed. This family COULD be emailed, and
    the only reason they will not be is that the sender has not been restored
    yet.

An always-raising function is an unusual shape and it is chosen on purpose:
it makes the disabled state impossible to overlook, and it makes restoring
the sender a single edit in one file rather than five. When that happens,
the `raise EmailSendDisabled` below becomes the real send, the call sites do
not change, and every one of them keeps the gate it already has.

NOTHING HERE SENDS. There is no provider import in this module and there is
no code path through it that reaches one.
"""

import logging
import os

from app.services import send_source as _src

logger = logging.getLogger(__name__)


# ── ONE SWITCH PER SOURCE, AND NO MASTER SWITCH ─────────────────────────────
#
# The first cut of this module had a single `raise EmailSendDisabled` at the
# bottom, so restoring the sender would have been one edit that turned all
# four dead paths on together. Bulk AI email is a human pressing a button on
# leads they picked; the post-appointment sweep is a cron that mails whoever
# had an appointment in the last three hours. Those two do not deserve to
# become live in the same moment, and nothing about the first implies the
# second is ready.
#
# So each gated source carries its own environment variable, each defaults to
# off, and there is deliberately NO variable that enables several at once. To
# turn one on you name it; nothing else moves.
#
# The pattern is app/services/workforce/activation.py's, including the
# accepted truthy spellings, and it is fail-safe in the same way: an unset
# variable, an unknown source and a typo all resolve to disabled. That is what
# makes "no repaired email path can send in this build" a statement about the
# code rather than a hope about the deployment.
#
# auto_send is deliberately ABSENT from this table. That path is already live,
# it does not come through this gate, and putting it here would mean an
# operator could switch off a working feature by forgetting an environment
# variable. See `source_enabled` for what a missing entry means.

STAFF_ESCALATION = "staff_escalation"
"""Not a send_source: nothing writes a lead history row for a staff alert.
It has its own switch so the internal escalation email can be restored
without any customer-facing path moving with it."""

_ENV_BY_SOURCE = {
    _src.BULK_AI:              "OUTBOUND_EMAIL_BULK_AI",
    _src.VOICE_BOOKING_LINK:   "OUTBOUND_EMAIL_VOICE_BOOKING_LINK",
    _src.PIPELINE_AUTO_REPLY:  "OUTBOUND_EMAIL_PIPELINE_AUTO_REPLY",
    _src.APPOINTMENT_FOLLOWUP: "OUTBOUND_EMAIL_APPOINTMENT_FOLLOWUP",
    STAFF_ESCALATION:          "OUTBOUND_EMAIL_STAFF_ESCALATION",
}

GATED_SOURCES = tuple(_ENV_BY_SOURCE)


def _env_flag(name: str, default: bool = False) -> bool:
    """Same spellings app/services/workforce/activation.py accepts."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def source_enabled(source) -> bool:
    """Is THIS repaired path permitted to reach a provider in this deployment?

    Answers the question for gated sources only. A source with no entry in the
    table above - auto_send, cadence, manual, a typo, None - is not "allowed by
    default", it is simply not a path this gate governs, and the honest answer
    to "may the gate let it send" is no. Every caller of this function is
    downstream of the gate, so failing closed is the correct reading.
    """
    name = _ENV_BY_SOURCE.get(source)
    if name is None:
        return False
    return _env_flag(name, False)


def enablement_report() -> dict:
    """Every gated source and whether it is live, for a health endpoint or an
    operator asking "what can actually send right now"."""
    return {source: source_enabled(source) for source in GATED_SOURCES}


class EmailSendDisabled(RuntimeError):
    """The send was permitted and did not happen, because no sender exists.

    A RuntimeError rather than an HTTPException on purpose: three of the five
    call sites are a websocket callback, a cron sweep and a webhook handler,
    where a 503 is not an answer to anybody.
    """


_DISABLED = (
    "Email sending is not enabled on this path yet. The compliance gate "
    "passed and this message would have been sent; the provider call is "
    "deliberately not wired up."
)


def _disabled_reason(source) -> str:
    """Name the switch in the message. An operator reading a queue row or a log
    line should not have to go and find out which variable governs this path."""
    name = _ENV_BY_SOURCE.get(source)
    if name is None:
        return (f"Outbound email source {source!r} is not one this gate can "
                f"enable, so it cannot send.")
    return (f"Outbound email for source {source!r} is disabled in this "
            f"deployment ({name} is not set). The compliance gate passed; "
            f"nothing was sent.")


def gate_lead_email(db, lead, *, send_source, actor_user_id=None):
    """Run every pre-send guard for an email TO A FAMILY, then refuse to send.

    Always raises. See the module docstring for which exception means what.

    `send_source` is not used to make a decision - it is required so that
    each call site has already declared, in code, which
    app/services/send_source.py value its future row will carry. The value a
    path sends under should be decided when the gate goes in, not improvised
    later by whoever restores the sender.
    """
    # The demonstration boundary comes first, for the same reason it comes
    # first in send_email_to_lead: compliance asks about a real family's real
    # consent, and this asks whether there is a real family at all.
    from app.services.sms_service import _demo_send_guard
    _demo_send_guard(db, lead, "email")

    # The authoritative gate. DNC on any channel, capacity hold, allow_email
    # is False, manual_flag == "bad_email", and no address at all. It raises
    # ValueError naming the reason, and it is the same function the auto-send
    # queue and send_email_to_lead run.
    from app.services.compliance_service import check_compliance_preflight
    check_compliance_preflight(db, lead, channel="email")

    if not source_enabled(send_source):
        logger.info(
            "outbound email gate PASSED but source is disabled: lead=%s "
            "source=%s actor=%s", getattr(lead, "id", None), send_source,
            actor_user_id,
        )
        raise EmailSendDisabled(_disabled_reason(send_source))

    # The source is switched ON and the family may be contacted - and there is
    # still no sender, because restoring one is Phase 3. When that lands, the
    # send replaces this raise and every guard above it stays exactly where it
    # is. Reaching here in this build means somebody set an environment
    # variable ahead of the work; it is logged at warning for that reason.
    logger.warning(
        "outbound email source %s is ENABLED but no sender is wired up yet; "
        "lead=%s was not contacted", send_source, getattr(lead, "id", None),
    )
    raise EmailSendDisabled(_DISABLED)


def gate_staff_email(recipient_email, *, purpose):
    """Run the guards for an email to one of OUR OWN PEOPLE, then refuse.

    Deliberately NOT check_compliance_preflight. That function answers
    questions about a lead's consent - DNC, allow_email, capacity hold - and
    an advisor is not a lead. Running it here would be a category error, and
    it has no Lead to run against anyway.

    The one real guard a staff notification needs is that there is somebody to
    notify. app/services/ai_conversation_service._escalate_conversation checks
    exactly this before its send and returns quietly; the voice escalation
    path, which does the same job, never did - it would have handed None to
    the provider as a recipient. That check is here so both paths behave the
    same way.
    """
    if not recipient_email:
        raise ValueError(
            f"No notification address on file for {purpose}; nothing was sent."
        )
    if not source_enabled(STAFF_ESCALATION):
        logger.info("staff email gate PASSED but source is disabled: purpose=%s", purpose)
        raise EmailSendDisabled(_disabled_reason(STAFF_ESCALATION))
    logger.warning(
        "staff email source is ENABLED but no sender is wired up yet; "
        "purpose=%s was not delivered", purpose,
    )
    raise EmailSendDisabled(_DISABLED)
