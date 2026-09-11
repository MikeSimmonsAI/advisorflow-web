"""T9 - THE MANAGEMENT VOCABULARY, DEFINED ONCE.

Every scope name, attention kind, severity, finding code, evidence class,
review decision, management action, reconciliation check and pass name this
layer can produce lives here and nowhere else. The rule is the one T6, T7 and
T8 each state in their own constants file, for the same reason: two spellings
of a name is how a comparison becomes silently False forever.

WHAT IS NOT HERE, ON PURPOSE. No work-item state, no communication state, no
deployment state, no denial code, no eligibility reason. Those belong to T6,
T7 and T8, and T9 imports them rather than re-declaring them - a second
spelling of `waiting_for_response` in this file would be a second definition
of what waiting means, and the two would drift the first time one changed.

Plain lowercase strings in VARCHAR, never a database enum. See
app/auto_migrate.py for why.
"""

from app.services.ai_deployment import constants as D
from app.services.ai_operations import constants as O
from app.services.workforce import constants as W


# ---------------------------------------------------------------------------
# SCOPE - who is asking, and about what
# ---------------------------------------------------------------------------
#
# THE FIVE LAYERS OF THE MISSION, AND GOD IS NOT ONE OF THEM HERE.
#
# God is an AUTHORITY, not a scope: a god_admin asking about one customer is
# asking an ORGANIZATION-scoped question, and the answer is the same answer the
# customer's own manager gets. Making "god" a scope type would create a second
# shape of every payload, and the first time the two disagreed the owner's
# screen and the customer's screen would be telling different stories about
# the same workforce. So authority decides WHICH scopes a caller may ask
# about; scope decides what the answer covers.

SCOPE_ORGANIZATION = "organization"
SCOPE_BRAND = "brand"
SCOPE_PLATFORM = "platform"
SCOPE_TYPES = (SCOPE_ORGANIZATION, SCOPE_BRAND, SCOPE_PLATFORM)

PLATFORM_SCOPE_ID = ""


# ---------------------------------------------------------------------------
# EVIDENCE CLASSES - section 5's separation, as a vocabulary
# ---------------------------------------------------------------------------
#
# NEVER PRESENT MODEL INTERPRETATION AS DATABASE FACT. Every number and every
# sentence T9 emits carries one of these labels, and the renderer shows the
# label. A reader who cannot tell which of these a line is has been given a
# claim, not information.

EV_FACT = "fact"                      # a row exists and says this
EV_METRIC = "metric"                  # computed from facts by a named formula
EV_INTERPRETATION = "interpretation"  # a reading OF the facts, labelled
EV_RECOMMENDATION = "recommendation"  # what a person might do next
EV_UNKNOWN = "unknown"                # not knowable from authoritative records
EVIDENCE_CLASSES = (EV_FACT, EV_METRIC, EV_INTERPRETATION, EV_RECOMMENDATION,
                    EV_UNKNOWN)

# UNKNOWN IS NOT ZERO, and this is the sentence that says so wherever a value
# is absent rather than nil.
UNKNOWN_NOTE = ("Not known from authoritative records. This is absent, not "
                "zero.")


# ---------------------------------------------------------------------------
# SEVERITY
# ---------------------------------------------------------------------------

SEV_CRITICAL = "critical"
SEV_HIGH = "high"
SEV_NORMAL = "normal"
SEV_LOW = "low"
SEV_INFO = "info"
SEVERITIES = (SEV_CRITICAL, SEV_HIGH, SEV_NORMAL, SEV_LOW, SEV_INFO)

# Lower sorts first on the queue. Ordering by the string would put "critical"
# after "high" alphabetically, which is exactly the sort of bug nobody notices
# until the worst item is halfway down the page.
SEVERITY_RANK = {SEV_CRITICAL: 0, SEV_HIGH: 1, SEV_NORMAL: 2, SEV_LOW: 3,
                 SEV_INFO: 4}

SEVERITY_LABELS = {
    SEV_CRITICAL: "Critical",
    SEV_HIGH: "High",
    SEV_NORMAL: "Needs attention",
    SEV_LOW: "Worth a look",
    SEV_INFO: "For information",
}


# ---------------------------------------------------------------------------
# ATTENTION KINDS - what can land in "Needs Your Attention"
# ---------------------------------------------------------------------------
#
# Section 2's candidate list, one constant each. A kind that is not here cannot
# be produced, which stops the queue filling with one-off strings no screen can
# group, filter or explain.

A_CUSTOMER_WAITING = "customer_waiting_for_human"
A_HANDOFF_WAITING = "handoff_waiting"
A_HANDOFF_UNROUTED = "handoff_unrouted"
A_COMPLIANCE_REVIEW = "compliance_review_required"
A_OBJECTIVE_FAILED = "objective_failed"
A_OBJECTIVE_STUCK = "objective_stuck"
A_APPOINTMENT_PROBLEM = "appointment_problem"
A_PROVIDER_FAILURE = "provider_or_channel_failure"
A_EMPLOYEE_CONFIG = "employee_configuration_problem"
A_REPEATED_FAILURE = "repeated_failure"
A_RETRY_LOOP = "unusual_retry_loop"
A_COST_RUNAWAY = "cost_or_runaway_condition"
A_STALE_FOLLOWUP = "stale_followup"
A_EMPLOYEE_SUSPENDED = "employee_suspended"
A_ENTITLEMENT_MISMATCH = "entitlement_or_config_mismatch"
A_READINESS_REGRESSION = "readiness_regression"
A_OBJECTIVE_BLOCKED = "objective_unable_to_proceed"
A_WORK_REVIEW = "work_requires_review"
A_INBOUND_UNROUTABLE = "inbound_could_not_be_placed"
A_EMPLOYEE_PAUSED_WITH_WORK = "employee_paused_while_work_remains"

ATTENTION_KINDS = (
    A_CUSTOMER_WAITING, A_HANDOFF_WAITING, A_HANDOFF_UNROUTED,
    A_COMPLIANCE_REVIEW, A_OBJECTIVE_FAILED, A_OBJECTIVE_STUCK,
    A_APPOINTMENT_PROBLEM, A_PROVIDER_FAILURE, A_EMPLOYEE_CONFIG,
    A_REPEATED_FAILURE, A_RETRY_LOOP, A_COST_RUNAWAY, A_STALE_FOLLOWUP,
    A_EMPLOYEE_SUSPENDED, A_ENTITLEMENT_MISMATCH, A_READINESS_REGRESSION,
    A_OBJECTIVE_BLOCKED, A_WORK_REVIEW, A_INBOUND_UNROUTABLE,
    A_EMPLOYEE_PAUSED_WITH_WORK,
)

ATTENTION_LABELS = {
    A_CUSTOMER_WAITING: "Someone is waiting for a person",
    A_HANDOFF_WAITING: "A handoff has not been picked up",
    A_HANDOFF_UNROUTED: "A handoff has nobody to go to",
    A_COMPLIANCE_REVIEW: "A compliance decision needs a person",
    A_OBJECTIVE_FAILED: "Work failed",
    A_OBJECTIVE_STUCK: "Work has stopped moving",
    A_APPOINTMENT_PROBLEM: "An appointment did not complete",
    A_PROVIDER_FAILURE: "A channel or provider is failing",
    A_EMPLOYEE_CONFIG: "An AI employee is not configured to work",
    A_REPEATED_FAILURE: "The same thing keeps failing",
    A_RETRY_LOOP: "Something is retrying without progressing",
    A_COST_RUNAWAY: "Usage is running ahead of its limit",
    A_STALE_FOLLOWUP: "A follow-up is overdue",
    A_EMPLOYEE_SUSPENDED: "An AI employee has been stopped",
    A_ENTITLEMENT_MISMATCH: "What is set up and what is paid for disagree",
    A_READINESS_REGRESSION: "An employee that was ready no longer is",
    A_OBJECTIVE_BLOCKED: "Work cannot proceed",
    A_WORK_REVIEW: "Work is waiting for review",
    A_INBOUND_UNROUTABLE: "A reply could not be matched to anyone",
    A_EMPLOYEE_PAUSED_WITH_WORK: "A paused employee still has work waiting",
}

# THE DEDUPLICATION ROOTS. Section 2: do not create 50 alerts from one root
# problem. When a kind on the left is present for the same employee, the kinds
# on the right are suppressed and counted into it instead - a provider outage
# is ONE item that stalled forty objectives, not forty items.
DEDUP_ROLLUP = {
    A_PROVIDER_FAILURE: (A_OBJECTIVE_STUCK, A_OBJECTIVE_FAILED,
                         A_REPEATED_FAILURE, A_RETRY_LOOP),
    A_EMPLOYEE_SUSPENDED: (A_OBJECTIVE_STUCK, A_STALE_FOLLOWUP,
                           A_EMPLOYEE_PAUSED_WITH_WORK),
    A_ENTITLEMENT_MISMATCH: (A_EMPLOYEE_CONFIG, A_READINESS_REGRESSION),
}

ATTENTION_STATE_OPEN = "open"
ATTENTION_STATE_ACK = "acknowledged"
ATTENTION_STATE_RESOLVED = "resolved"
ATTENTION_STATE_CLEARED = "cleared"
ATTENTION_STATES = (ATTENTION_STATE_OPEN, ATTENTION_STATE_ACK,
                    ATTENTION_STATE_RESOLVED, ATTENTION_STATE_CLEARED)

# The states that still count as "on the manager's desk".
ATTENTION_LIVE_STATES = frozenset({ATTENTION_STATE_OPEN, ATTENTION_STATE_ACK})


# ---------------------------------------------------------------------------
# SUPERVISOR FINDING CODES
# ---------------------------------------------------------------------------
#
# A finding is a SENTENCE WITH EVIDENCE, not an alert. The codes are what a
# test asserts on and what a screen groups by; the sentence is generated from
# the evidence, so the two cannot drift.

F_ELIGIBLE_BACKLOG = "eligible_work_waiting"
F_REVIEW_BACKLOG = "conversations_awaiting_review"
F_CONVERSION_DECLINE = "appointment_conversion_below_baseline"
F_BLOCKED_LONGER = "objectives_blocked_longer_than_expected"
F_PROVIDER_FAILURES_UP = "provider_failures_increased"
F_SAME_STAGE_FAILURE = "repeated_failure_at_one_stage"
F_VOICE_WITHOUT_YIELD = "voice_usage_without_matching_outcomes"
F_DENIAL_CONCENTRATION = "refusals_concentrated_on_one_cause"
F_HANDOFF_LATENCY = "handoffs_waiting_longer_than_usual"
F_RESPONSE_DECLINE = "response_rate_below_baseline"
F_COST_PER_OUTCOME = "cost_per_outcome_moved"
F_NO_OUTCOMES = "employee_producing_no_outcomes"
F_OPT_OUT_RISE = "opt_outs_increased"
F_IDLE_EMPLOYEE = "employee_live_with_no_work"

FINDING_CODES = (
    F_ELIGIBLE_BACKLOG, F_REVIEW_BACKLOG, F_CONVERSION_DECLINE,
    F_BLOCKED_LONGER, F_PROVIDER_FAILURES_UP, F_SAME_STAGE_FAILURE,
    F_VOICE_WITHOUT_YIELD, F_DENIAL_CONCENTRATION, F_HANDOFF_LATENCY,
    F_RESPONSE_DECLINE, F_COST_PER_OUTCOME, F_NO_OUTCOMES, F_IDLE_EMPLOYEE,
    F_OPT_OUT_RISE,
)

FINDING_STATE_OPEN = "open"
FINDING_STATE_ACK = "acknowledged"
FINDING_STATE_CLEARED = "cleared"


# ---------------------------------------------------------------------------
# QUALITY DIMENSIONS
# ---------------------------------------------------------------------------
#
# SECTION 7: do not grade merely on whether language sounded pleasant. Every
# dimension here is answerable from an authoritative record - a state, a tool
# execution, an eligibility verdict, a handoff row - and not one of them is a
# judgement about tone. A quality score built from prose sentiment would be a
# model grading a model, and it would be the number a customer argued with.

Q_OBJECTIVE_COMPLETION = "objective_completion"
Q_REQUIRED_INFORMATION = "required_information_collected"
Q_QUALIFICATION_COMPLETENESS = "qualification_completeness"
Q_FACTUAL_GROUNDING = "factual_grounding"
Q_TOOL_CORRECTNESS = "tool_correctness"
Q_POLICY_COMPLIANCE = "policy_compliance"
Q_STOP_COMPLIANCE = "stop_condition_compliance"
Q_HANDOFF_CORRECTNESS = "correct_handoff"
Q_APPOINTMENT_BEHAVIOUR = "correct_appointment_behaviour"
Q_REQUEST_RESPECTED = "customer_request_respected"
Q_ESCALATION = "correct_escalation"

QUALITY_DIMENSIONS = (
    Q_OBJECTIVE_COMPLETION, Q_REQUIRED_INFORMATION,
    Q_QUALIFICATION_COMPLETENESS, Q_FACTUAL_GROUNDING, Q_TOOL_CORRECTNESS,
    Q_POLICY_COMPLIANCE, Q_STOP_COMPLIANCE, Q_HANDOFF_CORRECTNESS,
    Q_APPOINTMENT_BEHAVIOUR, Q_REQUEST_RESPECTED, Q_ESCALATION,
)

QUALITY_LABELS = {
    Q_OBJECTIVE_COMPLETION: "Finished what it was asked to do",
    Q_REQUIRED_INFORMATION: "Collected what it was told to collect",
    Q_QUALIFICATION_COMPLETENESS: "Qualified people properly",
    Q_FACTUAL_GROUNDING: "Answered from your information, not invention",
    Q_TOOL_CORRECTNESS: "Used the right actions, and they worked",
    Q_POLICY_COMPLIANCE: "Stayed inside the rules",
    Q_STOP_COMPLIANCE: "Stopped when it was told to stop",
    Q_HANDOFF_CORRECTNESS: "Handed over cleanly when a person was needed",
    Q_APPOINTMENT_BEHAVIOUR: "Booked appointments correctly",
    Q_REQUEST_RESPECTED: "Did what the person asked",
    Q_ESCALATION: "Escalated the right things",
}

# THE DIMENSIONS WHERE ANY FAILURE IS A STOP-THE-LINE RESULT, matching T6's
# own CRITICAL_DIMENSIONS in spirit: a compliance or stop-condition failure is
# not a regression to look at on Monday.
QUALITY_CRITICAL = frozenset({Q_POLICY_COMPLIANCE, Q_STOP_COMPLIANCE,
                              Q_REQUEST_RESPECTED})

# QUALITY MAY RECOMMEND. QUALITY MAY NEVER GRANT.
#
# Section 7 in one constant: the complete set of consequences a quality verdict
# is allowed to produce. Nothing that changes authority, entitlement, channels
# or activation is in it, and `coaching.py` asserts membership rather than
# trusting a caller to have read this comment.
QUALITY_ALLOWED_CONSEQUENCES = ("recommend_review", "recommend_pause",
                                "recommend_configuration_change",
                                "recommend_investigation", "no_action")


# ---------------------------------------------------------------------------
# HUMAN REVIEW
# ---------------------------------------------------------------------------

REVIEW_SOURCE_WORK_ITEM = "work_item"
REVIEW_SOURCE_THREAD = "conversation"
REVIEW_SOURCE_ELIGIBILITY = "eligibility"
REVIEW_SOURCE_HANDOFF = "handoff"
REVIEW_SOURCE_COMMUNICATION = "communication"
REVIEW_SOURCE_SUPERVISOR = "supervisor_escalation"
REVIEW_SOURCES = (REVIEW_SOURCE_WORK_ITEM, REVIEW_SOURCE_THREAD,
                  REVIEW_SOURCE_ELIGIBILITY, REVIEW_SOURCE_HANDOFF,
                  REVIEW_SOURCE_COMMUNICATION, REVIEW_SOURCE_SUPERVISOR)

RD_APPROVED = "approved"
RD_REJECTED = "rejected"
RD_ESCALATED = "escalated"
RD_NO_ACTION = "no_action_needed"
RD_PAUSE_REQUESTED = "pause_requested"
REVIEW_DECISIONS = (RD_APPROVED, RD_REJECTED, RD_ESCALATED, RD_NO_ACTION,
                    RD_PAUSE_REQUESTED)

# WHAT A REVIEWER IS NEVER SHOWN. Section 8: do not expose hidden model
# chain-of-thought. The review payload is built by allow-list rather than by
# removing these, but the list is named so the test can assert it.
REVIEW_FORBIDDEN_FIELDS = ("chain_of_thought", "reasoning", "scratchpad",
                           "system_prompt", "prompt", "raw_completion",
                           "model_messages")


# ---------------------------------------------------------------------------
# MANAGEMENT ACTIONS - what T9 may ASK an owning system to do
# ---------------------------------------------------------------------------
#
# EVERY ONE OF THESE IS A DELEGATION. T9 holds no authority of its own: each
# action names the module that performs it, and `actions.py` refuses any
# action not in this map rather than falling through to a generic handler.
#
# WHAT IS DELIBERATELY ABSENT is the whole of section 9's prohibition list -
# grant a tool, change authority, invent consent, enable a channel, enable
# voice, activate live sending, alter billing, change commercial entitlement,
# bypass T8 readiness, change God authority. None of them has a constant here,
# so none of them can be requested through this surface by name or by typo.

M_PAUSE_EMPLOYEE = "pause_employee"
M_RESUME_EMPLOYEE = "resume_employee"
M_REQUEST_TAKEOVER = "request_human_takeover"
M_RELEASE_TAKEOVER = "release_human_takeover"
M_ACCEPT_HANDOFF = "accept_handoff"
M_RESOLVE_HANDOFF = "resolve_handoff"
M_ACKNOWLEDGE_EXCEPTION = "acknowledge_exception"
M_RESOLVE_ATTENTION = "resolve_attention_item"
M_RECORD_REVIEW = "record_review_decision"
M_ESCALATE = "escalate_to_support"
M_CANCEL_OBJECTIVE = "cancel_objective"
M_ACKNOWLEDGE_FINDING = "acknowledge_finding"
M_ACKNOWLEDGE_CONTRADICTION = "acknowledge_contradiction"

MANAGEMENT_ACTIONS = (
    M_PAUSE_EMPLOYEE, M_RESUME_EMPLOYEE, M_REQUEST_TAKEOVER,
    M_RELEASE_TAKEOVER, M_ACCEPT_HANDOFF, M_RESOLVE_HANDOFF,
    M_ACKNOWLEDGE_EXCEPTION, M_RESOLVE_ATTENTION, M_RECORD_REVIEW,
    M_ESCALATE, M_CANCEL_OBJECTIVE, M_ACKNOWLEDGE_FINDING,
    M_ACKNOWLEDGE_CONTRADICTION,
)

# WHICH SYSTEM ACTUALLY PERFORMS EACH ONE. Written into
# `ai_management_actions.authority_path` so the claim "T9 delegates" is
# checkable from the data rather than from this comment.
ACTION_AUTHORITY = {
    M_PAUSE_EMPLOYEE: "ai_deployment.lifecycle.pause",
    M_RESUME_EMPLOYEE: "ai_deployment.lifecycle.resume",
    M_REQUEST_TAKEOVER: "ai_operations.stop.take_over",
    M_RELEASE_TAKEOVER: "ai_operations.stop.release",
    M_ACCEPT_HANDOFF: "workforce.handoff.accept",
    M_RESOLVE_HANDOFF: "workforce.handoff.resolve",
    M_CANCEL_OBJECTIVE: "ai_operations.stop.stop_thread",
    M_ESCALATE: "support_incidents",
    # The four below act on T9's OWN management state and nothing else, which
    # is why T9 is named as the authority for them and for nothing else.
    M_ACKNOWLEDGE_EXCEPTION: "workforce_intelligence.attention",
    M_RESOLVE_ATTENTION: "workforce_intelligence.attention",
    M_RECORD_REVIEW: "workforce_intelligence.review",
    M_ACKNOWLEDGE_FINDING: "workforce_intelligence.findings",
    M_ACKNOWLEDGE_CONTRADICTION: "workforce_intelligence.reconciliation",
}

# Actions that change something outside T9. These are the ones that need the
# mutation guard AND the owning system's own authority check; the rest touch
# only T9's management state.
ACTIONS_TOUCHING_OTHER_SYSTEMS = frozenset({
    M_PAUSE_EMPLOYEE, M_RESUME_EMPLOYEE, M_REQUEST_TAKEOVER,
    M_RELEASE_TAKEOVER, M_ACCEPT_HANDOFF, M_RESOLVE_HANDOFF,
    M_CANCEL_OBJECTIVE, M_ESCALATE,
})

OUTCOME_PERFORMED = "performed"
OUTCOME_REFUSED = "refused"
OUTCOME_ERROR = "error"

# Refusal codes T9 itself produces. Anything an owning system refuses keeps
# that system's own code, verbatim.
R_UNKNOWN_ACTION = "unknown_management_action"
R_NOT_AUTHORIZED = "actor_not_authorized_for_this_action"
R_TENANT_MISMATCH = "record_not_in_this_tenant"
R_RECORD_NOT_FOUND = "record_not_found"
R_LAYER_UNAVAILABLE = "owning_layer_unavailable"
R_OBSERVATION_MODE = "read_only_observation_mode"
R_NOT_PERMITTED_BY_T9 = "t9_may_not_perform_this"


# ---------------------------------------------------------------------------
# RECONCILIATION CHECKS
# ---------------------------------------------------------------------------

RC_DEPLOYMENT_ACTIVE_ENGINE_OFF = "deployment_active_but_engine_off"
RC_EMPLOYEE_ACTIVE_NO_ENTITLEMENT = "employee_active_but_entitlement_gone"
RC_WORK_FOR_RETIRED = "objective_running_for_retired_employee"
RC_APPOINTMENT_WITHOUT_RECORD = "appointment_metric_without_appointment"
RC_HANDOFF_WITHOUT_RECORD = "handoff_metric_without_handoff"
RC_SUCCESS_AFTER_CANCEL = "success_counted_after_cancellation"
RC_COST_WITHOUT_USAGE = "cost_recorded_without_underlying_usage"
RC_QUEUE_ITEM_INVALID_SCOPE = "queue_item_refers_to_invalid_scope"
RC_DEPLOYMENT_WITHOUT_EMPLOYEE = "live_deployment_without_an_actor"
RC_THREAD_EMPLOYEE_MISSING = "conversation_names_an_unknown_employee"

RECONCILIATION_CHECKS = (
    RC_DEPLOYMENT_ACTIVE_ENGINE_OFF, RC_EMPLOYEE_ACTIVE_NO_ENTITLEMENT,
    RC_WORK_FOR_RETIRED, RC_APPOINTMENT_WITHOUT_RECORD,
    RC_HANDOFF_WITHOUT_RECORD, RC_SUCCESS_AFTER_CANCEL,
    RC_COST_WITHOUT_USAGE, RC_QUEUE_ITEM_INVALID_SCOPE,
    RC_DEPLOYMENT_WITHOUT_EMPLOYEE, RC_THREAD_EMPLOYEE_MISSING,
)

# WHO FIXES IT. Never "t9" - section 19 forbids silently repairing commercial
# or authority state from here, and naming T9 as the owner would be the first
# step towards doing it.
REMEDIATION_OWNER = {
    RC_DEPLOYMENT_ACTIVE_ENGINE_OFF: "t8_deployment_lifecycle",
    RC_EMPLOYEE_ACTIVE_NO_ENTITLEMENT: "t8_commercial_entitlement",
    RC_WORK_FOR_RETIRED: "t6_work_queue",
    RC_APPOINTMENT_WITHOUT_RECORD: "t7_appointments",
    RC_HANDOFF_WITHOUT_RECORD: "t6_handoff",
    RC_SUCCESS_AFTER_CANCEL: "t6_performance_ledger",
    RC_COST_WITHOUT_USAGE: "t7_operational_counters",
    RC_QUEUE_ITEM_INVALID_SCOPE: "t6_work_queue",
    RC_DEPLOYMENT_WITHOUT_EMPLOYEE: "t8_deployment_lifecycle",
    RC_THREAD_EMPLOYEE_MISSING: "t7_operations",
}


# ---------------------------------------------------------------------------
# PASSES - T9's own work, named so its failures are visible
# ---------------------------------------------------------------------------

P_AGGREGATE = "aggregate"
P_ATTENTION = "attention"
P_FINDINGS = "findings"
P_RECONCILE = "reconcile"
P_QUALITY = "quality"
P_COST = "cost"
PASSES = (P_AGGREGATE, P_ATTENTION, P_FINDINGS, P_RECONCILE, P_QUALITY,
          P_COST)

RUN_OK = "ok"
RUN_PARTIAL = "partial"
RUN_FAILED = "failed"


# ---------------------------------------------------------------------------
# READ MODELS - the view keys a screen may ask for
# ---------------------------------------------------------------------------

V_COMMAND_CENTER = "command_center"
V_ATTENTION = "attention_queue"
V_PERFORMANCE = "performance"
V_SCORECARDS = "scorecards"
V_QUALITY = "quality"
V_COST = "cost"
V_EXCEPTIONS = "exceptions"
V_EXECUTIVE = "executive_summary"
VIEW_KEYS = (V_COMMAND_CENTER, V_ATTENTION, V_PERFORMANCE, V_SCORECARDS,
             V_QUALITY, V_COST, V_EXCEPTIONS, V_EXECUTIVE)


# ---------------------------------------------------------------------------
# FRESHNESS
# ---------------------------------------------------------------------------
#
# SECTION 13: do not silently display stale metrics as real-time. Three words
# rather than a boolean, because "computed a moment ago", "computed a while
# ago but nothing has changed since" and "the aggregation has not run" are
# three different things and a manager acts differently on each.

FRESH_LIVE = "live"            # computed within FRESH_SECONDS
FRESH_RECENT = "recent"        # older, but no source event since
FRESH_STALE = "stale"          # older than STALE_SECONDS, or a failed pass
FRESH_ABSENT = "never_computed"
FRESHNESS = (FRESH_LIVE, FRESH_RECENT, FRESH_STALE, FRESH_ABSENT)

FRESH_SECONDS = 120
STALE_SECONDS = 900

FRESHNESS_LABELS = {
    FRESH_LIVE: "Up to date",
    FRESH_RECENT: "Recently computed - nothing has changed since",
    FRESH_STALE: "Out of date",
    FRESH_ABSENT: "Not computed yet",
}


# ---------------------------------------------------------------------------
# DETECTION THRESHOLDS
# ---------------------------------------------------------------------------
#
# CONFIGURABLE WHERE CONFIGURABLE IS HONEST, AND NOT INVENTED WHERE IT IS NOT.
#
# Section 6 asks for configurable thresholds AND forbids inventing
# customer-specific SLA promises. These are the two different things that
# request contains: a DETECTION threshold ("show me work that has not moved in
# an hour") is an operator's own preference and may be tuned; an SLA ("we will
# respond within an hour") is a promise to a customer and is not made here.
# Every name below is the first kind. Nothing in T9 renders any of them as a
# commitment, and the payloads say "has not moved since" rather than "is late".
#
# The defaults are deliberately unremarkable round numbers rather than tuned
# ones, for the reason T6's supervisor gives: a threshold nobody can explain is
# a threshold nobody trusts.

DEFAULTS = {
    # A claimable work item due for action and untouched for this long.
    "stalled_work_minutes": 60,
    # A conversation left waiting for a response with no scheduled follow-up.
    "waiting_without_followup_hours": 48,
    # A scheduled follow-up whose time has passed.
    "overdue_followup_minutes": 30,
    # An open handoff nobody has accepted.
    "handoff_unaccepted_hours": 4,
    # Consecutive failures on one record before it is a repeated failure.
    "failure_streak": 3,
    # Attempts on one action without the state advancing.
    "retry_without_progress": 3,
    # Refusals with one code, per employee, in 24h, before it is a spike.
    "denial_spike_24h": 25,
    # Provider failures for one channel, in 24h, before it is an outage.
    "provider_failure_24h": 5,
    # Fraction of the configured ceiling at which usage is "approaching".
    "budget_warning_fraction": 0.8,
    # A conversation in `blocked` or `review_required` for this long.
    "blocked_hours": 24,
    # A live employee with no work item touched in this long.
    "idle_employee_hours": 72,
    # How far a metric must move from its own baseline to be worth saying.
    "baseline_delta_fraction": 0.25,
    # The smallest denominator a rate comparison is allowed to use. Below this
    # a "50% decline" is two events and one of them, which is noise wearing a
    # percentage.
    "minimum_denominator": 10,
}

THRESHOLD_LABELS = {
    "stalled_work_minutes": "Work is stalled after (minutes)",
    "waiting_without_followup_hours": "Waiting with no follow-up after (hours)",
    "overdue_followup_minutes": "A follow-up is overdue after (minutes)",
    "handoff_unaccepted_hours": "A handoff is unclaimed after (hours)",
    "failure_streak": "Repeated failure after (consecutive failures)",
    "retry_without_progress": "Retry loop after (attempts with no progress)",
    "denial_spike_24h": "Refusal spike at (refusals in 24h)",
    "provider_failure_24h": "Provider trouble at (failures in 24h)",
    "budget_warning_fraction": "Warn at this fraction of a limit",
    "blocked_hours": "Blocked too long after (hours)",
    "idle_employee_hours": "A live employee is idle after (hours)",
    "baseline_delta_fraction": "Report a change of at least",
    "minimum_denominator": "Do not compare rates below (events)",
}


def thresholds(overrides=None):
    """The detection thresholds, with any operator overrides applied.

    An unknown key is IGNORED rather than accepted. A typo that silently
    created a new threshold would produce a setting that looks configured and
    changes nothing, which is worse than being told the name is wrong.
    """
    out = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key not in DEFAULTS:
            continue
        try:
            out[key] = type(DEFAULTS[key])(value)
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# WINDOWS
# ---------------------------------------------------------------------------

WINDOWS = {
    "24h": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
}
DEFAULT_WINDOW = "30d"


def window_days(window_key):
    return WINDOWS.get(window_key or DEFAULT_WINDOW, WINDOWS[DEFAULT_WINDOW])


# ---------------------------------------------------------------------------
# RE-EXPORTS - one spelling of the lower layers' vocabulary
# ---------------------------------------------------------------------------
#
# T9 reads these constantly. Importing them here rather than copying the
# strings is the whole point: if T6 renames a state, this module follows, and
# nothing in T9 keeps quietly comparing against the old spelling.

WORK_TERMINAL_STATES = W.TERMINAL_STATES
WORK_CLAIMABLE_STATES = W.CLAIMABLE_STATES
WORK_NEEDS_REVIEW = W.NEEDS_REVIEW
WORK_FAILED = W.FAILED
WORK_PAUSED = W.PAUSED
WORK_APPOINTMENT = W.APPOINTMENT_BOOKED
WORK_HANDOFF = W.HUMAN_HANDOFF

THREAD_BLOCKED = O.BLOCKED
THREAD_REVIEW = O.REVIEW_REQUIRED
THREAD_FAILED = O.FAILED
THREAD_HANDOFF = O.HANDOFF_REQUIRED
THREAD_HUMAN_OWNED = O.HUMAN_OWNED
THREAD_WAITING = O.WAITING_FOR_RESPONSE
THREAD_APPOINTMENT = O.APPOINTMENT_BOOKED

DEPLOY_LIVE_STATES = D.LIVE_STATES
DEPLOY_SUSPENDED = D.SUSPENDED
DEPLOY_PAUSED = D.PAUSED
DEPLOY_RETIRED = D.RETIRED
DEPLOY_COMMERCIALLY_LIVE = D.COMMERCIALLY_LIVE
READINESS_NOT_READY = D.READY_NO
READINESS_REVIEW = D.READY_REVIEW
