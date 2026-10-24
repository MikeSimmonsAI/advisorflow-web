"""Contact enrichment never ties a phone or email to the wrong owner, separates a
probable match from a matched one, and reports a plain status. No provider is called."""
from types import SimpleNamespace

from app.models.evosense_models import EvoSenseContactPoint, EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty
from app.services import contact_match_guard as G
from app.services import wholesale_enrichment as WE
from app.services.evosense import contacts as CT

ES = "/wholesale/evosense"


def res(name, phones=("2145550199",), **ev):
    return WE.EnrichmentResult(status=WE.STATUS_SUCCEEDED, provider="fake", owner_name=name,
                               phones=[WE.EnrichmentPhone(number=p, phone_type="mobile") for p in phones],
                               emails=[], match_evidence=ev or None)


def test_the_guard_reads_the_name_and_the_deceased_flag():
    assert G.assess("HOGAN KRISTINA L", res("Kristina Hogan"))["verdict"] == G.MATCHED
    assert G.assess("HOGAN KRISTINA L", res("Bob Hogan"))["verdict"] == G.PROBABLE
    wrong = G.assess("HOGAN KRISTINA L", res("Pat Example"))
    assert wrong["verdict"] == G.NO_VERIFIED_MATCH and "Pat Example" in wrong["why"]
    assert G.assess("HOGAN KRISTINA L", res(None))["verdict"] == G.PROBABLE
    assert G.assess("HOGAN KRISTINA L", res("Kristina Hogan", deceased=True))["verdict"] == G.NO_VERIFIED_MATCH
    # the vendor's own evidence wins when it gave one
    assert G.assess("HOGAN KRISTINA L", res("Kristina Hogan", name_match="none"))["verdict"] == G.NO_VERIFIED_MATCH


def _owner(db, org_id, name="HOGAN KRISTINA L"):
    p = EvoSenseProperty(organization_id=org_id, street_address="1 Guard Test St", city="Dallas", state="TX",
                         zip_code="75216", county="Dallas", is_test=True)
    o = EvoSenseOwner(organization_id=org_id, display_name=name, owner_type="individual", is_test=True)
    db.add_all([p, o])
    db.flush()
    db.add(EvoSenseOwnership(organization_id=org_id, property_id=p.id, owner_id=o.id, is_current=True, source="dcad"))
    db.flush()
    return p, o


PROVIDER = SimpleNamespace(key="fake_vendor", connector_kind="real")


def test_evosense_contacts_wrong_person_writes_nothing_probable_is_labelled(db_session, sample_org):
    p, o = _owner(db_session, sample_org.id)
    assert CT.apply_result(db_session, o, res("Pat Example", phones=("2145550101",)), PROVIDER) == []
    assert db_session.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.owner_id == o.id).count() == 0
    probable = CT.apply_result(db_session, o, res("Bob Hogan", phones=("2145550102",)), PROVIDER)
    assert len(probable) == 1 and probable[0].status_reason.startswith("probable match")
    good = CT.apply_result(db_session, o, res("Kristina Hogan", phones=("2145550103",)), PROVIDER)
    assert '"match_quality": "MATCHED"' in good[0].match_evidence
    db_session.commit()
    st = G.property_status(db_session, p)
    assert st["status"] == G.ST_MATCHED and {c["quality"] for c in st["contacts"]} == {G.MATCHED, G.PROBABLE}


def test_status_endpoint_and_states(client, auth_headers, db_session, sample_org):
    p, o = _owner(db_session, sample_org.id, name="SOLO OWNER")
    db_session.commit()
    st = client.get(ES + "/properties/%s/enrichment-status" % p.id, headers=auth_headers).json()
    assert st["status"] == "NOT_REQUESTED" and st["label"] == "Not requested"
    CT.apply_result(db_session, o, res("Someone Else"), PROVIDER)
    from app.models.evosense_models import EvoSenseEnrichmentDecision
    db_session.add(EvoSenseEnrichmentDecision(organization_id=sample_org.id, property_id=p.id, owner_id=o.id,
                                              capability="CONTACT_ENRICHMENT", decision="PAID_LOOKUP_APPROVED",
                                              outcome="no_match", reasons='["wrong person"]'))
    db_session.commit()
    st = client.get(ES + "/properties/%s/enrichment-status" % p.id, headers=auth_headers).json()
    assert st["status"] == "NO_VERIFIED_MATCH" and st["contacts"] == []


def test_wholesale_path_does_not_attach_another_persons_phone(client, auth_headers, db_session, sample_org,
                                                             sample_advisor):
    from app.models.models import Lead
    from app.models.wholesale_models import WholesaleEnrichmentRequest, WholesaleProperty
    from app.routers.wholesale_router import enrich_property
    from app.services import wholesale_service as svc
    wp = WholesaleProperty(organization_id=sample_org.id, street_address="2 Guard Test St", city="Dallas",
                           state="TX", zip_code="75216", owner_name="HOGAN KRISTINA L", is_test=True)
    db_session.add(wp)
    db_session.commit()
    settings = svc.resolve_settings(db_session, sample_org.id)

    class Fake:
        key, billable = "fake_vendor", False

        def __init__(self, name):
            self.name = name

        def lookup(self, data):
            return res(self.name, phones=("2145550177",))
    leads_before = db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count()
    out = enrich_property(db_session, sample_org.id, sample_advisor, settings, Fake("Pat Example"), wp)
    assert out["status"] == WE.STATUS_NO_MATCH and out["match_quality"] == G.NO_VERIFIED_MATCH
    out = enrich_property(db_session, sample_org.id, sample_advisor, settings, Fake("Bob Hogan"), wp)
    assert out["match_quality"] == G.PROBABLE
    db_session.commit()
    assert db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count() == leads_before
    rows = db_session.query(WholesaleEnrichmentRequest).filter(WholesaleEnrichmentRequest.property_id == wp.id).all()
    assert len(rows) == 2 and all(r.error for r in rows)
    out = enrich_property(db_session, sample_org.id, sample_advisor, settings, Fake("Kristina Hogan"), wp)
    db_session.commit()
    assert out["match_quality"] == G.MATCHED
    lead = db_session.query(Lead).filter(Lead.organization_id == sample_org.id).order_by(Lead.created_at.desc()).first()
    assert lead is not None and "2145550177" in (lead.phone or "")
