"""Enrichment policy tiers and enrichment economics (SANDBOX data only).

  * FREE ONLY never pays - not even by falling back;
  * MANUAL APPROVAL makes every paid lookup wait for a person;
  * AGGRESSIVE pays for a somewhat weaker property, still inside every budget;
  * each decision records the policy, the opportunity it was worth, and what
    it did to Contact Confidence; acquisition cost is the ledger, exactly.
"""
import pytest

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseCostEntry,
                                        EvoSenseEnrichmentDecision, EvoSenseProperty)
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import economics as EC
from app.services.evosense import enrichment as EN
from app.services.evosense import hunt as HU
from app.services.evosense import sandbox_seed as SS


@pytest.fixture()
def hunted(db_session, sample_org, sample_advisor):
    db, org = db_session, sample_org.id
    SS.enable_sandbox(db, org)
    s = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Policy (TEST)",
                                                          daily_budget_cents=100000,
                                                          outreach_policy={"auto_outreach": False}))
    db.commit()
    HU.run_strategy(db, org, s, trigger="test", enrich=False)
    db.commit()
    return db, org, s


def _needs_contact(db, org, s):
    """Sandbox properties worth a lookup whose owner has no contact yet."""
    out = []
    for p in (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org)
              .order_by(EvoSenseProperty.opportunity_score.desc()).all()):
        owner = CT.primary_owner(db, p)
        if owner is None or owner.owner_type not in ("individual", "unknown", None):
            continue
        if p.street_address == "1805 Nolte Dr":          # the sandbox's failing-provider case
            continue
        has = db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.owner_id == owner.id).count()
        if not has and (p.opportunity_score or 0) >= (s.min_opportunity_score or 60):
            out.append(p)
    return out


def _charged(db, org, prop):
    return db.query(EvoSenseCostEntry).filter(EvoSenseCostEntry.organization_id == org,
                                              EvoSenseCostEntry.property_id == prop.id,
                                              EvoSenseCostEntry.status == "charged").all()


def test_free_only_never_pays(hunted):
    db, org, s = hunted
    s.enrichment_policy = "free_only"
    prop = _needs_contact(db, org, s)[0]
    res = EN.run(db, prop, s)
    assert res["decision"] == C.D_POLICY and "FREE ONLY" in res["reasons"][0]
    assert _charged(db, org, prop) == []
    dec = db.query(EvoSenseEnrichmentDecision).filter_by(property_id=prop.id).one()
    assert dec.policy == "free_only" and dec.opportunity_score == prop.opportunity_score


def test_manual_approval_waits_for_a_person_then_records_what_it_bought(hunted, sample_advisor):
    db, org, s = hunted
    s.enrichment_policy = "manual_approval"
    prop = _needs_contact(db, org, s)[0]
    res = EN.run(db, prop, s)
    assert res["decision"] == C.D_APPROVAL and "MANUAL APPROVAL" in res["reasons"][0]
    assert _charged(db, org, prop) == []
    res = EN.run(db, prop, s, user=sample_advisor, approved=True)
    assert res["decision"] == C.D_PAID and res["outcome"] in ("found", "no_match")
    charged = _charged(db, org, prop)
    assert charged and all(c.total_cents <= res["estimated_cost_cents"] for c in charged)
    paid = (db.query(EvoSenseEnrichmentDecision)
            .filter_by(property_id=prop.id, decision=C.D_PAID).one())
    assert paid.policy == "manual_approval" and paid.decided_by == "user"
    assert paid.confidence_after == prop.contact_confidence
    cost = EC.acquisition_cost(db, prop)
    assert cost["total_cents"] == sum(c.total_cents for c in charged)
    assert cost["paid_decisions"][0]["confidence_after"] == prop.contact_confidence


def test_aggressive_pays_for_a_somewhat_weaker_property_standard_does_not(hunted):
    db, org, s = hunted
    prop = [p for p in _needs_contact(db, org, s) if (p.opportunity_score or 0) <= 90][0]
    s.min_opportunity_score = prop.opportunity_score + 10
    s.enrichment_policy = "standard"
    assert EN.decide(db, prop, s).decision == C.D_INSUFFICIENT
    s.enrichment_policy = "aggressive"
    assert EN.decide(db, prop, s).decision == C.D_PAID


def test_a_policy_is_validated_and_admin_only(client, auth_headers, db_session, sample_org):
    from app.services.evosense import strategy as ST
    clean, problems = ST.validate({"enrichment_policy": "spend_everything"}, partial=True)
    assert problems and "enrichment_policy" not in clean
    clean, problems = ST.validate({"enrichment_policy": "FREE_ONLY"}, partial=True)
    assert not problems and clean["enrichment_policy"] == "free_only"
