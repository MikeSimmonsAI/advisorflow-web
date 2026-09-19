"""THE SENSITIVE-DATA BOUNDARY, AND THE PIPELINE STATES EITHER SIDE OF IT.

===========================================================================
WHAT THIS FILE IS FOR
===========================================================================

An energy enrollment needs a social security number or a driver's licence, a
date of birth, and often a card or bank account. The platform has never held
data of that class and must not start holding it as a side effect of one
vertical.

So the enrollment payload is ASSEMBLED HERE, IN MEMORY, FOR THE LENGTH OF
ONE OUTBOUND REQUEST, and is never handed to anything that persists. The
path is:

    customer's browser
      -> a server-side handler that holds the payload in a local variable
      -> the Chariot adapter
      -> a redacted ProviderResult
      -> provider_transactions.record(), which REFUSES sensitive fields

`build_enrollment_payload` returns the payload and, separately, the
`audit_fields` that may be kept. Nothing merges them. A caller that wants to
store the payload has to defeat `assert_no_sensitive` to do it, and cannot do
it by accident.

WHAT THIS MEANS FOR THE AI. The conversation engine never sees any of it.
The model is given the pipeline stage and the non-sensitive plan snapshot;
an identity or payment field has no route into a prompt because it never
lands in a lead field, an activity, a note or a conversation body.
"""

from typing import Any, Dict, Optional, Tuple

from app.services.providers import base, chariot


# ── PIPELINE STATES FOR AN ENERGY WORKSPACE ─────────────────────────────────
#
# These are values for `PipelineConversation.stage`, which is a free string —
# so this vertical's vocabulary costs no schema change and cannot disturb the
# platform's own stages. They are CUSTOMER pipeline states; brand-sales
# opportunities are a different table and are not touched by any of this.
#
# ADVANCEMENT IS EVENT-DRIVEN. Each state below names the event that causes
# it. There is no state reached by elapsed time, and the provider states are
# reached only by an actual provider answer.

STAGE_NEW = "new"                             # lead created
STAGE_CONTACTED = "contacted"                 # first outbound touch sent
STAGE_ENGAGED = "engaged"                     # inbound reply received
STAGE_QUALIFIED = "qualified"                 # consent + qualification passed
STAGE_PLAN_REVIEW = "plan_review"             # plans shown to the customer
STAGE_ENROLLMENT_READY = "enrollment_ready"   # plan + ESIID + date all held
STAGE_PROVIDER_SUBMITTED = "provider_submitted"   # request accepted by us
STAGE_PROVIDER_PENDING = "provider_pending"   # provider said pending
STAGE_DEPOSIT_REQUIRED = "deposit_required"   # provider wants a deposit
STAGE_PROVIDER_CONFIRMED = "provider_confirmed"   # provider said completed
STAGE_ENROLLED = "enrolled"                   # confirmed and reconciled
STAGE_PROVIDER_FAILED = "provider_failed"     # provider refused
STAGE_HUMAN_REVIEW = "human_review"           # a person owes an answer
STAGE_NOT_INTERESTED = "not_interested"
STAGE_OPTED_OUT = "opted_out"

ENERGY_STAGES = (
    STAGE_NEW, STAGE_CONTACTED, STAGE_ENGAGED, STAGE_QUALIFIED,
    STAGE_PLAN_REVIEW, STAGE_ENROLLMENT_READY, STAGE_PROVIDER_SUBMITTED,
    STAGE_PROVIDER_PENDING, STAGE_DEPOSIT_REQUIRED, STAGE_PROVIDER_CONFIRMED,
    STAGE_ENROLLED, STAGE_PROVIDER_FAILED, STAGE_HUMAN_REVIEW,
    STAGE_NOT_INTERESTED, STAGE_OPTED_OUT,
)

# Stages at which automated prospecting must stop. `provider_confirmed` and
# `enrolled` are in here for a reason the guide forces on us: Chariot sends
# its OWN confirmation email and SMS on a completed enrollment. Continuing to
# nurture somebody Chariot has just told they are enrolled is how a customer
# gets two different companies contradicting each other about their power.
STOP_AUTOMATION_STAGES = frozenset({
    STAGE_PROVIDER_CONFIRMED, STAGE_ENROLLED, STAGE_NOT_INTERESTED,
    STAGE_OPTED_OUT, STAGE_HUMAN_REVIEW, STAGE_PROVIDER_FAILED,
    STAGE_DEPOSIT_REQUIRED,
})

# Stages a person owes an answer on.
HUMAN_QUEUE_STAGES = frozenset({
    STAGE_DEPOSIT_REQUIRED, STAGE_PROVIDER_FAILED, STAGE_HUMAN_REVIEW,
})


def stage_for_result(result: base.ProviderResult) -> str:
    """The pipeline stage an enrollment answer puts the customer in.

    NOTHING HERE RETURNS `enrolled`. `provider_confirmed` is as far as a
    provider answer can move it: `enrolled` is the workspace's own assertion
    that the confirmation has been reconciled, and only a person makes it.
    The distinction exists because "Chariot says completed" and "we have
    checked that this customer has power" are different facts, and the day
    they differ is the day this matters.
    """
    if result.normalized_status == base.PROVIDER_CONFIRMED:
        return STAGE_PROVIDER_CONFIRMED
    if result.normalized_status == base.PENDING_PROVIDER:
        return STAGE_PROVIDER_PENDING
    if result.normalized_status == base.ACTION_REQUIRED:
        if result.reason == base.REASON_DEPOSIT_REQUIRED:
            return STAGE_DEPOSIT_REQUIRED
        return STAGE_HUMAN_REVIEW
    if result.normalized_status == base.PROVIDER_FAILED:
        return STAGE_PROVIDER_FAILED
    # failed_validation and integration_error are OUR faults, not verdicts on
    # the customer. They go to a person, never to a "failed" state that reads
    # as the customer having been declined.
    return STAGE_HUMAN_REVIEW


def automation_should_stop(stage: str) -> bool:
    return stage in STOP_AUTOMATION_STAGES


# ── LEAD CLASSIFICATION FOR THIS VERTICAL ───────────────────────────────────
#
# Classification decides which automation a lead gets, so it is a closed set
# and it is resolved from what the lead actually carries — never guessed by a
# model. COMMERCIAL is the one that matters most: it routes AWAY from the
# residential provider path entirely, because Chariot's guide documents
# CustomerTypeID=1 (Residential) and nothing else.

CLASS_RESIDENTIAL_RATE_REVIEW = "residential_rate_review"
CLASS_COMMERCIAL_ENERGY = "commercial_energy"
CLASS_BILL_ANALYSIS = "bill_analysis"
CLASS_MOVE_CONCIERGE = "move_concierge"
CLASS_PARTNER_REFERRAL = "partner_referral"
CLASS_RENEWAL = "renewal"
CLASS_GENERAL_INQUIRY = "general_inquiry"

CLASSIFICATIONS = (
    CLASS_RESIDENTIAL_RATE_REVIEW, CLASS_COMMERCIAL_ENERGY,
    CLASS_BILL_ANALYSIS, CLASS_MOVE_CONCIERGE, CLASS_PARTNER_REFERRAL,
    CLASS_RENEWAL, CLASS_GENERAL_INQUIRY,
)

# Which classifications may ever reach the residential enrollment API.
# COMMERCIAL IS NOT ONE OF THEM, and this is the assertion that enforces it
# rather than a comment hoping somebody remembers.
PROVIDER_ELIGIBLE_CLASSIFICATIONS = frozenset({
    CLASS_RESIDENTIAL_RATE_REVIEW, CLASS_BILL_ANALYSIS,
    CLASS_MOVE_CONCIERGE, CLASS_RENEWAL,
})

HUMAN_ONLY_CLASSIFICATIONS = frozenset({
    CLASS_COMMERCIAL_ENERGY, CLASS_PARTNER_REFERRAL,
})


def classify(*, premise_type: Optional[str] = None,
             source_detail: Optional[str] = None,
             tier: Optional[str] = None,
             intent_hint: Optional[str] = None) -> str:
    """Resolve a lead's classification from facts it already carries.

    Deterministic and inspectable. A model may SUGGEST an intent hint from a
    conversation, but the decision that routes a lead into or away from a
    provider transaction is made here, from data, so it can be explained.
    """
    premise = (premise_type or "").strip().lower()
    source = (source_detail or "").strip().lower()
    hint = (intent_hint or "").strip().lower()
    tier_value = (tier or "").strip().lower()

    # Commercial wins over everything. A commercial premise that arrived
    # through a residential form is still commercial.
    if premise == "commercial" or "commercial" in hint or "business" in hint:
        return CLASS_COMMERCIAL_ENERGY
    if "move concierge" in source or "move" in hint:
        return CLASS_MOVE_CONCIERGE
    if tier_value in ("contract_signed", "renewal_due") or "renewal" in hint:
        return CLASS_RENEWAL
    if "partner" in source or "referral" in source:
        return CLASS_PARTNER_REFERRAL
    if "bill" in hint or "bill analysis" in source:
        return CLASS_BILL_ANALYSIS
    if tier_value in ("new_inquiry", "rate_review", "proposal_sent") \
            or "rate" in hint:
        return CLASS_RESIDENTIAL_RATE_REVIEW
    return CLASS_GENERAL_INQUIRY


def may_submit_to_provider(classification: str) -> bool:
    return classification in PROVIDER_ELIGIBLE_CLASSIFICATIONS


# ── THE PAYLOAD, AND THE PART OF IT THAT MAY BE KEPT ────────────────────────

class EnrollmentNotReady(ValueError):
    """The request would be incomplete or is not allowed to be made."""


# Chariot's mandatory residential fields, from the guide's Enrollment Submit
# input table. Checked before anything leaves this process, so an incomplete
# enrollment fails here with a readable message rather than coming back as
# "Validation Failed" with the customer already told to wait.
REQUIRED_FIELDS = (
    "Zip", "CustomerTypeID", "ProductID", "FirstName", "LastName", "DOB",
    "EmailAddress", "PhoneNumber", "BillDeliverMethodID", "languagePreference",
    "ESIID", "ServiceType", "ServiceStartDate", "IsAccountHolder",
    "BillingAddressTypeID", "AutoPay", "PayDeposit", "IsPromoInterested",
)

# Kept out of `audit_fields` and out of every durable structure. This is the
# same set `providers.base.SENSITIVE_FIELDS` guards, named here in Chariot's
# own spelling so the split is legible against the guide.
SENSITIVE_ENROLLMENT_FIELDS = (
    "SSN", "DriversLicence", "DriversLicenceState", "DOB",
    "CreditCardNumber", "CreditCardCVC", "CreditCardName",
    "CreditCardExpirationMonth", "CreditCardExpirationYear",
    "DepositCardNumber", "DepositCVC",
    "DepositCardExpirationMonth", "DepositCardExpirationYear",
    "RoutingNumber", "AccountNumber", "NameOnAccount",
    "Password", "ConfirmPassword", "SecurityQuestion", "SecurityAnswer",
)


def build_enrollment_payload(
        *, classification: str, fields: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return `(payload, audit_fields)`.

    `payload` is for the wire and nothing else. `audit_fields` is what may be
    written down: the product, the meter, the service type and date, and the
    non-identifying parts of the request.

    THEY ARE RETURNED SEPARATELY AND NEVER MERGED. A caller that persists
    `audit_fields` is safe by construction; one that persists `payload` hits
    `assert_no_sensitive` and gets an exception, which is the point.
    """
    if not may_submit_to_provider(classification):
        # THE COMMERCIAL GUARD, ENFORCED AND NOT DOCUMENTED-ONLY.
        # Chariot's guide documents CustomerTypeID=1 (Residential) and no
        # commercial equivalent. Pushing a business through the residential
        # API would submit a real enrollment under the wrong customer type.
        raise EnrollmentNotReady(
            "%s is not eligible for a residential provider enrollment. The "
            "provider's API documents residential customers only; this lead "
            "belongs in the human commercial workflow." % classification)

    payload = {k: v for k, v in (fields or {}).items() if v is not None}
    payload.setdefault("CustomerTypeID", chariot.CUSTOMER_TYPE_RESIDENTIAL)

    missing = [f for f in REQUIRED_FIELDS
               if not str(payload.get(f, "")).strip()]
    if missing:
        raise EnrollmentNotReady(
            "enrollment is not ready: missing %s" % ", ".join(sorted(missing)))

    # "If it is No: 1-It will perform soft credit check based on SSN or
    # Driver License" — so one of the two must be present unless the caller
    # has explicitly bypassed the credit check.
    if str(payload.get("ByPassCreditCheck", "No")).strip().lower() != "yes":
        if not str(payload.get("SSN", "")).strip() and \
                not str(payload.get("DriversLicence", "")).strip():
            raise EnrollmentNotReady(
                "a soft credit check needs either SSN or DriversLicence, or "
                "ByPassCreditCheck must be Yes")

    audit_fields = {k: v for k, v in payload.items()
                    if k not in SENSITIVE_ENROLLMENT_FIELDS}
    # Belt and braces: the split above is by name, this catches a spelling
    # the list has not learned yet.
    base.assert_no_sensitive(audit_fields, "enrollment audit_fields")
    return payload, audit_fields
