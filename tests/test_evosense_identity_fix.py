"""A person corrects a property's identifiers from an official record.

  * audited: before / after, the record's web address, the note;
  * a corrected account or address that belongs to ANOTHER property is
    refused (a merge is not an edit);
  * the free county lookups then answer for the corrected house - the owner
    comes from the county record, never from this form - even if the same
    lookup found nothing an hour earlier.
"""
import pytest

from app.models.evosense_models import EvoSenseEvent, EvoSenseOwnership, EvoSenseProperty
from app.services.evosense import hunt as HU
from app.services.evosense import identity_fix as IF
from app.services.evosense import providers as PV

LIST = ("record_id,street_address,city,state,zip_code,county\n"
        "top100-2010:a,2848 CAMINATA,Grand Prairie,TX,75054,Dallas\n"
        "top100-2010:b,9 OTHER ST,Arlington,TX,76001,Tarrant\n")


@pytest.fixture()
def props(db_session, sample_org, sample_advisor):
    HU.import_csv(db_session, sample_org.id, LIST, user=sample_advisor)
    db_session.commit()
    q = db_session.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == sample_org.id)
    return {p.street_address.upper(): p for p in q.all()}


def test_correction_is_audited_and_the_county_source_supplies_the_owner(db_session, sample_org, sample_advisor,
                                                                         props, monkeypatch):
    seen = []

    def lookup_many(self, targets):
        seen.extend(targets)
        t = targets[0]
        return {"results": {t["id"]: {"source_reference": "TAD:40544141", "street_address": "2848 CAMINATA DR",
                                      "city": "Grand Prairie", "state": "TX", "zip_code": "75054",
                                      "county": "Tarrant", "parcel_apn": "40544141",
                                      "owner": {"name": "SAMPLE COUNTY OWNER", "mailing_street": "1 MAIN ST",
                                                "mailing_city": "Dallas", "mailing_state": "TX",
                                                "mailing_zip": "75201"},
                                      "signals": [], "_source_url": "https://www.tad.org"}}, "errors": {}}
    monkeypatch.setattr(PV.TadSource, "lookup_many", lookup_many)
    monkeypatch.setattr(PV, "health_state", lambda *a, **k: "connected")
    from app.services.evosense import common as C
    monkeypatch.setattr(C, "H_CONNECTED", "connected", raising=False)
    cfg = PV.config(db_session, sample_org.id, "tad")
    cfg.enabled = True
    db_session.commit()
    p = props["2848 CAMINATA"]
    out = IF.correct(db_session, sample_org.id, p, {"county": "Tarrant", "parcel_apn": "40544141",
                                                    "street_address": "2848 Caminata Dr"},
                     user=sample_advisor, evidence_url="https://tarrant.prodigycad.com/property-search",
                     note="TAD record; the list had Dallas County")
    assert out["changed"]["county"] == {"was": "Dallas", "now": "Tarrant"}
    ev = db_session.query(EvoSenseEvent).filter_by(property_id=p.id, action="identity.corrected").one()
    assert "prodigycad" in ev.details and "had Dallas" in ev.details
    assert seen and seen[0]["parcel_apn"] == "40544141" and seen[0]["county"] == "Tarrant"
    link = db_session.query(EvoSenseOwnership).filter_by(property_id=p.id, is_current=True).one()
    assert link.source == "tad"                                   # the county's record, not the form


def test_an_account_owned_by_another_property_is_refused(db_session, sample_org, sample_advisor, props):
    other = props["9 OTHER ST"]
    IF.correct(db_session, sample_org.id, other, {"parcel_apn": "40544141"}, user=sample_advisor,
               evidence_url="https://www.tad.org/x", note=None, lookup=False)
    with pytest.raises(IF.IdentityConflict):
        IF.correct(db_session, sample_org.id, props["2848 CAMINATA"], {"county": "Tarrant", "parcel_apn": "40544141"},
                   user=sample_advisor, evidence_url="https://www.tad.org/x", note=None, lookup=False)


def test_evidence_and_zip_are_required_to_be_real(db_session, sample_org, sample_advisor, props):
    p = props["2848 CAMINATA"]
    with pytest.raises(ValueError):
        IF.correct(db_session, sample_org.id, p, {"zip_code": "750"}, user=sample_advisor,
                   evidence_url="https://x.gov", note=None, lookup=False)
    with pytest.raises(ValueError):
        IF.correct(db_session, sample_org.id, p, {"zip_code": "75054"}, user=sample_advisor,
                   evidence_url="", note=None, lookup=False)


def test_api_is_admin_only(client, admin_auth_headers, auth_headers, props):
    pid = props["2848 CAMINATA"].id
    body = {"zip_code": "75052", "evidence_url": "https://www.dallascad.org/x"}
    r = client.post("/wholesale/evosense/properties/%s/identity" % pid, headers=auth_headers, json=body)
    assert r.status_code in (403, 404)
    r = client.post("/wholesale/evosense/properties/%s/identity" % pid, headers=admin_auth_headers, json=body)
    assert r.status_code == 200, r.text[:300]
    assert r.json()["changed"]["zip_code"]["now"] == "75052"


# ── a lookup's answer never creates a second property for the house ────────

COLLIN = ("record_id,street_address,city,state,zip_code,county\n"
          "top100-2010:c,1301 STARPOINT,Wylie,TX,75098,Collin\n")


def _collin_rec(street="1301 STARPOINT LN", zip_code="75098", geo="R-4128-002-0040-1"):
    return {"source_reference": "CCAD:%s:2026" % geo, "street_address": street, "city": "Wylie", "state": "TX",
            "zip_code": zip_code, "county": "Collin", "parcel_apn": geo,
            "owner": {"name": "SAMPLE COLLIN OWNER", "mailing_street": "1301 STARPOINT LN",
                      "mailing_city": "Wylie", "mailing_state": "TX", "mailing_zip": "75098"},
            "signals": [], "_source_url": "https://data.texas.gov/resource/nne4-8riu.json"}


@pytest.fixture()
def starpoint(db_session, sample_org, sample_advisor):
    HU.import_csv(db_session, sample_org.id, COLLIN, user=sample_advisor)
    db_session.commit()
    return db_session.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == sample_org.id).one()


def test_a_lookup_answer_with_a_suffix_and_another_account_format_attaches_to_its_house(db_session, sample_org,
                                                                                       starpoint):
    from app.services.evosense import ingest as IN
    out, prop = IN.ingest(db_session, sample_org.id, PV.PROVIDERS["collin_cad"], "ASSESSOR", _collin_rec(),
                          target=starpoint)
    db_session.commit()
    assert out == "merged" and prop.id == starpoint.id
    assert db_session.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == sample_org.id).count() == 1
    assert db_session.query(EvoSenseOwnership).filter_by(property_id=starpoint.id, is_current=True).count() == 1


def test_a_lookup_answer_for_another_house_waits_in_review_never_new(db_session, sample_org, starpoint):
    from app.services.evosense import ingest as IN
    out, prop = IN.ingest(db_session, sample_org.id, PV.PROVIDERS["collin_cad"], "ASSESSOR",
                          _collin_rec(street="1303 STARPOINT LN", geo="R-9"), target=starpoint)
    db_session.commit()
    assert out == "review" and prop is None
    assert db_session.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == sample_org.id).count() == 1


def test_a_duplicate_for_the_same_house_folds_in_and_its_owner_moves(db_session, sample_org, sample_advisor,
                                                                     starpoint):
    from app.services.evosense import ingest as IN
    out, dup = IN.ingest(db_session, sample_org.id, PV.PROVIDERS["collin_cad"], "ASSESSOR", _collin_rec())
    db_session.commit()
    assert out == "created" and dup.id != starpoint.id                       # the production failure, reproduced
    res = IF.merge_duplicate(db_session, sample_org.id, starpoint, dup, user=sample_advisor,
                             reason="Same house: Collin CAD R-4128-002-0040-1")
    assert res["observations_moved"] == 1
    assert db_session.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == sample_org.id).count() == 1
    link = db_session.query(EvoSenseOwnership).filter_by(property_id=starpoint.id, is_current=True).one()
    assert link.source == "collin_cad"
    db_session.refresh(starpoint)
    assert starpoint.parcel_apn == "R-4128-002-0040-1"                      # the county's own account format
    ev = db_session.query(EvoSenseEvent).filter_by(property_id=starpoint.id, action="identity.merged").one()
    assert dup.id in ev.details


def test_merge_refuses_a_different_house(db_session, sample_org, sample_advisor, starpoint):
    from app.services.evosense import ingest as IN
    _, other = IN.ingest(db_session, sample_org.id, PV.PROVIDERS["collin_cad"], "ASSESSOR",
                         _collin_rec(street="99 ELSEWHERE LN", geo="R-77"))
    db_session.commit()
    with pytest.raises(IF.MergeRefused):
        IF.merge_duplicate(db_session, sample_org.id, starpoint, other, user=sample_advisor, reason="not the same")
