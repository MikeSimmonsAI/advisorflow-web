"""Johnson County appraisal roll (JCAD): fixture zip with the district's REAL column
header (externalnal.tab, 2026 certified) and synthetic accounts."""
import io
import zipfile

import pytest

from app.services.evosense import fc_match as M
from app.services.evosense.sources import base as B
from app.services.evosense.sources import johnson as J

HEADER = ("ACCOUNT\tGEO ACCOUNT NUM\tDSP GEO ACCOUNT NUM\tNAME\tNAME2\tMAIL ADDRESS LINE 1\tMAIL ADDRESS LINE 2\t"
          "MAIL CITY\tMAIL STATE\tMAIL ZIP\tSITUS STREET NUM\tSITUS STREET NAME\tSITUS UNIT\tSITUS CITY\t"
          "SITUS STREET SFX\tSITUS STREET NUM2\tSITUS STREET NAME2\tSITUS UNIT2\tSITUS CITY2\tSITUS STREET SFX2\t"
          "NBHD CD\tSUBDIVISION\tBLOCK\tLOT\tSECTION\tAGENT_CD\tAGENT NAME\tAGENT ADDRESS 1\tAGENT ADDRESS 2\t"
          "AGENT ADDRESS 3\tAGENT CITY\tAGENT STATE\tAGENT ZIP\tPLAT BOOK\tPLAT PAGE\tMAP ID\tMKT AREA CD\tRECD DATE\t"
          "INSTRUMENT NUM\tPARCEL USE CD\tUSE CD DESC\tBUILDING PTD\tBLD PTD DESC\tLAND PTD\tLAND PTD DESC\tLAND SQFT\t"
          "LAND MKT VALUE\tCOMMON LAND VAL\tPROD LAND VAL\tLAND ASSESSED VALUE\tBLDG HEAT AREA\tYEAR_BUILT\t"
          "BUILDING_VALUE\tEXTRA FEATURE VALUE\tTOTAL BUILDING VALUE\tNEW CON VALUE\tMKT VAL\tAPPRAISED VAL")
COLS = HEADER.split("\t")


def row(**kw):
    vals = {c: "" for c in COLS}
    vals.update(kw)
    return "\t".join(vals[c] for c in COLS)


LEGAL = ["parcel_id\tstrap\tln_num\tlgl_dscr",
         "900001\t126.9001.00010\t1\tLOT 22", "900001\t126.9001.00010\t2\tBLK 4",
         "900001\t126.9001.00010\t3\tTESTSTONE ESTATES PH 2",
         "900040\t126.9001.00040\t1\tLOT 13", "900040\t126.9001.00040\t2\tELMWOOD ADDN",
         "800000\t126.8000.00000\t1\tABST 1", "800000\t126.8000.00000\t2\tTR 1"]


def build_zip(rows, header=HEADER):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("externalnal.tab", "\r\n".join([header] + rows) + "\r\n")
        z.writestr("externallegal.tab", "\r\n".join(LEGAL) + "\r\n")
        z.writestr("externalsale.tab", "parcel_id\tstrap\tinstr_cd\tsale_date\tor_book\tor_page\tinstr_num\r\n")
    return buf.getvalue()


SYNTH = [
    row(**{"ACCOUNT": "R000900001", "GEO ACCOUNT NUM": "126.9001.00010", "NAME": "SYNTH OWNER ONE",
           "MAIL ADDRESS LINE 1": "PO BOX 1", "MAIL CITY": "ARLINGTON", "MAIL STATE": "TX", "MAIL ZIP": "760170000",
           "SITUS STREET NUM": "397", "SITUS STREET NAME": "TESTSTONE DR", "SITUS CITY": "BURLESON",
           "SUBDIVISION": "NULL", "RECD DATE": "03/15/2019",
           "INSTRUMENT NUM": "2019-5555", "PARCEL USE CD": "A1", "BLDG HEAT AREA": "1650", "YEAR_BUILT": "2004",
           "MKT VAL": "265000"}),
    # two accounts at one address: ambiguous
    row(**{"GEO ACCOUNT NUM": "126.9001.00020", "NAME": "SYNTH A", "SITUS STREET NUM": "10",
           "SITUS STREET NAME": "TWIN LN", "SITUS CITY": "CLEBURNE", "SUBDIVISION": "TWIN", "BLOCK": "1", "LOT": "1"}),
    row(**{"GEO ACCOUNT NUM": "126.9001.00030", "NAME": "SYNTH B", "SITUS STREET NUM": "10",
           "SITUS STREET NAME": "TWIN LN", "SITUS CITY": "CLEBURNE", "SUBDIVISION": "TWIN", "BLOCK": "1", "LOT": "2"}),
    row(**{"GEO ACCOUNT NUM": "126.9001.00040", "NAME": "SYNTH OTHER CITY", "SITUS STREET NUM": "55",
           "SITUS STREET NAME": "ELM ST", "SITUS CITY": "JOSHUA", "SUBDIVISION": "ELM", "BLOCK": "2", "LOT": "3"}),
] + [row(**{"GEO ACCOUNT NUM": "126.8000.%05d" % i, "NAME": "FILLER %d" % i, "SITUS STREET NUM": str(1000 + i),
            "SITUS STREET NAME": "FILLER RD", "SITUS CITY": "ALVARADO"}) for i in range(1200)]


@pytest.fixture
def roll(tmp_path, monkeypatch):
    path = tmp_path / "jcad.zip"
    path.write_bytes(build_zip(SYNTH))
    monkeypatch.setenv("EVOSENSE_SRC_JOHNSON_CAD_PATH", str(path))
    monkeypatch.setenv("EVOSENSE_SOURCE_CACHE", str(tmp_path / "cache"))
    J._MEMO.clear()
    yield path
    J._MEMO.clear()


def test_lookup_by_address_and_by_account(roll):
    r = J.JohnsonReader()
    rec = r.lookup({"street_address": "397 TESTSTONE DR", "city": "Burleson", "zip_code": "76028"})
    assert rec and rec["source_reference"] == "JCAD:126.9001.00010:2026"
    assert rec["owner"]["name"] == "SYNTH OWNER ONE" and rec["owner"]["mailing_zip"] == "76017"
    assert rec["deed_transfer_date"] == "2019-03-15" and rec["square_feet"] == 1650 and rec["year_built"] == 2004
    assert rec["_evidence"]["matched_by"] == "same house number and street"
    assert "no" in rec["_evidence"]["sale_price"] or "not published" in rec["_evidence"]["sale_price"]
    by_acct = r.lookup({"street_address": "nothing", "parcel_ids": ["126.9001.00010"]})
    assert by_acct["_evidence"]["matched_by"] == "county account number"
    assert J.load_index().rows == 1204


def test_ambiguous_and_wrong_city_never_match(roll):
    idx = J.load_index()
    row_, why = J.find(idx, {"street_address": "10 TWIN LN", "city": "Cleburne"})
    assert row_ is None and "ambiguous" in why
    # the roll writes the district's city; accepted only because the address is unique
    # in the county, and the difference is written into the evidence
    row_, why = J.find(idx, {"street_address": "55 ELM ST", "city": "Crowley"})
    assert row_ is not None and "only one in Johnson County" in why and "Joshua" in why
    assert J.find(idx, {"street_address": "9999 NOWHERE RD"})[0] is None


def test_index_is_cached_on_disk_and_reused(roll):
    J.load_index()
    J._MEMO.clear()
    roll.write_bytes(b"not a zip any more")        # the cached index is keyed by size/date, not re-read
    with pytest.raises(Exception):
        J.load_index()                             # a changed file is opened again, and refused if broken


def test_a_changed_file_layout_is_refused_not_guessed(tmp_path, monkeypatch):
    path = tmp_path / "bad.zip"
    path.write_bytes(build_zip(SYNTH[:1], header=HEADER.replace("SUBDIVISION", "SUBDIV_NAME")))
    monkeypatch.setenv("EVOSENSE_SRC_JOHNSON_CAD_PATH", str(path))
    monkeypatch.setenv("EVOSENSE_SOURCE_CACHE", str(tmp_path / "cache"))
    J._MEMO.clear()
    with pytest.raises(B.SourceError) as e:
        J.load_index()
    assert e.value.code == B.SOURCE_FORMAT_CHANGED and "SUBDIVISION" in str(e.value)
    J._MEMO.clear()


def test_the_legal_description_now_reaches_the_foreclosure_matcher(roll):
    import json
    rec = J.JohnsonReader().lookup({"street_address": "397 TESTSTONE DR", "city": "Burleson"})
    facts = M.facts_from_record("johnson_cad", json.dumps(rec["_raw"]))
    assert facts["parcel_ids"][0] == "126.9001.00010"
    assert rec["_raw"]["LEGAL"] == "TESTSTONE ESTATES PH 2 Block 4 Lot 22"
    assert facts["legal"]["block"] == "4" and facts["legal"]["lots"] == ["22"]
    elm = J.JohnsonReader().lookup({"street_address": "55 ELM ST", "city": "Joshua"})
    assert elm["_raw"]["LEGAL"] == "ELMWOOD ADDN Lot 13"
    acreage = J.find(J.load_index(), {"street_address": "1000 FILLER RD", "city": "Alvarado"})[0]
    assert acreage["LEGAL"] is None and acreage["LEGAL_LINES"] == "ABST 1 | TR 1"
    assert "johnson_cad" in M.LEGAL_SOURCES
    # a notice indexed by legal description names this lot -> matched; another lot -> rejected
    ident = {"parcel_ids": facts["parcel_ids"], "legal": facts["legal"], "names": ["SYNTH OWNER ONE"],
             "owner_names": ["SYNTH OWNER ONE"], "list_names": [], "city": "Burleson"}
    ok = M.decide(ident, "NOTICE OF SUBSTITUTE TRUSTEE SALE ... LOT 22, BLOCK 4, TESTSTONE ESTATES PHASE 2, "
                         "AN ADDITION TO THE CITY OF BURLESON, JOHNSON COUNTY ... SYNTH OWNER ONE")
    assert ok["decision"] in (M.VERIFIED, M.CANDIDATE)
    other = M.decide(ident, "NOTICE ... LOT 9, BLOCK 4, TESTSTONE ESTATES PHASE 2, JOHNSON COUNTY")
    assert other["decision"] != M.VERIFIED


def test_registered_as_a_free_johnson_only_source():
    from app.services.evosense import providers as PV
    p = PV.PROVIDERS["johnson_cad"]
    assert not p.costs and p.applies({"county": "Johnson", "street_address": "1 A St"})
    assert not p.applies({"county": "Tarrant", "street_address": "1 A St"})
