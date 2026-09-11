"""AI WORKFORCE INTELLIGENCE (T9) - what management state looks like on disk.

T9 SITS ABOVE T6, T7 AND T8 AND OWNS NONE OF THEIR TRUTH.

The authoritative records stay where they are: `ai_work_items` and
`ai_employee_runs` (T6), `ai_conversation_threads` and `ai_ops_actions` (T7),
`ai_employee_deployments` (T8). Nothing in this file duplicates one of them,
and nothing in this file is read by the engine.

WHAT IS ACTUALLY STORED HERE IS MANAGEMENT STATE - the small set of facts that
exist only because a PERSON is managing a workforce and that cannot be derived
from the engine's own tables:

    * whether somebody has acknowledged or resolved an alert, and when
    * what a reviewer decided, and why
    * the materialised answer to an expensive question, with the moment it
      was computed so a screen can say whether it is stale
    * a contradiction between two authoritative systems, which by definition
      neither of them records
    * a management action taken through T9, and which authority performed it
    * whether T9's own passes ran

EVERYTHING ELSE IS DERIVED ON READ. The Needs Attention queue's CONTENT is
recomputed from authoritative records on every pass; only its lifecycle -
first seen, acknowledged, resolved - is persisted, keyed by a deterministic
dedup key. That split is deliberate: a queue that stored its own copy of "this
objective is blocked" would keep saying so after the objective unblocked, and
the screen would be confidently wrong.

TENANCY. `organization_id` is NOT NULL on every customer-scoped table here,
the same positive assertion T6, T7 and T8 make, so a missing filter fails
loudly instead of quietly matching IS NULL. The only table without one is
`ai_intelligence_runs`, whose scope is a (scope_type, scope_id) pair because a
platform-wide pass genuinely has no single organization.

JSON IN TEXT COLUMNS, not a JSON type - matching every other structured blob
in this schema, so SQLite (tests) and Postgres (production) behave identically.

PLAIN LOWERCASE STRINGS IN VARCHAR, never a database enum. The standing rule
recorded in app/auto_migrate.py: SQLAlchemy's SAEnum writes the Python MEMBER
NAME into Postgres, and this codebase has already migrated two columns back
out of database enums because of it.
"""

from datetime import datetime

from sqlalchemy import (Column, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)

from app.models.models import Base, gen_uuid


# ===========================================================================
# MATERIALISED READ MODELS
# ===========================================================================

class AIIntelligenceReadModel(Base):
    """One computed answer, for one scope, with the moment it was computed.

    WHY MATERIALISE AT ALL. Section 23: a manager's overview touches work
    items, runs, tool executions, threads, communications, actions, handoffs
    and deployments. Computing that per page load, per employee, for a
    customer with a large lead population is the N+1 the brief forbids.

    WHY THE WATERMARK COLUMN. Section 13: a screen must know whether what it
    is showing is current. `computed_at` says when the pass ran;
    `source_watermark` says the newest source event the pass actually saw. The
    two together are what lets a reader distinguish "nothing has happened
    since" from "the aggregation has not run for six hours", which a single
    timestamp cannot.

    THE UNIQUE KEY INCLUDES SCOPE_TYPE AND SCOPE_ID, and both are part of every
    lookup. An aggregate cache keyed on view alone is a cross-tenant leak with
    a nice name - section 17 says so, and this constraint is where that is
    prevented rather than remembered.
    """
    __tablename__ = "ai_intelligence_read_models"

    id = Column(String, primary_key=True, default=gen_uuid)
    scope_type = Column(String, nullable=False, index=True)   # constants.SCOPE_*
    scope_id = Column(String, nullable=False, default="", index=True)
    view_key = Column(String, nullable=False, index=True)
    window_key = Column(String, nullable=False, default="30d")

    payload = Column(Text, nullable=True)                     # JSON object
    payload_digest = Column(String, nullable=True)

    computed_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                         index=True)
    compute_ms = Column(Integer, nullable=True)
    # The newest authoritative event this computation actually observed.
    source_watermark = Column(DateTime, nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    generation = Column(Integer, nullable=False, default=1)

    # A pass that failed leaves the LAST GOOD payload in place and records the
    # failure here. A screen then shows old-but-true with a stale marker,
    # rather than an empty dashboard that reads as "nothing is happening".
    last_error = Column(String, nullable=True)
    last_error_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id", "view_key", "window_key",
                         name="uq_ai_read_model_scope_view"),
        Index("ix_ai_read_model_lookup", "scope_type", "scope_id", "view_key"),
    )


# ===========================================================================
# THE NEEDS-YOUR-ATTENTION QUEUE
# ===========================================================================

class AIAttentionItem(Base):
    """One thing a manager should look at, and what happened to it.

    THE CONTENT IS DERIVED; THE LIFECYCLE IS STORED. Every pass recomputes the
    queue from authoritative records and upserts on `dedup_key`. A row whose
    condition has cleared is closed by the pass itself with
    `state = 'cleared'`, so "resolved because somebody fixed it" and "resolved
    because somebody pressed a button" stay distinguishable.

    DEDUP_KEY IS THE WHOLE ANTI-NOISE MECHANISM. Section 2: do not create 50
    alerts from one root problem. The key is deterministic and derived from the
    ROOT condition - one provider outage produces one key, whatever number of
    objectives it stalled - and `rolled_up_count` carries how many underlying
    records it represents.
    """
    __tablename__ = "ai_attention_items"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    platform_id = Column(String, ForeignKey("platforms.id",
                                            ondelete="SET NULL"),
                         nullable=True, index=True)
    employee_id = Column(String, nullable=True, index=True)
    deployment_id = Column(String, nullable=True, index=True)

    dedup_key = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False, index=True)          # constants.A_*
    severity = Column(String, nullable=False, default="normal", index=True)
    priority = Column(Integer, nullable=False, default=100, index=True)

    title = Column(String, nullable=False, default="")
    why = Column(Text, nullable=True)
    recommended_action = Column(String, nullable=True)

    subject_type = Column(String, nullable=True)
    subject_id = Column(String, nullable=True, index=True)
    objective_ref = Column(String, nullable=True)

    # WHERE THE CLAIM COMES FROM. Section 2 requires an authoritative source on
    # every item, and this is it: the table and the row id a reader can open.
    source_kind = Column(String, nullable=True)
    source_id = Column(String, nullable=True)
    drilldown = Column(Text, nullable=True)                    # JSON object
    evidence = Column(Text, nullable=True)                     # JSON object
    rolled_up_count = Column(Integer, nullable=False, default=1)

    state = Column(String, nullable=False, default="open", index=True)
    first_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                           index=True)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, ForeignKey("users.id"), nullable=True)
    resolution_note = Column(String, nullable=True)
    cleared_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("organization_id", "dedup_key",
                         name="uq_ai_attention_org_dedup"),
        Index("ix_ai_attention_open", "organization_id", "state", "priority"),
    )


# ===========================================================================
# SUPERVISOR INTELLIGENCE - findings, with their evidence kept separate
# ===========================================================================

class AISupervisorFinding(Base):
    """An evidence-backed observation, with fact and interpretation apart.

    THE FOUR COLUMNS ARE THE POINT. Section 5: separate FACT, CALCULATED
    METRIC, AI INTERPRETATION, RECOMMENDATION and UNKNOWN, and never present
    model interpretation as database fact. Storing them in one blob of prose
    would make that separation a convention; storing them in four columns
    makes it a property of the schema, and a renderer cannot accidentally
    print an interpretation where a fact belongs.

    `interpretation` may be empty and often is. A finding with facts, metrics
    and a recommendation and no interpretation is a complete finding.
    """
    __tablename__ = "ai_supervisor_findings"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    platform_id = Column(String, ForeignKey("platforms.id",
                                            ondelete="SET NULL"),
                         nullable=True, index=True)
    employee_id = Column(String, nullable=True, index=True)

    finding_key = Column(String, nullable=False, index=True)
    code = Column(String, nullable=False, index=True)          # constants.F_*
    severity = Column(String, nullable=False, default="info", index=True)
    headline = Column(String, nullable=False, default="")

    facts = Column(Text, nullable=True)          # JSON list of FactRef dicts
    metrics = Column(Text, nullable=True)        # JSON list of MetricRef dicts
    interpretation = Column(Text, nullable=True)   # labelled, never a fact
    unknowns = Column(Text, nullable=True)       # JSON list of strings
    confidence = Column(String, nullable=True)   # high | medium | low | None

    why_it_matters = Column(String, nullable=True)
    recommended_action = Column(String, nullable=True)
    affected_scope = Column(String, nullable=True)
    drilldown = Column(Text, nullable=True)                    # JSON object

    window_key = Column(String, nullable=False, default="30d")
    computed_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                         index=True)

    state = Column(String, nullable=False, default="open", index=True)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("organization_id", "finding_key",
                         name="uq_ai_finding_org_key"),
        Index("ix_ai_finding_open", "organization_id", "state", "severity"),
    )


# ===========================================================================
# HUMAN REVIEW
# ===========================================================================

class AIReviewDecision(Base):
    """What a person decided about one thing that required a person.

    THE QUEUE IS NOT STORED. Items requiring review are already recorded
    authoritatively - `ai_work_items.state = 'needs_review'`,
    `ai_eligibility_results.result = 'REQUIRES_REVIEW'`,
    `ai_conversation_threads.state = 'review_required'`, an unaccepted
    handoff. T9 renders those; what T9 adds is the DECISION, which nothing
    else records.

    DECISIONS ARE APPEND-ONLY. A reviewer who changes their mind adds a second
    row; the first is not edited. Section 8 requires review decisions to be
    audited, and an audit you can overwrite is a note.
    """
    __tablename__ = "ai_review_decisions"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    employee_id = Column(String, nullable=True, index=True)

    review_key = Column(String, nullable=False, index=True)
    source_kind = Column(String, nullable=False)   # constants.REVIEW_SOURCE_*
    source_id = Column(String, nullable=False, index=True)
    reason_code = Column(String, nullable=True)

    decision = Column(String, nullable=False, index=True)   # constants.RD_*
    note = Column(Text, nullable=True)
    # What the decision CAUSED, named by the authority that performed it.
    # Empty when the decision was recorded and nothing was actioned.
    effect = Column(String, nullable=True)
    effect_detail = Column(Text, nullable=True)

    decided_by = Column(String, ForeignKey("users.id"), nullable=True,
                        index=True)
    decided_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                        index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_ai_review_org_source", "organization_id", "source_kind",
              "source_id"),
    )


# ===========================================================================
# RECONCILIATION
# ===========================================================================

class AIReconciliationFinding(Base):
    """Two authoritative systems disagreeing, recorded rather than repaired.

    SECTION 19 IS EXPLICIT: do not silently repair commercial or authority
    state. A deployment that says ACTIVE while T6's activation says OFF is not
    a row to fix from here - fixing it from here would make T9 a second
    control plane, which is the one thing the brief forbids throughout. So the
    contradiction is SURFACED, with both sides quoted and the system that owns
    the remedy named.
    """
    __tablename__ = "ai_reconciliation_findings"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    platform_id = Column(String, ForeignKey("platforms.id",
                                            ondelete="SET NULL"),
                         nullable=True, index=True)
    employee_id = Column(String, nullable=True, index=True)

    check_key = Column(String, nullable=False, index=True)   # constants.RC_*
    contradiction_key = Column(String, nullable=False, index=True)
    severity = Column(String, nullable=False, default="warning", index=True)
    statement = Column(String, nullable=False, default="")

    left_source = Column(String, nullable=True)
    left_id = Column(String, nullable=True)
    left_value = Column(String, nullable=True)
    right_source = Column(String, nullable=True)
    right_id = Column(String, nullable=True)
    right_value = Column(String, nullable=True)

    # WHICH SYSTEM OWNS THE REMEDY. Never "t9".
    remediation_owner = Column(String, nullable=True)
    remediation_hint = Column(String, nullable=True)

    state = Column(String, nullable=False, default="open", index=True)
    first_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by = Column(String, ForeignKey("users.id"), nullable=True)
    cleared_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "contradiction_key",
                         name="uq_ai_reconcile_org_key"),
    )


# ===========================================================================
# MANAGEMENT ACTIONS - the receipt, never the authority
# ===========================================================================

class AIManagementAction(Base):
    """A management action requested through T9, and who actually performed it.

    `authority_path` IS THE INTERESTING COLUMN. It records the module that
    carried the action out - `ai_deployment.lifecycle.pause`,
    `ai_operations.stop.take_over`, `workforce.handoff.accept`. T9 never
    mutates an employee, a deployment, a thread or an entitlement itself; it
    calls the system that owns that state, and this column is how an auditor
    checks that claim instead of taking it on trust.

    REFUSALS ARE RECORDED TOO. An action the owning system refused is a fact a
    manager needs ("I pressed pause and nothing happened, why") and the
    refusal code from that system is written here verbatim.
    """
    __tablename__ = "ai_management_actions"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    platform_id = Column(String, ForeignKey("platforms.id",
                                            ondelete="SET NULL"),
                         nullable=True, index=True)
    employee_id = Column(String, nullable=True, index=True)
    deployment_id = Column(String, nullable=True, index=True)

    action = Column(String, nullable=False, index=True)        # constants.M_*
    target_kind = Column(String, nullable=True)
    target_id = Column(String, nullable=True, index=True)
    reason = Column(String, nullable=True)

    authority_path = Column(String, nullable=True)
    outcome = Column(String, nullable=False, default="performed", index=True)
    refusal_code = Column(String, nullable=True, index=True)
    detail = Column(Text, nullable=True)                       # JSON object

    # WHICH ATTENTION ITEM OR FINDING THIS CAME FROM, when it came from one.
    origin_kind = Column(String, nullable=True)
    origin_id = Column(String, nullable=True)

    requested_by = Column(String, ForeignKey("users.id"), nullable=True,
                          index=True)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                          index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_ai_mgmt_org_action", "organization_id", "action",
              "requested_at"),
    )


# ===========================================================================
# T9'S OWN OBSERVABILITY
# ===========================================================================

class AIIntelligenceRun(Base):
    """Whether T9 itself ran, and what it could not do.

    SECTION 24: do not allow silent failure to look like zero activity. An
    aggregation pass that raised and was swallowed produces a dashboard full
    of zeros that is indistinguishable from a quiet day, and an operator acts
    on the zeros. Every pass writes a row here whether it succeeded or not,
    and the read models it feeds carry the failure forward as staleness.

    NO organization_id. A platform-wide pass has no single organization, and
    inventing one to satisfy a column would be worse than the pair of scope
    columns used everywhere else in this file.
    """
    __tablename__ = "ai_intelligence_runs"

    id = Column(String, primary_key=True, default=gen_uuid)
    scope_type = Column(String, nullable=False, index=True)
    scope_id = Column(String, nullable=False, default="", index=True)
    pass_key = Column(String, nullable=False, index=True)      # constants.P_*

    status = Column(String, nullable=False, default="ok", index=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                        index=True)
    ended_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    organizations_scanned = Column(Integer, nullable=False, default=0)
    items_written = Column(Integer, nullable=False, default=0)
    items_cleared = Column(Integer, nullable=False, default=0)
    findings_written = Column(Integer, nullable=False, default=0)
    contradictions_found = Column(Integer, nullable=False, default=0)
    scope_refusals = Column(Integer, nullable=False, default=0)

    error = Column(String, nullable=True)
    detail = Column(Text, nullable=True)                       # JSON object
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_ai_intel_run_scope", "scope_type", "scope_id", "pass_key",
              "started_at"),
    )
