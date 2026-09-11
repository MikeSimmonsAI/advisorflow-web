"""CHANNEL ADAPTERS — the only code in this package that can reach a person.

TWO IMPLEMENTATIONS PER CHANNEL, AND THE SAFE ONE IS THE DEFAULT NOWHERE.

    Real*       calls the platform's own send path. Nothing here re-implements
                SMS or email; `sms_service` and `email_service` already own
                credentials, sender resolution, demo guards, delivery status
                and the message rows. A second sender is a second place for a
                consent check to be missing.

    Simulated*  records what WOULD have been sent and returns a well-formed
                result with a synthetic id. Used by the simulator and the
                evaluation harness.

THE ADAPTER IS NOT THE SAFETY MECHANISM. `tools.authorize` refuses any tool
with `reaches_outside=True` unless the resolved activation stage is CONTROLLED
or ACTIVE — so in simulation and shadow the real adapter is never reached, no
matter which one is installed. The adapters exist so the simulator can exercise
the send PATH, not so that swapping one in is what keeps a message from going
out. Belt and braces: the real adapters ALSO re-check activation themselves and
refuse, because a defence that appears in one place appears in zero places the
day somebody adds a caller.

VOICE HAS AN ADAPTER AND NO LIVE PATH. `RealVoiceAdapter.place_call` raises
unconditionally. The interface, the disposition shape and the eligibility path
are all real and exercised; the call is not placed. When live voice is
eventually enabled, this is the one method that changes.
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Lead, User
from app.models.workforce_models import AIEmployee
from app.services.workforce import constants as C

_log = logging.getLogger(__name__)


class OutboundRefused(RuntimeError):
    """An adapter refusing to act. Surfaced to the employee as a tool refusal."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _assert_may_execute(db: Session, employee: AIEmployee, what: str) -> None:
    """The adapter's own check, independent of the gateway's.

    Deliberately duplicated. `tools.authorize` has already refused this in
    every stage below CONTROLLED; this exists so that a future caller which
    reaches an adapter directly — a script, a cron, a well-meant helper — still
    cannot send.
    """
    from app.services.workforce import activation
    resolved = activation.resolve(db, employee=employee)
    if not resolved.may_execute:
        raise OutboundRefused(
            C.DENY_ACTIVATION_STAGE,
            "%s refused: the effective activation stage is '%s'."
            % (what, resolved.state))


def _resolve_sender(db: Session, employee: AIEmployee,
                    lead: Lead) -> Optional[User]:
    """WHOSE identity does the AI employee send under?

    An AI employee is not a user and has no Twilio number of its own. It sends
    under the record's ASSIGNED advisor, falling back to the person who created
    the employee. That is the honest answer — the message comes from the
    business, through the person responsible for that record — and it is also
    the only one that works: sender resolution, consent and A2P registration
    all hang off a real user's organization.
    """
    user = None
    if getattr(lead, "assigned_to_id", None):
        user = db.query(User).filter(User.id == lead.assigned_to_id).first()
    if user is None and getattr(employee, "created_by", None):
        user = db.query(User).filter(User.id == employee.created_by).first()
    if user is not None and str(user.organization_id) != str(employee.organization_id):
        # A sender from another tenant is not a fallback, it is a leak.
        return None
    return user


# ── SMS ─────────────────────────────────────────────────────────────────────

class SimulatedSmsAdapter:
    channel = C.CHANNEL_SMS
    is_simulated = True

    def __init__(self):
        self.sent: List[Dict] = []

    def send(self, db: Session, employee: AIEmployee, lead: Lead,
             body: str) -> Dict:
        rec = {"channel": C.CHANNEL_SMS, "lead_id": lead.id,
               "to": getattr(lead, "phone", None), "chars": len(body or ""),
               "at": datetime.utcnow().isoformat(), "simulated": True}
        self.sent.append(rec)
        return {"message_id": "sim-sms-%d" % len(self.sent),
                "provider_status": "simulated", "simulated": True,
                "chars": len(body or "")}


class RealSmsAdapter:
    channel = C.CHANNEL_SMS
    is_simulated = False

    def send(self, db: Session, employee: AIEmployee, lead: Lead,
             body: str) -> Dict:
        _assert_may_execute(db, employee, "SMS send")
        sender = _resolve_sender(db, employee, lead)
        if sender is None:
            raise OutboundRefused(
                C.DENY_BUSINESS_RULE,
                "No sender could be resolved for this record, so no message "
                "was sent.")
        from app.services import sms_service
        # THE EMPLOYEE NEVER INVENTS A URL.
        #
        # `compose_body` substitutes {booking_link} with the platform's own
        # signed booking URL for THIS lead and advisor. An employee that wants
        # to offer a booking link writes the placeholder; the platform mints
        # the link. A model that typed a URL of its own would be typing a URL
        # nobody can honour, and `include_booking_link` is set from the
        # placeholder rather than from a flag the model controls.
        wants_link = sms_service.BOOKING_LINK_PLACEHOLDER in (body or "")
        msg = sms_service.send_sms(db=db, advisor=sender, lead=lead,
                                   template=body,
                                   include_booking_link=wants_link)
        return {"message_id": getattr(msg, "id", None),
                "provider_status": getattr(msg, "delivery_status", None),
                "simulated": False}


# ── EMAIL ───────────────────────────────────────────────────────────────────

class SimulatedEmailAdapter:
    channel = C.CHANNEL_EMAIL
    is_simulated = True

    def __init__(self):
        self.sent: List[Dict] = []

    def send(self, db: Session, employee: AIEmployee, lead: Lead,
             subject: str, body: str) -> Dict:
        rec = {"channel": C.CHANNEL_EMAIL, "lead_id": lead.id,
               "to": getattr(lead, "email", None),
               "subject_chars": len(subject or ""), "chars": len(body or ""),
               "at": datetime.utcnow().isoformat(), "simulated": True}
        self.sent.append(rec)
        return {"message_id": "sim-email-%d" % len(self.sent),
                "provider_status": "simulated", "simulated": True}


class RealEmailAdapter:
    channel = C.CHANNEL_EMAIL
    is_simulated = False

    def send(self, db: Session, employee: AIEmployee, lead: Lead,
             subject: str, body: str) -> Dict:
        _assert_may_execute(db, employee, "Email send")
        sender = _resolve_sender(db, employee, lead)
        if sender is None:
            raise OutboundRefused(
                C.DENY_BUSINESS_RULE,
                "No sender could be resolved for this record, so no email was "
                "sent.")
        # THE COMPLIANCE PREFLIGHT, AGAIN. `send_email_to_lead` runs it and
        # takes no custom body; the generic sender takes a body and does not.
        # Rather than pick one and lose the other, the preflight is called
        # explicitly here — from the service that owns it — so a custom-bodied
        # AI email is checked by exactly the same gate a templated one is.
        from app.services import compliance_service
        compliance_service.check_compliance_preflight(
            db, lead, channel=compliance_service.CHANNEL_EMAIL)

        from app.models.models import EmailMessage
        from app.services import email_service
        to_email = (getattr(lead, "email", None) or "").strip()
        if not to_email:
            raise OutboundRefused(C.DENY_BUSINESS_RULE,
                                  "This record has no email address.")
        result = email_service.send_email(
            db=db, org_id=lead.organization_id, to_email=to_email,
            to_name=("%s %s" % (lead.first_name or "", lead.last_name or "")).strip(),
            subject=subject, body=body)
        if not (result or {}).get("success", True):
            raise OutboundRefused(
                C.DENY_BUSINESS_RULE,
                "The email provider refused the message; nothing was sent.")
        # The conversation timeline is part of the shared customer record
        # (section 16), so an AI-sent email has to appear in it exactly as a
        # human-sent one does.
        row = EmailMessage(lead_id=lead.id, sender_id=sender.id,
                           subject=subject, body_html=body, status="sent",
                           provider_message_id=(result or {}).get(
                               "provider_message_id"))
        db.add(row)
        lead.last_messaged_at = datetime.utcnow()
        db.flush()
        return {"message_id": row.id, "provider_status": row.status,
                "simulated": False}


# ── VOICE ───────────────────────────────────────────────────────────────────

class SimulatedVoiceAdapter:
    channel = C.CHANNEL_VOICE
    is_simulated = True

    def __init__(self):
        self.calls: List[Dict] = []

    def place_call(self, db: Session, employee: AIEmployee, lead: Lead,
                   purpose: str = "") -> Dict:
        rec = {"channel": C.CHANNEL_VOICE, "lead_id": lead.id,
               "to": getattr(lead, "phone", None), "purpose": purpose,
               "at": datetime.utcnow().isoformat(), "simulated": True}
        self.calls.append(rec)
        # The disposition shape a real provider returns, so the rest of the
        # engine is exercised against the real contract.
        return {"call_id": "sim-call-%d" % len(self.calls),
                "answered_by": "voicemail", "is_live_conversation": False,
                "disposition": "no_answer", "duration_seconds": 0,
                "simulated": True}


class RealVoiceAdapter:
    """NO LIVE CALL IS PLACED BY THIS BUILD.

    Present so the architecture is complete and so the day live voice is
    enabled there is one method to implement against a provider that the
    provider-neutral registry already resolves. Until then it refuses, and
    `tools.authorize` has already refused before anything reaches it.
    """

    channel = C.CHANNEL_VOICE
    is_simulated = False

    def place_call(self, db: Session, employee: AIEmployee, lead: Lead,
                   purpose: str = "") -> Dict:
        raise OutboundRefused(
            C.DENY_LIVE_VOICE_DISABLED,
            "Live AI voice is disabled in this deployment. No call was placed.")


# ── THE ADAPTER REGISTRY ────────────────────────────────────────────────────
#
# Default: the real adapters, because the safe behaviour must not depend on
# which adapter is installed — the gateway refuses the call long before the
# adapter matters, and a default of "simulated" would make the engine look safe
# for the wrong reason.

_ADAPTERS = {
    C.CHANNEL_SMS: RealSmsAdapter(),
    C.CHANNEL_EMAIL: RealEmailAdapter(),
    C.CHANNEL_VOICE: RealVoiceAdapter(),
}


def adapter(channel: str):
    a = _ADAPTERS.get((channel or "").lower())
    if a is None:
        raise OutboundRefused(C.DENY_BAD_ARGUMENTS,
                              "No adapter for channel %r." % channel)
    return a


def is_fully_simulated() -> bool:
    """Is EVERY channel currently served by a simulated adapter?

    ALL OR NOTHING, and the "all" is what makes it safe to rely on. The
    gateway uses this to permit a reach-shaped tool inside the SIMULATION
    stage — the simulator has to be able to exercise the real send PATH, or it
    is testing a different program from the one that ships (section 37).

    Requiring every adapter to be simulated is the guard against the obvious
    mistake: a test that installs a fake SMS adapter and leaves email real
    would otherwise be granted permission to "simulate" an email that actually
    goes out. One channel short and this is False, and the gateway refuses.

    It is also not the only protection. The real adapters re-check the
    activation stage themselves and refuse below CONTROLLED, so even a
    mis-answer here cannot produce a real send.
    """
    if set(_ADAPTERS) != set(C.ALL_CHANNELS):
        return False
    return all(getattr(a, "is_simulated", False) for a in _ADAPTERS.values())


def adapter_report() -> Dict[str, str]:
    """Which adapter is installed per channel. Rendered on the God screen."""
    return {ch: ("simulated" if getattr(a, "is_simulated", False) else "live")
            for ch, a in sorted(_ADAPTERS.items())}


class use_simulated_adapters:
    """Context manager installing the simulated adapters.

    Used by the simulator and the evaluation harness. Restores whatever was
    installed before on the way out, including on an exception, so a test that
    fails cannot leave the process sending simulated messages for every
    subsequent test — or, worse, leave a real adapter swapped out in a
    long-lived process.
    """

    def __init__(self):
        self._previous = None
        self.sms = SimulatedSmsAdapter()
        self.email = SimulatedEmailAdapter()
        self.voice = SimulatedVoiceAdapter()

    def __enter__(self):
        self._previous = dict(_ADAPTERS)
        _ADAPTERS[C.CHANNEL_SMS] = self.sms
        _ADAPTERS[C.CHANNEL_EMAIL] = self.email
        _ADAPTERS[C.CHANNEL_VOICE] = self.voice
        return self

    def __exit__(self, *exc):
        _ADAPTERS.clear()
        _ADAPTERS.update(self._previous or {})
        return False

    @property
    def all_sends(self) -> List[Dict]:
        return list(self.sms.sent) + list(self.email.sent)
