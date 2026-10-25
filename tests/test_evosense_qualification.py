"""Seller qualification from the seller's own words and a rep's notes only."""
from types import SimpleNamespace

from app.services.evosense import common as C
from app.services.evosense import conversation as CV
from app.services.evosense import qualification as QF
from app.services.evosense import sandbox_seed as SS


def F(ftype, value, truth=C.T_SELLER_STATED, quote="q"):
    return SimpleNamespace(fact_type=ftype, value=value, truth_state=truth, quote=quote, superseded=False,
                           created_at=None)


def test_the_reader_adds_offer_repairs_and_authority_facts_without_changing_the_outcome():
    r = CV.classify("I'm the executor. We already got an offer from another investor and it needs a new roof.")
    kinds = {f["fact_type"] for f in r["facts"]}
    assert {"existing_offer", "known_repairs", "authority_to_sell"} <= kinds
    assert CV.classify("It does not need a new roof")["facts"] == [] or \
        "known_repairs" not in {f["fact_type"] for f in CV.classify("It does not need a new roof")["facts"]}
    # a STOP still closes the conversation and extracts nothing
    stop = CV.classify("Stop texting me, I'm the owner")
    assert stop["outcome"] == C.O_DNC and stop["facts"] == []


def test_checklist_uses_only_stated_or_recorded_facts():
    facts = [F("willing_to_sell", "yes"), F("condition", "poor"), F("occupancy", "vacant", truth=C.T_HUMAN),
             F("asking_price", "150000", truth=C.T_PROVIDER)]
    q = QF.checklist(facts)
    ans = {r["key"]: r["answer"] for r in q["questions"]}
    assert ans["open_to_selling"]["who"] == "seller said" and ans["occupancy"]["who"] == "rep recorded"
    assert ans["timeline"] is None and ans["authority"] is None
    assert q["known"] == 3 and q["next_question"].startswith("Just to confirm")
    assert QF.checklist([])["known"] == 0                     # distress alone answers nothing


def test_a_real_reply_fills_the_checklist(db_session, sample_org, sample_advisor):
    SS.seed_review(db_session, sample_org.id, sample_advisor, replies=False)
    prop = SS.prop_at(db_session, sample_org.id, "1418 Cedar Springs Rd")
    CV.receive(db_session, sample_org.id, SS.engagement_for(db_session, prop),
               "Yes I'd sell. I'm the owner. It's vacant, needs a new roof, and I had another offer.",
               delivery="sandbox_simulated")
    db_session.commit()
    q = QF.for_property(db_session, prop)
    ans = {r["key"]: r["answer"] for r in q["questions"]}
    for k in ("authority", "open_to_selling", "occupancy", "repairs", "existing_offer"):
        assert ans[k] is not None, k


def test_a_callable_owner_becomes_one_va_call_task_and_the_outcome_closes_it(db_session, sample_org, sample_advisor):
    from app.models.evosense_models import EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty
    from app.models.wholesale_models import WholesaleWorkException
    from app.services import wholesale_enrichment as WE
    from app.services.evosense import calling as CL
    from app.services.evosense import contacts as CT
    from app.services.evosense import providers as PV
    db, org = db_session, sample_org.id
    p = EvoSenseProperty(organization_id=org, street_address="12 Call Task St", city="Dallas", state="TX",
                         zip_code="75216", county="Dallas", is_test=False, status="new", priority_score=80)
    db.add(p)
    db.flush()
    o = EvoSenseOwner(organization_id=org, display_name="RAY CALLER", owner_type="individual")
    db.add(o)
    db.flush()
    db.add(EvoSenseOwnership(organization_id=org, property_id=p.id, owner_id=o.id, is_current=True, source="dcad"))
    CT.apply_result(db, o, WE.EnrichmentResult(status=WE.STATUS_SUCCEEDED, provider="manual", owner_name="Ray Caller",
                                               phones=[WE.EnrichmentPhone(number="2145550166")]),
                    PV.PROVIDERS["manual"])
    db.commit()
    assert CL.sweep(db, org) == 1 and CL.sweep(db, org) == 1
    q = db.query(WholesaleWorkException).filter(WholesaleWorkException.subject_id == p.id,
                                                WholesaleWorkException.kind == CL.VA_KIND)
    assert q.count() == 1 and q.one().status == "open"                 # idempotent: one task
    CL.record_outcome(db, org, p, sample_advisor, outcome="voicemail")
    db.commit()
    assert q.one().status == "open"                                    # an attempt keeps the task open
    CL.record_outcome(db, org, p, sample_advisor, outcome="not_interested")
    db.commit()
    assert q.one().status == "complete"
    assert CL.call_queue(db, org)["rows"] == []                        # not interested: off the call list
