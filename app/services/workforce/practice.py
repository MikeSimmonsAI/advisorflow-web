"""PRACTICE RECORDS - the fictional people a customer's AI worker is tested on.

WHAT A PRACTICE RECORD IS. A Lead row created by the "Run test" button (or the
acceptance script) so an AI employee can be exercised on the real worker path
without touching anybody real. It has:

    is_test = True               so every CRM list, count, report and bulk
                                 outreach query that already excludes test
                                 records (test_records.exclude_test_records)
                                 keeps excluding it. A practice record never
                                 shows up in the customer's own pipeline.
    source = PRACTICE_SOURCE     so the workforce can tell it apart from a QA
                                 row somebody imported by hand.
    a reserved fictional phone   555-01xx, which no carrier routes,
    an .invalid email            which no resolver answers.

THE ONE EXCEPTION THIS MODULE GRANTS, AND WHY IT IS SAFE. The platform's
qualification gate excludes every test record ("internal_record"), and for a
real send that is exactly right. But the gate also runs in front of the
SIMULATION stage, where the engine talks only to simulated adapters - and there
it would make every practice run end at "needs review" before the employee did
any work, which is a test that tests nothing.

So `simulation_practice_allowed` says yes for exactly one combination:

    a PRACTICE record (is_test + PRACTICE_SOURCE + fictional contact)
    AND the employee's resolved stage is SIMULATION, not killed.

Any stage that can reach a real person (controlled, active) resolves above
simulation and gets the ordinary answer - excluded. The send gateway
(`tools.authorize` / `outbound._assert_may_execute`) independently refuses
every outward action outside EXECUTING_STAGES, so even a mistake here could not
produce a real message: two separate locks, and this file only opens the first
one, only for fiction, only in simulation.
"""

import itertools
import random
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import Lead
from app.services.workforce import constants as C

PRACTICE_SOURCE = "ai_workforce_practice"

# The reserved fiction markers. The phone block is the one profiles.safe_phone
# already uses (+1 214 555-0100..0199, which the numbering plan reserves for
# fiction); the email domain is RFC 2606's .invalid, which never resolves.
_FICTION_PHONE_DIGITS = "121455501"
_FICTION_EMAIL_DOMAIN = ".invalid"

_FIRST = ["Alex", "Bailey", "Casey", "Devon", "Emery", "Jordan", "Morgan",
          "Parker", "Quinn", "Reese"]
_LAST = ["Practice", "Sample", "Example", "Testwell", "Demo"]

_SEQ = itertools.count(random.randint(1, 10_000))


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def is_fictional_contact(lead) -> bool:
    phone = _digits(getattr(lead, "phone", ""))
    email = (getattr(lead, "email", "") or "").strip().lower()
    phone_ok = (not phone) or (phone.startswith(_FICTION_PHONE_DIGITS)
                               and len(phone) == 11) \
        or (phone.startswith(_FICTION_PHONE_DIGITS[1:]) and len(phone) == 10)
    email_ok = (not email) or email.endswith(_FICTION_EMAIL_DOMAIN)
    return phone_ok and email_ok and bool(phone or email)


def is_practice_record(lead) -> bool:
    """A record this module created for testing. All three marks required."""
    if lead is None:
        return False
    return (bool(getattr(lead, "is_test", False))
            and (getattr(lead, "source", None) or "") == PRACTICE_SOURCE
            and is_fictional_contact(lead))


def simulation_practice_allowed(db: Session, employee, lead) -> bool:
    """May the eligibility gate treat this practice record as a prospect?

    Only in the SIMULATION stage, which reaches nobody. See the module note.
    """
    if not is_practice_record(lead):
        return False
    from app.services.workforce import activation as wf_activation
    resolved = wf_activation.resolve(db, employee=employee)
    return (not resolved.killed) and resolved.state == C.SIMULATION


def simulation_refuses_real_record(db: Session, employee, lead) -> bool:
    """Is this a REAL person in a REAL account while the stage is SIMULATION?

    Test mode is something a customer administrator can switch on. It must
    not become a way to run AI over their actual customer list, even with
    simulated adapters: work records, handoffs and notes would be written
    about real people nobody agreed to put in front of an AI. So in an account
    that is not a demonstration account, SIMULATION works practice records
    only. Demonstration accounts (is_demo) keep the old behaviour; every
    synthetic proof and simulator scenario lives in one.
    """
    if lead is None or is_practice_record(lead):
        return False
    from app.models.models import Organization
    from app.services.workforce import activation as wf_activation
    resolved = wf_activation.resolve(db, employee=employee)
    if resolved.state != C.SIMULATION:
        return False
    org = (db.query(Organization)
           .filter(Organization.id == employee.organization_id).first())
    return not bool(getattr(org, "is_demo", False))


class PracticeView:
    """The lead as the qualification gate should see it in simulation.

    Reads through to the real row for every attribute except `is_test`, which
    is the single exclusion simulation_practice_allowed has already decided.
    Nothing is written back: the stored row stays is_test=True.
    """

    def __init__(self, lead):
        object.__setattr__(self, "_lead", lead)

    def __getattr__(self, name):
        if name == "is_test":
            return False
        return getattr(object.__getattribute__(self, "_lead"), name)

    def __setattr__(self, name, value):
        raise AttributeError("PracticeView is read-only")


def create_practice_lead(db: Session, *, organization_id: str,
                         assigned_to_id: Optional[str] = None,
                         status: str = "new",
                         notes: Optional[str] = None) -> Lead:
    """One fictional person in this organization, hidden from its CRM."""
    n = next(_SEQ)
    first = _FIRST[n % len(_FIRST)]
    last = _LAST[n % len(_LAST)]
    lead = Lead(
        organization_id=organization_id,
        assigned_to_id=assigned_to_id,
        first_name=first,
        last_name=last,
        phone="+1214555%04d" % (100 + (n % 100)),
        email="%s.%s.%d@example.invalid" % (first.lower(), last.lower(), n),
        status=status,
        source=PRACTICE_SOURCE,
        is_test=True,
        allow_sms=True,
        allow_email=True,
        sms_consent=True,
        notes=notes or "Practice record for an AI worker test. Fictional "
                       "person; cannot be contacted.",
    )
    db.add(lead)
    db.flush()
    return lead
