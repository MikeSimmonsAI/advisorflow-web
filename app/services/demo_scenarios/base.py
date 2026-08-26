"""What a demo scenario is, and the rules every one of them obeys.

A SCENARIO IS A SEED PLUS A RUNNING ORDER. `seed()` builds the starting world.
`steps` is the ordered list of things the operator can make happen, each with a
label they can read off a phone mid-presentation and a handler that performs it
against the real product services.

THE THREE RULES
---------------
1. DETERMINISTIC IDS. Every record a scenario creates carries an id beginning
   `demo-`. Reset deletes by that prefix, so "remove the demo data" is a
   precise operation with a provable extent rather than a hopeful sweep. It is
   also what makes re-seeding idempotent: the same scenario seeded twice
   produces the same ids, so a duplicate is a primary-key collision rather
   than a second silent copy.

2. REAL TABLES, REAL SERVICES. A scenario writes the tables the product
   already reads. It may not invent a parallel model to make a screen easier
   to populate, and it may not hardcode a number into a React component. If
   the manager dashboard shows "3 need attention", three records must
   genuinely need attention.

3. THE TWO TREES STAY APART. A `customer` scenario touches Organization,
   Lead, Message, Reply, VoiceCall, BookingLink. A `brand` scenario touches
   BrandSalesOrg, Opportunity, Proposal, SalesAppointment, PortalEvent.
   Nothing here creates a foreign key between a Lead and an Opportunity to
   make a story flow more neatly, because no such relationship exists in the
   product and inventing one for a demo would be demonstrating a system that
   does not ship.

TIME IS SEEDED, NOT SIMULATED
-----------------------------
There is no demo clock. Scenarios place their history at offsets from
`now` - "the first touch went out six days ago" is a timestamp six days in the
past, not a fake clock wound forward. Advancing a step writes new records at
the real current time.

That choice is deliberate and it is the simpler safe architecture the brief
asked me to weigh. A demo clock would have to be threaded through every
service that calls `datetime.utcnow()` - dozens of call sites - and any one
that missed it would produce a record inconsistent with the rest, mid-demo,
with no error. Seeded offsets need no plumbing, cannot leak into production
code, and are trivially deterministic on reset. The cost is that "advance one
day" is not available; the operator advances the STORY instead, which is what
they actually narrate.
"""

from datetime import datetime
from typing import Callable, List, Optional

from app.models.demo_models import DEMO_ID_PREFIX

DOMAIN_CUSTOMER = "customer"
DOMAIN_BRAND = "brand"


def demo_id(*parts) -> str:
    """The only way a scenario should mint an id.

    Deterministic and prefixed, so the same seed produces the same ids every
    time and reset can find all of them.
    """
    tail = "-".join(str(p).strip().lower().replace(" ", "-")
                    for p in parts if p is not None)
    return "%s%s" % (DEMO_ID_PREFIX, tail)


class Step:
    """One thing an operator can make happen, mid-presentation.

    `label` is read aloud or off a phone. `narration` is the sentence that
    tells the operator what to point at once it has run - the difference
    between a control panel and a teleprompter.
    """

    def __init__(self, key: str, label: str, narration: str,
                 handler: Callable, provider: Optional[str] = None):
        self.key = key
        self.label = label
        self.narration = narration
        self.handler = handler
        # Which simulated provider this step exercises, for the audit trail.
        self.provider = provider

    def to_dict(self, index: int, done: bool) -> dict:
        return {
            "index": index,
            "key": self.key,
            "label": self.label,
            "narration": self.narration,
            "provider": self.provider,
            "done": done,
        }


class Scenario:
    """Subclass this. Register it in `demo_scenarios/__init__.py`."""

    key = ""
    name = ""
    domain = ""
    # One line the operator sees when choosing what to demonstrate.
    summary = ""
    # The vertical this pack portrays. The ENGINE is industry-neutral; this
    # string only affects the names, wording and appointment labels the seed
    # writes. A roofing pack is a new subclass, not a change to any service.
    industry = "general"

    def steps(self) -> List[Step]:
        raise NotImplementedError

    def seed(self, db, now: datetime) -> dict:
        """Build the starting world. Reset runs first, so seed may assume a
        clean slate."""
        raise NotImplementedError

    def sid(self, *parts) -> str:
        """A scenario-scoped demo id: demo-<scenario key>-<parts>."""
        return demo_id(self.key, *parts)

    def describe(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "domain": self.domain,
            "summary": self.summary,
            "industry": self.industry,
            "total_steps": len(self.steps()),
        }


# ── safe synthetic contact details ──────────────────────────────────────────
#
# Every phone number a scenario writes comes from the 555-01xx block, which is
# reserved for fiction and cannot connect to a real person. Every email address
# uses a reserved example domain (RFC 2606), undeliverable by design.
#
# This matters EVEN WITH THE FIREWALL INSTALLED. The firewall stops this
# process from sending; it does nothing about a number that gets exported to a
# spreadsheet, pasted into a real console, or read aloud in a room. A demo
# record must be harmless on its own, not merely harmless in context.

def demo_phone(n) -> str:
    """+1 555 01NN - reserved for fictional use, cannot ring anybody."""
    return "+1555010%04d" % (int(n) % 10000)


def demo_email(local: str, domain: str = "example.com") -> str:
    return "%s@%s" % (local.strip().lower().replace(" ", "."), domain)


SAFE_EMAIL_SUFFIXES = ("example.com", "example.test", "example.org", "example.net")


def assert_safe_contact(phone: Optional[str], email: Optional[str]) -> None:
    """Belt and braces, asserted by the seeders' own tests.

    A scenario that ever grew a real-looking number should fail loudly at test
    time rather than at the moment somebody dials it.
    """
    if phone:
        digits = "".join(c for c in phone if c.isdigit())
        if not digits.startswith("1555010"):
            raise ValueError("Demo phone %r is not in the reserved 555-01xx "
                             "range." % phone)
    if email:
        low = email.lower().strip()
        if not any(low.endswith(s) for s in SAFE_EMAIL_SUFFIXES):
            raise ValueError("Demo email %r is not a reserved example domain."
                             % email)
