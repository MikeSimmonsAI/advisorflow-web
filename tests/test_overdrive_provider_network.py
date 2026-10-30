"""PROJECT OVERDRIVE Phase B (#439) - the Provider Intelligence Network.

ATTOM, PropStream and Tracerfy on one honest readiness view and one routing
rule. Every vendor answer here is FAKED (vendors._http / httpx.get are
replaced): nothing reaches ATTOM or Tracerfy, nothing is billed, no
credential is real.
"""
import json

import httpx
import pytest

from app.models.evosense_models import EvoSenseCostEntry, EvoSenseObservation, EvoSenseProperty
from app.models.models import Organization, User
from app.services.evosense import common as C
from app.services.evosense import ingest as IN
from app.services.evosense import provider_network as PN
from app.services.evosense import providers as PV
from app.services.evosense import vendors as V

FAKE_KEY = "fake-attom-key-zz9-not-real"
FAKE_TOKEN = "fake-tracerfy-token-qq7-not-real"


# ── fixtures ────────────────────────────────────────────────────────────────

def _prop(db, org_id, **kw):
    """A property the county record already describes (rank 60), owner of record included."""
    p = EvoSenseProperty(organization_id=org_id, street_address=kw.pop("street", "1418 Synth Springs Rd"),
                         city="Dallas", state="TX", zip_code="75201", county="Dallas", status=C.S_NEW,
                         is_test=True, **kw)
    db.add(p)
    db.flush()
    p.fact_ranks = C.jdump({k: IN.RANK_RECORDS for k in ("square_feet", "year_built", "parcel_apn")
                            if getattr(p, k) is not None})
    IN.upsert_owner(db, org_id, p, {"name": "COUNTY OWNER OF RECORD"}, source="dcad", observation_id=None,
                    rank=IN.RANK_RECORDS, is_test=True)
    db.flush()
    return p


def _attom_ready(db, org_id, *, unit=25, enabled=True):
    cfg = PV.config(db, org_id, "attom")
    cfg.enabled = enabled
    if unit is not None:
        cfg.cost_overrides = C.jdump({C.ASSESSOR: unit})
    db.flush()
    return cfg


DETAIL = {"property": [{
    "identifier": {"attomId": 777, "apn": "00-SYNTH-1", "fips": "48113"},
    "address": {"line1": "1418 SYNTH SPRINGS RD", "locality": "DALLAS", "countrySubd": "TX", "postal1": "75201"},
    "summary": {"proptype": "SFR", "yearbuilt": 1961},
    "building": {"size": {"livingsize": 1999}, "rooms": {"beds": 3, "bathstotal": 2}},
    "owner": {"owner1": {"fullname": "ATTOM SAYS SOMEONE ELSE"}, "mailingaddressoneline": "9 Synth Box, Tulsa, OK"},
    "mortgage": {"amount": 88000, "date": "2009-01-01", "lender": {"lastname": "SYNTH BANK"}}}]}
ASSESS = {"property": [{"assessment": {"assessed": {"assdttlvalue": 150000}, "market": {"mktttlvalue": 160000},
                                       "tax": {"taxamt": 4100, "taxyear": 2026}}}]}
AVM = {"property": [{"avm": {"amount": {"value": 231000, "low": 210000, "high": 250000, "scr": 81},
                             "eventDate": "2026-10-01"}}]}
HIST = {"property": [{"salehistory": [{"saleTransDate": "2016-06-15", "amount": {"saleamt": 140000,
                                                                                  "saledoctype": "WD"}}]}]}


@pytest.fixture()
def attom(monkeypatch):
    """Fake ATTOM. `script[path]` may be a body dict or an exception to raise."""
    calls = []
    script = {"property/detailmortgageowner": DETAIL, "assessment/detail": ASSESS, "attomavm/detail": AVM,
              "saleshistory/detail": HIST}

    def fake(method, url, *, headers=None, json=None, params=None, auth=None):
        path = url.split("v1.0.0/", 1)[1]
        calls.append({"path": path, "headers": headers, "params": params})
        ans = script[path]
        if isinstance(ans, Exception):
            raise ans
        return dict(ans, _status=ans.get("_status", 200))
    monkeypatch.setattr(V, "_http", fake)
    monkeypatch.setenv("ATTOM_API_KEY", FAKE_KEY)
    PN._INFLIGHT.clear()
    return calls, script


class _Resp:
    def __init__(self, code, body=None, headers=None):
        self.status_code, self._body, self.headers = code, body or {}, headers or {}

    def json(self):
        return self._body


@pytest.fixture()
def http_get(monkeypatch):
    """Fake httpx.get for Test Connection. Set `answer["resp"]` (a _Resp or an exception)."""
    seen, answer = [], {"resp": _Resp(200, {"balance": 940, "total_queues": 3})}

    def fake(url, headers=None, params=None, timeout=None):
        seen.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        if isinstance(answer["resp"], Exception):
            raise answer["resp"]
        return answer["resp"]
    monkeypatch.setattr(PN, "_http_get", fake)
    return seen, answer


# ── ATTOM: the router (steps 1-5, no call) ──────────────────────────────────

def test_missing_credentials_means_no_call_and_not_configured(db_session, sample_org, monkeypatch, attom):
    calls, _ = attom
    monkeypatch.delenv("ATTOM_API_KEY")
    p = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id)
    res = PN.attom_lookup(db_session, sample_org.id, p)
    assert res["decision"] == "NOT_CONFIGURED" and res["called"] is False and calls == []
    card = {c["key"]: c for c in PN.readiness(db_session, sample_org.id)["providers"]}["attom"]
    assert card["status"] == PN.NOT_CONFIGURED


def test_existing_data_sufficient_means_no_call(db_session, sample_org, attom):
    calls, _ = attom
    p = _prop(db_session, sample_org.id, parcel_apn="X1", square_feet=1500, year_built=1980,
              estimated_value=200000, mortgage_balance=50000)
    from datetime import date
    p.last_deed_transfer_date = date(2010, 1, 1)
    _attom_ready(db_session, sample_org.id)
    res = PN.attom_lookup(db_session, sample_org.id, p)
    assert res["decision"] == "USE_EXISTING" and calls == []


def test_a_free_healthy_county_source_is_used_before_attom(db_session, sample_org, attom):
    calls, _ = attom
    p = _prop(db_session, sample_org.id, estimated_value=200000, mortgage_balance=1)
    from datetime import date
    p.last_deed_transfer_date = date(2010, 1, 1)          # only owner/parcel/characteristics gaps remain
    cfg = PV.config(db_session, sample_org.id, "dcad")
    cfg.enabled, cfg.last_success_at = True, C.now()
    _attom_ready(db_session, sample_org.id)
    res = PN.attom_lookup(db_session, sample_org.id, p)
    assert res["decision"] == "USE_FREE_SOURCE" and res["free_source"] == "dcad" and calls == []


def test_no_price_switch_off_and_paused_each_block_the_paid_call(db_session, sample_org, attom):
    calls, _ = attom
    p = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id, unit=None)
    assert PN.attom_lookup(db_session, sample_org.id, p)["decision"] == "NO_PRICE"
    _attom_ready(db_session, sample_org.id, enabled=False)
    assert PN.attom_lookup(db_session, sample_org.id, p)["decision"] == "SWITCHED_OFF"
    _attom_ready(db_session, sample_org.id)
    C.controls(db_session, sample_org.id).paused_paid_data = True
    assert PN.attom_lookup(db_session, sample_org.id, p)["decision"] == "PAUSED"
    assert calls == []


def test_concurrent_duplicate_request_is_refused(db_session, sample_org, attom):
    calls, _ = attom
    p = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id)
    PN._INFLIGHT.add((sample_org.id, p.id))
    try:
        assert PN.attom_lookup(db_session, sample_org.id, p)["decision"] == "IN_FLIGHT"
    finally:
        PN._INFLIGHT.clear()
    assert calls == []


def test_budget_exhausted_buys_nothing(db_session, sample_org, attom):
    calls, _ = attom
    p = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id, unit=50)                  # 4 x 50 = 200 cents reserved
    C.controls(db_session, sample_org.id).org_daily_budget_cents = 100
    res = PN.attom_lookup(db_session, sample_org.id, p)
    assert res["decision"] == "BUDGET_BLOCKED" and calls == []
    assert db_session.query(EvoSenseCostEntry).filter_by(organization_id=sample_org.id, provider_key="attom").count() == 0


def test_attom_is_never_routed_to_a_hunt_even_when_connected(db_session, sample_org, attom):
    _attom_ready(db_session, sample_org.id)
    PV.config(db_session, sample_org.id, "attom").last_success_at = C.now()
    for cap in (C.ASSESSOR, C.OWNERSHIP, C.VALUATION):
        assert "attom" not in [r[0].key for r in PV.route(db_session, sample_org.id, cap)]


# ── ATTOM: the call (steps 6-8) ─────────────────────────────────────────────

def test_successful_lookup_is_normalized_priced_and_never_outranks_the_county(db_session, sample_org, attom):
    calls, _ = attom
    p = _prop(db_session, sample_org.id, square_feet=1500, year_built=1960)
    _attom_ready(db_session, sample_org.id, unit=25)
    res = PN.attom_lookup(db_session, sample_org.id, p)
    assert res["decision"] == "LOOKED_UP" and res["calls"] == 4 and res["cost_cents"] == 100
    assert [c["path"] for c in calls] == ["property/detailmortgageowner", "assessment/detail", "attomavm/detail",
                                          "saleshistory/detail"]
    assert calls[0]["headers"]["APIKey"] == FAKE_KEY
    # filled blanks, labelled
    assert p.parcel_apn == "00-SYNTH-1"
    assert p.estimated_value == 231000 and "never an ARV" in p.estimated_value_source
    assert str(p.last_deed_transfer_date) == "2016-06-15"
    # the county's values stand; ATTOM's differing ones are conflicts, not overwrites
    assert p.square_feet == 1500 and p.year_built == 1960
    fields = {c["field"] for c in C.jload(p.conflicts, [])}
    assert {"square_feet", "year_built"} <= fields and res["new_conflicts"] >= 2
    # the ORIGINAL recorded loan never becomes a mortgage balance
    assert p.mortgage_balance is None
    assert res["profile"]["mortgage"]["amount"] == 88000 and "not today's payoff" in res["profile"]["mortgage"]["truth"]
    assert res["profile"]["texas_rule"] and res["profile"]["owner_mailing"] == "9 Synth Box, Tulsa, OK"
    # one ledger line, settled to what was actually requested
    e = db_session.query(EvoSenseCostEntry).filter_by(organization_id=sample_org.id, provider_key="attom").one()
    assert (e.status, e.total_cents, e.quantity, e.unit_cost_cents) == ("charged", 100, 4, 25)
    # the evidence is kept with its source, and the credential is nowhere in it
    obs = db_session.query(EvoSenseObservation).filter_by(organization_id=sample_org.id, provider_key="attom").one()
    assert obs.raw_payload and FAKE_KEY not in (obs.raw_payload + (obs.payload or ""))
    assert obs.adapter_version == PV.PROVIDERS["attom"].adapter_version
    card = {c["key"]: c for c in PN.readiness(db_session, sample_org.id)["providers"]}["attom"]
    assert card["status"] == PN.CONNECTED and card["usage"] == {"calls": 1, "cents": 100}


def test_vendor_owner_never_replaces_the_county_owner(db_session, sample_org, attom):
    p = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id)
    PN.attom_lookup(db_session, sample_org.id, p)
    from app.services.evosense import contacts as CT
    assert CT.primary_owner(db_session, p).display_name == "COUNTY OWNER OF RECORD"


def test_a_saved_answer_is_reused_and_a_stale_one_is_not(db_session, sample_org, attom):
    calls, _ = attom
    p = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id)
    PN.attom_lookup(db_session, sample_org.id, p)
    n = len(calls)
    again = PN.attom_lookup(db_session, sample_org.id, p, force=True)
    assert again["decision"] == "CACHED" and len(calls) == n
    obs = db_session.query(EvoSenseObservation).filter_by(organization_id=sample_org.id, provider_key="attom").one()
    from datetime import timedelta
    obs.observed_at = C.now() - timedelta(days=PN.ATTOM_CACHE_DAYS + 1)
    db_session.flush()
    assert PN.attom_plan(db_session, sample_org.id, p, force=True)["decision"] == "READY"


def test_invalid_credentials_record_a_refusal_and_buy_nothing_more(db_session, sample_org, attom):
    calls, script = attom
    script["property/detailmortgageowner"] = PV.ProviderFailure("api.gateway.attomdata.com refused the credentials (401)")
    p = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id, unit=25)
    res = PN.attom_lookup(db_session, sample_org.id, p)
    assert res["decision"] == "ENTITLEMENT_MISSING" and res["calls"] == 1 and res["cost_cents"] == 25
    assert len(calls) == 1 and p.parcel_apn is None
    card = {c["key"]: c for c in PN.readiness(db_session, sample_org.id)["providers"]}["attom"]
    assert card["status"] == PN.ENTITLEMENT_MISSING
    # an unavailable provider is not asked again until someone fixes it
    assert PN.attom_plan(db_session, sample_org.id, p)["decision"] == "ENTITLEMENT_MISSING"


def test_an_unlicensed_package_is_recorded_and_the_rest_still_used(db_session, sample_org, attom):
    calls, script = attom
    script["attomavm/detail"] = PV.ProviderFailure("api.gateway.attomdata.com refused the credentials (403)")
    p = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id)
    res = PN.attom_lookup(db_session, sample_org.id, p)
    assert res["decision"] == "LOOKED_UP" and res["packages"]["avm"]["state"] == "not_entitled"
    assert p.estimated_value is None and p.parcel_apn == "00-SYNTH-1"
    caps = {c["key"]: c["state"] for c in
            {c["key"]: c for c in PN.readiness(db_session, sample_org.id)["providers"]}["attom"]["capabilities"]}
    assert caps["avm"] == "not in your plan" and caps["detail"] == "included"


def test_timeout_releases_the_unused_reservation(db_session, sample_org, attom):
    calls, script = attom
    script["property/detailmortgageowner"] = PV.ProviderTimeout("slow")
    p = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id, unit=25)
    res = PN.attom_lookup(db_session, sample_org.id, p)
    assert res["decision"] == "TIMEOUT" and res["cost_cents"] == 25
    e = db_session.query(EvoSenseCostEntry).filter_by(organization_id=sample_org.id, provider_key="attom").one()
    assert e.total_cents == 25                     # one request made; the other 75 cents went back
    assert {c["key"]: c for c in PN.readiness(db_session, sample_org.id)["providers"]}["attom"]["status"] == PN.DEGRADED


def test_rate_limit_stops_the_lookup_and_blocks_the_next_one(db_session, sample_org, attom):
    calls, script = attom
    script["assessment/detail"] = PV.ProviderRateLimited(600)
    p = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id)
    res = PN.attom_lookup(db_session, sample_org.id, p)
    assert res["calls"] == 2 and res["partial_failure"]          # the detail package was still used
    assert {c["key"]: c for c in PN.readiness(db_session, sample_org.id)["providers"]}["attom"]["status"] == PN.RATE_LIMITED
    q = _prop(db_session, sample_org.id, street="9 Other Synth St")
    assert PN.attom_plan(db_session, sample_org.id, q)["decision"] == "RATE_LIMITED"


# ── readiness and Test Connection ───────────────────────────────────────────

def test_readiness_is_honest_and_propstream_is_import_only(db_session, sample_org, monkeypatch):
    monkeypatch.delenv("ATTOM_API_KEY", raising=False)
    monkeypatch.delenv("TRACERFY_API_TOKEN", raising=False)
    cards = {c["key"]: c for c in PN.readiness(db_session, sample_org.id)["providers"]}
    assert list(cards) == ["attom", "propstream", "tracerfy"]
    assert cards["attom"]["status"] == cards["tracerfy"]["status"] == PN.NOT_CONFIGURED
    assert cards["propstream"]["status"] == PN.IMPORT_ONLY and cards["propstream"]["test"]["available"] is False
    monkeypatch.setenv("ATTOM_API_KEY", FAKE_KEY)
    monkeypatch.setenv("TRACERFY_API_TOKEN", FAKE_TOKEN)
    cards = {c["key"]: c for c in PN.readiness(db_session, sample_org.id)["providers"]}
    assert cards["attom"]["status"] == cards["tracerfy"]["status"] == PN.READY_TO_TEST     # never "connected" yet
    blob = json.dumps(PN.readiness(db_session, sample_org.id), default=str)
    assert FAKE_KEY not in blob and FAKE_TOKEN not in blob
    assert all(s in PN.STATUSES for s in (c["status"] for c in cards.values()))


def test_tracerfy_test_reads_usage_only_and_then_shows_connected(db_session, sample_org, monkeypatch, http_get):
    seen, answer = http_get
    monkeypatch.setenv("TRACERFY_API_TOKEN", FAKE_TOKEN)
    res = PN.test_connection(db_session, sample_org.id, "tracerfy")
    assert res["ok"] and res["status"] == PN.CONNECTED and res["detail"]["credit_balance"] == 940
    assert seen[0]["url"].endswith("/v1/api/analytics/") and "/trace/" not in seen[0]["url"]
    assert FAKE_TOKEN not in json.dumps(res, default=str)
    import inspect
    assert "timeout=TIMEOUT)" in inspect.getsource(PN) and 0 < PN.TIMEOUT <= 30   # bounded, never hangs


@pytest.mark.parametrize("resp,status", [
    (_Resp(401), PN.UNAVAILABLE), (_Resp(429, headers={"Retry-After": "120"}), PN.RATE_LIMITED),
    (httpx.ReadTimeout("slow"), PN.DEGRADED), (_Resp(500), PN.DEGRADED)])
def test_failed_tests_never_show_connected(db_session, sample_org, monkeypatch, http_get, resp, status):
    seen, answer = http_get
    monkeypatch.setenv("TRACERFY_API_TOKEN", FAKE_TOKEN)
    answer["resp"] = resp
    res = PN.test_connection(db_session, sample_org.id, "tracerfy")
    assert res["ok"] is False and res["status"] == status


def test_attom_test_is_one_request_counted_as_usage(db_session, sample_org, monkeypatch, http_get):
    seen, answer = http_get
    monkeypatch.setenv("ATTOM_API_KEY", FAKE_KEY)
    _attom_ready(db_session, sample_org.id, unit=7)
    answer["resp"] = _Resp(404)                     # key accepted; the sample address has no record
    res = PN.test_connection(db_session, sample_org.id, "attom")
    assert res["ok"] and res["status"] == PN.CONNECTED and len(seen) == 1
    assert seen[0]["headers"]["APIKey"] == FAKE_KEY and FAKE_KEY not in json.dumps(res, default=str)
    e = db_session.query(EvoSenseCostEntry).filter_by(organization_id=sample_org.id, provider_key="attom").one()
    assert (e.operation, e.total_cents) == ("attom_test", 7)


def test_no_credential_means_the_test_calls_nothing(db_session, sample_org, monkeypatch, http_get):
    seen, _ = http_get
    monkeypatch.delenv("TRACERFY_API_TOKEN", raising=False)
    assert PN.test_connection(db_session, sample_org.id, "tracerfy")["status"] == PN.NOT_CONFIGURED and seen == []
    assert PN.test_connection(db_session, sample_org.id, "propstream")["status"] == PN.IMPORT_ONLY


# ── TRACERFY (the existing adapter, edge cases) ─────────────────────────────

def _trace(monkeypatch, body):
    monkeypatch.setenv("TRACERFY_API_TOKEN", FAKE_TOKEN)

    def fake(method, url, *, headers=None, json=None, params=None, auth=None):
        if isinstance(body, Exception):
            raise body
        return dict(body, _status=200)
    monkeypatch.setattr(V, "_http", fake)


def _inp(**kw):
    from app.services import wholesale_enrichment as WE
    return WE.EnrichmentInput(owner_name=kw.get("owner", "PAT SYNTH"), street_address="1 Synth St", city="Dallas",
                              state="TX", zip_code="75201")


def test_tracerfy_empty_response_is_a_free_miss(monkeypatch):
    from app.services import wholesale_enrichment as WE
    _trace(monkeypatch, {"hit": False, "persons": [], "credits_deducted": 0})
    res = WE.PROVIDERS["tracerfy"].lookup(_inp())
    assert res.status == WE.STATUS_NO_MATCH and not res.billable and res.cost_cents == 0


def test_tracerfy_ambiguous_identity_is_evidence_not_a_verdict(monkeypatch):
    from app.services import wholesale_enrichment as WE
    _trace(monkeypatch, {"hit": True, "credits_deducted": 5, "persons": [
        {"full_name": "SOMEONE UNRELATED", "phones": [{"number": "2145550101", "type": "Mobile", "dnc": False}],
         "emails": []}]})
    res = WE.PROVIDERS["tracerfy"].lookup(_inp(owner="PAT SYNTH LLC"))
    assert res.status == WE.STATUS_SUCCEEDED and res.match_evidence["name_match"] == "none"
    assert res.cost_cents == 10 and res.billable


def test_tracerfy_rate_limit_is_a_retry_later_never_a_loop(monkeypatch):
    from app.services import wholesale_enrichment as WE
    _trace(monkeypatch, PV.ProviderRateLimited(900))
    res = WE.PROVIDERS["tracerfy"].lookup(_inp())
    assert res.status == WE.STATUS_FAILED and "slow down" in res.message
    with pytest.raises(PV.ProviderRateLimited):
        PV.PROVIDERS["tracerfy_contact"].enrich(_inp())


def test_tracerfy_missing_credentials_never_calls(monkeypatch):
    from app.services import wholesale_enrichment as WE
    monkeypatch.delenv("TRACERFY_API_TOKEN", raising=False)
    monkeypatch.setattr(V, "_http", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    assert WE.PROVIDERS["tracerfy"].lookup(_inp()).status == WE.STATUS_NOT_CONFIGURED


# ── PROPSTREAM (import cases not covered by test_propstream_import.py) ──────

def _import(db, org_id, csv_text, filename="ps.csv"):
    from app.services.evosense import hunt as HU
    return HU.import_csv(db, org_id, csv_text, user=None, filename=filename)


def test_propstream_alternate_headers_county_and_claims(db_session, sample_org):
    from app.services.evosense import propstream_csv as PS
    head = "Site Address,Site City,Site State,Site Zip,Property County,Owner 1 First Name,Owner 1 Last Name,Tax Delinquent,Vacant?\n"
    row = "77 Synth Ave,Dallas,TX,75215,Dallas County,Pat,Synth,Yes,No\n"
    assert PS.is_propstream(head.strip().split(","))
    out = _import(db_session, sample_org.id, head + row)
    assert out["format"]["format"] == "propstream" and out["counts"]["created"] == 1
    p = db_session.query(EvoSenseProperty).filter_by(organization_id=sample_org.id, street_address="77 Synth Ave").one()
    assert p.county == "Dallas"                                     # "Dallas County" normalized
    from app.models.evosense_models import EvoSenseSignal
    sig = db_session.query(EvoSenseSignal).filter_by(property_id=p.id).all()
    types = {s.signal_type for s in sig}
    assert "TAX_DELINQUENT" in types and "VACANT" not in types       # "No" never asserts anything
    td = [s for s in sig if s.signal_type == "TAX_DELINQUENT"]
    assert td and all(s.evidence_basis == "imported PropStream export" for s in td)
    assert all("not verified" in json.dumps(C.jload(s.provenance, {}) or {}) for s in td)


def test_propstream_rejects_malformed_rows_with_reasons(db_session, sample_org):
    head = "Property Address,Property City,Property Zip,Owner Name,Vacant\n"
    rows = "12 Synth Ct,Dallas,75201,A SYNTH,Y\n,Dallas,75201,NO ADDRESS,Y\n13 Synth Ct,,,NO CITY OR ZIP,Y\n"
    out = _import(db_session, sample_org.id, head + rows)
    assert out["counts"]["created"] == 1 and out["counts"]["rejected"] == 2
    assert all("required" in r["reason"] for r in out["rejected"])


def test_propstream_without_an_address_column_is_refused_plainly(db_session, sample_org):
    with pytest.raises(ValueError) as exc:
        _import(db_session, sample_org.id, "Owner Name,Vacant\nA SYNTH,Y\n")
    assert "street_address" in str(exc.value)


def test_propstream_reimport_and_reformatted_address_never_duplicate(db_session, sample_org):
    head = "Property Address,Property City,Property State,Property Zip,Owner Name,Vacant\n"
    _import(db_session, sample_org.id, head + "500 Synth Street,Dallas,TX,75201,A SYNTH,Y\n")
    again = _import(db_session, sample_org.id, head + "500 Synth Street,Dallas,TX,75201,A SYNTH,Y\n")
    assert again["counts"]["created"] == 0 and again["counts"]["seen"] == 1
    other = _import(db_session, sample_org.id, head + "500 SYNTH ST,DALLAS,TX,75201,A SYNTH,Y\n")
    assert other["counts"]["created"] == 0
    assert db_session.query(EvoSenseProperty).filter_by(organization_id=sample_org.id).count() == 1


def test_propstream_import_counts_conflicts_and_keeps_the_county_value(db_session, sample_org):
    p = _prop(db_session, sample_org.id, street="600 Synth Street", square_feet=1500, year_built=1960)
    p.zip_code = "75201"
    from app.services.evosense import identity as ID
    ID.apply_keys(p, {"street_address": p.street_address, "unit": None, "zip_code": "75201", "city": "Dallas",
                      "state": "TX", "parcel_apn": None, "county": "Dallas"})
    db_session.flush()
    head = "Property Address,Property City,Property State,Property Zip,Owner Name,Building Sqft,Vacant\n"
    out = _import(db_session, sample_org.id, head + "600 Synth Street,Dallas,TX,75201,COUNTY OWNER OF RECORD,2400,Y\n")
    assert out["counts"]["merged"] == 1 and out["counts"]["conflicts"] >= 1
    assert p.square_feet == 1500                                    # the county value stands


# ── TENANT ISOLATION ────────────────────────────────────────────────────────

@pytest.fixture()
def other_org(db_session):
    org = Organization(name="Other Wholesale Tenant (TEST)", slug="other-tenant-test", plan="standard",
                       industry="real_estate")
    db_session.add(org)
    db_session.commit()
    return org


def test_one_workspace_never_sees_or_reuses_anothers_paid_results(db_session, sample_org, other_org, attom):
    calls, _ = attom
    a = _prop(db_session, sample_org.id)
    _attom_ready(db_session, sample_org.id)
    PN.attom_lookup(db_session, sample_org.id, a)
    # the other workspace: same address, its own property, nothing inherited
    b = _prop(db_session, other_org.id)
    plan_b = PN.attom_plan(db_session, other_org.id, b)
    assert plan_b["decision"] == "SWITCHED_OFF"                     # its own switch, still off
    assert PN.property_intelligence(db_session, other_org.id, b)["attom"]["last"] is None
    cards_a = {c["key"]: c for c in PN.readiness(db_session, sample_org.id)["providers"]}
    cards_b = {c["key"]: c for c in PN.readiness(db_session, other_org.id)["providers"]}
    assert cards_a["attom"]["status"] == PN.CONNECTED and cards_b["attom"]["status"] == PN.READY_TO_TEST
    assert cards_b["attom"]["usage"] == {"calls": 0, "cents": 0}
    assert db_session.query(EvoSenseObservation).filter_by(organization_id=other_org.id, provider_key="attom").count() == 0


# ── API: permissions, privacy, isolation ────────────────────────────────────

def _user(db, org, role, email):
    from app.services.auth_service import create_access_token, hash_password
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pw-Synth-123!"), full_name=email,
             role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def test_api_hides_setting_names_from_workspace_admins_and_gates_paid_actions(client, db_session, sample_org,
                                                                             monkeypatch, http_get):
    monkeypatch.delenv("ATTOM_API_KEY", raising=False)
    admin = _user(db_session, sample_org, "org_admin", "pn-admin@synth.test")
    r = client.get("/wholesale/evosense/provider-network", headers=admin)
    assert r.status_code == 200, r.text
    assert "ATTOM_API_KEY" not in r.text and "TRACERFY_API_TOKEN" not in r.text
    assert [c["status"] for c in r.json()["providers"]][1] == PN.IMPORT_ONLY
    advisor = _user(db_session, sample_org, "advisor", "pn-advisor@synth.test")
    p = _prop(db_session, sample_org.id)
    db_session.commit()
    assert client.post("/wholesale/evosense/provider-network/tracerfy/test", headers=advisor).status_code == 403
    assert client.post("/wholesale/evosense/properties/%s/attom" % p.id, headers=advisor, json={}).status_code == 403
    r = client.patch("/wholesale/evosense/provider-network/attom", headers=admin, json={"enabled": True, "unit_cents": 30})
    assert r.status_code == 200, r.text
    assert C.jload(PV.config(db_session, sample_org.id, "attom").cost_overrides, {})[C.ASSESSOR] == 30
    assert client.patch("/wholesale/evosense/provider-network/tracerfy", headers=admin,
                        json={"unit_cents": 5}).status_code == 422
    r = client.get("/wholesale/evosense/properties/%s/intelligence" % p.id, headers=admin)
    assert r.status_code == 200 and r.json()["attom"]["plan"]["decision"] == "NOT_CONFIGURED"


def test_api_another_workspaces_property_is_not_found(client, db_session, sample_org, other_org):
    p = _prop(db_session, sample_org.id)
    db_session.commit()
    outsider = _user(db_session, other_org, "org_admin", "pn-outsider@synth.test")
    assert client.get("/wholesale/evosense/properties/%s/intelligence" % p.id, headers=outsider).status_code in (403, 404)
    assert client.post("/wholesale/evosense/properties/%s/attom" % p.id, headers=outsider, json={}).status_code in (403, 404)
