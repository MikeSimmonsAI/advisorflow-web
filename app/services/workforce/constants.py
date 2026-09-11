"""AI WORKFORCE — THE VOCABULARY, DEFINED ONCE.

Every state name, activation stage, channel, outcome and refusal code the
engine can produce lives here and nowhere else. Two copies of a state name is
how a state machine grows a transition nobody wrote: one module spells a state
`needs_review`, another `NEEDS_REVIEW`, and a comparison between them is
silently False forever.

The work-item states are deliberately lowercase strings stored in a plain
VARCHAR rather than a Postgres enum. That is not laziness — it is the standing
rule recorded in app/auto_migrate.py: SQLAlchemy's SAEnum writes the Python
MEMBER NAME into Postgres, and this codebase has already had to migrate two
columns back out of database enums because of it. A new state is a new string
and a new transition edge, not a production ALTER TYPE.
"""

# ── WORK ITEM STATES ────────────────────────────────────────────────────────
#
# One record being worked by one employee toward one objective. The names are
# the customer-facing vocabulary ("Waiting for response", "Needs review") and
# not developer internals, because they are what the Work Queue screen groups
# by — see section 51 of the mission brief.

ASSIGNED = "assigned"
ELIGIBILITY_PENDING = "eligibility_pending"
ELIGIBLE = "eligible"
WORKING = "working"
WAITING_FOR_RESPONSE = "waiting_for_response"
QUALIFIED = "qualified"
APPOINTMENT_BOOKED = "appointment_booked"
HUMAN_HANDOFF = "human_handoff"
NOT_INTERESTED = "not_interested"
DO_NOT_CONTACT = "do_not_contact"
BAD_CONTACT = "bad_contact"
EXHAUSTED = "exhausted"
NEEDS_REVIEW = "needs_review"
PAUSED = "paused"
FAILED = "failed"

ALL_STATES = (
    ASSIGNED, ELIGIBILITY_PENDING, ELIGIBLE, WORKING, WAITING_FOR_RESPONSE,
    QUALIFIED, APPOINTMENT_BOOKED, HUMAN_HANDOFF, NOT_INTERESTED,
    DO_NOT_CONTACT, BAD_CONTACT, EXHAUSTED, NEEDS_REVIEW, PAUSED, FAILED,
)

# TERMINAL MEANS NOBODY WORKS IT AGAIN WITHOUT A HUMAN.
#
# `qualified` and `appointment_booked` are terminal for THIS employee's job
# even though the customer relationship carries on — the next stage belongs to
# a different employee or to a person, reached through a handoff. Treating them
# as non-terminal is how an appointment setter keeps texting somebody who has
# already booked.
TERMINAL_STATES = frozenset({
    QUALIFIED, APPOINTMENT_BOOKED, HUMAN_HANDOFF, NOT_INTERESTED,
    DO_NOT_CONTACT, BAD_CONTACT, EXHAUSTED,
})

# States a worker may legitimately pick up and act on.
CLAIMABLE_STATES = frozenset({ASSIGNED, ELIGIBILITY_PENDING, ELIGIBLE,
                              WORKING, WAITING_FOR_RESPONSE})

# THE TRANSITION TABLE IS THE STATE MACHINE.
#
# An edge that is not here cannot be taken, and `queue.transition` refuses
# rather than logging and continuing. The refusal is the point: a silent
# illegal transition is how a record leaves `do_not_contact` and gets texted.
ALLOWED_TRANSITIONS = {
    ASSIGNED: {ELIGIBILITY_PENDING, PAUSED, FAILED, NEEDS_REVIEW,
               DO_NOT_CONTACT, BAD_CONTACT},
    ELIGIBILITY_PENDING: {ELIGIBLE, NEEDS_REVIEW, DO_NOT_CONTACT, BAD_CONTACT,
                          NOT_INTERESTED, EXHAUSTED, PAUSED, FAILED},
    ELIGIBLE: {WORKING, NEEDS_REVIEW, DO_NOT_CONTACT, BAD_CONTACT, PAUSED,
               FAILED, EXHAUSTED, ELIGIBILITY_PENDING},
    WORKING: {WAITING_FOR_RESPONSE, QUALIFIED, APPOINTMENT_BOOKED,
              HUMAN_HANDOFF, NOT_INTERESTED, DO_NOT_CONTACT, BAD_CONTACT,
              EXHAUSTED, NEEDS_REVIEW, PAUSED, FAILED, ELIGIBILITY_PENDING},
    WAITING_FOR_RESPONSE: {WORKING, QUALIFIED, APPOINTMENT_BOOKED,
                           HUMAN_HANDOFF, NOT_INTERESTED, DO_NOT_CONTACT,
                           BAD_CONTACT, EXHAUSTED, NEEDS_REVIEW, PAUSED,
                           FAILED, ELIGIBILITY_PENDING},
    # A REVIEWED ITEM GOES BACK TO THE QUEUE, NOT STRAIGHT TO WORKING.
    # Re-entering at eligibility_pending means the eligibility engine runs
    # again on the way out of review, so a human clearing a review cannot
    # thereby skip a DNC that arrived while the item was sitting there.
    NEEDS_REVIEW: {ELIGIBILITY_PENDING, DO_NOT_CONTACT, NOT_INTERESTED,
                   BAD_CONTACT, HUMAN_HANDOFF, EXHAUSTED, PAUSED, FAILED},
    PAUSED: {ASSIGNED, ELIGIBILITY_PENDING, ELIGIBLE, WAITING_FOR_RESPONSE,
             NEEDS_REVIEW, EXHAUSTED, DO_NOT_CONTACT, FAILED},
    FAILED: {ELIGIBILITY_PENDING, NEEDS_REVIEW, PAUSED, EXHAUSTED},
    # Terminal states below. Empty sets, on purpose: a record that opted out
    # does not re-enter the queue because a retry loop wanted it to.
    QUALIFIED: set(),
    APPOINTMENT_BOOKED: set(),
    HUMAN_HANDOFF: set(),
    NOT_INTERESTED: set(),
    DO_NOT_CONTACT: set(),
    BAD_CONTACT: set(),
    EXHAUSTED: set(),
}

# The customer-facing grouping used by the Work Queue screen. The engine never
# reads it; it exists so the API and the UI agree on one grouping instead of
# each inventing its own.
QUEUE_GROUPS = (
    ("working", "Working", (ASSIGNED, ELIGIBILITY_PENDING, ELIGIBLE, WORKING)),
    ("waiting", "Waiting for Response", (WAITING_FOR_RESPONSE,)),
    ("needs_review", "Needs Review", (NEEDS_REVIEW, FAILED)),
    ("qualified", "Qualified", (QUALIFIED,)),
    ("appointments", "Appointments Booked", (APPOINTMENT_BOOKED,)),
    ("handoffs", "Human Handoffs", (HUMAN_HANDOFF,)),
    ("closed", "Closed", (NOT_INTERESTED, DO_NOT_CONTACT, BAD_CONTACT,
                          EXHAUSTED)),
    ("paused", "Paused", (PAUSED,)),
)


# ── ACTIVATION STAGES ───────────────────────────────────────────────────────
#
# SAFE BY DEFAULT MEANS OFF, AND OFF IS THE DEFAULT EVERYWHERE.
#
# Resolution is MOST RESTRICTIVE WINS across platform -> brand -> customer ->
# employee. That direction is deliberate: a brand cannot grant itself more than
# the platform allows, and a customer admin cannot promote their own employee
# past what their brand has been given. The only thing any level can do
# unilaterally is restrict further, which is the safe direction.

OFF = "off"                 # nothing runs. The employee exists and does nothing.
SIMULATION = "simulation"   # the real engine, against fake channel adapters.
SHADOW = "shadow"           # observe real events, record what it WOULD do, send nothing.
CONTROLLED = "controlled"   # real execution, hard caps, one cohort, human watching.
ACTIVE = "active"           # real execution at configured capacity.

ACTIVATION_STAGES = (OFF, SIMULATION, SHADOW, CONTROLLED, ACTIVE)

# Ordered weakest-to-strongest so "most restrictive wins" is a min().
ACTIVATION_RANK = {OFF: 0, SIMULATION: 1, SHADOW: 2, CONTROLLED: 3, ACTIVE: 4}

# The stages in which a channel adapter may reach the outside world at all.
# SIMULATION and SHADOW are absent, and that absence is enforced in
# tools.py rather than trusted to a caller passing the right flag.
EXECUTING_STAGES = frozenset({CONTROLLED, ACTIVE})

# THE PRODUCTION DEFAULT FOR THIS DARK LAUNCH.
#
# Nothing in the deployed application may start above this without an explicit
# operator action recorded in `ai_workforce_activations`. The platform row is
# seeded at OFF, so an organization that has never been configured resolves to
# OFF whatever anybody writes at a lower level.
DEFAULT_ACTIVATION = OFF


# ── CHANNELS ────────────────────────────────────────────────────────────────
#
# The names match app/services/qualification.py's channel vocabulary exactly,
# because eligibility is answered by that engine and a second spelling would
# silently qualify against the wrong channel.
CHANNEL_SMS = "sms"
CHANNEL_EMAIL = "email"
CHANNEL_VOICE = "voice"
ALL_CHANNELS = (CHANNEL_SMS, CHANNEL_EMAIL, CHANNEL_VOICE)

# VOICE IS ARCHITECTURALLY PRESENT AND OPERATIONALLY OFF.
#
# The interfaces, the provider abstraction, the eligibility path and the call
# disposition model all exist. `live_voice_enabled()` in activation.py is the
# single switch, it is False, and tools.py refuses a voice send regardless of
# the employee's configuration when it is.
VOICE_LIVE_DEFAULT = False


# ── ELIGIBILITY RESULTS ─────────────────────────────────────────────────────
ALLOW = "ALLOW"
DENY = "DENY"
REQUIRES_REVIEW = "REQUIRES_REVIEW"
ELIGIBILITY_RESULTS = (ALLOW, DENY, REQUIRES_REVIEW)


# ── WORK OUTCOMES ───────────────────────────────────────────────────────────
OUTCOME_APPOINTMENT = "appointment_booked"
OUTCOME_QUALIFIED = "qualified"
OUTCOME_HANDOFF = "human_handoff"
OUTCOME_NOT_INTERESTED = "not_interested"
OUTCOME_DNC = "do_not_contact"
OUTCOME_BAD_CONTACT = "bad_contact"
OUTCOME_EXHAUSTED = "exhausted"
OUTCOME_NEEDS_REVIEW = "needs_review"

STATE_TO_OUTCOME = {
    APPOINTMENT_BOOKED: OUTCOME_APPOINTMENT,
    QUALIFIED: OUTCOME_QUALIFIED,
    HUMAN_HANDOFF: OUTCOME_HANDOFF,
    NOT_INTERESTED: OUTCOME_NOT_INTERESTED,
    DO_NOT_CONTACT: OUTCOME_DNC,
    BAD_CONTACT: OUTCOME_BAD_CONTACT,
    EXHAUSTED: OUTCOME_EXHAUSTED,
    NEEDS_REVIEW: OUTCOME_NEEDS_REVIEW,
}


# ── HANDOFF REASONS ─────────────────────────────────────────────────────────
HANDOFF_REASONS = {
    "human_requested": "The person asked to speak to someone",
    "high_value": "High-value opportunity",
    "policy_requires_human": "Policy requires a person for this step",
    "employee_uncertain": "The AI employee was not confident enough to proceed",
    "unsupported_request": "The request is outside this employee's job",
    "complaint": "The person complained",
    "legal_or_compliance": "A legal or compliance concern was raised",
    "hostile": "The conversation became hostile",
    "billing_or_payment": "A payment or billing question outside this authority",
    "tool_failure": "A tool or provider failed repeatedly",
    "qualification_threshold": "The person met the qualification threshold",
    "appointment_requires_human": "The appointment needs a person to confirm",
}


# ── REFUSAL CODES (the tool gateway's vocabulary) ───────────────────────────
#
# A refusal is DATA, with a code a test can assert on and a screen can explain.
# "403" alone tells an operator nothing about which of eleven gates said no.
DENY_UNKNOWN_TOOL = "unknown_tool"
DENY_NOT_IN_TEMPLATE = "tool_not_in_template"
DENY_NOT_IN_BRAND_OFFERING = "tool_not_in_brand_offering"
DENY_NOT_GRANTED = "tool_not_granted_to_employee"
DENY_EMPLOYEE_INACTIVE = "employee_not_active"
DENY_EMPLOYEE_PAUSED = "employee_paused"
DENY_KILLED = "kill_switch_engaged"
DENY_ACTIVATION_STAGE = "activation_stage_forbids_action"
DENY_NOT_ENTITLED = "customer_not_entitled"
DENY_FEATURE_OFF = "customer_feature_disabled"
DENY_CHANNEL_OFF = "channel_not_enabled"
DENY_OUTSIDE_HOURS = "outside_operating_hours"
DENY_INELIGIBLE_CONTACT = "contact_not_eligible"
DENY_ELIGIBILITY_REVIEW = "contact_requires_review"
DENY_TENANT_MISMATCH = "record_not_in_this_tenant"
DENY_RECORD_NOT_FOUND = "record_not_found"
DENY_BAD_ARGUMENTS = "invalid_arguments"
DENY_RATE_LIMIT = "rate_limit_exceeded"
DENY_DAILY_CAP = "daily_send_cap_reached"
DENY_RUN_BUDGET = "run_budget_exhausted"
DENY_LIVE_VOICE_DISABLED = "live_voice_disabled"
DENY_WORK_ITEM_MISMATCH = "work_item_not_for_this_employee"
DENY_DUPLICATE = "duplicate_suppressed_by_idempotency"
DENY_BUSINESS_RULE = "business_rule_refused"
DENY_NO_AUTHORITY = "employee_lacks_authority_for_this_record"


# ── RUN LIMITS ──────────────────────────────────────────────────────────────
#
# RUNAWAY PROTECTION IS A CEILING, NOT A SUGGESTION. These are the defaults;
# an employee's configuration may lower them and may never raise them past
# MAX_* below. A language model asking for one more turn is not a reason.
DEFAULT_MAX_ITERATIONS = 8
DEFAULT_MAX_TOOL_CALLS = 16
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3
DEFAULT_RUN_SECONDS = 120

MAX_ITERATIONS_CEILING = 24
MAX_TOOL_CALLS_CEILING = 48

# How many touches one work item may accumulate before it is EXHAUSTED.
DEFAULT_MAX_TOUCHES = 9

# Backoff between attempts after a failure, in seconds, by consecutive-failure
# count. The last value repeats.
RETRY_BACKOFF_SECONDS = (60, 300, 1800, 7200)

# How long a claim is held before another worker may take the item. A worker
# that dies mid-run must not park a record forever.
CLAIM_LEASE_SECONDS = 300


# ── ACTOR KINDS (the audit vocabulary) ──────────────────────────────────────
ACTOR_AI_EMPLOYEE = "ai_employee"
ACTOR_HUMAN = "human"
ACTOR_SYSTEM = "system"
ACTOR_SUPERVISOR = "ai_supervisor"
