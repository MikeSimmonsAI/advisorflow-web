"""CHARIOT ADAPTER — SAFE, NON-SENDING, END-TO-END.

NOT ONE BYTE LEAVES THIS PROCESS. `chariot.set_transport` is replaced in
every test, including the tests of the read-only operations. Even Chariot's
documented TEST host is out of bounds here: it is plain HTTP on somebody
else's infrastructure, so a suite that talked to it would fail whenever their
server was down and would post fixture data to a machine we do not own.

Every response body below is copied from the sample responses in Broker API
Guide v1.0 — including the misspelling "RefrenceNumber", which is theirs and
which this integration depends on reading.
"""

import json
import pytest

from app.services.providers import base, chariot
from app.services import energy_enrollment as ee


# ── transport doubles ───────────────────────────────────────────────────────

class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """A fake key, so no test can accidentally run against a real one."""
    monkeypatch.setenv(chariot.ENV_API_KEY, "test-key-not-a-real-credential")
    monkeypatch.setenv(chariot.ENV_ENVIRONMENT, "test")
    monkeypatch.delenv(chariot.ENV_ALLOW_TRANSACTIONS, raising=False)


@pytest.fixture
def transport():
    """Records calls and returns queued bodies. Restores the real one."""
    calls = []
    queue = []

    def fake(method, url, *, params=None, files=None, timeout=None):
        calls.append({"method": method, "url": url,
                      "params": params, "files": files})
        return _Response(queue.pop(0) if queue else {})

    previous = chariot.set_transport(fake)
    fake.calls = calls
    fake.queue = queue
    yield fake
    chariot.set_transport(previous)


# ── the credential never leaves the server, and never lands anywhere ────────

def test_an_unconfigured_integration_refuses_instead_of_returning_nothing(monkeypatch):
    """An empty plan list and 'not configured' must never look the same.

    A silent empty answer is how a customer gets shown "no plans available"
    forever while everybody believes the integration works.
    """
    monkeypatch.delenv(chariot.ENV_API_KEY, raising=False)
    assert chariot.is_configured() is False
    with pytest.raises(chariot.ChariotNotConfigured):
        chariot.api_key()


def test_the_configuration_report_proves_presence_without_revealing_the_key():
    report = chariot.configuration_report()
    assert report["key_present"] is True
    assert len(report["key_fingerprint"]) == 8
    blob = json.dumps(report)
    assert "test-key-not-a-real-credential" not in blob


def test_the_key_is_never_carried_in_the_result(transport):
    transport.queue.append([{"UtilityId": 1, "Plans": [], "Status": "x"}])
    result = chariot.list_plans("78415")
    blob = json.dumps({"detail": result.detail, "data": result.data},
                      default=str)
    assert "test-key-not-a-real-credential" not in blob
    assert "Key" not in (result.detail.get("request") or {})
    # ...but it WAS sent, otherwise the call would not authenticate.
    assert transport.calls[0]["params"]["Key"] == "test-key-not-a-real-credential"


# ── READ-ONLY OPERATIONS, against the guide's own sample responses ──────────

GUIDE_PLANS = [{
    "UtilityId": 1, "UtilityName": "AEP Texas Central",
    "Plans": [{
        "ProductId": 23639, "Title": "asdsds nasdasd", "Terms": 12,
        "Description": "asdd", "kWh500": "602.0", "kWh1000": "601.0",
        "kWh2000": "600.5", "ETF": "12", "ETFTypeId": 1,
        "CategoryName": "Fixed", "ProductRefID": "PRRN12000058",
        "IsBundled": True, "Renewable": 12,
        "EFL": "http://example/EFl?productId=23639",
        "TOS": "http://example/TOS?productId=23639",
        "YRAC": "http://example/YRAC?productId=23639",
    }],
    "TotalPlanCount": 1, "Status": "Plans Available",
}]

GUIDE_ADDRESSES = [{
    "Addresses": [{
        "ESIID": "10032789498801491",
        "FullAddress": "4714 PRESCOTT ST, CORPUS CHRISTI, TX 78416",
        "Address1": "4714 PRESCOTT ST", "City": "CORPUS CHRISTI",
        "State": "TX", "Zip": "78416", "TDSP": 1, "TDSPCode": "AEPC",
        "TDSPIndicator": "AMSR", "SwitchHoldIndicator": "0",
        "PremiseType": "Residential", "Zone": "South", "Status": "Active",
    }],
    "TotalAddresses": 1, "Status": "Addresses are Found",
}]


def test_get_plans_returns_the_audit_snapshot_fields(transport):
    transport.queue.append(GUIDE_PLANS)
    result = chariot.list_plans("78415")

    assert result.ok and result.normalized_status == base.OK
    assert transport.calls[0]["url"].endswith("/api/broker/GetPlans")
    assert transport.calls[0]["params"]["CustomerTypeID"] == "1"

    plan = result.data["plans"][0]
    # The fields an auditor needs to answer "what did they sign up for?"
    for key in ("product_id", "title", "term_months", "price_1000_kwh",
                "etf", "category", "renewable_percent",
                "efl_url", "tos_url", "yrac_url"):
        assert plan.get(key) is not None, key


def test_the_plan_snapshot_carries_no_person_and_is_timestamped():
    snapshot = chariot.plan_snapshot({"product_id": 23639, "title": "Plan"})
    assert snapshot["selected_at"].endswith("Z")
    assert snapshot["provider"] == "chariot"
    # assert_no_sensitive runs inside plan_snapshot; this proves it bites.
    with pytest.raises(ValueError):
        chariot.plan_snapshot({"product_id": 1, "SSN": "666730809"})


def test_search_address_surfaces_esiid_and_switch_hold(transport):
    transport.queue.append(GUIDE_ADDRESSES)
    result = chariot.search_address("78416", "4714 PRESCOTT ST")
    row = result.data["addresses"][0]
    assert row["esiid"] == "10032789498801491"
    assert row["switch_hold"] is False
    assert row["utility_id"] == 1


def test_a_switch_hold_address_is_reported_as_held(transport):
    held = json.loads(json.dumps(GUIDE_ADDRESSES))
    held[0]["Addresses"][0]["SwitchHoldIndicator"] = "1"
    transport.queue.append(held)
    result = chariot.search_address("78416", "4714 PRESCOTT ST")
    assert result.data["addresses"][0]["switch_hold"] is True


def test_service_type_date_and_charges_and_waivers_are_get_calls(transport):
    transport.queue.append({"RequestDate": "04/30/2019",
                            "ServiceType": "Switching",
                            "ServiceCharges": "$2.00",
                            "Description": "Your start date: 04/30/2019"})
    transport.queue.append({"UtilityId": 1, "MoveInMin": 1.1, "SSSMin": 2})
    transport.queue.append([{"Title": "Senior Citizens in Good Standing",
                             "Description": "..."}])

    assert chariot.service_type_date("1003", "Switching", "04/30/2019").ok
    assert chariot.utility_charges(1, address_type="AMSR").ok
    assert chariot.deposit_waivers().ok
    assert [c["method"] for c in transport.calls] == ["GET", "GET", "GET"]


def test_an_invalid_key_is_our_fault_not_the_customers(transport):
    transport.queue.append({"Message": "Error Message : Invalid APIKey"})
    result = chariot.list_plans("78415")
    assert result.normalized_status == base.INTEGRATION_ERROR
    assert result.reason == base.REASON_CREDENTIAL


def test_a_transport_failure_is_an_integration_error_not_a_refusal():
    def boom(*a, **k):
        raise OSError("connection reset")
    previous = chariot.set_transport(boom)
    try:
        result = chariot.list_plans("78415")
    finally:
        chariot.set_transport(previous)
    assert result.normalized_status == base.INTEGRATION_ERROR
    assert result.reason == base.REASON_TRANSPORT
    assert result.ok is False


# ── THE FIVE DOCUMENTED ENROLLMENT OUTCOMES ─────────────────────────────────
#
# Bodies copied verbatim from the guide's "Sample Response" section, including
# "RefrenceNumber", "No Deposit required" and the amount embedded in Status.

COMPLETED_NO_DEPOSIT = {
    "Status": "Enrollment has been completed",
    "Message": "Passed CreditCheck & No Deposit required",
    "RefrenceNumber": "STB1912180012"}

COMPLETED_DEPOSIT_PAID = {
    "Status": "Enrollment has been completed",
    "Message": "Passed CreditCheck & Submit Deposit Successfully",
    "RefrenceNumber": "BEB1911100001"}

COMPLETED_PENDING_WAIVER = {
    "Status": "Enrollment has been completed with Pending Status",
    "Message": "Deposit Waiver has been requested",
    "RefrenceNumber": "ALC1911150007"}

DEPOSIT_REQUIRED = {
    "Status": "Deposit Required Your Deposit Amount is $632.00",
    "Message": "Required Deposit Fields", "DepositAmount": "$632.00"}

CREDIT_CHECK_FAILED = {
    "Status": "CreditCheck Failed",
    "Message": "We could not verify your credit details. Please verify the "
               "details you entered and resubmit your request."}


def test_completed_is_the_only_thing_that_confirms():
    for body in (COMPLETED_NO_DEPOSIT, COMPLETED_DEPOSIT_PAID):
        result = chariot.normalize_enrollment(body)
        assert result.normalized_status == base.PROVIDER_CONFIRMED
        assert result.is_final_success is True
        assert result.reference_number  # never lost
        assert ee.stage_for_result(result) == ee.STAGE_PROVIDER_CONFIRMED


def test_the_misspelled_reference_number_is_read():
    """"RefrenceNumber" is Chariot's spelling. There is no lookup endpoint in
    this API, so a reference we fail to read is gone for good."""
    assert chariot.normalize_enrollment(
        COMPLETED_NO_DEPOSIT).reference_number == "STB1912180012"


def test_pending_never_becomes_enrolled():
    """THE ORDERING BUG THIS GUARDS.

    "Enrollment has been completed with Pending Status" starts with
    "Enrollment has been completed". A prefix test written in the obvious
    order marks a deposit-waiver request as a finished enrollment — and
    Chariot has no endpoint that would ever correct it.
    """
    result = chariot.normalize_enrollment(COMPLETED_PENDING_WAIVER)
    assert result.normalized_status == base.PENDING_PROVIDER
    assert result.is_final_success is False
    assert result.reason == base.REASON_DEPOSIT_WAIVER_PENDING
    assert result.reference_number == "ALC1911150007"
    assert ee.stage_for_result(result) == ee.STAGE_PROVIDER_PENDING
    assert ee.stage_for_result(result) != ee.STAGE_ENROLLED


def test_deposit_required_is_action_required_and_keeps_the_amount_verbatim():
    result = chariot.normalize_enrollment(DEPOSIT_REQUIRED)
    assert result.normalized_status == base.ACTION_REQUIRED
    assert result.reason == base.REASON_DEPOSIT_REQUIRED
    assert result.amount_quoted == "$632.00"   # not reformatted, not parsed
    assert result.needs_human is True
    assert ee.stage_for_result(result) == ee.STAGE_DEPOSIT_REQUIRED


def test_a_credit_failure_is_the_providers_verdict_and_needs_a_human():
    result = chariot.normalize_enrollment(CREDIT_CHECK_FAILED)
    assert result.normalized_status == base.PROVIDER_FAILED
    assert result.reason == base.REASON_CREDIT_CHECK_FAILED
    assert result.needs_human is True
    assert ee.stage_for_result(result) == ee.STAGE_PROVIDER_FAILED


def test_our_own_validation_failure_is_never_shown_as_a_decline():
    result = chariot.normalize_enrollment({"Status": "Validation Failed",
                                           "Message": "Zip is required"})
    assert result.normalized_status == base.FAILED_VALIDATION
    # human_review, NOT provider_failed: the customer was not declined.
    assert ee.stage_for_result(result) == ee.STAGE_HUMAN_REVIEW


def test_an_unrecognised_answer_rests_pending_and_never_guesses():
    """The whole safety property in one test. A sentence this adapter has not
    been taught is a sentence nobody has read."""
    result = chariot.normalize_enrollment(
        {"Status": "Something nobody documented", "Message": "?"})
    assert result.normalized_status == base.PENDING_PROVIDER
    assert result.reason == base.REASON_UNRECOGNISED_RESPONSE
    assert result.is_final_success is False


def test_nothing_in_the_vocabulary_can_reach_enrolled_from_a_provider_answer():
    """`enrolled` is the workspace's assertion that a confirmation has been
    reconciled. No provider answer may set it; only a person may."""
    for body in (COMPLETED_NO_DEPOSIT, COMPLETED_DEPOSIT_PAID,
                 COMPLETED_PENDING_WAIVER, DEPOSIT_REQUIRED,
                 CREDIT_CHECK_FAILED, {"Status": "?"}):
        assert ee.stage_for_result(
            chariot.normalize_enrollment(body)) != ee.STAGE_ENROLLED


# ── NO LIVE TRANSACTION IS POSSIBLE FROM THIS CONFIGURATION ─────────────────

def test_a_configured_key_alone_cannot_enrol_anybody(transport):
    """THE GATE THAT MATTERS.

    The key is needed for plan lookups, so if arming rode on the key then
    getting GetPlans working would silently arm SubmitEnrollment — a call
    that switches a real person's electricity supplier and which this API
    offers no way to cancel.
    """
    assert chariot.is_configured() is True
    assert chariot.transactions_armed() is False
    with pytest.raises(chariot.ChariotTransactionsDisarmed):
        chariot.submit_enrollment({"Zip": "78415"})
    with pytest.raises(chariot.ChariotTransactionsDisarmed):
        chariot.submit_renewal("1907220002", 23639)
    assert transport.calls == []   # nothing was even attempted


def test_when_armed_the_documented_outcomes_round_trip(monkeypatch, transport):
    """Arming is exercised ONLY against the fake transport, so this proves
    the wire shape without any request leaving the process."""
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    transport.queue.append(COMPLETED_PENDING_WAIVER)
    result = chariot.submit_enrollment(
        {"Zip": "78415", "ProductID": "23639", "SSN": "666730809"})

    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"].endswith("/api/broker/SubmitEnrollment")
    assert result.normalized_status == base.PENDING_PROVIDER
    # The SSN went out and did NOT come back in anything storable.
    echo = result.detail["request"]
    assert echo["SSN"] == base.REDACTED
    assert "Key" not in echo


def test_renewal_eligibility_and_submission_use_the_documented_strings(
        monkeypatch, transport):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    transport.queue.append({"Message": "Customer is Eligible to Renew",
                            "Status": "Eligible"})
    eligible = chariot.renewal_eligibility("1907220002")
    assert eligible.data["eligible"] is True

    transport.queue.append({"Message": "Dear Flores - Our records indicate "
                                       "that you have successfully renewed",
                            "Status": "Not Eligible"})
    assert chariot.renewal_eligibility("1907220002").data["eligible"] is False

    transport.queue.append({"Status": "Success",
                            "Message": "Renewal has been submitted successfully."})
    done = chariot.submit_renewal("1907220002", 23639)
    assert done.normalized_status == base.PROVIDER_CONFIRMED
    # GetRenewalDate is POSTed, per the guide's own sample code.
    assert transport.calls[0]["method"] == "POST"


# ── THE SENSITIVE-DATA BOUNDARY ─────────────────────────────────────────────

RESIDENTIAL_FIELDS = {
    "Zip": "78415", "CustomerTypeID": "1", "ProductID": "23639",
    "FirstName": "BILLIE", "LastName": "BEATTY", "DOB": "12/12/1990",
    "EmailAddress": "synthetic@example.com", "PhoneNumber": "1111111111",
    "BillDeliverMethodID": "Email", "languagePreference": "English",
    "ESIID": "10032789426879101", "ServiceType": "switching",
    "ServiceStartDate": "05/20/2019", "IsAccountHolder": "Yes",
    "BillingAddressTypeID": "No", "AutoPay": "No", "PayDeposit": "No",
    "IsPromoInterested": "No", "SSN": "666730809",
}


def test_the_payload_and_the_auditable_part_are_returned_separately():
    payload, audit = ee.build_enrollment_payload(
        classification=ee.CLASS_RESIDENTIAL_RATE_REVIEW,
        fields=RESIDENTIAL_FIELDS)

    assert payload["SSN"] == "666730809"          # the wire needs it
    assert "SSN" not in audit                      # the record must not have it
    assert "DOB" not in audit
    assert audit["ProductID"] == "23639"           # what may be kept
    assert audit["ESIID"] == "10032789426879101"


def test_persisting_the_payload_raises_instead_of_silently_redacting():
    payload, _ = ee.build_enrollment_payload(
        classification=ee.CLASS_RESIDENTIAL_RATE_REVIEW,
        fields=RESIDENTIAL_FIELDS)
    with pytest.raises(ValueError):
        base.assert_no_sensitive(payload, "payload")


@pytest.mark.parametrize("field,value", [
    ("SSN", "666730809"), ("CreditCardNumber", "4111111111111111"),
    ("CreditCardCVC", "123"), ("RoutingNumber", "111000025"),
    ("AccountNumber", "1234567890"), ("Password", "hunter22"),
    ("SecurityAnswer", "Dallas"), ("DriversLicence", "X1234567"),
])
def test_no_sensitive_field_survives_redaction(field, value):
    out = base.redact({field: value, "ProductID": "23639"})
    assert out[field] == base.REDACTED
    assert out["ProductID"] == "23639"
    assert value not in json.dumps(out)


def test_an_incomplete_enrollment_fails_here_not_at_the_provider():
    fields = dict(RESIDENTIAL_FIELDS)
    fields.pop("ESIID")
    with pytest.raises(ee.EnrollmentNotReady) as exc:
        ee.build_enrollment_payload(
            classification=ee.CLASS_RESIDENTIAL_RATE_REVIEW, fields=fields)
    assert "ESIID" in str(exc.value)


def test_a_credit_check_with_no_identity_document_is_refused():
    fields = dict(RESIDENTIAL_FIELDS)
    fields["SSN"] = ""
    with pytest.raises(ee.EnrollmentNotReady):
        ee.build_enrollment_payload(
            classification=ee.CLASS_RESIDENTIAL_RATE_REVIEW, fields=fields)


# ── ROUTING: COMMERCIAL, MOVE CONCIERGE, RENEWAL, DNC ───────────────────────

@pytest.mark.parametrize("kwargs,expected", [
    ({"premise_type": "Commercial"}, ee.CLASS_COMMERCIAL_ENERGY),
    ({"intent_hint": "quote for my business"}, ee.CLASS_COMMERCIAL_ENERGY),
    ({"source_detail": "Move Concierge"}, ee.CLASS_MOVE_CONCIERGE),
    ({"tier": "renewal_due"}, ee.CLASS_RENEWAL),
    ({"source_detail": "Partner"}, ee.CLASS_PARTNER_REFERRAL),
    ({"intent_hint": "can you check my bill"}, ee.CLASS_BILL_ANALYSIS),
    ({"tier": "rate_review"}, ee.CLASS_RESIDENTIAL_RATE_REVIEW),
    ({}, ee.CLASS_GENERAL_INQUIRY),
])
def test_classification_is_decided_from_data_not_guessed(kwargs, expected):
    assert ee.classify(**kwargs) == expected


def test_a_commercial_premise_beats_the_form_it_arrived_on():
    """A business that filled in the residential website form is still a
    business. Chariot documents CustomerTypeID=1 (Residential) and no
    commercial equivalent, so getting this wrong submits a real enrollment
    under the wrong customer type."""
    assert ee.classify(premise_type="Commercial",
                       source_detail="Website",
                       tier="rate_review") == ee.CLASS_COMMERCIAL_ENERGY


def test_a_commercial_lead_cannot_reach_the_residential_provider_api():
    assert ee.may_submit_to_provider(ee.CLASS_COMMERCIAL_ENERGY) is False
    with pytest.raises(ee.EnrollmentNotReady) as exc:
        ee.build_enrollment_payload(
            classification=ee.CLASS_COMMERCIAL_ENERGY,
            fields=RESIDENTIAL_FIELDS)
    assert "residential" in str(exc.value).lower()


def test_move_concierge_keeps_its_own_workflow_and_its_energy_subflow():
    """It is its own classification — so it can be worked as a concierge
    handoff — AND it is provider-eligible, because the customer still needs
    electricity at the new address."""
    assert ee.classify(source_detail="Move Concierge") == ee.CLASS_MOVE_CONCIERGE
    assert ee.may_submit_to_provider(ee.CLASS_MOVE_CONCIERGE) is True
    payload, audit = ee.build_enrollment_payload(
        classification=ee.CLASS_MOVE_CONCIERGE,
        fields={**RESIDENTIAL_FIELDS, "ServiceType": "moving"})
    assert payload["ServiceType"] == "moving"
    assert "SSN" not in audit


def test_automation_stops_where_it_must():
    """DNC and opt-out stop it; so does a provider confirmation.

    THE PROVIDER-CONFIRMED CASE IS THE SUBTLE ONE. The guide states that a
    completed enrollment makes CHARIOT send the customer a confirmation email
    and SMS. Nurture that keeps running afterwards has EvoSys and Chariot
    telling the same person different things about their own electricity.
    """
    for stage in (ee.STAGE_OPTED_OUT, ee.STAGE_NOT_INTERESTED,
                  ee.STAGE_PROVIDER_CONFIRMED, ee.STAGE_ENROLLED,
                  ee.STAGE_DEPOSIT_REQUIRED, ee.STAGE_PROVIDER_FAILED,
                  ee.STAGE_HUMAN_REVIEW):
        assert ee.automation_should_stop(stage) is True

    for stage in (ee.STAGE_NEW, ee.STAGE_CONTACTED, ee.STAGE_ENGAGED,
                  ee.STAGE_QUALIFIED, ee.STAGE_PLAN_REVIEW,
                  ee.STAGE_ENROLLMENT_READY, ee.STAGE_PROVIDER_PENDING):
        assert ee.automation_should_stop(stage) is False


def test_every_stage_a_provider_answer_can_produce_is_a_declared_stage():
    for body in (COMPLETED_NO_DEPOSIT, COMPLETED_PENDING_WAIVER,
                 DEPOSIT_REQUIRED, CREDIT_CHECK_FAILED,
                 {"Status": "Validation Failed"}, {"Status": "?"}):
        stage = ee.stage_for_result(chariot.normalize_enrollment(body))
        assert stage in ee.ENERGY_STAGES


# ── WHAT THE GUIDE DOES NOT CONTAIN ─────────────────────────────────────────

def test_the_adapter_states_that_there_is_no_async_status_mechanism():
    """Broker API Guide v1.0 documents eight functions and none of them is a
    webhook, a status lookup or a document retrieval. These constants are
    read by the reconciliation queue, so the absence is a fact the platform
    acts on rather than a comment somebody hopes is still true."""
    assert chariot.HAS_STATUS_CALLBACK is False
    assert chariot.HAS_STATUS_LOOKUP is False
    assert chariot.HAS_DOCUMENT_RETRIEVAL is False
