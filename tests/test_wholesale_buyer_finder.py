"""Cash-buyer finder: investors are counted from county parcel rows, the
institutions are left out, and importing builds a buy box from where they
already own - without inventing a phone or email."""
import json
from datetime import datetime

import pytest

from app.services import wholesale_buyer_finder as F

TODAY = datetime(2026, 10, 10)


def rows():
    out = []
    def add(owner, n, recent, kind="house", zip5="75216", mail=("PO BOX 1", "Dallas", "TX", "75201")):
        for i in range(n):
            deed = datetime(2026, 3, 1) if i < recent else datetime(2015, 1, 1)
            out.append(("A%s%d" % (owner[:3], i), owner, mail[0], mail[1], mail[2], mail[3],
                        "%d MAIN ST" % (100 + i), "Dallas", zip5, deed, kind))
    add("OAK CLIFF HOLDINGS LLC", 6, 3)
    add("JOHN Q INVESTOR", 4, 0, zip5="75215")
    add("CITY OF DALLAS", 40, 5)                         # government: excluded
    add("FIRST NATIONAL BANK", 5, 2)                     # bank: excluded
    add("HOMEOWNER JANE", 1, 1)                          # one house: not an investor
    add("BIG NATIONAL RENTALS LP", 400, 50)              # too big by default
    add("OUT OF STATE CAPITAL LLC", 3, 1, mail=("1 WALL ST", "New York", "NY", "10005"))
    add("LOT COLLECTOR LLC", 3, 0, kind="lot")
    return out


def test_scan_finds_active_local_investors_only():
    res = F.scan(lambda: iter(rows()), county="dallas", today=TODAY)
    names = [b["name"] for b in res["buyers"]]
    assert names[0] == "OAK CLIFF HOLDINGS LLC"           # most recent buys first
    assert set(names) == {"OAK CLIFF HOLDINGS LLC", "JOHN Q INVESTOR", "OUT OF STATE CAPITAL LLC",
                          "LOT COLLECTOR LLC"}
    oak = res["buyers"][0]
    assert oak["properties"] == 6 and oak["bought_recently"] == 3 and oak["kind"] == "company"
    assert oak["zips"] == ["75216"] and oak["mailing"]["street"] == "PO BOX 1"
    ny = next(b for b in res["buyers"] if b["name"].startswith("OUT OF STATE"))
    assert ny["out_of_state"] is True
    john = next(b for b in res["buyers"] if b["name"] == "JOHN Q INVESTOR")
    assert john["kind"] == "person" and john["bought_recently"] == 0
    assert res["stats"]["owners_too_big"] == 1


def test_excluded_names():
    for n in ("CITY OF DALLAS", "DALLAS ISD", "WELLS FARGO BANK NA", "OAK HOMEOWNERS ASSOCIATION",
              "SECRETARY OF HOUSING", "FIRST BAPTIST CHURCH", "LENNAR HOMES OF TEXAS",
              "OPENDOOR PROPERTY TRUST I", "SFR JV-3 PROPERTY LLC", "PR BORROWER 26 LLC",
              "FORT WORTH COMMUNITY LAND TRUST"):
        assert F.excluded(n), n
    for n in ("OAK CLIFF HOLDINGS LLC", "JOHN BANKS", "MARIA GARCIA", "OWLIA PROPERTIES LLC",
              "TW ROCK INVESTMENTS LLC", "CASA DE RENTA 2 LLC"):
        assert not F.excluded(n), n


@pytest.fixture
def scanned(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOSENSE_SOURCE_CACHE", str(tmp_path))
    res = F.scan(lambda: iter(rows()), county="dallas", today=TODAY)
    with open(F.result_path("dallas"), "w", encoding="utf-8") as fh:
        json.dump(res, fh)
    return res


def test_finder_route_and_import(client, auth_headers, scanned):
    got = client.get("/wholesale/buyers/finder?county=dallas", headers=auth_headers).json()
    assert got["total"] == 4 and not any(b["already_added"] for b in got["buyers"])
    oak = next(b for b in got["buyers"] if b["name"] == "OAK CLIFF HOLDINGS LLC")

    r = client.post("/wholesale/buyers/finder/import", headers=auth_headers,
                    json={"county": "dallas", "keys": [oak["key"]]})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1

    buyers = client.get("/wholesale/buyers", headers=auth_headers).json()
    rows_ = buyers.get("buyers") if isinstance(buyers, dict) else buyers
    b = next(x for x in rows_ if x.get("company_name") == "OAK CLIFF HOLDINGS LLC")
    assert not b.get("email") and not b.get("phone")      # nothing invented
    assert "county_roll:dallas" == b.get("source")
    boxes = b.get("buy_boxes") or []
    assert boxes and "75216" in json.dumps(boxes)

    again = client.post("/wholesale/buyers/finder/import", headers=auth_headers,
                        json={"county": "dallas", "keys": [oak["key"]]}).json()
    assert again["created"] == 0 and again["skipped"][0]["reason"] == "already in your buyer list"
    got2 = client.get("/wholesale/buyers/finder?county=dallas", headers=auth_headers).json()
    assert next(b for b in got2["buyers"] if b["name"] == "OAK CLIFF HOLDINGS LLC")["already_added"] is True


def test_finder_rejects_unknown_county(client, auth_headers):
    assert client.get("/wholesale/buyers/finder?county=harris", headers=auth_headers).status_code == 400
