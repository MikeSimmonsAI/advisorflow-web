"""Tax-foreclosure lawsuits & sales (taxsales.lgbs.com) and the DCAD lookup by
account that gives those Dallas properties their owner of record."""
from datetime import datetime, timedelta

import pytest

from app.models.evosense_models import EvoSenseObservation, EvoSenseProperty, EvoSenseSignal
from app.services.evosense import providers as PV
from app.services.evosense.sources import base as B
from app.services.evosense.sources import lgbs as LG
from app.services.evosense.sources.dallas import DcadReader
from tests.test_evosense_dfw_sources import (_admin, _enable, _pilot, csv_text, no_network,  # noqa: F401
                                             ok, write_zip)

TODAY = datetime.utcnow()
FUT = (TODAY + timedelta(days=24)).strftime("%Y-%m-%dT10:00:00")
PAST = (TODAY - timedelta(days=4)).strftime("%Y-%m-%dT10:00:00")


def row(uid, acct, addr, city="DALLAS", county="DALLAS COUNTY", sale_type="FUTURE SALE",
        status="Available for Future Sale", sale_date=None, notes=""):
    return {"uid": uid, "sale_id": uid, "county": county, "state": "TX", "cause_nbr": "TX-24-%05d" % uid,
            "sale_type": sale_type, "status": status, "sale_date": sale_date, "account_nbr": acct,
            "prop_address_one": addr, "prop_address_two": "", "prop_city": city, "prop_state": "TX",
            "prop_zipcode": "75210", "value": "120000.00", "minimum_bid": "18400.55", "sale_notes": notes,
            "geometry": {"type": "Point", "coordinates": [-96.75, 32.76]}}


DALLAS = [
    row(1, "00000555000000000", "100 HELD ST"),                                         # kept
    row(2, "00000555100000000", "200 SOON ST", sale_type="SALE", status="Scheduled for Online Auction",
        sale_date=FUT),                                                                 # kept, first
    row(3, "00000555200000000", "300 GONE ST", sale_type="STRUCK OFF"),                  # county owns it
    row(4, "00000555300000000", "400 PAID ST", sale_type="SALE", status="Cancelled"),    # paid off
    row(5, "00000555400000000", "500 SOLD ST", sale_type="SALE", status="Sold"),
    row(6, "00000555500000000", "600 PAST ST", sale_type="SALE", status="Scheduled for Online Auction",
        sale_date=PAST),                                                                # sale day passed
    row(7, "00000555600000000", "", sale_type="FUTURE SALE"),                            # no address
]
TARRANT = [row(8, "04669029", "2656 S PIPELINE RD W", city="EULESS", county="TARRANT COUNTY")]


@pytest.fixture()
def lgbs_api(monkeypatch, no_network):  # noqa: F811
    calls = []
    prior = B.get_json

    def get_json(url, params=None):
        if url.startswith(LG.API):
            params = params or {}
            calls.append(params)
            rows = {"DALLAS COUNTY": DALLAS, "TARRANT COUNTY": TARRANT}.get(params.get("county"), [])
            off, lim = int(params.get("offset", 0)), int(params.get("limit", 200))
            page = rows[off:off + lim]
            return {"count": len(rows), "next": "x" if off + lim < len(rows) else None, "results": page}
        return prior(url, params)
    monkeypatch.setattr(B, "get_json", get_json)
    return calls


def test_keeps_only_properties_the_owner_still_holds(lgbs_api):
    res = LG.LgbsTaxSaleReader().discover(limit=50, counties=("dallas", "tarrant"))
    addrs = [r["street_address"] for r in res["records"]]
    assert addrs[0] == "200 SOON ST"                       # scheduled sale first
    assert set(addrs) == {"200 SOON ST", "100 HELD ST", "2656 S PIPELINE RD W"}
    st = res["stats"]
    assert st["struck_off"] == 1 and st["cancelled"] == 1 and st["sold"] == 1
    assert st["sale_date_passed"] == 1 and st["no_address_or_account"] == 1


def test_record_carries_the_judgment_as_evidence(lgbs_api):
    res = LG.LgbsTaxSaleReader().discover(limit=50, counties=("dallas", "tarrant"))
    soon = next(r for r in res["records"] if r["street_address"] == "200 SOON ST")
    sig = {s["type"]: s for s in soon["signals"]}
    assert set(sig) == {"TAX_SUIT", "TAX_DELINQUENT"}
    assert "cause TX-24-00002" in sig["TAX_SUIT"]["value"] and "tax sale scheduled" in sig["TAX_SUIT"]["value"]
    assert "minimum bid $18,401" in sig["TAX_SUIT"]["value"]
    assert soon["county"] == "Dallas" and soon["parcel_apn"] == "00000555100000000"
    assert soon["latitude"] == 32.76 and soon["_raw"]["cause_nbr"] == "TX-24-00002"
    tar = next(r for r in res["records"] if r["county"] == "Tarrant")
    assert tar["parcel_apn"] == "4669029"                  # TAD's form: no leading zeros
    assert tar["city"] == "Euless"


def test_window_rotates_so_every_listing_is_reached(lgbs_api):
    seen = set()
    for day in range(4):
        res = LG.LgbsTaxSaleReader().discover(limit=2, counties=("dallas", "tarrant"), rotate=day)
        assert len(res["records"]) == 2
        assert res["records"][0]["street_address"] == "200 SOON ST"     # scheduled always in
        seen |= {r["street_address"] for r in res["records"]}
    assert seen == {"200 SOON ST", "100 HELD ST", "2656 S PIPELINE RD W"}


def test_source_skips_strategies_outside_its_counties(lgbs_api):
    src = PV.PROVIDERS["lgbs_tax_sales"]
    assert len(src.search("TAX", {"states": ["OK"], "limit": 5})) == 0
    assert len(src.search("TAX", {"states": ["TX"], "counties": ["Harris"], "limit": 5})) == 0
    got = src.search("TAX", {"states": ["TX"], "counties": ["Tarrant County"], "limit": 5})
    assert [r["county"] for r in got] == ["Tarrant"]
    assert src.discovery and src.connector_kind == "real" and not src.costs


def _dcad_zip(tmp_path):
    res_h = ["ACCOUNT_NUM", "APPRAISAL_YR", "CDU_RATING_DESC", "YR_BUILT", "TOT_LIVING_AREA_SF",
             "NUM_FULL_BATHS", "NUM_BEDROOMS", "DEPRECIATION_PCT"]
    info_h = ["ACCOUNT_NUM", "APPRAISAL_YR", "OWNER_NAME1", "OWNER_NAME2", "EXCLUDE_OWNER",
              "OWNER_ADDRESS_LINE1", "OWNER_ADDRESS_LINE2", "OWNER_ADDRESS_LINE3", "OWNER_CITY",
              "OWNER_STATE", "OWNER_ZIPCODE", "STREET_NUM", "STREET_HALF_NUM", "FULL_STREET_NAME",
              "UNIT_ID", "PROPERTY_CITY", "PROPERTY_ZIPCODE", "DEED_TXFR_DATE"]
    val_h = ["ACCOUNT_NUM", "APPRAISAL_YR", "TOT_VAL", "IMPR_VAL", "LAND_VAL", "SPTD_CODE", "CITY_JURIS_DESC"]
    y = str(TODAY.year)
    return write_zip(tmp_path / "dcad_lookup.zip", {
        "RES_DETAIL.CSV": csv_text(res_h, [
            {"ACCOUNT_NUM": "00000555000000000", "APPRAISAL_YR": y, "CDU_RATING_DESC": "AVERAGE",
             "YR_BUILT": "1951", "TOT_LIVING_AREA_SF": "1120", "NUM_FULL_BATHS": "1", "NUM_BEDROOMS": "3"}]),
        "ACCOUNT_APPRL_YEAR.CSV": csv_text(val_h, [
            {"ACCOUNT_NUM": "00000555000000000", "APPRAISAL_YR": y, "TOT_VAL": "120000", "IMPR_VAL": "80000",
             "LAND_VAL": "40000", "SPTD_CODE": "A11"},
            {"ACCOUNT_NUM": "00000555100000000", "APPRAISAL_YR": y, "TOT_VAL": "30000", "LAND_VAL": "30000",
             "SPTD_CODE": "C11"}]),
        "ACCOUNT_INFO.CSV": csv_text(info_h, [
            {"ACCOUNT_NUM": "00000555000000000", "OWNER_NAME1": "HELD HANNAH", "EXCLUDE_OWNER": "N",
             "OWNER_ADDRESS_LINE1": "77 FAR RD", "OWNER_CITY": "TULSA", "OWNER_STATE": "OKLAHOMA",
             "OWNER_ZIPCODE": "74101", "STREET_NUM": "100", "FULL_STREET_NAME": "HELD ST",
             "PROPERTY_CITY": "DALLAS", "PROPERTY_ZIPCODE": "75210", "DEED_TXFR_DATE": "03/15/1994"},
            {"ACCOUNT_NUM": "00000555100000000", "OWNER_NAME1": "SOON SAM", "EXCLUDE_OWNER": "N",
             "OWNER_ADDRESS_LINE1": "200 SOON ST", "OWNER_CITY": "DALLAS", "OWNER_STATE": "TEXAS",
             "OWNER_ZIPCODE": "75210", "STREET_NUM": "200", "FULL_STREET_NAME": "SOON ST",
             "PROPERTY_CITY": "DALLAS", "PROPERTY_ZIPCODE": "75210"}]),
        "APPLIED_STD_EXEMPT.CSV": csv_text(["ACCOUNT_NUM", "APPRAISAL_YR", "HOMESTEAD_EFF_DT"], []),
    })


def test_dcad_lookup_by_account(tmp_path, monkeypatch, no_network):  # noqa: F811
    monkeypatch.setenv("EVOSENSE_SRC_DCAD_PATH", _dcad_zip(tmp_path))
    res = DcadReader().lookup_many(["00000555000000000", "00000555100000000", "NOT-A-DCAD-ACCT"])
    recs = res["records"]
    assert set(recs) == {"00000555000000000", "00000555100000000"}       # unknown account: no record
    held = recs["00000555000000000"]
    assert held["owner"]["name"] == "HELD HANNAH" and held["owner"]["mailing_state"] == "OK"
    assert held["square_feet"] == 1120 and held["year_built"] == 1951 and held["bedrooms"] == 3
    assert held["appraisal"]["value"] == 120000 and held["property_type"] == "single_family"
    land = recs["00000555100000000"]
    assert land["property_type"] is None and land["square_feet"] is None   # no building row: not guessed
    src = PV.PROVIDERS["dcad"]
    assert src.applies({"county": "Dallas", "parcel_apn": "x"}) and not src.applies({"county": "Tarrant",
                                                                                      "parcel_apn": "x"})


def test_hunt_finds_tax_sale_properties_and_joins_the_owner(client, db_session, sample_org, tmp_path,
                                                            monkeypatch, lgbs_api):
    monkeypatch.setenv("EVOSENSE_SRC_DCAD_PATH", _dcad_zip(tmp_path))
    _, h = _admin(db_session, sample_org)
    _enable(client, h, "lgbs_tax_sales", "dcad")
    s = _pilot(client, h, counties=["Dallas"], property_types=["single_family", "land"],
               min_opportunity_score=10)
    out = ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    assert out["status"] == "succeeded", out
    assert out["counts"]["sources"]["lgbs_tax_sales"]["records"] == 2
    db = db_session
    p = (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == sample_org.id,
                                           EvoSenseProperty.parcel_apn == "00000555000000000").one())
    kinds = {o.provider_key for o in db.query(EvoSenseObservation)
             .filter(EvoSenseObservation.property_id == p.id).all()}
    assert {"lgbs_tax_sales", "dcad"} <= kinds                     # one property, both sources
    assert p.square_feet == 1120 and p.appraisal_value == 120000
    sigs = {x.signal_type for x in db.query(EvoSenseSignal)
            .filter(EvoSenseSignal.property_id == p.id, EvoSenseSignal.active.is_(True)).all()}
    assert {"TAX_SUIT", "TAX_DELINQUENT"} <= sigs
    assert (p.opportunity_score or 0) > 20                         # absentee / out-of-state from the join
