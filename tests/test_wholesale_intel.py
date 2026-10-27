"""SHARED INTELLIGENCE (#415 sections 16-19): fact verification, the
Wholesale / Investment switch, the Deal Opportunity Report and provider
readiness. Practice records only; nothing calls a paid provider."""
import json
from datetime import datetime, timedelta

from app.models.evosense_models import EvoSenseObservation, EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty
from app.services import wholesale_intel as INTEL
from tests.test_deal_room import _published_deal, ok

I = "/wholesale/intel"
NOW = datetime(2026, 10, 27, 12, 0)


def src(source, value, rank="official", days=10):
    return {"source": source, "rank": rank, "value": value, "source_date": NOW - timedelta(days=days),
            "retrieved_at": NOW - timedelta(days=days)}


def test_status_rules():
    assert INTEL.status_for("owner", [], NOW)["status"] == "UNKNOWN"
    assert INTEL.status_for("owner", [], NOW)["next"]
    assert INTEL.status_for("owner", [src("dcad", "PAT DOE")], NOW)["status"] == "VERIFIED"
    # same value from two official sources (formatting differences ignored) -> verified
    assert INTEL.status_for("address", [src("dcad", "1 Main St"), src("tad", "1 MAIN STREET")], NOW)["status"] == "VERIFIED"
    c = INTEL.status_for("owner", [src("dcad", "PAT DOE"), src("tad", "KIM ROE")], NOW)
    assert c["status"] == "CONFLICTED" and "dcad" in c["why"] and "tad" in c["why"]
    assert INTEL.status_for("tax", [src("dallas_trw", "1200", days=200)], NOW)["status"] == "STALE"
    assert INTEL.status_for("owner", [src("txgio_roll", "PAT DOE", rank="statewide")], NOW)["status"] == "LIKELY"
    assert INTEL.status_for("owner", [src("csv_import", "PAT DOE", rank="claim")], NOW)["status"] == "UNVERIFIED"
    # a weaker source never outranks or overturns a verified official value
    mixed = INTEL.status_for("owner", [src("dcad", "PAT DOE"), src("csv_import", "SOMEONE ELSE", rank="claim")], NOW)
    assert mixed["status"] == "VERIFIED"


def _evo(db, org, *, deal_id=None):
    p = EvoSenseProperty(organization_id=org, street_address="12 Verify Ln", city="Dallas", state="TX",
                         zip_code="75216", county="Dallas", property_type="single_family", is_test=True,
                         status="new", square_feet=1400, year_built=1962, promoted_deal_id=deal_id)
    db.add(p)
    db.flush()
    o = EvoSenseOwner(organization_id=org, display_name="PAT PRACTICE", owner_type="individual", is_test=True,
                      mailing_street="9 Mail St")
    db.add(o)
    db.flush()
    db.add(EvoSenseOwnership(organization_id=org, property_id=p.id, owner_id=o.id, is_current=True, source="dcad"))
    now = datetime.utcnow()
    db.add(EvoSenseObservation(organization_id=org, property_id=p.id, provider_key="dcad", connector_kind="real",
                               capability="ASSESSOR", source_reference="dcad:1", is_test=True,
                               observed_at=now - timedelta(days=5), last_seen_at=now - timedelta(days=5),
                               source_updated_at=now - timedelta(days=30),
                               payload=json.dumps({"owner": {"name": "PAT PRACTICE", "mailing_street": "9 Mail St"},
                                                   "square_feet": 1400, "year_built": 1962,
                                                   "parcel_apn": "00000123"})))
    db.add(EvoSenseObservation(organization_id=org, property_id=p.id, provider_key="tad", connector_kind="real",
                               capability="ASSESSOR", source_reference="tad:1", is_test=True,
                               observed_at=now - timedelta(days=3), last_seen_at=now - timedelta(days=3),
                               payload=json.dumps({"year_built": 1965})))
    db.add(EvoSenseObservation(organization_id=org, property_id=p.id, provider_key="csv_import",
                               connector_kind="import", capability="PROPERTY_SEARCH", source_reference="csv:1",
                               is_test=True, payload=json.dumps({"square_feet": 2000, "bedrooms": 3})))
    db.commit()
    return p


def test_property_verification_api(client, auth_headers, db_session, sample_org):
    p = _evo(db_session, sample_org.id)
    v = ok(client.get(I + "/property/%s/verification" % p.id, headers=auth_headers))
    f = {x["key"]: x for x in v["facts"]}
    assert f["owner_name"]["status"] == "VERIFIED" and f["owner_name"]["value_on_record"] == "PAT PRACTICE"
    assert f["owner_name"]["sources"][0]["source_date"] and f["owner_name"]["sources"][0]["retrieved_at"]
    assert f["square_feet"]["status"] == "VERIFIED"           # the import's 2000 does not overturn the record
    assert any(s["value"] == 2000 and s["rank"] == "claim" for s in f["square_feet"]["sources"])
    assert f["year_built"]["status"] == "CONFLICTED"          # dcad 1962 vs tad 1965
    assert f["bedrooms"]["status"] == "UNVERIFIED"
    assert f["bathrooms"]["status"] == "UNKNOWN" and f["bathrooms"]["next"]
    assert v["debt"]["status"] == "UNKNOWN" and "approval" in v["debt"]["next"]
    # another workspace's id is a 404
    assert client.get(I + "/property/nope/verification", headers=auth_headers).status_code == 404


def test_underwriting_switch_and_reports(client, auth_headers, admin_auth_headers, db_session, sample_org):
    H, A = auth_headers, admin_auth_headers
    deal_id = _published_deal(client, H, A, db_session, street="44 Report Rd")
    p = _evo(db_session, sample_org.id, deal_id=deal_id)
    w = ok(client.get(I + "/deal/%s/underwriting?strategy=wholesale" % deal_id, headers=H))
    assert "wholesale" in w and "investment" not in w
    spread = [l for l in w["wholesale"]["lines"] if l["label"] == "Estimated assignment spread"][0]
    lines = {l["label"]: l["value"] for l in w["wholesale"]["lines"]}
    if lines["Acquisition (contract) price"] is None or lines["Investor resale price"] is None:
        assert spread["value"] is None and "UNKNOWN" in spread["basis"]   # never an invented spread
    else:
        assert spread["value"] == (lines["Investor resale price"] - lines["Acquisition (contract) price"]
                                   - (lines["Transaction costs on record"] or 0))
    inv = ok(client.get(I + "/evosense/%s/underwriting?strategy=investment&purchase_price=150000&interest_rate=7"
                            "&ltv=75&amortization_years=30" % p.id, headers=H))
    assert "investment" in inv and "wholesale" not in inv and "SCENARIO" in inv["investment"]["note"]
    both = ok(client.get(I + "/evosense/%s/underwriting" % p.id, headers=H))
    assert "wholesale" in both and "investment" in both
    assert client.get(I + "/evosense/%s/underwriting?strategy=flip" % p.id, headers=H).status_code == 400
    assert client.get(I + "/evosense/%s/underwriting?ltv=120" % p.id, headers=H).status_code == 400

    rep = ok(client.get(I + "/evosense/%s/report" % p.id, headers=H))
    for k in ("property_summary", "distress_signals", "evidence", "ownership", "financial", "wholesale_analysis",
              "investment_analysis", "assumptions", "risks_and_missing", "buyer_matches", "next_action"):
        assert k in rep, k
    assert rep["audience"] == "internal" and any("Year built" in r for r in rep["risks_and_missing"])
    html = client.get(I + "/evosense/%s/report?format=html" % p.id, headers=H)
    assert html.status_code == 200 and "Deal Opportunity Report" in html.text and "Risks and missing" in html.text

    safe = ok(client.get(I + "/deal/%s/investor-report" % deal_id, headers=H))
    blob = json.dumps(safe)
    assert safe["audience"] == "investor" and "PAT PRACTICE" not in blob and "seller" not in blob.lower()
    assert "not an offer" in safe["notice"]
    sh = client.get(I + "/deal/%s/investor-report?format=html" % deal_id, headers=H)
    assert sh.status_code == 200 and "44 Report Rd" in sh.text and "PAT PRACTICE" not in sh.text


def test_investor_report_requires_published_package(client, auth_headers, db_session):
    from tests.test_wholesale_deal_package import _deal
    deal_id = _deal(client, auth_headers, db_session, street="7 Unpublished Way")
    assert client.get(I + "/deal/%s/investor-report" % deal_id, headers=auth_headers).status_code == 409


def test_buyer_verification_and_providers(client, auth_headers):
    b = ok(client.post("/wholesale/buyers", headers=auth_headers, json={
        "company_name": "Verify Practice LLC", "email": "verify@practice.invalid", "is_test": True}))
    v = ok(client.get(I + "/buyer/%s/verification" % b["id"], headers=auth_headers))
    f = {x["key"]: x["status"] for x in v["facts"]}
    assert f["purchases"] == "UNKNOWN" and f["funding"] == "UNKNOWN" and f["buy_box"] == "UNKNOWN"
    assert f["contact"] == "UNVERIFIED"
    pr = ok(client.get(I + "/providers", headers=auth_headers))
    keys = {p["key"]: p for p in pr["providers"]}
    assert {"attom", "propstream", "tracerfy", "reonomy", "trepp"} <= set(keys)
    assert keys["propstream"]["state"].startswith("IMPORT ONLY")
    assert keys["reonomy"]["state"].startswith(("NOT BUILT", "NOT CONNECTED"))
    assert "Nothing is activated" in pr["note"]
    assert "KEY" not in json.dumps(pr).replace("KEY PRESENT", "").replace("_API_KEY", "")
