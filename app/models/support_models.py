"""SUPPORT INTELLIGENCE — the tables underneath every brand's help experience.

WHERE THIS SITS
---------------
AdvisorFlow is the platform. A white-label brand (EvoSys Pro, BookaBoost, a
future one) owns the FACE a customer sees — "Ask Evo", "BookaBoost Support".
AdvisorFlow owns the BRAIN: the diagnosis, the fixer, the ticket, the SLA
clock, the correlation across brands, and the audit. So there is exactly one
set of tables, scoped by `platform_id` and `organization_id`, and a brand is a
configuration of it rather than a copy of it.

WHY THESE ARE ORM TABLES AND NOT DDL IN auto_migrate
----------------------------------------------------
`app/auto_migrate.py` says it out loud: ONE OWNER PER TABLE. `crm_contacts`
was created both by the ORM and by a hand-written CREATE TABLE, and the two
drifted. Every table here is declared once, in this module, and reaches a
database through `Base.metadata.create_all()` — which is why this module MUST
be imported by `app/models/registry.py`. A model nothing imports is a table
that silently never exists.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No second audit system: control-plane actions still go through
`audit_log_router.log_action`. `support_ticket_events` and `support_fix_runs`
are the *domain* record of what happened to a ticket and to a remediation —
the thing a support engineer reads — not a competing security ledger.

No credentials, no tokens, no raw provider payloads. Diagnostic evidence is
stored as a redacted summary produced by `support_diagnostics.py`; the
redaction happens before the write, not on the way out, so a bug in a
serializer cannot leak what was never stored.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, LargeBinary,
    String, Text, UniqueConstraint,
)

from app.models.models import Base, gen_uuid


# ══════════════════════════════════════════════════════════════════════════
# VOCABULARY
# ══════════════════════════════════════════════════════════════════════════
#
# Plain string constants rather than SAEnum, deliberately. This codebase has a
# standing scar about SAEnum writing the member NAME into Postgres and about
# adding an enum value needing its own migration entry (see auto_migrate's
# ENUM_VALUES_TO_ADD). A support product grows new categories and statuses;
# strings with a validated allow-list in the service layer cost one CHECK we
# do not need and buy a schema that never blocks a deploy.


class Severity:
    """How bad it is. Recommended by AI, never taken on the customer's word."""

    P1 = "P1"   # business-critical service unavailable / severe production failure
    P2 = "P2"   # major capability materially impaired
    P3 = "P3"   # a problem exists, system operational or workaround available
    P4 = "P4"   # question / configuration / training / enhancement / service request

    ALL = (P1, P2, P3, P4)

    LABELS = {
        P1: "Critical",
        P2: "High",
        P3: "Normal",
        P4: "Question or request",
    }

    # What a customer reads. No internal jargon, no promise we have not made.
    MEANINGS = {
        P1: "A business-critical part of the service is unavailable and there "
            "is no reasonable workaround.",
        P2: "A major capability is materially impaired.",
        P3: "Something is wrong, but the system is working or there is a way "
            "around it.",
        P4: "A question, a configuration request, training, or a request for "
            "work to be done.",
    }

    # Worst first, so a queue can lead with what needs doing.
    ORDER = {P1: 0, P2: 1, P3: 2, P4: 3}


class TicketStatus:
    """The lifecycle. AI_TRIAGE exists because a ticket is diagnosed BEFORE a
    human ever sees it, and a queue that shows it as NEW during that window is
    lying about who is holding it."""

    AI_TRIAGE = "ai_triage"
    NEW = "new"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    WAITING_ON_CUSTOMER = "waiting_on_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"

    ALL = (AI_TRIAGE, NEW, ASSIGNED, IN_PROGRESS, WAITING_ON_CUSTOMER,
           RESOLVED, CLOSED)

    # Still ours to answer. Everything else is either the customer's turn or done.
    OPEN = (AI_TRIAGE, NEW, ASSIGNED, IN_PROGRESS)
    TERMINAL = (RESOLVED, CLOSED)

    LABELS = {
        AI_TRIAGE: "Being diagnosed",
        NEW: "Open",
        ASSIGNED: "Assigned",
        IN_PROGRESS: "In progress",
        WAITING_ON_CUSTOMER: "Waiting on you",
        RESOLVED: "Resolved",
        CLOSED: "Closed",
    }


class TicketCategory:
    """THE THREE COMMERCIAL CONCEPTS, and they are not interchangeable.

    TECHNICAL_PRODUCT_SUPPORT  the product is not doing what it is supposed to
                               do. Included in every paying package. A customer
                               is NEVER charged because our software is broken,
                               and — the part that is easy to get wrong — this
                               must never consume their paid consulting minutes.

    CUSTOMER_ASSISTANCE        help USING or CONFIGURING a working product.
                               Some live assistance is included by package;
                               beyond that it is billable.

    PROFESSIONAL_SERVICES      our people doing work for their business:
                               campaigns, migrations, integrations, training,
                               custom development. Billable.
    """

    TECHNICAL_PRODUCT_SUPPORT = "technical_product_support"
    CUSTOMER_ASSISTANCE = "customer_assistance"
    PROFESSIONAL_SERVICES = "professional_services"

    ALL = (TECHNICAL_PRODUCT_SUPPORT, CUSTOMER_ASSISTANCE, PROFESSIONAL_SERVICES)

    LABELS = {
        TECHNICAL_PRODUCT_SUPPORT: "Something isn't working",
        CUSTOMER_ASSISTANCE: "Help using the product",
        PROFESSIONAL_SERVICES: "Work you'd like us to do",
    }

    # Which categories may draw down included live-assistance minutes.
    # Technical product support is absent, and that absence is the rule.
    CONSUMES_ASSISTANCE_ALLOWANCE = (CUSTOMER_ASSISTANCE, PROFESSIONAL_SERVICES)


class SlaState:
    WITHIN = "within"
    AT_RISK = "at_risk"
    BREACHED = "breached"
    PAUSED = "paused"
    MET = "met"          # first response happened before the target
    NOT_APPLICABLE = "not_applicable"

    ALL = (WITHIN, AT_RISK, BREACHED, PAUSED, MET, NOT_APPLICABLE)


class Queue:
    """Commercial priority. Package buys a queue; an emergency overrides it."""

    STANDARD = "standard"
    PRIORITY = "priority"
    PRIORITY_PLUS = "priority_plus"
    EMERGENCY = "emergency"

    ALL = (STANDARD, PRIORITY, PRIORITY_PLUS, EMERGENCY)
    LABELS = {
        STANDARD: "Standard",
        PRIORITY: "Priority",
        PRIORITY_PLUS: "Priority+",
        EMERGENCY: "Emergency",
    }


class Cause:
    """What the evidence says is actually responsible. Drives who fixes it."""

    USER_QUESTION = "user_question"
    CUSTOMER_CONFIGURATION = "customer_configuration"
    USAGE_CAPACITY = "usage_capacity"
    THIRD_PARTY_PROVIDER = "third_party_provider"
    PLATFORM_DEFECT = "platform_defect"
    UNKNOWN = "unknown"

    ALL = (USER_QUESTION, CUSTOMER_CONFIGURATION, USAGE_CAPACITY,
           THIRD_PARTY_PROVIDER, PLATFORM_DEFECT, UNKNOWN)

    LABELS = {
        USER_QUESTION: "How-to question",
        CUSTOMER_CONFIGURATION: "Customer configuration",
        USAGE_CAPACITY: "Usage or capacity",
        THIRD_PARTY_PROVIDER: "Third-party provider",
        PLATFORM_DEFECT: "Platform defect",
        UNKNOWN: "Not yet determined",
    }


class RiskClass:
    """WHAT MAY RUN WITHOUT A HUMAN, and what may never.

    SAFE_AUTO       idempotent, reversible-by-repetition, no customer data
                    mutation. May run automatically when policy allows.
    CONTROLLED      may run automatically ONLY where God has explicitly
                    enabled that remediation for that scope.
    GOD_APPROVAL    diagnosed and prepared by AI; a human with root authority
                    presses the button.
    ENGINEERING     no runtime remediation exists. Produce evidence, root
                    cause, recommendation. Nothing here writes or deploys code.
    """

    SAFE_AUTO = "safe_auto"
    CONTROLLED = "controlled"
    GOD_APPROVAL = "god_approval"
    ENGINEERING = "engineering"

    ALL = (SAFE_AUTO, CONTROLLED, GOD_APPROVAL, ENGINEERING)

    LABELS = {
        SAFE_AUTO: "Safe automatic fix",
        CONTROLLED: "Controlled — policy gated",
        GOD_APPROVAL: "Requires God approval",
        ENGINEERING: "Engineering fix required",
    }

    # The only two an automated path may ever execute without a person.
    AUTOMATABLE = (SAFE_AUTO, CONTROLLED)


class FixStatus:
    """The execution contract, as states. A fix is not FIXED because a function
    returned; it is FIXED because verification said the condition is gone."""

    DETECTED = "detected"
    DIAGNOSED = "diagnosed"
    RECOMMENDED = "recommended"
    APPROVAL_REQUIRED = "approval_required"
    AUTHORIZED = "authorized"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    FIXED = "fixed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    ESCALATED = "escalated"

    ALL = (DETECTED, DIAGNOSED, RECOMMENDED, APPROVAL_REQUIRED, AUTHORIZED,
           EXECUTING, VERIFYING, FIXED, FAILED, ROLLED_BACK, ESCALATED)

    TERMINAL = (FIXED, FAILED, ROLLED_BACK, ESCALATED)


class IncidentStatus:
    SUSPECTED = "suspected"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"

    ALL = (SUSPECTED, ACKNOWLEDGED, INVESTIGATING, MITIGATED, RESOLVED, DISMISSED)
    OPEN = (SUSPECTED, ACKNOWLEDGED, INVESTIGATING, MITIGATED)


class AuthorizationSource:
    """WHO said this fix could run. Recorded on every execution, always."""

    POLICY_AUTO = "policy_auto"          # a registered SAFE_AUTO fix under standing policy
    POLICY_CONTROLLED = "policy_controlled"   # an explicit God-configured scope policy
    GOD_APPROVAL = "god_approval"        # a named god_admin approved this run
    CUSTOMER_SELF_SERVICE = "customer_self_service"  # customer pressed a safe, own-scope button

    ALL = (POLICY_AUTO, POLICY_CONTROLLED, GOD_APPROVAL, CUSTOMER_SELF_SERVICE)


# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION — what a brand's support product actually promises
# ══════════════════════════════════════════════════════════════════════════

class SupportEntitlementConfig(Base):
    """What one PACKAGE of one BRAND includes, as support.

    Keyed by (platform_id, plan_key) so EvoSys Pro Growth and BookaBoost
    Growth can promise different things — which they will, because they sell
    to different people. `plan_key` matches the brand billing catalogue's
    `BrandBillingPlan.key` and the sales catalogue's `SalesPackage.key`
    ('starter' | 'growth' | 'professional' | …): support entitlement is a
    property of the package the customer bought, not a fourth vocabulary.

    NOTHING IS HARD-CODED GLOBALLY. `support_entitlements.py` carries a
    documented DEFAULT for a brand that has not configured itself, in exactly
    the same shape as `brand_config.FROZEN_BRAND_DEFAULTS` — a fallback that a
    row overrides field by field, never a rule.
    """

    __tablename__ = "support_entitlement_configs"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    plan_key = Column(String, nullable=False)

    # Human label for the support plan as the customer sees it, e.g. "Growth
    # Support". NULL falls back to the billing plan's own name.
    display_name = Column(String, nullable=True)

    # NULL MEANS "INHERIT THE PACKAGE RULE", ON EVERY COLUMN BELOW.
    #
    # This was `nullable=False, default=Queue.STANDARD` and that was a bug with
    # a straight face: an operator editing only the response targets for Growth
    # would have silently written queue='standard' and demoted every Growth
    # customer from the priority queue they pay for. A column default is not a
    # neutral technical choice when a partial edit is the normal case — it is a
    # value nobody typed, applied to a promise somebody sold.
    #
    # So the merge in `support_entitlements.resolve` is field by field, and
    # NULL is what makes that possible: a row that sets one field keeps the
    # package's answer for the rest.
    queue = Column(String, nullable=True)

    # FIRST RESPONSE ONLY, IN BUSINESS MINUTES.
    #
    # There is deliberately no resolution target column. A resolution promise
    # we have not decided is a promise we would break, and a NULL column that
    # renders as "—" on a customer's SLA page is worse than not offering it.
    first_response_normal_minutes = Column(Integer, nullable=True)
    first_response_high_minutes = Column(Integer, nullable=True)
    first_response_critical_minutes = Column(Integer, nullable=True)

    # Included scheduled human assistance, per billing period. NO ROLLOVER —
    # `support_entitlements.assistance_summary` recomputes from the current
    # period every time rather than carrying a balance forward.
    # NULL inherits; 0 is a real, deliberate "no included minutes" that a
    # person typed. Those are different answers and the column has to be able
    # to hold both — a NOT NULL DEFAULT 0 here would take Growth's thirty
    # minutes away the first time anybody edited its queue.
    included_assistance_minutes = Column(Integer, nullable=True)

    # Does a verified critical PLATFORM outage jump this customer to the
    # emergency queue regardless of package? True for every package by
    # default, and that is the point of the rule.
    emergency_override = Column(Boolean, nullable=True)

    # Feature switches for the support product itself, as a JSON allow-list:
    # ask_ai, knowledge_base, tickets, system_status, scheduled_assistance.
    included_features_json = Column(Text, nullable=True)

    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("platform_id", "plan_key",
                         name="uq_support_entitlement_platform_plan"),
    )


class SupportBrandSettings(Base):
    """ONE ROW PER BRAND: what its support is called, and when its clock runs.

    THE NAME AND THE HOURS LIVE TOGETHER because they are the same decision —
    "this is EvoSys Pro's support product" — and splitting them into two
    tables would mean two God screens and two ways for a brand to be
    half-configured.

    THE NAMING HALF. A brand owns the face: "Ask Evo", "EvoSys Pro Help
    Centre". `assistant_name` is stored rather than derived because deriving
    "Evo" from "EvoSys Pro" is a guess, and a guess about what a company calls
    its own assistant is exactly the kind of thing that ships wrong and stays
    wrong. NULL falls back to "Ask <brand display name>", which is always
    correct if never clever.

    THE HOURS HALF. An SLA measured in wall-clock hours punishes a customer
    for filing at 4pm on a Friday and rewards us for the weekend. So targets
    are in BUSINESS minutes and these columns say what a business minute is: a
    timezone, a weekly window, and the days nobody is here.

    A brand with no row gets the documented default in `support_sla.py`
    (Mon–Fri 09:00–17:00 America/Chicago, no holidays) rather than 24/7 —
    claiming to be open is the expensive direction to guess in.
    """

    __tablename__ = "support_brand_settings"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=False, unique=True, index=True)

    # ── The face ───────────────────────────────────────────────────────────
    # NULL on any of these means "fall back to the brand's own display name",
    # never "AdvisorFlow": the customer must not see the platform underneath.
    assistant_name = Column(String, nullable=True)      # "Ask Evo"
    help_center_name = Column(String, nullable=True)    # "EvoSys Pro Help Centre"
    support_display_name = Column(String, nullable=True)  # "EvoSys Pro Support"
    greeting = Column(Text, nullable=True)

    timezone = Column(String, nullable=False, default="America/Chicago")
    # Comma-separated Python weekday indices, Monday = 0. Same encoding as
    # `users.available_days`, so an operator reads one format across the app.
    business_days = Column(String, nullable=False, default="0,1,2,3,4")
    business_start = Column(String, nullable=False, default="09:00")   # HH:MM
    business_end = Column(String, nullable=False, default="17:00")     # HH:MM

    # ISO dates, JSON list of "YYYY-MM-DD". Explicit rather than a holiday
    # library: a platform serving several countries has no single calendar,
    # and an invented one closes support on a day the team is working.
    holidays_json = Column(Text, nullable=True)

    # Emergency queue only. A verified P1 outage does not wait for Monday.
    emergency_is_24x7 = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SupportServiceOffering(Base):
    """A support or professional-service PRODUCT a brand sells.

    NOT A SECOND BILLING ENGINE. Nothing here charges a card. A row carries
    either a configured price or, far more often, `pricing_mode='quoted'` and
    a NULL amount, because most of these are scoped work. `stripe_price_id`
    is the hook into the existing Stripe catalogue for the ones that are
    genuinely a fixed SKU; it is filled in by the same provisioning that
    handles every other price, never invented here.

    NO DEFAULT DOLLAR AMOUNTS SHIP IN CODE. A price nobody decided is a price
    a customer would be quoted by accident.
    """

    __tablename__ = "support_service_offerings"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    key = Column(String, nullable=False)          # live_support_session, hour_pack, …
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String, nullable=False,
                      default=TicketCategory.PROFESSIONAL_SERVICES)

    # 'quoted' | 'fixed' | 'hourly' | 'included'
    pricing_mode = Column(String, nullable=False, default="quoted")
    price_cents = Column(Integer, nullable=True)   # NULL unless a human set it
    currency = Column(String, nullable=False, default="usd")
    unit = Column(String, nullable=True)           # "session", "hour", "month"
    stripe_price_id = Column(String, nullable=True)

    sort_order = Column(Integer, nullable=False, default=100)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("platform_id", "key", name="uq_support_offering_platform_key"),
    )


class SupportFixPolicy(Base):
    """God's answer to "may this remediation run by itself, here?".

    A CONTROLLED fix executes only where a row here says so. Scope is
    deliberately coarse and explicit: platform-wide, or one organization.
    There is no wildcard, no inheritance chain and no "all brands" flag,
    because the failure mode of a permission system nobody can read in one
    line is that it grants more than anyone intended.
    """

    __tablename__ = "support_fix_policies"

    id = Column(String, primary_key=True, default=gen_uuid)
    action_key = Column(String, nullable=False, index=True)

    # Exactly one of these is set. NULL platform + NULL org is refused by the
    # service layer rather than read as "everywhere".
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=True, index=True)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=True, index=True)

    auto_execute = Column(Boolean, nullable=False, default=False)
    max_runs_per_day = Column(Integer, nullable=True)   # NULL = uncapped

    created_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_support_fix_policy_lookup", "action_key", "platform_id",
              "organization_id", "is_active"),
    )


# ══════════════════════════════════════════════════════════════════════════
# KNOWLEDGE
# ══════════════════════════════════════════════════════════════════════════

class SupportKnowledgeArticle(Base):
    """Approved help content. What Ask AI is allowed to answer FROM.

    `platform_id` NULL means the article is platform-wide — it describes how
    AdvisorFlow behaves, so every brand's help centre can serve it under its
    own name. A brand-scoped article overrides nothing; both are returned and
    the brand's own is ranked first.

    PUBLICATION IS CONTROLLED. `is_published` is the gate, and nothing
    automatic ever sets it. The learning loop proposes articles into
    `SupportKnowledgeCandidate`; a person publishes.
    """

    __tablename__ = "support_knowledge_articles"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=True, index=True)
    slug = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    body = Column(Text, nullable=False)
    category = Column(String, nullable=True)
    keywords = Column(Text, nullable=True)         # comma separated, lowercased
    related_services = Column(Text, nullable=True)  # comma separated service keys

    is_published = Column(Boolean, nullable=False, default=False)
    view_count = Column(Integer, nullable=False, default=0)
    helpful_count = Column(Integer, nullable=False, default=0)
    unhelpful_count = Column(Integer, nullable=False, default=0)

    created_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_support_kb_lookup", "platform_id", "is_published"),
    )


class SupportKnowledgeCandidate(Base):
    """Something the platform LEARNED, waiting for a person to decide.

    THE LEARNING LOOP DOES NOT TRAIN ON CUSTOMERS. No conversation text is
    fed back into a model, no article publishes itself and no new remediation
    registers itself. What happens instead is that a resolved pattern becomes
    a proposal on a screen, with the evidence attached, and an administrator
    accepts or rejects it. That is the whole loop, on purpose.
    """

    __tablename__ = "support_knowledge_candidates"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)

    # 'knowledge_article' | 'auto_fix_registration' | 'engineering_defect'
    kind = Column(String, nullable=False, index=True)
    signature = Column(String, nullable=True, index=True)
    title = Column(String, nullable=False)
    rationale = Column(Text, nullable=True)
    evidence_json = Column(Text, nullable=True)
    occurrence_count = Column(Integer, nullable=False, default=1)
    organizations_affected = Column(Integer, nullable=False, default=1)

    # 'proposed' | 'accepted' | 'rejected'
    status = Column(String, nullable=False, default="proposed", index=True)
    decided_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    decision_note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ══════════════════════════════════════════════════════════════════════════
# TICKETS
# ══════════════════════════════════════════════════════════════════════════

class SupportTicket(Base):
    """One customer problem, from first words to close.

    THE ENTITLEMENT IS SNAPSHOTTED, NOT LOOKED UP LATER. `entitlement_json`
    records the package, queue and response target that applied WHEN THE
    TICKET WAS RAISED. A customer who upgrades on Tuesday does not
    retroactively acquire a four-hour target for Monday's ticket, and a
    customer who downgrades does not lose the one they paid for. The billing
    catalogue changing must never silently rewrite what we promised.
    """

    __tablename__ = "support_tickets"

    id = Column(String, primary_key=True, default=gen_uuid)
    # Human-readable, stable, and safe to say out loud on a phone call.
    ticket_number = Column(String, nullable=False, unique=True, index=True)

    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    submitted_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    subject = Column(String, nullable=False)
    category = Column(String, nullable=False,
                      default=TicketCategory.TECHNICAL_PRODUCT_SUPPORT)
    severity = Column(String, nullable=False, default=Severity.P3)
    # What the CUSTOMER said it was, kept beside what we decided it is. A
    # customer cannot set severity; they can tell us how it feels, and the
    # difference between the two columns is a real signal.
    customer_reported_severity = Column(String, nullable=True)

    status = Column(String, nullable=False, default=TicketStatus.AI_TRIAGE, index=True)
    queue = Column(String, nullable=False, default=Queue.STANDARD, index=True)

    assigned_to = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_team = Column(String, nullable=True)

    entitlement_json = Column(Text, nullable=True)

    # ── SLA ────────────────────────────────────────────────────────────────
    first_response_due_at = Column(DateTime, nullable=True)
    first_response_at = Column(DateTime, nullable=True)
    sla_state = Column(String, nullable=False, default=SlaState.WITHIN, index=True)
    # Business minutes already spent, banked whenever the clock pauses. The
    # clock is never "rewound"; a pause banks and a resume restarts from now.
    sla_elapsed_minutes = Column(Integer, nullable=False, default=0)
    sla_paused_at = Column(DateTime, nullable=True)
    sla_clock_started_at = Column(DateTime, nullable=True)

    # ── What the platform worked out ───────────────────────────────────────
    ai_summary = Column(Text, nullable=True)
    ai_suspected_cause = Column(String, nullable=True)        # Cause.*
    ai_confidence = Column(String, nullable=True)             # low | medium | high
    ai_recommendation = Column(Text, nullable=True)
    diagnostic_run_id = Column(String, nullable=True, index=True)
    issue_signature = Column(String, nullable=True, index=True)
    incident_id = Column(String, ForeignKey("support_incidents.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    related_ticket_id = Column(String, nullable=True, index=True)

    # ── Assistance accounting ──────────────────────────────────────────────
    # Minutes of scheduled human time this ticket consumed, and whether they
    # came out of the included allowance or were billable. A technical product
    # support ticket must never write a non-zero included figure here — see
    # TicketCategory.CONSUMES_ASSISTANCE_ALLOWANCE.
    assistance_minutes_included = Column(Integer, nullable=False, default=0)
    assistance_minutes_billable = Column(Integer, nullable=False, default=0)

    resolution = Column(Text, nullable=True)
    resolution_code = Column(String, nullable=True)   # fixed_automatically | fixed_by_engineer | …
    satisfaction_score = Column(Integer, nullable=True)   # 1-5, only if a customer answers

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    last_customer_reply_at = Column(DateTime, nullable=True)
    last_agent_reply_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_support_tickets_org_status", "organization_id", "status"),
        Index("ix_support_tickets_queue_sev", "queue", "severity", "status"),
        Index("ix_support_tickets_platform_created", "platform_id", "created_at"),
    )


class SupportTicketMessage(Base):
    """One turn of the conversation.

    `is_internal` is the whole reason this is not two tables: an internal note
    and a customer update are the same event in the same thread, and splitting
    them is how a note ends up in front of a customer. Every read path for a
    customer filters `is_internal == False`, once, in `support_tickets.py`.
    """

    __tablename__ = "support_ticket_messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    ticket_id = Column(String, ForeignKey("support_tickets.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)

    # 'customer' | 'agent' | 'ai' | 'system'
    author_kind = Column(String, nullable=False, default="customer")
    author_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_display = Column(String, nullable=True)

    body = Column(Text, nullable=False)
    is_internal = Column(Boolean, nullable=False, default=False, index=True)
    # Set when this message is what stopped the first-response clock.
    is_first_response = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_support_msgs_ticket_time", "ticket_id", "created_at"),
    )


class SupportTicketAttachment(Base):
    """A screenshot or a log the customer sent.

    Bytes in the row, exactly like `exec_workspace_files`. Not because it is
    the prettiest storage but because it is the storage this platform already
    has, and a second blob strategy is a second thing to back up, secure and
    migrate.
    """

    __tablename__ = "support_ticket_attachments"

    id = Column(String, primary_key=True, default=gen_uuid)
    ticket_id = Column(String, ForeignKey("support_tickets.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    message_id = Column(String, ForeignKey("support_ticket_messages.id", ondelete="SET NULL"),
                        nullable=True)

    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=False, default="application/octet-stream")
    file_size = Column(Integer, nullable=False, default=0)
    file_data = Column(LargeBinary, nullable=False)

    uploaded_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class SupportTicketEvent(Base):
    """The ticket's own history: assignment, severity change, SLA pause, close.

    Separate from `audit_log_entries` on purpose. That table is the security
    ledger an administrator reads; this is the story of one ticket that a
    support engineer reads on the ticket itself. The security-relevant subset
    (severity reclassification, God actions) writes BOTH.
    """

    __tablename__ = "support_ticket_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    ticket_id = Column(String, ForeignKey("support_tickets.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)

    event_type = Column(String, nullable=False, index=True)
    actor_kind = Column(String, nullable=False, default="system")  # customer|agent|ai|system
    actor_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    from_value = Column(String, nullable=True)
    to_value = Column(String, nullable=True)
    detail = Column(Text, nullable=True)
    # True when this event is visible on the customer's own timeline.
    customer_visible = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class SupportAssistanceEntry(Base):
    """Scheduled human time: requested, delivered, and who paid for it.

    NO ROLLOVER is enforced by reading only the current period, never by
    decrementing a stored balance. A balance column would drift the first time
    a period boundary was computed differently in two places.
    """

    __tablename__ = "support_assistance_entries"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    ticket_id = Column(String, ForeignKey("support_tickets.id", ondelete="SET NULL"),
                       nullable=True, index=True)

    # 'requested' | 'scheduled' | 'delivered' | 'cancelled'
    status = Column(String, nullable=False, default="requested", index=True)
    category = Column(String, nullable=False, default=TicketCategory.CUSTOMER_ASSISTANCE)

    requested_topic = Column(Text, nullable=True)
    scheduled_for = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    minutes = Column(Integer, nullable=False, default=0)

    # THE COMMERCIAL FACT. True means it came out of the included allowance;
    # False means it is billable. A technical product support session is
    # neither — it is recorded with minutes and `billable=False`,
    # `counts_against_allowance=False`, because our defect is not their time.
    counts_against_allowance = Column(Boolean, nullable=False, default=True)
    billable = Column(Boolean, nullable=False, default=False)
    offering_key = Column(String, nullable=True)

    period_start = Column(DateTime, nullable=True, index=True)
    period_end = Column(DateTime, nullable=True)

    requested_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_support_assist_org_period", "organization_id", "period_start"),
    )


# ══════════════════════════════════════════════════════════════════════════
# ASK [BRAND] — the conversation, and what it was allowed to see
# ══════════════════════════════════════════════════════════════════════════

class SupportConversation(Base):
    """One Ask-[Brand] session, owned by one organization.

    The brand's DISPLAY name is stored on the row rather than resolved at read
    time, so a transcript read a year later says what the customer was
    actually talking to. A brand that renames its assistant does not rewrite
    history.
    """

    __tablename__ = "support_conversations"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    assistant_name = Column(String, nullable=True)     # "Ask Evo", "Ask BookaBoost"
    title = Column(String, nullable=True)

    # 'open' | 'escalated' | 'resolved' | 'abandoned'
    status = Column(String, nullable=False, default="open", index=True)
    resolved_by = Column(String, nullable=True)        # ai | auto_fix | ticket | customer
    escalated_ticket_id = Column(String, ForeignKey("support_tickets.id", ondelete="SET NULL"),
                                 nullable=True, index=True)

    suspected_cause = Column(String, nullable=True)
    issue_signature = Column(String, nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SupportConversationTurn(Base):
    """One message in that session, plus the machinery it triggered.

    `tool_calls_json` records WHICH REGISTERED TOOLS RAN — names and arguments
    only, never their output. The output is a diagnostic run with its own row
    and its own redaction; duplicating it here would create a second copy of
    the same evidence with none of the same guarantees.

    CUSTOMER TEXT IN `content` IS UNTRUSTED DATA and is treated as such
    everywhere it is read back. It is stored verbatim because a support
    transcript that has been "cleaned" is no longer evidence.
    """

    __tablename__ = "support_conversation_turns"

    id = Column(String, primary_key=True, default=gen_uuid)
    conversation_id = Column(String, ForeignKey("support_conversations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)

    role = Column(String, nullable=False)              # user | assistant | system
    content = Column(Text, nullable=False)

    tool_calls_json = Column(Text, nullable=True)
    diagnostic_run_id = Column(String, nullable=True, index=True)
    fix_run_id = Column(String, nullable=True, index=True)
    knowledge_article_ids = Column(Text, nullable=True)   # comma separated
    source = Column(String, nullable=True)             # model | rules | knowledge_base

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class SupportDiagnosticRun(Base):
    """What the platform looked at, on whose authority, and what it found.

    TWO SUMMARIES, NEVER ONE. `customer_summary_json` is what a customer may
    be shown; `technical_summary_json` is what God may be shown. They are
    produced at write time by `support_diagnostics.py`, not filtered at read
    time by whoever is rendering — a redaction that lives in a serializer is a
    redaction one new endpoint forgets.
    """

    __tablename__ = "support_diagnostic_runs"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    conversation_id = Column(String, nullable=True, index=True)
    ticket_id = Column(String, nullable=True, index=True)

    requested_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requested_by_kind = Column(String, nullable=False, default="ai")   # ai | customer | god | system

    checks_run = Column(Text, nullable=True)           # comma separated check keys
    overall_severity = Column(String, nullable=True)   # app.services.severity vocabulary
    suspected_cause = Column(String, nullable=True)
    confidence = Column(String, nullable=True)

    customer_summary_json = Column(Text, nullable=True)
    technical_summary_json = Column(Text, nullable=True)

    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ══════════════════════════════════════════════════════════════════════════
# REMEDIATION
# ══════════════════════════════════════════════════════════════════════════

class SupportFixRun(Base):
    """One attempt to fix one thing, with the whole contract on the row.

    DETECT → SNAPSHOT → AUTHORIZE → EXECUTE → VERIFY → RECORD → REPORT.

    `verified` is a separate boolean from `status` and that redundancy is
    deliberate: a row can be FAILED with verification never attempted, or
    EXECUTING with verification pending, and a reader must be able to tell
    "we think it worked" from "we checked and it worked". A fix is only ever
    reported to a customer as fixed when `verified` is True.
    """

    __tablename__ = "support_fix_runs"

    id = Column(String, primary_key=True, default=gen_uuid)
    action_key = Column(String, nullable=False, index=True)

    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=True, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    ticket_id = Column(String, ForeignKey("support_tickets.id", ondelete="SET NULL"),
                       nullable=True, index=True)
    conversation_id = Column(String, nullable=True, index=True)
    diagnostic_run_id = Column(String, nullable=True, index=True)
    incident_id = Column(String, nullable=True, index=True)

    status = Column(String, nullable=False, default=FixStatus.DETECTED, index=True)
    risk_class = Column(String, nullable=False, default=RiskClass.GOD_APPROVAL)

    target_resource_type = Column(String, nullable=True)
    target_resource_id = Column(String, nullable=True)

    detection_source = Column(String, nullable=True)   # ask_ai | sensor | god | ticket
    diagnosis = Column(Text, nullable=True)
    confidence = Column(String, nullable=True)
    issue_signature = Column(String, nullable=True, index=True)

    # WHO SAID YES. Never nullable once status passes AUTHORIZED — the service
    # layer refuses to execute without both of these.
    authorization_source = Column(String, nullable=True)
    authorized_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    authorized_at = Column(DateTime, nullable=True)
    approval_note = Column(Text, nullable=True)

    before_state_json = Column(Text, nullable=True)
    after_state_json = Column(Text, nullable=True)
    execution_result_json = Column(Text, nullable=True)

    verified = Column(Boolean, nullable=False, default=False)
    verification_result_json = Column(Text, nullable=True)
    verification_message = Column(Text, nullable=True)

    rollback_available = Column(Boolean, nullable=False, default=False)
    rolled_back_at = Column(DateTime, nullable=True)

    customer_explanation = Column(Text, nullable=True)
    technical_explanation = Column(Text, nullable=True)
    error_summary = Column(String(512), nullable=True)

    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_support_fix_runs_action_time", "action_key", "created_at"),
        Index("ix_support_fix_runs_org_time", "organization_id", "created_at"),
    )


# ══════════════════════════════════════════════════════════════════════════
# CORRELATION
# ══════════════════════════════════════════════════════════════════════════

class SupportIssueSignature(Base):
    """The same problem, counted.

    A signature is a stable, low-cardinality fingerprint of a KIND of problem
    — "calendar.microsoft.token_expired" — computed by
    `support_incidents.signature_for`. It is the unit the platform learns in:
    eight occurrences today across twelve organizations is a different fact
    from eight tickets, and only this row can say it.
    """

    __tablename__ = "support_issue_signatures"

    id = Column(String, primary_key=True, default=gen_uuid)
    signature = Column(String, nullable=False, unique=True, index=True)

    title = Column(String, nullable=False)
    service = Column(String, nullable=True, index=True)     # calendar | sms | email | ai | billing
    suspected_cause = Column(String, nullable=True)

    first_seen_at = Column(DateTime, default=datetime.utcnow, index=True)
    last_seen_at = Column(DateTime, default=datetime.utcnow, index=True)
    occurrence_count = Column(Integer, nullable=False, default=0)
    organizations_affected = Column(Integer, nullable=False, default=0)
    platforms_affected = Column(Integer, nullable=False, default=0)

    # Counts kept on the signature rather than derived every read: the God
    # console asks this question on every page load and the underlying scan
    # is the expensive part of the page.
    auto_fixed_count = Column(Integer, nullable=False, default=0)
    auto_fix_failed_count = Column(Integer, nullable=False, default=0)
    ticket_count = Column(Integer, nullable=False, default=0)

    known_remediation_key = Column(String, nullable=True)
    # True once a human has confirmed the auto-fix treats the symptom and the
    # cause is still live. This is what turns a working fix into an
    # engineering candidate instead of a permanent crutch.
    symptom_only_remediation = Column(Boolean, nullable=False, default=False)
    engineering_candidate = Column(Boolean, nullable=False, default=False)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SupportIncident(Base):
    """One platform-level event that several customers are feeling.

    CROSS-TENANT BY CONSTRUCTION, AND THEREFORE GOD-ONLY BY CONSTRUCTION.
    Nothing on this row reaches a customer. What a customer may see is a
    derived, sanitized statement — "calendar sync is degraded" — produced by
    `support_incidents.customer_statement()`, which names no other customer,
    no counts and no brand but their own.
    """

    __tablename__ = "support_incidents"

    id = Column(String, primary_key=True, default=gen_uuid)
    incident_number = Column(String, nullable=False, unique=True, index=True)

    title = Column(String, nullable=False)
    signature = Column(String, nullable=True, index=True)
    service = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default=IncidentStatus.SUSPECTED, index=True)

    classification = Column(String, nullable=True)    # Cause.*
    confidence = Column(String, nullable=True)
    likely_shared_dependency = Column(String, nullable=True)
    provider_health_note = Column(Text, nullable=True)

    first_observed_at = Column(DateTime, default=datetime.utcnow, index=True)
    last_observed_at = Column(DateTime, default=datetime.utcnow, index=True)
    occurrence_count = Column(Integer, nullable=False, default=0)
    organizations_affected = Column(Integer, nullable=False, default=0)
    platforms_affected = Column(Integer, nullable=False, default=0)

    # ── The strong recommendation, which is the point of the whole table ───
    likely_root_cause = Column(Text, nullable=True)
    impact_summary = Column(Text, nullable=True)
    recommended_remediation = Column(Text, nullable=True)
    suggested_validation = Column(Text, nullable=True)
    risk_assessment = Column(Text, nullable=True)
    evidence_json = Column(Text, nullable=True)

    # What a customer may be told, if anything. NULL means "say nothing" —
    # silence is the safe default for a cross-tenant record.
    customer_facing_statement = Column(Text, nullable=True)

    acknowledged_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SupportIncidentLink(Base):
    """What this incident is made of: tickets, fix runs, diagnostics, orgs."""

    __tablename__ = "support_incident_links"

    id = Column(String, primary_key=True, default=gen_uuid)
    incident_id = Column(String, ForeignKey("support_incidents.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    # 'ticket' | 'fix_run' | 'diagnostic_run' | 'organization' | 'job_run' | 'event'
    link_type = Column(String, nullable=False, index=True)
    link_id = Column(String, nullable=False)
    organization_id = Column(String, nullable=True, index=True)
    platform_id = Column(String, nullable=True, index=True)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("incident_id", "link_type", "link_id",
                         name="uq_support_incident_link"),
    )


class SupportDailyBrief(Base):
    """Yesterday, as one persisted, re-readable document.

    NOT AN EMAIL. An email is a delivery of this; the row is the artefact, so
    "what did the platform look like on the 3rd" is answerable in December.

    NOTHING IS INVENTED. Where there is no history to compare against, the
    trend fields stay NULL and the God screen says "no previous period",
    because a fabricated baseline is how a brief stops being trusted.
    """

    __tablename__ = "support_daily_briefs"

    id = Column(String, primary_key=True, default=gen_uuid)
    brief_date = Column(String, nullable=False, index=True)     # YYYY-MM-DD, UTC
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=True, index=True)             # NULL = whole platform

    issues_detected = Column(Integer, nullable=False, default=0)
    auto_fixed = Column(Integer, nullable=False, default=0)
    auto_fix_failed = Column(Integer, nullable=False, default=0)
    human_fixed = Column(Integer, nullable=False, default=0)
    unresolved = Column(Integer, nullable=False, default=0)
    awaiting_god_approval = Column(Integer, nullable=False, default=0)

    customer_configuration_issues = Column(Integer, nullable=False, default=0)
    provider_issues = Column(Integer, nullable=False, default=0)
    platform_defects = Column(Integer, nullable=False, default=0)
    multi_org_incidents = Column(Integer, nullable=False, default=0)

    sla_at_risk = Column(Integer, nullable=False, default=0)
    sla_breached = Column(Integer, nullable=False, default=0)
    tickets_opened = Column(Integer, nullable=False, default=0)
    tickets_resolved = Column(Integer, nullable=False, default=0)
    first_response_minutes_avg = Column(Integer, nullable=True)

    # Percentage 0-100 of ELIGIBLE auto-fix attempts that verified. NULL when
    # nothing was eligible: a rate computed from zero attempts is not 100%.
    auto_fix_success_rate = Column(Integer, nullable=True)

    top_services_json = Column(Text, nullable=True)
    top_signatures_json = Column(Text, nullable=True)
    recommended_actions_json = Column(Text, nullable=True)
    trend_json = Column(Text, nullable=True)          # NULL when no prior period

    generated_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("brief_date", "platform_id", name="uq_support_brief_date_platform"),
    )
