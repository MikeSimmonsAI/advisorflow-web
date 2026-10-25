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


def test_same_subdivision_finds_platted_neighbours(roll):
    idx = J.load_index()
    rows = idx.same_subdivision("TESTSTONE ESTATES PH 2")
    assert [r["GEO ACCOUNT NUM"] for r in rows] == ["126.9001.00010"]
    assert idx.same_subdivision("NOWHERE ADDN") == []


# ── JCAD refuses cloud servers: a platform admin uploads the free file ──────

@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOSENSE_SOURCE_UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setenv("EVOSENSE_SOURCE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("EVOSENSE_SRC_JOHNSON_CAD_PATH", raising=False)
    J._MEMO.clear()
    yield
    J._MEMO.clear()


@pytest.fixture
def god_headers(db_session, sample_org, sample_advisor):
    from app.services.auth_service import create_access_token
    sample_advisor.role = "god_admin"
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(sample_advisor, db_session),
            "X-Org-Override": sample_org.id}


UP = "/wholesale/evosense/sources/johnson_cad/upload"


def test_jcad_upload_status_and_admin_only(client, auth_headers, uploads):
    st = client.get(UP, headers=auth_headers).json()
    assert st["uploadable"] and st["download_page"] == "https://johnsoncad.com/downloads"
    assert st["publisher"] == "JCAD" and st["current"] is None and st["can_upload"] is False
    r = client.post(UP, headers=auth_headers, files={"file": ("j.zip", build_zip(SYNTH), "application/zip")})
    assert r.status_code == 403 and not B.uploaded_info("johnson_cad")


def test_jcad_upload_validates_lifts_the_block_and_the_reader_uses_it(client, db_session, god_headers, uploads,
                                                                     monkeypatch):
    from app.services.evosense import providers as PV
    PV.block_platform(db_session, "johnson_cad", "AUTH_FAILED", "AUTH_FAILED: johnsoncad.com refused the request (403)")
    db_session.commit()
    assert client.post(UP, headers=god_headers, files={"file": ("x.zip", b"nope", "application/zip")}).status_code == 422
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("externalnal.tab", HEADER + "\r\n")
    r = client.post(UP, headers=god_headers, files={"file": ("x.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 422 and "externallegal.tab" in r.json()["detail"]
    r = client.post(UP, headers=god_headers,
                    files={"file": ("x.zip", build_zip(SYNTH, header="ACCOUNT\tNAME"), "application/zip")})
    assert r.status_code == 422 and "missing columns" in r.json()["detail"]
    assert PV.platform_blocked(db_session, "johnson_cad")

    # the publisher is never called once a copy is on file
    def no_network(*a, **k):
        raise AssertionError("JCAD was called although a copy was uploaded")
    monkeypatch.setattr(B, "_probe", no_network)
    ok = client.post(UP, headers=god_headers,
                     files={"file": ("BATCH_896.all_.zip", build_zip(SYNTH), "application/zip")})
    assert ok.status_code == 200, ok.text
    db_session.expire_all()
    assert not PV.platform_blocked(db_session, "johnson_cad")
    rec = J.JohnsonReader().lookup({"street_address": "397 TESTSTONE DR", "city": "Burleson"})
    assert rec and rec["source_reference"] == "JCAD:126.9001.00010:2026"
    assert rec["owner"]["name"] == "SYNTH OWNER ONE"
    assert J.load_index().rows == 1204

    # a newer upload replaces the index in memory without a restart
    more = SYNTH + [row(**{"GEO ACCOUNT NUM": "126.9002.00010", "NAME": "SYNTH NEW", "SITUS STREET NUM": "5",
                           "SITUS STREET NAME": "NEWER LN", "SITUS CITY": "CLEBURNE"})]
    assert client.post(UP, headers=god_headers,
                       files={"file": ("BATCH_896.all_.zip", build_zip(more), "application/zip")}).status_code == 200
    assert J.load_index().rows == 1205


def test_a_blocked_source_does_not_crash_a_list_recheck(db_session):
    """cohort.recheck hands free_lookups a bare dict; a source refusing the
    server used to raise KeyError('provider_errors') and kill the whole re-check."""
    from types import SimpleNamespace
    from app.services.evosense import hunt as HU
    from app.services.evosense import providers as PV
    from app.services.evosense.sources.base import AUTH_FAILED, SourceError
    counts = {}
    p = PV.PROVIDERS["johnson_cad"]
    cfg = PV.config(db_session, "org-x", "johnson_cad")
    HU._source_failed(db_session, "org-x", SimpleNamespace(id=None, is_test=False), p, cfg,
                      SourceError(AUTH_FAILED, "johnsoncad.com refused the request (403)"), counts, None)
    assert counts["provider_errors"] == 1 and counts["source_errors"][0]["code"] == AUTH_FAILED
