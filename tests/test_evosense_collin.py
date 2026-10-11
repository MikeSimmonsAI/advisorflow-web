"""Collin CAD through the State of Texas open-data portal, and the durable copy
of operator-uploaded public files (TAD) that survives a deploy.

The Collin row below is the shape the portal returns (fields copied from a
real 2026 row, with the owner replaced by a fictional name). Tests here never
call the network; a live check is in scripts / the deploy verification."""
import json
import os
from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app.services.evosense.sources import base as B
from app.services.evosense.sources import collin as CO
from app.services.evosense import providers as PV

ROW = {
    "propyear": "2026", "propid": "2961281", "geoid": "R-13769-00C-0020-1", "proptype": "Real",
    "propsubtype": "Residential", "propcategorycode": "A",
    "situsbldgnum": "6621", "situsstreetname": "BAXLEY", "situsstreetsuffix": "RD",
    "situscity": "FRISCO", "situszip": "75033",
    "ownername": "SAMPLE OWNER &", "ownernameaddtl": "PAT SAMPLE",
    "owneraddrline1": "100 ELM ST", "owneraddrcity": "PLANO", "owneraddrstate": "TX",
    "owneraddrzip": "75024-4178",
    "deedtypecd": "WD", "deedeffdate": "2005-12-19T00:00:00.000",
    "imprvyearbuilt": "1998", "imprvmainarea": "3680", "exempthmstdflag": False,
    "propstatus": "Preliminary", "currvalyear": "2026", "currvalimprv": "573264",
    "currvalland": "210000", "currvalmarket": "783264", "datadate": "2026-10-05T00:48:05.187",
}


def test_record_maps_the_district_fields_and_never_invents_beds():
    rec = CO.CollinReader(dataset="5tkr-3759").record(ROW)
    assert rec["parcel_apn"] == "R-13769-00C-0020-1" and rec["county"] == "Collin"
    assert rec["street_address"] == "6621 BAXLEY RD" and rec["city"] == "Frisco" and rec["zip_code"] == "75033"
    assert rec["property_type"] == "single_family"
    assert rec["square_feet"] == 3680 and rec["year_built"] == 1998
    assert rec["bedrooms"] is None and rec["bathrooms"] is None          # not published
    assert rec["deed_transfer_date"] == "2005-12-19" and rec["occupancy"] is None
    assert rec["owner"]["name"] == "SAMPLE OWNER & PAT SAMPLE"
    assert rec["owner"]["mailing_street"] == "100 ELM ST" and rec["owner"]["mailing_zip"] == "75024"
    ap = rec["appraisal"]
    assert ap["value"] == 783264 and ap["improvements"] == 573264 and ap["district"] == "Collin CAD"
    assert "preliminary" in ap["basis"]                                   # the status travels
    assert rec["_evidence"]["value_status"] == "Preliminary"


def test_category_decides_the_type():
    assert CO.property_type({"propcategorycode": "A", "currvalimprv": "0"}) == "land"
    assert CO.property_type({"propcategorycode": "B"}) == "multifamily"
    assert CO.property_type({"propcategorycode": "C1"}) == "land"
    assert CO.property_type({"propcategorycode": "F1"}) == "commercial"
    assert CO.property_type({"propcategorycode": "O"}) is None


def test_discover_asks_the_portal_for_absentee_long_held_houses(monkeypatch):
    calls = []

    def fake(url, params=None):
        calls.append((url, dict(params or {})))
        if params.get("$select") == "count(*)":
            return [{"count": "3"}]
        return [ROW, dict(ROW, propid="2", geoid="R-2", ownername="CITY OF PLANO")]
    monkeypatch.setattr(B, "get_json", fake)
    res = CO.CollinReader(dataset="5tkr-3759").discover(limit=5, min_years=10,
                                                       today=datetime(2026, 10, 10), rotate=0)
    where = calls[1][1]["$where"]
    assert "propcategorycode = 'A'" in where and "currvalimprv > 0" in where
    assert "exempthmstdflag = false" in where and "upper(owneraddrcity) != upper(situscity)" in where
    assert "deedeffdate < '2016-" in where
    assert calls[1][0].endswith("/resource/5tkr-3759.json")
    assert [r["parcel_apn"] for r in res["records"]] == ["R-13769-00C-0020-1"]   # government skipped
    assert res["stats"]["matching"] == 3 and res["stats"]["government"] == 1


def test_lookup_by_geoid_and_propid(monkeypatch):
    seen = {}

    def fake(url, params=None):
        seen["where"] = params["$where"]
        return [ROW]
    monkeypatch.setattr(B, "get_json", fake)
    out = CO.CollinReader(dataset="5tkr-3759").lookup_many(["R-13769-00C-0020-1", "999"])
    assert "geoid in('R-13769-00C-0020-1')" in seen["where"] and "propid in(999)" in seen["where"]
    assert list(out["records"]) == ["R-13769-00C-0020-1"]


def test_source_is_registered_and_targets_collin_only(monkeypatch):
    src = PV.PROVIDERS["collin_cad"]
    assert src.discovery and src.applies({"county": "Collin", "parcel_apn": "R-1"})
    assert not src.applies({"county": "Dallas", "parcel_apn": "1"})
    out = src.search(PV.C.PROPERTY_SEARCH, {"states": ["TX"], "counties": ["Dallas"]})
    assert out.stats["skipped"]


def test_uploaded_file_survives_a_wiped_disk(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("EVOSENSE_SOURCE_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(PV, "DURABLE_CHUNK", 1000)          # force several chunks
    data = os.urandom(3500)
    src = tmp_path / "incoming.zip"
    src.write_bytes(data)
    meta = B.store_upload("tad", str(src), {"source_date": "Sat, 10 Oct 2026"})
    assert PV.persist_upload(db_session, "tad", B.uploaded_path("tad"), meta) is True

    os.remove(B.uploaded_path("tad"))                        # the deploy wipes the disk
    factory = sessionmaker(bind=db_session.get_bind())
    assert PV.restore_upload("tad", factory=factory) is True
    with open(B.uploaded_path("tad"), "rb") as fh:
        assert fh.read() == data
    info = B.uploaded_info("tad")
    assert info["source_date"] == "Sat, 10 Oct 2026" and info.get("restored_at")


def test_a_damaged_durable_copy_is_not_restored(db_session, tmp_path, monkeypatch):
    from app.models.evosense_models import EvoSenseSourceFile
    monkeypatch.setenv("EVOSENSE_SOURCE_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(PV, "DURABLE_CHUNK", 1000)
    src = tmp_path / "incoming.zip"
    src.write_bytes(os.urandom(2500))
    meta = B.store_upload("tad", str(src), {})
    PV.persist_upload(db_session, "tad", B.uploaded_path("tad"), meta)
    db_session.query(EvoSenseSourceFile).filter(EvoSenseSourceFile.chunk_index == 1).delete()
    db_session.commit()
    os.remove(B.uploaded_path("tad"))
    assert PV.restore_upload("tad", factory=sessionmaker(bind=db_session.get_bind())) is False
    assert not os.path.exists(B.uploaded_path("tad"))
