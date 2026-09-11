"""AI WORKFORCE OPERATIONS — the durable objects behind the workforce's reach.

WHAT LIVES HERE AND WHAT DELIBERATELY DOES NOT.

    HERE        one attempt to communicate; the thread that gives those
                attempts a shared history across channels; the inbound events
                that arrived and where they were routed; the actions that are
                scheduled to happen later; who owns a conversation when a
                person takes it over; every consequential operation and its
                decision chain; and the counters that stop a runaway.

    NOT HERE    the AI employee, its template, its authority grants, its work
                items, its runs, its memory, its performance ledger or its
                supervisor events. Those are T6's tables (app/models/
                workforce_models.py) and this layer consumes them through
                app/services/ai_operations/contracts.py. A second AIEmployee
                would be a second authority answer, and two authority answers
                means one of them is the one nobody checks.

NO FOREIGN KEYS ACROSS THE T6 BOUNDARY, AND THAT IS THE POINT. `employee_id`,
`work_item_id` and `run_id` below are plain indexed strings, not FKs into
`ai_employees` / `ai_work_items` / `ai_employee_runs`. T6 and T7 are separate
branches with separate merge orders, and a FK to a table that does not exist
yet is a table that cannot be created at all — the operations layer would
fail to boot on any deployment where T6 had not landed first. A soft
reference costs one thing (the database will not cascade a delete for us) and
buys independence; the cascade is handled explicitly where it matters, and
every read of a T6 row goes through `contracts` with a tenancy filter anyway.

TENANCY. `organization_id` is NOT NULL on every table here. That is the same
positive assertion `leads` and `ai_work_items` make, and it is what makes a
missing filter fail loudly instead of quietly matching `IS NULL`.

JSON IN TEXT COLUMNS, not a JSON type, matching `Lead.custom_fields`,
`Organization.enabled_features` and every other structured blob in this
schema — and keeping SQLite (tests) and Postgres (production) identical.

PLAIN VARCHAR, NEVER A DATABASE ENUM. The standing rule in app/auto_migrate.py:
SQLAlchemy's SAEnum writes the Python MEMBER NAME into Postgres and this
codebase has already migrated two columns back out of database enums because
of it. A new state here is a new string and a new edge in
app/services/ai_operations/constants.ALLOWED_COMM_TRANSITIONS.
"""

from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        Numeric, String, Text, UniqueConstraint)

from app.models.models import Base, gen_uuid


# ═══════════════════════════════════════════════════════════════════════════
# CONTINUITY — one objective, one history, across every permitted channel
# ═══════════════════════════════════════════════════════════════════════════

class AIConversationThread(Base):
    """THE CONVERSATION, not the channel.

    An SMS goes out, the person replies, they ask to be called, a call
    happens, an appointment is booked, a confirmation email follows and a
    salesperson picks it up. That is ONE conversation with one person about
    one objective, and the failure this table exists to prevent is it being
    read as five unrelated ones — which is what happens when history is
    reconstructed per channel from `messages`, `email_messages` and `replies`.

    THE UNIQUE CONSTRAINT IS THE CONTINUITY GUARANTEE. One open thread per
    (organization, employee, subject). Without it two enqueues produce two
    threads for the same family and each one knows half the story, which is
    how somebody gets asked the same qualifying question twice by the same
    AI employee an hour apart.

    `closed_at` is what makes the constraint liveable: a closed thread keeps
    its row for history and a new objective opens a new thread, so the
    uniqueness is over OPEN threads in practice, enforced by the service
    layer refusing to open a second while one is open.
    """

    __tablename__ = "ai_conversation_threads"
    __table_args__ = (
        UniqueConstraint("organization_id", "employee_id", "subject_type",
                         "subject_id", "thread_seq",
                         name="uq_ai_thread_employee_subject_seq"),
        Index("ix_ai_threads_org_status", "organization_id", "status"),
        Index("ix_ai_threads_subject", "organization_id", "subject_type",
              "subject_id"),
        Index("ix_ai_threads_next_action", "status", "next_action_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    # Soft references across the T6 boundary — see the module header.
    employee_id = Column(String, nullable=True, index=True)
    work_item_id = Column(String, nullable=True, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)

    subject_type = Column(String, nullable=False, default="lead")
    subject_id = Column(String, nullable=False, index=True)
    # Monotonic per (org, employee, subject): the second objective against the
    # same family is thread 2, not a collision.
    thread_seq = Column(Integer, nullable=False, default=1)

    objective = Column(Text, nullable=True)
    job_key = Column(String, nullable=True, index=True)

    # open | closed
    status = Column(String, nullable=False, default="open", index=True)
    # The operational state of the conversation as a whole, from
    # constants.ALL_COMM_STATES. The per-attempt state lives on the
    # communication row; this is "where is this conversation".
    state = Column(String, nullable=False, default="queued", index=True)
    state_reason = Column(String, nullable=True)

    # Which channels this thread has actually used, JSON list — the cheap
    # answer to "has this person ever been emailed by this employee".
    channels_used = Column(Text, nullable=True)
    last_channel = Column(String, nullable=True)

    outbound_count = Column(Integer, nullable=False, default=0)
    inbound_count = Column(Integer, nullable=False, default=0)
    action_count = Column(Integer, nullable=False, default=0)
    consecutive_failures = Column(Integer, nullable=False, default=0)

    last_outbound_at = Column(DateTime, nullable=True)
    last_inbound_at = Column(DateTime, nullable=True)
    next_action_at = Column(DateTime, nullable=True, index=True)

    # WHO OWNS THIS CONVERSATION RIGHT NOW. NULL means the AI employee does.
    # Set means a person has taken it and the AI must not act — checked at
    # execution time on every operation, not once when work was claimed.
    human_owner_user_id = Column(String, ForeignKey("users.id"), nullable=True,
                                 index=True)
    human_owned_at = Column(DateTime, nullable=True)
    human_owner_reason = Column(String, nullable=True)

    stopped_at = Column(DateTime, nullable=True)
    stop_reason = Column(String, nullable=True, index=True)
    stopped_by_kind = Column(String, nullable=True)     # constants.ACTOR_*
    stopped_by_id = Column(String, nullable=True)

    appointment_ref = Column(String, nullable=True)
    handoff_ref = Column(String, nullable=True)
    summary = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════════════════════════════
# ONE ATTEMPT TO COMMUNICATE
# ═══════════════════════════════════════════════════════════════════════════

class AICommunication(Base):
    """One message, one call, one row, one state.

    THE IDEMPOTENCY CONSTRAINT IS THE DUPLICATE-SEND GUARD. `(organization_id,
    idempotency_key)` unique, and the key is derived from the thread, the
    channel and a digest of the content. Two workers that both claim the same
    scheduled action, or a provider webhook delivered twice, or a retry after
    a timeout that actually succeeded — all three produce the same key and the
    second one loses at the database rather than at a check somebody
    remembered to write. A unique index is the only version of that guarantee
    that survives two processes.

    THE BODY IS NOT STORED IN FULL BY DEFAULT. `body_digest` and `body_length`
    are always written; `body_preview` holds a bounded excerpt for the
    operations console. The full text lives where the platform already keeps
    it — `messages`, `email_messages`, `replies` — and duplicating it here
    would mean a second copy of every family's words with a second set of
    retention rules.
    """

    __tablename__ = "ai_communications"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key",
                         name="uq_ai_comm_idempotency"),
        Index("ix_ai_comm_thread_created", "thread_id", "created_at"),
        Index("ix_ai_comm_org_state", "organization_id", "state"),
        Index("ix_ai_comm_employee_created", "employee_id", "created_at"),
        Index("ix_ai_comm_provider_ref", "provider_message_id"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    thread_id = Column(String,
                       ForeignKey("ai_conversation_threads.id",
                                  ondelete="CASCADE"),
                       nullable=False, index=True)
    employee_id = Column(String, nullable=True, index=True)
    work_item_id = Column(String, nullable=True, index=True)
    run_id = Column(String, nullable=True, index=True)
    action_id = Column(String, nullable=True, index=True)

    subject_type = Column(String, nullable=False, default="lead")
    subject_id = Column(String, nullable=False, index=True)

    direction = Column(String, nullable=False, default="outbound", index=True)
    channel = Column(String, nullable=False, index=True)
    state = Column(String, nullable=False, default="queued", index=True)
    state_reason = Column(String, nullable=True)

    # The address of record actually used, stored so "which number did we
    # text" is answerable when a lead's phone later changes.
    to_address = Column(String, nullable=True)
    from_address = Column(String, nullable=True)

    subject_line = Column(String, nullable=True)
    body_preview = Column(String, nullable=True)
    body_digest = Column(String, nullable=True)
    body_length = Column(Integer, nullable=True)

    idempotency_key = Column(String, nullable=True)
    correlation_kind = Column(String, nullable=True)

    provider = Column(String, nullable=True)
    provider_message_id = Column(String, nullable=True)
    provider_status = Column(String, nullable=True)
    provider_outcome = Column(String, nullable=True)   # constants.P_*
    provider_error = Column(String, nullable=True)

    # SIMULATED IS A COLUMN, NOT AN INFERENCE. An operator reading this row in
    # six months must be able to tell a real send from a simulated one without
    # reconstructing what the flags were that day.
    simulated = Column(Boolean, nullable=False, default=True)

    eligibility_result = Column(String, nullable=True)
    eligibility_reasons = Column(Text, nullable=True)    # JSON list
    denial_code = Column(String, nullable=True, index=True)
    denial_reason = Column(String, nullable=True)

    attempts = Column(Integer, nullable=False, default=0)
    estimated_cost_usd = Column(Numeric(12, 6), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # Voice only. Present in full because the voice architecture is real in
    # this build even though no live call can be placed.
    voice_disposition = Column(String, nullable=True)
    voice_seconds = Column(Integer, nullable=True)
    voice_transcript_ref = Column(String, nullable=True)

    # The platform's own record of this message, where one exists —
    # messages.id, email_messages.id, replies.id. Soft, because the operations
    # row is written before the platform row exists.
    platform_record_type = Column(String, nullable=True)
    platform_record_id = Column(String, nullable=True)

    scheduled_for = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    responded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


class AICommunicationEvent(Base):
    """Every state change of one communication, with who caused it and why.

    Separate from `ai_ops_audit` on purpose: that table answers "what did the
    workforce do", this one answers "what happened to this message". A
    provider status callback arriving four minutes after the send is an event
    here and not an action there, because nobody decided anything.
    """

    __tablename__ = "ai_communication_events"
    __table_args__ = (
        Index("ix_ai_comm_events_comm", "communication_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    communication_id = Column(String,
                              ForeignKey("ai_communications.id",
                                         ondelete="CASCADE"),
                              nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    from_state = Column(String, nullable=True)
    to_state = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    actor_kind = Column(String, nullable=False, default="ai_employee")
    actor_id = Column(String, nullable=True)
    detail = Column(Text, nullable=True)                # JSON object
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ═══════════════════════════════════════════════════════════════════════════
# INBOUND
# ═══════════════════════════════════════════════════════════════════════════

class AIInboundEvent(Base):
    """Something arrived, and here is exactly where it was routed.

    WHY EVERY INBOUND EVENT IS RECORDED, INCLUDING THE UNROUTABLE ONES. An
    inbound message that could not be matched to a tenant is the single most
    dangerous object in a multi-tenant communication system: guessing costs
    somebody else's private reply appearing in the wrong customer's inbox.
    So an unresolved event is stored with `routed = False` and a reason,
    routed nowhere, and surfaced to an operator — rather than dropped, which
    would make the failure invisible, or matched by best effort, which would
    make it a breach.

    THE UNIQUE CONSTRAINT IS THE REDELIVERY GUARD. Providers retry. Twilio
    will POST the same MessageSid again after a timeout, and the current
    inbound SMS webhook has no idempotency at all — a redelivered POST creates
    a second `Reply`. `(provider, provider_event_id)` unique means the second
    delivery of the same event is a no-op here regardless.
    """

    __tablename__ = "ai_inbound_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id",
                         name="uq_ai_inbound_provider_event"),
        Index("ix_ai_inbound_org_created", "organization_id", "created_at"),
        Index("ix_ai_inbound_thread", "thread_id", "created_at"),
        Index("ix_ai_inbound_unrouted", "routed", "created_at"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    # NULLABLE, and it is the only nullable organization_id in this module.
    # An event whose tenant could not be established has no organization, and
    # writing a guess into this column would be the breach described above.
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=True, index=True)

    provider = Column(String, nullable=False, default="unknown")
    provider_event_id = Column(String, nullable=False)
    channel = Column(String, nullable=False, default="sms")

    from_address = Column(String, nullable=True)
    to_address = Column(String, nullable=True)
    body_preview = Column(String, nullable=True)
    body_digest = Column(String, nullable=True)
    body_length = Column(Integer, nullable=True)

    routed = Column(Boolean, nullable=False, default=False, index=True)
    route_reason = Column(String, nullable=True)
    thread_id = Column(String,
                       ForeignKey("ai_conversation_threads.id",
                                  ondelete="SET NULL"),
                       nullable=True, index=True)
    communication_id = Column(String, nullable=True, index=True)
    employee_id = Column(String, nullable=True, index=True)
    work_item_id = Column(String, nullable=True)
    subject_type = Column(String, nullable=True)
    subject_id = Column(String, nullable=True, index=True)
    human_owner_user_id = Column(String, nullable=True)

    # What the platform's own classifier made of it, where it ran. Stored as
    # REPORTED rather than as fact: a lead's words are untrusted content and
    # a classification of them is an opinion about untrusted content.
    classification = Column(String, nullable=True)
    is_opt_out = Column(Boolean, nullable=False, default=False)

    # Provider timestamp when supplied, so an out-of-order delivery can be
    # recognised as out of order rather than as the newest word.
    occurred_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ═══════════════════════════════════════════════════════════════════════════
# THE NEXT ACTION
# ═══════════════════════════════════════════════════════════════════════════

class AIScheduledAction(Base):
    """What should happen next, when, and why — re-evaluated before it runs.

    A SCHEDULE CREATED WHILE ELIGIBLE DOES NOT GUARANTEE ELIGIBILITY WHEN THE
    TIME ARRIVES. That sentence is the entire design of this table. The row
    records an INTENT; the worker that picks it up runs the whole gate chain
    again — opt-out, DNC, human ownership, activation, caps, hours — and a
    follow-up scheduled last night is refused this morning if a STOP arrived
    at 2am. Nothing here is a licence to act.

    CLAIMING IS A LEASE, NOT A FLAG. `claim_token` plus `lock_expires_at`
    means a worker that dies mid-execution releases the action by failing to
    renew, and a worker whose lease expired cannot write its result over the
    worker that took over: every write is conditional on the token it holds.
    """

    __tablename__ = "ai_scheduled_actions"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key",
                         name="uq_ai_scheduled_action_idempotency"),
        Index("ix_ai_sched_due", "status", "scheduled_for"),
        Index("ix_ai_sched_org_status", "organization_id", "status"),
        Index("ix_ai_sched_thread", "thread_id", "scheduled_for"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    thread_id = Column(String,
                       ForeignKey("ai_conversation_threads.id",
                                  ondelete="CASCADE"),
                       nullable=False, index=True)
    employee_id = Column(String, nullable=True, index=True)
    work_item_id = Column(String, nullable=True, index=True)

    subject_type = Column(String, nullable=False, default="lead")
    subject_id = Column(String, nullable=False, index=True)

    operation = Column(String, nullable=False)          # constants.OP_*
    channel = Column(String, nullable=True)
    payload = Column(Text, nullable=True)               # JSON, redacted
    reason = Column(String, nullable=True)

    # pending | claimed | executed | skipped | cancelled | failed
    status = Column(String, nullable=False, default="pending", index=True)
    status_detail = Column(String, nullable=True)

    scheduled_for = Column(DateTime, nullable=False, index=True)
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)

    claim_token = Column(String, nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    lock_expires_at = Column(DateTime, nullable=True, index=True)

    idempotency_key = Column(String, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    result_action_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)
    created_by_kind = Column(String, nullable=False, default="ai_employee")
    created_by_id = Column(String, nullable=True)


# ═══════════════════════════════════════════════════════════════════════════
# HUMAN OWNERSHIP
# ═══════════════════════════════════════════════════════════════════════════

class AIHumanOwnership(Base):
    """A person has this conversation. The AI does not.

    OWNERSHIP IS EXPLICIT, TIMED AND AUDITABLE — not a boolean on the thread
    and not an inference from a handoff existing. A handoff is a request; this
    is the fact that somebody answered it. The distinction matters because the
    AI must stop on the fact, not on the request: a handoff nobody picked up
    leaves the conversation with the employee (which is why unclaimed handoffs
    are a supervisor signal), and an ownership row stops it immediately.

    RELEASING IS A NEW ROW STATE, NOT A DELETE, so "who had this and when"
    survives the person handing it back.
    """

    __tablename__ = "ai_human_ownership"
    __table_args__ = (
        Index("ix_ai_ownership_thread", "thread_id", "created_at"),
        Index("ix_ai_ownership_active", "organization_id", "is_active"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    thread_id = Column(String,
                       ForeignKey("ai_conversation_threads.id",
                                  ondelete="CASCADE"),
                       nullable=False, index=True)
    subject_type = Column(String, nullable=False, default="lead")
    subject_id = Column(String, nullable=False, index=True)
    employee_id = Column(String, nullable=True, index=True)
    handoff_ref = Column(String, nullable=True)

    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    reason_code = Column(String, nullable=True)
    note = Column(Text, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    taken_at = Column(DateTime, default=datetime.utcnow)
    released_at = Column(DateTime, nullable=True)
    released_by = Column(String, ForeignKey("users.id"), nullable=True)
    # Whether the AI employee may resume after release. Default False: a
    # person who took a conversation over and handed it back has not thereby
    # re-authorized automated outreach to that family.
    ai_may_resume = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ═══════════════════════════════════════════════════════════════════════════
# EVERY CONSEQUENTIAL OPERATION
# ═══════════════════════════════════════════════════════════════════════════

class AIOpsAction(Base):
    """One requested operation and the complete chain of answers it got.

    THIS IS THE IDEMPOTENCY SPINE FOR EVERYTHING THAT IS NOT A MESSAGE.
    Booking, rescheduling, pipeline mutation, handoff creation, task
    creation — each one is a row here with a key, so a retried worker books
    one appointment rather than two. Channel sends get a row here AND a
    communication row: this one records the decision, that one the delivery.

    WHY NOT REUSE T6's `ai_tool_executions`. It is the right table and this
    layer writes to it too — `contracts.mirror_tool_execution` does exactly
    that when T6 is deployed. It cannot be the only record, for two reasons
    that are both about honesty: it does not exist on a deployment where T6
    has not merged, and it has no columns for the provider, the eligibility
    answer or the state transition this layer is responsible for. Where both
    exist, `tool_execution_id` links them and T6's row remains authoritative
    for the AUTHORITY question.
    """

    __tablename__ = "ai_ops_actions"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key",
                         name="uq_ai_ops_action_idempotency"),
        Index("ix_ai_ops_action_org_created", "organization_id", "created_at"),
        Index("ix_ai_ops_action_thread", "thread_id", "created_at"),
        Index("ix_ai_ops_action_decision", "organization_id", "decision"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    thread_id = Column(String,
                       ForeignKey("ai_conversation_threads.id",
                                  ondelete="SET NULL"),
                       nullable=True, index=True)
    employee_id = Column(String, nullable=True, index=True)
    work_item_id = Column(String, nullable=True, index=True)
    run_id = Column(String, nullable=True, index=True)
    communication_id = Column(String, nullable=True, index=True)
    tool_execution_id = Column(String, nullable=True)

    operation = Column(String, nullable=False, index=True)
    tool_key = Column(String, nullable=True, index=True)
    channel = Column(String, nullable=True)
    subject_type = Column(String, nullable=True)
    subject_id = Column(String, nullable=True, index=True)

    # allowed | denied
    decision = Column(String, nullable=False, default="denied", index=True)
    denial_code = Column(String, nullable=True, index=True)
    denial_reason = Column(String, nullable=True)
    # WHICH GATE ANSWERED. "denied" tells an operator nothing; "the activation
    # stage refused it" tells them what to change.
    decided_by = Column(String, nullable=True)
    eligibility_result = Column(String, nullable=True)
    activation_state = Column(String, nullable=True)

    status = Column(String, nullable=True)              # ok | error | skipped
    result_summary = Column(Text, nullable=True)
    error = Column(String, nullable=True)

    arguments_digest = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=True)
    correlation_kind = Column(String, nullable=True)

    provider = Column(String, nullable=True)
    simulated = Column(Boolean, nullable=False, default=True)
    estimated_cost_usd = Column(Numeric(12, 6), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    actor_kind = Column(String, nullable=False, default="ai_employee")
    actor_id = Column(String, nullable=True)
    human_involved = Column(Boolean, nullable=False, default=False)
    next_action = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AIOpsAuditEntry(Base):
    """THE OPERATIONAL AUDIT. Twelve questions, one row, always written.

    WHO acted, which employee, for which tenant, about which contact, under
    which objective, which policy allowed it, which tool, which provider,
    what happened, what state changed, what it cost, was a human involved,
    and what is next.

    WHY NOT `audit_log_entries`. That table's `actor_user_id` is NOT NULL and
    an AI employee is not a user — the same reason T6 keeps its own
    transition log. Platform-wide audit entries are still written for
    mutations a PERSON makes through the operations console (pause, takeover,
    release), through the existing `log_action` helper, so a human action
    appears where humans' actions are looked for.

    THE AUDIT IS WRITTEN BEFORE THE MIRROR AND BEFORE THE PROVIDER RESULT IS
    KNOWN, then updated. An action that crashed mid-flight leaves a row
    saying it was attempted, which is the row an incident actually needs.
    """

    __tablename__ = "ai_ops_audit"
    __table_args__ = (
        Index("ix_ai_ops_audit_org_created", "organization_id", "created_at"),
        Index("ix_ai_ops_audit_employee", "employee_id", "created_at"),
        Index("ix_ai_ops_audit_event", "organization_id", "event_code"),
        Index("ix_ai_ops_audit_subject", "subject_type", "subject_id"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=True, index=True)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="SET NULL"),
                         nullable=True, index=True)

    event_code = Column(String, nullable=False, index=True)
    severity = Column(String, nullable=False, default="info")

    actor_kind = Column(String, nullable=False, default="ai_employee")
    actor_id = Column(String, nullable=True)
    actor_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    employee_id = Column(String, nullable=True, index=True)

    thread_id = Column(String, nullable=True, index=True)
    action_id = Column(String, nullable=True, index=True)
    communication_id = Column(String, nullable=True)
    work_item_id = Column(String, nullable=True)
    subject_type = Column(String, nullable=True)
    subject_id = Column(String, nullable=True)

    operation = Column(String, nullable=True)
    tool_key = Column(String, nullable=True)
    channel = Column(String, nullable=True)
    provider = Column(String, nullable=True)
    decision = Column(String, nullable=True)
    denial_code = Column(String, nullable=True)
    authority = Column(String, nullable=True)       # which policy allowed it
    activation_state = Column(String, nullable=True)
    eligibility_result = Column(String, nullable=True)

    state_from = Column(String, nullable=True)
    state_to = Column(String, nullable=True)
    outcome = Column(String, nullable=True)
    human_involved = Column(Boolean, nullable=False, default=False)
    simulated = Column(Boolean, nullable=False, default=True)
    estimated_cost_usd = Column(Numeric(12, 6), nullable=True)
    next_action = Column(String, nullable=True)

    message = Column(String, nullable=True)
    detail = Column(Text, nullable=True)            # JSON object, redacted
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ═══════════════════════════════════════════════════════════════════════════
# RUNAWAY PROTECTION
# ═══════════════════════════════════════════════════════════════════════════

class AIOpsCounter(Base):
    """One counter, one scope, one day. The ledger the ceilings read.

    A COUNTER ROW RATHER THAN A COUNT QUERY, for the same reason T6's
    performance ledger is a row: counting today's sends by re-querying
    communications works until a row is deleted or a thread is re-run, at
    which point a cap that was reached is silently un-reached. An
    only-ever-incrementing counter is the version a ceiling can be enforced
    against.

    `scope_type` is one of organization | employee | thread | channel, and
    `scope_id` is the id at that scope — so "this employee has sent 40 texts
    today" and "this organization has spent $12 today" are the same shape.
    """

    __tablename__ = "ai_ops_counters"
    __table_args__ = (
        UniqueConstraint("organization_id", "scope_type", "scope_id",
                         "metric_date", "metric_key",
                         name="uq_ai_ops_counter_scope_day_metric"),
        Index("ix_ai_ops_counter_lookup", "organization_id", "scope_type",
              "scope_id", "metric_date"),
    )

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    scope_type = Column(String, nullable=False)
    scope_id = Column(String, nullable=False, default="")
    metric_date = Column(String, nullable=False, index=True)   # YYYY-MM-DD UTC
    metric_key = Column(String, nullable=False, index=True)
    value = Column(Integer, nullable=False, default=0)
    value_usd = Column(Numeric(12, 6), nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)
