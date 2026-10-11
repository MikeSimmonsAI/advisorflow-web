"""Fort Worth OPEN code cases as discovery, and the TAD lookup by exact situs
address that gives those houses an owner of record. No network."""
import io
import zipfile

from app.services.evosense import providers as PV
from app.services.evosense.sources import base as B
from app.services.evosense.sources import fortworth as FW


def _case(cid, addr, kind, created=1790000000000):
    return {"attributes": {"Case_ID": cid, "Violation_ID": 1, "Violation_Address": addr,
                           "Complaint_Type_Description": kind, "Violation_Current_Status": "Open",
                           "Case_Current_Status": "Open", "Case_Created_Date": created, "Update_Date": created,
                           "City": "Fort Worth"}}


def test_discovery_groups_by_address_and_marks_substandard(monkeypatch):
    calls = []
    feats = [_case("26-1", "2251 Hemphill St", "Substandard Building"),
             _case("26-2", "2251 Hemphill St", "High Grass/Weeds"),
             _case("26-3", "1204 Nw 15th St", "High Grass/Weeds"),
             _case("26-4", "Hemphill St", "Property Maintenance")]          # no house number: skipped

    def fake(url, params=None):
        calls.append(params)
        if params.get("returnCountOnly"):
            return {"count": 4}
        return {"features": feats}
    monkeypatch.setattr(B, "get_json", fake)
    res = FW.discover(10, skip=6)
    assert calls[1]["resultOffset"] == 2                                   # 6 % 4: rotated
    assert "Case_Current_Status = 'Open'" in calls[0]["where"]
    by = {r["source_reference"]: r for r in res["records"]}
    assert set(by) == {"FWOPEN:2251 HEMPHILL ST", "FWOPEN:1204 NW 15TH ST"}
    h = by["FWOPEN:2251 HEMPHILL ST"]
    assert h["city"] == "Fort Worth" and h["county"] == "Tarrant" and h["street_address"] == "2251 Hemphill St"
    types = [s["type"] for s in h["signals"]]
    assert types.count("CODE_VIOLATION") == 2 and types.count("DISTRESSED_CONDITION") == 1
    grass = by["FWOPEN:1204 NW 15TH ST"]["signals"]
    assert [s["type"] for s in grass] == ["CODE_VIOLATION"]                # grass alone is not "distressed"
    assert res["stats"]["open_cases"] == 4


def test_no_open_cases_returns_nothing(monkeypatch):
    monkeypatch.setattr(B, "get_json", lambda url, params=None: {"count": 0})
    assert FW.discover(5)["records"] == []


TAD_HEADER = ["Account_Num", "Owner_Name", "Owner_Address", "Owner_CityState", "Owner_Zip", "Situs_Address",
              "City", "Total_Value", "Year_Built", "Living_Area", "Deed_Date", "Appraisal_Year"]


def _tad_zip(tmp_path, rows):
    p = tmp_path / "tad.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("PropertyData.txt", "\n".join("|".join(r) for r in [TAD_HEADER] + rows) + "\n")
    return str(p)


def test_tad_address_match_needs_one_fort_worth_account(tmp_path, monkeypatch):
    rows = []
    for i in range(6):                                                      # owner-occupied rows map city 026 -> FW
        rows.append(["%08d" % (100 + i), "OWNER %d" % i, "%d OAK ST" % i, "FORT WORTH, TEXAS", "76104",
                     "%d OAK ST" % i, "026", "100000", "1950", "1200", "01/02/2000", "2026"])
    for i in range(6):                                                      # city 004 -> ARLINGTON
        rows.append(["%08d" % (200 + i), "A %d" % i, "%d ELM ST" % i, "ARLINGTON, TEXAS", "76010",
                     "%d ELM ST" % i, "004", "100000", "1980", "1500", "01/02/2010", "2026"])
    rows.append(["00000900", "LANDLORD LLC", "PO BOX 1", "DALLAS, TEXAS", "75201",
                 "2251 HEMPHILL ST", "026", "90000", "1925", "1100", "05/06/1999", "2026"])
    rows.append(["00000901", "SOMEONE", "PO BOX 2", "DALLAS, TEXAS", "75201",
                 "77 MAIN ST", "004", "90000", "1925", "1100", "05/06/1999", "2026"])   # Arlington only
    monkeypatch.setenv("EVOSENSE_SRC_TAD_PATH", _tad_zip(tmp_path, rows))
    from app.services.evosense.sources.tarrant import TadReader
    res = TadReader().lookup_many([], addresses=["2251 Hemphill St", "77 Main St", "9 Nowhere Rd"])
    assert set(res["by_address"]) == {"2251 HEMPHILL ST"}
    rec = res["by_address"]["2251 HEMPHILL ST"]
    assert rec["parcel_apn"] == "00000900" and rec["owner"]["name"] == "LANDLORD LLC" and rec["city"] == "Fort Worth"
    assert res["stats"]["address_other_city"] == 1

    tad = PV.PROVIDERS["tad"]
    t_fw = {"id": "p1", "county": "Tarrant", "city": "Fort Worth", "street_address": "2251 Hemphill St"}
    t_arl = {"id": "p2", "county": "Tarrant", "city": "Arlington", "street_address": "77 Main St"}
    assert tad.applies(t_fw) and not tad.applies(t_arl)
    out = tad.lookup_many([t_fw])
    assert out["results"]["p1"]["parcel_apn"] == "00000900"


def test_provider_registered():
    p = PV.PROVIDERS["fw_open_cases"]
    assert p.discovery and p.counties == ("Tarrant",)
    assert p.search(PV.C.CODE_VIOLATION, {"counties": ["Dallas"], "limit": 3}).stats["skipped"]
