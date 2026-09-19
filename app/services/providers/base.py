"""THE PROVIDER BOUNDARY.

===========================================================================
WHY A BOUNDARY AND NOT A CHARIOT MODULE
===========================================================================

A retail-energy workspace transacts with an electricity provider: it lists
plans, verifies a service address, checks a start date, submits an enrollment
and later a renewal. Those are the concepts. "Chariot Energy" is one company
that implements them, with its own URLs, its own field spellings and its own
English-sentence status strings.

If the company's names leak upward, every screen, job and test in the vertical
learns them, and adding a second retailer means finding all of it again. So
this module defines the CONCEPTS and the NORMALIZED RESULT, and an adapter
translates one provider's wire format into them.

===========================================================================
THE ONE RULE THIS FILE EXISTS TO ENFORCE
===========================================================================

    SUBMISSION IS NOT CONFIRMATION.

A provider call returning HTTP 200 means the provider received and answered
the request. It does NOT mean the account is enrolled. The answer carries its
own verdict — completed, pending a deposit waiver, blocked on a credit check,
rejected on validation — and only that verdict decides the state.

`normalize_*` below therefore never returns a confirmed state by default. The
default for an answer nobody has taught it to read is PENDING_PROVIDER, which
is the state that gets a human to look. A vocabulary that guesses "probably
fine" is how an account gets told it has power when it has not.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ── NORMALIZED TRANSACTION STATE ────────────────────────────────────────────
#
# EvoSys's own words for where a provider transaction stands. The provider's
# raw Status and Message are kept ALONGSIDE these, never replaced by them:
# the normalized value is what the platform acts on, the raw pair is what a
# human reads when the two disagree.

# The provider has finished the work and said so.
PROVIDER_CONFIRMED = "provider_confirmed"
# Accepted, outcome not yet decided by the provider. The honest resting state.
PENDING_PROVIDER = "pending_provider"
# The provider needs something more from the customer before it can finish.
ACTION_REQUIRED = "action_required"
# The provider processed the request and refused it on its own merits.
PROVIDER_FAILED = "provider_failed"
# The request never became a real attempt: our data did not pass validation.
FAILED_VALIDATION = "failed_validation"
# We could not complete a conversation with the provider at all — transport,
# credential, or an answer we could not parse. NOT a statement about the
# customer, and never shown to one as a refusal.
INTEGRATION_ERROR = "integration_error"
# Read-only lookups that simply succeeded. Never used for a transaction.
OK = "ok"

TERMINAL_SUCCESS = (PROVIDER_CONFIRMED,)
NEEDS_HUMAN = (ACTION_REQUIRED, PROVIDER_FAILED, FAILED_VALIDATION,
               INTEGRATION_ERROR)
NORMALIZED_STATES = (PROVIDER_CONFIRMED, PENDING_PROVIDER, ACTION_REQUIRED,
                     PROVIDER_FAILED, FAILED_VALIDATION, INTEGRATION_ERROR, OK)


# ── WHY A TRANSACTION NEEDS A HUMAN ─────────────────────────────────────────
#
# A reason code, not a sentence. The sentence is the provider's; this is what
# the platform routes on, so it has to be a closed set.
REASON_DEPOSIT_REQUIRED = "deposit_required"
REASON_DEPOSIT_WAIVER_PENDING = "deposit_waiver_pending"
REASON_CREDIT_CHECK_FAILED = "credit_check_failed"
REASON_AUTOPAY_REJECTED = "autopay_rejected"
REASON_VALIDATION = "validation"
REASON_CREDENTIAL = "credential"
REASON_TRANSPORT = "transport"
REASON_UNRECOGNISED_RESPONSE = "unrecognised_response"


# ── OPERATIONS ──────────────────────────────────────────────────────────────
#
# Named here so a transaction row records WHICH concept was attempted rather
# than a provider's endpoint name, and so the read-only set is a fact the code
# can check rather than a convention somebody remembers.

OP_LIST_PLANS = "list_plans"
OP_SEARCH_ADDRESS = "search_address"
OP_SERVICE_DATE = "service_date"
OP_DEPOSIT_WAIVERS = "deposit_waivers"
OP_UTILITY_CHARGES = "utility_charges"
OP_RENEWAL_ELIGIBILITY = "renewal_eligibility"
OP_SUBMIT_ENROLLMENT = "submit_enrollment"
OP_SUBMIT_RENEWAL = "submit_renewal"

READ_ONLY_OPERATIONS = (OP_LIST_PLANS, OP_SEARCH_ADDRESS, OP_SERVICE_DATE,
                        OP_DEPOSIT_WAIVERS, OP_UTILITY_CHARGES,
                        OP_RENEWAL_ELIGIBILITY)
TRANSACTIONAL_OPERATIONS = (OP_SUBMIT_ENROLLMENT, OP_SUBMIT_RENEWAL)
OPERATIONS = READ_ONLY_OPERATIONS + TRANSACTIONAL_OPERATIONS


# ── THE RESULT EVERY ADAPTER RETURNS ────────────────────────────────────────

@dataclass
class ProviderResult:
    """One answer from a provider, in EvoSys's words and in the provider's.

    `data` is the useful payload for a read. `raw_status` / `raw_message` are
    the provider's own strings, carried verbatim so a human can always see
    what was actually said — the normalized value is a summary, and a summary
    is the thing that turns out to be wrong at 2am.
    """
    operation: str
    normalized_status: str
    ok: bool = False
    raw_status: Optional[str] = None
    raw_message: Optional[str] = None
    reference_number: Optional[str] = None
    reason: Optional[str] = None
    # Provider-quoted money, kept as the provider wrote it ("$632.00"). Not
    # parsed into a number here: a currency string reformatted by us and then
    # shown to a customer is a number we invented.
    amount_quoted: Optional[str] = None
    data: Any = None
    http_status: Optional[int] = None
    # Never contains anything from SENSITIVE_FIELDS — `redact()` below is
    # applied before an adapter puts a request echo anywhere near this.
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def needs_human(self) -> bool:
        return self.normalized_status in NEEDS_HUMAN

    @property
    def is_final_success(self) -> bool:
        return self.normalized_status in TERMINAL_SUCCESS


# ── WHAT MUST NEVER BE PERSISTED OR LOGGED ──────────────────────────────────
#
# An enrollment carries identity and payment data. It has to reach the
# provider; it must not settle anywhere on the way. These are the field names
# the energy contract uses for that data — `redact` is applied to any dict
# heading for a log, a transaction row, a test fixture or a model prompt, and
# `assert_no_sensitive` refuses outright rather than quietly stripping, so a
# new field name added upstream fails loudly instead of leaking silently.
SENSITIVE_FIELDS = frozenset({
    "ssn", "socialsecuritynumber", "social_security_number",
    "driverslicence", "driverslicense", "drivers_licence", "drivers_license",
    "driverslicencestate", "driverslicensestate",
    "dob", "dateofbirth", "date_of_birth",
    "creditcardnumber", "creditcardcvc", "creditcardname",
    "creditcardexpirationmonth", "creditcardexpirationyear",
    "depositcardnumber", "depositcvc",
    "depositcardexpirationmonth", "depositcardexpirationyear",
    "routingnumber", "accountnumber", "nameonaccount",
    "password", "confirmpassword", "securityquestion", "securityanswer",
    "card", "cardnumber", "cvv", "cvc",
})

REDACTED = "[redacted]"


def _is_sensitive(key: str) -> bool:
    return str(key).strip().lower().replace("-", "").replace("_", "") in {
        k.replace("_", "") for k in SENSITIVE_FIELDS}


def redact(payload: Any) -> Any:
    """A copy safe to persist, log or show. Recurses; never mutates the input.

    Values are replaced, not dropped: a log that silently loses a key reads
    as "the provider never asked for it", which is a different bug.
    """
    if isinstance(payload, dict):
        return {k: (REDACTED if _is_sensitive(k) else redact(v))
                for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redact(v) for v in payload]
    return payload


def assert_no_sensitive(payload: Any, where: str = "payload") -> None:
    """Refuse rather than strip.

    Used on the way INTO anything durable. Stripping would make a leak
    invisible; this makes it a test failure and a 500, which is the only way
    the next person finds out before a customer's SSN is in a database.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if _is_sensitive(key):
                raise ValueError(
                    "%s carries sensitive field %r, which must never be "
                    "persisted or logged" % (where, key))
            assert_no_sensitive(value, where)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            assert_no_sensitive(value, where)
