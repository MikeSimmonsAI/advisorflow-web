"""Organization-defined qualification rules — how each customer says what a
valuable lead is, WITHOUT any of them being written into the platform.

THE RULE THIS FILE EXISTS TO ENFORCE
------------------------------------
    "Do NOT hardcode Restland-specific classifications into the platform
     engine. The architecture must allow each organization to define what
     constitutes a valuable lead."   - Mike

A funeral home cares about property owners with no memorial. A roofer cares
about a storm area and an aging estimate. A brokerage cares about seller leads.
None of those belong in `app/services/qualification.py`, and the moment one of
them is written there the engine stops being a platform capability and becomes
one customer's report.

So the engine ships GENERIC PRIMITIVES - a field, an operator, a value, an
effect - and every industry-specific meaning lives in rows scoped to an
organization. `TierDefinition` is the precedent this follows: the platform
knows what a tier IS, each organization says which tiers it HAS.

WHAT A RULE CAN DO
------------------
    exclude   this lead may not be contacted on this channel at all
    review    a human should look before this one goes out
    boost     add points, with a reason the person reading it can check
    demote    subtract points, same

WHAT A RULE CANNOT DO
---------------------
It cannot widen authorization. Rules are applied to leads the caller was
already entitled to see, never to select them - qualification narrows a scope
that lead_scope has already decided, and there is no rule effect that admits a
lead. An `exclude` rule can only remove; there is deliberately no `include`.

It also cannot read arbitrary attributes. `field` is checked against a
whitelist in the engine (RULE_FIELDS), so a rule cannot be pointed at
`password_hash` or at another table by writing a clever string.
"""

from datetime import datetime

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Text, Integer, Index,
)

from app.models.models import Base, gen_uuid


# Effects a rule may have. `include` is deliberately absent - see the docstring.
RULE_EFFECTS = ("exclude", "review", "boost", "demote")

# Operators the engine knows how to evaluate. Kept small on purpose: every one
# of these has to be explainable in a sentence to the person reading a reason.
RULE_OPERATORS = (
    "equals", "not_equals", "in", "not_in", "contains", "not_contains",
    "is_empty", "is_not_empty", "is_true", "is_false",
    "older_than_days", "newer_than_days",
    "greater_than", "less_than",
)


class QualificationRule(Base):
    """One organization-defined rule, for one channel or for all of them."""

    __tablename__ = "qualification_rules"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)

    # Human name, shown in the rule list. Not used for matching.
    name = Column(String, nullable=False)

    # NULL means "every channel". Otherwise "email" | "sms" | "voice".
    # A rule that excludes a lead with no mobile belongs to SMS, not to email -
    # which is the whole reason qualification is not one global yes/no field.
    channel = Column(String, nullable=True)

    effect = Column(String, nullable=False)            # RULE_EFFECTS
    points = Column(Integer, nullable=False, default=0)  # boost / demote only

    # WHAT IS BEING TESTED. Either a whitelisted Lead field name, or
    # "custom_fields.<key>" to reach a column the organization imported that
    # the platform has no opinion about. The whitelist lives in the engine so
    # there is one place to audit what a rule can see.
    field = Column(String, nullable=False)
    operator = Column(String, nullable=False)          # RULE_OPERATORS
    # Serialized comparison value. Lists for in/not_in are JSON arrays; the
    # numeric operators parse it as a number. Stored as text so one column
    # serves every operator rather than five nullable typed columns.
    value = Column(Text, nullable=True)

    # THE SENTENCE THE PERSON READS. Required, because a qualification result
    # whose reason is "rule 7 matched" is not explainable, and explainable is
    # the entire point of this engine over an opaque score.
    reason_label = Column(String, nullable=False)

    # Lower runs first. Only matters for reason ordering and for which
    # exclusion is reported when several apply.
    sort_order = Column(Integer, nullable=False, default=100)

    is_active = Column(Boolean, nullable=False, default=True)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # Every read is "the active rules for this organization and channel".
        Index("ix_qualrules_org_active", "organization_id", "is_active"),
    )
