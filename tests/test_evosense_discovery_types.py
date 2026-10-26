"""Discovery beyond houses (2026-10-25): the Dallas tax roll, the Travis
delinquent file, the TxGIO parcel roll and DCAD now keep every property type
the hunting strategy asks for, by the record's OWN category code.

  * each widened reader returns residential, commercial, agricultural, land
    and multifamily records when the file holds them, keeping the county code;
  * minerals / utilities / personal property and rows with no code are never
    imported, and a missing code is never taken for a house;
  * "Houses only" strategies still get residential only;
  * the same parcel read twice is one property, one observation chain;
  * absentee / out-of-state never score for land, commercial, industrial,
    agricultural or multifamily (normal for those types);
  * the coverage report says what the readers actually counted.
"""
import io
import json
import zipfile
from datetime import datetime

import pytest

from app.models.evosense_models import EvoSenseProperty, EvoSenseRun
from app.services.evosense import ingest as IN
from app.services.evosense import property_class as PCL
from app.services.evosense import providers as PV
from app.services.evosense import scoring as SC
from app.services.evosense.sources import base as B
from app.services.evosense.sources import dallas_trw as T
from app.services.evosense.sources import travis_delinquent as TD
from app.services.evosense.sources import txgio_roll as R

from tests.test_evosense_dallas_trw import MEMBER, line
from tests.test_evosense_travis_delinquent import CSV as TRAVIS_CSV, HEADER as TRAVIS_HEADER, row as trow
from tests.test_evosense_txgio_roll import dbf, row as grow

TODAY = datetime(2026, 10, 11)
ALL = PCL.TARGET_CATEGORIES


@pytest.fixture
def trw(tmp_path, monkeypatch):
    rows = [line(account="H1", cat="A11"), line(account="M1", cat="B11"), line(account="L1", cat="C11"),
            line(account="G1", cat="D10"), line(account="F1", cat="F10"), line(account="I1", cat="F20"),
            line(account="X1", cat="J30"),                     # a utility account: never a target
            line(account="N1", cat="    ")]                    # no code: never assumed to be a house
    p = tmp_path / "trwfile.1.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(MEMBER, "\r\n".join(rows) + "\r\n")
    monkeypatch.setenv("EVOSENSE_SRC_DALLAS_TRW_PATH", str(p))
    return p


def test_dallas_tax_roll_returns_every_target_type_with_its_own_code(trw):
    res = T.TrwReader().discover(limit=20, today=TODAY, categories=ALL)
    got = {r["source_reference"].split(":")[1]: r for r in res["records"]}
    assert set(got) == {"H1", "M1", "L1", "G1", "F1", "I1"}
    cats = {a: PCL.reader_category(r["_raw"]["acc"]["first"]["category"])[0] for a, r in got.items()}
    assert cats == {"H1": "residential", "M1": "multifamily", "L1": "land", "G1": "agricultural",
                    "F1": "commercial", "I1": "industrial"}
    assert got["F1"]["_raw"]["acc"]["first"]["category"] == "F10"           # the county's code, kept as read
    st = res["stats"]
    assert st["excluded_rows_not_target"] == 1 and st["excluded_rows_no_code"] == 1
    assert st["kept_commercial"] == 1 and st["imported_agricultural"] == 1


def test_a_houses_only_hunt_still_gets_houses_only(trw):
    res = T.TrwReader().discover(limit=20, today=TODAY, categories=("residential",))
    assert [r["source_reference"] for r in res["records"]] == ["DALTRW:H1"]
    assert res["stats"]["excluded_rows_commercial"] == 1 and res["stats"]["excluded_rows_land"] == 1


def test_classification_reads_the_tax_roll_code_and_a_missing_code_stays_unknown():
    trw_ev = [{"provider": "dallas_trw", "payload": {"_raw": {"acc": {}}, "category": "D10"}, "raw": None}]
    assert PCL.classify(trw_ev)["category"] == "agricultural"
    assert PCL.classify([{"provider": "dallas_trw", "payload": {}, "raw": None}])["category"] == "unknown"
    assert PCL.reader_category("") == (None, None, "no_code")
    assert PCL.reader_category("J3")[2] == "not_target"


def test_conflicting_county_codes_need_review():
    ev = [{"provider": "dcad", "payload": {"_raw": {"SPTD_CODE": "F10"}}, "raw": None},
          {"provider": "dallas_trw", "payload": {"category": "A11"}, "raw": None}]
    r = PCL.classify(ev)
    assert r["category"] == "unknown" and "disagree" in r["conflict"]


def test_travis_file_brings_land_commercial_and_agricultural_when_asked():
    csv = "\r\n".join([TRAVIS_HEADER, trow("0101", cls="D1"), trow("0102", cls="B1"), trow("0103", cls="E1"),
                       trow("0104", cls="L1"), trow("0105", cls="")]) + "\r\n"
    res = TD.TravisDelinquentReader().discover(limit=10, today=datetime(2026, 10, 22), text=csv, categories=ALL)
    got = {r["parcel_apn"]: r for r in res["records"]}
    assert set(got) == {"0101", "0102", "0103"}                  # personal property and no-code rows left out
    assert got["0101"]["property_type"] == "agricultural" and got["0101"]["_evidence"]["property_class"] == "D1"
    assert got["0102"]["property_type"] == "multifamily" and got["0103"]["property_type"] == "land"
    assert res["stats"]["found_not_target"] == 1 and res["stats"]["found_no_code"] == 1


@pytest.fixture
def parcels(monkeypatch):
    rows = [grow("1", "SMITH JOHN EST OF", "10", "FARM RD", "10 FARM RD", stat="D1"),       # estate ag tract
            grow("2", "AWAY INVESTOR", "12", "FARM RD", "9 ELM AVE", stat="C1"),           # absentee lot: not kept
            grow("3", "JONES MARY ESTATE", "14", "MAIN ST", "1 LAKE DR", stat="F1"),        # estate commercial
            grow("4", "AWAY LANDLORD", "16", "OAK ST", "9 ELM AVE", stat="A1"),            # absentee house: kept
            grow("5", "ESTATE OF PAT LEE", "18", "OAK ST", "18 OAK ST", stat=""),          # no code at all
            grow("6", "ESTATE OF RAY", "20", "PINE ST", "20 PINE ST", stat="B1")]           # estate apartments

    def open_zip(key, url, allow_download=True):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("p.dbf", dbf(rows))
        buf.seek(0)
        return B.OpenedZip(zipfile.ZipFile(buf), {"url": url, "mode": "test"})
    monkeypatch.setattr(B, "open_zip", open_zip)


def test_parcel_roll_non_house_types_come_in_only_on_an_estate_owner(parcels):
    res = R.TxRollReader().discover("Ellis", limit=10, categories=ALL)
    got = {r["street_address"]: r for r in res["records"]}
    assert set(got) == {"10 FARM RD", "14 MAIN ST", "16 OAK ST", "20 PINE ST"}
    assert got["10 FARM RD"]["property_type"] == "agricultural" and got["10 FARM RD"]["_evidence"]["land_use"] == "D1"
    assert got["14 MAIN ST"]["property_type"] == "commercial" and got["20 PINE ST"]["property_type"] == "multifamily"
    assert got["16 OAK ST"]["property_type"] == "single_family"
    assert res["stats"]["found_no_code"] == 1 and res["stats"]["kept_agricultural"] == 1
    houses = R.TxRollReader().discover("Ellis", limit=10)                     # default: residential only
    assert [r["street_address"] for r in houses["records"]] == ["16 OAK ST"]


def test_the_strategy_type_list_decides_the_categories():
    assert PCL.query_categories({"houses_only": True, "property_types": ["land"]}) == ("residential",)
    assert PCL.query_categories({"property_types": ["single_family", "land", "commercial"]}) == \
        ("residential", "commercial", "land")
    assert PCL.query_categories({}) == ALL
    assert "agricultural" in __import__("app.services.evosense.strategy", fromlist=["x"]).PROPERTY_TYPES


def test_the_same_parcel_read_twice_is_one_property(db_session, sample_org, trw):
    prov = PV.PROVIDERS["dallas_trw"]
    rec = [r for r in T.TrwReader().discover(limit=20, today=TODAY, categories=ALL)["records"]
           if r["source_reference"] == "DALTRW:F1"][0]
    o1, p1 = IN.ingest(db_session, sample_org.id, prov, "TAX", dict(rec), is_test=True)
    o2, p2 = IN.ingest(db_session, sample_org.id, prov, "TAX", dict(rec), is_test=True)
    db_session.flush()
    assert p1.id == p2.id
    assert db_session.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == sample_org.id,
                                                     EvoSenseProperty.parcel_apn == p1.parcel_apn).count() == 1


def _stacked(*types):
    return [{"signal_type": t, "freshness": "current", "sources": ["dcad"], "record_sources": ["dcad"],
             "evidence": [], "observed_at": None} for t in types]


class _P:
    def __init__(self, **kw):
        base = dict(property_type=None, property_category=None, property_subtype=None, property_class_code=None,
                    equity_pct=None, ownership_years=None, appraisal_improvement_value=None, square_feet=None,
                    year_built=None, estimated_value=None, estimated_value_source=None, appraisal_value=200000,
                    appraisal_year=2026, appraisal_source="county", equity_basis=None, has_conflicts=False,
                    street_address="1 A ST", zip_code="75201", state="TX", county="Dallas", city="Dallas",
                    valuation_detail=None, appraisal_land_value=None, mortgage_balance=None)
        base.update(kw)
        self.__dict__.update(base)


def test_absentee_is_not_scored_for_commercial_land_or_apartments_but_is_for_a_house():
    for cat in ("commercial", "land", "multifamily", "agricultural", "industrial"):
        r = SC.property_opportunity(_P(property_category=cat), _stacked("ABSENTEE_OWNER", "TAX_DELINQUENT"), None)
        absentee = [f for f in r["factors"] if "Absentee" in f["label"]]
        assert absentee and absentee[0]["points"] == 0 and "normal for" in absentee[0]["label"], cat
    house = SC.property_opportunity(_P(property_category="residential"), _stacked("ABSENTEE_OWNER"), None)
    assert any(f["points"] == 15 for f in house["factors"])
    unknown = SC.property_opportunity(_P(property_category="unknown"), _stacked("ABSENTEE_OWNER"), None)
    assert any(f["points"] == 15 for f in unknown["factors"])          # no assumption either way


def test_houses_only_skips_by_the_county_category_when_the_type_field_is_empty():
    assert SC.land_only_reason(_P(property_category="agricultural", property_class_code="D1"))
    assert SC.land_only_reason(_P(property_category="residential")) is None


def test_coverage_report_says_what_the_readers_actually_counted(db_session, sample_org):
    run = EvoSenseRun(organization_id=sample_org.id, trigger="test", status="succeeded",
                      counts=json.dumps({"sources": {"dallas_trw": {"records": 3, "found_commercial": 40,
                                                                    "kept_commercial": 31, "imported_commercial": 2,
                                                                    "found_residential": 900, "kept_residential": 800,
                                                                    "excluded_rows_land": 12}}}))
    db_session.add(run)
    db_session.flush()
    rows = {(r["source"], r["category"]): r for r in PCL.checked_coverage(db_session, sample_org.id)}
    com = rows[("dallas_trw", "commercial")]
    assert com["identified"] == 40 and com["eligible"] == 31 and com["imported_last_run"] == 2
    assert rows[("dallas_trw", "agricultural")]["status"] in (PCL.CHECK_NO_MATCH, PCL.CHECK_BLOCKED)
    assert rows[("dcad", "commercial")]["status"] == PCL.CHECK_UNSUPPORTED
    assert rows[("travis_delinquent", "land")]["status"] in (PCL.CHECK_NOT_CHECKED, PCL.CHECK_BLOCKED)
