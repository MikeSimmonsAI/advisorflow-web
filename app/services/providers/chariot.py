"""CHARIOT ENERGY — broker API adapter.

Implements the concepts in `providers/base.py` against the Chariot Energy
Broker API Guide v1.0. Everything below that looks arbitrary is quoted from
that document; where it is, the quote is in the comment, because the next
person needs to know whether a spelling is ours or theirs.

===========================================================================
WHAT THE GUIDE ACTUALLY SAYS, AND THE FOUR THINGS THAT BITE
===========================================================================

1. BASE URLS.  Test  http://35.174.234.23:8084
               Prod  https://api.chariotenergy.com
   The guide's "Link:" lines all name the production host; its Sample Code
   all names the test IP. The sample code is the older artefact. Production
   is selected by configuration here, never by copying a sample.

2. THE KEY IS A QUERY/FORM PARAMETER, not a header, and it travels on EVERY
   call. It is read from the environment at call time and never stored on an
   organization row, never sent to a browser, never logged.

3. LIVE KEYS ARE BOUND TO A DOMAIN. "In Live environment you will also need
   to provide the domain from where you will be calling the API. Chariot
   Energy will map API key with broker domain for security purpose." That is
   a registration fact, not a request field — see docs/CHARIOT_INTEGRATION.md
   for which domain to give them and why it is not the Render hostname.

4. THE REFERENCE NUMBER IS MISSPELLED IN THEIR JSON: "RefrenceNumber". It is
   the authoritative external id for an enrollment and losing it is
   unrecoverable, so `_reference_of` reads that spelling AND the correct one.

===========================================================================
TRANSPORT SHAPES, WHICH ARE NOT UNIFORM
===========================================================================

GET  + query string : GetPlans, GetErcotAddresses, GetServiceTypeDate,
                      DepositWaive, GetTDSPCharges
POST + multipart    : SubmitEnrollment, GetRenewalDate, SubmitRenewal

GetRenewalDate reads like a GET in the guide's "Link:" line and is POSTed as
FormData in the guide's own sample code. The sample code is the executable
statement of intent, so it is POSTed here.
"""

import os
from typing import Any, Dict, List, Optional

import requests

from app.services.providers import base

PROVIDER = "chariot"

# Chariot's own environment names. `test` is the address in the guide; it is
# plain HTTP, which is exactly why no real person's data may go near it.
ENV_TEST = "test"
ENV_PRODUCTION = "production"

BASE_URLS = {
    ENV_TEST: "http://35.174.234.23:8084",
    ENV_PRODUCTION: "https://api.chariotenergy.com",
}

# THE ONLY NAMES THIS INTEGRATION READS FOR ITS CREDENTIAL.
#
# Server-side only. Not in render.yaml with a value, not on an organization
# row, not in a test fixture, not in a prompt. render.yaml declares the name
# with `sync: false` so Render knows the service wants it and no value is
# ever committed.
ENV_API_KEY = "CHARIOT_API_KEY"
ENV_ENVIRONMENT = "CHARIOT_ENV"          # test | production. Default: test.
ENV_PROMO_CODE = "CHARIOT_PROMO_CODE"    # Chariot issues the broker's codes.

# "CustomerTypeID Mandatory Numeric / Possible value is 1 - For Residential".
# ONE is the only documented value. The guide does not document a commercial
# type at all, which is why commercial leads do not go through this adapter.
CUSTOMER_TYPE_RESIDENTIAL = "1"

DEFAULT_TIMEOUT = 25


# ── CONFIGURATION, READ AT CALL TIME ────────────────────────────────────────

class ChariotNotConfigured(RuntimeError):
    """No usable credential. Raised BEFORE any network call is attempted.

    Deliberately not a silent no-op: an unconfigured integration that returns
    an empty plan list is indistinguishable from a provider with no plans,
    and that is how a customer gets shown "no plans available" forever.
    """


def environment() -> str:
    value = (os.getenv(ENV_ENVIRONMENT) or "").strip().lower()
    return ENV_PRODUCTION if value == ENV_PRODUCTION else ENV_TEST


def base_url() -> str:
    return BASE_URLS[environment()]


def api_key() -> str:
    key = (os.getenv(ENV_API_KEY) or "").strip()
    if not key:
        raise ChariotNotConfigured(
            "%s is not set. The Chariot broker key is server-side "
            "configuration and has no default." % ENV_API_KEY)
    return key


def promo_code() -> str:
    # Mandatory on GetPlans per the guide, and Chariot tells each broker which
    # codes it may use. Empty string is what their own sample sends when the
    # broker has none, so an empty value is passed through rather than guessed.
    return (os.getenv(ENV_PROMO_CODE) or "").strip()


def is_configured() -> bool:
    """Presence, never the value. Safe to call from a health endpoint."""
    return bool((os.getenv(ENV_API_KEY) or "").strip())


def configuration_report() -> Dict[str, Any]:
    """What an operator needs to see, with nothing secret in it.

    `key_present` and `key_fingerprint` only — the fingerprint is a short
    SHA-256 prefix, enough to confirm two environments hold the SAME key
    without either of them revealing it.
    """
    import hashlib
    raw = (os.getenv(ENV_API_KEY) or "").strip()
    return {
        "provider": PROVIDER,
        "environment": environment(),
        "base_url": base_url(),
        "key_present": bool(raw),
        "key_fingerprint": (hashlib.sha256(raw.encode()).hexdigest()[:8]
                            if raw else None),
        "promo_code_present": bool(promo_code()),
    }


# ── TRANSPORT ───────────────────────────────────────────────────────────────
#
# One seam, so every test in this repo drives the adapter without a socket and
# so there is exactly one place a credential could be logged (and is not).

def _default_transport(method: str, url: str, *, params=None, files=None,
                       timeout=DEFAULT_TIMEOUT):
    if method == "GET":
        return requests.get(url, params=params, timeout=timeout)
    # SubmitEnrollment/GetRenewalDate/SubmitRenewal are multipart in the
    # guide's sample code (`new FormData()`, `contentType: false`). requests
    # produces multipart when given `files=`, so the field tuples are built
    # that way rather than as a form body.
    return requests.post(url, files=files, timeout=timeout)


_transport = _default_transport


def set_transport(fn):
    """Swap the transport. Returns the previous one so a test can restore it.

    THE TESTS IN THIS REPO NEVER REACH CHARIOT. Not the test host either:
    that address is plain HTTP on a third party's infrastructure, and a test
    suite that talks to it is a test suite that fails when somebody else's
    server is down and that quietly sends whatever fixture data it holds to a
    machine we do not own.
    """
    global _transport
    previous = _transport
    _transport = fn
    return previous


def _multipart(fields: Dict[str, Any]):
    """Chariot's POSTs are multipart with every value as a string part."""
    return {k: (None, "" if v is None else str(v)) for k, v in fields.items()}


def _call(operation: str, method: str, path: str, fields: Dict[str, Any]):
    """One request.

    Returns `(failure, body, http_status, echo)`. `failure` is a finished
    ProviderResult when the exchange never produced a readable answer, and
    None when it did — so every caller starts with one `if failure:` and can
    then trust `body`. Returning two different types from one function is how
    a transport error ends up being read as a provider verdict.
    """
    payload = dict(fields)
    payload["Key"] = api_key()
    url = base_url() + path

    # WHAT GOES IN THE RESULT'S `detail` IS THE REDACTED ECHO, MINUS THE KEY.
    # `redact` handles the customer's data; the key is dropped outright
    # because it is not the customer's secret to redact — it is ours, and it
    # belongs in no structure that anything downstream might persist.
    echo = base.redact({k: v for k, v in payload.items() if k != "Key"})

    try:
        if method == "GET":
            response = _transport("GET", url, params=payload)
        else:
            response = _transport("POST", url, files=_multipart(payload))
    except Exception as exc:  # noqa: BLE001 - transport, not provider verdict
        return (base.ProviderResult(
            operation=operation, ok=False,
            normalized_status=base.INTEGRATION_ERROR,
            reason=base.REASON_TRANSPORT,
            raw_message=str(exc)[:300], detail={"request": echo}),
            None, None, echo)

    status_code = getattr(response, "status_code", None)
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - an unparseable body is an integration fault
        return (base.ProviderResult(
            operation=operation, ok=False,
            normalized_status=base.INTEGRATION_ERROR,
            reason=base.REASON_UNRECOGNISED_RESPONSE,
            http_status=status_code,
            raw_message=(getattr(response, "text", "") or "")[:300],
            detail={"request": echo}),
            None, status_code, echo)

    return None, body, status_code, echo


# ── READING CHARIOT'S ANSWERS ───────────────────────────────────────────────

def _first(body: Any) -> Dict[str, Any]:
    """Their list-shaped responses wrap one object in an array."""
    if isinstance(body, list):
        return body[0] if body and isinstance(body[0], dict) else {}
    return body if isinstance(body, dict) else {}


def _reference_of(body: Dict[str, Any]) -> Optional[str]:
    """THE MISSPELLING IS THEIRS AND IT IS LOAD-BEARING.

    Their JSON says "RefrenceNumber". It is the authoritative external id for
    an enrollment and there is no documented way to look one up after the
    fact, so reading only the correct spelling would lose it permanently the
    first time it arrived. Both are read; the correct spelling is preferred
    in case they ever fix it.
    """
    for key in ("ReferenceNumber", "RefrenceNumber", "referenceNumber"):
        value = body.get(key)
        if value:
            return str(value).strip()
    return None


def _invalid_key(body: Dict[str, Any]) -> bool:
    """"If API key is invalid then system will display message as:
    'Error Message : Invalid APIKey'" — checked on every operation, because
    a bad key is an operator fault and must never be reported to a customer
    as a refusal of their enrollment."""
    blob = " ".join(str(v) for v in body.values() if isinstance(v, (str, int)))
    return "invalid apikey" in blob.lower()


def normalize_enrollment(body: Dict[str, Any]) -> base.ProviderResult:
    """THE MOST IMPORTANT FUNCTION IN THIS FILE.

    Every documented SubmitEnrollment outcome, verbatim from the guide, and
    what EvoSys calls it:

      Status "Enrollment has been completed"
      Message "Passed CreditCheck & No Deposit required"
        -> provider_confirmed. Chariot says it is done.

      Status "Enrollment has been completed"
      Message "Passed CreditCheck & Submit Deposit Successfully"
        -> provider_confirmed. Done, with the deposit taken.

      Status "Enrollment has been completed with Pending Status"
      Message "Deposit Waiver has been requested"
        -> pending_provider. THEIR OWN WORDS SAY PENDING. A waiver request is
           a thing a human at Chariot decides later, and there is no endpoint
           in this guide that will ever tell us the answer. It stays pending
           until a person reconciles it.

      Status "Deposit Required Your Deposit Amount is $632.00"
      Message "Required Deposit Fields", DepositAmount "$632.00"
        -> action_required. Not a failure and NOT an enrollment: the customer
           has to do something. The quoted amount is carried as the provider
           wrote it.

      Status "CreditCheck Failed"
        -> provider_failed. Chariot processed it and refused.

      Status/Message "DepositAutopayFail"
        -> action_required. Their autopay details did not validate; this is
           recoverable by re-entering them, so it is not a refusal.

      Status "Validation Failed"
        -> failed_validation. Our request was malformed. The customer did
           nothing wrong and must never be told they were declined.

      "Error Message : Invalid APIKey"
        -> integration_error. Ours. Never surfaced to a customer.

    ANYTHING ELSE -> pending_provider with REASON_UNRECOGNISED_RESPONSE.
    Not "probably fine". A sentence this function has not been taught is a
    sentence nobody has read, and the safe resting place for an enrollment
    nobody has read is a queue with a human in front of it.
    """
    body = _first(body)
    status = str(body.get("Status") or "").strip()
    message = str(body.get("Message") or "").strip()
    low_status = status.lower()
    low_message = message.lower()
    reference = _reference_of(body)
    amount = body.get("DepositAmount")

    def result(normalized, *, ok=False, reason=None):
        return base.ProviderResult(
            operation=base.OP_SUBMIT_ENROLLMENT,
            normalized_status=normalized, ok=ok,
            raw_status=status or None, raw_message=message or None,
            reference_number=reference, reason=reason,
            amount_quoted=(str(amount) if amount else None),
            data=base.redact(body))

    if _invalid_key(body):
        return result(base.INTEGRATION_ERROR, reason=base.REASON_CREDENTIAL)

    # Checked BEFORE the "completed" prefix test: their pending status string
    # starts with the completed one, so ordering is the whole correctness of
    # this branch. "Enrollment has been completed with Pending Status"
    # startswith "Enrollment has been completed".
    if "pending status" in low_status or "waiver" in low_message:
        return result(base.PENDING_PROVIDER,
                      reason=base.REASON_DEPOSIT_WAIVER_PENDING)

    if low_status.startswith("enrollment has been completed"):
        return result(base.PROVIDER_CONFIRMED, ok=True)

    if low_status.startswith("deposit required"):
        return result(base.ACTION_REQUIRED,
                      reason=base.REASON_DEPOSIT_REQUIRED)

    if "creditcheck failed" in low_status or "credit check failed" in low_status:
        return result(base.PROVIDER_FAILED,
                      reason=base.REASON_CREDIT_CHECK_FAILED)

    if "depositautopayfail" in low_status.replace(" ", "") \
            or "depositautopayfail" in low_message.replace(" ", ""):
        return result(base.ACTION_REQUIRED, reason=base.REASON_AUTOPAY_REJECTED)

    if "validation failed" in low_status:
        return result(base.FAILED_VALIDATION, reason=base.REASON_VALIDATION)

    return result(base.PENDING_PROVIDER,
                  reason=base.REASON_UNRECOGNISED_RESPONSE)


def normalize_renewal(body: Dict[str, Any]) -> base.ProviderResult:
    """SubmitRenewal: Status "Success", Message "Renewal has been submitted
    successfully." Anything else follows the same never-guess rule."""
    body = _first(body)
    status = str(body.get("Status") or "").strip()
    message = str(body.get("Message") or "").strip()

    def result(normalized, *, ok=False, reason=None):
        return base.ProviderResult(
            operation=base.OP_SUBMIT_RENEWAL, normalized_status=normalized,
            ok=ok, raw_status=status or None, raw_message=message or None,
            reference_number=_reference_of(body), reason=reason,
            data=base.redact(body))

    if _invalid_key(body):
        return result(base.INTEGRATION_ERROR, reason=base.REASON_CREDENTIAL)
    if status.lower() == "success":
        return result(base.PROVIDER_CONFIRMED, ok=True)
    if "validation failed" in status.lower():
        return result(base.FAILED_VALIDATION, reason=base.REASON_VALIDATION)
    return result(base.PENDING_PROVIDER,
                  reason=base.REASON_UNRECOGNISED_RESPONSE)


# ── READ-ONLY OPERATIONS ────────────────────────────────────────────────────

def _read(operation, method, path, fields, shape):
    failure, body, http_status, echo = _call(operation, method, path, fields)
    if failure is not None:
        return failure
    flat = _first(body)
    if _invalid_key(flat):
        return base.ProviderResult(
            operation=operation, normalized_status=base.INTEGRATION_ERROR,
            reason=base.REASON_CREDENTIAL, http_status=http_status,
            raw_message=str(flat.get("Message") or flat)[:300],
            detail={"request": echo})
    return base.ProviderResult(
        operation=operation, normalized_status=base.OK, ok=True,
        raw_status=str(flat.get("Status") or "") or None,
        http_status=http_status, data=shape(body),
        detail={"request": echo})


def list_plans(zip_code: str, *, is_renewal: bool = False,
               promo: Optional[str] = None) -> base.ProviderResult:
    """GetPlans. Residential only — the guide documents no other type."""
    return _read(
        base.OP_LIST_PLANS, "GET", "/api/broker/GetPlans",
        {"Zip": zip_code,
         "PromoCode": promo if promo is not None else promo_code(),
         "CustomerTypeID": CUSTOMER_TYPE_RESIDENTIAL,
         "IsRenewal": "True" if is_renewal else ""},
        _shape_plans)


def search_address(zip_code: str, address1: str, *, address2: str = "",
                   city: str = "", state: str = "") -> base.ProviderResult:
    """GetErcotAddresses. Resolves a street address to its ESIID."""
    return _read(
        base.OP_SEARCH_ADDRESS, "GET", "/api/broker/GetErcotAddresses",
        {"Zip": zip_code, "Address1": address1, "Address2": address2,
         "City": city, "State": state,
         "CustomerTypeID": CUSTOMER_TYPE_RESIDENTIAL},
        _shape_addresses)


def service_type_date(esiid: str, moving_type: str,
                      service_start_date: str) -> base.ProviderResult:
    """GetServiceTypeDate.

    `service_start_date` is MM/DD/YYYY, or the literal "Standard Switch" —
    the guide is explicit that passing a date with a standard switch is a
    validation error, so the caller's string is sent through untouched rather
    than being helpfully reformatted into one.
    """
    return _read(
        base.OP_SERVICE_DATE, "GET", "/api/broker/GetServiceTypeDate",
        {"MovingType": moving_type, "ServiceStartDate": service_start_date,
         "ESIID": esiid},
        lambda body: _first(body))


def deposit_waivers() -> base.ProviderResult:
    """DepositWaive. The waiver reasons a customer may claim, from Chariot —
    never a list maintained here, because the reasons are theirs to change."""
    return _read(
        base.OP_DEPOSIT_WAIVERS, "GET", "/api/broker/DepositWaive",
        {"CustomerTypeID": CUSTOMER_TYPE_RESIDENTIAL},
        lambda body: body if isinstance(body, list) else [])


def utility_charges(utility_id, *, address_type: str = "") -> base.ProviderResult:
    """GetTDSPCharges. Utility ids 1,2,3,5,7,8 per the guide."""
    return _read(
        base.OP_UTILITY_CHARGES, "GET", "/api/broker/GetTDSPCharges",
        {"AddressType": address_type, "UtilityId": utility_id,
         "CustomerTypeID": CUSTOMER_TYPE_RESIDENTIAL},
        lambda body: _first(body))


def renewal_eligibility(account_number: str, *,
                        promo: Optional[str] = None) -> base.ProviderResult:
    """GetRenewalDate. POST multipart, per the guide's own sample code.

    Read-only: it reports whether an account MAY renew. Status is one of
    "Eligible" / "Not Eligible" / "Could not verify", and the Message is a
    customer-facing sentence Chariot wrote — which is why it is carried
    verbatim rather than paraphrased.
    """
    failure, body, http_status, echo = _call(
        base.OP_RENEWAL_ELIGIBILITY, "POST", "/api/broker/GetRenewalDate",
        {"AccountNumber": account_number,
         "PromoCode": promo if promo is not None else promo_code()})
    if failure is not None:
        return failure
    flat = _first(body)
    status = str(flat.get("Status") or "").strip()
    if _invalid_key(flat):
        return base.ProviderResult(
            operation=base.OP_RENEWAL_ELIGIBILITY,
            normalized_status=base.INTEGRATION_ERROR,
            reason=base.REASON_CREDENTIAL, http_status=http_status,
            detail={"request": echo})
    return base.ProviderResult(
        operation=base.OP_RENEWAL_ELIGIBILITY, normalized_status=base.OK,
        ok=True, raw_status=status or None,
        raw_message=str(flat.get("Message") or "") or None,
        http_status=http_status,
        data={"eligible": status.lower() == "eligible", "status": status},
        detail={"request": echo})


# ── THE PLAN SNAPSHOT ───────────────────────────────────────────────────────

def _shape_plans(body: Any) -> Dict[str, Any]:
    """Flatten Chariot's utility-grouped plan list, keeping the audit fields.

    WHAT IS KEPT AND WHY. When a customer later asks "what did I sign up
    for?", the answer has to be the product and terms as they were shown at
    the moment of selection — Chariot can reprice a product id tomorrow.
    So a selected plan is snapshotted: the id, the name, the term, the three
    published price points, the early-termination fee, the category, the
    renewable percentage, and the three document URLs.

    THE DOCUMENT URLS ARE LINKS, NOT DOCUMENTS. EFL, TOS and YRAC come back
    as PDF links. They are stored as URLs. Nothing here downloads them, and
    nothing anywhere calls them "signed" — the guide says Chariot generates
    PDF documents during enrollment, and says nothing about returning signed
    evidence of them, so this integration makes no such claim.
    """
    utilities = body if isinstance(body, list) else [body]
    plans: List[Dict[str, Any]] = []
    status = None
    for utility in utilities:
        if not isinstance(utility, dict):
            continue
        status = status or utility.get("Status")
        for plan in (utility.get("Plans") or []):
            plans.append({
                "product_id": plan.get("ProductId"),
                "product_ref_id": plan.get("ProductRefID"),
                "utility_id": utility.get("UtilityId"),
                "utility_name": utility.get("UtilityName"),
                "title": plan.get("Title"),
                "term_months": plan.get("Terms"),
                "description": plan.get("Description"),
                "price_500_kwh": plan.get("kWh500"),
                "price_1000_kwh": plan.get("kWh1000"),
                "price_2000_kwh": plan.get("kWh2000"),
                "etf": plan.get("ETF"),
                "etf_type_id": plan.get("ETFTypeId"),
                "category": plan.get("CategoryName"),
                "renewable_percent": plan.get("Renewable"),
                "is_bundled": plan.get("IsBundled"),
                "energy_charge": plan.get("ENERGYCHARGE"),
                "base_charge": plan.get("BASECHARGE"),
                "bill_credit": plan.get("BILLCREDIT"),
                "bill_credit_trigger": plan.get("BILLCREDITTRIGGER"),
                "min_usage_charge": plan.get("MINUSAGECHARGE"),
                "usage_trigger": plan.get("USAGETRIGGER"),
                "efl_url": plan.get("EFL"),
                "tos_url": plan.get("TOS"),
                "yrac_url": plan.get("YRAC"),
            })
    return {"plans": plans, "status": status, "count": len(plans)}


def _shape_addresses(body: Any) -> Dict[str, Any]:
    flat = _first(body)
    out = []
    for row in (flat.get("Addresses") or []):
        out.append({
            "esiid": row.get("ESIID"),
            "full_address": row.get("FullAddress"),
            "address1": row.get("Address1"),
            "address2": row.get("Address2"),
            "city": row.get("City"),
            "state": row.get("State"),
            "zip": row.get("Zip"),
            "utility_id": row.get("TDSP"),
            "tdsp_code": row.get("TDSPCode"),
            "tdsp_indicator": row.get("TDSPIndicator"),
            # "Switch hold (Yes/No)" — an address under switch hold cannot be
            # enrolled, so it is surfaced rather than buried in the raw row.
            "switch_hold": str(row.get("SwitchHoldIndicator") or "0") not in ("0", "", "No"),
            "premise_type": row.get("PremiseType"),
            "zone": row.get("Zone"),
            "status": row.get("Status"),
        })
    return {"addresses": out, "status": flat.get("Status"),
            "count": flat.get("TotalAddresses", len(out))}


def plan_snapshot(plan: Dict[str, Any], *, selected_at=None) -> Dict[str, Any]:
    """The auditable record of what the customer chose and when.

    Non-sensitive by construction: it is product and terms, no person in it.
    """
    from datetime import datetime
    snapshot = dict(plan or {})
    snapshot["selected_at"] = (selected_at or datetime.utcnow()).isoformat() + "Z"
    snapshot["provider"] = PROVIDER
    base.assert_no_sensitive(snapshot, "plan_snapshot")
    return snapshot


# ── TRANSACTIONAL OPERATIONS ────────────────────────────────────────────────
#
# THESE CREATE REAL OBLIGATIONS FOR A REAL PERSON. A SubmitEnrollment that
# succeeds switches somebody's electricity supplier, generates contract
# documents and sends them a confirmation email and SMS from Chariot. There
# is no documented way to cancel one through this API.
#
# So they are FAIL-CLOSED behind an environment switch that is not set
# anywhere. Configuring the API key is not enough and must not be: the key is
# needed for the read-only work, and if arming rode on the key then getting
# plans working would silently arm enrollment.

ENV_ALLOW_TRANSACTIONS = "CHARIOT_ALLOW_TRANSACTIONS"


class ChariotTransactionsDisarmed(RuntimeError):
    """A transactional call was attempted while the gate is closed."""


def transactions_armed() -> bool:
    return (os.getenv(ENV_ALLOW_TRANSACTIONS) or "").strip().lower() in (
        "1", "true", "yes")


def _require_armed(operation: str) -> None:
    if not transactions_armed():
        raise ChariotTransactionsDisarmed(
            "%s is disarmed. %s is not set, so no real enrollment or renewal "
            "can be created. This is deliberate: the key alone must never be "
            "enough to transact." % (operation, ENV_ALLOW_TRANSACTIONS))


def submit_enrollment(payload: Dict[str, Any]) -> base.ProviderResult:
    """SubmitEnrollment. Refuses unless explicitly armed.

    `payload` is the Chariot field set, built by
    `app.services.energy_enrollment.build_enrollment_payload` — which is the
    only thing that should ever assemble it, because it is also the thing
    that guarantees the sensitive fields in it are never persisted.

    THE PAYLOAD IS NOT VALIDATED AGAINST SENSITIVE FIELDS HERE. It contains
    them on purpose; that is the whole point of this call. What is guarded is
    everything that comes back out: the ProviderResult carries a redacted
    echo and a redacted body, and nothing else in this process sees the rest.
    """
    _require_armed("SubmitEnrollment")
    failure, body, http_status, echo = _call(
        base.OP_SUBMIT_ENROLLMENT, "POST", "/api/broker/SubmitEnrollment",
        payload)
    if failure is not None:
        return failure
    result = normalize_enrollment(body)
    result.http_status = http_status
    result.detail = {"request": echo}
    return result


def submit_renewal(account_number: str, product_id,
                   *, promo: Optional[str] = None) -> base.ProviderResult:
    """SubmitRenewal. Refuses unless explicitly armed."""
    _require_armed("SubmitRenewal")
    failure, body, http_status, echo = _call(
        base.OP_SUBMIT_RENEWAL, "POST", "/api/broker/SubmitRenewal",
        {"AccountNumber": account_number, "ProductID": product_id,
         "PromoCode": promo if promo is not None else promo_code()})
    if failure is not None:
        return failure
    result = normalize_renewal(body)
    result.http_status = http_status
    result.detail = {"request": echo}
    return result


# ── WHAT THIS GUIDE DOES NOT CONTAIN ────────────────────────────────────────
#
# Read the contents page of Broker API Guide v1.0 and there are eight
# functions. There is no webhook, no callback registration, no
# GetEnrollmentStatus, no transaction-status lookup, no document retrieval.
# Every page of the document was read to confirm it, because the alternative
# was inventing one.
#
# THE CONSEQUENCE IS ARCHITECTURAL, NOT COSMETIC. A transaction that comes
# back "Enrollment has been completed with Pending Status" has no mechanism
# in this API that will ever tell us what happened to it. It can only be
# resolved by a human asking Chariot. So:
#
#   - PENDING_PROVIDER is a resting state, not a transient one.
#   - Nothing promotes it on a timer. Time is not evidence.
#   - It surfaces in a reconciliation queue with its age.
#
# When Chariot later publishes a status endpoint or a webhook, it lands here
# as one more function returning a ProviderResult, and
# `provider_transactions.apply_result` updates the SAME row it already wrote.
# No part of the platform above this file needs to change for that.
HAS_STATUS_CALLBACK = False
HAS_STATUS_LOOKUP = False
HAS_DOCUMENT_RETRIEVAL = False
