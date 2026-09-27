"""Who SAYS they buy vs who ACTUALLY buys.

The standing is read off what a buyer has done on this organization's deals and
never off what they claimed. It sits beside the match score, never inside it.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.models import Organization
from app.models.wholesale_models import WholesaleBuyer, WholesaleBuyerOutreach
from app.services.wholesale_matching import UNRESPONSIVE_AFTER, buyer_standing


def ok(response):
    assert response.status_code in (200, 201), "%s %s" % (response.status_code, response.text[:300])
    return response.json()


@pytest.mark.parametrize("activity,claimed,expected", [
    (None, 0, "new"),
    ({"sheets_sent": 0}, 12, "new"),
    ({"sheets_sent": 1}, 0, "unproven"),
    ({"sheets_sent": UNRESPONSIVE_AFTER - 1}, 20, "unproven"),
    ({"sheets_sent": UNRESPONSIVE_AFTER}, 20, "unresponsive"),
    ({"sheets_sent": 5, "responded": 1}, 0, "responsive"),
    ({"sheets_sent": 5, "responded": 2, "offers_made": 1}, 0, "active"),
    ({"sheets_sent": 5, "responded": 2, "selected_count": 1}, 0, "active"),
    ({"sheets_sent": 9, "responded": 0, "deals_closed": 1}, 0, "proven"),
])
def test_standing_is_the_strongest_measured_evidence(activity, claimed, expected):
    s = buyer_standing(activity, SimpleNamespace(past_deals_count=claimed))
    assert s["standing"] == expected and s["label"] and s["why"]
    assert s["claimed_past_deals"] == claimed       # reported ...
    if claimed and expected in ("new", "unproven"):
        assert "not measured" in s["why"]
    assert s["closed_here"] == int((activity or {}).get("deals_closed") or 0)   # ... never counted


def test_a_big_claim_never_makes_a_buyer_proven():
    s = buyer_standing({"sheets_sent": 0}, SimpleNamespace(past_deals_count=500))
    assert s["standing"] == "new" and s["closed_here"] == 0


def test_response_rate_only_when_something_was_sent():
    assert buyer_standing({"sheets_sent": 0}, None)["response_rate"] is None
    assert buyer_standing({"sheets_sent": 4, "responded": 1}, None)["response_rate"] == 0.25


def _buyer(db, org_id, name, claimed=0, is_test=False):
    b = WholesaleBuyer(organization_id=org_id, company_name=name, email="%s@b.test" % name.lower(),
                       past_deals_count=claimed, is_test=is_test)
    db.add(b)
    db.flush()
    return b


@pytest.fixture
def deal_id(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "5 Standing Way", "city": "Dallas", "state": "TX",
                                "county": "Dallas", "zip_code": "75201", "is_test": True}))
    return prop["deal"]["id"]


def _sheet(db, org_id, deal, buyer, status="sent", replied=False):
    o = WholesaleBuyerOutreach(organization_id=org_id, deal_id=deal, buyer_id=buyer.id,
                               status=status, sent_at=datetime.utcnow() - timedelta(days=1),
                               replied_at=datetime.utcnow() if replied else None)
    db.add(o)
    return o


def test_buyer_list_reports_standing_from_this_orgs_activity_only(client, db_session, auth_headers,
                                                                   sample_org, deal_id):
    loud = _buyer(db_session, sample_org.id, "Loud", claimed=40)
    quiet = _buyer(db_session, sample_org.id, "Quiet")
    for _ in range(UNRESPONSIVE_AFTER):
        _sheet(db_session, sample_org.id, deal_id, loud)
    _sheet(db_session, sample_org.id, deal_id, quiet, status="replied", replied=True)
    # Another organization's activity for a same-named buyer never counts here.
    other = Organization(name="Other Wholesale", slug="other-ws-standing", plan="enterprise")
    db_session.add(other)
    db_session.flush()
    stranger = _buyer(db_session, other.id, "Loud")
    db_session.commit()

    buyers = ok(client.get("/wholesale/buyers?with_activity=true", headers=auth_headers))["buyers"]
    by = {b["company_name"]: b for b in buyers}
    assert set(by) == {"Loud", "Quiet"}
    assert by["Loud"]["standing"]["standing"] == "unresponsive"
    assert by["Loud"]["standing"]["claimed_past_deals"] == 40
    assert by["Quiet"]["standing"]["standing"] == "responsive"
    assert stranger.id not in {b["id"] for b in buyers}
    # Without the activity, no verdict is invented.
    plain = ok(client.get("/wholesale/buyers", headers=auth_headers))["buyers"]
    assert all(b["standing"] is None for b in plain)


def test_standing_is_beside_the_match_score_not_inside_it(client, db_session, auth_headers,
                                                          sample_org, deal_id):
    b = _buyer(db_session, sample_org.id, "Boxed", is_test=True)   # test deals match test buyers
    db_session.commit()
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % b.id, headers=auth_headers,
                   json={"cities": ["Dallas"], "states": ["TX"]}))
    first = ok(client.post("/wholesale/deals/%s/match-buyers" % deal_id, headers=auth_headers, json={}))
    score_before = {m["buyer_id"]: m["score"] for m in first["matches"]}[b.id]
    for _ in range(UNRESPONSIVE_AFTER):
        _sheet(db_session, sample_org.id, deal_id, b)
    db_session.commit()
    again = ok(client.post("/wholesale/deals/%s/match-buyers" % deal_id, headers=auth_headers, json={}))
    assert {m["buyer_id"]: m["score"] for m in again["matches"]}[b.id] == score_before
    room = ok(client.get("/wholesale/deals/%s" % deal_id, headers=auth_headers))
    m = next(x for x in room["buyer_matches"] if x["buyer_id"] == b.id)
    assert m["standing"]["standing"] == "unresponsive" and m["score"] == score_before
