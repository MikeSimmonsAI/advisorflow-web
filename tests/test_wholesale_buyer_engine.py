"""BUYER INTELLIGENCE ENGINE (#413) - permanent tests.

Synthetic records only (example.test / .invalid addresses, 555-01xx numbers,
"SYNTH" names). Nothing is sent, nothing is bought, no real investor is touched.
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app.models.wholesale_buyer_engine_models import (CLAIMED_PURCHASE, RECORDED_TITLE_TRANSFER,
                                                      WholesaleBuyerAcquisition, WholesaleBuyerEntity)
from app.models.wholesale_models import WholesaleBuyBox, WholesaleBuyer
from app.services import wholesale_buyer_engine as ENG
from app.services import wholesale_buyer_evidence as EVD
from app.services import wholesale_buyer_finder as finder
from app.services import wholesale_buyer_qualify as Q
from app.services import wholesale_matching as M

BI = "/wholesale/buyer-intel"
TODAY = date(2026, 10, 27)


def ok(r, what=""):
    assert r.status_code in (200, 201), "%s -> %s %s" % (what or r.url, r.status_code, r.text[:500])
    return r.json()


def box(**kw):
    base = dict(is_active=True, source="stated", confirmed_at=None, criteria=None, property_types=None,
                strategies=None, states=None, counties=None, cities=None, zips=None, markets=None,
                min_price=None, max_price=None, min_beds=None, max_beds=None, min_baths=None, min_sqft=None,
                max_sqft=None, min_year_built=None, max_year_built=None, rehab_tolerance=None, min_spread=None,
                min_acres=None, max_acres=None, min_units=None, max_units=None, min_building_sqft=None,
                max_building_sqft=None, min_cap_rate=None, value_add=None, id="bx", label="x")
    base.update(kw)
    return NS(**base)


def buyer(**kw):
    base = dict(id="b1", source=None, source_detail=None, proof_of_funds_on_file=False, proof_of_funds_expires=None,
                cash_verified=False, past_deals_count=0, phone=None, email=None, do_not_contact=False,
                do_not_contact_reason=None, is_active=True, identity_status=None, linked_to_buyer_id=None,
                contact_verified_at=None, contact_verified_how=None, contact_bad_at=None, decision_maker_name=None,
                decision_maker_confirmed=None, stated_buying_status=None, stated_buying_at=None,
                stated_budget_min=None, stated_budget_max=None, stated_funding=None, stated_close_days=None,
                stated_capacity=None, financing_preference=None, typical_close_days=None, file_status=None,
                purchase_evidence=None, company_name="SYNTH Co", contact_name=None, qualification_status=None)
    base.update(kw)
    return NS(**base)


def acq(cls=RECORDED_TITLE_TRANSFER, days_ago=100, kind="single_family"):
    return NS(evidence_class=cls, transfer_date=TODAY - timedelta(days=days_ago), property_kind=kind)


def deal(**kw):
    base = dict(arv=None, repair_estimate=None, buyer_price=None, contract_price=None, proposed_offer=None)
    base.update(kw)
    return NS(**base)


def prop(**kw):
    base = dict(zip_code="75216", city="Dallas", county="Dallas", state="TX", property_type=None,
                property_category=None, property_subtype=None, bedrooms=None, bathrooms=None, square_feet=None,
                year_built=None, acreage=None, unit_count=None, building_sqft=None, type_facts=None)
    base.update(kw)
    return NS(**base)


# ── segmentation ────────────────────────────────────────────────────────────

def test_single_family_segmentation_by_strategy_and_price():
    s = ENG.box_segments(box(property_types='["single_family"]', strategies='["flip","rental"]',
                             max_price=200000))
    assert set(s) == {"single_family"} and {"flip", "buy_hold", "entry_level"} <= s["single_family"]
    lux = ENG.box_segments(box(property_types='["single_family"]', min_price=900000))
    assert "luxury" in lux["single_family"]


def test_multifamily_segmentation_by_unit_bands():
    s = ENG.box_segments(box(property_types='["multifamily"]', min_units=40, max_units=150))
    assert s["multifamily"] == {"u20_49", "u50_99", "u100_199"}
    open_top = ENG.box_segments(box(min_units=250))
    assert open_top["multifamily"] == {"u200_299", "u300p"}
    small = ENG.box_segments(box(property_types='["multifamily"]', min_units=2, max_units=4))
    assert small["multifamily"] == {"u2_4"}
    assert ENG.box_segments(box(max_units=4))["multifamily"] == {"u2_4"}


def test_land_segmentation():
    s = ENG.box_segments(box(property_types='["land","farm","residential_lot"]', min_acres=10,
                             criteria='{"land_types": ["development"]}'))
    assert {"acreage", "agricultural", "residential_lots", "development"} <= s["land"]


def test_commercial_segmentation():
    s = ENG.box_segments(box(property_types='["retail","office","self_storage","medical_office"]'))
    assert s["commercial"] == {"retail", "office", "self_storage", "medical"}
    assert set(ENG.box_segments(box(property_types='["warehouse"]'))) == {"industrial"}


def test_one_investor_with_several_asset_interests_is_one_record_in_several_segments():
    boxes = [box(property_types='["single_family"]', source="stated"),
             box(property_types='["multifamily"]', min_units=20, max_units=60, source="file"),
             box(property_types='["land"]', source="county_records")]
    segs = ENG.segments(boxes, [acq(kind="commercial")])
    assert set(segs) == {"single_family", "multifamily", "land", "commercial"}
    assert segs["land"]["basis"] == ["records"] and segs["commercial"]["basis"] == ["records"]
    rows = [{"segments": segs, "linked_to": None, "geography": {"states": [], "counties": [], "cities": [],
             "zips": []}, "name": "x", "badges": [], "readiness": {"level": "UNKNOWN"}, "ai": {"status": "none"},
             "has_phone": False, "has_email": False, "do_not_contact": False, "last_interaction": None,
             "funding": {"stated": None, "documented": False}, "matches": {"good": 0}, "purchases": {},
             "price": {"min": None, "max": None}, "units": {"min": 20, "max": 60},
             "acres": {"min": None, "max": None}, "strategies": []}]
    for seg in ("single_family", "multifamily", "land", "commercial"):
        assert len(ENG.apply_filters(rows, {"segment": seg}, TODAY)) == 1
    assert ENG.apply_filters(rows, {"segment": "multifamily", "sub": "u20_49"}, TODAY)
    assert not ENG.apply_filters(rows, {"segment": "multifamily", "sub": "u300p"}, TODAY)
    assert not ENG.apply_filters(rows, {"segment": "industrial"}, TODAY)
    # filters combine (AND): segment + an unknown price range never passes a price filter
    assert not ENG.apply_filters(rows, {"segment": "land", "price_min": 100000}, TODAY)


# ── verification, readiness, enrichment ─────────────────────────────────────

def test_recorded_deeds_are_not_verified_or_cash_purchases():
    h = ENG.purchase_history([acq(), acq(days_ago=500)], buyer(), {}, TODAY)
    assert h["status"] == "RECORDED" and h["verified"] == 0 and "no price" in h["note"]
    old = ENG.purchase_history([acq(days_ago=1000)], buyer(), {}, TODAY)
    assert old["status"] == "STALE"
    claimed = ENG.purchase_history([acq(cls=CLAIMED_PURCHASE)], buyer(), {}, TODAY)
    assert claimed["status"] == "CLAIMED" and "not counted" in claimed["lines"][0]


def test_owning_many_properties_is_not_an_active_buyer():
    b = buyer(source="county_roll:dallas", source_detail="Dallas County records: owns 40 houses; 12 deed(s) to "
                                                         "them in the last 24 months")
    a = ENG.assess(b, [box(source="county_records", property_types='["single_family"]')],
                   [acq(days_ago=30)] * 10, [], {}, TODAY)
    assert "ACTIVE_BUYER" not in a["badges"] and a["readiness"]["level"] == "SIGNS_OF_ACTIVITY"
    assert "PURCHASE_HISTORY_VERIFIED" in a["badges"]
    assert a["buy_box"]["status"] == "INFERRED"


def test_verification_transitions_to_deal_ready_only_with_all_evidence():
    now = datetime(2026, 10, 20)
    base = dict(phone="+12145550111", contact_verified_at=now, decision_maker_name="Pat",
                stated_buying_status="buying", stated_buying_at=now, stated_close_days=14)
    confirmed = box(property_types='["single_family"]', confirmed_at=now)
    a = ENG.assess(buyer(**base), [confirmed], [], [], {}, TODAY)
    assert a["readiness"]["level"] == "ACTIVE"                 # no documented funding yet
    assert "No documented funding" in " ".join(a["readiness"]["missing"])
    ready = ENG.assess(buyer(**base, proof_of_funds_on_file=True, proof_of_funds_expires=TODAY + timedelta(days=60)),
                       [confirmed], [], [], {}, TODAY)
    assert ready["readiness"]["level"] == "DEAL_READY" and "DEAL_READY_BUYER" in ready["badges"]
    assert ready["enrichment"]["percent"] == 70    # identity partial (name only), purchases not researched
    stated_only = ENG.assess(buyer(**dict(base, stated_funding="cash", stated_budget_max=300000)), [confirmed], [],
                             [], {}, TODAY)
    assert stated_only["readiness"]["level"] != "DEAL_READY"  # "I pay cash" is not proof of funds
    assert not stated_only["readiness"]["funding"]["documented"]


def test_expired_proof_of_funds_needs_review():
    b = buyer(proof_of_funds_on_file=True, proof_of_funds_expires=TODAY - timedelta(days=1), phone="+12145550111")
    a = ENG.assess(b, [], [], [], {}, TODAY)
    assert not a["readiness"]["funding"]["documented"]
    st = {s["key"]: s for s in a["enrichment"]["stages"]}
    assert st["readiness"]["status"] == "NEEDS_REVIEW"


def test_stale_contact_and_bad_contact():
    stale = ENG.contact_status(buyer(phone="x", contact_verified_at=datetime(2026, 1, 2)), {}, TODAY)
    assert stale["status"] == "STALE"
    bad = ENG.contact_status(buyer(phone="x", contact_verified_at=datetime(2026, 10, 1),
                                   contact_bad_at=datetime(2026, 10, 10)), {}, TODAY)
    assert bad["status"] == "BAD"
    dnc = ENG.contact_status(buyer(phone="x", do_not_contact=True), {}, TODAY)
    assert dnc["status"] == "DO_NOT_CONTACT"


def test_enrichment_is_research_progress_and_a_new_county_buyer_is_honest():
    b = buyer(source="county_roll:dallas", source_detail="Dallas County records: owns 9 houses; 4 deed(s) to them "
                                                         "in the last 24 months")
    a = ENG.assess(b, [box(source="county_records")], [], [], {}, TODAY)
    st = {s["key"]: s["status"] for s in a["enrichment"]["stages"]}
    assert st == {"identity": "COMPLETE", "purchases": "PARTIAL", "contact": "NOT_STARTED",
                  "buy_box": "PARTIAL", "readiness": "NOT_STARTED"}
    assert a["enrichment"]["percent"] == 40                    # 20 + 10 + 0 + 10 + 0
    nxt = a["enrichment"]["next"]
    assert nxt["key"] == "evidence_refresh" and nxt["one_click"] and nxt["free"]
    with_deeds = ENG.assess(b, [box(source="county_records")], [acq()], [], {}, TODAY)
    assert with_deeds["enrichment"]["next"]["key"] == "find_contact" and with_deeds["enrichment"]["next"]["paid"]
    assert not with_deeds["enrichment"]["next"]["one_click"]   # paid lookups are never one click


# ── matching (v2) ───────────────────────────────────────────────────────────

def test_unknown_property_facts_are_missing_not_misses():
    r = M.score_buy_box(deal(), prop(), box(min_price=100000, max_price=200000, min_units=10, max_units=50,
                                            property_types='["multifamily"]'))
    assert not r["disqualified"] and r["version"] == "buyer_match/v2"
    assert len(r["missing"]) == 3 and r["points_available"] == 0 and r["checked"] == 0   # nothing knowable
    assert all(f["matched"] is None for f in r["factors"] if f["dimension"] in ("price", "units", "property_type"))


def test_multifamily_unit_count_matching():
    mf = box(property_types='["multifamily"]', min_units=50, max_units=150)
    hit = M.score_buy_box(deal(buyer_price=4_000_000), prop(property_category="multifamily", unit_count=96), mf)
    miss = M.score_buy_box(deal(buyer_price=4_000_000), prop(property_category="multifamily", unit_count=24), mf)
    assert [f for f in hit["factors"] if f["dimension"] == "units"][0]["matched"] is True
    assert [f for f in miss["factors"] if f["dimension"] == "units"][0]["matched"] is False
    assert hit["score"] > miss["score"]


def test_land_acreage_and_zoning_matching():
    land = box(property_types='["land"]', min_acres=5, max_acres=50, criteria='{"zoning": ["ag", "sf"]}')
    tf = '{"zoning": {"value": "AG"}}'
    r = M.score_buy_box(deal(), prop(property_category="land", acreage=20, type_facts=tf), land)
    dims = {f["dimension"]: f["matched"] for f in r["factors"]}
    assert dims["acres"] is True and dims["zoning"] is True
    small = M.score_buy_box(deal(), prop(property_category="land", acreage=2), land)
    assert {f["dimension"]: f["matched"] for f in small["factors"]}["acres"] is False
    assert any("zoning" in m for m in small["missing"])


def test_commercial_matching_and_cap_rate_only_when_supported():
    c = box(property_types='["retail","office"]', min_cap_rate=7)
    r = M.score_buy_box(deal(), prop(property_category="commercial", property_subtype="retail"), c)
    assert {f["dimension"]: f["matched"] for f in r["factors"]}["property_type"] is True
    assert any("cap rate" in m for m in r["missing"])
    tf = '{"cap_rate": {"value": 8.1}}'
    r2 = M.score_buy_box(deal(), prop(property_category="commercial", property_subtype="retail", type_facts=tf), c)
    assert {f["dimension"]: f["matched"] for f in r2["factors"]}["cap_rate"] is True
    no = M.score_buy_box(deal(), prop(property_category="industrial"), c)
    assert no["disqualified"] and "does not buy" in no["disqualified_reason"]


def test_buyer_exclusions_are_disqualifiers_and_explanations_are_complete():
    b = box(property_types='["single_family"]', counties='["dallas"]',
            criteria='{"exclusions": {"zips": ["75216"]}}')
    r = M.score_buy_box(deal(), prop(property_type="single_family"), b)
    assert r["disqualified"] and "excludes ZIP 75216" in r["disqualified_reason"]
    assert all("detail" in f and "dimension" in f for f in r["factors"])


def test_multi_buy_box_matching_keeps_the_best_box():
    b = NS(id="b", is_active=True, do_not_contact=False, linked_to_buyer_id=None,
           buy_boxes=[box(id="far", property_types='["land"]'),
                      box(id="near", property_types='["single_family"]', counties='["dallas"]')])
    r = M.match_deal_to_buyers(deal(), prop(property_type="single_family"), [b])
    assert r[0]["buy_box"].id == "near" and r[0]["score"] == 100
    linked = NS(**{**b.__dict__, "linked_to_buyer_id": "other"})
    assert M.match_deal_to_buyers(deal(), prop(property_type="single_family"), [linked]) == []


# ── discovery, identity, evidence (DB) ──────────────────────────────────────

def _owner(name, deeds=2, **kw):
    o = {"key": "dallas:%s" % name, "name": name, "kind": "company", "properties": 6, "houses": 5, "multifamily": 1,
         "lots": 0, "land": 0, "commercial": 0, "industrial": 0, "bought_recently": deeds, "last_deed": "2026-08-01",
         "mailing": {"street": "100 SYNTH ST", "city": "Dallas", "state": "TX", "zip": "75201"}, "zips": ["75216"],
         "acquisitions": [{"account": "SYN%04d" % i, "street": "%d SYNTH AVE" % i, "city": "Dallas", "zip": "75216",
                           "kind": "house", "deed_date": "2026-0%d-15" % (i + 1)} for i in range(deeds)]}
    o.update(kw)
    return o


def test_finder_scan_keeps_each_recent_deed_and_reads_commercial_and_land():
    rows = [("A%d" % i, "SYNTH HOLD LLC", "1 MAIL", "Dallas", "TX", "75201", "%d SYNTH ST" % i, "Dallas", "75216",
             datetime(2026, 5, i + 1), k) for i, k in enumerate(("house", "house", "commercial", "land"))]
    res = finder.scan(lambda: iter(rows), county="dallas", min_props=3, today=datetime(2026, 10, 27))
    o = res["buyers"][0]
    assert o["commercial"] == 1 and o["land"] == 1 and len(o["acquisitions"]) == 4 and res["format"] == 2
    assert finder.kind_for("F1") == "commercial" and finder.kind_for("F2") == "industrial"
    assert finder.kind_for("D1") == "land" and finder.kind_for("J") is None


def test_county_import_dedupes_exact_names_and_sends_similar_llcs_to_review(db_session, sample_org):
    org = sample_org.id
    idx = EVD.Index(db_session, org)
    first = EVD.import_owner(db_session, org, "dallas", _owner("SYNTH RIVER HOMES LLC"), generated_at="g",
                             recent_months=24, index=idx)
    assert first["result"] == "created" and first["deeds_added"] == 2
    again = EVD.import_owner(db_session, org, "dallas", _owner("SYNTH RIVER HOMES, L.L.C.", deeds=3),
                             generated_at="g", recent_months=24, index=idx)
    assert again["result"] == "attached" and again["buyer_id"] == first["buyer_id"]
    assert again["deeds_added"] == 1                                      # idempotent: 2 already there
    similar = EVD.import_owner(db_session, org, "dallas", _owner("SYNTH RIVER HOMES II LLC"), generated_at="g",
                               recent_months=24, index=idx)
    assert similar["result"] == "created" and similar["needs_review"] == 1   # NOT merged
    db_session.commit()
    assert db_session.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org).count() == 2
    q = EVD.review_queue(db_session, org)
    assert len(q) == 1 and "LLC" in q[0]["reason"]
    acqs = db_session.query(WholesaleBuyerAcquisition).filter(WholesaleBuyerAcquisition.buyer_id ==
                                                              first["buyer_id"]).all()
    assert len(acqs) == 3 and all(a.evidence_class == RECORDED_TITLE_TRANSFER and a.price is None for a in acqs)


def test_identity_review_same_links_without_deleting_and_different_is_remembered(db_session, sample_org,
                                                                                  sample_advisor):
    org = sample_org.id
    idx = EVD.Index(db_session, org)
    a = EVD.import_owner(db_session, org, "dallas", _owner("SYNTH OAK CAPITAL LLC"), generated_at="g",
                         recent_months=24, index=idx)
    b = EVD.import_owner(db_session, org, "dallas", _owner("SYNTH OAK CAPITAL LP"), generated_at="g",
                         recent_months=24, index=idx)
    item = EVD.review_queue(db_session, org)[0]
    r = EVD.decide(db_session, org, item["id"], "same", sample_advisor)
    assert r["status"] == "confirmed" and r["linked_buyer_id"] == b["buyer_id"]
    sec = db_session.query(WholesaleBuyer).get(b["buyer_id"])
    assert sec.linked_to_buyer_id == a["buyer_id"] and sec.is_active          # linked, not deleted
    EVD.unlink(db_session, org, b["buyer_id"], sample_advisor)
    assert db_session.query(WholesaleBuyer).get(b["buyer_id"]).linked_to_buyer_id is None
    # a "different" decision is never asked again
    c = EVD.import_owner(db_session, org, "dallas", _owner("SYNTH PINE GROUP LLC"), generated_at="g",
                         recent_months=24, index=idx)
    EVD.import_owner(db_session, org, "dallas", _owner("SYNTH PINE GROUP INC"), generated_at="g", recent_months=24,
                     index=idx)
    item = [x for x in EVD.review_queue(db_session, org) if x["buyer_id"] == c["buyer_id"]][0]
    EVD.decide(db_session, org, item["id"], "different", sample_advisor)
    e = db_session.query(WholesaleBuyerEntity).get(item["id"])
    EVD.ensure_entity(db_session, org, e.buyer_id, e.legal_name, status="needs_review")
    assert db_session.query(WholesaleBuyerEntity).get(item["id"]).status == "rejected"


def test_cash_purchase_classification_safeguards(db_session, sample_org, sample_advisor):
    from fastapi import HTTPException
    b = WholesaleBuyer(organization_id=sample_org.id, company_name="SYNTH Cash Co", is_test=True)
    db_session.add(b)
    db_session.flush()
    with pytest.raises(HTTPException):
        EVD.add_evidence(db_session, sample_org.id, b, {"evidence_class": "VERIFIED_CASH_PURCHASE"}, sample_advisor)
    claim = EVD.add_evidence(db_session, sample_org.id, b, {"evidence_class": "CLAIMED_PURCHASE", "price": 150000},
                             sample_advisor)
    assert claim.price is None and claim.confidence == "low"            # no invented price on a claim
    ok_row = EVD.add_evidence(db_session, sample_org.id, b, {"evidence_class": "VERIFIED_CASH_PURCHASE",
                                                             "reference": "TITLE-FILE-SYNTH-1", "price": 150000,
                                                             "date": "2026-09-01"}, sample_advisor)
    assert ok_row.price == 150000 and ok_row.confidence == "high"


# ── API: the dashboard, isolation, permissions, existing data ───────────────

def _mk_buyer(client, H, **kw):
    body = {"company_name": "SYNTH Buyer", "email": "synth@buyer.example.test", "is_test": True}
    body.update(kw)
    return ok(client.post("/wholesale/buyers", headers=H, json=body))


def test_existing_buyers_are_preserved_and_counted(client, auth_headers):
    H = auth_headers
    a = _mk_buyer(client, H, company_name="SYNTH Old One")
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % a["id"], headers=H,
                   json={"label": "old", "property_types": ["single_family"], "counties": ["Dallas"]}))
    lst = ok(client.get(BI + "/buyers?include_test=true", headers=H))
    row = [x for x in lst["buyers"] if x["buyer_id"] == a["id"]][0]
    assert "single_family" in row["segments"] and row["enrichment"]["percent"] >= 0
    s = ok(client.get(BI + "/summary?include_test=true", headers=H))
    cards = {c["key"]: c["value"] for c in s["cards"]}
    assert cards["discovered"] == lst["segment_counts"]["all"] >= 1
    assert ok(client.get("/wholesale/buyers?include_test=true", headers=H))     # the old list still works
    assert ok(client.get(BI + "/summary", headers=H))["cards"][0]["value"] == 0   # test data kept out by default


def test_another_workspace_cannot_see_or_touch_these_buyers(client, auth_headers, db_session):
    from app.models.models import Organization, User
    from app.services.auth_service import create_access_token, hash_password
    a = _mk_buyer(client, auth_headers)
    org = Organization(name="Other BI Co", slug="other-bi", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="bi.other@example.test", full_name="O", role="org_admin",
             password_hash=hash_password("TestPass123!"), must_change_password=False)
    db_session.add(u)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    r = client.get(BI + "/buyers/%s" % a["id"], headers=h)
    assert r.status_code in (403, 404)
    if r.status_code == 404:
        assert not ok(client.get(BI + "/buyers?include_test=true", headers=h))["buyers"]
        for path, body in (("/buyers/%s/facts" % a["id"], {"contact_verified": True}),):
            assert client.patch(BI + path, headers=h, json=body).status_code in (403, 404)
        assert client.post(BI + "/buyers/%s/qualify" % a["id"], headers=h,
                           json={"mode": "recorded"}).status_code in (403, 404)


def test_field_logins_are_fenced_off_buyer_intelligence():
    from app.services.wholesale_team import fence
    for role in ("field_specialist", "canvasser"):
        assert fence("/wholesale/buyer-intel/buyers", "GET", role)
        assert fence("/wholesale/buyer-intel/match", "POST", role)
    assert fence("/wholesale/buyer-intel/buyers", "GET", "manager") is None


# ── AI qualification (practice mode) ────────────────────────────────────────

def test_practice_conversation_extracts_persists_follows_up_and_hands_off(client, auth_headers, db_session,
                                                                          monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)         # gateway refuses -> rules reader, no spend
    H = auth_headers
    pb = ok(client.post(BI + "/practice-buyer", headers=H))
    conv = ok(client.post(BI + "/buyers/%s/qualify" % pb["buyer_id"], headers=H, json={"mode": "practice"}))
    assert conv["mode"] == "practice" and conv["transcript"][-1]["role"] == "ai"

    def say(text):
        return ok(client.post(BI + "/conversations/%s/reply" % conv["id"], headers=H, json={"text": text}))
    say("Yes, we are actively buying right now")
    say("Mostly apartments and some houses")
    say("Dallas County and Tarrant County, also 75216")
    say("Anywhere from $1m to $6m")
    r = say("50 to 150 units")
    assert r["result"]["facts"]["units"] == {"min": 50.0, "max": 150.0}
    say("value-add, hold long term")
    say("heavy is fine")
    say("Cash and private money")
    say("We close in 21 days")
    r = say("Call me next week to talk numbers")
    assert r["result"]["commitments"] and r["result"]["commitments"][0]["exception_id"]
    d = ok(client.get(BI + "/buyers/%s" % pb["buyer_id"], headers=H))
    box = [x for x in d["buy_boxes"] if x["source"] == "ai_conversation"][0]
    assert {"multifamily", "single_family"} <= set(box["property_types"])
    assert box["min_units"] == 50 and box["max_units"] == 150 and box["confirmed_at"] is None   # stated only
    assert d["assessment"]["readiness"]["funding"]["documented"] is False
    c = d["conversations"][0]
    assert len([t for t in c["transcript"] if t["role"] == "buyer"]) == 10              # transcript persisted
    assert c["answers"]["funding"]["quote"].startswith("Cash")                          # every fact keeps its quote
    r = say("Can I speak to a real person about this?")
    assert r["conversation"]["status"] == "handed_off" and r["result"]["handoff_exception_id"]
    from app.models.wholesale_models import WholesaleWorkException
    kinds = {e.kind for e in db_session.query(WholesaleWorkException).filter(
        WholesaleWorkException.subject_id == pb["buyer_id"]).all()}
    assert kinds == {"buyer_handoff", "buyer_followup"}
    # a person takes over, schedules a call; nothing was sent anywhere
    ok(client.post(BI + "/conversations/%s/schedule" % conv["id"], headers=H,
                   json={"kind": "call", "starts_at": "2026-11-02T15:00:00"}))
    from app.models.wholesale_models import WholesaleBuyerOutreach
    assert db_session.query(WholesaleBuyerOutreach).count() == 0


def test_opt_out_is_enforced_and_practice_mode_never_touches_a_real_buyer(client, auth_headers):
    H = auth_headers
    real = _mk_buyer(client, H, company_name="SYNTH Real-Looking", email="buyer@synth-real.example.test",
                     is_test=False)
    r = client.post(BI + "/buyers/%s/qualify" % real["id"], headers=H, json={"mode": "practice"})
    assert r.status_code == 409 and "practice buyer" in r.json()["detail"]
    assert client.post(BI + "/buyers/%s/qualify" % real["id"], headers=H, json={"mode": "live"}).status_code == 409
    pb = ok(client.post(BI + "/practice-buyer", headers=H))
    conv = ok(client.post(BI + "/buyers/%s/qualify" % pb["buyer_id"], headers=H, json={"mode": "practice"}))
    r = ok(client.post(BI + "/conversations/%s/reply" % conv["id"], headers=H, json={"text": "STOP texting me"}))
    assert r["result"]["stopped"] == "opt_out" and r["conversation"]["status"] == "stopped"
    d = ok(client.get(BI + "/buyers/%s" % pb["buyer_id"], headers=H))
    assert d["do_not_contact"] and "DO_NOT_CONTACT" in d["badges"]
    assert client.post(BI + "/buyers/%s/qualify" % pb["buyer_id"], headers=H,
                       json={"mode": "practice"}).status_code == 409
    assert client.post(BI + "/buyers/%s/request-qualification" % pb["buyer_id"], headers=H).status_code == 409


def test_rules_reader_extracts_preferences_and_separates_budget_from_funding():
    r = Q.rules_read("We buy houses and duplexes in Fort Worth and Arlington under $250k, cash, close in 2 weeks")
    f = r["facts"]
    assert {"single_family", "duplex"} <= set(f["property_types"])
    assert {"Fort Worth", "Arlington"} <= set(f["markets"]["cities"])
    assert f["price"] == {"min": None, "max": 250000.0} and f["funding"] == "cash" and f["close_days"] == 14
    assert Q.rules_read("please remove me from your list")["stop"] == "opt_out"
    assert Q.rules_read("not interested")["stop"] == "not_interested"


# ── universal matching from a foreclosure / discovery, then disposition prep ─

def test_foreclosure_property_finds_buyers_and_is_prepared_for_disposition(client, auth_headers, db_session):
    H = auth_headers
    ES = "/wholesale/evosense"
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": "77 Synth Auction Way", "city": "Dallas", "state": "TX", "zip_code": "75216",
        "county": "Dallas", "property_type": "single_family", "is_test": True}))["property_id"]
    fit = _mk_buyer(client, H, company_name="SYNTH Dallas Houses", email="fit@buyer.example.test")
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % fit["id"], headers=H,
                   json={"label": "dallas", "property_types": ["single_family"], "counties": ["Dallas"],
                         "max_price": 200000}))
    land = _mk_buyer(client, H, company_name="SYNTH Land Only", email="land@buyer.example.test")
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % land["id"], headers=H,
                   json={"label": "land", "property_types": ["land"]}))
    m = ok(client.post(BI + "/match", headers=H, json={"subject_type": "evosense", "subject_id": pid}))
    by = {x["buyer_id"]: x for x in m["buyers"]}
    assert by[fit["id"]]["match_score"] == 100 and not by[fit["id"]]["disqualified"]
    assert any("price" in s.lower() for s in by[fit["id"]]["missing"])   # no asking price: missing, not a miss
    assert by[land["id"]]["disqualified"]
    assert by[fit["id"]]["readiness"]["level"] != "DEAL_READY"           # a match is not readiness
    assert not m["subject"]["can_save"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Synth Auction Owner"}))
    prep = ok(client.post(BI + "/match/prepare", headers=H, json={"evosense_id": pid}))
    assert prep["deal_id"] and prep["matches_saved"] >= 1
    s = {c["key"]: c["value"] for c in ok(client.get(BI + "/summary?include_test=true", headers=H))["cards"]}
    assert s["deals_matched"] == 1
    m2 = ok(client.post(BI + "/match", headers=H, json={"subject_type": "deal", "subject_id": prep["deal_id"]}))
    assert m2["subject"]["can_save"] and [x for x in m2["buyers"] if x["buyer_id"] == fit["id"]][0]["saved"]
    ok(client.post(BI + "/match/save", headers=H, json={"deal_id": prep["deal_id"], "buyer_id": fit["id"]}))
    # the existing disposition desk still reads the same matches
    assert ok(client.get("/wholesale/deals/%s/matches" % prep["deal_id"], headers=H))["matches"]


def test_demand_summary_suggests_but_never_starts_a_hunt(client, auth_headers, db_session):
    H = auth_headers
    b = _mk_buyer(client, H, company_name="SYNTH MF Demand")
    bx = ok(client.post("/wholesale/buyers/%s/buy-boxes" % b["id"], headers=H,
                        json={"label": "mf", "property_types": ["multifamily"], "counties": ["Dallas", "Tarrant"],
                              "min_units": 50, "max_units": 150}))
    box_id = bx.get("id") or bx.get("buy_box", {}).get("id") or db_session.query(WholesaleBuyBox).filter(
        WholesaleBuyBox.buyer_id == b["id"]).first().id
    assert not ok(client.get(BI + "/demand?include_test=true", headers=H))["groups"]   # unconfirmed: not counted
    ok(client.post(BI + "/buyers/%s/buy-boxes/%s/confirm" % (b["id"], box_id), headers=H))
    d = ok(client.get(BI + "/demand?include_test=true&segment=multifamily", headers=H))
    g = d["groups"][0]
    assert g["buyers"] == 1 and g["suggested_hunt"]["units_min"] == 50 and "changes no hunt" in d["note"]
    from app.models.evosense_models import EvoSenseStrategy
    assert db_session.query(EvoSenseStrategy).count() == 0


def test_no_paid_lookup_or_send_happens_anywhere_in_the_engine(client, auth_headers, db_session):
    from app.models.wholesale_models import WholesaleBuyerOutreach, WholesaleEnrichmentRequest
    H = auth_headers
    b = _mk_buyer(client, H)
    for path in ("/summary?include_test=true", "/buyers?include_test=true", "/buyers/%s" % b["id"],
                 "/identity-review", "/demand", "/taxonomy", "/match-candidates"):
        ok(client.get(BI + path, headers=H), path)
    ok(client.patch(BI + "/buyers/%s/facts" % b["id"], headers=H, json={"contact_verified": True,
                                                                       "decision_maker_name": "Synth Person"}))
    assert db_session.query(WholesaleEnrichmentRequest).count() == 0
    assert db_session.query(WholesaleBuyerOutreach).count() == 0
