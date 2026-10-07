"""Regression coverage for the 2026-10-07 Wholesale fixes.

Each test pins one defect that was fixed in commits 2b1506c..e541333 so it
cannot come back: a suppressed phone texted as a buyer, a deal closed twice or
closed from Dead, a negative fee, a strategy-only buy box scoring 100 on every
deal, rankings that depended on database order, and matching that read across
tenants.

NOTHING HERE REACHES A PROVIDER. Every SMS/email provider seam is replaced with
a recorder, and the refusal tests assert the recorder was never called.
"""

import json
from types import SimpleNamespace

import pytest

from app.models.models import (Organization, SuppressionEntry, SuppressionSource,
                               User)
from app.services.auth_service import create_access_token, hash_password
from app.services.wholesale_matching import match_deal_to_buyers, score_buy_box


def ok(response):
    assert response.status_code in (200, 201), \
        "%s %s" % (response.status_code, response.text[:400])
    return response.json()


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def live_deal(client, auth_headers):
    """A NON-sandbox deal with an ARV and an offer, plus one buyer with a phone."""
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "7 Regression Way",
                                "city": "Dallas", "state": "TX",
                                "zip_code": "75201", "county": "Dallas",
                                "property_type": "single_family",
                                "bedrooms": 3, "bathrooms": 2,
                                "square_feet": 1500}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Rae", "last_name": "Seller",
                         "phone": "2145557002"}))
    deal_id = prop["deal"]["id"]
    ok(client.patch("/wholesale/deals/%s/analysis" % deal_id,
                    headers=auth_headers,
                    json={"arv": 300000, "repair_estimate": 40000,
                          "proposed_offer": 150000}))
    buyer = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Suppressed Capital",
                                 "email": "suppressed@example.com",
                                 "phone": "2145558099"}))
    return {"property": prop, "deal_id": deal_id, "buyer": buyer}


@pytest.fixture()
def other_headers(db_session):
    org = Organization(name="Other Wholesaler", slug="other-wholesale-reg",
                       plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    user = User(organization_id=org.id, email="other@wholesale-reg.test",
                password_hash=hash_password("TestPass123!"),
                full_name="Other", role="org_admin", must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(user, db_session),
            "org_id": org.id}


def _headers_only(h):
    return {"Authorization": h["Authorization"]}


# ── 1. Suppressed buyer deal-sheet SMS ──────────────────────────────────────

def test_a_suppressed_buyer_phone_is_refused_before_any_provider_is_touched(
        client, auth_headers, live_deal, db_session, monkeypatch):
    """The refusal sits ahead of the deployment switch, so it holds whether or
    not SMS is turned on, and `_send_sms` (which calls Twilio directly) is never
    reached."""
    from app.models.models import User as _U
    from app.services import wholesale_disposition as disposition

    calls = []
    monkeypatch.setattr(disposition, "_send_sms",
                        lambda *a, **k: calls.append((a, k)), raising=False)
    monkeypatch.setenv(disposition.SMS_ENV, "true")

    org_id = db_session.query(_U).first().organization_id
    db_session.add(SuppressionEntry(organization_id=org_id, phone="+12145558099",
                                    reason="replied STOP",
                                    source=SuppressionSource.REPLY_STOP))
    db_session.commit()

    result = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                            headers=auth_headers,
                            json={"buyer_ids": [live_deal["buyer"]["id"]],
                                  "channel": "sms"}))
    assert result["results"][0]["code"] == "suppressed"
    assert "opt-out" in result["results"][0]["reason"]
    assert calls == []


def test_suppression_does_not_block_the_email_channel(
        client, auth_headers, live_deal, db_session):
    """The list is keyed by phone: it must not refuse a channel it says nothing
    about (that would hide the real, different reason for a refusal)."""
    from app.models.models import User as _U

    org_id = db_session.query(_U).first().organization_id
    db_session.add(SuppressionEntry(organization_id=org_id, phone="+12145558099",
                                    reason="replied STOP",
                                    source=SuppressionSource.REPLY_STOP))
    db_session.commit()
    result = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                            headers=auth_headers,
                            json={"buyer_ids": [live_deal["buyer"]["id"]],
                                  "channel": "email"}))
    assert result["results"][0]["code"] != "suppressed"


# ── 2 + 3. Closing happens once; money is never negative ────────────────────

def _deal_room(client, auth_headers, deal_id):
    return ok(client.get("/wholesale/deals/%s" % deal_id,
                         headers=auth_headers))["deal"]


def test_a_second_close_is_a_409_and_changes_nothing(client, auth_headers, live_deal):
    d = live_deal["deal_id"]
    ok(client.post("/wholesale/deals/%s/close" % d, headers=auth_headers,
                   json={"wholesale_fee_collected": 12000,
                         "fee_payment_reference": "WIRE-1"}))
    before = _deal_room(client, auth_headers, d)

    again = client.post("/wholesale/deals/%s/close" % d, headers=auth_headers,
                        json={"wholesale_fee_collected": 99999,
                              "fee_payment_reference": "WIRE-2"})
    assert again.status_code == 409
    assert "already closed" in again.json()["detail"]
    assert _deal_room(client, auth_headers, d) == before


def test_a_dead_deal_cannot_be_closed_back_to_life(client, auth_headers, live_deal):
    d = live_deal["deal_id"]
    ok(client.post("/wholesale/deals/%s/lost" % d, headers=auth_headers,
                   json={"reason": "price_too_high"}))
    before = _deal_room(client, auth_headers, d)
    assert before["stage"] == "dead"

    response = client.post("/wholesale/deals/%s/close" % d, headers=auth_headers,
                           json={"wholesale_fee_collected": 5000})
    assert response.status_code == 409
    assert "dead" in response.json()["detail"]
    after = _deal_room(client, auth_headers, d)
    assert after == before
    assert after["stage"] == "dead"


def test_a_negative_fee_at_close_is_a_400_and_the_deal_stays_open(
        client, auth_headers, live_deal):
    d = live_deal["deal_id"]
    before = _deal_room(client, auth_headers, d)
    response = client.post("/wholesale/deals/%s/close" % d, headers=auth_headers,
                           json={"wholesale_fee_collected": -1})
    assert response.status_code == 400
    assert "negative" in response.json()["detail"]
    assert _deal_room(client, auth_headers, d) == before


def test_a_negative_collected_amount_is_a_400_and_records_nothing(
        client, auth_headers, live_deal):
    d = live_deal["deal_id"]
    before = _deal_room(client, auth_headers, d)
    response = client.post("/wholesale/deals/%s/fee-collected" % d,
                           headers=auth_headers, json={"amount": -250})
    assert response.status_code == 400
    assert "negative" in response.json()["detail"]
    assert _deal_room(client, auth_headers, d) == before


def test_a_fee_that_lands_later_still_goes_through_fee_collected(
        client, auth_headers, live_deal):
    """The 409 on re-close must not strand the legitimate follow-up."""
    d = live_deal["deal_id"]
    ok(client.post("/wholesale/deals/%s/close" % d, headers=auth_headers, json={}))
    ok(client.post("/wholesale/deals/%s/fee-collected" % d, headers=auth_headers,
                   json={"amount": 11000, "reference": "WIRE-LATE"}))


# ── 4. Strategy-only and unscoreable buy boxes ──────────────────────────────

def _box(**kw):
    base = dict(id="box1", is_active=True, markets=None, states=None,
                counties=None, cities=None, zips=None, property_types=None,
                strategies=None, min_price=None, max_price=None, min_beds=None,
                max_beds=None, min_baths=None, min_sqft=None, max_sqft=None,
                min_year_built=None, max_year_built=None, rehab_tolerance=None,
                min_spread=None)
    for key, value in kw.items():
        base[key] = json.dumps(value) if isinstance(value, list) else value
    return SimpleNamespace(**base)


def _prop(**kw):
    base = dict(street_address="1 Test St", city="dallas", state="tx",
                county="dallas", market="dfw", zip_code="75201",
                property_type="single_family", bedrooms=3, bathrooms=2,
                square_feet=1500, year_built=1985)
    base.update(kw)
    return SimpleNamespace(**base)


def _deal(**kw):
    base = dict(arv=300000, repair_estimate=30000, buyer_price=165000,
                contract_price=150000, proposed_offer=150000)
    base.update(kw)
    return SimpleNamespace(**base)


def _buyer(bid, name=None, boxes=None, **kw):
    base = dict(id=bid, is_active=True, do_not_contact=False, is_test=False,
                company_name=name if name is not None else "Buyer %s" % bid,
                contact_name=None, buy_boxes=boxes or [])
    base.update(kw)
    return SimpleNamespace(**base)


def _dim(result, name):
    return next(f for f in result["factors"] if f["dimension"] == name)


def test_a_strategy_only_buy_box_does_not_score_a_perfect_fit():
    result = score_buy_box(_deal(), _prop(), _box(strategies=["flip"]))
    assert _dim(result, "strategy")["matched"] is None
    assert result["score"] != 100
    assert result["score"] == 0


def test_a_strategy_adds_no_points_to_a_box_that_really_matches_or_misses():
    plain = score_buy_box(_deal(), _prop(), _box(states=["tx"]))
    with_strategy = score_buy_box(_deal(), _prop(),
                                  _box(states=["tx"], strategies=["flip"]))
    assert plain["score"] == with_strategy["score"] == 100

    miss_plain = score_buy_box(_deal(), _prop(), _box(states=["ok"]))
    miss_strategy = score_buy_box(_deal(), _prop(),
                                  _box(states=["ok"], strategies=["flip"]))
    assert miss_plain["score"] == miss_strategy["score"]


def test_an_empty_buy_box_says_there_is_not_enough_information():
    result = score_buy_box(_deal(), _prop(), _box())
    assert result["score"] == 0
    assert not result["disqualified"]
    notes = [f for f in result["factors"] if f["dimension"] == "buy_box"]
    assert notes and notes[0]["matched"] is None
    assert "not enough information" in notes[0]["detail"]


def test_a_buy_box_that_constrains_something_has_no_insufficient_data_note():
    result = score_buy_box(_deal(), _prop(), _box(states=["tx"]))
    assert not [f for f in result["factors"] if f["dimension"] == "buy_box"]


# ── 5. Deterministic ranking ties ───────────────────────────────────────────

def _tied_buyers():
    box = _box(states=["tx"])
    return [_buyer("b3", "Zeta Capital", [box]),
            _buyer("b2", "Alpha Capital", [box]),
            _buyer("b1", "Alpha Capital", [box]),
            _buyer("b4", "Mid Capital", [box])]


def test_ties_rank_by_name_then_id_whatever_order_the_database_returned():
    expected = ["b1", "b2", "b4", "b3"]
    buyers = _tied_buyers()
    for order in (buyers, list(reversed(buyers)),
                  [buyers[2], buyers[0], buyers[3], buyers[1]]):
        ranked = match_deal_to_buyers(_deal(), _prop(), order)
        assert [r["score"] for r in ranked] == [100, 100, 100, 100]
        assert [r["buyer"].id for r in ranked] == expected


def test_qualified_ranks_above_disqualified_even_when_the_score_is_higher_or_tied():
    good = _buyer("g", "Zed", [_box(states=["tx"])])
    bad = _buyer("a", "Aaa", [_box(states=["ok"])])
    ranked = match_deal_to_buyers(_deal(), _prop(), [bad, good])
    assert [r["buyer"].id for r in ranked][0] == "g"
    assert ranked[-1]["disqualified"]


def test_a_nameless_buyer_does_not_crash_the_tie_break():
    box = _box(states=["tx"])
    ranked = match_deal_to_buyers(
        _deal(), _prop(),
        [_buyer("n2", "", [box], contact_name=None),
         _buyer("n1", None, [box], contact_name="Zoe")])
    assert len(ranked) == 2


# ── 5b. Org isolation in matching ───────────────────────────────────────────

def _make_deal_and_box(client, headers, street):
    prop = ok(client.post("/wholesale/properties", headers=headers,
                          json={"street_address": street, "city": "Dallas",
                                "state": "TX", "zip_code": "75201",
                                "county": "Dallas",
                                "property_type": "single_family",
                                "bedrooms": 3, "bathrooms": 2,
                                "square_feet": 1500}))
    deal_id = prop["deal"]["id"]
    ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=headers,
                    json={"arv": 300000, "repair_estimate": 40000,
                          "proposed_offer": 150000}))
    return deal_id


def _make_buyer(client, headers, name):
    buyer = ok(client.post("/wholesale/buyers", headers=headers,
                           json={"company_name": name,
                                 "email": "%s@example.com" % name.split()[0].lower()}))
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % buyer["id"],
                   headers=headers,
                   json={"label": "TX", "states": ["TX"]}))
    return buyer


def test_matching_only_scores_this_organizations_buyers(
        client, auth_headers, other_headers):
    mine = _make_buyer(client, auth_headers, "Mine Capital")
    theirs = _make_buyer(client, _headers_only(other_headers), "Theirs Capital")
    deal_id = _make_deal_and_box(client, auth_headers, "11 Isolation Ln")

    matches = ok(client.post("/wholesale/deals/%s/match-buyers" % deal_id,
                             headers=auth_headers, json={}))["matches"]
    ids = {m["buyer_id"] for m in matches}
    assert mine["id"] in ids
    assert theirs["id"] not in ids


def test_another_organization_cannot_run_or_read_matching_on_my_deal(
        client, auth_headers, other_headers):
    _make_buyer(client, auth_headers, "Mine Capital")
    deal_id = _make_deal_and_box(client, auth_headers, "12 Isolation Ln")
    ok(client.post("/wholesale/deals/%s/match-buyers" % deal_id,
                   headers=auth_headers, json={}))

    foreign = _headers_only(other_headers)
    assert client.post("/wholesale/deals/%s/match-buyers" % deal_id,
                       headers=foreign, json={}).status_code == 404
    assert client.get("/wholesale/deals/%s/matches" % deal_id,
                      headers=foreign).status_code == 404
