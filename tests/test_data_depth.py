"""DATA DEPTH: sold comps, the ARV engine, the MAO gate, repairs, provider
capabilities, the provider evaluation harness and skip-trace candidates.

The bar these tests hold:

  * an ARV comes ONLY from eligible closed sales - never a tax/appraisal value,
    an AVM or a list price - and "INSUFFICIENT COMPARABLE SALES" is a valid,
    stored answer;
  * a comp a person types is MANUAL for good; a person can verify it, which
    never makes it provider-verified;
  * no MAO without an evidence-backed ARV and an accepted repair estimate
    (never an assumed $0);
  * "configured" is never "connected"; a sandbox adapter is never real data;
  * a provider-returned mobile number is a CANDIDATE, and never consent;
  * nothing crosses tenants.

Every record here is TEST data. No provider is called except the synthetic
sandbox adapters.
"""
import json
from datetime import date, timedelta

import pytest

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseProperty,
                                        EvoSenseProviderEvaluation)
from app.models.models import Organization, User
from app.models.wholesale_models import (WholesaleComp, WholesaleDeal, WholesaleProperty,
                                         WholesaleRepairEstimate)
from app.services import arv_engine
from app.services import communication_eligibility as CE
from app.services import comp_rules as CR
from app.services import mao_gate
from app.services import wholesale_repairs as REP
from app.services.auth_service import create_access_token, hash_password
from app.services.evosense import capabilities as CAPS
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import economics as EC
from app.services.evosense import provider_eval as PE
from app.services.evosense import providers as PV
from app.services.evosense import sandbox_seed as SS


def ok(r):
    assert r.status_code in (200, 201), "%s %s -> %s %s" % (
        r.request.method, r.request.url, r.status_code, r.text[:500])
    return r.json()


def _ago(days):
    return (date.today() - timedelta(days=days)).isoformat()


SUBJECT = {"street_address": "900 Depth Test Ln", "city": "Dallas", "state": "TX", "zip_code": "75201",
           "property_type": "single_family", "bedrooms": 3, "bathrooms": 2, "square_feet": 1500,
           "year_built": 1985, "is_test": True, "test_note": "data depth"}


def _comp(n, **over):
    base = {"street_address": "%s Comp St" % (100 + n), "city": "Dallas", "state": "TX",
            "sale_price": 300000 + n * 3000, "sale_date": _ago(60 + n * 10), "square_feet": 1500,
            "bedrooms": 3, "bathrooms": 2, "year_built": 1985, "property_type": "single_family",
            "distance_miles": 0.3 + n * 0.1, "sale_type": "arms_length",
            "source_reference": "MLS #TEST%s closed record" % n}
    base.update(over)
    return base


@pytest.fixture()
def W(client, auth_headers, admin_auth_headers):
    class _W:
        h, admin = auth_headers, admin_auth_headers

        def get(self, path, h=None, **params):
            return client.get("/wholesale" + path, headers=h or self.h, params=params)

        def post(self, path, body=None, h=None):
            return client.post("/wholesale" + path, headers=h or self.h, json=body or {})

        def patch(self, path, body, h=None):
            return client.patch("/wholesale" + path, headers=h or self.h, json=body)

        def deal(self, **over):
            p = ok(self.post("/properties", dict(SUBJECT, **over)))
            return p["id"], p["deal"]["id"]

        def add_comp(self, deal_id, n, **over):
            return ok(self.post("/deals/%s/comps" % deal_id, _comp(n, **over)))

        def valuation(self, deal_id, h=None):
            return ok(self.get("/deals/%s/valuation" % deal_id, h=h))
    return _W()


def _excluded_codes(W, deal_id):
    arv = W.valuation(deal_id)["arv"]
    return {c["address"]: {e["code"] for e in c["excluded"]} for c in arv["comps_excluded"]}


# ── 1-3. Manual comps: visibly MANUAL, never silently provider verified ───────

def test_01_a_typed_comp_is_manual_and_verifying_it_never_makes_it_provider_verified(W, db_session):
    _, deal_id = W.deal()
    out = W.add_comp(deal_id, 1)
    comp = out["comp"]
    assert comp["origin"] == "MANUAL" and comp["verification_state"] == "manual"
    r = W.post("/comps/%s/verify" % comp["id"], {"attestation": "ok"})
    assert r.status_code == 422                                    # must say HOW it was verified
    v = ok(W.post("/comps/%s/verify" % comp["id"], {"attestation": "Checked MLS #TEST1 closed record"}))
    assert v["comp"]["verification_state"] == "human_verified"
    assert v["comp"]["origin"] == "MANUAL"                         # still manual in origin
    row = db_session.query(WholesaleComp).get(comp["id"])
    assert row.provider_key is None and row.verified_by_id and row.attestation


def test_02_a_manual_comp_without_a_source_reference_does_not_count(W):
    _, deal_id = W.deal()
    W.add_comp(deal_id, 1, source_reference=None, street_address="1 NoRef St")
    assert "NO_SOURCE_REFERENCE" in _excluded_codes(W, deal_id)["1 NoRef St"]


def test_03_system_fields_cannot_be_set_through_the_comp_api(W, db_session):
    _, deal_id = W.deal()
    body = _comp(1)
    body.update({"provider_key": "attom", "verification_state": "provider_verified"})
    comp = ok(W.post("/deals/%s/comps" % deal_id, body))["comp"]
    row = db_session.query(WholesaleComp).get(comp["id"])
    assert row.provider_key is None and row.verification_state == "manual"


# ── 4-9. Comp eligibility: versioned, explainable, tenant-configurable ───────

def test_04_a_price_without_a_closed_sale_is_not_a_comp(W):
    _, deal_id = W.deal()
    W.add_comp(deal_id, 1, sale_price=None, street_address="1 Listing St")    # a listing
    W.add_comp(deal_id, 2, sale_date=None, street_address="2 Undated St")
    codes = _excluded_codes(W, deal_id)
    assert "NO_SALE_PRICE" in codes["1 Listing St"]
    assert "NO_SALE_DATE" in codes["2 Undated St"]


def test_05_non_arms_length_sales_never_count(W):
    _, deal_id = W.deal()
    W.add_comp(deal_id, 1, sale_type="foreclosure", street_address="1 REO St")
    W.add_comp(deal_id, 2, sale_type="Family", street_address="2 Family St")
    codes = _excluded_codes(W, deal_id)
    assert "SALE_TYPE" in codes["1 REO St"] and "SALE_TYPE" in codes["2 Family St"]


def test_06_old_far_and_dissimilar_sales_are_excluded_with_the_reason(W):
    _, deal_id = W.deal()
    W.add_comp(deal_id, 1, sale_date=_ago(500), street_address="1 Old St")
    W.add_comp(deal_id, 2, distance_miles=4.5, street_address="2 Far St")
    W.add_comp(deal_id, 3, square_feet=3200, street_address="3 Big St")
    codes = _excluded_codes(W, deal_id)
    assert "TOO_OLD" in codes["1 Old St"] and "TOO_FAR" in codes["2 Far St"]
    assert codes["3 Big St"]                                       # size outside the tolerance
    arv = W.valuation(deal_id)["arv"]
    assert arv["rules_version"] == CR.VERSION and arv["rules"]["max_distance_miles"] == 1.0


def test_07_the_same_sale_entered_twice_counts_once(W):
    _, deal_id = W.deal()
    W.add_comp(deal_id, 1, street_address="1 Dup St")
    W.add_comp(deal_id, 1, street_address="1 Dup St")
    arv = W.valuation(deal_id)["arv"]
    assert len([c for c in arv["comps_used"] if c["address"] == "1 Dup St"]) == 1
    assert any("DUPLICATE_TRANSACTION" in {e["code"] for e in c["excluded"]}
               for c in arv["comps_excluded"])


def test_08_a_person_excluding_a_comp_is_recorded_with_who_and_why(W, db_session):
    _, deal_id = W.deal()
    comp = W.add_comp(deal_id, 1)["comp"]
    ok(W.patch("/comps/%s" % comp["id"], {"included": False, "exclusion_reason": "Flipped - full remodel"}))
    row = db_session.query(WholesaleComp).get(comp["id"])
    assert row.excluded_by_id and row.exclusion_reason == "Flipped - full remodel"
    codes = _excluded_codes(W, deal_id)
    assert "EXCLUDED_BY_PERSON" in codes[comp["street_address"]]


def test_09_comp_rules_are_per_workspace_validated_and_admin_only(W):
    _, deal_id = W.deal()
    for n in (1, 2):
        W.add_comp(deal_id, n)
    W.add_comp(deal_id, 3, distance_miles=1.8)
    assert W.valuation(deal_id)["arv"]["status"] == arv_engine.INSUFFICIENT
    assert W.patch("/settings", {"comp_rules": {"max_distance_miles": 2}}).status_code == 403
    assert W.patch("/settings", {"comp_rules": {"bogus": 1}}, h=W.admin).status_code == 400
    assert W.patch("/settings", {"comp_rules": {"min_comps": 0}}, h=W.admin).status_code == 400
    ok(W.patch("/settings", {"comp_rules": {"max_distance_miles": 2}}, h=W.admin))
    eff = ok(W.get("/settings"))["comp_rules_effective"]
    assert eff["customized"] and eff["rules"]["max_distance_miles"] == 2
    ok(W.post("/deals/%s/analysis/recalculate" % deal_id))
    assert W.valuation(deal_id)["arv"]["status"] == arv_engine.ESTIMATED


# ── 10-14. The ARV engine ────────────────────────────────────────────────────

def test_10_three_legitimate_sold_comps_produce_an_explained_arv(W):
    _, deal_id = W.deal()
    for n in (1, 2, 3):
        W.add_comp(deal_id, n)
    v = W.valuation(deal_id)
    arv = v["arv"]
    assert arv["status"] == arv_engine.ESTIMATED and arv["method"] == "median_psf"
    assert arv["version"] == arv_engine.VERSION and len(arv["comps_used"]) == 3
    assert arv["low"] <= arv["value"] <= arv["high"]
    assert v["arv_value"] == arv["value"] and v["arv_source"] == "estimated"
    conf = arv["confidence"]
    assert conf["label"] in ("high", "medium", "low") and conf["factors"]
    assert any("manual comp" in f["label"].lower() for f in conf["factors"])
    assert any("not an appraisal" in lim for lim in arv["limitations"])


def test_11_insufficient_comps_produce_no_arv_and_clear_a_stale_one(W, db_session):
    _, deal_id = W.deal()
    ids = [W.add_comp(deal_id, n)["comp"]["id"] for n in (1, 2, 3)]
    assert W.valuation(deal_id)["arv_value"] is not None
    ok(W.patch("/comps/%s" % ids[0], {"included": False, "exclusion_reason": "bad data"}))
    v = W.valuation(deal_id)
    assert v["arv"]["status"] == arv_engine.INSUFFICIENT
    assert v["arv"]["label"] == "INSUFFICIENT COMPARABLE SALES"
    assert v["arv_value"] is None and v["arv_confidence"] == "insufficient"
    assert v["mao"]["value"] is None and v["mao"]["status"] == mao_gate.NOT_CALCULATED


def test_12_a_tax_appraisal_or_avm_never_becomes_the_arv(W, db_session):
    prop_id, deal_id = W.deal()
    prop = db_session.query(WholesaleProperty).get(prop_id)
    prop.estimated_value = 410000
    prop.estimated_value_source = "DCAD 2026 appraised value (appraisal district, not a market estimate)"
    db_session.commit()
    W.add_comp(deal_id, 1)
    ok(W.post("/deals/%s/analysis/recalculate" % deal_id))
    v = W.valuation(deal_id)
    assert v["arv_value"] is None and v["arv"]["status"] == arv_engine.INSUFFICIENT
    other = v["arv"]["other_valuations"]["estimated_value"]
    assert other["value"] == 410000 and other["is_arv"] is False


def test_13_a_person_entered_arv_is_not_overwritten_by_comps(W):
    _, deal_id = W.deal()
    ok(W.patch("/deals/%s/analysis" % deal_id, {"arv": 333000}))
    for n in (1, 2, 3):
        W.add_comp(deal_id, n)
    v = W.valuation(deal_id)
    assert v["arv_value"] == 333000 and v["arv_source"] == "manual"
    assert v["arv"]["status"] == arv_engine.ESTIMATED               # still shown beside it


def test_14_confidence_explains_itself_and_verification_raises_it():
    subj = type("S", (), dict(square_feet=1500, bedrooms=3, bathrooms=2, year_built=1985,
                              lot_size_sqft=None, property_type="single_family",
                              latitude=None, longitude=None))()

    def comps(state):
        out = []
        for n in (1, 2, 3):
            d = _comp(n)
            d["sale_date"] = date.fromisoformat(d["sale_date"])
            out.append(type("Cp", (), dict(d, id=str(n), included=True, provider_key=None,
                                           verification_state=state, lot_size_sqft=None,
                                           latitude=None, longitude=None, half_baths=None,
                                           exclusion_reason=None, created_at=str(n)))())
        return out
    rules = dict(CR.DEFAULT_RULES)
    raw = arv_engine.compute(subj, comps("manual"), rules)
    checked = arv_engine.compute(subj, comps("human_verified"), rules)
    assert checked["confidence"]["score"] > raw["confidence"]["score"]
    assert all("points" in f and f["label"] for f in raw["confidence"]["factors"])
    thin = arv_engine.compute(type("S2", (), dict(vars(type(subj)), square_feet=None))(),
                              comps("manual"), rules)
    assert thin["method"] == "median_price"
    assert thin["confidence"]["score"] < raw["confidence"]["score"]


# ── 15-19. The MAO gate and repairs ─────────────────────────────────────────

def _three(W, deal_id):
    for n in (1, 2, 3):
        W.add_comp(deal_id, n)


def test_15_no_arv_means_no_mao(W):
    _, deal_id = W.deal()
    ok(W.post("/deals/%s/repairs" % deal_id, {"status": "MANUAL_ESTIMATE", "amount": 30000}))
    mao = W.valuation(deal_id)["mao"]
    assert mao["value"] is None and mao["status"] == mao_gate.NOT_CALCULATED
    assert any("No ARV" in r for r in mao["reasons"])


def test_16_low_confidence_arv_is_blocked_by_the_workspace_policy(W):
    _, deal_id = W.deal()
    _three(W, deal_id)
    ok(W.post("/deals/%s/repairs" % deal_id, {"status": "MANUAL_ESTIMATE", "amount": 30000}))
    ok(W.patch("/settings", {"mao_policy": {"min_arv_confidence": "high"}}, h=W.admin))
    ok(W.post("/deals/%s/analysis/recalculate" % deal_id))
    v = W.valuation(deal_id)
    if v["arv_confidence"] != "high":
        assert v["mao"]["value"] is None and any("confidence" in r for r in v["mao"]["reasons"])
    assert W.patch("/settings", {"mao_policy": {"min_arv_confidence": "certain"}},
                   h=W.admin).status_code == 400


def test_17_missing_repairs_prevents_mao_never_an_assumed_zero(W):
    _, deal_id = W.deal()
    _three(W, deal_id)
    v = W.valuation(deal_id)
    assert v["repairs"]["status"] == REP.UNKNOWN
    assert v["mao"]["value"] is None
    assert any("Repairs are unknown" in r for r in v["mao"]["reasons"])
    ok(W.post("/deals/%s/repairs" % deal_id, {"status": "MANUAL_ESTIMATE", "amount": 30000}))
    v = W.valuation(deal_id)
    assert v["arv_confidence"] in ("high", "medium"), v["arv"]["confidence"]
    assert v["mao"]["status"] == mao_gate.CALCULATED and v["mao"]["value"] is not None


def test_18_repair_history_is_append_only_and_seller_or_system_numbers_are_not_accepted(W, db_session):
    _, deal_id = W.deal()
    _three(W, deal_id)
    assert W.post("/deals/%s/repairs" % deal_id, {"status": "MANUAL_ESTIMATE"}).status_code == 422
    assert W.post("/deals/%s/repairs" % deal_id, {"status": "UNKNOWN", "amount": 1}).status_code == 422
    ok(W.post("/deals/%s/repairs" % deal_id, {"status": "SELLER_REPORTED", "amount": 5000,
                                              "notes": "Seller: needs a roof"}))
    v = W.valuation(deal_id)
    assert v["repairs"]["status"] == REP.SELLER_REPORTED and v["mao"]["value"] is None
    ok(W.post("/deals/%s/repairs" % deal_id, {"status": "SYSTEM_ESTIMATE", "low": 15000, "high": 30000}))
    assert W.valuation(deal_id)["mao"]["value"] is None               # default policy refuses SYSTEM
    ok(W.post("/deals/%s/repairs" % deal_id, {"status": "INSPECTION_ESTIMATE", "amount": 41000,
                                              "source": "Inspector report 9/26"}))
    v = W.valuation(deal_id)
    hist = v["repairs"]["history"]
    assert [h["status"] for h in hist] == ["SELLER_REPORTED", "SYSTEM_ESTIMATE", "INSPECTION_ESTIMATE"]
    assert [h["current"] for h in hist] == [False, False, True]
    assert v["repairs"]["amount"] == 41000
    assert db_session.query(WholesaleRepairEstimate).filter_by(deal_id=deal_id).count() == 3


def test_19_a_workspace_can_accept_system_estimates_explicitly(W):
    _, deal_id = W.deal()
    _three(W, deal_id)
    ok(W.post("/deals/%s/repairs" % deal_id, {"status": "SYSTEM_ESTIMATE", "amount": 20000}))
    ok(W.patch("/settings", {"mao_policy": {"accepted_repair_statuses": [
        "MANUAL_ESTIMATE", "INSPECTION_ESTIMATE", "VERIFIED", "SYSTEM_ESTIMATE"],
        "min_arv_confidence": "low"}}, h=W.admin))
    ok(W.post("/deals/%s/analysis/recalculate" % deal_id))
    mao = W.valuation(deal_id)["mao"]
    assert mao["status"] == mao_gate.CALCULATED and mao["value"] is not None
    assert W.patch("/settings", {"mao_policy": {"accepted_repair_statuses": ["UNKNOWN"]}},
                   h=W.admin).status_code == 400


# ── 20. Tenant isolation ────────────────────────────────────────────────────

@pytest.fixture()
def org_b(db_session):
    org = Organization(name="Depth Org B", slug="depth-org-b", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    user = User(organization_id=org.id, email="b@depth-b.test", password_hash=hash_password("TestPass123!"),
                full_name="B Admin", role="org_admin", must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return org, {"Authorization": "Bearer %s" % create_access_token(user, db_session)}


def test_20_an_identical_property_in_two_tenants_stays_isolated(W, org_b):
    _, deal_a = W.deal()
    _three(W, deal_a)
    comp_a = W.valuation(deal_a)["arv"]["comps_used"][0]["comp_id"]
    _, hb = org_b
    pb = ok(W.post("/properties", SUBJECT, h=hb))
    deal_b = pb["deal"]["id"]
    vb = W.valuation(deal_b, h=hb)
    assert vb["arv_value"] is None and vb["arv"] is None or vb["arv"]["status"] == arv_engine.INSUFFICIENT
    # B cannot read, verify, edit or add repairs to A's deal or comps
    assert W.get("/deals/%s/valuation" % deal_a, h=hb).status_code == 404
    assert W.post("/comps/%s/verify" % comp_a, {"attestation": "Checked MLS closed"}, h=hb).status_code == 404
    assert W.patch("/comps/%s" % comp_a, {"included": False}, h=hb).status_code == 404
    assert W.post("/deals/%s/repairs" % deal_a, {"status": "MANUAL_ESTIMATE", "amount": 1},
                  h=hb).status_code == 404
    # B's rules do not change A's answer
    ok(W.patch("/settings", {"comp_rules": {"min_comps": 10}}, h=hb))
    ok(W.post("/deals/%s/analysis/recalculate" % deal_a))
    assert W.valuation(deal_a)["arv"]["status"] == arv_engine.ESTIMATED


class _StubRealContact(PV.AcquisitionProvider):
    """A REAL-kind contact provider that is never actually called: stands in
    for a purchased vendor so the registry and the paid-confirmation gate can
    be tested without one."""
    key = "stub_real_contact"
    label = "Stub real contact vendor"
    connector_kind = C.REAL
    capabilities = (C.CONTACT_ENRICHMENT,)
    costs = {C.CONTACT_ENRICHMENT: 25}
    required_env = ()
    calls = []

    def enrich(self, data):
        self.calls.append(data)
        raise AssertionError("a real provider must never be called in tests")


@pytest.fixture()
def stub_real(monkeypatch):
    p = _StubRealContact()
    _StubRealContact.calls = []
    monkeypatch.setitem(PV.PROVIDERS, p.key, p)
    return p


# ── 21. Provider capability registry ────────────────────────────────────────

def test_21_configured_is_never_connected_and_sandbox_is_never_real(client, auth_headers, db_session,
                                                                    sample_org):
    SS.enable_sandbox(db_session, sample_org.id)
    db_session.commit()
    body = ok(client.get("/wholesale/evosense/capabilities", headers=auth_headers))
    caps = {c["capability"]: c for c in body["capabilities"]}
    assert set(caps) == set(CAPS.CAPABILITY_MAP)
    assert "CONNECTED" not in json.dumps(body).upper().replace("NOT CONNECTED", "")
    phone = caps["PHONE"]
    assert phone["state"] in (CAPS.S_SANDBOX, CAPS.S_NOT_CONFIGURED, CAPS.S_MANUAL)
    assert phone["real_operational"] == []
    for row in phone["providers"]:
        if row["synthetic"]:
            assert row["state"] in (CAPS.S_SANDBOX, CAPS.S_NOT_ENABLED)
        assert row["platform"].keys() >= {"configured", "blocked"}
        assert row["tenant"].keys() >= {"enabled", "reachable", "healthy"}
    assert caps["VALUATION"]["label"].endswith("never ARV)")
    assert CAPS.is_operational(db_session, sample_org.id, "SOLD_COMPS") is False


def test_21b_a_configured_real_provider_that_never_ran_is_unverified(db_session, sample_org, stub_real):
    p = stub_real
    cfg = PV.config(db_session, sample_org.id, p.key)
    cfg.enabled = True
    db_session.flush()
    row = CAPS.provider_capability(db_session, sample_org.id, p, [C.CONTACT_ENRICHMENT], cfg=cfg)
    assert row["state"] == CAPS.S_UNVERIFIED and not row["healthy"]
    assert "not 'connected'" in row["why"]
    phone = {c["capability"]: c for c in CAPS.matrix(db_session, sample_org.id)}["PHONE"]
    assert phone["state"] == CAPS.S_UNVERIFIED and phone["real_operational"] == [p.key]
    assert CAPS.is_operational(db_session, sample_org.id, "PHONE") is True     # may be tried
    PV.record_success(cfg, capability=C.CONTACT_ENRICHMENT)
    row = CAPS.provider_capability(db_session, sample_org.id, p, [C.CONTACT_ENRICHMENT], cfg=cfg)
    assert row["state"] == CAPS.S_OPERATIONAL and row["healthy"]
    # health is per capability: success on contact enrichment says nothing about validation
    other = CAPS.provider_capability(db_session, sample_org.id, p, [C.PHONE_VALIDATION], cfg=cfg)
    assert other["tenant"]["last_success_at"] is None
    for _ in range(PV.DEGRADE_AFTER):
        PV.record_failure(cfg, "HTTPError: 500", capability=C.CONTACT_ENRICHMENT)
    row = CAPS.provider_capability(db_session, sample_org.id, p, [C.CONTACT_ENRICHMENT], cfg=cfg)
    assert row["state"] == CAPS.S_DEGRADED and not row["operational"]


# ── 22. Provider evaluation harness ─────────────────────────────────────────

@pytest.fixture()
def hunted(db_session, sample_org, sample_advisor):
    from app.services.evosense import hunt as HU
    db, org = db_session, sample_org.id
    SS.enable_sandbox(db, org)
    s = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Depth (TEST)",
                                                          daily_budget_cents=100000,
                                                          outreach_policy={"auto_outreach": False}))
    db.commit()
    HU.run_strategy(db, org, s, trigger="test", enrich=False)
    db.commit()
    return db, org, s


def test_22_a_sandbox_evaluation_is_synthetic_budgeted_and_writes_no_contacts(hunted, sample_advisor):
    db, org, _ = hunted
    props = (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org)
             .order_by(EvoSenseProperty.opportunity_score.desc()).limit(4).all())
    before = db.query(EvoSenseContactPoint).filter_by(organization_id=org).count()
    sandbox = [k for k in PV.SANDBOX_KEYS
               if C.CONTACT_ENRICHMENT in (PV.PROVIDERS[k].capabilities or ())]
    ev = PE.run(db, org, name="Depth eval", provider_keys=sandbox,
                property_ids=[p.id for p in props], user=sample_advisor)
    out = PE.payload(ev)
    assert out["synthetic"] and out["label"] == "SYNTHETIC"
    assert out["results"]["label"].startswith("SYNTHETIC")
    block = out["results"]["providers"][sandbox[0]]
    for k in ("match_rate", "phone_coverage", "mobile_coverage", "false_positive_rate",
              "cost_per_usable_contact_cents", "median_latency_ms"):
        assert k in block
    assert db.query(EvoSenseContactPoint).filter_by(organization_id=org).count() == before
    # a sandbox adapter never runs on a real property
    real_prop = props[0]
    real_prop.is_test = False
    db.flush()
    with pytest.raises(PE.EvaluationRefused):
        PE.run(db, org, name="x", provider_keys=sandbox, property_ids=[real_prop.id])
    # a property from another workspace is refused
    with pytest.raises(PE.EvaluationRefused):
        PE.run(db, "not-this-org", name="x", provider_keys=sandbox, property_ids=[props[1].id])


def test_22b_a_real_provider_needs_the_owners_exact_confirmation(hunted, stub_real):
    db, org, _ = hunted
    p = stub_real
    PV.config(db, org, p.key).enabled = True
    db.flush()
    prop = db.query(EvoSenseProperty).filter_by(organization_id=org).first()
    with pytest.raises(PE.EvaluationRefused) as exc:
        PE.run(db, org, name="x", provider_keys=[p.key], property_ids=[prop.id])
    assert PE.PAID_CONFIRMATION in str(exc.value)
    with pytest.raises(PE.EvaluationRefused):
        PE.run(db, org, name="x", provider_keys=[p.key], property_ids=[prop.id],
               confirm="%s 99" % PE.PAID_CONFIRMATION)
    assert _StubRealContact.calls == []                             # nothing was ever called
    assert db.query(EvoSenseProviderEvaluation).filter_by(organization_id=org).count() == 0


def test_22c_evaluation_routes_are_admin_only_and_tenant_scoped(client, auth_headers, org_b, hunted):
    db, org, _ = hunted
    prop = db.query(EvoSenseProperty).filter_by(organization_id=org).first()
    r = client.post("/wholesale/evosense/provider-evaluations", headers=auth_headers,
                    json={"name": "x", "provider_keys": ["sandbox_skiptrace"], "property_ids": [prop.id]})
    assert r.status_code == 403
    ev = EvoSenseProviderEvaluation(organization_id=org, name="A's eval", provider_keys="[]", sample="{}",
                                    synthetic=True, status="completed")
    db.add(ev)
    db.commit()
    _, hb = org_b
    assert client.get("/wholesale/evosense/provider-evaluations/%s" % ev.id, headers=hb).status_code == 404


# ── 23. Skip-trace candidates: provenance, and a mobile number is not consent ─

def test_23_a_found_mobile_is_a_candidate_with_provenance_and_never_consent(hunted, sample_advisor):
    from app.services.evosense import enrichment as EN
    db, org, s = hunted
    got = None
    for prop in (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org)
                 .order_by(EvoSenseProperty.opportunity_score.desc()).all()):
        if prop.street_address == "1805 Nolte Dr":
            continue
        owner = CT.primary_owner(db, prop)
        if owner is None:
            continue
        res = EN.run(db, prop, s, user=sample_advisor, approved=True)
        if res.get("outcome") == "found":
            got = (prop, owner)
            break
    assert got, "the sandbox should find at least one contact"
    prop, owner = got
    phones = (db.query(EvoSenseContactPoint)
              .filter_by(organization_id=org, owner_id=owner.id, kind="phone").all())
    assert phones
    for cp in phones:
        assert cp.trust_state in ("candidate", "trusted", "rejected")
        assert cp.looked_up_at is not None and cp.source
    mobile = next((cp for cp in phones if cp.line_type == "mobile"), phones[0])
    verdict = CE.sms(db, org, mobile.value, line_type=mobile.line_type)
    assert verdict["eligible"] is False and verdict["permitted"] is False
    assert verdict["permission_basis"] is None


# ── Economics: cost to find / contact / qualify; manual EvoSense comps ─────

def test_acquisition_cost_is_bucketed_from_the_ledger_only(hunted, sample_advisor):
    db, org, s = hunted
    prop = (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org)
            .order_by(EvoSenseProperty.opportunity_score.desc()).first())
    cost = EC.acquisition_cost(db, prop)
    assert set(cost["buckets"]) == set(EC.COST_BUCKETS)
    assert sum(cost["buckets"].values()) == cost["total_cents"]
    assert cost["cost_to_find_cents"] == cost["buckets"]["public_data"]
    assert cost["cost_to_qualification_cents"] is None               # never qualified: no figure


def test_an_evosense_manual_comp_set_feeds_the_same_engine(client, auth_headers, hunted):
    db, org, _ = hunted
    prop = (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org,
                                              EvoSenseProperty.square_feet.isnot(None)).first())
    base = {"sale_price": 280000, "sale_date": _ago(90), "source_reference": "Deed 2026-TEST",
            "square_feet": int(prop.square_feet),
            "bedrooms": float(prop.bedrooms) if prop.bedrooms is not None else None,
            "bathrooms": float(prop.bathrooms) if prop.bathrooms is not None else None,
            "year_built": prop.year_built, "property_type": prop.property_type,
            "distance_miles": 0.4, "sale_type": "arms_length"}
    r = client.post("/wholesale/evosense/properties/%s/comps" % prop.id, headers=auth_headers,
                    json=dict(base, street_address="1 Evo Comp"))
    body = ok(r)
    assert body["arv"]["value"] is None and body["arv"]["status"] == "insufficient"
    bad = client.post("/wholesale/evosense/properties/%s/comps" % prop.id, headers=auth_headers,
                      json=dict(base, street_address="x", source_reference=" "))
    assert bad.status_code == 422
    for n in (2, 3):
        body = ok(client.post("/wholesale/evosense/properties/%s/comps" % prop.id, headers=auth_headers,
                              json=dict(base, street_address="%s Evo Comp" % n,
                                        sale_price=280000 + n * 2000)))
    rows = db.query(WholesaleComp).filter_by(evosense_property_id=prop.id).all()
    assert all(r.verification_state == "manual" and r.provider_key is None for r in rows)
    assert body["arv"]["status"] == "estimated", body["arv"]
    assert body["arv"]["method"].startswith(arv_engine.VERSION) and body["arv"]["comp_count"] == 3
    pre = EC.preliminary(db, prop)
    assert pre["mao"] is None                                       # no accepted repair estimate


def test_value_of_information_skips_a_do_not_contact_owner(hunted):
    from app.services.evosense import enrichment as EN
    db, org, s = hunted
    prop = (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org)
            .order_by(EvoSenseProperty.opportunity_score.desc()).first())
    prop.contactability = "DO_NOT_CONTACT"
    db.flush()
    res = EN.run(db, prop, s)
    assert res["decision"] == C.D_SUPPRESSED
    assert "Do not contact" in res["reasons"][0]


def test_a_gated_mao_leaves_no_number_in_the_worked_steps(W):
    """Found in production verification: the deal room's worked offer still
    ended in '= maximum allowable offer $162,860' (repairs assumed zero) while
    the headline said not calculated. Not calculated means no number anywhere."""
    _, deal_id = W.deal()
    _three(W, deal_id)
    room = ok(W.get("/deals/%s" % deal_id))
    a = room["analysis"]
    assert a["max_allowable_offer"] is None and a["blocked"]
    last = [s for s in a["steps"] if s["label"].startswith("= maximum allowable offer")]
    assert all(s["value"] is None for s in last)
    assert a["warnings"][0].startswith("MAO NOT CALCULATED")
    assert not any("as if repairs were zero" in w for w in a["warnings"])
    # once the evidence is there, the number comes back
    ok(W.post("/deals/%s/repairs" % deal_id, {"status": "MANUAL_ESTIMATE", "amount": 30000}))
    a = ok(W.get("/deals/%s" % deal_id))["analysis"]
    assert a["max_allowable_offer"] is not None and not a.get("mao_blocked")


# ── Found in production verification ───────────────────────────────────────

FRONTEND_ROUTES = ("/wholesale/deals/", "/wholesale/evosense/property/", "/wholesale/evosense/inbox",
                   "/wholesale/evosense/controls", "/wholesale/evosense/strategies", "/wholesale/properties")


def test_every_command_center_link_is_a_real_screen():
    """Links pointed at /wholesale/evosense?tab=... (the EvoSense home, which has
    no tabs) and /wholesale/evosense/properties/<id> (an API path, not a page)."""
    import inspect
    import re
    from app.services import wholesale_command as WC
    src = inspect.getsource(WC)
    links = set(re.findall(r'"(/wholesale[^"%]*)', src))
    assert links
    for link in links:
        assert "?tab=" not in link and "/evosense/properties/" not in link, link
        assert link.startswith(FRONTEND_ROUTES) or link in ("/wholesale", "/wholesale/"), link


def test_a_test_records_lifecycle_does_not_claim_the_seller_asked_not_to_be_contacted():
    from types import SimpleNamespace
    from app.services import wholesale_command as WC
    life = WC.seller_lifecycle(lead=SimpleNamespace(status="new", is_test=True),
                               contactability="DO_NOT_CONTACT")
    assert life["stage"] == WC.DO_NOT_CONTACT and "test record" in life["why"]
    life = WC.seller_lifecycle(lead=SimpleNamespace(status="dnc", is_test=False))
    assert life["why"] == "Asked not to be contacted"


def test_manual_sources_do_not_claim_reachability_or_health(db_session, sample_org):
    manual = PV.PROVIDERS["manual"]
    row = CAPS.provider_capability(db_session, sample_org.id, manual, [C.CONTACT_ENRICHMENT])
    assert row["state"] == CAPS.S_MANUAL
    assert row["tenant"]["reachable"] is None and row["tenant"]["healthy"] is None
    assert row["operational"] is False or row["synthetic"] is False


def test_contactability_is_computed_on_read_for_a_property_never_rescored(client, auth_headers, hunted):
    db, org, _ = hunted
    prop = db.query(EvoSenseProperty).filter_by(organization_id=org).first()
    prop.contactability = None
    prop.contactability_detail = None
    db.commit()
    d = ok(client.get("/wholesale/evosense/properties/%s" % prop.id, headers=auth_headers))
    assert d["contactability"] and d["contactability"]["state"] and d["contactability"]["computed_on_read"]
    db.refresh(prop)
    assert prop.contactability_detail is None                      # read-only: nothing stored


def test_manual_sources_are_never_provider_problems(db_session, sample_org):
    """Production showed Manual entry, CSV import and two manual Dallas sources
    as DEGRADED 'provider problems' - they are never called, so never failing."""
    from app.services import wholesale_command as WC
    for key in ("manual", "csv_import"):
        if key in PV.PROVIDERS:
            PV.config(db_session, sample_org.id, key).enabled = True
    db_session.commit()
    body = WC.command_center(db_session, sample_org.id)
    assert not [p for p in body["provider_problems"]["items"]
                if p["provider"] in ("manual", "csv_import")]


def test_the_scheduler_backfills_contactability_without_calling_anyone(hunted, monkeypatch):
    from app.services.evosense import scheduler as SCH
    db, org, _ = hunted
    props = db.query(EvoSenseProperty).filter_by(organization_id=org).all()
    for p in props:
        p.contactability = p.contactability_detail = None
    db.commit()
    monkeypatch.setattr(PV.AcquisitionProvider, "enrich",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no provider call")))
    n = SCH.backfill_contactability(db, org_id=org)
    assert n == min(len(props), SCH.BACKFILL_BATCH)
    assert all(p.contactability for p in db.query(EvoSenseProperty).filter_by(organization_id=org).all()[:n])
    assert SCH.backfill_contactability(db, org_id=org) == max(0, len(props) - n)


def test_property_page_acquisition_cost_is_the_charged_ledger(client, auth_headers, hunted):
    """The EvoSense property page shows acquisition cost from the ledger only."""
    from app.models.evosense_models import EvoSenseCostEntry
    db, org, _ = hunted
    for prop in db.query(EvoSenseProperty).filter_by(organization_id=org).limit(5).all():
        d = ok(client.get("/wholesale/evosense/properties/%s" % prop.id, headers=auth_headers))
        cost = d["acquisition_cost"]
        charged = sum(r.total_cents or 0 for r in db.query(EvoSenseCostEntry).filter(
            EvoSenseCostEntry.organization_id == org, EvoSenseCostEntry.property_id == prop.id,
            EvoSenseCostEntry.status.in_(("charged", "failed_charged"))))
        assert cost["total_cents"] == charged == sum(cost["buckets"].values())
        assert cost["cost_to_qualification_cents"] is None
