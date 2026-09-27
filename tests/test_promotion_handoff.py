"""Property -> seller -> deal handoff (SANDBOX data only).

When EvoSense promotes a worked owner into the Wholesale deal workflow, the
deal receives: the seller's facts WITH provenance, Seller Intent, contact
provenance, contactability, acquisition cost and the known unknowns - and
never a fabricated ARV or MAO. One deal object; no second one.
"""
import pytest

from app.models.wholesale_models import (WholesaleDeal, WholesaleEvent, WholesaleSellerFact,
                                         WholesaleSellerProfile)
from app.services.evosense import common as C
from app.services.evosense import conversation as CV
from app.services.evosense import promotion as PR
from app.services.evosense import sandbox_seed as SS

FLAGSHIP = "1418 Cedar Springs Rd"


@pytest.fixture()
def promoted(db_session, sample_org, sample_advisor):
    db, org = db_session, sample_org.id
    SS.seed_review(db, org, sample_advisor, replies=False)
    prop = SS.prop_at(db, org, FLAGSHIP)
    CV.receive(db, org, SS.engagement_for(db, prop), SS.FLAGSHIP_REPLY, delivery="sandbox_simulated")
    db.commit()
    out = PR.promote(db, org, prop, sample_advisor)
    db.commit()
    return db, org, prop, out


def test_the_sellers_words_travel_with_their_provenance(promoted):
    db, org, prop, out = promoted
    prof = db.query(WholesaleSellerProfile).filter_by(property_id=out["property_id"]).one()
    facts = {f.fact_type: f for f in db.query(WholesaleSellerFact).filter_by(
        profile_id=prof.id, superseded=False)}
    assert {"willing_to_sell", "asking_price", "condition", "occupancy"} <= set(facts)
    for f in facts.values():
        assert f.source_channel == "evosense" and f.message_ref.startswith("evosense:")
        assert f.truth_state == C.T_SELLER_STATED and f.verified_at is None and f.quote
    assert prof.seller_intent is not None and prof.qualification_status


def test_the_deal_receives_the_analysis_package_and_no_invented_numbers(promoted):
    db, org, prop, out = promoted
    deal = db.query(WholesaleDeal).filter_by(id=out["deal_id"]).one()
    ev = db.query(WholesaleEvent).filter_by(deal_id=deal.id, action="evosense.promoted").one()
    d = C.jload(ev.details)
    assert d["contact_provenance"]["contact_confidence"] is not None
    assert d["contactability"]["state"]
    assert d["acquisition_cost"]["total_cents"] == d["acquisition_cost_cents"]
    assert any(u.startswith("ARV") for u in d["known_unknowns"])
    # no fabricated ARV / MAO on the deal
    assert deal.arv is None and deal.max_allowable_offer is None
    assert db.query(WholesaleDeal).count() == 1
