"""Phase 3: the negotiation, the disposition decision, the close and the loss.

These are the steps a wholesaler actually performs between "we like this house"
and "the fee landed". Each test asserts the thing that would cost real money if
it were wrong — an offer recorded above the MAO without anybody being told, a
buyer auto-selected on price alone, a closed deal whose economics can be edited
by a stray keystroke.
"""

import pytest


def ok(response):
    assert response.status_code in (200, 201), \
        "%s %s" % (response.status_code, response.text[:300])
    return response.json()


@pytest.fixture
def deal(client, auth_headers):
    """A property with an owner, an ARV and a repair number — ready to offer."""
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "9 Workflow Rd", "city": "Dallas",
                                "state": "TX", "county": "Dallas",
                                "zip_code": "75201", "property_type": "single_family",
                                "bedrooms": 3, "bathrooms": 2, "square_feet": 1500,
                                "is_test": True}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Wanda", "last_name": "Owner",
                         "phone": "2145557001"}))
    deal_id = prop["deal"]["id"]
    ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=auth_headers,
                    json={"arv": 300000, "repair_estimate": 40000}))
    ok(client.post("/wholesale/deals/%s/analysis/recalculate" % deal_id,
                   headers=auth_headers, json={}))
    return {"property_id": prop["id"], "deal_id": deal_id}


# ── The negotiation ─────────────────────────────────────────────────────────

def test_an_offer_and_a_counter_are_both_kept_in_order(client, auth_headers, deal):
    d = deal["deal_id"]
    ok(client.post("/wholesale/deals/%s/offers" % d, headers=auth_headers,
                   json={"amount": 150000, "direction": "us"}))
    ok(client.post("/wholesale/deals/%s/offers" % d, headers=auth_headers,
                   json={"amount": 185000, "direction": "seller",
                         "notes": "Wants more"}))
    ok(client.post("/wholesale/deals/%s/offers" % d, headers=auth_headers,
                   json={"amount": 162000, "direction": "us"}))

    offers = ok(client.get("/wholesale/deals/%s/offers" % d,
                           headers=auth_headers))["offers"]
    assert [o["direction"] for o in offers] == ["us", "seller", "us"]
    assert [o["amount"] for o in offers] == [150000, 185000, 162000]


def test_a_seller_counter_does_not_become_our_proposed_number(
        client, auth_headers, deal):
    """What they want is their position, not our offer."""
    d = deal["deal_id"]
    ok(client.post("/wholesale/deals/%s/offers" % d, headers=auth_headers,
                   json={"amount": 150000, "direction": "us"}))
    ok(client.post("/wholesale/deals/%s/offers" % d, headers=auth_headers,
                   json={"amount": 185000, "direction": "seller"}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    assert room["analysis"]["proposed_offer"] == 150000


def test_an_offer_above_the_mao_is_recorded_and_said_out_loud(
        client, auth_headers, deal):
    """Not blocked — a wholesaler may knowingly go over. But never silently."""
    d = deal["deal_id"]
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    mao = room["analysis"]["max_allowable_offer"]

    result = ok(client.post("/wholesale/deals/%s/offers" % d, headers=auth_headers,
                            json={"amount": mao + 25000, "direction": "us"}))
    assert result["over_mao"] is True
    assert "above the maximum allowable offer" in result["note"]
    assert result["offer"]["mao_at_time"] == mao


def test_recording_an_offer_does_not_move_the_deal_past_the_approval_gate(
        client, auth_headers, deal):
    """The Phase 1 guardrail is untouched by the Phase 3 history."""
    d = deal["deal_id"]
    ok(client.post("/wholesale/deals/%s/offers" % d, headers=auth_headers,
                   json={"amount": 150000, "direction": "us"}))
    response = client.post("/wholesale/deals/%s/stage" % d, headers=auth_headers,
                           json={"stage": "offer_sent"})
    assert response.status_code == 409
    assert "approv" in response.json()["detail"].lower()


# ── The disposition decision ────────────────────────────────────────────────

@pytest.fixture
def with_buyers(client, auth_headers, deal):
    """Two buyers on the deal, one offering more than the other."""
    d = deal["deal_id"]
    ok(client.patch("/wholesale/deals/%s/contract" % d, headers=auth_headers,
                    json={"contract_price": 150000, "contract_status": "signed"}))
    out = {}
    for key, name, offer in (("high", "Big Number Capital", 175000),
                             ("solid", "Reliable Homes", 168000)):
        buyer = ok(client.post("/wholesale/buyers", headers=auth_headers,
                               json={"company_name": name,
                                     "email": "%s@example.test" % key,
                                     "is_test": True}))
        ok(client.post("/wholesale/buyers/%s/buy-boxes" % buyer["id"],
                       headers=auth_headers,
                       json={"label": "TX", "states": ["TX"],
                             "min_price": 1, "max_price": 999999}))
        out[key] = {"buyer_id": buyer["id"], "offer": offer}

    ok(client.post("/wholesale/deals/%s/match-buyers" % d, headers=auth_headers,
                   json={}))
    composed = ok(client.post("/wholesale/deals/%s/disposition" % d,
                              headers=auth_headers,
                              json={"buyer_ids": [v["buyer_id"] for v in out.values()],
                                    "channel": "email"}))
    board = ok(client.get("/wholesale/deals/%s/buyer-board" % d,
                          headers=auth_headers))
    for row in board["buyers"]:
        for key, v in out.items():
            if row["buyer_id"] == v["buyer_id"]:
                v["outreach_id"] = row["outreach_id"]
    deal["buyers"] = out
    return deal


def test_the_buyer_board_shows_the_spread_against_what_we_are_paying(
        client, auth_headers, with_buyers):
    d = with_buyers["deal_id"]
    high = with_buyers["buyers"]["high"]
    ok(client.post("/wholesale/outreach/%s/response" % high["outreach_id"],
                   headers=auth_headers,
                   json={"status": "offer_submitted", "offer_amount": 175000}))

    board = ok(client.get("/wholesale/deals/%s/buyer-board" % d,
                          headers=auth_headers))
    row = next(r for r in board["buyers"] if r["buyer_id"] == high["buyer_id"])
    assert row["offer_amount"] == 175000
    assert row["spread"] == 25000          # 175,000 offered - 150,000 contract
    assert board["contract_price"] == 150000


def test_recording_an_amount_moves_the_status_by_itself(
        client, auth_headers, with_buyers):
    """A board showing an offer nobody has noticed is how a deal goes stale."""
    high = with_buyers["buyers"]["high"]
    result = ok(client.post("/wholesale/outreach/%s/response" % high["outreach_id"],
                            headers=auth_headers, json={"offer_amount": 175000}))
    assert result["status"] == "offer_submitted"


def test_nothing_selects_a_buyer_on_its_own(client, auth_headers, with_buyers):
    """Two offers on the table and the deal still has no buyer until a person
    says so — the highest number does not win by default."""
    d = with_buyers["deal_id"]
    for key in ("high", "solid"):
        b = with_buyers["buyers"][key]
        ok(client.post("/wholesale/outreach/%s/response" % b["outreach_id"],
                       headers=auth_headers,
                       json={"status": "offer_submitted", "offer_amount": b["offer"]}))

    board = ok(client.get("/wholesale/deals/%s/buyer-board" % d,
                          headers=auth_headers))
    assert board["selected_buyer_id"] is None
    assert not any(r["is_selected"] for r in board["buyers"])


def test_a_person_can_pick_the_lower_offer_and_the_economics_follow(
        client, auth_headers, with_buyers):
    """The whole reason selection is manual: the lower bid is often the right
    one. Picking it must set the economics to THAT buyer's number."""
    d = with_buyers["deal_id"]
    solid = with_buyers["buyers"]["solid"]
    for key in ("high", "solid"):
        b = with_buyers["buyers"][key]
        ok(client.post("/wholesale/outreach/%s/response" % b["outreach_id"],
                       headers=auth_headers,
                       json={"status": "offer_submitted", "offer_amount": b["offer"]}))

    result = ok(client.post("/wholesale/deals/%s/select-buyer" % d,
                            headers=auth_headers,
                            json={"outreach_id": solid["outreach_id"],
                                  "note": "Closes in 10 days, POF verified"}))
    assert result["selected_buyer_id"] == solid["buyer_id"]
    assert result["buyer_price"] == 168000
    assert result["assignment_fee"] == 18000      # 168,000 - 150,000


def test_selecting_a_buyer_who_opted_out_is_refused(client, auth_headers,
                                                    with_buyers):
    d = with_buyers["deal_id"]
    solid = with_buyers["buyers"]["solid"]
    ok(client.patch("/wholesale/buyers/%s" % solid["buyer_id"],
                    headers=auth_headers, json={"do_not_contact": True}))
    response = client.post("/wholesale/deals/%s/select-buyer" % d,
                           headers=auth_headers,
                           json={"outreach_id": solid["outreach_id"]})
    assert response.status_code == 409
    assert "opted out" in response.json()["detail"]


def test_selecting_a_buyer_records_who_chose_and_when(client, auth_headers,
                                                      with_buyers):
    d = with_buyers["deal_id"]
    solid = with_buyers["buyers"]["solid"]
    ok(client.post("/wholesale/outreach/%s/response" % solid["outreach_id"],
                   headers=auth_headers,
                   json={"status": "offer_submitted", "offer_amount": 168000}))
    ok(client.post("/wholesale/deals/%s/select-buyer" % d, headers=auth_headers,
                   json={"outreach_id": solid["outreach_id"]}))

    events = ok(client.get("/wholesale/events", headers=auth_headers,
                           params={"deal_id": d}))["events"]
    chosen = [e for e in events if e["action"] == "buyer.selected"]
    assert chosen and chosen[0]["actor_type"] == "user"


# ── Proof of funds ──────────────────────────────────────────────────────────

def test_proof_of_funds_moves_through_its_states_and_is_never_auto_verified(
        client, auth_headers, with_buyers):
    """No provider in this module verifies a bank letter, so `verified` is
    only ever a person's decision."""
    high = with_buyers["buyers"]["high"]
    board_row = high["outreach_id"]

    for state in ("requested", "received", "verified"):
        result = ok(client.post("/wholesale/outreach/%s/pof-status" % board_row,
                                headers=auth_headers, json={"status": state}))
        assert result["pof_status"] == state

    bad = client.post("/wholesale/outreach/%s/pof-status" % board_row,
                      headers=auth_headers, json={"status": "definitely_fine"})
    assert bad.status_code == 400


# ── Closing, and the lock that comes with it ────────────────────────────────

def test_closing_locks_the_economics(client, auth_headers, deal):
    d = deal["deal_id"]
    ok(client.post("/wholesale/deals/%s/close" % d, headers=auth_headers,
                   json={"wholesale_fee_collected": 12000}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    assert room["deal"]["economics_locked"] is True
    assert room["deal"]["stage"] == "closed"


def test_a_correction_needs_a_reason_and_leaves_a_before_and_after(
        client, auth_headers, deal):
    """The only way to change the money on a closed deal, and it is defensible
    afterwards because the event carries both sides of the change."""
    d = deal["deal_id"]
    ok(client.patch("/wholesale/deals/%s/contract" % d, headers=auth_headers,
                    json={"contract_price": 150000}))
    ok(client.post("/wholesale/deals/%s/close" % d, headers=auth_headers,
                   json={"wholesale_fee_collected": 12000}))

    thin = client.post("/wholesale/deals/%s/economics-correction" % d,
                       headers=auth_headers,
                       json={"reason": "typo", "wholesale_fee_collected": 14000})
    assert thin.status_code == 400

    ok(client.post("/wholesale/deals/%s/economics-correction" % d,
                   headers=auth_headers,
                   json={"reason": "Title sent a corrected settlement statement",
                         "wholesale_fee_collected": 14000}))

    events = ok(client.get("/wholesale/events", headers=auth_headers,
                           params={"deal_id": d}))["events"]
    correction = [e for e in events if e["action"] == "economics.corrected"]
    assert correction, "a correction must be auditable"


# ── Losing a deal ───────────────────────────────────────────────────────────

def test_a_lost_deal_needs_a_reason_from_the_list(client, auth_headers, deal):
    d = deal["deal_id"]
    bad = client.post("/wholesale/deals/%s/lost" % d, headers=auth_headers,
                      json={"reason": "just because"})
    assert bad.status_code == 400
    assert "Pick a reason" in bad.json()["detail"]

    ok(client.post("/wholesale/deals/%s/lost" % d, headers=auth_headers,
                   json={"reason": "price_too_high",
                         "detail": "Owner wanted 40k over the MAO"}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    assert room["deal"]["stage"] == "dead"
    assert room["deal"]["lost_reason"] == "price_too_high"


def test_losing_a_deal_stops_the_cadence(client, auth_headers):
    """The Phase 2 stop conditions still apply — this is another way in, not a
    way around.

    A LIVE deal, not a sandbox one: sandbox deals are refused enrolment
    outright, which is the Phase 2 guarantee and not what is under test here.
    """
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "11 Cadence Ct", "state": "TX"}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Cad", "last_name": "Owner",
                         "phone": "2145557333"}))
    d = prop["deal"]["id"]
    ok(client.post("/wholesale/deals/%s/cadence" % d, headers=auth_headers,
                   json={"action": "start"}))
    ok(client.post("/wholesale/deals/%s/lost" % d, headers=auth_headers,
                   json={"reason": "seller_changed_mind"}))
    status = ok(client.get("/wholesale/deals/%s/cadence" % d, headers=auth_headers))
    assert status["state"] == "stopped_deal_dead"


# ── The next action, which two screens both read ────────────────────────────

def test_the_next_action_is_a_reading_of_real_state(client, auth_headers, deal):
    d = deal["deal_id"]
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    action = room["next_action"]
    assert action["label"] and action["detail"]
    assert action["tone"] in ("ok", "attention", "urgent")
    assert action["tab"] in ("overview", "seller", "analysis", "offer",
                            "documents", "buyers", "closing", "audit")


def test_a_pending_approval_outranks_everything_else(client, auth_headers, deal):
    d = deal["deal_id"]
    ok(client.post("/wholesale/deals/%s/approvals" % d, headers=auth_headers,
                   json={"kind": "offer", "amount": 150000}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    assert "Approve" in room["next_action"]["label"]
    assert room["next_action"]["tone"] == "urgent"


def test_a_closed_deal_is_not_waiting_for_anything(client, auth_headers, deal):
    d = deal["deal_id"]
    ok(client.post("/wholesale/deals/%s/close" % d, headers=auth_headers,
                   json={"wholesale_fee_collected": 9000}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    assert room["next_action"]["label"] == "Closed"
    assert room["next_action"]["tone"] == "ok"


# ── Comps: excluding is not deleting ───────────────────────────────

def _comp(client, headers, deal_id, **kw):
    body = {"street_address": "1 Comp St", "sale_price": 300000,
            "square_feet": 1500}
    body.update(kw)
    return ok(client.post("/wholesale/deals/%s/comps" % deal_id,
                          headers=headers, json=body))["comp"]


def test_excluding_a_comp_keeps_the_record_and_changes_the_arithmetic(
        client, auth_headers, deal):
    """The distinction the whole comps workspace exists to make."""
    d = deal["deal_id"]
    a = _comp(client, auth_headers, d, street_address="A", sale_price=300000)
    _comp(client, auth_headers, d, street_address="B", sale_price=600000)

    before = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    assert before["comp_statistics"]["included_count"] == 2

    ok(client.patch("/wholesale/comps/%s" % a["id"], headers=auth_headers,
                    json={"included": False}))

    after = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    # Still two rows. One of them no longer counts.
    assert len(after["comps"]) == 2
    assert after["comp_statistics"]["included_count"] == 1
    assert after["comp_statistics"]["excluded_count"] == 1
    assert after["comp_statistics"]["median_sale_price"] == 600000


def test_deleting_a_comp_removes_the_row(client, auth_headers, deal):
    d = deal["deal_id"]
    a = _comp(client, auth_headers, d, street_address="A")
    ok(client.delete("/wholesale/comps/%s" % a["id"], headers=auth_headers))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    assert room["comps"] == []


def test_excluding_and_editing_are_different_lines_in_the_audit_history(
        client, auth_headers, deal):
    """'Comp removed' told a reader nothing about which of the two happened."""
    d = deal["deal_id"]
    a = _comp(client, auth_headers, d, street_address="A")

    ok(client.patch("/wholesale/comps/%s" % a["id"], headers=auth_headers,
                    json={"included": False}))
    ok(client.patch("/wholesale/comps/%s" % a["id"], headers=auth_headers,
                    json={"sale_price": 310000}))
    ok(client.patch("/wholesale/comps/%s" % a["id"], headers=auth_headers,
                    json={"included": True}))

    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    kinds = [e["action"] for e in room["events"]]
    assert "comp.excluded" in kinds
    assert "comp.included" in kinds
    assert "comp.updated" in kinds


def test_a_comp_edit_that_changes_nothing_else_is_not_logged_as_an_edit(
        client, auth_headers, deal):
    """Toggling the ARV checkbox is not 'somebody edited the comp'."""
    d = deal["deal_id"]
    a = _comp(client, auth_headers, d, street_address="A")
    ok(client.patch("/wholesale/comps/%s" % a["id"], headers=auth_headers,
                    json={"included": False}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    kinds = [e["action"] for e in room["events"]]
    assert "comp.excluded" in kinds
    assert "comp.updated" not in kinds


def test_a_comp_carries_its_year_built_and_its_price_per_square_foot(
        client, auth_headers, deal):
    d = deal["deal_id"]
    _comp(client, auth_headers, d, sale_price=300000, square_feet=1500,
          year_built=1974)
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    c = room["comps"][0]
    assert c["year_built"] == 1974
    assert c["price_per_sqft"] == 200
    assert c["photo"] is None


def test_a_comp_with_no_square_footage_has_no_price_per_square_foot(
        client, auth_headers, deal):
    """Not zero, and not the sale price. A dash."""
    d = deal["deal_id"]
    _comp(client, auth_headers, d, sale_price=300000, square_feet=None)
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    assert room["comps"][0]["price_per_sqft"] is None


def test_clearing_a_comp_field_actually_clears_it(client, auth_headers, deal):
    """The screen sends null for an emptied box; skipping it would keep the
    old value and the user would think the edit did not save."""
    d = deal["deal_id"]
    a = _comp(client, auth_headers, d, sale_price=300000, year_built=1974)
    ok(client.patch("/wholesale/comps/%s" % a["id"], headers=auth_headers,
                    json={"year_built": None}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    assert room["comps"][0]["year_built"] is None


def test_the_deal_room_reports_the_comp_set_as_numbers(client, auth_headers, deal):
    """What the comparison panel renders, asserted where it is computed."""
    d = deal["deal_id"]
    _comp(client, auth_headers, d, street_address="A", sale_price=300000,
          square_feet=1500)
    _comp(client, auth_headers, d, street_address="B", sale_price=330000,
          square_feet=1500)
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))
    stats = room["comp_statistics"]
    assert stats["median_price_per_sqft"] == 210
    assert stats["average_price_per_sqft"] == 210
    assert stats["subject_square_feet"] == 1500
    # The deal's own ARV was set to 300,000 by the fixture.
    assert stats["subject_price_per_sqft"] == 200


# ── Contract, title and closing ───────────────────────────────────

def test_every_contract_date_the_screen_offers_actually_saves(
        client, auth_headers, deal):
    """These columns existed on the model with no endpoint that would write
    them, which is a form field that silently never saves."""
    d = deal["deal_id"]
    ok(client.patch("/wholesale/deals/%s/contract" % d, headers=auth_headers,
                    json={"contract_date": "2026-09-21",
                          "seller_signed_at": "2026-09-21",
                          "buyer_signed_at": "2026-09-20",
                          "effective_date": "2026-09-22",
                          "earnest_money": 2500,
                          "earnest_money_due": "2026-09-24",
                          "earnest_money_received_at": "2026-09-23",
                          "option_fee": 200,
                          "closing_deadline": "2026-11-14"}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))["deal"]
    assert room["contract_date"] == "2026-09-21"
    assert room["seller_signed_at"] == "2026-09-21"
    assert room["buyer_signed_at"] == "2026-09-20"
    assert room["effective_date"] == "2026-09-22"
    assert room["earnest_money"] == 2500
    assert room["earnest_money_due"] == "2026-09-24"
    assert room["earnest_money_received_at"] == "2026-09-23"
    assert room["option_fee"] == 200
    assert room["closing_deadline"] == "2026-11-14"


def test_every_title_and_closing_field_the_screen_offers_actually_saves(
        client, auth_headers, deal):
    d = deal["deal_id"]
    ok(client.patch("/wholesale/deals/%s/title" % d, headers=auth_headers,
                    json={"title_company": "Lone Star Title",
                          "title_escrow_officer": "Ramona Vega",
                          "title_phone": "2145550188",
                          "title_email": "ramona@example.com",
                          "title_status": "title_search",
                          "title_commitment_received_at": "2026-10-02",
                          "title_issues": "Second lien from 2011.",
                          "closing_date": "2026-11-09",
                          "closing_time": "10:30",
                          "closing_location": "Oak Lawn"}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))["deal"]
    assert room["title_escrow_officer"] == "Ramona Vega"
    assert room["title_phone"] == "2145550188"
    assert room["title_email"] == "ramona@example.com"
    assert room["title_status"] == "title_search"
    assert room["title_commitment_received_at"] == "2026-10-02"
    assert room["title_issues"] == "Second lien from 2011."
    assert room["closing_time"] == "10:30"
    assert room["closing_location"] == "Oak Lawn"


def test_an_unknown_title_status_is_refused_rather_than_stored(
        client, auth_headers, deal):
    """A stored key nothing recognises prints raw on every screen that reads it."""
    d = deal["deal_id"]
    bad = client.patch("/wholesale/deals/%s/title" % d, headers=auth_headers,
                       json={"title_status": "probably_fine"})
    assert bad.status_code == 400
    assert "must be one of" in bad.text


def test_the_phase_one_title_spellings_still_save(client, auth_headers, deal):
    """Rows written before Phase 3 say `clear`. Refusing them would mean a deal
    whose only crime is being older than the list cannot be saved."""
    d = deal["deal_id"]
    ok(client.patch("/wholesale/deals/%s/title" % d, headers=auth_headers,
                    json={"title_status": "clear"}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))["deal"]
    assert room["title_status"] == "clear"


def test_a_closed_deals_contract_price_cannot_be_edited_in_passing(
        client, auth_headers, deal):
    """The lock is the point: a fee nobody can stand behind is one a stray
    keystroke could have changed."""
    d = deal["deal_id"]
    ok(client.patch("/wholesale/deals/%s/contract" % d, headers=auth_headers,
                    json={"contract_price": 144000}))
    ok(client.post("/wholesale/deals/%s/close" % d, headers=auth_headers,
                   json={"wholesale_fee_collected": 11200}))

    refused = client.patch("/wholesale/deals/%s/contract" % d, headers=auth_headers,
                           json={"contract_price": 999000})
    assert refused.status_code == 409
    assert "economics correction" in refused.text

    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))["deal"]
    assert room["contract_price"] == 144000


def test_a_closed_deal_can_still_have_its_dates_tidied(client, auth_headers, deal):
    """The lock is on the MONEY. Correcting a typo in a closing location is not
    a revenue restatement and should not need a written justification."""
    d = deal["deal_id"]
    ok(client.post("/wholesale/deals/%s/close" % d, headers=auth_headers,
                   json={"wholesale_fee_collected": 11200}))
    ok(client.patch("/wholesale/deals/%s/title" % d, headers=auth_headers,
                    json={"closing_location": "Oak Lawn, suite 200"}))
    room = ok(client.get("/wholesale/deals/%s" % d, headers=auth_headers))["deal"]
    assert room["closing_location"] == "Oak Lawn, suite 200"


# ── The buyer CRM ─────────────────────────────────────────────

def _buyer(client, headers, **kw):
    body = {"company_name": "Test Buyer Co", "email": "buyer@example.com",
            "is_test": True}
    body.update(kw)
    return ok(client.post("/wholesale/buyers", headers=headers, json=body))


def test_a_buyer_nobody_has_contacted_can_be_deleted(client, auth_headers):
    b = _buyer(client, auth_headers, company_name="Typo Holdings")
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % b["id"], headers=auth_headers,
                   json={"label": "DFW", "states": ["TX"]}))
    result = ok(client.delete("/wholesale/buyers/%s" % b["id"], headers=auth_headers))
    assert result["buy_boxes_deleted"] == 1

    listing = ok(client.get("/wholesale/buyers", headers=auth_headers,
                            params={"include_test": True, "active_only": False}))
    assert b["id"] not in [x["id"] for x in listing["buyers"]]


def test_a_buyer_with_history_is_refused_and_told_what_to_do_instead(
        client, auth_headers, deal):
    """The name has to survive: somebody will ask who we sold it to."""
    d = deal["deal_id"]
    b = _buyer(client, auth_headers, company_name="Real Buyer LLC")
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % b["id"], headers=auth_headers,
                   json={"label": "DFW", "states": ["TX"]}))
    ok(client.post("/wholesale/deals/%s/match-buyers" % d, headers=auth_headers))

    refused = client.delete("/wholesale/buyers/%s" % b["id"], headers=auth_headers)
    assert refused.status_code == 409
    assert "inactive" in refused.text

    # Still there, and the suggested route out actually works.
    ok(client.patch("/wholesale/buyers/%s" % b["id"], headers=auth_headers,
                    json={"is_active": False}))
    listing = ok(client.get("/wholesale/buyers", headers=auth_headers,
                            params={"include_test": True, "active_only": False}))
    assert b["id"] in [x["id"] for x in listing["buyers"]]


def test_the_buyer_track_record_counts_rows_not_opinions(
        client, auth_headers, deal):
    d = deal["deal_id"]
    b = _buyer(client, auth_headers, company_name="Tracked Buyer",
               past_deals_count=99)
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % b["id"], headers=auth_headers,
                   json={"label": "DFW", "states": ["TX"]}))
    ok(client.post("/wholesale/deals/%s/disposition" % d, headers=auth_headers,
                   json={"buyer_ids": [b["id"]], "asking_price": 160000}))

    listing = ok(client.get("/wholesale/buyers", headers=auth_headers,
                            params={"include_test": True, "active_only": False,
                                    "with_activity": True}))
    row = [x for x in listing["buyers"] if x["id"] == b["id"]][0]
    # The stated figure and the measured one are both present and separate.
    assert row["past_deals_count"] == 99
    assert row["activity"]["offers_made"] == 0
    assert row["activity"]["selected_count"] == 0


def test_the_track_record_is_absent_unless_it_was_asked_for(
        client, auth_headers):
    """It costs a query. A caller that did not ask does not pay for it."""
    _buyer(client, auth_headers, company_name="Unasked Buyer")
    listing = ok(client.get("/wholesale/buyers", headers=auth_headers,
                            params={"include_test": True, "active_only": False}))
    assert all(x["activity"] is None for x in listing["buyers"])
