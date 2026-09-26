"""EvoSense Phase 7 — the required journeys, end to end through the services.

Every property, owner and phone number here is SANDBOX data (sandbox_data.py):
synthetic, marked is_test, phones in the 555-01xx fiction range.
"""
from datetime import timedelta

import pytest

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseCostEntry,
                                        EvoSenseEngagement, EvoSenseEnrichmentDecision,
                                        EvoSenseFact, EvoSenseHandoff, EvoSenseIdentityReview,
                                        EvoSenseMessage, EvoSenseObservation, EvoSenseOwner,
                                        EvoSenseProperty, EvoSenseScore, EvoSenseSignal,
                                        EvoSenseStrategy)
from app.models.models import Lead, SuppressionEntry
from app.services.evosense import common as C
from app.services.evosense import conversation as CV
from app.services.evosense import eligibility as EL
from app.services.evosense import enrichment as EN
from app.services.evosense import evaluate as EV
from app.services.evosense import hunt as HU
from app.services.evosense import ingest as IN
from app.services.evosense import outreach as OU
from app.services.evosense import promotion as PR
from app.services.evosense import providers as PV
from app.services.evosense import sandbox_seed as SS

FLAGSHIP = "1418 Cedar Springs Rd"


@pytest.fixture()
def world(db_session, sample_org, sample_advisor):
    out = SS.seed_review(db_session, sample_org.id, sample_advisor, replies=False)
    out["org_id"] = sample_org.id
    out["user"] = sample_advisor
    return out


def P(db, org_id, street):
    return SS.prop_at(db, org_id, street)


def charged(db, org_id, **flt):
    q = db.query(EvoSenseCostEntry).filter(EvoSenseCostEntry.organization_id == org_id,
                                           EvoSenseCostEntry.status == "charged")
    for k, v in flt.items():
        q = q.filter(getattr(EvoSenseCostEntry, k) == v)
    return q.all()


# ── 1. positive journey ─────────────────────────────────────────────────────

def test_flagship_three_sources_one_property_signals_cost_contact(db_session, world):
    db, org = db_session, world["org_id"]
    props = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org,
                                              EvoSenseProperty.street_key.like("1418 CEDAR SPRINGS RD%")).all()
    assert len(props) == 1, "three sources must converge on ONE canonical property"
    prop = props[0]
    obs = db.query(EvoSenseObservation).filter(EvoSenseObservation.property_id == prop.id).all()
    assert {o.provider_key for o in obs} == {"sandbox_property_records", "sandbox_vacancy", "sandbox_tax_roll"}
    assert all(o.connector_kind == C.SANDBOX and o.is_test for o in obs)
    types = {s.signal_type for s in db.query(EvoSenseSignal).filter(
        EvoSenseSignal.property_id == prop.id, EvoSenseSignal.active.is_(True))}
    assert {"VACANT", "ABSENTEE_OWNER", "HIGH_EQUITY", "TAX_DELINQUENT", "LONG_OWNERSHIP"} <= types
    assert prop.opportunity_score >= 80 and prop.is_test
    lookups = charged(db, org, property_id=prop.id, capability=C.CONTACT_ENRICHMENT)
    assert [e.total_cents for e in lookups] == [18]
    cp = db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.owner_id ==
                                               EV.CT.primary_owner(db, prop).id,
                                               EvoSenseContactPoint.kind == "phone").one()
    assert cp.validation == "valid" and cp.line_type == "mobile" and cp.agreeing_sources == 2
    assert prop.contact_confidence >= 80
    eng = SS.engagement_for(db, prop)
    assert eng.status == "active" and eng.delivery_mode == "sandbox_simulated"
    out = db.query(EvoSenseMessage).filter(EvoSenseMessage.engagement_id == eng.id).one()
    assert out.delivery == "sandbox_simulated" and "STOP" in out.body


def test_flagship_reply_becomes_facts_intent_handoff_and_promotion(db_session, world):
    db, org, user = db_session, world["org_id"], world["user"]
    prop = P(db, org, FLAGSHIP)
    eng = SS.engagement_for(db, prop)
    res = CV.receive(db, org, eng, SS.FLAGSHIP_REPLY, delivery="sandbox_simulated")
    db.commit()
    assert res["outcome"] == C.O_INTERESTED
    facts = {f.fact_type: f for f in db.query(EvoSenseFact).filter(EvoSenseFact.property_id == prop.id)}
    assert {"willing_to_sell", "asking_price", "wants_quick_close", "condition", "occupancy",
            "estate_context"} <= set(facts)
    assert facts["asking_price"].value == "150000"
    assert "around 150" in facts["asking_price"].quote and "150,000" in facts["asking_price"].quote
    assert facts["occupancy"].value == "vacant" and facts["condition"].value == "poor"
    assert "last year" in facts["estate_context"].value
    for f in facts.values():
        assert f.truth_state == C.T_SELLER_STATED and f.message_id and f.quote
    assert prop.seller_intent >= 70
    h = db.query(EvoSenseHandoff).filter(EvoSenseHandoff.property_id == prop.id).one()
    codes = {r["code"] for r in C.jload(h.reasons)}
    assert {"INTENT_THRESHOLD", "PRICE_STATED", "ESTATE"} <= codes
    assert prop.status == C.S_NEEDS_YOU
    from app.services.evosense import economics as ECO
    eco = ECO.preliminary(db, prop)
    # A sandbox AVM is a modelled estimate, not comps: no ARV, so NO MAO -
    # even with the seller's asking price and a repair estimate on file.
    assert eco["mao"] is None and eco["asking"] == 150000 and eco["blocked"] == "no_verified_arv"
    labels = {l["label"]: l["truth_label"] for l in eco["lines"]}
    assert labels["Repairs"] == "SYSTEM ESTIMATE" and labels["Seller asking"] == "SELLER STATED"
    assert labels["ARV"] == "INSUFFICIENT COMPARABLE SALES"

    leads_before = db.query(Lead).filter(Lead.organization_id == org).count()
    out = PR.promote(db, org, prop, user)
    db.commit()
    from app.models.wholesale_models import (WholesaleDeal, WholesaleEvent, WholesaleProperty,
                                             WholesaleSellerProfile)
    wp = db.query(WholesaleProperty).filter(WholesaleProperty.id == out["property_id"]).one()
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == out["deal_id"]).one()
    assert wp.is_test and wp.acquisition_source == "evosense" and deal.stage == "seller_engaged"
    prof = db.query(WholesaleSellerProfile).filter(WholesaleSellerProfile.property_id == wp.id).one()
    assert float(prof.asking_price) == 150000 and prof.lead_id == eng.lead_id
    assert db.query(Lead).filter(Lead.organization_id == org).count() == leads_before, \
        "promotion reuses the Lead EvoSense worked; no duplicate person"
    ev = db.query(WholesaleEvent).filter(WholesaleEvent.deal_id == deal.id,
                                         WholesaleEvent.action == "evosense.promoted").one()
    details = C.jload(ev.details)
    assert details["history"] and details["acquisition_cost_cents"] >= 18
    assert PR.promote(db, org, prop, user)["already"] is True
    assert prop.status == C.S_PROMOTED


# ── 2. wrong person ─────────────────────────────────────────────────────────

def test_wrong_person_stops_suppresses_and_never_recontacts(db_session, world, sample_org, sample_advisor):
    db, org = db_session, world["org_id"]
    prop = P(db, org, "4915 Live Oak St")
    eng = SS.engagement_for(db, prop)
    cp = db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.id == eng.contact_point_id).one()
    before = EV.CT.score_contact_point(db, prop, cp)["value"]
    res = CV.receive(db, org, eng, SS.WRONG_PERSON_REPLY, delivery="sandbox_simulated")
    db.commit()
    assert res["outcome"] == C.O_WRONG_PERSON
    assert cp.status == "wrong_party"
    assert EV.CT.score_contact_point(db, prop, cp)["value"] < before
    assert db.query(SuppressionEntry).filter(SuppressionEntry.organization_id == org,
                                             SuppressionEntry.phone == "12145550177").count() == 1
    assert db.query(Lead).filter(Lead.id == eng.lead_id).one().status == "dnc"
    assert eng.status == "stopped" and prop.status == C.S_CLOSED_OUT
    # a NEW strategy covering the same house cannot resurrect it
    s2 = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Second strategy (TEST)"))
    db.commit()
    HU.run_strategy(db, org, s2, trigger="test")
    assert OU.start(db, prop, s2)["started"] is False
    outbound = db.query(EvoSenseMessage).filter(EvoSenseMessage.property_id == prop.id,
                                                EvoSenseMessage.direction == "outbound").count()
    assert outbound == 1, "no second message, ever"
    # and a paid lookup is not bought again for this owner
    dec = EN.decide(db, prop, s2)
    assert dec.decision != C.D_PAID


# ── 3. not now ──────────────────────────────────────────────────────────────

def test_not_now_goes_to_nurture_with_context_and_comes_back(db_session, world):
    db, org = db_session, world["org_id"]
    prop = P(db, org, "7302 Ferguson Rd")
    eng = SS.engagement_for(db, prop)
    res = CV.receive(db, org, eng, SS.NOT_NOW_REPLY, delivery="sandbox_simulated")
    db.commit()
    assert res["outcome"] == C.O_NOT_NOW
    assert eng.status == "nurture" and prop.status == C.S_NURTURE
    assert eng.nurture_until.month == 1 and eng.nurture_until.day == 6 and eng.nurture_until > C.now()
    assert "after the holidays" in eng.nurture_reason
    assert prop.seller_intent is not None and prop.seller_intent <= 25
    assert db.query(SuppressionEntry).filter(SuppressionEntry.organization_id == org,
                                             SuppressionEntry.phone == "15125550119").count() == 0
    eng.nurture_until = C.now() - timedelta(minutes=1)
    EV.CT.primary_owner(db, prop).last_touch_at = C.now() - timedelta(days=90)
    assert CV.resume_due(db, org) == 1
    out = OU.start(db, prop, EV.strategy_for(db, prop))
    assert out["started"], out
    last = (db.query(EvoSenseMessage).filter(EvoSenseMessage.engagement_id == eng.id,
                                             EvoSenseMessage.direction == "outbound")
            .order_by(EvoSenseMessage.created_at.desc()).first())
    assert "after the holidays" in last.body, "the follow-up carries the retained context"


# ── 4. budget ───────────────────────────────────────────────────────────────

def test_tiny_budget_strategy_is_budget_blocked_without_overspending(db_session, world):
    db, org = db_session, world["org_id"]
    sid = world["strategies"]["probate"]
    spent = sum(e.total_cents for e in charged(db, org, strategy_id=sid))
    assert spent <= 20
    blocked = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org,
                                                EvoSenseProperty.status == C.S_BUDGET_BLOCKED).all()
    assert len(blocked) == 2
    for p in blocked:
        assert p.blocked_reason == "BUDGET" and "BUDGET BLOCKED" in p.next_action_detail
        assert not charged(db, org, property_id=p.id)


# ── 5. dedupe across sources ────────────────────────────────────────────────

def test_manual_and_csv_of_the_same_house_merge_and_ambiguous_goes_to_review(db_session, world):
    db, org, user = db_session, world["org_id"], world["user"]
    prop = P(db, org, FLAGSHIP)
    r = HU.add_manual(db, org, {"street_address": "1418 Cedar Springs Road", "city": "Dallas",
                                "state": "TX", "zip_code": "75201"}, user=user)
    assert r["outcome"] == "merged" and r["property_id"] == prop.id
    csv_text = ("street_address,city,state,zip_code,owner_name,estimated_value,signals\n"
                "1418 CEDAR SPRINGS RD,Dallas,TX,75201-2702,Someone Else,999000,VACANT\n")
    out = HU.import_csv(db, org, csv_text, user=user)
    assert out["counts"]["merged"] == 1
    assert prop.estimated_value == 305000, "a lower-ranked import never overwrites the record"
    conflicts = C.jload(prop.conflicts, [])
    assert {c["field"] for c in conflicts} >= {"estimated_value", "owner"}
    again = HU.import_csv(db, org, csv_text, user=user)
    assert again["counts"]["seen"] == 1, "re-importing the same file is idempotent"
    review = db.query(EvoSenseIdentityReview).filter(EvoSenseIdentityReview.organization_id == org).one()
    bonnie = P(db, org, "5530 Bonnie View Rd")
    assert bonnie.identity_status == "review" and bonnie.status == C.S_NEEDS_REVIEW
    assert C.jload(review.candidate_property_ids) == [bonnie.id]
    IN.resolve_review(db, org, review, "merge", bonnie.id, user)
    EV.rescore(db, bonnie)
    assert review.status == "merged" and bonnie.identity_status == "resolved"


# ── 6. multi-property owner ─────────────────────────────────────────────────

def test_six_properties_one_owner_one_lookup_one_conversation(db_session, world):
    db, org = db_session, world["org_id"]
    owners = db.query(EvoSenseOwner).filter(EvoSenseOwner.organization_id == org,
                                            EvoSenseOwner.name_key == "RAYMOND CASTILLO").all()
    assert len(owners) == 1
    owner = owners[0]
    lookups = charged(db, org, owner_id=owner.id, capability=C.CONTACT_ENRICHMENT)
    assert len(lookups) == 1, "the owner is looked up once, not once per property"
    engs = db.query(EvoSenseEngagement).filter(EvoSenseEngagement.organization_id == org,
                                               EvoSenseEngagement.owner_id == owner.id).all()
    assert [e.status for e in engs].count("active") == 1
    assert {e.blocked_reason for e in engs if e.status == "blocked"} == {"OWNER ALREADY IN CONVERSATION"}
    msgs = db.query(EvoSenseMessage).filter(
        EvoSenseMessage.engagement_id.in_([e.id for e in engs]),
        EvoSenseMessage.direction == "outbound").count()
    assert msgs == 1, "no blast"


# ── 7. provider failure ─────────────────────────────────────────────────────

def test_provider_failure_refunds_falls_back_and_waits_without_inventing(db_session, world):
    db, org = db_session, world["org_id"]
    prop = P(db, org, "1805 Nolte Dr")
    rows = db.query(EvoSenseCostEntry).filter(EvoSenseCostEntry.property_id == prop.id,
                                              EvoSenseCostEntry.capability == C.CONTACT_ENRICHMENT).all()
    assert sorted((r.provider_key, r.status) for r in rows) == [
        ("sandbox_skiptrace", "failed_refunded"), ("sandbox_skiptrace_backup", "charged")]
    assert prop.status == C.S_WAITING_DATA and "WAITING FOR DATA" in prop.next_action_detail
    owner = EV.CT.primary_owner(db, prop)
    assert db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.owner_id == owner.id).count() == 0
    cfg = PV.config(db, org, "sandbox_skiptrace")
    assert cfg.last_failure_at is not None and "ProviderTimeout" in cfg.last_failure_reason
    # three consecutive failures -> DEGRADED -> routed around
    for _ in range(3):
        PV.record_failure(cfg, "ProviderTimeout: test")
    assert PV.health_state(PV.PROVIDERS["sandbox_skiptrace"], cfg) == C.H_DEGRADED
    keys = [p.key for p, _, _ in PV.route(db, org, C.CONTACT_ENRICHMENT)]
    assert "sandbox_skiptrace" not in keys and "sandbox_skiptrace_backup" in keys


def test_provider_failure_with_no_budget_for_fallback_waits_for_data(db_session, sample_org, sample_advisor):
    db, org = db_session, sample_org.id
    SS.enable_sandbox(db, org)
    s = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Tight (TEST)", daily_budget_cents=20,
                                                          outreach_policy={"auto_outreach": False}))
    db.commit()
    HU.run_strategy(db, org, s, trigger="test", enrich=False)
    prop = P(db, org, "1805 Nolte Dr")
    res = EN.run(db, prop, s)
    assert res["outcome"] == "provider_failed"
    assert [a["result"] for a in res["attempts"]] == ["failed", "not attempted"]
    assert prop.status == C.S_WAITING_DATA
    assert not charged(db, org, property_id=prop.id)


# ── 8. idempotency, kill switches, suppression, LLC ─────────────────────────

def test_hunt_twice_changes_nothing(db_session, world):
    db, org = db_session, world["org_id"]
    before = (db.query(EvoSenseProperty).count(), db.query(EvoSenseCostEntry).count(),
              db.query(EvoSenseMessage).count(), db.query(EvoSenseEngagement).count())
    for sid in world["strategies"].values():
        HU.run_strategy(db, org, db.query(EvoSenseStrategy).get(sid), trigger="test")
    after = (db.query(EvoSenseProperty).count(), db.query(EvoSenseCostEntry).count(),
             db.query(EvoSenseMessage).count(), db.query(EvoSenseEngagement).count())
    assert before == after


def test_kill_switches_are_enforced_server_side(db_session, world):
    db, org = db_session, world["org_id"]
    ctl = C.controls(db, org)
    prop = P(db, org, "2611 Glenfield Ave")
    ctl.paused_paid_data = True
    n = db.query(EvoSenseCostEntry).count()
    assert EN.decide(db, prop, EV.strategy_for(db, prop), approved=True).decision == C.D_RETRY
    assert db.query(EvoSenseCostEntry).count() == n
    ctl.paused_paid_data = False
    # a lookup refused only because paid data was paused is retried by the next hunt
    blocked = P(db, org, "3102 Avenue J")
    probate = db.query(EvoSenseStrategy).get(world["strategies"]["probate"])
    EN.run(db, blocked, probate)
    ctl.paused_paid_data = True
    EN.run(db, blocked, probate)
    assert blocked.status == C.S_WAITING_DATA
    ctl.paused_paid_data = False
    probate.daily_budget_cents = 500
    HU.run_strategy(db, org, probate, trigger="test")
    assert charged(db, org, property_id=blocked.id), "retried once the brake was released"
    ctl.paused_discovery = True
    run = HU.run_strategy(db, org, db.query(EvoSenseStrategy).get(world["strategies"]["dfw"]))
    assert run.status == "skipped"
    ctl.paused_discovery = False
    ctl.paused_sms = True
    kincaid = P(db, org, "7302 Ferguson Rd")
    cp, _ = EV.CT.best_contact(db, kincaid)
    assert "SMS_PAUSED" in EL.check(db, kincaid, cp, EV.strategy_for(db, kincaid), mark=False)["blocks"]


def test_suppressed_contact_is_never_worked(db_session, world):
    db, org = db_session, world["org_id"]
    prop = P(db, org, "3330 Hatcher St")
    assert prop.status == C.S_SUPPRESSED
    cp = db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.value == SS.SUPPRESSED_PHONE).one()
    assert cp.status == "suppressed"
    assert db.query(EvoSenseMessage).filter(EvoSenseMessage.property_id == prop.id).count() == 0
    assert EN.decide(db, prop, EV.strategy_for(db, prop)).decision == C.D_SUPPRESSED


def test_llc_owner_stays_unresolved_and_is_not_worked(db_session, world):
    db, org = db_session, world["org_id"]
    prop = P(db, org, "2124 S Ervay St")
    owner = EV.CT.primary_owner(db, prop)
    assert owner.owner_type == "llc" and owner.resolution == "unresolved"
    assert prop.contact_confidence < 60 and prop.status == C.S_CONTACT_FOUND
    assert db.query(EvoSenseMessage).filter(EvoSenseMessage.property_id == prop.id).count() == 0


def test_high_opportunity_without_contact_waits_honestly(db_session, world):
    db, org = db_session, world["org_id"]
    prop = P(db, org, "2611 Glenfield Ave")
    assert prop.opportunity_score >= 80 and prop.status == C.S_WAITING_DATA
    assert prop.contact_confidence is None


def test_real_property_never_gets_sandbox_data_and_cold_sms_needs_attestation(
        db_session, sample_org, sample_advisor):
    db, org = db_session, sample_org.id
    SS.enable_sandbox(db, org)
    s = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Real (not test)"))
    s.is_test = False
    r = HU.add_manual(db, org, {"street_address": "900 Real St", "city": "Dallas", "state": "TX",
                                "zip_code": "75201", "county": "Dallas", "property_type": "single_family",
                                "estimated_value": 250000, "mortgage_balance": 50000,
                                "last_sale_date": "2001-01-01", "owner_name": "Pat Q Realowner",
                                "mailing_street": "1 Elsewhere Ln", "mailing_city": "Austin",
                                "mailing_state": "TX", "mailing_zip": "78701",
                                "signals": ["VACANT", "TAX_DELINQUENT"]}, user=sample_advisor, strategy=s)
    prop = db.query(EvoSenseProperty).get(r["property_id"])
    assert not prop.is_test
    dec = EN.decide(db, prop, s)
    assert dec.decision == C.D_RETRY and "sandbox providers never serve real properties" in dec.reasons
    from app.services import wholesale_enrichment as WE
    EV.CT.apply_result(db, EV.CT.primary_owner(db, prop),
                       WE.EnrichmentResult(status="succeeded", provider="manual",
                                           phones=[WE.EnrichmentPhone(number="2145550100")]),
                       PV.PROVIDERS["manual"])
    cp, _ = EV.CT.best_contact(db, prop)
    assert "COMPLIANCE_NOT_CONFIRMED" in EL.check(db, prop, cp, s, mark=False)["blocks"]


# ── 9. scores ───────────────────────────────────────────────────────────────

def test_scores_are_versioned_explained_and_never_overwritten(db_session, world):
    db, org = db_session, world["org_id"]
    prop = P(db, org, FLAGSHIP)
    cur = db.query(EvoSenseScore).filter(EvoSenseScore.property_id == prop.id,
                                         EvoSenseScore.score_type == "property_opportunity",
                                         EvoSenseScore.is_current.is_(True)).one()
    assert cur.version == "property_opportunity/v3"
    factors = C.jload(cur.factors)
    assert factors and all("label" in f and "points" in f for f in factors)
    n = db.query(EvoSenseScore).filter(EvoSenseScore.property_id == prop.id).count()
    EV.rescore(db, prop)
    assert db.query(EvoSenseScore).filter(EvoSenseScore.property_id == prop.id).count() == n
    s = EV.strategy_for(db, prop)
    s.max_value = 200000          # the flagship falls out of the value range
    EV.rescore(db, prop, s)
    rows = db.query(EvoSenseScore).filter(EvoSenseScore.property_id == prop.id,
                                          EvoSenseScore.score_type == "property_opportunity").all()
    assert len(rows) == 2 and sum(r.is_current for r in rows) == 1


# ── 10. the 17 outcomes ─────────────────────────────────────────────────────

@pytest.mark.parametrize("text,outcome", [
    ("No. Never selling.", C.O_HARD_NO),
    ("STOP", C.O_DNC),
    ("Please do not text me again", C.O_DNC),
    ("Wrong number", C.O_WRONG_PERSON),
    ("Not right now, maybe next year", C.O_NOT_NOW),
    ("Busy right now, call me next week", C.O_CALL_LATER),
    ("I won't take less than 400", C.O_PRICE_TOO_HIGH),
    ("We already sold it last spring", C.O_ALREADY_SOLD),
    ("It's listed with my realtor", C.O_LISTED),
    ("I'm just the tenant here", C.O_NOT_OWNER),
    ("I need to talk to my siblings first", C.O_FAMILY),
    ("The tenant won't pay and I want out", C.O_TENANT),
    ("Maybe, depends on the number", C.O_MAYBE),
    ("Yes I'm interested", C.O_INTERESTED),
    ("What would you offer for it?", C.O_WANTS_OFFER),
    ("You can come see it Saturday", C.O_APPOINTMENT),
    ("My mother passed away and the estate owns it now", C.O_ESTATE),
    ("The weather is nice", C.O_UNKNOWN),
])
def test_reader_places_each_outcome(text, outcome):
    assert CV.classify(text)["outcome"] == outcome


def test_nurture_dates():
    from datetime import datetime
    now = datetime(2026, 9, 24)
    assert CV.nurture_date("maybe after the holidays", now, 60)[0] == datetime(2027, 1, 6)
    assert CV.nurture_date("try me in 3 months", now, 60)[0] == now + timedelta(days=90)
    assert CV.nurture_date("no idea", now, 60)[0] == now + timedelta(days=60)
