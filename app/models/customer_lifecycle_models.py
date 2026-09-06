"""WHERE A CUSTOMER IS IN THEIR LIFE WITH US — AND WHY LEAVING IS NOT DELETION.

WHAT WAS HERE BEFORE: NOTHING. A customer organization carried `is_active`, a
boolean, and that was the whole of it. Two very different facts had to share it:

    "suspended for now"      — non-payment, a pause, an investigation
    "gone, and not coming back"

A boolean cannot tell those apart, so neither could anybody reading it. There
was no cancellation date, no reason, no effective date, no archive, and no way
to ask "who left us this quarter, and why".

`is_active` KEEPS ITS EXISTING MEANING and is deliberately not replaced. It is
the operational switch: can anybody walk into this workspace right now.
`lifecycle_status` is the commercial fact: what is this customer's relationship
with us. They answer different questions and a cancelled customer whose
workspace is still open during a notice period is a real, ordinary state that
only two fields can express.

CANCELLATION IS NOT DELETION, and this module is where that is enforced rather
than merely intended. Every transition below preserves the customer row, the
originating opportunity, the proposal and its version, the pricing snapshot on
the implementation, the compensation already earned, and the audit trail. The
only thing a cancellation changes is the STATUS and the dates that explain it.

TWO PLACES, ON PURPOSE
----------------------
The CURRENT state lives in columns on `organizations`, so the customer list can
filter and sort by status without a join, and so "when did they cancel" is one
read rather than a scan of history.

The HISTORY lives in `customer_lifecycle_events` below, one row per transition,
because a customer who cancelled, came back, and cancelled again has a story
that a single set of columns flattens into a lie. The columns are the latest
chapter; this table is the book.

NOTHING HERE IS BRAND-SPECIFIC. The states are the states of a B2B software
customer, and a white-label brand inherits them by existing. A brand that wants
different labels changes labels; it does not get its own lifecycle.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (Column, DateTime, ForeignKey, Index, String, Text)

from app.models.models import Base, gen_uuid

# ── the canonical states ────────────────────────────────────────────────────
#
# Ordered as a customer actually travels them. There is no "prospect" or
# "opportunity" state here on purpose: before provisioning there is no customer
# organization to hold a status, and the sales pipeline already models those
# stages on the Opportunity. Duplicating them would create a second answer to
# "is this deal won" — the exact failure the Won → Customer crossing was
# designed to avoid.

CUST_ACTIVE = "active"
# A cancellation has been REQUESTED. Service usually continues — a notice
# period is normal, and cutting a customer off the moment somebody clicks
# Cancel would break contracts we are still being paid under.
CUST_CANCELLATION_REQUESTED = "cancellation_requested"
# The work of leaving is underway: exports, final invoices, disconnections.
CUST_OFFBOARDING = "offboarding"
# Gone. The relationship is over and the record is history.
CUST_CANCELLED = "cancelled"
# Gone AND filed away. Archive is a VIEW decision, not a data decision: it
# removes a customer from everyday operational lists while keeping every record
# retrievable to an authorised administrator.
CUST_ARCHIVED = "archived"

CUSTOMER_STATUSES = (
    CUST_ACTIVE, CUST_CANCELLATION_REQUESTED, CUST_OFFBOARDING,
    CUST_CANCELLED, CUST_ARCHIVED,
)

CUSTOMER_STATUS_LABELS = {
    CUST_ACTIVE: "Active",
    CUST_CANCELLATION_REQUESTED: "Cancellation requested",
    CUST_OFFBOARDING: "Offboarding",
    CUST_CANCELLED: "Cancelled",
    CUST_ARCHIVED: "Archived",
}

# Which statuses mean "still a live commercial relationship". Used for the
# default filter on the customer list and for counting customers — a cancelled
# customer must stop being counted as one without ceasing to exist.
CUSTOMER_OPEN_STATUSES = (CUST_ACTIVE, CUST_CANCELLATION_REQUESTED,
                          CUST_OFFBOARDING)

# WHAT MAY FOLLOW WHAT. Enforced server-side so a screen cannot walk a customer
# into a state that makes no sense — archiving somebody who never cancelled, or
# "completing" a cancellation nobody requested.
#
# Reactivation is deliberately permitted from every terminal state. Customers do
# come back, and the alternative is creating a second organization for the same
# business, which orphans their history from their present.
CUSTOMER_TRANSITIONS = {
    CUST_ACTIVE: (CUST_CANCELLATION_REQUESTED, CUST_ARCHIVED),
    CUST_CANCELLATION_REQUESTED: (CUST_OFFBOARDING, CUST_CANCELLED, CUST_ACTIVE),
    CUST_OFFBOARDING: (CUST_CANCELLED, CUST_ACTIVE),
    CUST_CANCELLED: (CUST_ARCHIVED, CUST_ACTIVE),
    CUST_ARCHIVED: (CUST_ACTIVE,),
}


def may_transition(current: str, target: str) -> bool:
    """Is this move legal? Unknown current state fails CLOSED.

    A customer row written before this model existed reports `active`, which is
    the safe reading: it is the state that permits the fewest destructive
    follow-ons.
    """
    return target in CUSTOMER_TRANSITIONS.get(current or CUST_ACTIVE, ())


# ── why they left ───────────────────────────────────────────────────────────
#
# A short, fixed vocabulary so churn is countable, plus free text for the part
# no list anticipates. Deliberately not exhaustive and deliberately not
# validated as the ONLY thing recordable: `other` plus a note beats forcing an
# operator to file a real reason under the nearest wrong one.

REASON_PRICE = "price"
REASON_VALUE = "not_seeing_value"
REASON_COMPETITOR = "moved_to_competitor"
REASON_CLOSED = "business_closed"
REASON_CONSOLIDATION = "internal_consolidation"
REASON_NONPAYMENT = "non_payment"
REASON_SERVICE = "service_or_support"
REASON_MISSING_FEATURE = "missing_capability"
REASON_TEST = "test_or_duplicate_record"
REASON_OTHER = "other"

CANCELLATION_REASONS = (
    REASON_PRICE, REASON_VALUE, REASON_COMPETITOR, REASON_CLOSED,
    REASON_CONSOLIDATION, REASON_NONPAYMENT, REASON_SERVICE,
    REASON_MISSING_FEATURE, REASON_TEST, REASON_OTHER,
)

CANCELLATION_REASON_LABELS = {
    REASON_PRICE: "Price",
    REASON_VALUE: "Not seeing value",
    REASON_COMPETITOR: "Moved to a competitor",
    REASON_CLOSED: "Business closed",
    REASON_CONSOLIDATION: "Internal consolidation",
    REASON_NONPAYMENT: "Non-payment",
    REASON_SERVICE: "Service or support",
    REASON_MISSING_FEATURE: "Missing capability",
    REASON_TEST: "Test or duplicate record",
    REASON_OTHER: "Other",
}


# ── the history ─────────────────────────────────────────────────────────────

EVENT_CANCELLATION_REQUESTED = "cancellation_requested"
EVENT_OFFBOARDING_STARTED = "offboarding_started"
EVENT_CANCELLED = "cancellation_completed"
EVENT_ARCHIVED = "archived"
EVENT_REACTIVATED = "reactivated"

LIFECYCLE_EVENTS = (EVENT_CANCELLATION_REQUESTED, EVENT_OFFBOARDING_STARTED,
                    EVENT_CANCELLED, EVENT_ARCHIVED, EVENT_REACTIVATED)


class CustomerLifecycleEvent(Base):
    """One transition in a customer's life. Append-only.

    NOTHING UPDATES OR DELETES A ROW HERE. A customer who cancelled in March,
    came back in June and cancelled again in November has three rows, and the
    March reason survives the November one — which is the entire point of
    keeping history separately from the current-state columns.

    This does NOT replace the audit log, and the audit log does not replace
    this. The audit log answers "what did somebody do to the system"; this
    answers "what happened to this customer", and it is the table a churn
    report reads. Both are written on every transition.
    """
    __tablename__ = "customer_lifecycle_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)

    event = Column(String, nullable=False)          # LIFECYCLE_EVENTS
    from_status = Column(String, nullable=True)     # CUSTOMER_STATUSES
    to_status = Column(String, nullable=False)      # CUSTOMER_STATUSES

    # WHEN THE CUSTOMER STOPS BEING SERVED, which is not when somebody clicked.
    # A notice period, a paid-through date and a contract end are all reasons
    # these differ, and collapsing them is how a customer gets cut off early or
    # billed late.
    effective_at = Column(DateTime, nullable=True)

    reason = Column(String, nullable=True)          # CANCELLATION_REASONS
    note = Column(Text, nullable=True)

    # WHAT WAS STILL OWED WHEN THEY LEFT, captured as a sentence at the moment
    # of cancellation. Not a calculation and not a promise to collect — a
    # record that somebody was told, so a cancellation cannot quietly erase an
    # obligation that nothing else in this codebase currently tracks.
    obligations_note = Column(Text, nullable=True)

    actor_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False,
                        index=True)

    __table_args__ = (
        Index("ix_cust_lifecycle_org_created", "organization_id", "created_at"),
    )
