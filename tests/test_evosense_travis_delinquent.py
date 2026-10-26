"""Travis County delinquent-tax file. The header is the LIVE file's header
(TaxDelqOpenData.csv, checked 2026-10-22); the rows are synthetic.

  * by the file's own Property Type Code: the types the strategy asks for (default residential);
  * Delinquent Total is the past-due balance - the current-year bill never
    qualifies a house; below the minimum, bankrupt or stale accounts are out;
  * a cause number is a tax lawsuit; the situs direction moves to the front;
  * City/State/Zip are the owner's mailing address, not the house's.
"""
from datetime import datetime

import pytest

from app.services.evosense import providers as PV
from app.services.evosense.sources import base as B
from app.services.evosense.sources import travis_delinquent as TD

TODAY = datetime(2026, 10, 22)
HEADER = ("Account #,Last Tax Roll Year ,Property Type Code,Land Value,Improvement Value,Ag Market,Appraisal Value,"
          "Ag Taxable Value,Assessed Value,Exemptions,Delinquent Total,Current Year Total,Total Due,Sequence #,"
          "1st Year Delinquent,Cause #,Judgement Date,Bankruptcy Number,Owner ID,Owner Name,Address 1,Address 2,"
          "Address 3,City,State,Zip Code,Country,Street Number,Street Name,Property Zip,Legal Description")


def row(acct, cls="A1", delq="4979.45", cur="0", first="2023", cause="", judg="", bk="", owner="SAMPLE PAT",
        num="913", street="GIBSON ST W", pzip="78704", roll="2025", ex=""):
    return ",".join([acct, roll, cls, "576054", "153296", "0", "729350", "0", "729350", ex, delq, cur,
                     "4979.45", "0", first, cause, judg, bk, "000001828028", owner, "", "9 ELM AVE", "",
                     "DALLAS", "TX", "752011234", "", num, street, pzip, "LOT 7 BLK 9"])


CSV = "\r\n".join([HEADER,
                   row("0001"),                                              # kept
                   row("0002", cause="GN21006856", judg="4/27/2026", first="2021", num="7302", street="LENAPE TRL"),
                   row("0003", cls="C1"),                                    # vacant lot
                   row("0004", cls="F1"),                                    # commercial
                   row("0005", cls="A4"),                                    # other residential code
                   row("0006", delq="25"),                                   # below minimum
                   row("0007", delq="0", cur="3200"),                        # only this year's bill
                   row("0008", bk="26-30011"),                               # bankruptcy
                   row("0009", roll="2006"),                                 # stale account
                   row("0010", owner=""),                                    # no owner
                   row("0011", num="", street=""),                           # no situs
                   ]) + "\r\n"


def test_keeps_only_past_due_residential_by_default_and_counts_every_rejection():
    res = TD.TravisDelinquentReader().discover(limit=10, today=TODAY, text=CSV)
    got = {r["parcel_apn"]: r for r in res["records"]}
    assert set(got) == {"0001", "0002", "0005"}             # A4 is residential by its own code
    s = res["stats"]
    assert s["rows"] == 11 and s["houses"] == 8 and s["found_residential"] == 9
    assert s["found_land"] == 1 and s["found_commercial"] == 1 and s["excluded_type"] == 2
    assert s["below_min_due"] == 2 and s["bankruptcy_skipped"] == 1 and s["stale_account"] == 1
    assert s["no_owner"] == 1 and s["no_situs"] == 1 and s["delinquent_kept"] == 3 and s["with_lawsuit"] == 1
    assert got["0005"]["property_type"] is None and got["0005"]["_evidence"]["property_class"] == "A4"


def test_a_strategy_asking_for_land_and_commercial_gets_them_with_their_own_code():
    res = TD.TravisDelinquentReader().discover(limit=10, today=TODAY, text=CSV,
                                               categories=("land", "commercial"))
    got = {r["parcel_apn"]: r for r in res["records"]}
    assert set(got) == {"0003", "0004"}
    assert got["0003"]["property_type"] == "land" and got["0003"]["_evidence"]["property_class"] == "C1"
    assert got["0004"]["property_type"] == "commercial" and res["stats"]["kept_commercial"] == 1


def test_record_fields():
    res = TD.TravisDelinquentReader().discover(limit=10, today=TODAY, text=CSV)
    a = {r["parcel_apn"]: r for r in res["records"]}["0001"]
    assert a["street_address"] == "913 W GIBSON ST" and a["zip_code"] == "78704" and a["city"] is None
    assert a["county"] == "Travis" and a["property_type"] == "single_family"
    assert a["owner"]["mailing_street"] == "9 ELM AVE" and a["owner"]["mailing_city"] == "Dallas"
    assert a["owner"]["mailing_zip"] == "75201"
    sig = a["signals"][0]
    assert sig["type"] == "TAX_DELINQUENT" and "$4,979.45" in sig["value"] and "2023" in sig["value"]
    assert sig["effective_at"] == "2024-02-01"
    assert a["appraisal"]["value"] == 729350
    b = {r["parcel_apn"]: r for r in res["records"]}["0002"]
    assert [x["type"] for x in b["signals"]] == ["TAX_DELINQUENT", "TAX_SUIT"]
    assert "GN21006856" in b["signals"][1]["value"] and "4/27/2026" in b["signals"][1]["value"]


def test_a_changed_header_is_a_format_error():
    with pytest.raises(B.SourceError) as e:
        TD.rows_from_text("Account Number,Owner Name,Tax Year\n1,A,2024\n")
    assert e.value.code == B.SOURCE_FORMAT_CHANGED


def test_source_answers_for_travis_tax_only(monkeypatch):
    monkeypatch.setattr(TD.TravisDelinquentReader, "fetch", lambda self: CSV)
    src = PV.PROVIDERS["travis_delinquent"]
    assert src.discovery and not src.costs and src.counties == ("Travis",)
    assert len(src.search("TAX", {"states": ["TX"], "counties": ["Dallas"], "limit": 5})) == 0
    # no type list: every target type the file carries; "houses only": residential codes only
    assert len(src.search("TAX", {"states": ["TX"], "counties": ["Travis County"], "limit": 9})) == 5
    assert len(src.search("TAX", {"states": ["TX"], "counties": ["Travis County"], "limit": 9,
                                  "houses_only": True})) == 3
    assert len(src.search("TAX", {"states": ["TX"], "counties": ["Travis County"], "limit": 9,
                                  "property_types": ["single_family", "land"]})) == 4
    assert len(src.search("TAX", {"states": ["TX"], "counties": ["All Texas"], "limit": 1})) == 1
    assert len(src.search("PROPERTY_SEARCH", {"states": ["TX"], "counties": ["Travis"], "limit": 5})) == 0


def test_the_live_files_byte_order_mark_is_not_part_of_the_header():
    """The real TaxDelqOpenData.csv begins with a UTF-8 BOM (seen from production
    2026-10-23: 'lacks Account #; header is <BOM>Account #, ...')."""
    rows = TD.rows_from_text("\ufeff" + CSV)
    assert rows[0]["Account #"] == "0001"
