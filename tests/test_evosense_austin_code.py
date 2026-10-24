"""City of Austin code complaint cases (Socrata 6wtj-zbtb), fields as in the live
dataset (checked 2026-10-22).

  * only the SAME house: same number and ZIP from the query, then the same
    street - a case next door or on another street with the number is dropped;
  * an open case is a CODE_COMPLAINT (a lead), a closed one CODE_HISTORY;
  * the source answers for Austin, and for Travis houses whose file has no city.
"""
import pytest

from app.services.evosense import providers as PV
from app.services.evosense.sources import austin as A
from app.services.evosense.sources import base as B


def case(cid, num, street, status, opened="2026-10-01T00:00:00.000", desc="Property Abatement"):
    return {"case_id": cid, "status": status, "house_number": num, "street_name": street, "zip_code": "78704",
            "opened_date": opened, "date_updated": opened, "case_type": "Complaints", "description": desc,
            "priority": "2", "parcelid": "0101010101", "repeatoffenderrelated": "No"}


@pytest.fixture()
def api(monkeypatch):
    calls = []
    rows = [case("2026-1 CC", "913", "GIBSON ST W", "Active"),
            case("2025-9 CC", "913", "GIBSON ST", "Closed", opened="2025-03-02T00:00:00.000"),
            case("2026-2 CC", "913", "OAK ST", "Active")]

    def get_json(url, params=None):
        calls.append(params)
        return rows
    monkeypatch.setattr(B, "get_json", get_json)
    return calls


def test_only_the_same_house_and_open_vs_closed(api):
    T = {"street_address": "913 W GIBSON ST", "zip_code": "78704", "county": "Travis", "city": None}
    rec = PV.PROVIDERS["austin_code"].lookup_one(T)
    types = [s["type"] for s in rec["signals"]]
    assert types == ["CODE_COMPLAINT", "CODE_HISTORY"]
    assert "2026-1 CC" in rec["signals"][0]["value"] and "not a confirmed violation" in rec["signals"][0]["evidence_basis"]
    assert "house_number = '913'" in api[0]["$where"] and "zip_code = '78704'" in api[0]["$where"]
    assert rec["city"] == "Austin" and rec["county"] == "Travis"


def test_no_case_is_no_record(monkeypatch):
    monkeypatch.setattr(B, "get_json", lambda url, params=None: [])
    assert PV.PROVIDERS["austin_code"].lookup_one({"street_address": "1 A ST", "zip_code": "78701"}) is None


@pytest.mark.parametrize("t,ok", [
    ({"street_address": "1 A ST", "city": "Austin", "county": "Travis"}, True),
    ({"street_address": "1 A ST", "city": None, "county": "Travis"}, True),
    ({"street_address": "1 A ST", "city": "Pflugerville", "county": "Travis"}, False),
    ({"street_address": "1 A ST", "city": "Dallas", "county": "Dallas"}, False),
])
def test_applies(t, ok):
    assert PV.PROVIDERS["austin_code"].applies(t) is ok
