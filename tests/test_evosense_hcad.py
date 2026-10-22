"""Harris County houses from HCAD's public real_acct.txt.

  * same keep rule as the statewide rolls: residential (A*), a situs house
    number, a named owner, mailing elsewhere or an estate;
  * condo / apartment units (str_unit) and non-residential classes are skipped;
  * the HCAD account is the property's APN; a changed header is a format error;
  * the source answers only for Harris (or All Texas) in Texas.
"""
import io
import zipfile

import pytest

from app.services.evosense import providers as PV
from app.services.evosense.sources import base as B
from app.services.evosense.sources import hcad as H

COLS = ["acct", "yr", "mailto", "mail_addr_1", "mail_addr_2", "mail_city", "mail_state", "mail_zip",
        "mail_country", "undeliverable", "str_pfx", "str_num", "str_num_sfx", "str", "str_sfx", "str_sfx_dir",
        "str_unit", "site_addr_1", "site_addr_2", "site_addr_3", "state_class", "yr_impr", "land_val",
        "bld_val", "tot_mkt_val", "new_own_dt"]


def row(acct, owner, num, street, mail1, mail_city="HOUSTON", mail_state="TX", cls="A1", unit="", sfx="ST"):
    v = {"acct": acct, "yr": "2026", "mailto": owner, "mail_addr_1": mail1, "mail_city": mail_city,
         "mail_state": mail_state, "mail_zip": "77008", "str_num": num, "str": street, "str_sfx": sfx,
         "str_unit": unit, "site_addr_1": "%s %s %s" % (num, street, sfx), "site_addr_2": "HOUSTON",
         "site_addr_3": "77008", "state_class": cls, "yr_impr": "1962", "land_val": "90000",
         "bld_val": "150000", "tot_mkt_val": "240000", "new_own_dt": "2001-05-04"}
    return "\t".join(v.get(c, "") for c in COLS)


LINES = [
    row("0001", "OWNER AT HOME", "10", "OAK", "10 OAK ST"),
    row("0002", "AWAY LANDLORD LLC", "12", "OAK", "500 MAIN ST", mail_city="DALLAS"),
    row("0003", "DOE JANE ESTATE OF", "14", "OAK", "14 OAK ST"),
    row("0004", "CONDO OWNER", "16", "OAK", "9 ELM", unit="4B"),
    row("0005", "STORE OWNER", "18", "OAK", "9 ELM", cls="F1"),
    row("0006", "PO BOX OWNER", "20", "OAK", "PO BOX 5"),
    row("0007", "FAR AWAY", "22", "OAK", "1 PINE RD", mail_city="DENVER", mail_state="CO"),
]


@pytest.fixture()
def hcad(monkeypatch):
    opened = []

    def open_zip(key, url, allow_download=True):
        opened.append((key, url))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("real_acct.txt", "\n".join(["\t".join(COLS)] + LINES) + "\n")
        buf.seek(0)
        return B.OpenedZip(zipfile.ZipFile(buf), {"url": url, "mode": "test"})
    monkeypatch.setattr(B, "open_zip", open_zip)
    return opened


def test_keeps_absentee_and_estate_houses(hcad):
    res = H.HcadRollReader(2026).discover(limit=10)
    got = {r["street_address"]: r for r in res["records"]}
    assert set(got) == {"12 OAK ST", "14 OAK ST", "22 OAK ST"}
    a = got["12 OAK ST"]
    assert a["county"] == "Harris" and a["parcel_apn"] == "0002" and a["city"] == "Houston"
    assert a["owner"]["name"] == "AWAY LANDLORD LLC" and a["owner"]["mailing_city"] == "Dallas"
    assert a["appraisal"]["value"] == 240000 and a["year_built"] == 1962
    assert a["source_reference"] == "HCAD:0002:2026"
    assert a["_source_url"].endswith("/CAMA/2026/Real_acct_owner.zip")
    assert got["22 OAK ST"]["owner"]["mailing_state"] == "CO"
    assert hcad[0][0] == "hcad_roll"


def test_changed_header_is_a_format_error():
    with pytest.raises(B.SourceError) as e:
        list(H.tsv_rows(["account\towner\tclass", "1\tA\tA1"]))
    assert e.value.code == B.SOURCE_FORMAT_CHANGED


def test_source_answers_for_harris_or_all_texas_only(hcad):
    src = PV.PROVIDERS["hcad_roll"]
    assert src.discovery and not src.costs and src.counties == ("Harris",)
    assert len(src.search("PROPERTY_SEARCH", {"states": ["TX"], "counties": ["Dallas"], "limit": 5})) == 0
    assert len(src.search("PROPERTY_SEARCH", {"states": ["TX"], "counties": ["Harris County"], "limit": 5, "rotate": 0})) == 3
    assert len(src.search("PROPERTY_SEARCH", {"states": ["TX"], "counties": ["All Texas"], "limit": 2, "rotate": 0})) == 2
    assert len(src.search("PROPERTY_SEARCH", {"states": ["OK"], "counties": ["Harris"], "limit": 5})) == 0
