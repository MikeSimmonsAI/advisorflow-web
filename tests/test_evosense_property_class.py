"""Property classification from the county's own category code. Synthetic records."""
import json

import pytest

from app.models.evosense_models import EvoSenseObservation, EvoSenseProperty
from app.services.evosense import property_class as PC


@pytest.mark.parametrize("code,expect", [
    ("A1", ("residential", "single_family")), ("A11", ("residential", "single_family")),
    ("A2", ("residential", "manufactured_home")), ("M1", ("residential", "manufactured_home")),
    ("B1", ("multifamily", "apartments")), ("B2", ("residential", "duplex_fourplex")), ("B", ("multifamily", None)),
    ("C1", ("land", "vacant_lot")), ("E1", ("land", "undeveloped_acreage")),
    ("D1", ("agricultural", "ag_acreage")), ("D2", ("agricultural", "ag_improvements")),
    ("F1", ("commercial", None)), ("F10", ("commercial", None)), ("F2", ("industrial", None)),
    ("F20", ("industrial", None)), ("J4", ("unknown", None)), ("X", ("unknown", None)),
    ("", None), ("9", None),
])
def test_state_codes(code, expect):
    assert PC.from_state_code(code) == expect


@pytest.mark.parametrize("words,expect", [
    ("Mixed Use", ("mixed_use", None)), ("Self Storage", ("specialty", "self_storage")),
    ("RV Park", ("specialty", "rv_park")), ("Office", ("commercial", "office")),
    ("Warehouse", ("industrial", "warehouse")), ("Ranch", ("agricultural", "ranch")),
    ("Single Family", ("residential", "single_family")), ("Vacant Land", ("land", None)),
    ("Apartment Complex", ("multifamily", None)), ("something odd", None),
])
def test_words_from_a_list_or_a_person(words, expect):
    assert PC.from_text(words) == expect


def ev(provider, evidence=None, raw=None, **payload):
    return {"provider": provider, "payload": dict(payload, evidence=evidence or {}), "raw": raw}


def test_the_appraisal_district_code_wins_over_a_list_and_the_old_type():
    r = PC.classify([ev("csv_import", {"list_claims": {"property_type": "Single Family"}}),
                     ev("dcad", raw=json.dumps({"ACCOUNT_APPRL_YEAR": {"SPTD_CODE": "F10"}}))],
                    legacy_type="single_family")
    assert (r["category"], r["confidence"], r["source"], r["code"]) == ("commercial", "county_record", "dcad", "F10")


def test_two_county_records_that_disagree_need_review():
    r = PC.classify([ev("tad", {"state_use_code": "A1"}), ev("txgio_roll", {"land_use": "F1"})])
    assert r["category"] == "unknown" and "disagree" in r["conflict"]


def test_no_code_means_unknown_never_residential_by_default():
    assert PC.classify([])["category"] == "unknown"
    assert PC.classify([ev("census_geocoder")])["category"] == "unknown"
    old = PC.classify([], legacy_type="single_family")
    assert old["category"] == "residential" and old["confidence"] == "source_type"   # labelled as the weakest basis


def test_a_person_can_state_mixed_use_and_it_is_kept_with_who_said_it(db_session, sample_org, sample_advisor):
    p = EvoSenseProperty(organization_id=sample_org.id, street_address="1 Mixed Synth St", city="Dallas",
                         state="TX", is_test=True, status="new")
    db_session.add(p)
    db_session.flush()
    db_session.add(EvoSenseObservation(organization_id=sample_org.id, property_id=p.id, provider_key="dcad",
                                       connector_kind="real", capability="ASSESSOR", source_reference="dcad:x",
                                       payload=json.dumps({"evidence": {}}),
                                       raw_payload=json.dumps({"ACCOUNT_APPRL_YEAR": {"SPTD_CODE": "F10"}})))
    db_session.flush()
    assert PC.backfill(db_session, sample_org.id) == 1
    assert p.property_category == "commercial" and p.property_class_code == "F10"
    v = PC.set_by_person(db_session, p, "mixed_use", "retail_with_apartments", user=sample_advisor, note="site visit")
    assert v["category"] == "mixed_use" and v["confidence"] == "person"
    with pytest.raises(ValueError):
        PC.set_by_person(db_session, p, "land", "self_storage", user=sample_advisor)
    assert PC.backfill(db_session, sample_org.id) == 0                     # nothing left unclassified
