"""T8 - THE DEPLOYMENT VOCABULARY, DEFINED ONCE.

Every lifecycle state, commercial state, readiness verdict and refusal code
this layer can produce lives here and nowhere else, for the reason T6's own
constants file gives: two copies of a state name is how a state machine grows
a transition nobody wrote.

PLAIN LOWERCASE STRINGS IN A VARCHAR, never a Postgres enum. That is the
standing rule recorded in app/auto_migrate.py - SQLAlchemy's SAEnum writes the
Python MEMBER NAME into Postgres, and this codebase has already migrated two
columns back out of database enums because of it.

THE TWO MACHINES ARE SEPARATE ON PURPOSE. `DEPLOYMENT_STATES` is operational -
where a hired employee is in its life. `COMMERCIAL_STATES` mirror what T2 says
about entitlement. Neither is derived from the other, and the brief is explicit
about why: paid does not mean safe to activate, paused does not mean cancelled,
and a cancelled entitlement must not leave an active employee.
"""

# ---------------------------------------------------------------------------
# DEPLOYMENT LIFECYCLE
# ---------------------------------------------------------------------------
#
# AVAILABLE is a CATALOGUE state, not a row state: it describes a job a brand
# offers that this customer has not taken. It is named here because the brief
# names it and because the catalogue payload reports it, but no
# `ai_employee_deployments` row is ever written in it - a row begins at
# SELECTED, which is the first thing that is true about a customer rather than
# about a brand.

AVAILABLE = "available"

SELECTED = "selected"
CONFIGURING = "configuring"
VALIDATION_REQUIRED = "validation_required"
READY = "ready"
CONTROLLED = "controlled"
ACTIVE = "active"
PAUSED = "paused"
SUSPENDED = "suspended"
RETIRED = "retired"

ROW_STATES = (SELECTED, CONFIGURING, VALIDATION_REQUIRED, READY, CONTROLLED,
              ACTIVE, PAUSED, SUSPENDED, RETIRED)
ALL_STATES = (AVAILABLE,) + ROW_STATES

# The states in which an actor exists and an operator has switched it on.
# Membership here is NOT permission to act: T6's activation resolver and T7's
# gate chain answer that, live, on every call. This set is what a SCREEN reads.
LIVE_STATES = frozenset({CONTROLLED, ACTIVE})

# States in which the deployment holds no running actor.
DORMANT_STATES = frozenset({SELECTED, CONFIGURING, VALIDATION_REQUIRED, READY,
                            PAUSED, SUSPENDED, RETIRED})

TERMINAL_STATES = frozenset({RETIRED})

# Human wording. The customer's screen reads these; the engine never does.
STATE_LABELS = {
    AVAILABLE: "Available to hire",
    SELECTED: "Selected",
    CONFIGURING: "Being set up",
    VALIDATION_REQUIRED: "Needs attention",
    READY: "Ready to start",
    CONTROLLED: "Working - controlled",
    ACTIVE: "Working",
    PAUSED: "Paused",
    SUSPENDED: "Stopped",
    RETIRED: "Retired",
}

# THE TRANSITION TABLE IS THE STATE MACHINE. An edge that is not here cannot be
# taken, and `lifecycle.transition` refuses rather than logging and continuing.
#
# Note what is absent. There is no edge out of RETIRED - a retired deployment
# is not re-hired, it is replaced, because re-using the row would attach a new
# commercial arrangement to an old one's history. And SUSPENDED never goes
# straight back to a live state: re-entitlement returns a deployment to READY,
# and somebody has to switch it on again. Section 6 - no existing customer
# becomes active because something changed underneath them.
ALLOWED_TRANSITIONS = {
    SELECTED: {CONFIGURING, VALIDATION_REQUIRED, SUSPENDED, RETIRED},
    CONFIGURING: {CONFIGURING, VALIDATION_REQUIRED, READY, SUSPENDED, RETIRED},
    VALIDATION_REQUIRED: {CONFIGURING, VALIDATION_REQUIRED, READY, SUSPENDED,
                          RETIRED},
    READY: {CONFIGURING, VALIDATION_REQUIRED, CONTROLLED, ACTIVE, PAUSED,
            SUSPENDED, RETIRED},
    CONTROLLED: {ACTIVE, CONTROLLED, READY, PAUSED, SUSPENDED, RETIRED},
    ACTIVE: {CONTROLLED, ACTIVE, READY, PAUSED, SUSPENDED, RETIRED},
    # Resuming returns to the stage the pause was taken from, which is why the
    # live stages are reachable from here and not from SUSPENDED. A pause is an
    # operator's own reversible act on something they had already approved.
    PAUSED: {READY, CONTROLLED, ACTIVE, VALIDATION_REQUIRED, SUSPENDED,
             RETIRED},
    SUSPENDED: {READY, VALIDATION_REQUIRED, SUSPENDED, RETIRED},
    RETIRED: set(),
}


# ---------------------------------------------------------------------------
# COMMERCIAL STATE - a mirror of T2's answer, never an opinion
# ---------------------------------------------------------------------------

COMM_UNKNOWN = "unknown"            # not yet resolved
COMM_NOT_OFFERED = "not_offered"    # this brand does not offer the job
COMM_AVAILABLE = "available"        # offered and buyable; nobody has bought it
COMM_INCLUDED = "included"          # the customer's package includes it
COMM_PENDING = "pending"            # a checkout exists and nobody has paid
COMM_ENTITLED = "entitled"          # a live purchase, per T2's own LIVE states
COMM_LAPSED = "lapsed"              # was entitled; the arrangement ended

COMMERCIAL_STATES = (COMM_UNKNOWN, COMM_NOT_OFFERED, COMM_AVAILABLE,
                     COMM_INCLUDED, COMM_PENDING, COMM_ENTITLED, COMM_LAPSED)

# The commercial states in which an employee may hold a live operational stage.
# PENDING IS NOT ONE OF THEM: opening a checkout is not paying, and section 2
# says so in as many words.
COMMERCIALLY_LIVE = frozenset({COMM_INCLUDED, COMM_ENTITLED})

COMMERCIAL_LABELS = {
    COMM_UNKNOWN: "Not checked yet",
    COMM_NOT_OFFERED: "Not offered here",
    COMM_AVAILABLE: "Available to add",
    COMM_INCLUDED: "Included in your package",
    COMM_PENDING: "Awaiting payment",
    COMM_ENTITLED: "Included on your account",
    COMM_LAPSED: "No longer on your account",
}


# ---------------------------------------------------------------------------
# COMMERCIAL MODES a brand may configure
# ---------------------------------------------------------------------------

MODE_INCLUDED = "included"
MODE_ADDON = "addon"
MODE_QUOTED = "quoted"
MODE_CAPACITY = "capacity"
COMMERCIAL_MODES = (MODE_INCLUDED, MODE_ADDON, MODE_QUOTED, MODE_CAPACITY)

MODE_LABELS = {
    MODE_INCLUDED: "Included with a package",
    MODE_ADDON: "Recurring add-on",
    MODE_QUOTED: "Quoted per deal",
    MODE_CAPACITY: "Additional capacity",
}


# ---------------------------------------------------------------------------
# READINESS
# ---------------------------------------------------------------------------
#
# THREE VERDICTS, AND THE MIDDLE ONE IS NOT A SOFTER NO. REVIEW_REQUIRED means
# a person has to look - a configuration that is complete and contentious, a
# channel that is live-capable, a handoff pointing somewhere unusual. It never
# behaves like READY, which is the same rule T6's contact eligibility already
# keeps about REQUIRES_REVIEW.

READY_YES = "ready"
READY_NO = "not_ready"
READY_REVIEW = "review_required"
READINESS_VERDICTS = (READY_YES, READY_NO, READY_REVIEW)

# Severity of one failed check. `blocking` makes the whole verdict NOT READY;
# `review` makes it REVIEW REQUIRED unless something blocking already did.
SEVERITY_BLOCKING = "blocking"
SEVERITY_REVIEW = "review"
SEVERITY_INFO = "info"


# ---------------------------------------------------------------------------
# REFUSAL CODES
# ---------------------------------------------------------------------------
#
# A refusal is DATA, with a code a test can assert on and a screen can explain.
# "403" tells an operator nothing about which of a dozen gates said no.

R_UNKNOWN_TEMPLATE = "unknown_template"
R_NOT_OFFERED = "template_not_offered_by_brand"
R_OFFERING_DISABLED = "brand_offering_disabled"
R_NOT_ENTITLED = "customer_not_entitled"
R_ENTITLEMENT_PENDING = "entitlement_pending_payment"
R_PACKAGE_INELIGIBLE = "package_not_eligible_for_this_employee"
R_CAPACITY_REACHED = "employee_capacity_reached"
R_FEATURE_OFF = "customer_feature_disabled"
R_ALREADY_HELD = "already_deployed"
R_NOT_READY = "readiness_not_satisfied"
R_REVIEW_REQUIRED = "readiness_requires_review"
R_CONTROLLED_FIRST = "controlled_stage_required_first"
R_ILLEGAL_TRANSITION = "illegal_deployment_transition"
R_NOT_AUTHORIZED = "actor_not_authorized_for_this_action"
R_TENANT_MISMATCH = "record_not_in_this_tenant"
R_RETIRED = "deployment_is_retired"
R_ENGINE_UNAVAILABLE = "workforce_engine_unavailable"
R_OPERATIONS_UNAVAILABLE = "operations_layer_unavailable"
R_FORBIDDEN_CONFIG = "configuration_field_not_permitted"
R_MISSING_CONFIG = "required_business_information_missing"
R_HANDOFF_LOOP = "handoff_would_create_a_loop"
R_NO_HANDOFF_TARGET = "no_handoff_destination_configured"
R_SUSPENDED = "deployment_is_suspended"
R_STALE_VIEW = "deployment_changed_since_this_screen_loaded"


# ---------------------------------------------------------------------------
# ACTOR KINDS for the deployment event log
# ---------------------------------------------------------------------------

ACTOR_HUMAN = "human"
ACTOR_SYSTEM = "system"
ACTOR_COMMERCE = "commerce"
ACTOR_PLATFORM = "platform"


# ---------------------------------------------------------------------------
# CONFIGURATION - what a customer answers, and what they must never be shown
# ---------------------------------------------------------------------------
#
# THE PRODUCT SHOULD FEEL LIKE HIRING AN EMPLOYEE, NOT CONFIGURING AN LLM
# (section 4). These are the BUSINESS fields this layer adds on top of the
# questions each platform template already declares in T6's registry. Every one
# of them is a fact about the customer's business.

BUSINESS_FIELDS = (
    ("business_unit", "Which part of the business does this employee work for?",
     "text", False),
    ("location", "Which location does it represent?", "text", False),
    ("service_area", "Which territory or service area does it cover?",
     "text", False),
    ("working_hours", "When may it work?", "hours", True),
    ("timezone", "Which timezone are those hours in?", "timezone", True),
    ("channels", "Which ways may it contact people?", "channels", True),
    ("qualification_questions", "What does it need to find out?",
     "text_list", False),
    ("required_information", "What must it collect before handing over?",
     "text_list", False),
    ("appointment_type", "What kind of appointment does it book?",
     "appointment_type", False),
    ("booking_owner", "Whose calendar does it book into?", "user", False),
    ("handoff_user_id", "Who takes over when a person is needed?",
     "user", True),
    ("handoff_team", "Which team should that person come from?", "text", False),
    ("escalation_conditions", "What should always go to a person?",
     "text_list", False),
    ("backup_owner", "Who covers when that person is unavailable?",
     "user", False),
    ("followup_policy", "How persistent should follow-up be?",
     "followup", False),
    ("goals", "What does a good outcome look like?", "text_list", False),
    ("inbound_enabled", "Should it answer people who contact you?",
     "boolean", False),
    ("outbound_enabled", "Should it reach out first?", "boolean", False),
    ("voice_enabled", "Should it be able to speak to people by phone?",
     "boolean", False),
    ("knowledge_kinds", "Which of your information may it use?",
     "knowledge", False),
    ("audience", "Which group of records may it work?", "audience", False),
    ("handoff_to_employee_id", "Which AI employee picks up after this one?",
     "ai_employee", False),
)

BUSINESS_FIELD_KEYS = tuple(f[0] for f in BUSINESS_FIELDS)

# WHAT A CUSTOMER MAY NEVER CONFIGURE, whatever they put in the request body.
#
# Section 4 lists these as things not to EXPOSE. Refusing them on the way in is
# the stronger version of the same rule: a field that is never displayed but is
# accepted is a field somebody reaches with curl. Matching is by normalised
# substring, so `model_temperature` and `systemPrompt` are both caught.
FORBIDDEN_CONFIG_FRAGMENTS = (
    "prompt", "system_message", "temperature", "top_p", "max_tokens",
    "model", "provider", "api_key", "apikey", "secret", "token", "credential",
    "password", "tool_keys", "tools", "function_call", "sql", "query_raw",
    "endpoint", "webhook_url", "authorization", "role_override", "capability",
    "entitlement", "price", "amount_cents", "stripe",
)


def forbidden_reason(key):
    """Why this configuration key is refused, or None if it is fine.

    Returns the FRAGMENT that matched rather than a generic message, because
    the person who has to fix this is usually an integrator who typed a
    reasonable-looking key and needs to know which word was the problem.
    """
    k = (key or "").strip().lower().replace("-", "_")
    if not k:
        return "empty"
    for fragment in FORBIDDEN_CONFIG_FRAGMENTS:
        if fragment in k:
            return fragment
    return None
