"""THE OPERATIONS VOCABULARY, DEFINED ONCE.

Every operational state, outcome, refusal code and stop reason this layer can
produce lives here. The rule is the one T6's constants module states and for
the same reason: two spellings of a state is how a state machine grows an
edge nobody wrote, and a comparison between `needs_review` and `NEEDS_REVIEW`
is silently False forever.

WHY THESE ARE NOT T6'S STATES. T6's work-item states answer "where is this
RECORD in this employee's job" — assigned, working, waiting_for_response,
appointment_booked. The states here answer "where is this ONE ATTEMPT TO
COMMUNICATE" — queued, sending, sent, delivered, failed. A work item lives
for days and owns many communications; a communication lives for seconds and
belongs to exactly one work item. Collapsing them would mean a failed SMS and
an exhausted lead were the same kind of fact.

Plain lowercase strings in VARCHAR columns, never a database enum — the
standing rule recorded in app/auto_migrate.py, because SQLAlchemy's SAEnum
writes the Python MEMBER NAME into Postgres and this codebase has already had
to migrate columns back out of database enums twice.
"""

# ── DIRECTION ───────────────────────────────────────────────────────────────
OUTBOUND = "outbound"
INBOUND = "inbound"
DIRECTIONS = (OUTBOUND, INBOUND)


# ── CHANNELS ────────────────────────────────────────────────────────────────
#
# The spelling matches T6's constants and app/services/compliance_service.py
# exactly. A third spelling would answer eligibility against the wrong channel.
CHANNEL_SMS = "sms"
CHANNEL_EMAIL = "email"
CHANNEL_VOICE = "voice"
ALL_CHANNELS = (CHANNEL_SMS, CHANNEL_EMAIL, CHANNEL_VOICE)

# Which platform FEATURE key each channel needs. app/services/entitlements.py
# owns these keys; they are not re-invented here.
CHANNEL_FEATURE = {
    CHANNEL_SMS: "sms",
    CHANNEL_EMAIL: "email",
    CHANNEL_VOICE: "voice",
}


# ── COMMUNICATION STATES ────────────────────────────────────────────────────
#
# ONE ATTEMPT, ONE ROW, ONE STATE. The names are deliberately operational
# rather than provider-flavoured: "delivered" means the provider told us it
# arrived, not that Twilio used the word.
QUEUED = "queued"
ELIGIBILITY_CHECK = "eligibility_check"
READY = "ready"
SENDING = "sending"
SENT = "sent"
DELIVERED = "delivered"
WAITING_FOR_RESPONSE = "waiting_for_response"
RESPONSE_RECEIVED = "response_received"
PROCESSING = "processing"
FOLLOWUP_SCHEDULED = "followup_scheduled"
APPOINTMENT_BOOKED = "appointment_booked"
HANDOFF_REQUIRED = "handoff_required"
HUMAN_OWNED = "human_owned"
COMPLETED = "completed"
STOPPED = "stopped"
BLOCKED = "blocked"
FAILED = "failed"
REVIEW_REQUIRED = "review_required"

ALL_COMM_STATES = (
    QUEUED, ELIGIBILITY_CHECK, READY, SENDING, SENT, DELIVERED,
    WAITING_FOR_RESPONSE, RESPONSE_RECEIVED, PROCESSING, FOLLOWUP_SCHEDULED,
    APPOINTMENT_BOOKED, HANDOFF_REQUIRED, HUMAN_OWNED, COMPLETED, STOPPED,
    BLOCKED, FAILED, REVIEW_REQUIRED,
)

# TERMINAL MEANS THIS ATTEMPT IS OVER.
#
# `failed` is NOT terminal: a provider timeout is retried by the follow-up
# engine after a fresh eligibility check. `blocked` and `stopped` ARE
# terminal, because both mean a gate said no and a retry would be the same
# gate asking the same question with the same answer.
TERMINAL_COMM_STATES = frozenset({
    COMPLETED, STOPPED, BLOCKED, APPOINTMENT_BOOKED, HUMAN_OWNED,
})

# THE TRANSITION TABLE IS THE STATE MACHINE.
#
# An edge that is not here cannot be taken. `comm_state.transition` refuses
# rather than logging and continuing — a silent illegal transition is how a
# communication that was BLOCKED gets sent anyway.
ALLOWED_COMM_TRANSITIONS = {
    QUEUED: {ELIGIBILITY_CHECK, BLOCKED, STOPPED, FAILED, REVIEW_REQUIRED,
             HUMAN_OWNED},
    ELIGIBILITY_CHECK: {READY, BLOCKED, REVIEW_REQUIRED, STOPPED, FAILED,
                        HUMAN_OWNED},
    READY: {SENDING, BLOCKED, STOPPED, FAILED, REVIEW_REQUIRED, HUMAN_OWNED},
    # SENDING is the in-flight state and the reason a duplicate worker cannot
    # send twice: the claim that moves a row out of READY is conditional.
    SENDING: {SENT, FAILED, BLOCKED, STOPPED},
    SENT: {DELIVERED, WAITING_FOR_RESPONSE, FAILED, RESPONSE_RECEIVED,
           STOPPED, HUMAN_OWNED, COMPLETED},
    DELIVERED: {WAITING_FOR_RESPONSE, RESPONSE_RECEIVED, STOPPED, HUMAN_OWNED,
                COMPLETED, FAILED},
    WAITING_FOR_RESPONSE: {RESPONSE_RECEIVED, FOLLOWUP_SCHEDULED, STOPPED,
                           HANDOFF_REQUIRED, HUMAN_OWNED, COMPLETED,
                           REVIEW_REQUIRED},
    RESPONSE_RECEIVED: {PROCESSING, STOPPED, HANDOFF_REQUIRED, HUMAN_OWNED,
                        REVIEW_REQUIRED, COMPLETED},
    PROCESSING: {FOLLOWUP_SCHEDULED, APPOINTMENT_BOOKED, HANDOFF_REQUIRED,
                 REVIEW_REQUIRED, COMPLETED, STOPPED, HUMAN_OWNED, FAILED},
    FOLLOWUP_SCHEDULED: {QUEUED, STOPPED, HUMAN_OWNED, COMPLETED,
                         HANDOFF_REQUIRED, REVIEW_REQUIRED},
    HANDOFF_REQUIRED: {HUMAN_OWNED, STOPPED, COMPLETED},
    # A failure may be retried — through QUEUED, so the whole gate chain runs
    # again. It may never go straight back to SENDING.
    FAILED: {QUEUED, STOPPED, REVIEW_REQUIRED, HUMAN_OWNED, COMPLETED},
    REVIEW_REQUIRED: {QUEUED, STOPPED, HUMAN_OWNED, HANDOFF_REQUIRED,
                      COMPLETED},
    # Terminal. Empty on purpose: a blocked send does not re-enter the queue
    # because a retry loop wanted it to.
    APPOINTMENT_BOOKED: set(),
    HUMAN_OWNED: set(),
    COMPLETED: set(),
    STOPPED: set(),
    BLOCKED: set(),
}

# ── THE CONVERSATION'S OWN TRANSITIONS ──────────────────────────────────────
#
# TWO MACHINES, ONE VOCABULARY. The table above governs ONE MESSAGE, where
# the edges are narrow on purpose — `ready -> sending` is a claim, and a
# message that reached `blocked` must never reach `sent`. The table below
# governs THE CONVERSATION, where the legal moves are genuinely wider: a
# conversation can go from "queued" straight to "appointment booked" when the
# person rang in and booked, and from "waiting" straight to "handoff" when
# they asked for someone.
#
# Sharing one table would have forced a choice between a message machine
# loose enough to allow a conversation's moves — which is how a blocked send
# gets sent — and a conversation machine so tight that ordinary lifecycles
# have to be expressed as illegal transitions the code then works around.
# Two tables, one vocabulary, and `thread_state` reads this one.
ALLOWED_THREAD_TRANSITIONS = {
    QUEUED: {ELIGIBILITY_CHECK, READY, SENDING, SENT, WAITING_FOR_RESPONSE,
             RESPONSE_RECEIVED, PROCESSING, FOLLOWUP_SCHEDULED,
             APPOINTMENT_BOOKED, HANDOFF_REQUIRED, HUMAN_OWNED, COMPLETED,
             STOPPED, BLOCKED, FAILED, REVIEW_REQUIRED},
    ELIGIBILITY_CHECK: {READY, WAITING_FOR_RESPONSE, BLOCKED, STOPPED,
                        REVIEW_REQUIRED, HUMAN_OWNED, FAILED, QUEUED},
    READY: {SENDING, SENT, WAITING_FOR_RESPONSE, BLOCKED, STOPPED,
            REVIEW_REQUIRED, HUMAN_OWNED, FAILED, QUEUED},
    SENDING: {SENT, WAITING_FOR_RESPONSE, FAILED, BLOCKED, STOPPED},
    SENT: {DELIVERED, WAITING_FOR_RESPONSE, RESPONSE_RECEIVED, FAILED,
           STOPPED, HUMAN_OWNED, COMPLETED, HANDOFF_REQUIRED},
    DELIVERED: {WAITING_FOR_RESPONSE, RESPONSE_RECEIVED, STOPPED, HUMAN_OWNED,
                COMPLETED, FAILED, HANDOFF_REQUIRED},
    WAITING_FOR_RESPONSE: {RESPONSE_RECEIVED, PROCESSING, FOLLOWUP_SCHEDULED,
                           APPOINTMENT_BOOKED, HANDOFF_REQUIRED, HUMAN_OWNED,
                           COMPLETED, STOPPED, REVIEW_REQUIRED, FAILED,
                           QUEUED},
    RESPONSE_RECEIVED: {PROCESSING, FOLLOWUP_SCHEDULED, APPOINTMENT_BOOKED,
                        HANDOFF_REQUIRED, HUMAN_OWNED, COMPLETED, STOPPED,
                        REVIEW_REQUIRED, QUEUED, WAITING_FOR_RESPONSE},
    PROCESSING: {FOLLOWUP_SCHEDULED, APPOINTMENT_BOOKED, HANDOFF_REQUIRED,
                 REVIEW_REQUIRED, COMPLETED, STOPPED, HUMAN_OWNED, FAILED,
                 WAITING_FOR_RESPONSE, QUEUED},
    FOLLOWUP_SCHEDULED: {QUEUED, WAITING_FOR_RESPONSE, RESPONSE_RECEIVED,
                         APPOINTMENT_BOOKED, HANDOFF_REQUIRED, HUMAN_OWNED,
                         COMPLETED, STOPPED, REVIEW_REQUIRED},
    APPOINTMENT_BOOKED: {COMPLETED, STOPPED, HANDOFF_REQUIRED, HUMAN_OWNED,
                         RESPONSE_RECEIVED},
    HANDOFF_REQUIRED: {HUMAN_OWNED, STOPPED, COMPLETED, RESPONSE_RECEIVED},
    HUMAN_OWNED: {STOPPED, COMPLETED, QUEUED, RESPONSE_RECEIVED},
    FAILED: {QUEUED, FOLLOWUP_SCHEDULED, STOPPED, REVIEW_REQUIRED,
             HUMAN_OWNED, COMPLETED},
    REVIEW_REQUIRED: {QUEUED, FOLLOWUP_SCHEDULED, STOPPED, HUMAN_OWNED,
                      HANDOFF_REQUIRED, COMPLETED, RESPONSE_RECEIVED},
    # A stopped conversation stays stopped. Resuming one is a NEW thread, so
    # that "this family was worked twice" is visible rather than hidden
    # inside one row's history.
    COMPLETED: set(),
    STOPPED: set(),
    BLOCKED: {STOPPED, REVIEW_REQUIRED, HUMAN_OWNED},
}

# The grouping the operations console shows. The engine never reads it; it
# exists so the API and the UI agree on one grouping.
COMM_GROUPS = (
    ("in_flight", "In Flight", (QUEUED, ELIGIBILITY_CHECK, READY, SENDING)),
    ("sent", "Sent", (SENT, DELIVERED)),
    ("waiting", "Waiting for Response", (WAITING_FOR_RESPONSE,)),
    ("responded", "Responded", (RESPONSE_RECEIVED, PROCESSING)),
    ("scheduled", "Follow-up Scheduled", (FOLLOWUP_SCHEDULED,)),
    ("booked", "Appointments", (APPOINTMENT_BOOKED,)),
    ("handoff", "Handoffs", (HANDOFF_REQUIRED, HUMAN_OWNED)),
    ("review", "Needs Review", (REVIEW_REQUIRED, FAILED)),
    ("closed", "Closed", (COMPLETED, STOPPED, BLOCKED)),
)


# ── ELIGIBILITY RESULTS ─────────────────────────────────────────────────────
#
# Same three answers T6 records in `ai_eligibility_results`, same spelling.
ALLOW = "ALLOW"
DENY = "DENY"
REQUIRES_REVIEW = "REQUIRES_REVIEW"
ELIGIBILITY_RESULTS = (ALLOW, DENY, REQUIRES_REVIEW)


# ── ELIGIBILITY REASON CODES ────────────────────────────────────────────────
#
# A reason is DATA, with a code a test can assert on and a screen can explain.
# "Not eligible" tells an operator nothing about which of fourteen gates said
# no, and "which gate" is the only question they ever actually ask.
E_OPTED_OUT = "contact_opted_out"
E_DNC = "do_not_contact"
E_SUPPRESSED = "phone_on_suppression_list"
E_NO_ADDRESS = "no_address_for_channel"
E_BAD_ADDRESS = "address_flagged_unusable"
E_CHANNEL_PERMISSION = "channel_permission_denied"
E_CHANNEL_NOT_ENABLED = "channel_not_enabled_for_employee"
E_CHANNEL_FEATURE_OFF = "channel_feature_not_enabled_for_customer"
E_CAPACITY_HOLD = "lead_held_over_plan_capacity"
E_OUTSIDE_WINDOW = "outside_permitted_communication_window"
E_HUMAN_OWNED = "human_owns_this_conversation"
E_ALREADY_RESPONDED_STOP = "prior_response_requires_stop"
E_TENANT_MISMATCH = "record_not_in_this_tenant"
E_RECORD_NOT_FOUND = "record_not_found"
E_CONSENT_UNKNOWN = "consent_state_requires_review"
E_TEST_RECORD = "test_record_not_contactable"
E_DUPLICATE_RECORD = "duplicate_record_requires_review"
E_POLICY_REVIEW = "customer_policy_requires_review"
E_PLATFORM_REFUSED = "platform_compliance_refused"


# ── OPERATIONAL REFUSAL CODES ───────────────────────────────────────────────
#
# The orchestrator's own vocabulary, distinct from the eligibility reasons
# above: these are refusals about the ACTOR or the ACTION, not about the
# person being contacted. T6's gateway codes are consumed verbatim where T6
# answered; these cover the questions this layer asks on its own.
D_UNKNOWN_OPERATION = "unknown_operation"
D_NOT_AUTHORIZED = "operation_not_authorized_for_employee"
D_INELIGIBLE = "contact_not_eligible"
D_REQUIRES_REVIEW = "contact_requires_review"
D_ACTIVATION_STAGE = "activation_stage_forbids_action"
D_KILLED = "kill_switch_engaged"
D_EMPLOYEE_PAUSED = "employee_paused"
D_EMPLOYEE_INACTIVE = "employee_not_active"
D_ORGANIZATION_DISABLED = "organization_disabled"
D_OBJECTIVE_CANCELLED = "objective_cancelled"
D_OBJECTIVE_COMPLETE = "objective_already_complete"
D_HUMAN_OWNED = "human_owns_this_conversation"
D_DUPLICATE = "duplicate_suppressed_by_idempotency"
D_BAD_ARGUMENTS = "invalid_arguments"
D_TENANT_MISMATCH = "record_not_in_this_tenant"
D_RECORD_NOT_FOUND = "record_not_found"
D_ATTEMPT_CAP = "max_attempts_reached"
D_ACTION_CAP = "max_actions_for_objective_reached"
D_CHANNEL_CAP = "channel_daily_cap_reached"
D_COST_CAP = "cost_ceiling_reached"
D_FAILURE_LOOP = "consecutive_failure_limit_reached"
D_LIVE_VOICE_DISABLED = "live_voice_disabled"
D_PROVIDER_UNAVAILABLE = "no_provider_configured_for_channel"
D_PROVIDER_FAILED = "provider_failed"
D_PROVIDER_TIMEOUT = "provider_timed_out"
D_FEATURE_FLAG_OFF = "ai_operations_disabled"
D_STATE_TRANSITION = "illegal_state_transition"
D_NO_AVAILABILITY = "no_authorized_availability"
D_SLOT_NOT_OFFERED = "slot_was_never_offered"
D_BOOKING_AUTHORITY = "booking_authority_refused"


# ── STOP REASONS ────────────────────────────────────────────────────────────
#
# Why work on a subject stopped. Recorded on the thread, so "why did this
# conversation end" is answerable months later without replaying the log.
STOP_OPT_OUT = "contact_opted_out"
STOP_DNC = "do_not_contact"
STOP_OBJECTIVE_COMPLETE = "objective_complete"
STOP_APPOINTMENT_BOOKED = "appointment_booked"
STOP_HUMAN_TAKEOVER = "human_took_over"
STOP_CONTACT_REQUESTED = "contact_requested_stop"
STOP_INVALID_CONTACT = "contact_details_invalid"
STOP_EMPLOYEE_DISABLED = "employee_disabled_by_tenant"
STOP_EMPLOYEE_PAUSED = "employee_paused"
STOP_OBJECTIVE_CANCELLED = "objective_cancelled"
STOP_CHANNEL_DISABLED = "communication_capability_disabled"
STOP_POLICY_VIOLATION = "policy_violation"
STOP_COST_LIMIT = "cost_or_risk_limit"
STOP_SUPERVISOR = "supervisor_intervention"
STOP_EXHAUSTED = "attempts_exhausted"
STOP_KILL_SWITCH = "kill_switch_engaged"

# The stop reasons that mean NOBODY works this subject again without a human.
HARD_STOP_REASONS = frozenset({
    STOP_OPT_OUT, STOP_DNC, STOP_CONTACT_REQUESTED, STOP_INVALID_CONTACT,
    STOP_POLICY_VIOLATION,
})


# ── PROVIDER OUTCOMES ───────────────────────────────────────────────────────
#
# What an adapter reports back, normalized away from provider vocabulary so
# swapping Twilio for something else changes no caller.
P_ACCEPTED = "accepted"          # the provider took it
P_DELIVERED = "delivered"        # the provider says it arrived
P_FAILED = "failed"              # the provider refused or errored
P_TIMEOUT = "timeout"            # no answer inside the adapter's budget
P_REJECTED = "rejected"          # the adapter itself refused before dialling
P_SIMULATED = "simulated"        # nothing left this process
PROVIDER_OUTCOMES = (P_ACCEPTED, P_DELIVERED, P_FAILED, P_TIMEOUT, P_REJECTED,
                     P_SIMULATED)

# Voice call dispositions. Present in full because the voice ARCHITECTURE is
# real in this build even though no call can be placed.
V_COMPLETED = "completed"
V_NO_ANSWER = "no_answer"
V_BUSY = "busy"
V_VOICEMAIL = "voicemail"
V_TRANSFERRED = "transferred_to_human"
V_CALLBACK_REQUESTED = "callback_requested"
V_FAILED = "failed"
VOICE_DISPOSITIONS = (V_COMPLETED, V_NO_ANSWER, V_BUSY, V_VOICEMAIL,
                      V_TRANSFERRED, V_CALLBACK_REQUESTED, V_FAILED)


# ── OPERATIONS (the capability names an employee may request) ───────────────
#
# These are OPERATIONS, not tools. T6's registry owns the tool keys an
# employee holds authority for; `contracts.TOOL_FOR_OPERATION` maps each
# operation to the tool key whose authority it consumes, so an operation can
# never be performed by an employee that was not granted the corresponding
# tool. Adding an operation without adding that mapping makes it unauthorized
# by construction, which is the safe direction for a mistake to fail.
OP_SEND_MESSAGE = "send_message"
OP_SEND_EMAIL = "send_email"
OP_INITIATE_CALL = "initiate_call"
OP_RESPOND_TO_INBOUND = "respond_to_inbound"
OP_SCHEDULE_FOLLOWUP = "schedule_followup"
OP_BOOK_APPOINTMENT = "book_appointment"
OP_RESCHEDULE_APPOINTMENT = "reschedule_appointment"
OP_CANCEL_APPOINTMENT = "cancel_appointment"
OP_TRANSFER_TO_HUMAN = "transfer_to_human"
OP_UPDATE_LEAD = "update_lead"
OP_CREATE_TASK = "create_task"
OP_RECORD_OUTCOME = "record_outcome"

ALL_OPERATIONS = (
    OP_SEND_MESSAGE, OP_SEND_EMAIL, OP_INITIATE_CALL, OP_RESPOND_TO_INBOUND,
    OP_SCHEDULE_FOLLOWUP, OP_BOOK_APPOINTMENT, OP_RESCHEDULE_APPOINTMENT,
    OP_CANCEL_APPOINTMENT, OP_TRANSFER_TO_HUMAN, OP_UPDATE_LEAD,
    OP_CREATE_TASK, OP_RECORD_OUTCOME,
)


# ── CORRELATION KINDS ───────────────────────────────────────────────────────
#
# What an idempotency key is protecting. Stored so a duplicate refusal can say
# WHAT it thought was a duplicate.
CORR_SEND = "send"
CORR_BOOKING = "booking"
CORR_INBOUND = "inbound_event"
CORR_HANDOFF = "handoff"
CORR_FOLLOWUP = "scheduled_action"
CORR_PIPELINE = "pipeline_mutation"
CORR_TASK = "task"


# ── ACTOR KINDS ─────────────────────────────────────────────────────────────
#
# Matches T6's audit vocabulary exactly. An AI employee is not a user, which
# is why `audit_log_entries.actor_user_id` cannot carry it and why this layer
# keeps its own audit table.
ACTOR_AI_EMPLOYEE = "ai_employee"
ACTOR_HUMAN = "human"
ACTOR_SYSTEM = "system"
ACTOR_SUPERVISOR = "ai_supervisor"
ACTOR_PROVIDER = "provider"


# ── OPERATIONAL DEFAULTS (ceilings, not suggestions) ────────────────────────
#
# Configuration may LOWER any of these and may never raise one past its
# ceiling. A model asking for one more attempt is not a reason.
DEFAULT_MAX_ATTEMPTS_PER_COMM = 3
DEFAULT_MAX_ACTIONS_PER_OBJECTIVE = 40
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
DEFAULT_CHANNEL_DAILY_CAP = 250
DEFAULT_VOICE_DAILY_CAP = 0            # voice is dark; the cap says so too
DEFAULT_MAX_VOICE_SECONDS = 300
DEFAULT_COST_CEILING_USD = 25.0

MAX_ACTIONS_CEILING = 200
MAX_ATTEMPTS_CEILING = 6
CHANNEL_DAILY_CAP_CEILING = 5000

# Backoff between retries of a failed communication, in seconds, by
# consecutive-failure count. The last value repeats. Matches T6's shape so an
# operator sees one retry philosophy rather than two.
RETRY_BACKOFF_SECONDS = (60, 300, 1800, 7200)

# How long a scheduled action's claim is held before another worker may take
# it. A worker that dies mid-execution must not park an action forever.
ACTION_LEASE_SECONDS = 300

# How long an adapter may take before the orchestrator calls it a timeout.
PROVIDER_TIMEOUT_SECONDS = 20

# The default quiet-hours window applied when a customer has configured none.
#
# UNCONFIGURED IS NOT "ANY TIME". T6's `within_operating_hours` refuses
# outright when an employee has no hours configured, and this window is the
# equivalent for the CONTACT side: it is the outer bound the platform will not
# cross even for a customer who configured a wider one. 3am texting is the
# harm a defaulted-open window produces.
DEFAULT_WINDOW_START_HOUR = 8
DEFAULT_WINDOW_END_HOUR = 21
DEFAULT_WINDOW_TIMEZONE = "America/Chicago"


# ── SUPERVISOR EVENT CODES ──────────────────────────────────────────────────
SUP_POLICY_DENIAL = "ops.policy_denial"
SUP_PROVIDER_FAILURE = "ops.provider_failure"
SUP_RUNAWAY = "ops.runaway_guard_tripped"
SUP_BLOCKED_WORK = "ops.work_blocked"
SUP_REVIEW_REQUIRED = "ops.review_required"
SUP_HANDOFF_CREATED = "ops.handoff_created"
SUP_HANDOFF_UNCLAIMED = "ops.handoff_unclaimed"
SUP_HUMAN_TAKEOVER = "ops.human_takeover"
SUP_INBOUND_UNROUTABLE = "ops.inbound_unroutable"
SUP_APPOINTMENT_BOOKED = "ops.appointment_booked"
SUP_OPT_OUT = "ops.contact_opted_out"
