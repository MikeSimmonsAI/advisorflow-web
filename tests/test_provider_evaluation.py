"""The provider evaluation harness and the commercial vendor adapters.

NO VENDOR IS CALLED. Every adapter is exercised against the response shapes
its vendor documents, by replacing the one HTTP function (`vendors._http`).
Holds the line on:
  * evaluation-only vendors are never routed in production, never counted
    operational, and never write a contact, comp, lead or message;
  * a paid run needs the owner's exact confirmation; misses cost nothing
    where the vendor says so; every cent is in the ledger;
  * comps: only a closed sale counts - an estimate, a list price, an AVM or a
    Texas "last sale price" of unknown origin never does;
  * evaluation data can be deleted, leaving the metrics;
  * a scheduled hunt that already ran cannot run again in the gap between
    another worker's due check and its lock.
"""
import json
from datetime import date, timedelta

import pytest

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseCostEntry, EvoSenseProperty,
                                        EvoSenseProviderEvaluation, EvoSenseStrategy)
from app.models.models import Lead, User
from app.models.wholesale_models import WholesaleComp
from app.services import comp_rules as CR
from app.services.auth_service import create_access_token, hash_password
from app.services.evosense import capabilities as CAPS
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import hunt as HU
from app.services.evosense import provider_eval as PE
from app.services.evosense import providers as PV
from app.services.evosense import sandbox_seed as SS
from app.services.evosense import vendors as V
from app.services.evosense.enrichment import _input_for

# ── documented response shapes (abridged from each vendor's API reference) ──

TRACERFY_HIT = {"hit": True, "persons_count": 2, "credits_deducted": 5, "persons": [
    {"first_name": "Someone", "last_name": "Else", "full_name": "Someone Else", "property_owner": False,
     "phones": [{"number": "2145550999", "type": "Landline", "dnc": False, "carrier": "X", "rank": 1}],
     "emails": []},
    {"first_name": "Pat", "last_name": "Owner", "full_name": "Pat Owner", "property_owner": True,
     "litigator": False, "mailing_address": {"street": "1 Main", "city": "Dallas", "state": "TX", "zip": "75201"},
     "phones": [{"number": "(214) 555-0101", "type": "Mobile", "dnc": False, "carrier": "T", "rank": 1},
                {"number": "2145550102", "type": "Landline", "dnc": True, "carrier": "A", "rank": 2}],
     "emails": [{"email": "Pat@Example.com", "rank": 1}]}]}
TRACERFY_HIT_ONE = dict(TRACERFY_HIT, persons_count=1, persons=[TRACERFY_HIT["persons"][1]])
TRACERFY_MISS = {"hit": False, "persons_count": 0, "credits_deducted": 0, "persons": []}
REAPI_SKIP = {"requestId": "req-1", "match": True, "cached": False, "credits": 1, "output": {"identity": {
    "names": [{"personId": "p1", "fullName": "Pat Owner"}],
    "phones": [{"personId": "p1", "phone": "2145550201", "phoneType": "Mobile", "isConnected": True,
                "doNotCall": False, "lastSeen": "2026-06-01"}],
    "emails": [{"personId": "p1", "email": "pat@x.com", "emailType": "personal"}]}}}
DATASKIP = {"success": True, "found": True, "charged": 0.04,
            "contact": {"fullName": "PAT OWNER"},
            "phones": [{"number": "2145550301", "type": "mobile", "dnc": False}], "emails": ["p@y.com"]}
TWILIO = {"valid": True, "line_type_intelligence": {"type": "mobile", "carrier_name": "T-Mobile"}}


def _reapi_comp(i, *, mls=True, state="TX", list_only=False):
    d = (date.today() - timedelta(days=40 + 20 * i)).isoformat()
    c = {"id": "c%s" % i, "distance": 0.3 + 0.1 * i, "address": {"street": "%s Comp Rd" % (10 + i),
         "city": "Dallas", "state": state, "zip": "75201"}, "bedrooms": 3, "bathrooms": 2,
         "yearBuilt": "1985", "squareFeet": "1500", "latitude": 32.8, "longitude": -96.8,
         "propertyType": "SFR", "estimatedValue": "999999", "preForeclosure": False}
    if list_only:
        c["mlsListingPrice"] = 300000
    elif mls:
        c.update({"mlsSoldPrice": 270000 + 3000 * i, "mlsLastSaleDate": d})
    else:
        c.update({"lastSaleAmount": str(265000 + 3000 * i), "lastSaleDate": d})
    return c


@pytest.fixture()
def http(monkeypatch):
    """Route vendors._http by URL to canned documented responses; record calls."""
    calls, answers = [], {}
    for k in ("TRACERFY_API_TOKEN", "REAPI_API_KEY", "DATASKIP_API_TOKEN", "TWILIO_ACCOUNT_SID",
              "TWILIO_AUTH_TOKEN"):
        monkeypatch.setenv(k, "test-" + k.lower())

    def fake(method, url, **kw):
        calls.append((method, url, kw.get("json") or kw.get("params")))
        for frag, body in answers.items():
            if frag in url:
                return body() if callable(body) else json.loads(json.dumps(body))
        raise AssertionError("unexpected vendor call %s" % url)
    monkeypatch.setattr(V, "_http", fake)
    return calls, answers


@pytest.fixture()
def world(db_session, sample_org, sample_advisor, monkeypatch):
    db, org = db_session, sample_org.id
    SS.enable_sandbox(db, org)
    s = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Eval (TEST)", daily_budget_cents=100000,
                                                          outreach_policy={"auto_outreach": False}))
    db.commit()
    HU.run_strategy(db, org, s, trigger="test", enrich=False)
    db.commit()
    for k in ("TRACERFY_API_TOKEN", "REAPI_API_KEY", "DATASKIP_API_TOKEN", "TWILIO_ACCOUNT_SID",
              "TWILIO_AUTH_TOKEN"):
        monkeypatch.setenv(k, "test-" + k.lower())
    for key in ("tracerfy", "reapi_skiptrace", "dataskip", "twilio_lookup", "reapi_comps"):
        PV.config(db, org, key).enabled = True
    # Three "real" properties (the pilot's shape): individual owners awaiting contact data.
    real = []
    for p in (db.query(EvoSenseProperty).filter_by(organization_id=org)
              .order_by(EvoSenseProperty.opportunity_score.desc()).all()):
        o = CT.primary_owner(db, p)
        if o is not None and (o.owner_type or "individual") in ("individual", "unknown") \
                and p.street_address != "1805 Nolte Dr":
            p.is_test = False
            p.contactability = "ENRICHMENT_NEEDED"
            real.append(p)
        if len(real) == 3:
            break
    db.commit()
    return {"db": db, "org": org, "real": real, "strategy": s}


def _counts(db, org):
    return (db.query(EvoSenseContactPoint).filter_by(organization_id=org).count(),
            db.query(Lead).filter_by(organization_id=org).count(),
            db.query(WholesaleComp).filter_by(organization_id=org).count())


# ── adapters parse what the vendors document ──────────────────────────────

def test_tracerfy_picks_the_owner_of_record_types_lines_and_charges_only_hits(http):
    calls, answers = http
    answers["tracerfy.com"] = TRACERFY_HIT
    inp = V.WE.EnrichmentInput(street_address="1 Main", city="Dallas", state="TX", zip_code="75201",
                               owner_name="OWNER PAT")
    res = V.TracerfySkipTrace().enrich(inp)
    assert res.owner_name == "Pat Owner" and res.match_evidence["name_match"] == "full"
    assert [(p.number, p.phone_type, p.dnc_flag) for p in res.phones] == [
        ("2145550101", "mobile", False), ("2145550102", "landline", True)]
    assert res.emails[0].address == "pat@example.com"
    assert res.billable and res.cost_cents == 10                      # 5 credits x $0.02
    assert calls[0][1].endswith("/trace/lookup/") and calls[0][2]["find_owner"] is True
    answers["tracerfy.com"] = TRACERFY_MISS
    miss = V.TracerfySkipTrace().enrich(inp)
    assert miss.status == V.WE.STATUS_NO_MATCH and not miss.billable and miss.cost_cents == 0


def test_reapi_dataskip_and_twilio_adapters_parse_their_documented_shapes(http):
    _, answers = http
    answers["SkipTrace"] = REAPI_SKIP
    answers["dataskip"] = DATASKIP
    answers["lookups.twilio.com"] = TWILIO
    inp = V.WE.EnrichmentInput(street_address="1 Main", city="Dallas", state="TX", owner_name="OWNER PAT")
    r = V.RealEstateApiSkipTrace().enrich(inp)
    assert r.phones[0].last_seen == "2026-06-01" and r.phones[0].phone_type == "mobile"
    assert r.phones[0].provider_reference == "p1" and r.provider_reference == "req-1"
    d = V.DataSkipSkipTrace().enrich(inp)
    assert d.phones[0].number == "2145550301" and d.cost_cents == 4 and d.owner_name == "PAT OWNER"
    t = V.TwilioLookupLineType().validate_phone("2145550101")
    assert t["line_type"] == "mobile" and t["carrier"] == "T-Mobile"


def test_reapi_comps_classify_every_price_by_origin():
    tx_mls = V.reapi_comp(_reapi_comp(1), "TX")
    tx_record = V.reapi_comp(_reapi_comp(2, mls=False), "TX")
    az_record = V.reapi_comp(_reapi_comp(3, mls=False, state="AZ"), "AZ")
    listing = V.reapi_comp(_reapi_comp(4, list_only=True), "TX")
    assert tx_mls["price_source"] == CR.PRICE_MLS_CLOSED and tx_mls["sale_price"] > 0
    assert tx_record["price_source"] == CR.PRICE_UNVERIFIED_RECORD        # Texas: origin unknown
    assert az_record["price_source"] == CR.PRICE_PUBLIC_RECORD
    assert listing["price_source"] == CR.PRICE_LIST and listing["sale_date"] is None
    assert tx_mls["reference"]["avm_type"].endswith("never ARV")


def test_only_a_closed_sale_passes_the_eligibility_rules():
    from types import SimpleNamespace
    subj = SimpleNamespace(square_feet=1500, bedrooms=3, bathrooms=2, year_built=1985, lot_size_sqft=None,
                           property_type="single_family", latitude=None, longitude=None)
    for src, ok in ((CR.PRICE_MLS_CLOSED, True), (CR.PRICE_PUBLIC_RECORD, True),
                    (CR.PRICE_UNVERIFIED_RECORD, False), (CR.PRICE_ESTIMATED, False),
                    (CR.PRICE_LIST, False), (CR.PRICE_AVM, False)):
        comp = SimpleNamespace(sale_price=270000, sale_date=date.today() - timedelta(days=60),
                               square_feet=1500, bedrooms=3, bathrooms=2, year_built=1985,
                               property_type="single_family", distance_miles=0.4, provider_key="v",
                               source_reference="ref", sale_type="arms_length", price_source=src,
                               included=True, street_address="x", lot_size_sqft=None,
                               latitude=None, longitude=None)
        ev = CR.evaluate(comp, subj, dict(CR.DEFAULT_RULES))
        assert ev["eligible"] is ok, src
        if not ok:
            assert "PRICE_NOT_CLOSED_SALE" in {e["code"] for e in ev["excluded"]}


# ── evaluation only: never production ────────────────────────────────────

def test_evaluation_only_vendors_are_never_routed_or_operational(world):
    db, org = world["db"], world["org"]
    for cap in (C.CONTACT_ENRICHMENT, C.PHONE_VALIDATION, C.COMPS):
        keys = [p.key for p, _, _ in PV.route(db, org, cap, sandbox_allowed=False)]
        assert not set(keys) & {"tracerfy", "reapi_skiptrace", "dataskip", "twilio_lookup", "reapi_comps"}
    caps = {c["capability"]: c for c in CAPS.matrix(db, org)}
    for cap in ("PHONE", "EMAIL", "LINE_TYPE", "PHONE_VALIDATION", "SOLD_COMPS"):
        assert caps[cap]["real_operational"] == [], cap
        assert caps[cap]["state"] not in (CAPS.S_OPERATIONAL, CAPS.S_UNVERIFIED), cap
    row = next(r for r in caps["PHONE"]["providers"] if r["provider"] == "tracerfy")
    assert row["state"] == CAPS.S_EVALUATION and row["operational"] is False
    assert CAPS.is_operational(db, org, "PHONE") is False


def test_without_credentials_a_vendor_is_not_configured(db_session, sample_org, monkeypatch):
    monkeypatch.delenv("TRACERFY_API_TOKEN", raising=False)
    p = PV.PROVIDERS["tracerfy"]
    row = CAPS.provider_capability(db_session, sample_org.id, p, [C.CONTACT_ENRICHMENT])
    assert row["state"] == CAPS.S_NOT_CONFIGURED and "evaluation" in row["why"].lower()


# ── contact mode ─────────────────────────────────────────────────────────

def test_a_paid_contact_evaluation_needs_the_exact_confirmation(world, http):
    db, org, real = world["db"], world["org"], world["real"]
    ids = [p.id for p in real]
    with pytest.raises(PE.EvaluationRefused) as exc:
        PE.run(db, org, name="x", provider_keys=["tracerfy", "dataskip"], property_ids=ids)
    assert "RUN PAID EVALUATION 6" in str(exc.value)
    with pytest.raises(PE.EvaluationRefused) as exc:
        PE.run(db, org, name="x", provider_keys=["tracerfy"], property_ids=ids,
               referee_key="twilio_lookup")
    assert "RUN PAID EVALUATION %s" % (3 + 3 * 1 * PE.REFEREE_MAX_PER_LOOKUP) in str(exc.value)
    assert http[0] == []                                              # nothing was called


def test_contact_evaluation_is_isolated_metered_and_measured(world, http):
    db, org, real = world["db"], world["org"], world["real"]
    calls, answers = http
    seq = iter([TRACERFY_HIT_ONE, TRACERFY_MISS, TRACERFY_HIT_ONE])
    answers["tracerfy.com"] = lambda: json.loads(json.dumps(next(seq)))
    answers["lookups.twilio.com"] = TWILIO
    before = _counts(db, org)
    ids = [p.id for p in real]
    truth = {ids[0]: {"phones": ["2145550101"]}}
    ev = PE.run(db, org, name="Pilot contact eval", provider_keys=["tracerfy"], property_ids=ids,
                referee_key="twilio_lookup", ground_truth=truth,
                confirm="RUN PAID EVALUATION %s" % (3 + 3 * PE.REFEREE_MAX_PER_LOOKUP))
    out = PE.payload(ev)
    assert out["label"] == "REAL" and out["results"]["label"].startswith("REAL PROVIDER EVALUATION DATA")
    m = out["results"]["providers"]["tracerfy"]
    assert m["attempted"] == 3 and m["match_rate"] == round(2 / 3, 3)
    assert m["mobile_coverage"] == round(2 / 3, 3) and m["email_coverage"] == round(2 / 3, 3)
    assert m["correct_owner_rate_truth"] == 1.0
    assert m["line_type_compared"] >= 2 and m["line_type_agreement"] is not None
    # money: 2 hits x 10c + the miss free + referee lookups at 1c each (2 distinct numbers)
    assert m["cost_cents"] == 20
    assert ev.total_cost_cents == 20 + 2
    charged = sum(e.total_cents or 0 for e in db.query(EvoSenseCostEntry).filter(
        EvoSenseCostEntry.organization_id == org, EvoSenseCostEntry.operation == "provider_evaluation",
        EvoSenseCostEntry.status == "charged"))
    assert charged == ev.total_cost_cents
    for k in ("cost_per_lookup_cents", "cost_per_matched_owner_cents", "cost_per_usable_contact_cents",
              "cost_per_verified_contact_cents", "false_positive_rate", "median_latency_ms",
              "owner_name_agreement_rate"):
        assert k in m
    # isolation: nothing became a contact, a lead or a comp; contactability untouched
    assert _counts(db, org) == before
    assert all(p.contactability == "ENRICHMENT_NEEDED" for p in real)
    recs = out["results"]["records"]
    assert recs and recs[0]["phones"][0]["number"] == "2145550101"
    # deletion after the evaluation: values and ground truth gone, metrics kept
    PE.purge(db, ev)
    after = PE.payload(ev)
    assert after["purged"] and after["results"]["records"] == []
    assert json.loads(ev.sample)["ground_truth"] == {}
    assert after["results"]["providers"]["tracerfy"]["match_rate"] == m["match_rate"]


def test_a_failing_vendor_is_refunded_and_recorded_per_capability(world, http, monkeypatch):
    db, org, real = world["db"], world["org"], world["real"]
    _, answers = http

    def boom():
        raise V.ProviderFailure("tracerfy.com answered 500")
    answers["tracerfy.com"] = boom
    ev = PE.run(db, org, name="x", provider_keys=["tracerfy"], property_ids=[real[0].id],
                confirm="RUN PAID EVALUATION 1")
    m = PE.payload(ev)["results"]["providers"]["tracerfy"]
    assert m["failures"] == 1 and ev.total_cost_cents == 0
    assert db.query(EvoSenseCostEntry).filter_by(organization_id=org, status="failed_refunded").count() >= 1


# ── comps mode ───────────────────────────────────────────────────────────

def test_sandbox_comps_mode_counts_only_closed_sales_and_writes_no_deal(world):
    db, org = world["db"], world["org"]
    props = (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org,
                                               EvoSenseProperty.is_test.is_(True),
                                               EvoSenseProperty.square_feet.isnot(None)).limit(3).all())
    before = _counts(db, org)
    ev = PE.run(db, org, name="Comps (synthetic)", mode="comps", provider_keys=["sandbox_comps"],
                property_ids=[p.id for p in props])
    out = PE.payload(ev)
    assert out["synthetic"] and out["mode"] == "comps"
    m = out["results"]["providers"]["sandbox_comps"]
    assert m["comps_by_price_source"][CR.PRICE_MLS_CLOSED] == 4 * len(props)
    assert m["closed_price_share"] == round(4 / 7, 3)
    assert m["production_ready"] is False
    rec = out["results"]["records"][0]
    excluded = {x["address"]: x["why"] for x in rec["would_be_arv"]["excluded"]}
    assert any("PRICE_NOT_CLOSED_SALE" in why for why in excluded.values())
    assert _counts(db, org) == before                                  # no comp, no deal value


def test_a_texas_record_price_of_unknown_origin_never_makes_an_arv(world, http):
    db, org, real = world["db"], world["org"], world["real"]
    _, answers = http
    answers["PropertyComps"] = {"comps": [_reapi_comp(i, mls=False) for i in range(5)]}
    prop = real[0]
    prop.square_feet = prop.square_feet or 1500
    prop.state = "TX"
    db.flush()
    ev = PE.run(db, org, name="REAPI comps", mode="comps", provider_keys=["reapi_comps"],
                property_ids=[prop.id], confirm="RUN PAID EVALUATION 1")
    m = PE.payload(ev)["results"]["providers"]["reapi_comps"]
    assert m["comps_by_price_source"] == {CR.PRICE_UNVERIFIED_RECORD: 5}
    assert m["closed_price_share"] == 0 and m["subjects_with_3_eligible"] == 0
    assert m["would_be_arv_confidence"] == {"insufficient": 1}
    assert m["closed_price_origin_confirmed"] is False and "NOT established" in m["readiness_note"]


def test_mls_labelled_comps_can_form_an_arv_but_are_never_production_ready(world, http):
    db, org, real = world["db"], world["org"], world["real"]
    _, answers = http
    answers["PropertyComps"] = {"comps": [_reapi_comp(i) for i in range(4)]}
    prop = real[1]
    prop.square_feet, prop.bedrooms, prop.bathrooms, prop.year_built = 1500, 3, 2, 1985
    prop.property_type = "single_family"
    db.flush()
    ev = PE.run(db, org, name="REAPI comps", mode="comps", provider_keys=["reapi_comps"],
                property_ids=[prop.id], confirm="RUN PAID EVALUATION 1")
    m = PE.payload(ev)["results"]["providers"]["reapi_comps"]
    assert m["mls_closed_share"] == 1.0 and m["subjects_with_3_eligible"] == 1.0
    assert m["production_ready"] is False


# ── HTTP: setup, admin-only, tenant scope, purge ─────────────────────────

@pytest.fixture()
def admin_h(db_session, sample_org):
    u = User(organization_id=sample_org.id, email="evadmin@t.test", password_hash=hash_password("TestPass123!"),
             full_name="Eval Admin", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(u, db_session)}


def test_setup_proposes_the_real_awaiting_sample_and_calls_no_one(client, auth_headers, world, http):
    r = client.get("/wholesale/evosense/provider-evaluations/setup", headers=auth_headers,
                   params={"mode": "contact"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {i["property_id"] for i in body["sample"]["items"]} == {p.id for p in world["real"]}
    keys = {p["key"]: p for p in body["providers"]["contact"]}
    assert keys["tracerfy"]["ready"] and keys["tracerfy"]["evaluation_only"]
    assert keys["tracerfy"]["price_confirmed"] is True and keys["reapi_skiptrace"]["price_confirmed"] is False
    assert any(p["key"] == "twilio_lookup" for p in body["providers"]["referees"])
    assert http[0] == []


def test_records_are_admin_only_purge_is_admin_only_and_tenant_scoped(client, auth_headers, admin_h, world,
                                                                     http, db_session):
    db, org = world["db"], world["org"]
    _, answers = http
    answers["tracerfy.com"] = TRACERFY_HIT
    ev = PE.run(db, org, name="x", provider_keys=["tracerfy"], property_ids=[world["real"][0].id],
                confirm="RUN PAID EVALUATION 1")
    db.commit()
    path = "/wholesale/evosense/provider-evaluations/%s" % ev.id
    assert client.get(path, headers=auth_headers).json()["results"]["records"] is None
    assert client.get(path, headers=admin_h).json()["results"]["records"]
    assert client.post(path + "/purge", headers=auth_headers).status_code == 403
    r = client.post(path + "/purge", headers=admin_h)
    assert r.status_code == 200 and r.json()["purged"]


# ── the scheduled-hunt race ──────────────────────────────────────────────

def test_a_scheduled_hunt_that_already_ran_cannot_run_again_before_it_is_due(db_session, sample_org,
                                                                            sample_advisor):
    """Worker B checked 'due' before worker A's hunt finished. When B then
    reaches the lock, A has finished and released it: B must NOT hunt (and
    pay) again. The due check is now part of the atomic claim."""
    db, org = db_session, sample_org.id
    SS.enable_sandbox(db, org)
    s = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Race (TEST)",
                                                          outreach_policy={"auto_outreach": False}))
    db.commit()
    a = HU.run_strategy(db, org, s, trigger="schedule", enrich=False)
    assert a.status == "succeeded"
    b = HU.run_strategy(db, org, s, trigger="schedule", enrich=False)    # B's stale "due" decision
    assert b.status == "skipped" and "not due" in b.error
    m = HU.run_strategy(db, org, s, trigger="manual", enrich=False)      # a person may always hunt
    assert m.status == "succeeded"


def test_an_enabled_evaluation_vendor_never_reads_as_a_real_connector(client, auth_headers, world):
    """Enabling Tracerfy so it can be evaluated must not make the Providers
    screen say contact enrichment has a real connector."""
    body = client.get("/wholesale/evosense/providers", headers=auth_headers).json()
    row = next(p for p in body["providers"] if p["key"] == "tracerfy")
    assert row["state"] == PV.S_EVALUATION_ONLY and "EVALUATION ONLY" in row["why"]
    caps = {c["capability"]: c for c in body["capabilities"]}
    assert "Tracerfy skip trace" not in caps["CONTACT_ENRICHMENT"]["providers"]
    assert caps["CONTACT_ENRICHMENT"]["kind"] != C.REAL
    assert "tracerfy" not in body["real_connectors"]


def test_the_proposed_sample_leaves_out_what_would_skew_the_rates_and_says_why(world):
    db, org, real = world["db"], world["org"], world["real"]
    real[0].city = None
    db.flush()
    s = PE.proposed_sample(db, org, "contact")
    assert real[0].id not in {i["property_id"] for i in s["items"]}
    out = {x["property_id"]: x["why"] for x in s["left_out"]}
    assert "no city" in out[real[0].id]


def test_joint_owners_and_estates_are_people_and_stay_in_the_sample(world):
    db, org, real = world["db"], world["org"], world["real"]
    for p, kind in zip(real, ("joint", "estate_indicated", "llc")):
        CT.primary_owner(db, p).owner_type = kind
    db.flush()
    s = PE.proposed_sample(db, org, "contact")
    ids = {i["property_id"] for i in s["items"]}
    assert real[0].id in ids and real[1].id in ids and real[2].id not in ids
    assert "entity owner (llc)" in {x["property_id"]: x["why"] for x in s["left_out"]}[real[2].id]
