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
HARRIS = [row(9, "0761230000012", "900 BAYOU ST", city="HOUSTON", county="HARRIS COUNTY"),
          row(10, "0761230000013", "910 BAYOU ST", city="HOUSTON", county="HARRIS COUNTY", sale_type="STRUCK OFF")]
BEXAR = [row(11, "05123000040", "77 ALAMO WAY", city="SAN ANTONIO", county="BEXAR COUNTY")]


@pytest.fixture()
def lgbs_api(monkeypatch, no_network):  # noqa: F811
    calls = []
    prior = B.get_json

    def get_json(url, params=None):
        if url.startswith(LG.API):
            params = params or {}
            calls.append(params)
            by = {"DALLAS COUNTY": DALLAS, "TARRANT COUNTY": TARRANT, "HARRIS COUNTY": HARRIS,
                  "BEXAR COUNTY": BEXAR}
            rows = by.get(params.get("county"), []) if params.get("county") else DALLAS + TARRANT + HARRIS + BEXAR
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
    assert set(sig) == {"TAX_SUIT", "TAX_DELINQUENT", "TAX_SALE"}      # a dated sale is its own signal
    assert sig["TAX_SALE"]["sale_date"] and sig["TAX_SALE"]["foreclosure_type"] == "tax_foreclosure_sale"
    held = next(r for r in res["records"] if r["street_address"] == "100 HELD ST")
    assert "TAX_SALE" not in {s["type"] for s in held["signals"]}       # no date: no sale signal
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


def test_source_skips_strategies_outside_texas_and_serves_any_texas_county(lgbs_api):
    src = PV.PROVIDERS["lgbs_tax_sales"]
    assert len(src.search("TAX", {"states": ["OK"], "limit": 5})) == 0
    got = src.search("TAX", {"states": ["TX"], "counties": ["Harris"], "limit": 5})
    assert [(r["county"], r["street_address"]) for r in got] == [("Harris", "900 BAYOU ST")]   # struck off skipped
    got = src.search("TAX", {"states": ["TX"], "counties": ["Tarrant County"], "limit": 5})
    assert [r["county"] for r in got] == ["Tarrant"]
    assert src.discovery and src.connector_kind == "real" and not src.costs
    assert src.scope == "state" and src.scope_name == "Texas"


def test_no_county_list_keeps_the_north_texas_default(lgbs_api):
    """An existing strategy with no county list sees exactly what it saw before."""
    got = PV.PROVIDERS["lgbs_tax_sales"].search("TAX", {"states": ["TX"], "limit": 50})
    assert {r["county"] for r in got} == {"Dallas", "Tarrant"}
    assert all(c.get("county") for c in lgbs_api)                   # per-county requests, as before


def test_all_texas_reads_every_county_in_one_statewide_pass(lgbs_api):
    got = PV.PROVIDERS["lgbs_tax_sales"].search("TAX", {"states": ["TX"], "counties": ["All Texas"], "limit": 50})
    assert {r["county"] for r in got} == {"Dallas", "Tarrant", "Harris", "Bexar"}
    assert all("county" not in c for c in lgbs_api) and len(lgbs_api) == 1


def test_all_texas_keeps_the_county_sources_and_the_geography_check():
    from types import SimpleNamespace
    from app.services.evosense import strategy as ST
    assert PV._wants({"states": ["TX"], "counties": ["All Texas"]}, "dallas")
    assert not PV._wants({"states": ["TX"], "counties": ["Harris"]}, "dallas")
    strat = SimpleNamespace(states='["TX"]', counties='["All Texas"]', cities="[]", zips="[]")
    ok_tx, _ = ST.geography_match(strat, SimpleNamespace(county="Harris", city="Houston", zip_code="77002", state="TX"))
    assert ok_tx
    strat2 = SimpleNamespace(states='["TX"]', counties='["Dallas"]', cities="[]", zips="[]")
    ok_h, _ = ST.geography_match(strat2, SimpleNamespace(county="Harris", city="Houston", zip_code="77002", state="TX"))
    assert not ok_h


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


def test_dcad_finds_the_account_from_an_exact_address(tmp_path, monkeypatch, no_network):  # noqa: F811
    monkeypatch.setenv("EVOSENSE_SRC_DCAD_PATH", _dcad_zip(tmp_path))
    got = DcadReader().accounts_for_addresses({
        "a": {"street_address": "100 Held Street", "zip_code": "75210"},      # suffix spelled out
        "b": {"street_address": "200 SOON ST", "city": "Dallas"},
        "c": {"street_address": "100 HELD ST", "zip_code": "75999"},          # ZIP contradicts
        "d": {"street_address": "999 NOWHERE LN", "zip_code": "75210"},
        "e": {"street_address": "HELD ST", "zip_code": "75210"},              # no house number
    })
    assert got["accounts"] == {"a": "00000555000000000", "b": "00000555100000000"}
    assert got["stats"]["no_match"] == 2 and got["stats"]["no_house_number"] == 1
    src = PV.PROVIDERS["dcad"]
    assert src.applies({"county": "Dallas", "street_address": "100 Held St"})
    out = src.lookup_many([{"id": "p1", "county": "Dallas", "street_address": "100 Held Street",
                            "zip_code": "75210", "parcel_apn": None},
                           {"id": "p2", "county": "Dallas", "street_address": "999 Nowhere Ln",
                            "zip_code": "75210", "parcel_apn": None}])
    assert out["results"]["p1"]["owner"]["name"] == "HELD HANNAH"
    assert out["results"]["p1"]["parcel_apn"] == "00000555000000000"
    assert out["results"]["p2"] is None                                       # no match, not a guess


def test_two_accounts_at_one_address_is_ambiguous(tmp_path, monkeypatch, no_network):  # noqa: F811
    import zipfile
    src_zip = _dcad_zip(tmp_path)
    with zipfile.ZipFile(src_zip) as z:
        files = {n: z.read(n).decode("latin-1") for n in z.namelist()}
    info = files["ACCOUNT_INFO.CSV"].rstrip("\r\n").splitlines()
    dup = info[1].replace("00000555000000000", "00000555900000000")
    files["ACCOUNT_INFO.CSV"] = "\n".join(info + [dup]) + "\n"
    monkeypatch.setenv("EVOSENSE_SRC_DCAD_PATH", write_zip(tmp_path / "dup.zip", files))
    got = DcadReader().accounts_for_addresses({"a": {"street_address": "100 HELD ST", "zip_code": "75210"}})
    assert got["accounts"] == {} and got["stats"]["ambiguous"] == 1


def test_imported_foreclosure_house_gets_its_owner_on_the_next_hunt(client, db_session, sample_org, tmp_path,
                                                                    monkeypatch, lgbs_api):
    monkeypatch.setenv("EVOSENSE_SRC_DCAD_PATH", _dcad_zip(tmp_path))
    _, h = _admin(db_session, sample_org)
    _enable(client, h, "dcad")
    csv_body = ("county,street_address,city,zip_code,state,signal_date,case_status,case_number\n"
                "Dallas,100 HELD STREET,Dallas,75210,TX,2026-10-01,2026-11-03,202600009999\n")
    r = client.post("/wholesale/evosense/import", headers=h,
                    files={"file": ("dallas.csv", csv_body, "text/csv")},
                    data={"list_kind": "foreclosure_filing", "list_source": "clerk postings"})
    assert r.status_code == 200, r.text
    p = (db_session.query(EvoSenseProperty)
         .filter(EvoSenseProperty.organization_id == sample_org.id,
                 EvoSenseProperty.street_address.ilike("100 HELD%")).one())
    assert p.parcel_apn is None
    s = _pilot(client, h, counties=["Dallas"], property_types=["single_family"], min_opportunity_score=10)
    out = ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    assert out["status"] == "succeeded", out
    assert out["counts"].get("lookup_backlog", 0) >= 1
    db_session.refresh(p)
    assert p.parcel_apn == "00000555000000000" and p.square_feet == 1120
    sigs = {x.signal_type for x in db_session.query(EvoSenseSignal)
            .filter(EvoSenseSignal.property_id == p.id, EvoSenseSignal.active.is_(True)).all()}
    assert "PRE_FORECLOSURE" in sigs
