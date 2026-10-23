"""Travis delinquent-tax adapter. Fixture headers are ASSUMED, not copied from a live
file (the build sandbox had no access to the county site). No network, no paid calls."""
from datetime import datetime

import pytest

from app.services.evosense.sources import base as B
from app.services.evosense.sources import travis_delinquent as TD

TODAY = datetime(2026, 10, 22)
CSV = """Account Number,Owner Name,Tax Year,Total Due,Situs Address,Mailing Address,Mailing City,Mailing State,Mailing Zip
0101,SAMPLE PAT,2024,"$1,200.50",12 Oak Street,PO BOX 4,AUSTIN,TX,78701-0004
0101,SAMPLE PAT,2025,800.00,12 Oak Street,PO BOX 4,AUSTIN,TX,78701-0004
0101,SAMPLE PAT,2025,800.00,12 Oak Street,PO BOX 4,AUSTIN,TX,78701-0004
0202,SAMPLE LOT LLC,2024,900.00,500 Vacant Rd,,,,
0303,,2024,900.00,7 Elm Dr,,,,
0404,SAMPLE JO,2026,900.00,9 Pine Ln,,,,
0505,SAMPLE LOW,2024,50.00,1 Tiny Ln,,,,
0606,SAMPLE MISMATCH,2024,900.00,3 Ash Ct,,,,
"""
PROPS = {
    "0101": {"property_type": "single_family", "improved": True, "street_address": "12 OAK ST"},
    "0202": {"property_type": "land", "improved": False, "street_address": "500 VACANT RD"},
    "0303": {"property_type": "single_family", "street_address": "7 ELM DR"},
    "0404": {"property_type": "single_family", "street_address": "9 PINE LN"},
    "0505": {"property_type": "single_family", "street_address": "1 TINY LN"},
    "0606": {"property_type": "single_family", "street_address": "99 OTHER ST"},
}


def run(**kw):
    return TD.TravisDelinquentReader().discover_csv(CSV, limit=10, today=TODAY, **kw)


def test_only_past_due_residential_matched_owner_verified_qualifies():
    out = run(residential_lookup=PROPS.get)
    assert [r["parcel_apn"] for r in out["records"]] == ["0101"]
    st = out["stats"]
    assert st["not_residential"] == 1 and st["missing_owner"] == 1 and st["situs_mismatch"] == 1
    assert st["below_min_due"] == 1 and st["not_past_due"] == 1      # 2026 levy is not yet delinquent


def test_duplicate_rows_are_summed_by_year_not_double_counted_as_accounts():
    rec = run(residential_lookup=PROPS.get)["records"][0]
    assert rec["_raw"]["years"] == [2024, 2025]
    assert rec["_raw"]["due"] == 2000.5          # the repeated 2025 row counted once
    assert rec["owner"]["mailing_zip"] == "78701"
    assert rec["signals"][0]["type"] == "TAX_DELINQUENT"
    assert rec["_evidence"] == {"capability": "tax delinquency", "live_verified": False}


def test_no_lookup_means_no_leads():
    out = run()
    assert out["records"] == [] and out["stats"]["no_residential_lookup"] >= 1


def test_year_before_feb_first_rolls_back():
    assert TD.current_tax_year(datetime(2027, 1, 15)) == 2026
    assert TD.current_tax_year(datetime(2027, 2, 2)) == 2027


def test_unknown_header_fails_loudly():
    with pytest.raises(B.SourceError) as e:
        TD.TravisDelinquentReader().discover_csv("foo,bar\n1,2\n", limit=1)
    assert e.value.code == B.SOURCE_FORMAT_CHANGED


def test_limit_caps_output():
    assert len(run(residential_lookup=PROPS.get)["records"]) <= 10
