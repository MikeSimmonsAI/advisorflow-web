"""EvoSense DFW public-record acquisition: adapters, raw evidence, identity,
signals, the Source Registry, the cost governor's free-lookup lane, PILOT
mode, rollback, SMS eligibility and tenant isolation.

NO NETWORK. Every source file here is a small synthetic fixture written in
the published layout of the real file (Tarrant tax roll fixed-width,
TAD pipe-delimited, DCAD CSVs), handed to the adapters through the
EVOSENSE_SRC_<KEY>_PATH override; every API answer (Census, Fort Worth,
Dallas 311) is monkeypatched. `_request` itself is replaced with a function
that fails the test if anything tries to reach the internet.
"""
import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta

import pytest

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseCostEntry,
                                        EvoSenseEnrichmentDecision, EvoSenseEngagement,
                                        EvoSenseObservation, EvoSenseOwnership, EvoSensePerson,
                                        EvoSenseProperty, EvoSenseSignal, EvoSenseStrategy)
from app.models.models import Organization, User
from app.services.auth_service import create_access_token, hash_password
from app.services.evosense import common as C
from app.services.evosense import hunt as HU
from app.services.evosense import providers as PV
from app.services.evosense import scheduler as SCH
from app.services.evosense import scoring as SC
from app.services.evosense.sources import base as B
from app.services.evosense.sources import tarrant as TT

TODAY = datetime.utcnow()


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:600])
    return r.json()


# ── fixture files in the real published layouts ────────────────────────────

def master_line(**f):
    buf = [" "] * 760

    def put(name, value, zero=False):
        spec = next(x for x in TT.MASTER if x[0] == name)
        _, a, n = spec
        v = str(value)
        v = v.rjust(n, "0") if zero else v.ljust(n)
        buf[a - 1:a - 1 + n] = list(v[:n])
    put("account", f.get("account", "1"), zero=True)
    put("sptb", f.get("sptb", "001"))
    put("roll", "01")
    put("legal", f.get("legal", "TEST ADDITION BLK 1 LOT 1"))
    put("street_name", f.get("street_name", "OAK ST"))
    put("street_no", f.get("street_no", "100"), zero=True)
    put("year_built", f.get("year_built", "1955"))
    put("owner1", f.get("owner1", "SMITH JOHN A"))
    put("owner2", f.get("owner2", ""))
    put("addr1", f.get("addr1", "100 OAK ST"))
    put("addr2", f.get("addr2", ""))
    put("ocity", f.get("ocity", "FORT WORTH"))
    put("ostate", f.get("ostate", "TX"))
    put("ozip", f.get("ozip", "761040000"))
    put("def_start", f.get("def_start", "01/01/9999"))
    put("def_end", "01/01/9999")
    put("deed_date", f.get("deed_date", "03/15/2001"))
    put("exempt", f.get("exempt", ""))
    put("delq_date", f.get("delq_date", "02/01/%d" % TODAY.year))
    put("land", f.get("land", "40000"), zero=True)
    put("impr", f.get("impr", "120000"), zero=True)
    put("levy", "000003000.00")
    put("cur_due", f.get("cur_due", "000000000.00"))
    put("prior_due", f.get("prior_due", "000000000.00"))
    put("status", f.get("status", ""))
    put("litig", f.get("litig", ""))
    return "".join(buf)


TAD_HEADER = ("RP|Appraisal_Year|Account_Num|Record_Type|Sequence_No|PIDN|Owner_Name|Owner_Address|"
              "Owner_CityState|Owner_Zip|Owner_Zip4|Owner_CRRT|Situs_Address|Property_Class|TAD_Map|"
              "MAPSCO|Exemption_Code|State_Use_Code|LegalDescription|Notice_Date|County|City|School|"
              "Num_Special_Dist|Spec1|Spec2|Spec3|Spec4|Spec5|Deed_Date|Deed_Book|Deed_Page|Land_Value|"
              "Improvement_Value|Total_Value|Garage_Capacity|Num_Bedrooms|Num_Bathrooms|Year_Built|"
              "Living_Area|Swimming_Pool_Ind|ARB_Indicator|Ag_Code|Land_Acres|Land_SqFt|Ag_Acres|Ag_Value|"
              "Central_Heat_Ind|Central_Air_Ind|Structure_Count|From_Accts|Appraisal_Date|Appraised_Value|"
              "GIS_Link|Instrument_No|Overlap_Flag|Gross_Building_Area|Total_Net_Rentable_Area")


def tad_row(acct, owner, oaddr, ocitystate, ozip, situs, city_code="026", total="160000",
            deed="2001-03-15"):
    cols = TAD_HEADER.split("|")
    row = {c: "" for c in cols}
    row.update({"RP": "R", "Appraisal_Year": str(TODAY.year), "Account_Num": acct, "Owner_Name": owner,
                "Owner_Address": oaddr, "Owner_CityState": ocitystate, "Owner_Zip": ozip,
                "Situs_Address": situs, "Property_Class": "A", "State_Use_Code": "A1", "City": city_code,
                "Deed_Date": deed, "Total_Value": total, "Num_Bedrooms": "3", "Num_Bathrooms": "2",
                "Year_Built": "1955", "Living_Area": "1400.00"})
    return "|".join(row[c] for c in cols)


def write_zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members.items():
            z.writestr(name, text)
    return str(path)


def csv_text(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL)
    w.writerow(header)
    for r in rows:
        w.writerow([r.get(h, "") for h in header])
    return buf.getvalue()


@pytest.fixture()
def no_network(monkeypatch):
    def refuse(url, *a, **k):
        raise AssertionError("test tried to reach the network: %s" % url)
    monkeypatch.setattr(B, "_request", refuse)


@pytest.fixture()
def dfw_files(tmp_path, monkeypatch, no_network):
    past = "000001250.40"
    lines = [
        # 1. delinquent single-family, absentee owner (Arlington mailing), prior years due
        master_line(account="1309", street_no="704", street_name="E WEATHERFORD ST",
                    owner1="DAILEY TODD W AND DAILEY MELIS", addr1="9 ELM CT", ocity="ARLINGTON",
                    ozip="760100000", prior_due=past, litig="Y"),
        # 2. delinquent current year only, owner lives there
        master_line(account="6483", street_no="922", street_name="E PEACH ST", owner1="ALVAREZ JUANITA",
                    addr1="922 E PEACH ST", ozip="761020000", cur_due="000000980.00"),
        # 3. over-65 deferral: NOT delinquent per the tax office
        master_line(account="7001", street_no="10", street_name="PINE ST", owner1="OLDER OWNER",
                    addr1="10 PINE ST", prior_due=past, status="D"),
        # 4. half-pay plan: NOT delinquent
        master_line(account="7002", street_no="12", street_name="PINE ST", owner1="PLAN OWNER",
                    addr1="12 PINE ST", prior_due=past, status="H"),
        # 5. government owner: excluded
        master_line(account="7003", street_no="14", street_name="PINE ST", owner1="FORT WORTH ISD",
                    addr1="100 N UNIVERSITY DR", prior_due=past),
        # 6. commercial SPTB: not residential
        master_line(account="7004", sptb="010", street_no="16", street_name="PINE ST", owner1="SHOP LLC",
                    prior_due=past),
        # 7. tiny balance: below the discovery minimum
        master_line(account="7005", street_no="18", street_name="PINE ST", owner1="TINY OWE",
                    addr1="18 PINE ST", prior_due="000000005.92"),
        # 8. paid up: not delinquent
        master_line(account="7006", street_no="20", street_name="PINE ST", owner1="PAID UP",
                    addr1="20 PINE ST"),
        # 9. deferral by date on file
        master_line(account="7007", street_no="22", street_name="PINE ST", owner1="DEFER DATE",
                    addr1="22 PINE ST", prior_due=past, def_start="12/29/2016"),
        # 10. third delinquent (used for the record cap)
        master_line(account="8001", street_no="500", street_name="MAIN ST", owner1="THIRD OWNER LLC",
                    addr1="PO BOX 44", ozip="750010000", ocity="DALLAS", prior_due=past),
    ]
    tax = write_zip(tmp_path / "TaxRoll.zip", {"Rec.DAT": "x\n", "Master.dat": "\r\n".join(lines) + "\r\n"})
    tad_lines = [TAD_HEADER,
                 tad_row("1309", "TODD & MELISSA DAILEY", "9 ELM CT", "ARLINGTON ,  TX", "76010",
                         "704 E WEATHERFORD ST"),
                 tad_row("6483", "JUANITA ALVAREZ", "922 E PEACH ST", "FORT WORTH ,  TX", "76102",
                         "922 E PEACH ST"),
                 # owner-occupied rows that teach the city-code map (026 = FORT WORTH)
                 tad_row("9001", "A", "1 A ST", "FORT WORTH ,  TX", "76104", "1 A ST"),
                 tad_row("9002", "B", "2 B ST", "FORT WORTH ,  TX", "76104", "2 B ST"),
                 tad_row("9003", "C", "3 C ST", "FORT WORTH ,  TX", "76104", "3 C ST"),
                 tad_row("9004", "D", "4 D ST", "FORT WORTH ,  TX", "76104", "4 D ST"),
                 tad_row("8001", "THIRD OWNER LLC", "PO BOX 44", "DALLAS ,  TX", "75001", "500 MAIN ST")]
    tad = write_zip(tmp_path / "tad.zip", {"PropertyData(Delimited)_R.txt": "\r\n".join(tad_lines) + "\r\n"})

    res_h = ["ACCOUNT_NUM", "APPRAISAL_YR", "CDU_RATING_DESC", "YR_BUILT", "TOT_LIVING_AREA_SF",
             "NUM_FULL_BATHS", "NUM_BEDROOMS", "DEPRECIATION_PCT"]
    info_h = ["ACCOUNT_NUM", "APPRAISAL_YR", "OWNER_NAME1", "OWNER_NAME2", "EXCLUDE_OWNER",
              "OWNER_ADDRESS_LINE1", "OWNER_ADDRESS_LINE2", "OWNER_ADDRESS_LINE3", "OWNER_CITY",
              "OWNER_STATE", "OWNER_ZIPCODE", "STREET_NUM", "STREET_HALF_NUM", "FULL_STREET_NAME",
              "UNIT_ID", "PROPERTY_CITY", "PROPERTY_ZIPCODE", "DEED_TXFR_DATE", "PHONE_NUM"]
    val_h = ["ACCOUNT_NUM", "APPRAISAL_YR", "TOT_VAL", "IMPR_VAL", "LAND_VAL", "SPTD_CODE", "CITY_JURIS_DESC"]
    ex_h = ["ACCOUNT_NUM", "APPRAISAL_YR", "HOMESTEAD_EFF_DT"]
    y = str(TODAY.year)
    dcad = write_zip(tmp_path / "dcad.zip", {
        "RES_DETAIL.CSV": csv_text(res_h, [
            {"ACCOUNT_NUM": "D1", "APPRAISAL_YR": y, "CDU_RATING_DESC": "POOR", "YR_BUILT": "1948",
             "TOT_LIVING_AREA_SF": "980", "NUM_FULL_BATHS": "1", "NUM_BEDROOMS": "2"},
            {"ACCOUNT_NUM": "D2", "APPRAISAL_YR": y, "CDU_RATING_DESC": "GOOD", "YR_BUILT": "1990"},
            {"ACCOUNT_NUM": "D3", "APPRAISAL_YR": y, "CDU_RATING_DESC": "VERY POOR", "YR_BUILT": "1950"},
        ]),
        "ACCOUNT_APPRL_YEAR.CSV": csv_text(val_h, [
            {"ACCOUNT_NUM": "D1", "APPRAISAL_YR": y, "TOT_VAL": "150000.00", "SPTD_CODE": "A11"},
            {"ACCOUNT_NUM": "D2", "APPRAISAL_YR": y, "TOT_VAL": "350000.00", "SPTD_CODE": "A11"},
            {"ACCOUNT_NUM": "D3", "APPRAISAL_YR": y, "TOT_VAL": "90000.00", "SPTD_CODE": "A11"},
        ]),
        "ACCOUNT_INFO.CSV": csv_text(info_h, [
            {"ACCOUNT_NUM": "D1", "OWNER_NAME1": "GARCIA MARIA", "EXCLUDE_OWNER": "N",
             "OWNER_ADDRESS_LINE1": "LIFE ESTATE", "OWNER_ADDRESS_LINE2": "77 FAR RD", "OWNER_CITY": "HOUSTON", "OWNER_STATE": "TEXAS",
             "OWNER_ZIPCODE": "770010000", "STREET_NUM": "504", "FULL_STREET_NAME": "N MADISON AVE",
             "PROPERTY_CITY": "DALLAS", "PROPERTY_ZIPCODE": "752081234", "DEED_TXFR_DATE": "06/01/1999",
             "PHONE_NUM": "2145550199"},
            {"ACCOUNT_NUM": "D3", "OWNER_NAME1": "CONFIDENTIAL PERSON", "EXCLUDE_OWNER": "Y",
             "OWNER_ADDRESS_LINE2": "1 SECRET WAY", "OWNER_CITY": "DALLAS", "OWNER_STATE": "TEXAS",
             "OWNER_ZIPCODE": "75201", "STREET_NUM": "9", "FULL_STREET_NAME": "ELM ST",
             "PROPERTY_CITY": "DALLAS", "PROPERTY_ZIPCODE": "75210"},
        ]),
        "APPLIED_STD_EXEMPT.CSV": csv_text(ex_h, []),
    })
    monkeypatch.setenv("EVOSENSE_SRC_TARRANT_TAX_ROLL_PATH", tax)
    monkeypatch.setenv("EVOSENSE_SRC_TAD_PATH", tad)
    monkeypatch.setenv("EVOSENSE_SRC_DCAD_PATH", dcad)
    return {"tax": tax, "tad": tad, "dcad": dcad, "lines": lines}


@pytest.fixture()
def fake_apis(monkeypatch):
    """Census, Fort Worth ArcGIS and Dallas 311, answered locally."""
    calls = {"census": 0, "fw": 0, "dal": 0}

    def get_json(url, params=None):
        params = params or {}
        if "geocoding.geo.census.gov" in url:
            calls["census"] += 1
            street = params.get("street", "")
            county = "Tarrant" if street != "500 MAIN ST" else "Dallas"      # wrong county -> no match
            return {"result": {"addressMatches": [{
                "matchedAddress": "%s, FORT WORTH, TX, 76102" % street,
                "addressComponents": {"zip": "76102", "city": "FORT WORTH"},
                "coordinates": {"x": -97.33, "y": 32.75},
                "geographies": {"Counties": [{"BASENAME": county}]}}]}}
        if "arcgis.com" in url:
            calls["fw"] += 1
            if url.endswith("/query"):
                where = params.get("where", "")
                if "704 E WEATHERFORD ST" in where:
                    return {"features": [{"attributes": {
                        "Case_ID": "26-000001", "Violation_Address": "704 E WEATHERFORD ST",
                        "Complaint_Type_Description": "Substandard Structure",
                        "Case_Current_Status": "Open",
                        "Case_Created_Date": int((TODAY - timedelta(days=20)).timestamp() * 1000)}}]}
                return {"features": []}
            return {"fields": [{"name": "Violation_Address"}]}
        if "dallasopendata.com" in url:
            calls["dal"] += 1
            if "504 N MADISON AVE" in params.get("$where", ""):
                return [{"service_request_number": "26-00426041", "address": "504 N MADISON AVE, DALLAS, TX, 75208",
                         "service_request_type": "Code Concern - CCS", "status": "Closed",
                         "created_date": (TODAY - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S.000")}]
            return []
        raise AssertionError("unexpected API %s" % url)
    monkeypatch.setattr(B, "get_json", get_json)
    return calls


def _admin(db, org, email="dfwadmin@restland.com"):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("AdminPass123!"),
             full_name="DFW Admin", role="org_admin", must_change_password=False)
    db.add(u)
    db.commit()
    return u, {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _enable(client, h, *keys):
    for k in keys:
        ok(client.patch("/wholesale/evosense/providers", headers=h, json={"key": k, "enabled": True}))


def _pilot(client, h, **over):
    body = {"name": "DFW pilot", "states": ["TX"], "counties": ["Tarrant", "Dallas"],
            "property_types": ["single_family"], "min_opportunity_score": 10,
            "pilot_mode": True, "pilot_max_properties": 40, "hunt_cadence": "manual"}
    body.update(over)
    s = ok(client.post("/wholesale/evosense/strategies", headers=h, json=body))
    ok(client.post("/wholesale/evosense/strategies/%s/activate" % s["id"], headers=h))
    return s


def _prop(db, org_id, apn, county="Tarrant"):
    return (db.query(EvoSenseProperty)
            .filter(EvoSenseProperty.organization_id == org_id, EvoSenseProperty.parcel_apn == apn,
                    EvoSenseProperty.county == county).one())


# ── 1. the Tarrant tax roll reader ──────────────────────────────────────────

def test_tax_roll_reads_only_real_delinquency_and_excludes_what_the_tax_office_says_is_not(dfw_files):
    res = TT.TarrantTaxRollReader().discover(limit=50)
    accts = [r["parcel_apn"] for r in res["records"]]
    assert accts == ["1309", "6483", "8001"]
    st = res["stats"]
    assert st["excluded"] == 4                      # D, H, government, dated deferral
    assert st["below_min_due"] == 1
    r = res["records"][0]
    assert r["county"] == "Tarrant" and r["state"] == "TX" and r["street_address"] == "704 E WEATHERFORD ST"
    sig = {s["type"]: s for s in r["signals"]}
    assert "prior-year taxes due $1,250.40" in sig["TAX_DELINQUENT"]["value"]
    assert "TAX_SUIT" in sig
    assert r["_raw"] == dfw_files["lines"][0] and r["_adapter_version"] == "tarrant_tax_roll/1"
    # a current-year-only delinquency is dated by the roll's own delinquency date
    cur = {s["type"]: s for s in res["records"][1]["signals"]}["TAX_DELINQUENT"]
    assert cur["effective_at"] == "%d-02-01" % TODAY.year
    # owner lives there: the mailing city/ZIP are the property's, with that basis
    r2 = res["records"][1]
    assert (r2["city"], r2["zip_code"]) == ("Fort Worth", "76102")
    assert r2["_evidence"]["city_basis"] == "owner mailing address is the property"
    assert r["city"] is None and r["zip_code"] is None       # absentee: unknown, not guessed


def test_tax_roll_record_cap_is_hard(dfw_files):
    res = TT.TarrantTaxRollReader().discover(limit=2)
    assert len(res["records"]) == 2


def test_a_changed_file_layout_is_a_first_class_failure(tmp_path, monkeypatch, no_network):
    bad = write_zip(tmp_path / "bad.zip", {"Master.dat": "too short\n" * 60})
    monkeypatch.setenv("EVOSENSE_SRC_TARRANT_TAX_ROLL_PATH", bad)
    with pytest.raises(B.SourceError) as e:
        TT.TarrantTaxRollReader().discover(limit=5)
    assert e.value.code == B.SOURCE_FORMAT_CHANGED


def test_download_guard_refuses_when_disk_is_short(tmp_path, monkeypatch, no_network):
    monkeypatch.setenv("EVOSENSE_SOURCE_CACHE", str(tmp_path))
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(B.SourceError) as e:
        B.download("dcad", "https://example.invalid/x.zip", 1000)
    assert e.value.code == B.INSUFFICIENT_DISK
    monkeypatch.setenv("EVOSENSE_SOURCE_MAX_BYTES", "100")
    with pytest.raises(B.SourceError) as e:
        B.download("dcad", "https://example.invalid/x.zip", 1000)
    assert e.value.code == B.INSUFFICIENT_DISK


# ── 2. the registry never claims what it has not verified ──────────────────

def test_registry_states_are_honest(client, db_session, sample_org, dfw_files, fake_apis):
    _, h = _admin(db_session, sample_org)
    reg = {r["key"]: r for r in ok(client.get("/wholesale/evosense/sources", headers=h))["sources"]}
    for k in ("tarrant_tax_roll", "tad", "dcad", "census_geocoder", "fw_code_violations", "dallas_311_code"):
        assert reg[k]["state"] == "DISABLED", k                 # off until an admin enables it
        assert reg[k]["configured"] is True and reg[k]["operational"] is False
        assert reg[k]["cost"] == "free" and reg[k]["jurisdiction"] and reg[k]["access_method"]
    for k in ("rentcast", "regrid", "attom"):
        assert reg[k]["state"] == "NOT PURCHASED" and "paid" in reg[k]["cost"]
    for k in ("dallas_foreclosure_manual", "dallas_tax_manual", "manual", "csv_import"):
        assert reg[k]["state"] == "MANUAL ONLY"
    assert not any(k.startswith("sandbox") for k in reg)
    _enable(client, h, "tad")
    reg = {r["key"]: r for r in ok(client.get("/wholesale/evosense/sources", headers=h))["sources"]}
    assert reg["tad"]["state"] == "UNVERIFIED"                  # enabled is not healthy
    v = ok(client.post("/wholesale/evosense/sources/verify", headers=h, json={"key": "tad"}))
    assert v["ok"] is True
    reg = {r["key"]: r for r in v["registry"]["sources"]}
    assert reg["tad"]["state"] == "HEALTHY" and reg["tad"]["last_verified_at"]


def test_a_failed_probe_reads_failed_with_its_code(client, db_session, sample_org, dfw_files, monkeypatch):
    _, h = _admin(db_session, sample_org)
    _enable(client, h, "fw_code_violations")

    def boom(url, params=None):
        raise B.SourceError(B.RATE_LIMITED, "429 from the city", retry_after=600)
    monkeypatch.setattr(B, "get_json", boom)
    v = ok(client.post("/wholesale/evosense/sources/verify", headers=h, json={"key": "fw_code_violations"}))
    assert v["ok"] is False and v["code"] == "RATE_LIMITED"
    reg = {r["key"]: r for r in v["registry"]["sources"]}
    assert reg["fw_code_violations"]["state"] == "RATE LIMITED"  # rate limited, not "down forever"


def test_only_an_admin_verifies_a_source(client, auth_headers, dfw_files):
    r = client.post("/wholesale/evosense/sources/verify", headers=auth_headers, json={"key": "tad"})
    assert r.status_code == 403


# ── 3. the pilot, end to end ────────────────────────────────────────────────

ALL_DFW = ("tarrant_tax_roll", "tad", "dcad", "census_geocoder", "fw_code_violations", "dallas_311_code")


def test_pilot_discovers_joins_and_scores_real_shaped_records(client, db_session, sample_org,
                                                              dfw_files, fake_apis):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, *ALL_DFW)
    s = _pilot(client, h)
    out = ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    assert out["status"] == "succeeded", out
    c = out["counts"]
    assert c["pilot"]["outreach"] == "off" and c["pilot"]["paid_data"] == "off"
    assert c["sources"]["tarrant_tax_roll"]["records"] == 3
    assert c["sources"]["dcad"]["records"] == 2
    org = sample_org.id

    # APN join: one property per parcel, tax roll + TAD + geocoder + code cases
    p = _prop(db, org, "1309")
    obs = {o.provider_key: o for o in db.query(EvoSenseObservation)
           .filter(EvoSenseObservation.property_id == p.id).all()}
    assert set(obs) >= {"tarrant_tax_roll", "tad", "census_geocoder", "fw_code_violations"}
    assert p.city == "Fort Worth" and p.zip_code == "76102"
    assert p.estimated_value == 160000 and "appraisal district" in p.estimated_value_source
    from app.services.evosense import valuation as VAL
    assert VAL.view(p)["market_value"] is None and VAL.appraisal_value(p) == 160000
    # value known, mortgage unknown: NO equity, NO high-equity, NO free-and-clear
    assert p.equity_pct is None and p.mortgage_balance is None
    types = {x.signal_type for x in db.query(EvoSenseSignal)
             .filter(EvoSenseSignal.property_id == p.id, EvoSenseSignal.active.is_(True)).all()}
    assert {"TAX_DELINQUENT", "TAX_SUIT", "ABSENTEE_OWNER", "CODE_VIOLATION"} <= types
    assert not types & {"HIGH_EQUITY", "FREE_AND_CLEAR", "VACANT"}   # never from absence / absentee

    # raw evidence, hashed, versioned, with the source's own "as of"
    t = obs["tarrant_tax_roll"]
    assert t.raw_payload == dfw_files["lines"][0]
    assert t.content_hash == hashlib.sha256(t.raw_payload.encode()).hexdigest()
    assert t.adapter_version == "tarrant_tax_roll/1" and t.processing_status == "ingested"
    assert t.source_url

    # the two spellings of the same owner are one owner, not a conflict
    links = db.query(EvoSenseOwnership).filter(EvoSenseOwnership.property_id == p.id).all()
    assert len({l.owner_id for l in links}) == 1 and not any(l.in_conflict for l in links)
    assert not p.has_conflicts

    # owner lives at the property -> not absentee; PO box -> absentee undecided
    p2 = _prop(db, org, "6483")
    t2 = {x.signal_type for x in db.query(EvoSenseSignal)
          .filter(EvoSenseSignal.property_id == p2.id, EvoSenseSignal.active.is_(True)).all()}
    assert "ABSENTEE_OWNER" not in t2 and "TAX_DELINQUENT" in t2
    p3 = _prop(db, org, "8001")
    t3 = {x.signal_type for x in db.query(EvoSenseSignal)
          .filter(EvoSenseSignal.property_id == p3.id, EvoSenseSignal.active.is_(True)).all()}
    assert "ABSENTEE_OWNER" not in t3
    assert p3.zip_code is None             # the geocoder's only match was in another county: no guess

    # Dallas: DCAD condition + 311 complaint; the confidential owner is never read
    d1 = _prop(db, org, "D1", "Dallas")
    t4 = {x.signal_type for x in db.query(EvoSenseSignal)
          .filter(EvoSenseSignal.property_id == d1.id, EvoSenseSignal.active.is_(True)).all()}
    assert {"DISTRESSED_CONDITION", "CODE_COMPLAINT", "ABSENTEE_OWNER"} <= t4
    assert "OUT_OF_STATE_OWNER" not in t4                      # Houston is in Texas
    from app.models.evosense_models import EvoSenseOwner
    ow = db.query(EvoSenseOwner).join(EvoSenseOwnership, EvoSenseOwnership.owner_id == EvoSenseOwner.id) \
        .filter(EvoSenseOwnership.property_id == d1.id).one()
    # DCAD's first mailing line continues the name; the street is the next line
    assert ow.display_name == "GARCIA MARIA LIFE ESTATE" and ow.mailing_street == "77 FAR RD"
    assert d1.zip_code == "75208" and d1.city == "Dallas"
    d3 = _prop(db, org, "D3", "Dallas")
    assert not db.query(EvoSenseOwnership).filter(EvoSenseOwnership.property_id == d3.id).count()
    raw_d3 = db.query(EvoSenseObservation).filter(EvoSenseObservation.property_id == d3.id).first().raw_payload
    assert "CONFIDENTIAL PERSON" not in raw_d3 and "SECRET" not in raw_d3

    # cost governor: every free lookup is a recorded decision; nothing was bought
    lookups = db.query(EvoSenseEnrichmentDecision).filter(
        EvoSenseEnrichmentDecision.organization_id == org,
        EvoSenseEnrichmentDecision.capability != C.CONTACT_ENRICHMENT).all()
    assert lookups and {d.decision for d in lookups} <= set(C.LOOKUP_DECISIONS)
    assert all((d.estimated_cost_cents or 0) == 0 for d in lookups)
    assert db.query(EvoSenseCostEntry).filter(EvoSenseCostEntry.organization_id == org).count() == 0
    # no outreach of any kind
    assert db.query(EvoSenseEngagement).filter(EvoSenseEngagement.organization_id == org).count() == 0

    # the property page shows provenance, raw evidence and the SMS verdict
    d = ok(client.get("/wholesale/evosense/properties/%s" % p.id, headers=h))
    prov = {x["provider"]: x for x in d["provenance"]}
    assert prov["tarrant_tax_roll"]["has_raw"] and prov["tarrant_tax_roll"]["content_hash"]
    assert d["lookups"] and all(x["governor"] in C.LOOKUP_DECISIONS for x in d["lookups"])
    assert d["sms_eligibility"]["verdict"].startswith("UNKNOWN")
    raw = ok(client.get("/wholesale/evosense/properties/%s/observations/%s/raw"
                        % (p.id, prov["tarrant_tax_roll"]["id"]), headers=h))
    assert raw["raw"] == dfw_files["lines"][0]
    assert d["scores"]["property_opportunity"]["version"].startswith("property_opportunity/v2")


def test_pilot_record_cap_is_a_hard_cap(client, db_session, sample_org, dfw_files, fake_apis):
    _, h = _admin(db_session, sample_org)
    _enable(client, h, "tarrant_tax_roll")
    s = _pilot(client, h, pilot_max_properties=2, counties=["Tarrant"])
    ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    assert db_session.query(EvoSenseProperty).filter(
        EvoSenseProperty.organization_id == sample_org.id).count() == 2
    r = client.post("/wholesale/evosense/strategies", headers=h,
                    json={"name": "too big", "states": ["TX"], "pilot_mode": True,
                          "pilot_max_properties": 5000, "property_types": ["single_family"]})
    assert r.status_code == 422


def test_a_pilot_is_never_hunted_by_the_scheduler(client, db_session, sample_org, dfw_files, fake_apis):
    _, h = _admin(db_session, sample_org)
    _enable(client, h, "tarrant_tax_roll")
    s = _pilot(client, h, hunt_cadence="daily")
    rep = SCH.run_due(db_session, org_id=sample_org.id)
    assert rep["ran"] == 0 and rep["manual_only"] >= 1
    assert db_session.query(EvoSenseProperty).filter(
        EvoSenseProperty.organization_id == sample_org.id).count() == 0
    st = ok(client.get("/wholesale/evosense/strategies/%s" % s["id"], headers=h))
    assert st["pilot"]["on"] and st["automation"]["cadence"] == "manual"
    assert st["outreach_policy"]["auto_outreach"] is False


def test_pilot_never_buys_paid_data_even_with_a_paid_provider_connected(db_session, sample_org):
    """The enrichment gate refuses paid lookups for a pilot with paid data off."""
    from app.services.evosense import enrichment as EN
    db = db_session
    s = EvoSenseStrategy(organization_id=sample_org.id, name="p", status="active", pilot_mode=True,
                         daily_budget_cents=10000, min_opportunity_score=0,
                         property_types=json.dumps(["single_family"]), states=json.dumps(["TX"]),
                         is_test=True)
    db.add(s)
    db.flush()
    PV.config(db, sample_org.id, "sandbox_skiptrace").enabled = True
    prop = EvoSenseProperty(organization_id=sample_org.id, street_address="1 Test Ln", state="TX",
                            zip_code="75001", opportunity_score=90, is_test=True, first_strategy_id=s.id)
    db.add(prop)
    db.flush()
    from app.services.evosense import ingest as IN
    IN.upsert_owner(db, sample_org.id, prop, {"name": "Test Owner"}, source="manual", observation_id=None,
                    rank=100, is_test=True)
    dec = EN.decide(db, prop, s)
    assert dec.decision == C.D_BUDGET and "PILOT MODE" in json.loads(dec.reasons)[0]


# ── 4. failures are first-class; UNKNOWN is not NO ─────────────────────────

def test_a_failing_lookup_source_is_recorded_and_concludes_nothing(client, db_session, sample_org,
                                                                   dfw_files, fake_apis, monkeypatch):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "tarrant_tax_roll", "tad")

    def rate_limited(self, targets):
        raise B.SourceError(B.RATE_LIMITED, "TAD answered 429", retry_after=900)
    monkeypatch.setattr(PV.TadSource, "lookup_many", rate_limited)
    s = _pilot(client, h, counties=["Tarrant"])
    out = ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    assert out["status"] == "partial"
    assert out["counts"]["source_errors"][0]["code"] == "RATE_LIMITED"
    p = _prop(db, sample_org.id, "1309")
    assert p.city is None and p.estimated_value is None           # unknown stays unknown
    dec = db.query(EvoSenseEnrichmentDecision).filter(
        EvoSenseEnrichmentDecision.property_id == p.id,
        EvoSenseEnrichmentDecision.provider_key == "tad").one()
    assert dec.outcome == "provider_failed" and "UNKNOWN is not NO" in json.loads(dec.reasons)[0]
    reg = {r["key"]: r for r in ok(client.get("/wholesale/evosense/sources", headers=h))["sources"]}
    assert reg["tad"]["state"] in ("DEGRADED", "FAILED", "RATE LIMITED") and "RATE" in reg["tad"]["why"].upper() + (
        reg["tad"]["last_failure_reason"] or "")


def test_a_discovery_source_failure_does_not_end_the_hunt(client, db_session, sample_org, dfw_files,
                                                          fake_apis, monkeypatch):
    _, h = _admin(db_session, sample_org)
    _enable(client, h, "tarrant_tax_roll", "dcad")

    def broken(self, capability, query):
        raise B.SourceError(B.DOWNLOAD_FAILED, "connection reset")
    monkeypatch.setattr(PV.TarrantTaxRollSource, "search", broken)
    s = _pilot(client, h)
    out = ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    assert out["status"] == "partial" and out["counts"]["sources"]["dcad"]["records"] == 2


def test_second_hunt_skips_fresh_lookups_and_creates_nothing_twice(client, db_session, sample_org,
                                                                   dfw_files, fake_apis):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "tarrant_tax_roll", "tad", "census_geocoder", "fw_code_violations")
    s = _pilot(client, h, counties=["Tarrant"])
    ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    n_props = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == sample_org.id).count()
    n_obs = db.query(EvoSenseObservation).filter(EvoSenseObservation.organization_id == sample_org.id).count()
    ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    assert db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == sample_org.id).count() == n_props
    assert db.query(EvoSenseObservation).filter(EvoSenseObservation.organization_id == sample_org.id).count() == n_obs
    fresh = db.query(EvoSenseEnrichmentDecision).filter(
        EvoSenseEnrichmentDecision.organization_id == sample_org.id,
        EvoSenseEnrichmentDecision.decision == C.L_SKIP_FRESH_DATA).count()
    assert fresh >= 3


# ── 5. rollback ─────────────────────────────────────────────────────────────

def test_pilot_rollback_hides_and_restores_without_deleting(client, db_session, sample_org, dfw_files,
                                                            fake_apis):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "tarrant_tax_roll")
    s = _pilot(client, h, counties=["Tarrant"])
    ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    before = ok(client.get("/wholesale/evosense/inbox", headers=h))["total"]
    assert before == 3
    out = ok(client.post("/wholesale/evosense/strategies/%s/pilot-archive" % s["id"], headers=h))
    assert out["archived"] == 3
    assert ok(client.get("/wholesale/evosense/inbox", headers=h))["total"] == 0
    assert ok(client.get("/wholesale/evosense/inbox", headers=h, params={"archived": True}))["total"] == 3
    assert db.query(EvoSenseObservation).filter(EvoSenseObservation.organization_id == sample_org.id).count() >= 3
    ok(client.post("/wholesale/evosense/strategies/%s/pilot-restore" % s["id"], headers=h))
    assert ok(client.get("/wholesale/evosense/inbox", headers=h))["total"] == 3


# ── 6. tenant isolation ─────────────────────────────────────────────────────

def test_sources_and_evidence_are_per_tenant(client, db_session, sample_org, dfw_files, fake_apis):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "tarrant_tax_roll")
    s = _pilot(client, h, counties=["Tarrant"])
    ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    p = _prop(db, sample_org.id, "1309")
    o = db.query(EvoSenseObservation).filter(EvoSenseObservation.property_id == p.id).first()

    org_b = Organization(name="Atlantis Test", slug="atlantis-test-dfw", plan="standard", industry="real_estate")
    db.add(org_b)
    db.commit()
    _, hb = _admin(db, org_b, email="b@atlantis.test")
    reg_b = {r["key"]: r for r in ok(client.get("/wholesale/evosense/sources", headers=hb))["sources"]}
    assert reg_b["tarrant_tax_roll"]["state"] == "DISABLED"         # A's switch is not B's
    assert client.get("/wholesale/evosense/properties/%s/observations/%s/raw" % (p.id, o.id),
                      headers=hb).status_code == 404
    assert ok(client.get("/wholesale/evosense/inbox", headers=hb))["total"] == 0
    assert client.post("/wholesale/evosense/strategies/%s/pilot-archive" % s["id"], headers=hb).status_code == 404


# ── 7. found phone != consent; scoring is deterministic and configurable ────

def test_a_found_phone_is_not_sms_permission(client, db_session, sample_org, dfw_files, fake_apis):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "tarrant_tax_roll")
    s = _pilot(client, h, counties=["Tarrant"])
    ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    p = _prop(db, sample_org.id, "1309")
    ok(client.post("/wholesale/evosense/properties/%s/contacts" % p.id, headers=h,
                   json={"kind": "phone", "value": "8175550142"}))
    d = ok(client.get("/wholesale/evosense/properties/%s" % p.id, headers=h))
    v = d["sms_eligibility"]
    assert v["verdict"] == "NOT ELIGIBLE"
    assert "NO_SMS_CONSENT" in v["phones"][0]["reasons"]


def test_score_weights_are_configurable_fingerprinted_and_deterministic(client, db_session, sample_org,
                                                                        dfw_files, fake_apis):
    db = db_session
    _, h = _admin(db, sample_org)
    _enable(client, h, "tarrant_tax_roll")
    s = _pilot(client, h, counties=["Tarrant"])
    ok(client.post("/wholesale/evosense/strategies/%s/hunt" % s["id"], headers=h))
    p = _prop(db, sample_org.id, "1309")
    base = p.opportunity_score
    ctl = ok(client.patch("/wholesale/evosense/controls", headers=h,
                          json={"score_weights": {"TAX_DELINQUENT": 25, "NOT_A_SIGNAL": 99}}))
    assert ctl["score_weights"] == {"TAX_DELINQUENT": 25}
    r1 = ok(client.post("/wholesale/evosense/properties/%s/rescore" % p.id, headers=h))
    d = ok(client.get("/wholesale/evosense/properties/%s" % p.id, headers=h))
    po = d["scores"]["property_opportunity"]
    assert "+w" in po["version"] and po["value"] > base
    assert po["inputs"]["weights"] == {"TAX_DELINQUENT": 25}
    again = ok(client.get("/wholesale/evosense/properties/%s" % p.id, headers=h))["scores"]["property_opportunity"]
    assert again["value"] == po["value"]
    assert r1 is not None


def test_weights_cannot_be_set_by_a_non_admin(client, auth_headers):
    r = client.patch("/wholesale/evosense/controls", headers=auth_headers,
                     json={"score_weights": {"TAX_DELINQUENT": 30}})
    assert r.status_code == 403


def test_negative_signals_subtract_and_never_stack():
    class P:
        equity_pct = None
        estimated_value = 150000
        ownership_years = None
        property_type = "single_family"
        equity_basis = None
        estimated_value_source = None
    stacked = [
        {"signal_type": "TAX_DELINQUENT", "freshness": "current", "derived": False, "sources": ["x"], "observed_at": None},
        {"signal_type": "ACTIVE_LISTING", "freshness": "current", "derived": False, "sources": ["y"], "observed_at": None},
    ]
    res = SC.property_opportunity(P(), stacked, None)
    labels = [f["label"] for f in res["factors"]]
    assert res["value"] == 0 and not any("Two signals" in l for l in labels)
