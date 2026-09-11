"""AI WORKFORCE — the durable objects behind "Your AI Team".

FOUR LAYERS, AND EVERY TABLE HERE KNOWS WHICH ONE IT BELONGS TO.

    1. ADVISORFLOW PLATFORM   reusable engine. Templates and tool definitions
                              live in CODE (app/services/workforce/registry.py),
                              the same way CAPABILITIES and FEATURES do, because
                              a registry in a table is a registry a typo can
                              silently empty. `ai_employee_templates` is the
                              DATABASE MIRROR of that registry, written by a
                              seeder so God Mode can list, version and disable
                              one without a deploy.
    2. WHITE-LABEL BRAND      `ai_brand_offerings` — which approved templates a
                              brand sells, under what name, on which channels.
    3. BRAND BACK-OFFICE      no table of its own. A brand's own staff reach
                              this through the existing brand-sales identity.
    4. CUSTOMER WORKSPACE     everything else here. Every row carries
                              organization_id, and every query filters on it.

WHY NOT MODEL AN AI EMPLOYEE AS A `users` ROW. It was the obvious shortcut and
it is the wrong one. A row in `users` is an authentication subject: it can hold
a password, a session, a capability grant, a membership, and `get_current_user`
would happily resolve it. Everything in this codebase that asks "may this user
do X" would then have to learn about a kind of user that must never log in, and
the one that forgot would be the leak. An AI employee is an ACTOR, not an
identity: it has authority, it is auditable, and it cannot authenticate.

TENANCY. `organization_id` is NOT NULL on every customer-layer table below.
That is the same positive assertion `leads` and `pipeline_conversations` make,
and it is what makes a missing filter fail loudly instead of quietly matching
`IS NULL` — see the note in app/deps.py:require_tenant_user for the outage
that reasoning came from.

JSON IN TEXT COLUMNS, not a JSON type. Matches `Organization.enabled_features`,
`Lead.custom_fields` and every other structured blob in this schema, and keeps
SQLite (the test database) and Postgres (production) behaving identically.
"""

from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        Numeric, String, Text, UniqueConstraint)

from app.models.models import Base, gen_uuid


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 1 — PLATFORM: the reusable job library
# ═══════════════════════════════════════════════════════════════════════════

class AIEmployeeTemplate(Base):
    """One reusable JOB an AI employee can hold.

    The customer never sees this row; they see "AI Reactivation Specialist"
    and a description of what it does. What makes it a template rather than a
    bot is that the objective, the tool allow-list, the channels and the policy
    are DATA — a second job is a second row, not a second agent implementation.

    `allowed_tool_keys` is the OUTER bound on authority. A brand may narrow it,
    a customer may narrow it further, and nothing anywhere may widen it: the
    gateway intersects all three and an empty intersection is a refusal.
    """

    __tablename__ = "ai_employee_templates"

    id = Column(String, primary_key=True, default=gen_uuid)
    # Stable machine key — `reactivation_specialist`. This is what the code
    # registry keys on, so it is unique and never renamed in place.
    key = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    job_role = Column(String, nullable=False, index=True)
    summary = Column(String, nullable=True)
    description = Column(Text, nullable=True)

    # What this job is for, in one sentence the run loop is actually given.
    default_objective = Column(Text, nullable=False, default="")
    # JSON list of tool keys from the code registry.
    allowed_tool_keys = Column(Text, nullable=True)
    # JSON list from constants.ALL_CHANNELS.
    allowed_channels = Column(Text, nullable=True)
    # JSON object: run limits, escalation triggers, refusal policy.
    default_policy = Column(Text, nullable=True)
    # JSON list of {key,label,type,help,required} — the BUSINESS questions a
    # customer answers when hiring this employee. Never prompts or model names.
    config_questions = Column(Text, nullable=True)

    # The commercial boundary, expressed without a price. T2 owns catalogue and
    # billing; this is the key T2's entitlement interface is asked about.
    entitlement_key = Column(String, nullable=True, index=True)
    # The customer FEATURE (app/services/entitlements.py) this job needs. An
    # employee whose customer lost the feature stops being runnable, which is
    # the correct direction for a gate to fail.
    required_feature = Column(String, nullable=True)

    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 2 — BRAND: what a white-label brand offers
# ═══════════════════════════════════════════════════════════════════════════

class AIBrandOffering(Base):
    """A brand's version of a platform template.

    One brand may call the same job "AI Reactivation Specialist" and another
    may call it something else entirely; both run the identical engine.
    `display_name` is the ONLY place a brand's own wording may influence what a
    customer reads. No brand is named anywhere in this file or in the engine —
    not in code and not in a comment, because an example brand in a docstring
    is how the first hard-coded one gets written.

    A brand may restrict channels and add policy. It may not add a tool the
    template does not carry, and `normalize` in the service layer enforces the
    intersection rather than trusting whatever was written here.
    """

    __tablename__ = "ai_brand_offerings"
    __table_args__ = (
        UniqueConstraint("platform_id", "template_id",
                         name="uq_ai_brand_offering_platform_template"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    template_id = Column(String, ForeignKey("ai_employee_templates.id"),
                         nullable=False, index=True)

    display_name = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    allowed_channels = Column(Text, nullable=True)       # JSON list, narrowing only
    allowed_tool_keys = Column(Text, nullable=True)      # JSON list, narrowing only
    brand_policy = Column(Text, nullable=True)           # JSON object
    entitlement_key = Column(String, nullable=True)      # overrides template's

    is_enabled = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=100)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 4 — CUSTOMER: the employees, their authority, and their work
# ═══════════════════════════════════════════════════════════════════════════

class AIEmployee(Base):
    """One AI employee working inside one customer organization.

    THE PAUSE COLUMNS ARE CHECKED AT EXECUTION TIME, NOT AT ENQUEUE TIME.
    `paused_at` being set is not a note about the past; `tools.authorize` reads
    it on every single tool call. A worker that read it once when it claimed
    the item would keep sending for the rest of its run after somebody hit
    pause, which is precisely the failure a kill switch exists to prevent.
    """

    __tablename__ = "ai_employees"
    __table_args__ = (
        Index("ix_ai_employees_org_status", "organization_id", "status"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    # Denormalized from the organization so brand policy can be resolved
    # without a join on every tool call. Kept in step by the service that
    # creates the employee; never used as the tenancy filter.
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    template_id = Column(String, ForeignKey("ai_employee_templates.id"),
                         nullable=False, index=True)
    offering_id = Column(String, ForeignKey("ai_brand_offerings.id"), nullable=True)

    name = Column(String, nullable=False)
    job_role = Column(String, nullable=False, index=True)
    # draft | active | disabled. Distinct from activation_state: `status` is
    # whether the customer has hired this employee, `activation_state` is how
    # far it is allowed to go when it works.
    status = Column(String, nullable=False, default="draft", index=True)
    activation_state = Column(String, nullable=False, default="off", index=True)

    objective = Column(Text, nullable=True)
    allowed_channels = Column(Text, nullable=True)   # JSON list
    config = Column(Text, nullable=True)             # JSON object: business answers
    operating_hours = Column(Text, nullable=True)    # JSON: {days:[0-6], start, end}
    timezone = Column(String, nullable=True, default="America/Chicago")

    daily_work_cap = Column(Integer, nullable=True)
    max_touches = Column(Integer, nullable=True)
    max_iterations = Column(Integer, nullable=True)
    max_tool_calls = Column(Integer, nullable=True)

    # Where a handoff goes. A user id is preferred; the queue name is the
    # fallback so an employee is never configured with nowhere to escalate.
    handoff_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    handoff_queue = Column(String, nullable=True)

    # Which lead population this employee may touch, as a saved selection —
    # the SAME criteria vocabulary qualification.apply_selection_filters
    # understands, so an employee cannot select on a field the platform does
    # not already allow a person to select on.
    audience_criteria = Column(Text, nullable=True)   # JSON object
    knowledge_binding = Column(Text, nullable=True)   # JSON: source ids/kinds

    paused_at = Column(DateTime, nullable=True)
    paused_by = Column(String, ForeignKey("users.id"), nullable=True)
    pause_reason = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)


class AIEmployeeAuthority(Base):
    """An explicit tool grant for one employee. DEFAULT DENY.

    The absence of a row is a refusal, not a default-allow. That is the whole
    reason this table exists rather than a JSON list on the employee: a grant
    is an auditable act with a grantor and a time, and revoking one has to be
    a row that changes rather than a string that was edited.
    """

    __tablename__ = "ai_employee_authorities"
    __table_args__ = (
        UniqueConstraint("employee_id", "tool_key",
                         name="uq_ai_employee_authority_tool"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    employee_id = Column(String, ForeignKey("ai_employees.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    tool_key = Column(String, nullable=False, index=True)
    is_allowed = Column(Boolean, nullable=False, default=True)
    constraints = Column(Text, nullable=True)        # JSON object, per-tool limits
    granted_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══════════════════════════════════════════════════════════════════════════
# THE WORK
# ═══════════════════════════════════════════════════════════════════════════

class AIWorkItem(Base):
    """One record, one employee, one objective, one state.

    THE UNIQUE CONSTRAINT IS THE DUPLICATE-ASSIGNMENT GUARD. Without it the
    same lead can be assigned to the same employee twice by two overlapping
    enqueues and then worked twice in parallel — two first-touch texts to the
    same family, from the same employee, minutes apart. A database constraint
    is the only version of that guarantee that survives two processes.

    CLAIMING IS A LEASE, NOT A FLAG. `claim_token` plus `lock_expires_at` means
    a worker that dies mid-run releases the item by simply failing to renew,
    and a worker whose lease expired cannot write its result over the worker
    that took over — every write is conditional on the token it holds.
    """

    __tablename__ = "ai_work_items"
    __table_args__ = (
        UniqueConstraint("employee_id", "subject_type", "subject_id",
                         name="uq_ai_work_item_employee_subject"),
        Index("ix_ai_work_items_org_state", "organization_id", "state"),
        Index("ix_ai_work_items_employee_state", "employee_id", "state"),
        Index("ix_ai_work_items_next_action", "state", "next_action_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    employee_id = Column(String, ForeignKey("ai_employees.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    job_key = Column(String, nullable=False, index=True)

    # `subject_type` is a string rather than a set of nullable FKs because the
    # engine is not lead-only by design: a prospecting employee's subject is an
    # imported prospect and a support employee's is a ticket. The tenancy check
    # is done by loading the subject through its own scoped loader, never by
    # trusting this pair.
    subject_type = Column(String, nullable=False, default="lead")
    subject_id = Column(String, nullable=False, index=True)

    state = Column(String, nullable=False, default="assigned", index=True)
    state_reason = Column(String, nullable=True)
    priority = Column(Integer, nullable=False, default=100)

    claim_token = Column(String, nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    lock_expires_at = Column(DateTime, nullable=True, index=True)
    claimed_by_run_id = Column(String, nullable=True)

    attempts = Column(Integer, nullable=False, default=0)
    touches = Column(Integer, nullable=False, default=0)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    next_action_at = Column(DateTime, nullable=True)
    last_action_at = Column(DateTime, nullable=True)
    last_inbound_at = Column(DateTime, nullable=True)

    eligibility_state = Column(String, nullable=True)
    eligibility_reasons = Column(Text, nullable=True)   # JSON list
    eligibility_checked_at = Column(DateTime, nullable=True)

    outcome = Column(String, nullable=True, index=True)
    outcome_detail = Column(String, nullable=True)
    terminal_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AIWorkItemEvent(Base):
    """Every state change, with who caused it and why.

    This is the state machine's own audit trail and is deliberately separate
    from `audit_log_entries`: that table's `actor_user_id` is NOT NULL and an
    AI employee is not a user. The platform-wide audit entry is still written
    for every consequential mutation — see workforce/audit.py — but the
    transition log has to be able to name a non-human actor, which is exactly
    what `actor_kind` is for.
    """

    __tablename__ = "ai_work_item_events"
    __table_args__ = (
        Index("ix_ai_work_item_events_item", "work_item_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    work_item_id = Column(String, ForeignKey("ai_work_items.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    employee_id = Column(String, ForeignKey("ai_employees.id"), nullable=True)
    from_state = Column(String, nullable=True)
    to_state = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    actor_kind = Column(String, nullable=False, default="ai_employee")
    actor_id = Column(String, nullable=True)
    run_id = Column(String, nullable=True, index=True)
    detail = Column(Text, nullable=True)        # JSON object
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AIEmployeeRun(Base):
    """ONE BOUNDED EXECUTION. There is no unbounded agent loop in this engine.

    Every run declares its objective, its ceiling and its mode before it starts,
    and the ceilings are enforced by the gateway rather than by the model
    choosing to stop. `mode` is recorded rather than inferred so an operator
    reading the history can tell a simulation from a live run without having to
    reconstruct what the flags were that day.
    """

    __tablename__ = "ai_employee_runs"
    __table_args__ = (
        Index("ix_ai_runs_org_started", "organization_id", "started_at"),
        Index("ix_ai_runs_employee_started", "employee_id", "started_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    employee_id = Column(String, ForeignKey("ai_employees.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    work_item_id = Column(String, ForeignKey("ai_work_items.id", ondelete="SET NULL"),
                          nullable=True, index=True)

    objective = Column(Text, nullable=True)
    trigger = Column(String, nullable=True)     # scheduled | inbound | manual | simulation
    mode = Column(String, nullable=False, default="simulation", index=True)
    status = Column(String, nullable=False, default="running", index=True)

    iterations = Column(Integer, nullable=False, default=0)
    tool_calls = Column(Integer, nullable=False, default=0)
    denied_tool_calls = Column(Integer, nullable=False, default=0)

    model_capability = Column(String, nullable=True)
    provider = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    estimated_cost_usd = Column(Numeric(12, 6), nullable=True)

    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    ended_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    abort_reason = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    error = Column(String, nullable=True)


class AIToolExecution(Base):
    """Every attempt to use a tool — INCLUDING THE REFUSALS.

    A refused call is recorded with the same weight as a successful one and
    that is the point: "the model tried to email a lead in another tenant and
    was refused" is the single most valuable line in this table, and a gateway
    that only logged what it allowed would never show it.

    `arguments` is written through a redactor. Nothing here may contain a
    credential, and message bodies are stored as a length and a digest rather
    than as text — see observability in workforce/audit.py.
    """

    __tablename__ = "ai_tool_executions"
    __table_args__ = (
        Index("ix_ai_tool_exec_run_seq", "run_id", "sequence"),
        Index("ix_ai_tool_exec_org_created", "organization_id", "created_at"),
        UniqueConstraint("employee_id", "idempotency_key",
                         name="uq_ai_tool_exec_idempotency"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    run_id = Column(String, ForeignKey("ai_employee_runs.id", ondelete="CASCADE"),
                    nullable=True, index=True)
    work_item_id = Column(String, ForeignKey("ai_work_items.id", ondelete="SET NULL"),
                          nullable=True, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    employee_id = Column(String, ForeignKey("ai_employees.id", ondelete="CASCADE"),
                         nullable=False, index=True)

    tool_key = Column(String, nullable=False, index=True)
    sequence = Column(Integer, nullable=False, default=0)
    arguments = Column(Text, nullable=True)         # JSON, redacted
    arguments_digest = Column(String, nullable=True)

    decision = Column(String, nullable=False, default="allowed")   # allowed | denied
    denial_code = Column(String, nullable=True, index=True)
    denial_reason = Column(String, nullable=True)

    status = Column(String, nullable=True)          # ok | error
    result_summary = Column(Text, nullable=True)
    error = Column(String, nullable=True)

    # NULL for calls that carry no idempotency requirement. The unique
    # constraint above is (employee_id, idempotency_key); on both Postgres and
    # SQLite multiple NULLs are permitted in a unique index, which is the
    # behaviour this relies on.
    idempotency_key = Column(String, nullable=True)

    target_type = Column(String, nullable=True)
    target_id = Column(String, nullable=True, index=True)
    duration_ms = Column(Integer, nullable=True)
    simulated = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AIEligibilityResult(Base):
    """The engine's answer to "may this person be contacted on this channel".

    Stored rather than recomputed because a refusal has to be explainable
    months later, to somebody who is not looking at the lead as it is today.
    """

    __tablename__ = "ai_eligibility_results"
    __table_args__ = (
        Index("ix_ai_elig_org_created", "organization_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    employee_id = Column(String, ForeignKey("ai_employees.id", ondelete="CASCADE"),
                         nullable=True, index=True)
    work_item_id = Column(String, ForeignKey("ai_work_items.id", ondelete="CASCADE"),
                          nullable=True, index=True)
    subject_type = Column(String, nullable=False, default="lead")
    subject_id = Column(String, nullable=False, index=True)
    channel = Column(String, nullable=False)

    result = Column(String, nullable=False, index=True)   # ALLOW|DENY|REQUIRES_REVIEW
    reasons = Column(Text, nullable=True)                 # JSON list of {code, detail}
    decided_by = Column(String, nullable=True)            # which layer decided
    policy_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ═══════════════════════════════════════════════════════════════════════════
# CONTEXT, HANDOFF, MEASUREMENT
# ═══════════════════════════════════════════════════════════════════════════

class AIEmployeeMemory(Base):
    """Scoped, tenant-isolated employee memory.

    NOT one global vector store. Every row carries an organization_id and a
    scope, and `memory.recall` takes both — there is no call signature that
    can read across tenants, which is a stronger guarantee than a filter every
    caller has to remember.

    `source` records WHERE a fact came from. A fact whose source is
    `lead_message` is untrusted content that happens to be stored; it never
    becomes policy, and memory.recall labels it as reported rather than known.
    """

    __tablename__ = "ai_employee_memory"
    __table_args__ = (
        UniqueConstraint("organization_id", "employee_id", "scope", "scope_id",
                         "key", name="uq_ai_memory_scope_key"),
        Index("ix_ai_memory_lookup", "organization_id", "employee_id", "scope",
              "scope_id"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    employee_id = Column(String, ForeignKey("ai_employees.id", ondelete="CASCADE"),
                         nullable=True, index=True)
    # lead | opportunity | organization | job
    scope = Column(String, nullable=False, default="lead")
    scope_id = Column(String, nullable=False, default="")
    key = Column(String, nullable=False)
    value = Column(Text, nullable=True)
    # stated_by_contact | derived_by_employee | set_by_human | platform
    source = Column(String, nullable=False, default="derived_by_employee")
    confidence = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AIHandoff(Base):
    """A person has to take this over, and here is everything they need.

    Section 17: "Do not make the human reread a 40-message transcript to
    understand what happened." The summary, the facts and the open questions
    are columns rather than a free-text blob so the queue screen can show them
    without parsing prose, and so a test can assert that a handoff arrived
    carrying a recommended next action.
    """

    __tablename__ = "ai_handoffs"
    __table_args__ = (
        Index("ix_ai_handoffs_org_status", "organization_id", "status"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    employee_id = Column(String, ForeignKey("ai_employees.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    work_item_id = Column(String, ForeignKey("ai_work_items.id", ondelete="CASCADE"),
                          nullable=True, index=True)
    subject_type = Column(String, nullable=False, default="lead")
    subject_id = Column(String, nullable=False, index=True)

    reason_code = Column(String, nullable=False, index=True)
    priority = Column(String, nullable=False, default="normal")
    summary = Column(Text, nullable=False, default="")
    known_facts = Column(Text, nullable=True)        # JSON list
    open_questions = Column(Text, nullable=True)     # JSON list
    recommended_action = Column(String, nullable=True)
    conversation_ref = Column(String, nullable=True)
    appointment_ref = Column(String, nullable=True)

    assigned_to_user_id = Column(String, ForeignKey("users.id"), nullable=True,
                                 index=True)
    assigned_queue = Column(String, nullable=True)
    status = Column(String, nullable=False, default="open", index=True)
    accepted_by = Column(String, ForeignKey("users.id"), nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AIPerformanceEntry(Base):
    """The performance ledger: one counter, one employee, one day.

    A DAY-GRAINED COUNTER RATHER THAN A DERIVED QUERY, deliberately. Counting
    appointments by re-querying work items works until a work item is deleted,
    reassigned or re-run, at which point last month's number changes. A ledger
    that only ever increments is the version an operator can trust.

    NOTHING HERE IS REVENUE. Section 27: no ROI is invented and no revenue is
    attributed without authoritative opportunity or payment data, which this
    engine does not own.
    """

    __tablename__ = "ai_performance_entries"
    __table_args__ = (
        UniqueConstraint("organization_id", "employee_id", "metric_date",
                         "metric_key", name="uq_ai_perf_day_metric"),
        Index("ix_ai_perf_employee_date", "employee_id", "metric_date"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    employee_id = Column(String, ForeignKey("ai_employees.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    metric_date = Column(String, nullable=False, index=True)   # YYYY-MM-DD, UTC
    metric_key = Column(String, nullable=False, index=True)
    value = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AISupervisorEvent(Base):
    """Something an operator should know about the workforce.

    The Supervisor observes and RECOMMENDS. It writes rows here; it does not
    act through a privileged path. Anything it wants done goes back through the
    same registered tools with the same authority checks as any other actor —
    section 21.
    """

    __tablename__ = "ai_supervisor_events"
    __table_args__ = (
        Index("ix_ai_supervisor_scope_created", "organization_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=True, index=True)
    platform_id = Column(String, ForeignKey("platforms.id"), nullable=True,
                         index=True)
    employee_id = Column(String, ForeignKey("ai_employees.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    severity = Column(String, nullable=False, default="info", index=True)
    event_code = Column(String, nullable=False, index=True)
    message = Column(String, nullable=False, default="")
    detail = Column(Text, nullable=True)             # JSON object
    recommended_action = Column(String, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ═══════════════════════════════════════════════════════════════════════════
# ACTIVATION, SHADOW, EVALUATION
# ═══════════════════════════════════════════════════════════════════════════

class AIWorkforceActivation(Base):
    """How far the workforce may go, at one scope.

    Four scopes — platform, brand, customer, employee — and the resolver takes
    the MINIMUM. A missing row is OFF, not "inherit whatever the parent said":
    an organization nobody has configured is an organization nobody decided to
    switch on.

    `kill_switch` is separate from `state` and outranks it. Dropping the stage
    to `off` and engaging the kill switch look similar and are not the same
    thing: a stage change is configuration, a kill is an incident, and the
    supervisor screen has to be able to tell them apart afterwards.
    """

    __tablename__ = "ai_workforce_activations"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id",
                         name="uq_ai_activation_scope"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    # platform | brand | customer | employee
    scope_type = Column(String, nullable=False, index=True)
    # "" for the single platform-wide row; otherwise the id at that scope.
    scope_id = Column(String, nullable=False, default="", index=True)

    state = Column(String, nullable=False, default="off")
    kill_switch = Column(Boolean, nullable=False, default=False)
    daily_cap = Column(Integer, nullable=True)
    cohort_limit = Column(Integer, nullable=True)
    reason = Column(String, nullable=True)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AIShadowRecommendation(Base):
    """What the employee WOULD have done, recorded instead of done.

    Shadow mode is the bridge between a simulator that proves the engine works
    and a controlled activation that touches real people. Nothing here is ever
    executed by the engine; the comparison columns are filled in later, by
    whatever a human actually did, so the two can be read side by side.
    """

    __tablename__ = "ai_shadow_recommendations"
    __table_args__ = (
        Index("ix_ai_shadow_org_created", "organization_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    employee_id = Column(String, ForeignKey("ai_employees.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    work_item_id = Column(String, ForeignKey("ai_work_items.id", ondelete="SET NULL"),
                          nullable=True)
    subject_type = Column(String, nullable=False, default="lead")
    subject_id = Column(String, nullable=False, index=True)

    trigger_event = Column(String, nullable=True)
    recommended_tool = Column(String, nullable=True)
    recommended_payload = Column(Text, nullable=True)      # JSON, redacted
    rationale = Column(Text, nullable=True)
    eligibility_result = Column(String, nullable=True)

    actual_action = Column(String, nullable=True)
    actual_at = Column(DateTime, nullable=True)
    comparison = Column(String, nullable=True)     # agreed | diverged | unknown
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AIEvaluationRun(Base):
    """One execution of the evaluation harness."""

    __tablename__ = "ai_evaluation_runs"

    id = Column(String, primary_key=True, default=gen_uuid)
    suite_key = Column(String, nullable=False, index=True)
    environment = Column(String, nullable=True)
    commit_ref = Column(String, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    ended_at = Column(DateTime, nullable=True)
    total = Column(Integer, nullable=False, default=0)
    passed = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    summary = Column(Text, nullable=True)          # JSON object
    triggered_by = Column(String, ForeignKey("users.id"), nullable=True)


class AIEvaluationResult(Base):
    """One evaluated case inside a run."""

    __tablename__ = "ai_evaluation_results"
    __table_args__ = (
        Index("ix_ai_eval_result_run", "run_id", "dimension"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    run_id = Column(String, ForeignKey("ai_evaluation_runs.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    case_key = Column(String, nullable=False)
    dimension = Column(String, nullable=False, index=True)
    expected = Column(Text, nullable=True)
    actual = Column(Text, nullable=True)
    passed = Column(Boolean, nullable=False, default=False)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
