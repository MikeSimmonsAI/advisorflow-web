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

logger = logging.getLogger(__name__)


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

    logger.info(
        "outbound email gate PASSED but sender is disabled: lead=%s source=%s actor=%s",
        getattr(lead, "id", None), send_source, actor_user_id,
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
    logger.info("staff email gate PASSED but sender is disabled: purpose=%s", purpose)
    raise EmailSendDisabled(_DISABLED)
