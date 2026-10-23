"""Texas statewide owner of record (TxGIO StratMap parcels).

  * the owner is taken ONLY from a parcel whose own situs has the same house
    number and street - a neighbour's parcel at the geocoded point is refused;
  * no geocode, an ambiguous geocode, or two matching parcels -> no record;
  * it covers what no county source can (Harris, Bexar..., Tarrant outside Fort
    Worth, Collin / Denton without an account) and never Dallas.
"""
import pytest

from app.services.evosense import providers as PV
from app.services.evosense.sources import base as B
from app.services.evosense.sources import txgio as TX
from app.services.evosense.sources.census import URL as CENSUS


def census(x=-95.398, y=29.79, county="Harris", matched="1015 HEIGHTS BLVD, HOUSTON, TX, 77008"):
    return {"result": {"addressMatches": [{"matchedAddress": matched, "coordinates": {"x": x, "y": y},
                                           "addressComponents": {"zip": "77008", "city": "HOUSTON"},
                                           "geographies": {"Counties": [{"BASENAME": county}]}}]}}


def parcel(situs="1015 HEIGHTS BLVD , HOUSTON, TX 77008", owner="CASTILLO AURELIANO G", pid="0202120000005"):
    return {"attributes": {"PROP_ID": pid, "OWNER_NAME": owner, "SITUS_ADDR": situs, "SITUS_CITY": "HOUSTON",
                           "MAIL_LINE1": "PO BOX 7", "MAIL_LINE2": "Null", "MAIL_CITY": "HOUSTON",
                           "MAIL_STAT": "TX", "MAIL_ZIP": "77008", "COUNTY": "HARRIS", "TAX_YEAR": "2026",
                           "DATE_ACQ": "46235"}}


@pytest.fixture()
def api(monkeypatch):
    state = {"census": census(), "parcels": [parcel()], "calls": [], "tolerances": []}

    def get_json(url, params=None):
        state["calls"].append(url)
        if url.startswith(CENSUS):
            return state["census"]
        if url.startswith(TX.IDENTIFY):
            state["tolerances"].append(params["tolerance"])
            wide = params["tolerance"] != "2"
            return {"results": state["parcels"] + (state.get("wide") or [] if wide else [])}
        raise AssertionError("unexpected call %s" % url)
    monkeypatch.setattr(B, "get_json", get_json)
    return state


T = {"id": "p1", "street_address": "1015 Heights Boulevard", "city": "Houston", "state": "TX",
     "zip_code": "77008", "county": "Harris", "parcel_apn": None}


def test_owner_comes_from_the_parcel_at_the_same_address(api):
    rec = TX.TxParcelReader().lookup(T)
    assert rec["owner"]["name"] == "CASTILLO AURELIANO G"
    assert rec["owner"]["mailing_street"] == "PO BOX 7" and rec["owner"]["mailing_state"] == "TX"
    assert rec["county"] == "Harris" and rec["parcel_apn"] is None          # state id is evidence only
    assert rec["_evidence"]["county_account"] == "0202120000005"
    assert rec["_source_updated_at"] == "2026-08-01" and rec["_evidence"]["tax_year"] == "2026"


def test_a_neighbours_parcel_is_never_used(api):
    api["parcels"] = [parcel(situs="1017 HEIGHTS BLVD , HOUSTON, TX 77008", owner="NEIGHBOR")]
    assert TX.TxParcelReader().lookup(T) is None


def test_two_matching_parcels_or_no_geocode_give_no_record(api):
    api["parcels"] = [parcel(), parcel(owner="SOMEONE ELSE", pid="2")]
    assert TX.TxParcelReader().lookup(T) is None
    api["parcels"] = [parcel()]
    api["census"] = {"result": {"addressMatches": []}}
    assert TX.TxParcelReader().lookup(T) is None
    api["census"] = census(county="Fort Bend")                             # wrong county
    assert TX.TxParcelReader().lookup(T) is None


@pytest.mark.parametrize("ours,theirs,city,ok", [
    ("1101 W Mulberry Ave", "1101 W MULBERRY AVE SAN ANTONIO, TX 78201", "SAN ANTONIO", True),
    ("1309 Ballweg Rd", "1309 BALLWEG RD", None, True),
    ("1309 Ballweg Rd", "1311 BALLWEG RD", None, False),
    ("1309 Ballweg Rd", "1309 OAK RD", None, False),
    ("1309 Ballweg Rd", None, None, False),
])
def test_same_parcel_address(ours, theirs, city, ok):
    assert TX.same_parcel_address(ours, theirs, city) is ok


@pytest.mark.parametrize("target,covered", [
    ({"county": "Harris", "city": "Houston"}, True),
    ({"county": "Bexar", "city": "San Antonio", "parcel_apn": "123"}, True),
    ({"county": "Tarrant", "city": "Arlington"}, True),
    ({"county": "Tarrant", "city": "Fort Worth"}, False),          # TAD by address
    ({"county": "Tarrant", "city": "Arlington", "parcel_apn": "7"}, False),
    ({"county": "Collin", "city": "Plano"}, True),
    ({"county": "Collin", "city": "Plano", "parcel_apn": "9"}, False),
    ({"county": "Dallas", "city": "Dallas"}, False),               # DCAD always
    ({"county": "Harris", "city": "Houston", "state": "OK"}, False),
])
def test_it_covers_only_what_no_county_source_can(target, covered):
    t = dict({"street_address": "1 MAIN ST", "state": "TX", "zip_code": "77000", "parcel_apn": None}, **target)
    src = PV.PROVIDERS["txgio_parcels"]
    assert src.applies(t) is covered
    assert src.scope == "state" and not src.costs and src.lookup_capability == "ASSESSOR"


def test_a_settings_row_created_by_another_request_is_reused_not_an_error(db_session, sample_org):
    """Providers and Controls load together; both may create a new source's
    settings row. The second must read the first one's row."""
    from app.models.evosense_models import EvoSenseProviderConfig
    from sqlalchemy.orm import sessionmaker
    other = sessionmaker(bind=db_session.get_bind())()
    try:
        other.add(EvoSenseProviderConfig(organization_id=sample_org.id, provider_key="txgio_parcels",
                                         enabled=False, priority=100))
        other.commit()
    finally:
        other.close()
    # This session has not seen it yet: the lookup misses, the insert collides.
    real_query = db_session.query
    calls = {"n": 0}

    def query(*a, **k):
        q = real_query(*a, **k)
        if a and a[0] is EvoSenseProviderConfig and calls["n"] == 0:
            calls["n"] += 1
            return q.filter(EvoSenseProviderConfig.id == "nothing-yet")
        return q
    db_session.query = query
    try:
        row = PV.config(db_session, sample_org.id, "txgio_parcels")
    finally:
        db_session.query = real_query
    assert row is not None and row.provider_key == "txgio_parcels"
    assert db_session.query(EvoSenseProviderConfig).filter_by(
        organization_id=sample_org.id, provider_key="txgio_parcels").count() == 1


def test_a_point_on_the_street_reaches_the_house_a_little_wider(api):
    """Census points sit on the street centerline: the tight search finds
    only the neighbour, the ~80 m search finds the house itself."""
    api["parcels"] = [parcel(situs="1017 HEIGHTS BLVD , HOUSTON, TX 77008", owner="NEIGHBOR")]
    api["wide"] = [parcel()]
    rec = TX.TxParcelReader().lookup(T)
    assert rec["owner"]["name"] == "CASTILLO AURELIANO G"
    assert api["tolerances"] == ["2", "100"]


def test_the_tight_search_is_enough_when_it_matches(api):
    TX.TxParcelReader().lookup(T)
    assert api["tolerances"] == ["2"]
