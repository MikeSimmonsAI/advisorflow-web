"""HOW FAR OFF THE CATALOGUE A PERSON MAY GO, AND WHO SAYS SO.

A percentage, not a price. The catalogue already holds the prices; a floor
expressed as an absolute figure would be a second copy of them that goes stale
the moment a package is repriced, and then the guardrail quietly protects a
number nobody sells at any more.

WHY THIS IS A TABLE AND NOT A CONSTANT
--------------------------------------
The discount a salesperson may give is a commercial decision that changes, and
it will not be the same for every white-label brand on this platform forever.
So it is data, resolved at request time, with a documented fallback - not a
literal compiled into the pricing logic where changing it means a deploy.

RESOLUTION, MOST SPECIFIC WINS
------------------------------
    1. this brand-sales org + this role
    2. this brand-sales org, any role      (brand-wide policy)
    3. no org + this role                  (platform-wide policy for a role)
    4. no org, any role                    (platform default policy)
    5. the built-in fallback below

THE BUILT-IN FALLBACK IS TODAY'S BEHAVIOUR, ON PURPOSE
------------------------------------------------------
With no policy row anywhere, a rep's ceiling is ZERO PERCENT and a manager's is
unlimited. That is exactly what the product does today - `apply_pricing` has
always refused a rep any adjustment and allowed a manager any adjustment - so
installing this system changes nothing until somebody deliberately configures a
policy. The alternative was to pick a number like "10%" here, which would be
this file inventing a commercial policy on the owner's behalf and handing every
rep a discount authority nobody granted.

EACH COMPONENT IS FENCED SEPARATELY
-----------------------------------
Setup and monthly carry their own ceilings and are checked independently. A deal
that guts the recurring rate and makes the total look acceptable by inflating
the one-time fee is precisely the trade this is here to catch: MRR is the thing
the business is actually built on, and a healthy TCV on a gutted MRR is a worse
deal wearing a better number.
"""

from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Index,
                        Integer, Numeric, String, Text)

from app.models.models import Base, gen_uuid

# ── The fallback, used only when no policy row matches ───────────────────────
#
# Deliberately equal to the pre-existing rule so this system is inert until
# configured. See the module docstring.
FALLBACK_REP_MAX_DISCOUNT_PCT = 0          # a rep discounts nothing unasked
FALLBACK_ELEVATED_MAX_DISCOUNT_PCT = None  # None means "no ceiling"

# Roles that carry unlimited pricing authority unless a policy narrows them.
# Kept here rather than in the service so the vocabulary and its meaning sit in
# one place with the table that can override it.
ELEVATED_SALES_ROLES = ("sales_manager", "brand_executive")
ELEVATED_PLATFORM_ROLES = ("god_admin", "super_admin")


class PricingPolicy(Base):
    """One row = "in this scope, this role may discount this much".

    Both percentage columns are NULLABLE and null means UNLIMITED for that
    component, not zero. A policy that sets only the monthly ceiling is saying
    something specific about the monthly rate and nothing at all about setup;
    reading that silence as "0%" would be the table inventing a rule it was not
    asked to express.
    """
    __tablename__ = "pricing_policies"

    id = Column(String, primary_key=True, default=gen_uuid)

    # NULL = the platform-wide policy. Not a ForeignKey-less guess: brand-sales
    # orgs are a real table and a policy for a deleted brand should go with it.
    brand_sales_org_id = Column(String,
                                ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=True, index=True)

    # NULL = every role in this scope. A row naming a role beats a row that
    # does not, which is what lets one brand say "reps 10%, managers 30%".
    role = Column(String, nullable=True)

    # Percent OFF the catalogue figure, 0-100. 15 means the floor is 85% of
    # catalogue. NULL = no ceiling on this component.
    max_discount_pct_setup   = Column(Numeric(5, 2), nullable=True)
    max_discount_pct_monthly = Column(Numeric(5, 2), nullable=True)

    # The shortest commitment this scope may sell. NULL = no minimum. A term is
    # not a discount, but a one-month "agreement" at the agreement rate is a
    # discount wearing a term, so it belongs behind the same gate.
    min_term_months = Column(Integer, nullable=True)

    # WHAT HAPPENS BELOW THE FLOOR.
    #
    # True (the default) routes the negotiated figure to a manager as a request:
    # the rep has not done something forbidden, they have done something someone
    # else has to agree to.
    #
    # False is a HARD STOP, not permission - the price is refused outright and
    # no request is created. It exists for a brand that wants a genuine floor
    # rather than a speed bump. It is deliberately not "allow it anyway": a
    # setting that let a rep past the floor unrecorded would make the floor
    # decorative.
    below_floor_requires_approval = Column(Boolean, default=True, nullable=False)

    # WHEN THIS POLICY APPLIES. Both NULL means "always", which is what every
    # policy written before these columns existed meant.
    #
    # Dated rather than edited in place for the same reason compensation plans
    # are: a discount ceiling that changed in March should explain a February
    # approval, not silently claim February was judged by March's rule.
    effective_from = Column(Date, nullable=True)
    effective_to   = Column(Date, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    # Why this policy exists, for the person who finds it in six months.
    note       = Column(Text, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # DELIBERATELY NOT UNIQUE ON (scope, role) ANY MORE.
        #
        # It was, and that was right while a policy had no dates: two live rows
        # answering the same question is how a guardrail starts depending on row
        # order. Dating them changes the question — "reps, 10%, from January"
        # and "reps, 15%, from June" are both legitimate and must coexist, or a
        # rate change means destroying the record of the previous one.
        #
        # Uniqueness moves into resolution instead: most specific scope wins,
        # then the latest effective_from that has started. See
        # pricing_authority.resolve_policy, which is the single place that
        # decides, so "which policy applies" has one answer rather than one per
        # caller.
        Index("ix_pricing_policies_lookup", "brand_sales_org_id", "is_active"),
    )
