"""Texas statewide discovery from the TxGIO 2026 county appraisal rolls.

  * only residential parcels with a situs address and a named owner;
  * kept when the owner mails elsewhere (absentee) or the owner is an estate;
    a PO-box mailing address proves nothing and is not kept on that ground;
  * Dallas / Collin / Denton (their own discovery) are never read here;
  * 'All Texas' rotates through every other county; a strategy with no
    county list is not served (existing strategies keep what they had).
"""
import io
import struct
import zipfile

import pytest

from app.services.evosense import providers as PV
from app.services.evosense.sources import base as B
from app.services.evosense.sources import txgio_roll as R

FIELDS = [("PROP_ID", 20), ("OWNER_NAME", 60), ("STAT_LAND_", 5), ("LOC_LAND_U", 5), ("LAND_VALUE", 11),
          ("IMP_VALUE", 11), ("MKT_VALUE", 11), ("SITUS_ADDR", 80), ("SITUS_NUM", 15), ("SITUS_ST_1", 60),
          ("SITUS_CITY", 30), ("SITUS_ZIP", 5), ("MAIL_ADDR", 120), ("MAIL_LINE1", 60), ("MAIL_LINE2", 60),
          ("MAIL_CITY", 30), ("MAIL_STAT", 2), ("MAIL_ZIP", 5), ("SOURCE", 40), ("DATE_ACQ", 8),
          ("FIPS", 5), ("COUNTY", 30), ("TAX_YEAR", 4), ("YEAR_BUILT", 4)]


def dbf(rows):
    rlen = 1 + sum(n for _, n in FIELDS)
    hlen = 32 + 32 * len(FIELDS) + 1
    out = bytearray(struct.pack("<BBBBIHH20x", 3, 126, 10, 1, len(rows), hlen, rlen))
    for name, n in FIELDS:
        out += name.encode().ljust(11, b"\0") + b"C" + b"\0" * 4 + bytes([n, 0]) + b"\0" * 14
    out += b"\x0D"
    for r in rows:
        out += b" " + b"".join(str(r.get(name, "")).encode("latin-1")[:n].ljust(n) for name, n in FIELDS)
    out += b"\x1A"
    return bytes(out)


def row(pid, owner, num, street, mail1, mail_city="HOUSTON", mail_stat="TX", stat="A1", loc="", city="HOUSTON"):
    return {"PROP_ID": pid, "OWNER_NAME": owner, "STAT_LAND_": stat, "LOC_LAND_U": loc, "MKT_VALUE": "210000",
            "LAND_VALUE": "50000", "IMP_VALUE": "160000", "SITUS_NUM": num, "SITUS_ST_1": street,
            "SITUS_CITY": city, "SITUS_ZIP": "77008", "MAIL_LINE1": mail1, "MAIL_CITY": mail_city,
            "MAIL_STAT": mail_stat, "MAIL_ZIP": "77008", "SOURCE": "HARRIS CENTRAL APPRAISAL DISTRICT",
            "DATE_ACQ": "20260801", "FIPS": "48201", "COUNTY": "HARRIS", "TAX_YEAR": "2026", "YEAR_BUILT": "1958"}


ROWS = [
    row("1", "OWNER LIVES HERE", "100", "OAK ST", "100 OAK ST"),                         # not kept
    row("2", "AWAY LANDLORD", "102", "OAK ST", "9 ELM AVE", mail_city="AUSTIN"),       # absentee
    row("3", "SMITH JOHN EST OF", "104", "OAK ST", "104 OAK ST"),                      # estate
    row("4", "PO BOX OWNER", "106", "OAK ST", "PO BOX 7"),                             # unknown - not kept
    row("5", "OUT OF STATER", "108", "OAK ST", "5 PINE RD", mail_city="TULSA", mail_stat="OK"),
    row("6", "COMMERCIAL CO", "110", "OAK ST", "1 MAIN", stat="F1"),                   # not residential
    row("7", "NO ADDRESS", "", "", "1 MAIN"),                                          # no situs
    row("8", "LOCAL CODE RES", "112", "OAK ST", "77 BAY ST", stat="", loc="RES"),       # absentee, local code
]


@pytest.fixture()
def rolls(monkeypatch):
    opened = []

    def open_zip(key, url, allow_download=True):
        opened.append(url)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("shp/stratmap-2026-land-parcels.dbf", dbf(ROWS))
            z.writestr("shp/stratmap-2026-land-parcels.shp", b"geometry is never read")
        buf.seek(0)
        return B.OpenedZip(zipfile.ZipFile(buf), {"url": url, "mode": "test"})
    monkeypatch.setattr(B, "open_zip", open_zip)
    return opened


def test_county_files_follow_the_alphabetical_odd_fips_codes():
    assert len(R.TEXAS_COUNTIES) == 254
    assert {c: R.fips(c) for c in ("Anderson", "Bexar", "Dallas", "El Paso", "Harris", "Tarrant", "Zavala")} == \
        {"Anderson": 1, "Bexar": 29, "Dallas": 113, "El Paso": 141, "Harris": 201, "Tarrant": 439, "Zavala": 507}
    assert R.county_url("Harris").endswith("stratmap26-landparcels_48201_lp.zip")


def test_keeps_absentee_and_estate_houses_only(rolls):
    res = R.TxRollReader().discover("Harris", limit=10)
    got = {r["street_address"]: r for r in res["records"]}
    assert set(got) == {"102 OAK ST", "104 OAK ST", "108 OAK ST", "112 OAK ST"}
    a = got["102 OAK ST"]
    assert a["owner"]["name"] == "AWAY LANDLORD" and a["owner"]["mailing_city"] == "Austin"
    assert a["county"] == "Harris" and a["parcel_apn"] is None and a["city"] == "Houston"
    assert a["appraisal"]["value"] == 210000 and a["year_built"] == 1958
    assert a["_evidence"]["county_account"] == "2" and a["_source_updated_at"] == "2026-08-01"
    assert got["108 OAK ST"]["owner"]["mailing_state"] == "OK"
    assert res["stats"]["qualifying"] == 4 and res["stats"]["residential"] == 7


def test_rotation_moves_the_window_and_wraps(rolls):
    first = [r["street_address"] for r in R.TxRollReader().discover("Harris", limit=2)["records"]]
    later = [r["street_address"] for r in R.TxRollReader().discover("Harris", limit=2, rotate=2)["records"]]
    wrap = R.TxRollReader().discover("Harris", limit=2, rotate=9)
    assert first == ["102 OAK ST", "104 OAK ST"] and later == ["108 OAK ST", "112 OAK ST"]
    assert wrap["stats"]["wrapped"] == 1 and len(wrap["records"]) == 2


def test_texas_mail_state_quirk_and_po_box():
    assert R.mail_state({"MAIL_STAT": "TE", "MAIL_ADDR": "1 A ST, WYLIE, TEXAS 75098"}) == "TX"
    assert R.absentee({"SITUS_NUM": "5", "SITUS_ST_1": "OAK ST", "MAIL_LINE1": "PO BOX 1"}) is None
    assert R.absentee({"SITUS_NUM": "5", "SITUS_ST_1": "OAK ST", "MAIL_LINE1": "5 OAK STREET"}) is False


def test_source_targets_counties_and_never_reads_dallas_collin_denton(rolls):
    src = PV.PROVIDERS["txgio_roll"]
    assert src.scope == "state" and src.discovery and not src.costs and "PROPERTY_SEARCH" in src.capabilities
    assert len(src.search("PROPERTY_SEARCH", {"states": ["TX"], "limit": 10})) == 0          # no county list
    assert len(src.search("PROPERTY_SEARCH", {"states": ["TX"], "counties": ["Dallas", "Collin"], "limit": 10})) == 0
    got = src.search("PROPERTY_SEARCH", {"states": ["TX"], "counties": ["Harris County"], "limit": 10})
    assert got and all(r["county"] == "Harris" for r in got)
    rolls.clear()
    src.search("PROPERTY_SEARCH", {"states": ["TX"], "counties": ["All Texas"], "limit": 10, "rotate": 3})
    assert len(rolls) == 2
    assert not any(u.endswith(("48113_lp.zip", "48085_lp.zip", "48121_lp.zip")) for u in rolls)
    assert len(src.search("PROPERTY_SEARCH", {"states": ["OK"], "counties": ["Harris"], "limit": 5})) == 0
