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

import json
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

# ── PUBLIC DISCOVERY / DEMO BOOKING ─────────────────────────────────────────
#
# THREE SWITCHES, BECAUSE THEY ARE THREE DECISIONS.
#
# These three paths first shipped sharing STAFF_ESCALATION, and that was wrong
# for the reason the comment above this table already gives: one variable that
# turns on several unrelated paths is precisely what this module was built to
# avoid. Sharing it meant an operator who wanted a prospect to receive their
# booking confirmation would, in the same edit, have enabled the AI
# conversation service's internal escalation alerts - a completely unrelated
# feature, on a completely unrelated schedule, that nobody was asking about.
#
# It was also wrong in the other direction. "Let the sales team start getting
# notified about website bookings" and "start emailing prospects automatically"
# are different sentences with different blast radii, and a deployment must be
# able to say the first without saying the second.
#
# So each path names its own variable, and none of them implies another:
#
#   CONFIRMATION - the ONE branded message a prospect receives immediately
#                  after booking. Customer-facing.
#   INTERNAL     - the alert to the assigned salesperson and the leadership
#                  who were actually booked, plus the emailed .ics invitation
#                  those same people receive when they have no calendar
#                  connected. Staff-facing, and the reason those two share a
#                  switch is that they are one notification to one set of
#                  people, differing only in whether it carries an attachment.
#   REMINDERS    - the 24-hour and one-hour messages before the meeting.
#                  Customer-facing, and deliberately separate from the
#                  confirmation: a brand may well want the confirmation live
#                  while it decides whether automated reminders are wanted at
#                  all, and the reverse is a coherent position too.
#
# None of these is a `send_source`: like STAFF_ESCALATION, no lead history row
# is written for any of them - a brand-sales prospect is not a customer's lead.
PUBLIC_BOOKING_CONFIRMATION = "public_booking_confirmation"
PUBLIC_BOOKING_INTERNAL     = "public_booking_internal"
PUBLIC_BOOKING_REMINDERS    = "public_booking_reminders"

# Handy for a health endpoint or a setup screen that wants to report on the
# booking feature specifically rather than on all gated sources.
PUBLIC_BOOKING_SOURCES = (PUBLIC_BOOKING_CONFIRMATION, PUBLIC_BOOKING_INTERNAL,
                          PUBLIC_BOOKING_REMINDERS)

_ENV_BY_SOURCE = {
    _src.BULK_AI:              "OUTBOUND_EMAIL_BULK_AI",
    _src.VOICE_BOOKING_LINK:   "OUTBOUND_EMAIL_VOICE_BOOKING_LINK",
    _src.PIPELINE_AUTO_REPLY:  "OUTBOUND_EMAIL_PIPELINE_AUTO_REPLY",
    _src.APPOINTMENT_FOLLOWUP: "OUTBOUND_EMAIL_APPOINTMENT_FOLLOWUP",
    STAFF_ESCALATION:          "OUTBOUND_EMAIL_STAFF_ESCALATION",
    PUBLIC_BOOKING_CONFIRMATION: "OUTBOUND_EMAIL_PUBLIC_BOOKING_CONFIRMATION",
    PUBLIC_BOOKING_INTERNAL:     "OUTBOUND_EMAIL_PUBLIC_BOOKING_INTERNAL",
    PUBLIC_BOOKING_REMINDERS:    "OUTBOUND_EMAIL_PUBLIC_BOOKING_REMINDERS",
}

GATED_SOURCES = tuple(_ENV_BY_SOURCE)


def _env_flag(name: str, default: bool = False) -> bool:
    """Same spellings app/services/workforce/activation.py accepts."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def org_enabled_sources(org) -> list:
    """The sources THIS customer may use. NULL means none.

    Mirrors capabilities.self_managed_by line for line, including the reading
    of NULL, because the question has the same shape: God has not said this
    customer may use this path, and the safe reading of "never said" for
    contacting somebody's families is no.

    Unknown strings are dropped rather than honoured, so a typo in a stored
    list cannot enable anything, and a corrupt value yields no sources at all
    rather than a licence.
    """
    if org is None:
        return []
    raw = getattr(org, "outbound_email_sources", None)
    if raw is None:
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(value, list):
        return []
    return [s for s in value if isinstance(s, str) and s in _ENV_BY_SOURCE]


def org_source_enabled(org, source) -> bool:
    return source in org_enabled_sources(org)


def set_org_sources(db, org, actor, sources):
    """Replace a customer's outbound source list. Audited, like every other
    entitlement change. `None` and `[]` both mean none - there is deliberately
    no value meaning "all", because "all" is what nobody should be able to say
    in one keystroke about four paths that contact families."""
    from app.routers.audit_log_router import log_action

    before = org_enabled_sources(org)
    cleaned = []
    unknown = []
    for raw in (sources or []):
        if raw in _ENV_BY_SOURCE:
            if raw not in cleaned:
                cleaned.append(raw)
        else:
            unknown.append(raw)
    if unknown:
        raise ValueError(
            "Unknown outbound email source(s): %s. Valid: %s"
            % (", ".join(sorted(set(unknown))), ", ".join(GATED_SOURCES)))
    org.outbound_email_sources = json.dumps(cleaned)
    db.flush()
    log_action(
        db, org.id, actor.id,
        action="customer.outbound_email_sources_set",
        target_type="organization", target_id=org.id,
        platform_id=getattr(org, "platform_id", None),
        before={"outbound_email_sources": before},
        after={"outbound_email_sources": cleaned},
        commit=False,
    )
    return cleaned


def effective_report(org) -> dict:
    """Both halves, per source, and the AND of them. This is what an operator
    asking "why did that not send" should be shown."""
    out = {}
    for source in GATED_SOURCES:
        deployment = source_enabled(source)
        customer = org_source_enabled(org, source)
        out[source] = {
            "deployment_enabled": deployment,
            "organization_enabled": customer,
            "effective": bool(deployment and customer),
            "variable": _ENV_BY_SOURCE[source],
        }
    return out


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


def _org_for_lead(db, lead):
    """The LEAD'S organization, never the caller's.

    The same rule sms_service._demo_send_guard follows and for the same
    reason: a god_admin operating inside a customer has organization_id None,
    so reading the sender's tenancy answers the wrong question for exactly the
    caller most able to cause damage. The family that would be contacted
    belongs to one organization, and that organization decides.
    """
    from app.models.models import Organization
    org_id = getattr(lead, "organization_id", None)
    if not org_id:
        return None
    return db.query(Organization).filter(Organization.id == org_id).first()


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

    # BOTH HALVES, AND THE DEPLOYMENT HALF FIRST.
    #
    # A deployment switch alone is not enough on a white-label platform serving
    # several customers from one process: turning bulk AI email on for the
    # customer who asked for it would have turned it on for every other tenant
    # in the same deployment at the same instant.
    #
    # So the rule is DEPLOYMENT AND ORGANIZATION. The deployment switch says
    # "this build may do this at all"; the organization list says "this
    # customer bought it / asked for it / is ready for it". Neither alone
    # sends, and one customer's list can never speak for another's.
    if not source_enabled(send_source):
        logger.info(
            "outbound email gate PASSED but source is disabled for this "
            "deployment: lead=%s source=%s actor=%s",
            getattr(lead, "id", None), send_source, actor_user_id,
        )
        raise EmailSendDisabled(_disabled_reason(send_source))

    org = _org_for_lead(db, lead)
    if not org_source_enabled(org, send_source):
        logger.info(
            "outbound email gate PASSED but source is not enabled for this "
            "organization: lead=%s org=%s source=%s",
            getattr(lead, "id", None), getattr(lead, "organization_id", None),
            send_source,
        )
        raise EmailSendDisabled(
            f"Outbound email for source {send_source!r} is not enabled for "
            f"this organization. The deployment allows it; this customer has "
            f"not been switched on for it. Nothing was sent."
        )

    # CLEARED. Both switches are on, the family may be contacted, and the
    # caller may now send.
    #
    # This function used to raise EmailSendDisabled here unconditionally,
    # because there was no sender behind it. There is one now -
    # `send_lead_email` below - so the terminal raise is gone and what stops a
    # send is the switches, which is where that decision belongs. The shipped
    # posture is every switch off, so in practice nothing reaches this line.
    return None


def send_lead_email(db, lead, *, advisor, subject, body_html, send_source,
                    actor_user_id=None):
    """THE RESTORED SENDER. Gate first, provider second, row always.

    This is what the four repaired paths call. It is a thin composition and
    that is the point - every guarantee below belongs to something that
    already existed and was simply never reached from these paths:

      gate_lead_email        demo boundary, compliance preflight, the
                             deployment switch and the organization switch,
                             in that order, before a provider is resolved.
      send_email_to_lead     the only email function that writes the
                             `email_messages` row the timeline, the activity
                             feed and the sent log all read - now carrying
                             send_source and sent_by_user_id, and raising with
                             the provider's own error text rather than a
                             generic rejection.

    RAISES, ALWAYS, ON ANYTHING THAT IS NOT A SUCCESSFUL SEND:

      ValueError                 the family may not be emailed (compliance).
      DemoBoundaryViolation      the lead belongs to a demonstration tenant.
      EmailSendDisabled          a switch is off, or both are on and there is
                                 nothing behind them.
      RuntimeError               the provider refused, with its own words.

    So a caller cannot mistake a refusal for a send, and every caller's
    success bookkeeping - a sent flag, a counter, a followup row - belongs
    strictly after this returns.

    Returns the EmailMessage row.
    """
    gate_lead_email(db, lead, send_source=send_source, actor_user_id=actor_user_id)
    # Unreachable while either switch is off, which is every deployment today.
    from app.services.email_service import send_email_to_lead
    return send_email_to_lead(
        db, advisor, lead,
        subject=subject,
        body_html=body_html,
        send_source=send_source,
        sent_by_user_id=actor_user_id,
        raise_on_provider_failure=True,
    )


def send_staff_email(db, advisor, recipient_email, subject, body_html, *, purpose):
    """The internal alert. Not lead contact, and gated differently.

    No compliance preflight and no organization switch: both answer questions
    about a family's consent and a customer's entitlement, and the recipient
    here is our own advisor. The guards that do belong are that there is
    somebody to notify and that this deployment has the staff switch on.

    Uses `_send_email_resend`, which is what the working sibling
    ai_conversation_service._escalate_conversation already uses for exactly
    this job. It writes no `email_messages` row, correctly - that table is
    lead communication history and an advisor alert is not part of it.
    """
    gate_staff_email(recipient_email, purpose=purpose)
    from app.services.ai_conversation_service import _send_email_resend
    return _send_email_resend(db, advisor, recipient_email, subject, body_html)


def gate_transactional_email(recipient_email, *, purpose, source):
    """The guards for a transactional email on a NAMED gated source, then refuse.

    Identical in shape to `gate_staff_email` below - which now delegates here -
    except that the caller states which switch governs it instead of inheriting
    one. That is the whole point: a path that does not name its own source ends
    up sharing somebody else's, and then the two can only ever be turned on
    together.

    No compliance preflight, for the same reason `gate_staff_email` has none:
    that function answers questions about a LEAD's consent - DNC, allow_email,
    capacity hold - and none of the recipients here is a lead. A brand-sales
    prospect who filled in a booking form on a marketing site and asked for a
    meeting is not a customer's family member being marketed to, and running a
    customer-tenant consent check against them would be a category error with no
    Lead row to run it against.

    Raises ValueError when there is nobody to send to, and EmailSendDisabled
    when the deployment has not enabled this specific source.
    """
    if not recipient_email:
        raise ValueError(
            f"No recipient address for {purpose}; nothing was sent."
        )
    if not source_enabled(source):
        logger.info("transactional email gate PASSED but source is disabled: "
                    "purpose=%s source=%s", purpose, source)
        raise EmailSendDisabled(_disabled_reason(source))
    return None


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
    # Delegates, so there is one implementation of "check the switch and refuse"
    # rather than two that can drift. The MESSAGE for a missing recipient stays
    # the one this function has always raised, because callers and tests read it.
    return gate_transactional_email(recipient_email, purpose=purpose,
                                    source=STAFF_ESCALATION)
