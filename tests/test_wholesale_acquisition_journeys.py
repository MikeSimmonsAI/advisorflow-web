"""Wholesale acquisition journeys: what a person sees, start to finish, for the
seller situations that actually come up - SYNTHETIC records, ZERO outbound.

Every journey starts from county records in the live adapters' shape (made-up
addresses, owners and 555 numbers), goes through the real ingest, scoring,
reply reader, hand-off and promotion code, and ends in a deal or a clean stop.

    success: inherited house, house needing repairs, seller on a deadline,
             deal with a matching cash buyer, deal with no buyer match,
             one owner holding two houses
    rejection / stop: not interested, opt-out, wrong person, two prices in one
             reply (a person confirms), asking above every buyer's limit

Each test ends by checking nothing was texted, emailed or charged. These are
tests on fiction, never live transactions.
"""
import json

import pytest

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseCostEntry, EvoSenseFact,
                                        EvoSenseHandoff, EvoSenseMessage, EvoSenseOwner,
                                        EvoSenseOwnership, EvoSenseProperty, EvoSenseStrategy)
from app.models.wholesale_models import (WholesaleBuyBox, WholesaleBuyer, WholesaleDeal,
                                         WholesaleSellerProfile)
from app.services.evosense import common as C
from app.services.evosense import evaluate as EV
from app.services.evosense import ingest as IN
from app.services.evosense import providers as PV
from app.services.evosense import strategy as ST


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


def _records(n, street, owner="SAMPLE ROBIN", mailing=("1 EXAMPLE CT", "ARLINGTON", "VA", "22204")):
    apn = "99900%02d" % n
    ms, mc, mst, mz = mailing
    own = {"name": owner, "mailing_street": ms, "mailing_city": mc, "mailing_state": mst, "mailing_zip": mz}
    return [
        ("tarrant_tax_roll", "TAX", {
            "source_reference": "TCTAX:%s:20260925" % apn, "street_address": street, "city": None,
            "zip_code": None, "state": "TX", "county": "Tarrant", "parcel_apn": apn,
            "deed_transfer_date": "1997-12-01", "owner": dict(own),
            "signals": [{"type": "TAX_DELINQUENT", "confidence": 90,
                         "value": "2025 taxes unpaid after 02/01/2026 ($2,794.64)",
                         "raw": "PRIOR_DUE=000000000.00;CUR_DUE=000002794.64;DELQ_DATE=02/01/2026",
                         "effective_at": "2026-02-01", "evidence_basis": "tax roll delinquency date"}],
            "evidence": {"account": "000" + apn, "current_due": 2794.64},
            "source_url": "https://www.tarrantcountytx.gov/", "adapter_version": "tarrant_tax_roll/2"}),
        ("tad", "ASSESSOR", {
            "source_reference": "TAD:%s:2026" % apn, "street_address": street, "city": "Fort Worth",
            "state": "TX", "zip_code": "76105", "county": "Tarrant", "parcel_apn": apn,
            "square_feet": 1054, "year_built": 1972, "deed_transfer_date": "1997-12-01",
            "property_type": "single_family", "owner": dict(own), "signals": [],
            "appraisal": {"value": 127663, "land": 18750, "improvements": 108913, "year": 2026,
                          "district": "TAD", "basis": "TAD 2026 appraisal (property-tax value)"},
            "source_url": "https://www.tad.org/", "adapter_version": "tad_property_data/2"}),
    ]


def _property(db, org, strategy, n, street, **kw):
    for provider, cap, rec in _records(n, street, **kw):
        IN.ingest(db, org, PV.PROVIDERS[provider], cap, rec, strategy_id=strategy.id)
    db.commit()
    prop = (db.query(EvoSenseProperty)
            .filter(EvoSenseProperty.organization_id == org, EvoSenseProperty.parcel_apn == "99900%02d" % n)
            .one())
    EV.rescore(db, prop, strategy)
    db.commit()
    return prop


def _talk(client, h, prop, phone, words):
    """A person reached the owner and types in what they said (no message is sent)."""
    r = client.post("/wholesale/evosense/properties/%s/contacts" % prop.id, headers=h,
                    json={"kind": "phone", "value": phone, "person_name": "Owner of record"})
    assert r.status_code == 200, r.text
    r = client.post("/wholesale/evosense/properties/%s/reply" % prop.id, headers=h,
                    json={"text": words, "delivery": "manual_entry"})
    assert r.status_code == 200, r.text
    return r.json()


def _facts(db, prop):
    return {f.fact_type: f for f in db.query(EvoSenseFact).filter_by(property_id=prop.id, superseded=False)}


def _handoff_codes(db, prop):
    h = db.query(EvoSenseHandoff).filter_by(property_id=prop.id).first()
    return {r["code"] for r in json.loads(h.reasons)} if h else set()


def _promote(client, h, prop):
    r = client.post("/wholesale/evosense/properties/%s/promote" % prop.id, headers=h,
                    json={"note": "Owner confirmed by phone (synthetic journey)"})
    assert r.status_code == 200, r.text
    return r.json()


def _nothing_sent_or_charged(db, org):
    assert db.query(EvoSenseMessage).filter_by(organization_id=org, direction="outbound").count() == 0
    assert db.query(EvoSenseCostEntry).filter_by(organization_id=org, status="charged").count() == 0


def _buyer(db, org, name, **box):
    b = WholesaleBuyer(organization_id=org, company_name=name, contact_name=name,
                       email="%s@example.com" % name.lower().replace(" ", ""), source="synthetic")
    db.add(b)
    db.flush()
    db.add(WholesaleBuyBox(organization_id=org, buyer_id=b.id, label="main",
                           **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in box.items()}))
    db.commit()
    return b


# ── success journeys ────────────────────────────────────────────────────────

def test_inherited_house_becomes_a_deal_with_decision_makers_flagged(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    prop = _property(db, org, strategy, 1, "101 FICTION ESTATE LN")
    out = _talk(client, admin_auth_headers, prop, "817-555-0101",
                "My father passed in March and the house is in probate. My sister and I both want "
                "to sell it. We'd take around 120,000.")
    assert out["outcome"] == C.O_INTERESTED
    facts = _facts(db, prop)
    assert facts["estate_context"].value.startswith("inherited")
    assert facts["asking_price"].value == "120000"
    assert {"ESTATE", "PRICE_STATED"} <= _handoff_codes(db, prop)
    deal = db.query(WholesaleDeal).filter_by(id=_promote(client, admin_auth_headers, prop)["deal_id"]).one()
    prof = db.query(WholesaleSellerProfile).filter_by(property_id=deal.property_id).one()
    assert float(prof.asking_price) == 120000 and deal.arv is None
    _nothing_sent_or_charged(db, org)


def test_house_needing_repairs_carries_the_sellers_condition_not_an_invented_repair_number(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    prop = _property(db, org, strategy, 2, "102 FICTION REPAIR RD")
    out = _talk(client, admin_auth_headers, prop, "817-555-0102",
                "The foundation is cracked and the roof leaks. I'd sell it as-is for 95,000.")
    assert out["outcome"] == C.O_INTERESTED
    facts = _facts(db, prop)
    assert facts["condition"].value == "poor" and facts["asking_price"].value == "95000"
    deal = db.query(WholesaleDeal).filter_by(id=_promote(client, admin_auth_headers, prop)["deal_id"]).one()
    assert deal.repair_estimate is None and deal.arv is None        # no number nobody measured
    _nothing_sent_or_charged(db, org)


def test_seller_on_a_deadline_is_handed_to_a_person_as_wanting_an_offer(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    prop = _property(db, org, strategy, 3, "103 FICTION DEADLINE DR")
    out = _talk(client, admin_auth_headers, prop, "817-555-0103",
                "I'm relocating for work and need to close in two weeks. Make me an offer.")
    assert out["outcome"] == C.O_WANTS_OFFER
    assert "wants_quick_close" in _facts(db, prop)
    assert "WANTS_OFFER" in _handoff_codes(db, prop)
    db.expire_all()
    assert db.query(EvoSenseProperty).filter_by(id=prop.id).one().status == C.S_NEEDS_YOU
    _nothing_sent_or_charged(db, org)


def test_deal_with_a_matching_buyer_ranks_them_and_sends_nothing(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    prop = _property(db, org, strategy, 4, "104 FICTION MATCH ST")
    _talk(client, admin_auth_headers, prop, "817-555-0104", "Yes I'd sell. I'd take around 110,000.")
    deal = db.query(WholesaleDeal).filter_by(id=_promote(client, admin_auth_headers, prop)["deal_id"]).one()
    fits = _buyer(db, org, "Fiction Flips", counties=["Tarrant"], states=["TX"], min_price=80000,
                  max_price=160000, property_types=["single_family"])
    _buyer(db, org, "Elsewhere Homes", states=["OK"], min_price=80000, max_price=160000)
    deal.buyer_price = 125000                       # the disposition price a person set
    db.commit()
    r = client.post("/wholesale/deals/%s/match-buyers" % deal.id, headers=admin_auth_headers)
    assert r.status_code == 200, r.text
    ranked = r.json()["matches"]
    assert ranked[0]["buyer_id"] == fits.id and not ranked[0]["disqualified"]
    assert any(m["disqualified"] for m in ranked if m["buyer_id"] != fits.id)
    _nothing_sent_or_charged(db, org)


def test_deal_with_no_buyer_match_stays_a_deal_and_says_why(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    prop = _property(db, org, strategy, 5, "105 FICTION NOMATCH AVE")
    _talk(client, admin_auth_headers, prop, "817-555-0105", "Yes I'd sell. I'd take around 110,000.")
    deal = db.query(WholesaleDeal).filter_by(id=_promote(client, admin_auth_headers, prop)["deal_id"]).one()
    _buyer(db, org, "Oklahoma Only", states=["OK"], min_price=50000, max_price=300000)
    deal.buyer_price = 125000
    db.commit()
    r = client.post("/wholesale/deals/%s/match-buyers" % deal.id, headers=admin_auth_headers)
    assert r.status_code == 200, r.text
    ranked = r.json()["matches"]
    assert ranked and all(m["disqualified"] for m in ranked)
    assert any("outside" in (m["disqualified_reason"] or "").lower() or "geograph" in
               (m["disqualified_reason"] or "").lower() for m in ranked)
    assert db.query(WholesaleDeal).filter_by(id=deal.id).count() == 1
    _nothing_sent_or_charged(db, org)


def test_one_owner_with_two_houses_is_one_owner_and_a_number_is_not_copied_across(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    a = _property(db, org, strategy, 6, "106 FICTION TWIN CT", owner="SAMPLE LANDLORD")
    b = _property(db, org, strategy, 7, "107 FICTION TWIN CT", owner="SAMPLE LANDLORD")
    owners = {o.owner_id for o in db.query(EvoSenseOwnership).filter(
        EvoSenseOwnership.property_id.in_((a.id, b.id)), EvoSenseOwnership.is_current.is_(True))}
    assert len(owners) == 1, "same name + same mailing address = one owner across both houses"
    assert db.query(EvoSenseOwner).filter_by(organization_id=org, display_name="SAMPLE LANDLORD").count() == 1
    _talk(client, admin_auth_headers, a, "817-555-0106", "Yes, I'd consider selling this one.")
    # The second house was not part of that conversation: no reply, no hand-off on it.
    assert db.query(EvoSenseHandoff).filter_by(property_id=b.id).count() == 0
    assert db.query(EvoSenseFact).filter_by(property_id=b.id).count() == 0
    _nothing_sent_or_charged(db, org)


# ── rejection / stop journeys ───────────────────────────────────────────────

def test_not_interested_stops_cleanly_with_nothing_handed_off(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    prop = _property(db, org, strategy, 8, "108 FICTION NOPE LN")
    out = _talk(client, admin_auth_headers, prop, "817-555-0108", "No thanks, I'm not interested in selling.")
    assert out["outcome"] == C.O_HARD_NO
    assert _handoff_codes(db, prop) == set()
    assert "asking_price" not in _facts(db, prop)
    _nothing_sent_or_charged(db, org)


def test_opt_out_marks_the_number_and_is_never_handed_off(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    prop = _property(db, org, strategy, 9, "109 FICTION STOP ST")
    out = _talk(client, admin_auth_headers, prop, "817-555-0109", "STOP")
    assert out["outcome"] == C.O_DNC
    cp = db.query(EvoSenseContactPoint).filter_by(organization_id=org, value="+18175550109").one()
    assert cp.status == "opted_out"
    assert _handoff_codes(db, prop) == set()
    _nothing_sent_or_charged(db, org)


def test_wrong_person_marks_the_number_wrong_party(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    prop = _property(db, org, strategy, 10, "110 FICTION WRONG WAY")
    out = _talk(client, admin_auth_headers, prop, "817-555-0110", "Wrong number, I don't own any house.")
    assert out["outcome"] == C.O_WRONG_PERSON
    cp = db.query(EvoSenseContactPoint).filter_by(organization_id=org, value="+18175550110").one()
    assert cp.status == "wrong_party"
    _nothing_sent_or_charged(db, org)


def test_two_prices_in_one_reply_keeps_the_correction_and_asks_a_person_to_confirm(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    prop = _property(db, org, strategy, 11, "111 FICTION MIXED SIGNAL RD")
    _talk(client, admin_auth_headers, prop, "817-555-0111",
          "I'd take 150,000. Actually no, I need at least 180,000 for it.")
    f = _facts(db, prop)["asking_price"]
    assert f.value == "180000" and "150,000" in f.quote and "confirm" in f.quote
    assert {"PRICE_STATED", "PRICE_UNCLEAR"} <= _handoff_codes(db, prop)
    _nothing_sent_or_charged(db, org)


def test_asking_above_every_buyers_limit_is_shown_as_a_price_miss_not_hidden(
        client, db_session, sample_org, strategy, admin_auth_headers):
    db, org = db_session, sample_org.id
    prop = _property(db, org, strategy, 12, "112 FICTION HIGH HOPES DR")
    _talk(client, admin_auth_headers, prop, "817-555-0112",
          "I'd sell, but I won't take less than 250k. That's my bottom line is 250k.")
    assert _facts(db, prop)["asking_price"].value == "250000"
    deal = db.query(WholesaleDeal).filter_by(id=_promote(client, admin_auth_headers, prop)["deal_id"]).one()
    _buyer(db, org, "Budget Buyer", counties=["Tarrant"], states=["TX"], max_price=160000)
    deal.buyer_price = 260000
    db.commit()
    ranked = client.post("/wholesale/deals/%s/match-buyers" % deal.id, headers=admin_auth_headers).json()["matches"]
    assert ranked and all(m["disqualified"] for m in ranked)
    assert "above" in json.dumps(ranked[0]["factors"])
    _nothing_sent_or_charged(db, org)


# ── the reader does not turn durations and phone numbers into prices ─────────

@pytest.mark.parametrize("text", [
    "We've owned it for 30 years and I'm ready to sell.",
    "I'd sell, my mom lived there for 45 years.",
    "Yes I would sell, it's been empty for about 18 months.",
    "I'd sell, I can be out in about 60 days.",
    "Yes, call me at 817 555 0164.",
    "Yes, it has 3 bedrooms and I'd sell.",
])
def test_durations_sizes_and_phone_numbers_are_not_asking_prices(text):
    from app.services.evosense import conversation as CV
    r = CV.classify(text)
    assert not [f for f in r["facts"] if f["fact_type"] == "asking_price"], r["facts"]


@pytest.mark.parametrize("text,price", [
    ("I'd take 150,000.", "150000"),
    ("I'd sell for 150k, it would take 30 days to move out.", "150000"),
    ("Nobody lives there and I'd take around 140,000 and could close in 30 days.", "140000"),
    ("I need at least 180,000.", "180000"),
])
def test_how_sellers_say_a_price_is_read(text, price):
    from app.services.evosense import conversation as CV
    r = CV.classify(text)
    got = [f["value"] for f in r["facts"] if f["fact_type"] == "asking_price"]
    assert got == [price] and not r["price_conflict"]
