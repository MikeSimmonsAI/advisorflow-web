"""Wholesale, end to end: public county records -> a deal a person can work.

    county tax roll + appraisal district + Census + city code case
      -> ONE property, signals with their sources, a versioned score
      -> owner of record; contact lookup stays OFF (no paid call, no charge)
      -> outreach refused (no cold-text sign-off) and nothing is sent
      -> a person adds the number the owner gave them (made-up 555 number)
      -> the owner's own tick on a permission link records consent
      -> the seller's words (typed in from the call) become facts + a hand-off
      -> a person acknowledges the hand-off and promotes it to a Wholesale deal

The records below have EXACTLY the shape the live adapters produce
(tarrant_tax_roll/2, tad_property_data/2, census_geocoder/1,
fw_code_violations/2 - copied from a production observation on 2026-10-19),
with a made-up address, owner, account and case numbers so no real owner sits
in this public repository.

To run the same journey on REAL records (no network, nothing sent), point
WHOLESALE_E2E_RECORDS at a JSON list of {"provider", "capability", "record"}
taken read-only from evosense_observations; the test asserts the same path and
prints what happened at each step.
"""
import json
import os
import re

import pytest

from app.models.evosense_models import (EvoSenseCostEntry, EvoSenseEngagement, EvoSenseFact,
                                        EvoSenseHandoff, EvoSenseObservation, EvoSenseProperty,
                                        EvoSenseScore, EvoSenseSignal, EvoSenseStrategy)
from app.models.sms_consent_models import SmsConsentRecord
from app.models.wholesale_models import WholesaleDeal, WholesaleProperty, WholesaleSellerProfile
from app.services.evosense import common as C
from app.services.evosense import enrichment as EN
from app.services.evosense import evaluate as EV
from app.services.evosense import ingest as IN
from app.services.evosense import providers as PV
from app.services.evosense import strategy as ST

STREET = "4410 FICTION GROVE DR"
OWNER_PHONE = "817-555-0164"          # fiction range


def county_records():
    """Tarrant tax roll, TAD, Census, Fort Worth code - adapter output shape."""
    return [
        {"provider": "tarrant_tax_roll", "capability": "TAX", "record": {
            "source_reference": "TCTAX:9990001:20260925", "street_address": STREET, "city": None,
            "zip_code": None, "state": "TX", "county": "Tarrant", "parcel_apn": "9990001",
            "property_type": None, "year_built": None, "deed_transfer_date": "1997-12-01",
            "owner": {"name": "SAMPLE ROBIN AND SAMPLE P", "name_truncated": True,
                      "mailing_street": "1 EXAMPLE CT 200", "mailing_city": "ARLINGTON",
                      "mailing_state": "VA", "mailing_zip": "22204"},
            "signals": [{"type": "TAX_DELINQUENT", "confidence": 90,
                         "value": "2025 taxes unpaid after 02/01/2026 ($2,794.64)",
                         "raw": "PRIOR_DUE=000000000.00;CUR_DUE=000002794.64;DELQ_DATE=02/01/2026",
                         "effective_at": "2026-02-01", "evidence_basis": "tax roll delinquency date"}],
            "evidence": {"account": "00009990001", "current_due": 2794.64, "delinquency_date": "02/01/2026"},
            "source_url": "https://www.tarrantcountytx.gov/content/dam/main/tax/tax-rolls/2026/TaxRoll20260925.zip",
            "adapter_version": "tarrant_tax_roll/2"}},
        {"provider": "tad", "capability": "ASSESSOR", "record": {
            "source_reference": "TAD:9990001:2026", "street_address": STREET, "city": "Fort Worth",
            "state": "TX", "zip_code": None, "county": "Tarrant", "parcel_apn": "9990001",
            "square_feet": 1054, "year_built": 1972, "deed_transfer_date": "1997-12-01",
            "owner": {"name": "ROBIN & PAT ETAL SAMPLE", "mailing_street": "1 EXAMPLE CT 200",
                      "mailing_city": "ARLINGTON", "mailing_state": "VA", "mailing_zip": "22204"},
            "signals": [],
            "appraisal": {"value": 127663, "land": 18750, "improvements": 108913, "year": 2026,
                          "district": "TAD", "basis": "TAD 2026 appraisal (property-tax value)"},
            "source_url": "https://www.tad.org/resources/data-downloads", "adapter_version": "tad_property_data/2"}},
        {"provider": "census_geocoder", "capability": "GEOCODING", "record": {
            "source_reference": "CENSUS:9990001:76105", "street_address": STREET, "city": "Fort Worth",
            "state": "TX", "zip_code": "76105", "county": "Tarrant", "parcel_apn": "9990001",
            "latitude": 32.7167, "longitude": -97.2449, "signals": [],
            "source_url": "https://geocoding.geo.census.gov/geocoder/", "adapter_version": "census_geocoder/1"}},
        {"provider": "fw_code_violations", "capability": "CODE_VIOLATION", "record": {
            "source_reference": "FWCODE:26-999001", "street_address": STREET, "city": "Fort Worth",
            "state": "TX", "zip_code": "76105", "county": "Tarrant", "parcel_apn": "9990001",
            "signals": [{"type": "CODE_VIOLATION", "confidence": 90,
                         "value": "Fort Worth case 26-999001: High Grass/Weeds (open, opened 09/22/2026)",
                         "ref": "FWCODE:26-999001", "effective_at": "2026-09-22",
                         "evidence_basis": "city case created date", "case_status": "open"},
                        {"type": "CODE_HISTORY", "confidence": 70,
                         "value": "Fort Worth case 25-999002: High Grass/Weeds (closed, opened 05/30/2025)",
                         "ref": "FWCODE:25-999002", "effective_at": "2025-05-30",
                         "evidence_basis": "city case created date", "case_status": "closed"}],
            "source_url": "https://data.fortworthtexas.gov/", "adapter_version": "fw_code_violations/2"}},
    ]


def _records():
    path = os.environ.get("WHOLESALE_E2E_RECORDS")
    if path:
        with open(path) as fh:
            return json.load(fh), True
    return county_records(), False


def _say(real, *parts):
    if real:
        print("  ->", *parts)


@pytest.fixture()
def strategy(db_session, sample_org):
    clean, problems = ST.validate(ST.starter_values("behind_on_taxes"))
    assert not problems, problems
    s = EvoSenseStrategy(organization_id=sample_org.id, is_test=False)
    ST.apply(s, clean)
    db_session.add(s)
    db_session.flush()
    ST.transition(s, "activate")
    db_session.commit()
    return s


def test_public_records_to_promoted_deal_without_spending_or_sending(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    recs, real = _records()

    # 1. Real-shaped public records in, one property out.
    for r in recs:
        outcome, prop = IN.ingest(db, org, PV.PROVIDERS[r["provider"]], r["capability"], r["record"],
                                  strategy_id=strategy.id)
        _say(real, r["provider"], outcome)
    db.commit()
    props = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org).all()
    assert len(props) == 1, "four sources, one canonical property"
    prop = props[0]
    assert not prop.is_test
    assert db.query(EvoSenseObservation).filter_by(property_id=prop.id).count() == len(recs)

    # 2. Signals scored, with sources; closed code history shown, not scored.
    EV.rescore(db, prop, strategy)
    db.commit()
    kinds = {s.signal_type for s in db.query(EvoSenseSignal).filter_by(property_id=prop.id, active=True)}
    assert {"TAX_DELINQUENT", "ABSENTEE_OWNER", "OUT_OF_STATE_OWNER", "LONG_OWNERSHIP",
            "CODE_VIOLATION"} <= kinds
    score = db.query(EvoSenseScore).filter_by(property_id=prop.id, is_current=True,
                                              score_type="property_opportunity").one()
    factors = json.loads(score.factors)
    assert any("shown, not scored" in f["label"] for f in factors)            # CODE_HISTORY
    assert prop.opportunity_score >= strategy.min_opportunity_score
    _say(real, "score", prop.opportunity_score, "status", prop.status, "-", prop.next_action)
    assert prop.status == C.S_HIGH and "contact" in (prop.next_action or "").lower()

    # 3. Owner known; contact lookup is the PAID step and it stays off.
    owner = EV.CT.primary_owner(db, prop)
    assert owner is not None and not owner.name_truncated, \
        "the appraisal district's full name replaces the tax roll's cut-off one"
    res = EN.run(db, prop, strategy)
    db.commit()
    _say(real, "contact lookup decision", res["decision"])
    assert res["decision"] == C.D_RETRY                     # waits for a provider; never charged
    assert db.query(EvoSenseCostEntry).filter_by(organization_id=org, status="charged").count() == 0
    assert prop.contact_confidence is None

    # 4. Automatic outreach cannot start: nothing sent.
    r = client.post("/wholesale/evosense/properties/%s/outreach" % prop.id, headers=admin_auth_headers)
    assert r.status_code == 200, r.text
    _say(real, "outreach", r.json().get("started"), r.json().get("blocks") or r.json().get("reason"))
    assert not r.json().get("started")

    # 5. A person reached the owner (door knock / call) and adds the number.
    r = client.post("/wholesale/evosense/properties/%s/contacts" % prop.id, headers=admin_auth_headers,
                    json={"kind": "phone", "value": OWNER_PHONE, "person_name": "Owner of record"})
    assert r.status_code == 200, r.text
    _say(real, "contact added by hand; status", r.json()["status"])

    # 6. The owner's OWN tick on a permission link is the only consent.
    r = client.post("/wholesale/evosense/properties/%s/permission-link" % prop.id,
                    headers=admin_auth_headers, json={"phone": OWNER_PHONE, "owner_name": "Owner"})
    assert r.status_code == 200, r.text
    assert db.query(SmsConsentRecord).count() == 0                              # a link grants nothing
    token = re.search(r"/permission/([A-Za-z0-9_-]+)$", r.json()["url"]).group(1)
    ok = client.post("/permission/%s" % token, data={"answer": "confirm", "agree": "yes"})
    assert ok.status_code == 200, ok.text
    assert db.query(SmsConsentRecord).filter_by(organization_id=org).count() == 1
    _say(real, "owner confirmed permission for that number")

    # 7. What the seller said on the call becomes facts, intent and a hand-off.
    words = ("Yes, I'd consider selling. Nobody lives there since my mom passed last year and it "
             "needs a new roof. I'd take around 140,000 and could close in 30 days.")
    r = client.post("/wholesale/evosense/properties/%s/reply" % prop.id, headers=admin_auth_headers,
                    json={"text": words, "delivery": "manual_entry"})
    assert r.status_code == 200, r.text
    db.expire_all()
    prop = db.query(EvoSenseProperty).filter_by(id=prop.id).one()
    facts = {f.fact_type: f.value for f in db.query(EvoSenseFact).filter_by(property_id=prop.id)}
    _say(real, "reply outcome", r.json().get("outcome"), "intent", prop.seller_intent, "facts", facts)
    assert facts.get("willing_to_sell") and facts.get("asking_price") == "140000"
    h = db.query(EvoSenseHandoff).filter_by(property_id=prop.id).one()
    assert prop.status == C.S_NEEDS_YOU

    # 8. A person takes the hand-off and promotes it.
    r = client.post("/wholesale/evosense/handoffs/%s" % h.id, headers=admin_auth_headers,
                    json={"status": "acknowledged", "note": "Calling back tomorrow"})
    assert r.status_code == 200, r.text
    r = client.post("/wholesale/evosense/properties/%s/promote" % prop.id, headers=admin_auth_headers,
                    json={"note": "Owner confirmed by phone"})
    assert r.status_code == 200, r.text
    out = r.json()
    wp = db.query(WholesaleProperty).filter_by(id=out["property_id"]).one()
    deal = db.query(WholesaleDeal).filter_by(id=out["deal_id"]).one()
    prof = db.query(WholesaleSellerProfile).filter_by(property_id=wp.id).one()
    _say(real, "deal", deal.id, "stage", deal.stage, "asking", prof.asking_price)
    assert wp.acquisition_source == "evosense" and not wp.is_test
    assert float(prof.asking_price) == 140000
    # No ARV was invented: there are no verified comparable sales.
    assert deal.arv is None

    # Nothing was texted, emailed or charged at any step.
    assert db.query(EvoSenseEngagement).filter_by(property_id=prop.id).count() >= 1
    from app.models.evosense_models import EvoSenseMessage
    assert db.query(EvoSenseMessage).filter_by(property_id=prop.id, direction="outbound").count() == 0
    assert db.query(EvoSenseCostEntry).filter_by(organization_id=org, status="charged").count() == 0


# ── the reader hears how sellers actually say it ────────────────────────────

@pytest.mark.parametrize("text,present,absent", [
    ("Nobody lives there since my mom passed last year and it needs a new roof. "
     "I'd take around 140,000 and could close in 30 days.",
     {"estate_context", "condition", "wants_quick_close", "occupancy", "asking_price"}, set()),
    ("My dad died in 2023 and the foundation has issues", {"estate_context"}, set()),
    ("The foundation issues are bad, roof leaks too", {"condition"}, set()),
    ("We can close within two weeks", {"wants_quick_close"}, set()),
    # not a quick close, not a death in the family, not a repair
    ("We could close in 20 weeks if the price is right", set(), {"wants_quick_close"}),
    ("The house passed inspection last year", set(), {"estate_context"}),
    ("The roof is new and the foundation is solid", set(), {"condition"}),
])
def test_reader_hears_common_seller_phrasings(text, present, absent):
    from app.services.evosense import conversation as CV
    kinds = {f["fact_type"] for f in CV.classify(text)["facts"]}
    assert present <= kinds, (present - kinds)
    assert not (absent & kinds), (absent & kinds)


def test_cut_off_owner_name_is_completed_only_by_the_same_owner(db_session, sample_org, strategy):
    db, org = db_session, sample_org.id
    tax, tad = county_records()[0], county_records()[1]
    IN.ingest(db, org, PV.PROVIDERS["tarrant_tax_roll"], "TAX", tax["record"])
    prop = db.query(EvoSenseProperty).one()
    assert EV.CT.primary_owner(db, prop).name_truncated
    # A DIFFERENT mailing address is not proof it is the same owner: stays cut, becomes a conflict.
    other = json.loads(json.dumps(tad["record"]))
    other["source_reference"] = "TAD:9990001:2025"
    other["owner"]["mailing_street"] = "9 SOMEWHERE ELSE"
    IN.ingest(db, org, PV.PROVIDERS["tad"], "ASSESSOR", other)
    db.commit()
    first = EV.CT.primary_owner(db, prop)
    assert first.name_truncated and first.display_name == "SAMPLE ROBIN AND SAMPLE P"
    # The same owner (same mailing, shared name word) with the whole name completes it.
    IN.ingest(db, org, PV.PROVIDERS["tad"], "ASSESSOR", tad["record"])
    db.commit()
    db.refresh(first)
    assert not first.name_truncated and first.display_name == "ROBIN & PAT ETAL SAMPLE"
    # ...and a later cut-off spelling never undoes it.
    again = json.loads(json.dumps(tax["record"]))
    again["source_reference"] = "TCTAX:9990001:20261002"
    IN.ingest(db, org, PV.PROVIDERS["tarrant_tax_roll"], "TAX", again)
    db.commit()
    db.refresh(first)
    assert not first.name_truncated and first.display_name == "ROBIN & PAT ETAL SAMPLE"
