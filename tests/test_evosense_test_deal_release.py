"""A rehearsal (test) deal on a REAL address must not keep the real discovery
record out of contact lookups, rankings and calls. Synthetic records only."""
from app.models.evosense_models import EvoSenseEvent, EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty
from app.models.wholesale_models import WholesaleDeal
from app.services import wholesale_service as WS
from app.services.evosense import contact_batch as CB
from app.services.evosense import promotion as PR


def _real_prop(db, org, addr="10 Real Rehearsal St", *, is_test=False):
    p = EvoSenseProperty(organization_id=org, street_address=addr, city="Fort Worth", state="TX",
                         zip_code="76111", county="Tarrant", property_type="single_family", is_test=is_test,
                         status="new", opportunity_score=70, priority_score=80)
    db.add(p)
    db.flush()
    o = EvoSenseOwner(organization_id=org, display_name="SYNTH REAL OWNER", owner_type="individual",
                      is_test=is_test, mailing_street="1 Mail St")
    db.add(o)
    db.flush()
    db.add(EvoSenseOwnership(organization_id=org, property_id=p.id, owner_id=o.id, is_current=True, source="tad"))
    db.flush()
    return p


def _promote_to_deal(db, org, p, *, deal_is_test=False):
    wprop = WS.create_property(db, org, None, {"street_address": p.street_address, "city": p.city, "state": "TX",
                                               "zip_code": p.zip_code, "is_test": deal_is_test})
    deal = WS.deal_for_property(db, org, wprop.id)
    p.promoted_deal_id, p.promoted_property_id = deal.id, wprop.id
    db.flush()
    return wprop, deal


def test_marking_the_deal_as_test_releases_the_real_property(db_session, sample_org):
    db, org = db_session, sample_org.id
    p = _real_prop(db, org)
    wprop, deal = _promote_to_deal(db, org, p)
    db.commit()
    assert CB._classify(p, None, {}, {}, [])[0] == "skipped"          # tied to a deal: skipped
    out = WS.set_test_flag(db, org, wprop, True, note="rehearsal", user=None)
    db.commit()
    db.refresh(p)
    assert out["evosense_released"] == 1
    assert p.promoted_deal_id is None and p.promoted_property_id is None
    d = db.query(WholesaleDeal).filter(WholesaleDeal.id == deal.id).one()
    assert d.is_test is True                                          # the test deal is kept, unchanged
    ev = (db.query(EvoSenseEvent).filter(EvoSenseEvent.property_id == p.id,
                                         EvoSenseEvent.action == "promotion.released_test_deal").one())
    assert deal.id in (ev.details or "")                              # the audit keeps the old link
    owner = db.query(EvoSenseOwner).filter(EvoSenseOwner.display_name == "SYNTH REAL OWNER").first()
    st, why = CB._classify(p, owner, {"status": "VERIFIED"}, {}, [])
    assert st == "ready", why                                         # back in the lookup queue


def test_the_scheduler_pass_repairs_existing_links_and_is_idempotent(db_session, sample_org):
    db, org = db_session, sample_org.id
    p = _real_prop(db, org, "11 Already Linked St")
    _promote_to_deal(db, org, p, deal_is_test=True)
    real = _real_prop(db, org, "12 Real Deal St")
    _promote_to_deal(db, org, real)                                   # a real deal stays linked
    sandbox = _real_prop(db, org, "13 Sandbox St", is_test=True)
    _promote_to_deal(db, org, sandbox, deal_is_test=True)             # sandbox on sandbox stays linked
    db.commit()
    assert PR.release_test_promotions(db, org) == 1
    db.commit()
    for x in (p, real, sandbox):
        db.refresh(x)
    assert p.promoted_deal_id is None
    assert real.promoted_deal_id is not None and sandbox.promoted_deal_id is not None
    assert PR.release_test_promotions(db, org) == 0


def test_release_is_scoped_to_the_organization(db_session, sample_org):
    db, org = db_session, sample_org.id
    p = _real_prop(db, org, "14 Scoped St")
    _promote_to_deal(db, org, p, deal_is_test=True)
    db.commit()
    assert PR.release_test_promotions(db, "some-other-org") == 0
    db.refresh(p)
    assert p.promoted_deal_id is not None


def test_absentee_recheck_retracts_a_signal_when_the_mailing_address_is_the_house(db_session, sample_org):
    from app.models.evosense_models import EvoSenseSignal
    from app.services.evosense import signals as SIG
    db, org = db_session, sample_org.id
    p = _real_prop(db, org, "41 Maple Green")
    p.zip_code = "75002"
    o = db.query(EvoSenseOwner).filter(EvoSenseOwner.organization_id == org).order_by(
        EvoSenseOwner.created_at.desc()).first()
    o.mailing_street, o.mailing_zip = "41 MAPLE GRN", "75002"
    away = _real_prop(db, org, "50 Elsewhere Dr")
    db.flush()
    # an old signal written under the previous comparison rule
    for prop in (p, away):
        SIG.upsert(db, prop, "ABSENTEE_OWNER", source=SIG.DERIVED_SOURCE, confidence=80,
                   normalized_value="mailing address differs", provenance={}, rule_version="derive/v3")
    db.commit()
    assert SIG.recheck_absentee(db, org) == 1
    db.commit()
    live = {s.property_id for s in db.query(EvoSenseSignal).filter(
        EvoSenseSignal.signal_type == "ABSENTEE_OWNER", EvoSenseSignal.active.is_(True))}
    assert p.id not in live and away.id in live                     # "1 Mail St" is elsewhere
    assert SIG.recheck_absentee(db, org) == 0                        # idempotent
