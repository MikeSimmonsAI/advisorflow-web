"""Denton County through the county's public GIS parcel layer. Row shapes copied
from the live layer (owner replaced by a fictional name). No network here."""
from datetime import datetime

from app.services.evosense.sources import base as B
from app.services.evosense.sources import denton as DN
from app.services.evosense import providers as PV

ROW = {"prop_id": 247593, "PID": "247593", "prop_val_yr": 2026, "owner_name": "SAMPLE, PAT & JO",
       "confidential": 0, "state_cd": "A1", "situs_num": "3004", "situs_street": "SOUTHMOOR",
       "situs_street_sufix": "TRL", "situs_city": "FLOWER MOUND", "situs_zip": "75022-1055",
       "addr_line2": "PO BOX 44", "addr_city": "PLANO", "addr_state": "TX", "addr_zip": "75024-1055",
       "yr_blt": 2004, "living_area": 3376.0, "cert_mkt_val": 880976, "main_imprv_val": 663444,
       "exemptions": "OTHER", "instrumentNum": "05-94548", "deedType": "GNV",
       "property_url": "https://denton.prodigycad.com/property-detail/247593"}
TODAY = datetime(2026, 10, 10)


def test_deed_year_is_a_year_never_a_date():
    assert DN.deed_year("2018-137347", TODAY) == 2018
    assert DN.deed_year("05-94548", TODAY) == 2005
    assert DN.deed_year("98-1234", TODAY) == 1998
    assert DN.deed_year("", TODAY) is None and DN.deed_year("ABC", TODAY) is None


def test_record_fields():
    rec = DN.DentonReader().record(ROW, today=TODAY)
    assert rec["parcel_apn"] == "247593" and rec["county"] == "Denton"
    assert rec["street_address"] == "3004 SOUTHMOOR TRL" and rec["zip_code"] == "75022"
    assert rec["property_type"] == "single_family" and rec["square_feet"] == 3376
    assert rec["bedrooms"] is None and rec["bathrooms"] is None
    assert rec["deed_transfer_date"] == "2005-01-01" and "year only" in rec["_evidence"]["deed_basis"]
    assert rec["owner"]["mailing_street"] == "PO BOX 44" and rec["owner"]["mailing_city"] == "Plano"
    assert rec["occupancy"] is None and rec["appraisal"]["district"] == "Denton CAD"


def test_confidential_owner_is_never_read():
    rec = DN.DentonReader().record(dict(ROW, confidential=1), today=TODAY)
    assert rec["owner"] is None and "owner_name" not in rec["_raw"] and "addr_city" not in rec["_raw"]


def test_discover_one_page_with_house_and_absentee_filters(monkeypatch):
    seen = []

    def fake(url, params=None):
        seen.append(dict(params))
        if params.get("returnCountOnly"):
            return {"count": 2}
        recent = dict(ROW, prop_id=2, instrumentNum="2024-1")
        return {"features": [{"attributes": ROW}, {"attributes": recent}]}
    monkeypatch.setattr(B, "get_json", fake)
    res = DN.DentonReader().discover(limit=5, min_years=10, today=TODAY, rotate=0)
    w = seen[0]["where"]
    assert "state_cd LIKE 'A1%'" in w and "main_imprv_val > 0" in w and "NOT LIKE '%HS%'" in w
    assert "confidential = 0" in w and int(seen[1]["resultRecordCount"]) <= 1000
    assert [r["parcel_apn"] for r in res["records"]] == ["247593"]
    assert res["stats"]["recent_or_unknown_deed"] == 1


def test_registered_and_scoped_to_denton():
    src = PV.PROVIDERS["denton_gis"]
    assert src.discovery and src.applies({"county": "Denton", "parcel_apn": "247593"})
    assert not src.applies({"county": "Collin", "parcel_apn": "R-1"})
    assert src.search(PV.C.PROPERTY_SEARCH, {"counties": ["Dallas"]}).stats["skipped"]
