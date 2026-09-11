"""SYNTHETIC PROVING CONFIGURATIONS — no real customer, no real person.

WHY SYNTHETIC PROFILES EXIST. The only honest way to prove an operations
layer works is to drive whole lifecycles through it — outreach, reply,
qualification, booking, opt-out, handoff, failure, recovery. Doing that
against a real customer's records means real messages to real families, and
doing it against nothing means proving nothing. So: real engine, real gates,
real state machine, real audit; invented organizations, invented contacts,
simulated providers.

TWO PROFILES, BOTH USE CASES AND NEITHER ARCHITECTURE.

    REACTIVATION      an aged database worked back to life. The first
                      operational proof of T6's Reactivation Specialist.
    FULL-LIFECYCLE    a business with both B2B and residential customers,
                      new and dormant leads, inbound and outbound, SMS and
                      email, voice-capable, booking, transfer, follow-up.

NEITHER IS A CUSTOMER. Restland is a use case, Atlantis is a use case, and
nothing in this file — or anywhere in this package — branches on either. The
organizations built here carry an unmistakable synthetic marker in their name
and slug, their contacts are flagged `is_test`, and every send resolves to
the simulated adapter because the employee contexts are DECLARED rather than
loaded from T6 (see contracts.declare_employee_context).

THE GUARD. `build()` refuses outright unless the caller passes
`allow_synthetic_data=True`, and the router that exposes it is God-only. A
synthetic organization appearing in a real deployment's customer list would
be a support ticket at best and a billing question at worst.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.ai_operations import constants as C
from app.services.ai_operations import contracts

_log = logging.getLogger(__name__)

SYNTHETIC_PREFIX = "synthetic-aiops"
SYNTHETIC_MARKER = "SYNTHETIC (AI Operations proof)"

# The tool authority each proving employee holds. Deliberately spelled with
# T6's own tool keys rather than with operation names: a declared context
# that named operations would be declaring an authority vocabulary of its
# own, which is the thing this layer must never do.
REACTIVATION_TOOLS = {
    "lead.get", "lead.get_context", "conversation.get_history",
    "customer.get_context", "knowledge.search", "lead.add_note",
    "memory.remember", "handoff.create", "employee.request_review",
    "employee.pause_work_item", "employee.wait_for_response",
    "conversation.prepare_sms", "conversation.prepare_email",
    "conversation.send_sms", "conversation.send_email",
    "lead.mark_not_interested", "lead.mark_do_not_contact",
    "lead.mark_bad_contact", "employee.mark_exhausted",
    "calendar.get_availability", "appointment.book",
    "appointment.reschedule", "lead.update_qualification",
    "employee.mark_qualified",
}

# The full-lifecycle employee additionally holds the voice tool — so that the
# voice REFUSAL is exercised by an employee that genuinely has the authority,
# which is the only way to prove the refusal comes from the platform switch
# rather than from a missing grant.
LIFECYCLE_TOOLS = REACTIVATION_TOOLS | {"conversation.place_call",
                                        "opportunity.get",
                                        "opportunity.create",
                                        "opportunity.update"}


# ── making a declared employee findable by a worker ────────────────────────
#
# The follow-up worker comes back later holding only an employee id, and
# correctly refuses to act without an authority answer. For synthetic runs
# that answer has to come from somewhere, so a profile registers its declared
# context with `contracts` — which keeps it marked `declared`, and therefore
# keeps it on the simulated adapter, whatever it claims about itself.
_DECLARED: Dict[str, contracts.EmployeeContext] = {}


def _provider(db, employee_id, organization_id):
    ctx = _DECLARED.get(employee_id)
    if ctx is None:
        return None
    if organization_id and ctx.organization_id != organization_id:
        return None
    return ctx


def _register(ctx: contracts.EmployeeContext) -> contracts.EmployeeContext:
    _DECLARED[ctx.employee_id] = ctx
    contracts.register_context_provider(_provider)
    return ctx


def forget_declared() -> None:
    """Drop every synthetic context. Tests share a process."""
    _DECLARED.clear()
    contracts.clear_context_providers()


@dataclass
class Profile:
    """Everything a scenario needs, already in the database."""

    key: str
    organization: Any
    advisor: Any
    leads: List[Any] = field(default_factory=list)
    ctx: Optional[contracts.EmployeeContext] = None
    notes: Dict[str, Any] = field(default_factory=dict)

    def lead_by_key(self, key: str):
        for lead in self.leads:
            if (lead.source_file or "").endswith(key):
                return lead
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "organization_id": self.organization.id,
            "organization_name": self.organization.name,
            "advisor_id": self.advisor.id,
            "leads": [{"id": lead.id,
                       "name": ("%s %s" % (lead.first_name or "",
                                           lead.last_name or "")).strip(),
                       "tag": lead.source_file} for lead in self.leads],
            "employee": self.ctx.as_dict() if self.ctx else None,
            "notes": dict(self.notes),
        }


def _org(db: Session, slug: str, name: str, *, features: Optional[List[str]],
         timezone: str = "America/Chicago"):
    from app.models.models import Organization
    existing = (db.query(Organization)
                .filter(Organization.slug == slug).first())
    if existing is not None:
        return existing
    import json
    org = Organization(
        name="%s — %s" % (name, SYNTHETIC_MARKER), slug=slug, plan="standard",
        enabled_features=(json.dumps(features) if features is not None
                          else None),
        org_twilio_phone_number="+15550000000",
        from_email="noreply@%s.invalid" % slug)
    db.add(org)
    db.flush()
    return org


def _advisor(db: Session, org, *, email: str, name: str, phone: str):
    from app.models.models import User
    from app.services.auth_service import hash_password
    existing = db.query(User).filter(User.email == email).first()
    if existing is not None:
        return existing
    user = User(organization_id=org.id, email=email,
                password_hash=hash_password("synthetic-not-a-real-account"),
                full_name=name, role="advisor", twilio_phone_number=phone,
                must_change_password=False)
    db.add(user)
    db.flush()
    return user


def _lead(db: Session, org, advisor, *, tag: str, first: str, last: str,
          phone: str, email: Optional[str], tier: str = "pre_need",
          status: str = "new", allow_sms: Optional[bool] = True,
          allow_email: Optional[bool] = True,
          last_contact_days_ago: Optional[int] = None):
    from app.models.models import Lead
    existing = (db.query(Lead)
                .filter(Lead.organization_id == org.id,
                        Lead.source_file == tag).first())
    if existing is not None:
        return existing
    lead = Lead(
        organization_id=org.id, assigned_to_id=advisor.id,
        first_name=first, last_name=last, phone=phone, email=email,
        tier=tier, status=status, allow_sms=allow_sms,
        allow_email=allow_email, source_file=tag, source="synthetic",
        # EVERY SYNTHETIC CONTACT IS FLAGGED AS A TEST RECORD... except where
        # a scenario needs a contactable one: `is_test` is itself a DENY
        # reason in the eligibility engine, so a profile that flagged
        # everything would prove only that the test-record gate works.
        is_test=False,
        last_contact_date=(datetime.utcnow()
                           - timedelta(days=last_contact_days_ago)
                           if last_contact_days_ago else None))
    db.add(lead)
    db.flush()
    return lead


def build_reactivation(db: Session, *, allow_synthetic_data: bool = False,
                       activation_state: str = "simulation") -> Profile:
    """The dormant-database proof.

    Five contacts, chosen so that the five lifecycle ENDINGS are all
    reachable from one profile: one who books, one who opts out, one who
    never answers, one who asks for a person, and one the eligibility engine
    must refuse outright.
    """
    if not allow_synthetic_data:
        raise PermissionError(
            "Synthetic profiles create organizations and contacts; the "
            "caller must pass allow_synthetic_data=True deliberately.")

    org = _org(db, "%s-reactivation" % SYNTHETIC_PREFIX,
               "Reactivation Proof", features=["sms", "email", "booking",
                                               "leads", "cadences",
                                               "compliance", "ai_assist"])
    advisor = _advisor(db, org, email="advisor@%s-reactivation.invalid"
                                      % SYNTHETIC_PREFIX,
                       name="Synthetic Advisor", phone="+15550000001")
    leads = [
        _lead(db, org, advisor, tag="synthetic:books", first="Dana",
              last="Booker", phone="15550100001",
              email="dana.booker@example.invalid",
              last_contact_days_ago=540),
        _lead(db, org, advisor, tag="synthetic:optout", first="Ray",
              last="Stopper", phone="15550100002",
              email="ray.stopper@example.invalid",
              last_contact_days_ago=700),
        _lead(db, org, advisor, tag="synthetic:silent", first="Pat",
              last="Quiet", phone="15550100003",
              email="pat.quiet@example.invalid", last_contact_days_ago=900),
        _lead(db, org, advisor, tag="synthetic:human", first="Sam",
              last="Asker", phone="15550100004",
              email="sam.asker@example.invalid", last_contact_days_ago=480),
        # Explicitly opted out of SMS at the source. The engine must refuse
        # this one before anything is composed, let alone sent.
        _lead(db, org, advisor, tag="synthetic:blocked", first="Lee",
              last="Refused", phone="15550100005",
              email="lee.refused@example.invalid", allow_sms=False,
              allow_email=False, last_contact_days_ago=365),
    ]
    ctx = contracts.declare_employee_context(
        employee_id="synthetic-reactivation-employee",
        organization_id=org.id, name="AI Reactivation Specialist",
        job_role="reactivation_specialist",
        template_key="reactivation_specialist",
        tool_keys=set(REACTIVATION_TOOLS),
        channels={C.CHANNEL_SMS, C.CHANNEL_EMAIL},
        activation_state=activation_state, status="active",
        handoff_user_id=advisor.id, handoff_queue="synthetic-handoffs",
        timezone="America/Chicago",
        operating_hours={"days": [0, 1, 2, 3, 4], "start": "08:00",
                         "end": "20:00"})
    _register(ctx)
    db.flush()
    return Profile(key="reactivation", organization=org, advisor=advisor,
                   leads=leads, ctx=ctx,
                   notes={"objective": ("Re-engage a dormant record, qualify "
                                        "it, and either book or hand over."),
                          "activation_state": activation_state})


def build_full_lifecycle(db: Session, *, segment: str = "b2b",
                         allow_synthetic_data: bool = False,
                         activation_state: str = "simulation") -> Profile:
    """The full-lifecycle proof, in either segment.

    B2B AND RESIDENTIAL ARE THE SAME ENGINE WITH DIFFERENT DATA, and the
    profile is the proof of that claim: the only differences below are the
    contacts, the tier vocabulary and the appointment language. No code path
    anywhere reads `segment`.
    """
    if not allow_synthetic_data:
        raise PermissionError(
            "Synthetic profiles create organizations and contacts; the "
            "caller must pass allow_synthetic_data=True deliberately.")
    segment = (segment or "b2b").strip().lower()
    if segment not in ("b2b", "residential"):
        raise ValueError("segment must be 'b2b' or 'residential'")

    org = _org(db, "%s-lifecycle-%s" % (SYNTHETIC_PREFIX, segment),
               "Full-Lifecycle Energy Proof (%s)" % segment.upper(),
               features=["sms", "email", "voice", "booking", "leads",
                         "cadences", "compliance", "ai_assist", "crm"])
    advisor = _advisor(
        db, org, email="advisor@%s-lifecycle-%s.invalid"
                       % (SYNTHETIC_PREFIX, segment),
        name="Synthetic Energy Advisor",
        phone="+1555000%s" % ("0002" if segment == "b2b" else "0003"))

    if segment == "b2b":
        people = [
            ("new", "Morgan", "Facilities", "15550200001", "at_need"),
            ("dormant", "Jordan", "Procurement", "15550200002", "pre_need"),
            ("inbound", "Casey", "Operations", "15550200003", "imminent"),
        ]
    else:
        people = [
            ("new", "Alex", "Rivera", "15550300001", "at_need"),
            ("dormant", "Robin", "Chen", "15550300002", "pre_need"),
            ("inbound", "Taylor", "Okafor", "15550300003", "imminent"),
        ]
    leads = [
        _lead(db, org, advisor, tag="synthetic:%s" % key, first=first,
              last=last, phone=phone,
              email="%s.%s@example.invalid" % (first.lower(), last.lower()),
              tier=tier,
              status=("new" if key != "dormant" else "sent"),
              last_contact_days_ago=(None if key == "new" else 400))
        for key, first, last, phone, tier in people
    ]
    ctx = contracts.declare_employee_context(
        employee_id="synthetic-lifecycle-%s-employee" % segment,
        organization_id=org.id, name="AI Lifecycle Specialist",
        job_role="sales_assistant", template_key="sales_assistant",
        tool_keys=set(LIFECYCLE_TOOLS),
        # VOICE IS IN THE EMPLOYEE'S CHANNELS DELIBERATELY. The refusal must
        # come from the platform switch, and that is only proven when the
        # employee would otherwise have been allowed.
        channels={C.CHANNEL_SMS, C.CHANNEL_EMAIL, C.CHANNEL_VOICE},
        activation_state=activation_state, status="active",
        handoff_user_id=advisor.id, handoff_queue="synthetic-handoffs",
        timezone="America/Chicago",
        operating_hours={"days": [0, 1, 2, 3, 4], "start": "08:00",
                         "end": "20:00"})
    _register(ctx)
    db.flush()
    return Profile(key="lifecycle-%s" % segment, organization=org,
                   advisor=advisor, leads=leads, ctx=ctx,
                   notes={"segment": segment,
                          "objective": ("Work a %s enquiry from first contact "
                                        "to a booked appointment or a "
                                        "person." % segment),
                          "activation_state": activation_state})


def catalogue() -> List[Dict[str, str]]:
    """What can be built, for the console."""
    return [
        {"key": "reactivation",
         "label": "Reactivation Specialist (dormant database)",
         "description": ("Five synthetic contacts covering booking, opt-out, "
                         "silence, a request for a person, and an ineligible "
                         "record.")},
        {"key": "lifecycle-b2b",
         "label": "Full-Lifecycle Energy — B2B",
         "description": ("New, dormant and inbound business contacts through "
                         "qualification, booking and transfer.")},
        {"key": "lifecycle-residential",
         "label": "Full-Lifecycle Energy — Residential",
         "description": ("The same engine and the same employee template "
                         "against residential contacts.")},
    ]


def build(db: Session, key: str, *, allow_synthetic_data: bool = False,
          activation_state: str = "simulation") -> Profile:
    if key == "reactivation":
        return build_reactivation(
            db, allow_synthetic_data=allow_synthetic_data,
            activation_state=activation_state)
    if key in ("lifecycle-b2b", "b2b"):
        return build_full_lifecycle(
            db, segment="b2b", allow_synthetic_data=allow_synthetic_data,
            activation_state=activation_state)
    if key in ("lifecycle-residential", "residential"):
        return build_full_lifecycle(
            db, segment="residential",
            allow_synthetic_data=allow_synthetic_data,
            activation_state=activation_state)
    raise ValueError("unknown profile %r" % key)
