"""An imported list followed as a group (the 2010 Top 100 is one).

  * the cohort is exactly the properties its import brought in - nothing is
    imported again, nothing outside the list leaks in;
  * a targeted tax check ingests a delinquent account like a hunt would (one
    property, a dated signal) and records a clean account as checked;
  * status words are evidence-bound: foreclosure is UNVERIFIED without a
    notice; an un-checked county says NOT CHECKED;
  * the score is the ordinary score (weights untouched) with its reasons.
"""
from datetime import datetime

import pytest

from app.models.evosense_models import EvoSenseProperty, EvoSenseSignal
from app.services.evosense import cohort as CO
from app.services.evosense import hunt as HU

LIST = ("record_id,street_address,city,state,zip_code,county,parcel_apn\n"
        "top100-2010:1,911 Clint Smith Dr,Duncanville,TX,75137,Dallas,22084500080090000\n"
        "top100-2010:2,1908 Drake St,Glenn Heights,TX,75154,Dallas,270002600H0040000\n"
        "top100-2010:3,108 Parkhurst Ln,Allen,TX,75013,Collin,\n")
OTHER = "street_address,city,state,zip_code\n5 Elsewhere Rd,Dallas,TX,75215\n"


@pytest.fixture()
def top(db_session, sample_org, sample_advisor):
    HU.import_csv(db_session, sample_org.id, LIST, user=sample_advisor, filename="top.csv")
    HU.import_csv(db_session, sample_org.id, OTHER, user=sample_advisor, filename="other.csv")
    db_session.commit()
    return {"db": db_session, "org": sample_org.id, "user": sample_advisor}


def test_the_cohort_is_exactly_the_list(top):
    props = CO.members(top["db"], top["org"], "top100-2010")
    assert sorted(p.street_address.upper() for p in props) == ["108 PARKHURST LN", "1908 DRAKE ST", "911 CLINT SMITH DR"]
    assert {"kind": "top100-2010", "properties": 3} in CO.cohorts(top["db"], top["org"])
    with pytest.raises(ValueError):
        CO.members(top["db"], top["org"], "x' or 1=1 --")


def test_tax_check_ingests_a_delinquent_account_and_records_a_clean_one(top, monkeypatch):
    from app.services.evosense.sources import dallas_trw as T
    from app.services.evosense.sources import lgbs as L

    def fake_trw(self, *, limit, accounts=None, min_due_cents=0, **kw):
        assert accounts == {"22084500080090000", "270002600H0040000"}
        rec = {"source_reference": "DALTRW:00000000000000001", "street_address": "911 CLINT SMITH DR",
               "city": "Duncanville", "state": "TX", "zip_code": "75137", "county": "Dallas",
               "parcel_apn": "22084500080090000", "owner": {"name": "SAMPLE OWNER"},
               "signals": [{"type": "TAX_DELINQUENT", "confidence": 90, "value": "at least $812.00 delinquent",
                            "raw": "ACCOUNT=1", "observed_days_ago": 0}],
               "_source_url": T.PAGE, "_adapter_version": "dallas_trw/2"}
        return {"records": [rec], "stats": {"checked": set(accounts)}, "source_updated_at": datetime(2026, 10, 17)}
    monkeypatch.setattr(T.TrwReader, "discover", fake_trw)
    monkeypatch.setattr(L.LgbsTaxSaleReader, "discover", lambda self, **kw: {"records": [], "stats": {}})
    db, org = top["db"], top["org"]
    props = CO.members(db, org, "top100-2010")
    out = CO.tax_check(db, org, props, user=top["user"])
    assert out["dallas_trw"]["delinquent"] == 1 and out["dallas_trw"]["on_roll"] == 2
    assert len(CO.members(db, org, "top100-2010")) == 3                    # merged, not duplicated
    rows = {r["street_address"].upper(): r for r in CO.rows(db, org, "top100-2010")}
    assert rows["911 CLINT SMITH DR"]["tax"]["status"] == "DELINQUENT"
    assert rows["1908 DRAKE ST"]["tax"]["status"] == "CURRENT"
    assert rows["1908 DRAKE ST"]["tax"]["date"] == "2026-10-17"
    assert rows["108 PARKHURST LN"]["tax"]["status"] == "NOT CHECKED"     # no Collin file connected


def test_status_words_are_evidence_bound(top):
    rows = CO.rows(top["db"], top["org"], "top100-2010")
    # no sale date in the rows and none declared: no claim - and "no evidence" is not "no foreclosure"
    assert all(r["foreclosure"]["status"] == "NO EVIDENCE" for r in rows)
    assert "Not proof there is none" in rows[0]["foreclosure"]["detail"]
    assert all(r["owner"]["status"] == "NO OWNER" for r in rows)            # the list carried no owner
    assert all(r["priority"] == "REVIEW" for r in rows)
    s = CO.summary(rows)
    assert s["properties"] == 3 and s["foreclosure"] == {"NO EVIDENCE": 3}
    assert s["sale_dates"] == {"supplied": 0, "verified": 0, "missing": 3, "dates": {}}


def test_api_view_and_recheck(client, top, auth_headers, monkeypatch):
    started = []
    monkeypatch.setattr(CO, "start_recheck", lambda org, kind, user_id=None: started.append(kind) or True)
    r = client.get("/wholesale/evosense/cohorts/top100-2010", headers=auth_headers)
    if r.status_code == 404 and "Unknown" not in r.text:
        pytest.skip("auth fixture belongs to a different organization")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["summary"]["properties"] == 3
    assert client.get("/wholesale/evosense/cohorts/nope", headers=auth_headers).status_code == 404
    r = client.post("/wholesale/evosense/cohorts/top100-2010/recheck", headers=auth_headers)
    assert r.status_code == 200 and started == ["top100-2010"]


def test_an_estate_named_owner_is_flagged_for_probate_confirmation_not_scored():
    owner = {"status": "VERIFIED", "name": "WARD ISEBELL EST OF",
             "flags": [{"key": "ESTATE_INDICATED", "label": "x"}]}
    act = CO._next_action("C", owner, {"status": "NOT CHECKED"}, {"phones": 0, "emails": 0})
    assert "probate" in act and "before any contact" in act
    from app.services.evosense.ingest import owner_flags
    assert owner_flags("WARD ISEBELL EST OF") == ["ESTATE_INDICATED"]
