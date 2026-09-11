"""AI WORKFORCE DEPLOYMENT (T8) - hiring an AI employee, as a durable record.

WHAT T8 ADDS THAT T6 AND T7 DID NOT HAVE.

    T6 built the engine: who an AI employee is, what it may do, whether it may
       run. Its `ai_employees` row is the ACTOR.
    T7 built the reach: how an authorised actor touches the world.
    T8 builds the PRODUCT: how a brand offers a job, how a customer acquires
       one, configures it, proves it ready, switches it on, pauses it, and
       gives it back - and what happens to all of that when the commercial
       arrangement behind it ends.

WHY A DEPLOYMENT ROW AND NOT MORE COLUMNS ON `ai_employees`.

Because the two things have different lifetimes and different owners. An
`ai_employees` row is an actor that either exists or does not; a deployment is
a COMMERCIAL AND OPERATIONAL ARRANGEMENT that exists before the actor is
created (a customer who has selected a job and not finished configuring it has
no actor yet) and outlives it (a retired deployment keeps its history after the
actor is disabled). Folding them together would mean creating an actor in order
to record an intention, and the actor is the thing with authority.

It also keeps the two state machines apart, which section 3 of the brief asks
for in as many words: COMMERCIAL STATE AND OPERATIONAL STATE MUST REMAIN
DISTINCT. `state` here is operational. `commercial_state` is a MIRROR of what
T2 says, never an opinion of its own, and nothing in this package writes to a
T2 table.

NO PRICE IS STORED IN THIS FILE. Not an amount, not a currency, not an
interval. The commercial terms of an AI employee are a `brand_catalog_items`
row, owned by T2, and this schema refers to one by KEY. A price column here
would be a second catalogue, which is the one thing the brief forbids twice.

TENANCY. `organization_id` is NOT NULL on every customer-scoped table, the
same positive assertion `ai_employees` and `leads` make, so a missing filter
fails loudly instead of quietly matching IS NULL.

JSON IN TEXT COLUMNS, not a JSON type - matching `ai_employees.config` and
every other structured blob in this schema, so SQLite (tests) and Postgres
(production) behave identically.
"""

from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)

from app.models.models import Base, gen_uuid


# ===========================================================================
# BRAND LAYER - the commercial terms a brand attaches to a job it offers
# ===========================================================================

class AIOfferingTerms(Base):
    """How one brand SELLS one platform job. Availability, not price.

    `ai_brand_offerings` (T6) already says WHICH jobs a brand offers, under
    what name and on which channels. It says nothing about the commercial
    arrangement, because T6 deliberately stopped at the boundary: T2 owns
    commerce. This row is that boundary made explicit - it names a
    `brand_catalog_items.key` inside the SAME brand and states which packages
    may hold the job, and that is all.

    WHY THE CATALOGUE ITEM IS NAMED BY KEY AND NOT BY ID. A key is stable and
    brand-scoped (`uq_catalog_brand_key`), and resolving it goes through
    `brand_catalog.resolve`, which is the one function that answers "does this
    brand have such an item". An id here would be a second way to reach a
    catalogue row, and the second way is the one that forgets the brand scope.

    A ROW HERE GRANTS NOTHING. `is_available` defaults to FALSE and an absent
    row means the job is not commercially available from this brand at all -
    the same default-refuse the catalogue itself takes.
    """

    __tablename__ = "ai_offering_terms"
    __table_args__ = (
        UniqueConstraint("platform_id", "template_key",
                         name="uq_ai_offering_terms_brand_template"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    # The platform template this term sheet is about, by its CODE registry key.
    # Not an FK into `ai_employee_templates`: that table is a mirror of the
    # code registry, and a term sheet must survive the mirror being resynced.
    template_key = Column(String, nullable=False, index=True)

    # ---- How it is acquired -----------------------------------------------
    #
    # included  - comes with one of the packages named in `included_plan_keys`
    #             and needs no purchase. Still needs everything else: readiness,
    #             activation, an operator.
    # addon     - bought through the existing catalogue as a recurring add-on.
    # quoted    - priced per deal by an authorised seller, through the existing
    #             pricing-authority path. No amount is stated anywhere here.
    # capacity  - additional units of a job the customer already holds.
    commercial_mode = Column(String, nullable=False, default="addon")

    # The `brand_catalog_items.key` that sells this job, inside this brand.
    # NULL is the honest state for `included`, and for a job a brand has
    # decided to offer before anybody priced it.
    catalog_item_key = Column(String, nullable=True)

    # JSON lists of `brand_billing_plans.key`. Empty/absent means "no package
    # restriction" for eligibility and "included with nothing" for inclusion -
    # two different defaults, because the safe answer differs: an unstated
    # restriction should not block a sale, and an unstated inclusion must never
    # give a job away.
    included_plan_keys = Column(Text, nullable=True)
    eligible_plan_keys = Column(Text, nullable=True)

    # How many of this job one customer may hold. NULL means "no stated limit",
    # which is not the same as unlimited capacity: package capability gates and
    # entitlement still apply on every one of them.
    max_per_customer = Column(Integer, nullable=True)

    # CONTROLLED BEFORE ACTIVE. Defaults to TRUE and is the brand's to relax,
    # never the customer's. A brand that has never thought about it gets the
    # careful answer.
    requires_controlled_first = Column(Boolean, nullable=False, default=True)

    # Channels this brand will let this job run on commercially. NARROWING
    # ONLY - intersected against the offering, which is itself intersected
    # against the platform template.
    allowed_channels = Column(Text, nullable=True)

    is_available = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)


# ===========================================================================
# CUSTOMER LAYER - one hired AI employee, from selection to retirement
# ===========================================================================

class AIEmployeeDeployment(Base):
    """One AI employee a customer has acquired, and everything around it.

    THE PROVISIONING KEY IS THE IDEMPOTENCY GUARANTEE, and it is a database
    constraint rather than a check. Two clicks on Hire, a retried webhook and a
    duplicated provisioning worker all arrive as the same key, and the second
    one loses at the unique index instead of at a query somebody wrote
    correctly. `uq_ai_deployment_provisioning_key` is what makes "provisioning
    must be idempotent" a property rather than an intention.

    `employee_id` IS NULLABLE AND UNIQUE. Nullable because a deployment exists
    from the moment a customer selects a job, before there is any actor;
    unique because one actor must never be claimed by two deployments, which
    is how an employee ends up entitled by one arrangement and paid for by
    another. Multiple NULLs are permitted in a unique index on both Postgres
    and SQLite, which is the behaviour this relies on - the same reliance
    `ai_tool_executions.idempotency_key` already makes.

    TWO STATES, AND NEITHER IS THE OTHER.

        `state`            operational: where this deployment is in its
                           lifecycle, and whether an operator switched it on.
        `commercial_state` a MIRROR of T2's answer about entitlement. Nothing
                           in this package writes a T2 row, and nothing reads
                           this column to decide whether a tool may run - that
                           question is answered live, by T6's own gateway,
                           against T2's own tables.

    Storing the mirror at all is worth the risk of it going stale because "why
    is this employee suspended" has to be answerable on a screen without
    re-deriving four things, and because a transition needs to record WHAT WAS
    TRUE when it happened. `commercial_checked_at` says how fresh it is, so a
    stale mirror reads as stale rather than as fact.
    """

    __tablename__ = "ai_employee_deployments"
    __table_args__ = (
        UniqueConstraint("organization_id", "provisioning_key",
                         name="uq_ai_deployment_provisioning_key"),
        UniqueConstraint("employee_id", name="uq_ai_deployment_employee"),
        Index("ix_ai_deployments_org_state", "organization_id", "state"),
        Index("ix_ai_deployments_org_template", "organization_id",
              "template_key"),
        Index("ix_ai_deployments_entitlement", "entitlement_key"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    # Denormalised from the organization so brand terms resolve without a join.
    # Never used as the tenancy filter - `organization_id` is.
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)

    template_key = Column(String, nullable=False, index=True)
    offering_id = Column(String, ForeignKey("ai_brand_offerings.id",
                                            ondelete="SET NULL"), nullable=True)
    # SET NULL rather than CASCADE. Deleting an actor must not erase the record
    # that a customer hired one - the arrangement happened, and this row is how
    # that stays answerable.
    employee_id = Column(String, ForeignKey("ai_employees.id",
                                            ondelete="SET NULL"),
                         nullable=True, index=True)

    display_name = Column(String, nullable=True)

    # ---- Operational state -------------------------------------------------
    state = Column(String, nullable=False, default="selected", index=True)
    state_reason = Column(String, nullable=True)
    # The stage an operator last ASKED T6 for. T6's resolver remains the only
    # authority on what is actually in force; this records the request.
    requested_stage = Column(String, nullable=True)
    # Where a pause should return to. Recorded at pause time because resuming
    # to "whatever it says now" is how a paused employee comes back louder than
    # it went away.
    stage_before_pause = Column(String, nullable=True)

    # ---- Commercial mirror (read from T2, never written to it) -------------
    commercial_state = Column(String, nullable=False, default="unknown",
                              index=True)
    commercial_detail = Column(String, nullable=True)
    commercial_checked_at = Column(DateTime, nullable=True)
    entitlement_key = Column(String, nullable=True, index=True)
    catalog_item_key = Column(String, nullable=True)
    # `catalog_purchases.id` as a plain indexed string, NOT a foreign key.
    # A purchase row may be detached from its catalogue item by T2's own
    # SET NULL rules, and a hard FK here would make T8 a reason T2 could not
    # delete something.
    purchase_id = Column(String, nullable=True, index=True)

    # ---- Idempotency -------------------------------------------------------
    provisioning_key = Column(String, nullable=False, index=True)

    # ---- Business configuration (never AI internals) ----------------------
    config = Column(Text, nullable=True)             # JSON object
    config_version = Column(Integer, nullable=False, default=0)
    configured_at = Column(DateTime, nullable=True)
    configured_by = Column(String, ForeignKey("users.id"), nullable=True)

    # ---- Readiness ---------------------------------------------------------
    readiness_state = Column(String, nullable=True, index=True)
    readiness_detail = Column(Text, nullable=True)   # JSON: every check, named
    readiness_checked_at = Column(DateTime, nullable=True)

    # ---- The review, acknowledged ------------------------------------------
    #
    # REVIEW REQUIRED is not a softer NO, and the only thing that gets past it
    # is a person who has seen what it is about. That person is recorded here,
    # along with a DIGEST of exactly which review items they saw.
    #
    # The digest is the whole point. An acknowledgement that said only "somebody
    # looked" would carry over to a review item that appeared afterwards - the
    # handoff owner leaving, a channel becoming live-capable - and the second
    # concern would be waved through by a signature given for the first. A
    # digest that no longer matches is an acknowledgement that no longer
    # applies, which is the safe direction for it to expire in.
    review_acknowledged_at = Column(DateTime, nullable=True)
    review_acknowledged_by = Column(String, ForeignKey("users.id"),
                                    nullable=True)
    review_acknowledged_digest = Column(String, nullable=True)
    review_acknowledgement_note = Column(String, nullable=True)

    # ---- Who did what, and when -------------------------------------------
    requested_by = Column(String, ForeignKey("users.id"), nullable=True)
    activated_by = Column(String, ForeignKey("users.id"), nullable=True)
    activated_at = Column(DateTime, nullable=True)
    paused_at = Column(DateTime, nullable=True)
    paused_by = Column(String, ForeignKey("users.id"), nullable=True)
    suspended_at = Column(DateTime, nullable=True)
    suspend_reason = Column(String, nullable=True)
    retired_at = Column(DateTime, nullable=True)
    retired_by = Column(String, ForeignKey("users.id"), nullable=True)
    retire_reason = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


class AIDeploymentEvent(Base):
    """Every transition, with who caused it and what was true at the time.

    SEPARATE FROM `ai_work_item_events` AND FROM `audit_log_entries`, for the
    reason T6 already recorded about the first of those: the platform audit
    table's `actor_user_id` is NOT NULL, and several transitions here have no
    human behind them at all. An entitlement lapsing suspends a deployment, and
    the actor is the commerce layer.

    NOTHING HERE IS EVER DELETED. Section 12 of the brief: history survives
    retirement, because "what did this employee do for us" is a question asked
    most often about one that is no longer running.
    """

    __tablename__ = "ai_deployment_events"
    __table_args__ = (
        Index("ix_ai_deployment_events_deployment", "deployment_id",
              "created_at"),
        Index("ix_ai_deployment_events_org_created", "organization_id",
              "created_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    deployment_id = Column(String, ForeignKey("ai_employee_deployments.id",
                                              ondelete="CASCADE"),
                           nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    from_state = Column(String, nullable=True)
    to_state = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    # human | system | commerce | platform. A transition nobody chose is not a
    # transition a person made, and an operator reading this afterwards needs
    # to be able to tell them apart.
    actor_kind = Column(String, nullable=False, default="human")
    actor_id = Column(String, nullable=True)
    commercial_state = Column(String, nullable=True)
    readiness_state = Column(String, nullable=True)
    detail = Column(Text, nullable=True)             # JSON object
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
