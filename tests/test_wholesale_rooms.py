# -*- coding: utf-8 -*-
"""THE PUBLICATION BOUNDARY, ATTACKED.

This file exists because "the browser does not render it" is not security. Every
assertion below is made against the API response, not against a screen, and the
strongest ones are made against VALUES rather than key names: a payload that
renamed `assignment_fee` to `spread` would still be a breach, and a key-name
check would sail straight past it.

The three audiences and the two things each must never learn:

    investor   what the seller said, and what we make on the deal
    seller     who the buyers are, and what we make on the deal
    stranger   anything at all

A token is the whole authorisation, so the tests treat it as one: revoked,
expired, wrong audience, and unpublished all have to fail, and fail the SAME
way, because a distinguishable refusal is an oracle.
"""
import json
from datetime import datetime, timedelta

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128

# The values planted in the fixture. If any of these strings turns up in an
# external payload, something leaked — whatever the key was called.
SELLER_SECRET = "SellerSaidThisPrivately"
SELLER_NAME = "Marisol"
INTERNAL_NOTE = "InternalAnalysisNote"
MAO_VALUE = 144000
ASSIGNMENT_FEE = 12000
BUYER_PRICE = 156000
CONTRACT_PRICE = 140000
ASKING_PRICE = 158000


def ok(response):
    assert response.status_code in (200, 201), \
        "%s %s" % (response.status_code, response.text[:400])
    return response.json()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WHOLESALE_LOCAL_MEDIA_ROOT", str(tmp_path / "media"))
    return tmp_path


@pytest.fixture
def world(client, auth_headers, storage):
    """One deal with everything on it — including the things nobody outside
    the workspace may ever see."""
    prop = ok(client.post("/wholesale/properties", headers=auth_headers, json={
        "street_address": "1418 Cedar Springs Rd", "city": "Dallas",
        "state": "TX", "zip_code": "75201", "bedrooms": 3, "bathrooms": 2,
        "square_feet": 1520, "year_built": 1968, "property_type": "single_family",
        "is_test": True,
    }))
    seller = ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                            headers=auth_headers,
                            json={"first_name": SELLER_NAME,
                                  "last_name": "Alvarez",
                                  "phone": "2145550137",
                                  "motivation": SELLER_SECRET}))
    deal_id = prop["deal"]["id"]

    # The internal numbers. Every one of these is a thing an investor or a
    # seller must not be able to read.
    ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=auth_headers,
                    json={"arv": 300000, "repair_estimate": 45000,
                          "proposed_offer": CONTRACT_PRICE,
                          "analysis_notes": INTERNAL_NOTE}))
    ok(client.patch("/wholesale/deals/%s/contract" % deal_id, headers=auth_headers,
                    json={"contract_price": CONTRACT_PRICE}))
    ok(client.patch("/wholesale/deals/%s/title" % deal_id, headers=auth_headers,
                    json={"title_company": "Lone Star Title",
                          "title_file_number": "LS-4471"}))

    buyer = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Lone Oak Capital",
                                 "contact_name": "Ray Whitfield",
                                 "email": "ray@loneoak.example.com"}))
    ok(client.post("/wholesale/deals/%s/assign" % deal_id, headers=auth_headers,
                   json={"buyer_id": buyer["id"], "buyer_price": BUYER_PRICE,
                         "assignment_fee": ASSIGNMENT_FEE}))

    # Two photos: one published to investors, one deliberately not.
    shown = ok(client.post("/wholesale/properties/%s/photos" % prop["id"],
                           headers=auth_headers,
                           files={"file": ("front.png", PNG, "image/png")},
                           data={"caption": "Front"}))
    hidden = ok(client.post("/wholesale/properties/%s/photos" % prop["id"],
                            headers=auth_headers,
                            files={"file": ("mess.png", PNG, "image/png")},
                            data={"caption": "Seller's belongings"}))
    ok(client.patch("/wholesale/files/%s" % shown["id"], headers=auth_headers,
                    json={"buyer_visible": True, "category": "exterior_front"}))

    # Two documents: one for investors, one that is nobody's business.
    public_doc = ok(client.post("/wholesale/deals/%s/documents" % deal_id,
                                headers=auth_headers,
                                json={"doc_type": "repair_estimate",
                                      "title": "Repair scope",
                                      "buyer_visible": True}))
    private_doc = ok(client.post("/wholesale/deals/%s/documents" % deal_id,
                                 headers=auth_headers,
                                 json={"doc_type": "purchase_contract",
                                       "title": "Signed purchase contract"}))

    return {"property_id": prop["id"], "deal_id": deal_id,
            "seller": seller, "buyer_id": buyer["id"],
            "shown_photo": shown["id"], "hidden_photo": hidden["id"],
            "public_doc": public_doc["id"], "private_doc": private_doc["id"]}


def publish(client, headers, deal_id, audience, **fields):
    if fields:
        ok(client.patch("/wholesale/deals/%s/publication" % deal_id,
                        headers=headers, json=fields))
    return ok(client.post("/wholesale/deals/%s/publication/state" % deal_id,
                          headers=headers,
                          json={"audience": audience, "published": True}))


def mint(client, headers, deal_id, audience, **kw):
    body = {"audience": audience}
    body.update(kw)
    return ok(client.post("/wholesale/deals/%s/share-links" % deal_id,
                          headers=headers, json=body))


# ══════════════════════════════════════════════════════════════════════════
#  A link is the whole authorisation, so every way it can be dead must be
# ══════════════════════════════════════════════════════════════════════════

def test_an_unpublished_room_is_not_reachable_even_with_a_real_link(
        client, auth_headers, world):
    """Minting a link is not publishing. Two decisions, both required."""
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    assert client.get("/wholesale-rooms/buyer/%s" % link["token"]).status_code == 404


def test_a_made_up_token_and_a_revoked_one_fail_identically(
        client, auth_headers, world):
    """A distinguishable refusal tells a stranger they guessed a real token."""
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE)
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    assert client.get("/wholesale-rooms/buyer/%s" % link["token"]).status_code == 200

    ok(client.post("/wholesale/share-links/%s/revoke" % link["id"],
                   headers=auth_headers))
    revoked = client.get("/wholesale-rooms/buyer/%s" % link["token"])
    invented = client.get("/wholesale-rooms/buyer/definitely-not-a-real-token")
    assert revoked.status_code == invented.status_code == 404
    assert revoked.json()["detail"] == invented.json()["detail"]


def test_unpublishing_kills_a_live_link_on_the_next_request(
        client, auth_headers, world):
    publish(client, auth_headers, world["deal_id"], "buyer")
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    assert client.get("/wholesale-rooms/buyer/%s" % link["token"]).status_code == 200

    ok(client.post("/wholesale/deals/%s/publication/state" % world["deal_id"],
                   headers=auth_headers,
                   json={"audience": "buyer", "published": False}))
    assert client.get("/wholesale-rooms/buyer/%s" % link["token"]).status_code == 404


def test_an_expired_link_stops_working(client, auth_headers, world, db_session):
    from app.models.wholesale_share_models import WholesaleShareLink
    publish(client, auth_headers, world["deal_id"], "buyer")
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"], expires_in_days=1)
    row = (db_session.query(WholesaleShareLink)
           .filter(WholesaleShareLink.id == link["id"]).first())
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()
    assert client.get("/wholesale-rooms/buyer/%s" % link["token"]).status_code == 404


def test_a_buyer_token_cannot_open_the_seller_page_or_the_reverse(
        client, auth_headers, world):
    """The audience is not a permission LEVEL. It is a different page, and a
    token minted for one can never render the other."""
    publish(client, auth_headers, world["deal_id"], "buyer")
    publish(client, auth_headers, world["deal_id"], "seller")
    buyer_link = mint(client, auth_headers, world["deal_id"], "buyer",
                      buyer_id=world["buyer_id"])
    seller_link = mint(client, auth_headers, world["deal_id"], "seller")

    assert client.get("/wholesale-rooms/seller/%s"
                      % buyer_link["token"]).status_code == 404
    assert client.get("/wholesale-rooms/buyer/%s"
                      % seller_link["token"]).status_code == 404


# ══════════════════════════════════════════════════════════════════════════
#  What an investor may see, and what they must never see
# ══════════════════════════════════════════════════════════════════════════

def test_the_investor_room_shows_only_what_was_published(
        client, auth_headers, world):
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE,
            buyer_room_summary="Three bed, needs a roof.",
            buyer_room_condition="Roof and HVAC.")
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"], recipient_name="Lone Oak Capital")
    room = ok(client.get("/wholesale-rooms/buyer/%s" % link["token"]))

    assert room["asking_price"] == ASKING_PRICE
    assert room["property"]["city"] == "Dallas"
    assert room["summary"] == "Three bed, needs a roof."
    # ARV and repairs were never switched on, so the keys are ABSENT rather
    # than null — a null tells a reader the number exists and is withheld.
    assert "arv" not in room
    assert "estimated_repairs" not in room
    assert "comparable_sales" not in room

    # Only the photo somebody ticked, and only the document somebody ticked.
    assert [p["id"] for p in room["photos"]] == [world["shown_photo"]]
    assert [d["id"] for d in room["documents"]] == [world["public_doc"]]


def test_switching_arv_on_publishes_it_and_nothing_else(
        client, auth_headers, world):
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_show_arv=True)
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    room = ok(client.get("/wholesale-rooms/buyer/%s" % link["token"]))
    assert room["arv"] == 300000
    assert "estimated_repairs" not in room


def test_no_internal_value_reaches_an_investor(client, auth_headers, world):
    """THE IMPORTANT ONE. Asserted on VALUES, not on key names.

    A serializer that renamed `assignment_fee` to `spread` would pass a
    key-name check and still hand an investor our margin. This searches the
    whole rendered payload for the actual numbers and sentences.
    """
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE, buyer_room_show_arv=True,
            buyer_room_show_repairs=True, buyer_room_show_comps=True)
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    raw = client.get("/wholesale-rooms/buyer/%s" % link["token"]).text

    for secret in (SELLER_SECRET, SELLER_NAME, INTERNAL_NOTE, "2145550137"):
        assert secret not in raw, "leaked to investor: %s" % secret
    for number in (MAO_VALUE, ASSIGNMENT_FEE, BUYER_PRICE, CONTRACT_PRICE):
        assert str(number) not in raw, "leaked to investor: %s" % number

    from app.services.wholesale_publication import (FORBIDDEN_TO_BUYER,
                                                    leaked_keys)
    assert leaked_keys(json.loads(raw), FORBIDDEN_TO_BUYER) == []


def test_an_investor_cannot_fetch_a_photo_nobody_published(
        client, auth_headers, world):
    publish(client, auth_headers, world["deal_id"], "buyer")
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    shown = client.get("/wholesale-rooms/buyer/%s/photo/%s"
                       % (link["token"], world["shown_photo"]))
    hidden = client.get("/wholesale-rooms/buyer/%s/photo/%s"
                        % (link["token"], world["hidden_photo"]))
    assert shown.status_code == 200
    assert hidden.status_code == 404, "an unpublished photo was served"


def test_an_investor_cannot_fetch_a_document_nobody_published(
        client, auth_headers, world):
    publish(client, auth_headers, world["deal_id"], "buyer")
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    assert client.get("/wholesale-rooms/buyer/%s/document/%s"
                      % (link["token"], world["private_doc"])).status_code == 404


# ══════════════════════════════════════════════════════════════════════════
#  What an investor DOES, and where it lands
# ══════════════════════════════════════════════════════════════════════════

def _sent_deal_sheet(client, headers, world):
    """Put a real outreach row on the board, the way the operator would."""
    return client.post("/wholesale/deals/%s/disposition" % world["deal_id"],
                       headers=headers,
                       json={"buyer_ids": [world["buyer_id"]],
                             "channel": "email", "asking_price": ASKING_PRICE})


def test_an_action_before_anything_was_sent_is_refused_not_invented(
        client, auth_headers, world):
    """Inventing an outreach row would put a 'sent' record on the board for
    something nobody sent."""
    publish(client, auth_headers, world["deal_id"], "buyer")
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    response = client.post("/wholesale-rooms/buyer/%s/action" % link["token"],
                           json={"action": "interested"})
    assert response.status_code == 409


def test_investor_actions_write_the_records_the_operator_reads(
        client, auth_headers, world):
    _sent_deal_sheet(client, auth_headers, world)
    publish(client, auth_headers, world["deal_id"], "buyer")
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"], recipient_name="Lone Oak Capital")

    result = ok(client.post("/wholesale-rooms/buyer/%s/action" % link["token"],
                            json={"action": "offer", "amount": 171000,
                                  "message": "Can close in ten days."}))
    assert result["status"] == "offer_submitted"
    assert result["your_offer"] == 171000

    # The operator's own screen, not a second copy of the answer.
    board = ok(client.get("/wholesale/deals/%s/buyer-board" % world["deal_id"],
                          headers=auth_headers))
    rows = [r for r in board["buyers"] if r.get("offer_amount")]
    assert rows and float(rows[0]["offer_amount"]) == 171000

    # And it is on the deal's activity, named as the investor rather than as us.
    activity = ok(client.get("/wholesale/deals/%s/share-activity" % world["deal_id"],
                             headers=auth_headers))
    assert any(a["action"] == "offer" for a in activity["activity"])


@pytest.mark.parametrize("action,expected", [
    ("interested", "interested"),
    ("pass", "passed"),
    ("walkthrough", "requested_info"),
    ("question", "requested_info"),
])
def test_every_investor_action_maps_onto_a_real_status(
        client, auth_headers, world, action, expected):
    _sent_deal_sheet(client, auth_headers, world)
    publish(client, auth_headers, world["deal_id"], "buyer")
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    result = ok(client.post("/wholesale-rooms/buyer/%s/action" % link["token"],
                            json={"action": action, "message": "hello"}))
    assert result["status"] == expected


def test_an_offer_with_no_amount_is_refused(client, auth_headers, world):
    _sent_deal_sheet(client, auth_headers, world)
    publish(client, auth_headers, world["deal_id"], "buyer")
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    response = client.post("/wholesale-rooms/buyer/%s/action" % link["token"],
                           json={"action": "offer"})
    assert response.status_code == 400


def test_an_invented_action_is_refused(client, auth_headers, world):
    publish(client, auth_headers, world["deal_id"], "buyer")
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    response = client.post("/wholesale-rooms/buyer/%s/action" % link["token"],
                           json={"action": "delete_everything"})
    assert response.status_code == 400


# ══════════════════════════════════════════════════════════════════════════
#  The seller's own page
# ══════════════════════════════════════════════════════════════════════════

def test_the_seller_page_shows_their_transaction_and_no_disposition(
        client, auth_headers, world):
    publish(client, auth_headers, world["deal_id"], "seller",
            seller_room_message="We are on track for the 14th.",
            seller_room_contact_name="Your acquisitions manager",
            seller_room_contact_phone="2145550100")
    link = mint(client, auth_headers, world["deal_id"], "seller",
                recipient_name="Marisol Alvarez")
    page = ok(client.get("/wholesale-rooms/seller/%s" % link["token"]))

    assert page["property"]["line1"] == "1418 Cedar Springs Rd"
    assert page["message"] == "We are on track for the 14th."
    assert page["contact"]["phone"] == "2145550100"
    assert [s["key"] for s in page["progress"]] == \
        ["offer", "agreement", "review", "title", "closed"]
    assert sum(1 for s in page["progress"] if s["state"] == "current") == 1


def test_no_disposition_value_reaches_a_seller(client, auth_headers, world):
    """A seller learning the assignment fee is the failure this boundary is
    for. Asserted on values, for the same reason as the investor test."""
    publish(client, auth_headers, world["deal_id"], "seller")
    link = mint(client, auth_headers, world["deal_id"], "seller")
    raw = client.get("/wholesale-rooms/seller/%s" % link["token"]).text

    for number in (ASSIGNMENT_FEE, BUYER_PRICE, MAO_VALUE, 300000, 45000):
        assert str(number) not in raw, "leaked to seller: %s" % number
    for secret in ("Lone Oak Capital", "ray@loneoak.example.com", INTERNAL_NOTE):
        assert secret not in raw, "leaked to seller: %s" % secret

    from app.services.wholesale_publication import (FORBIDDEN_TO_SELLER,
                                                    leaked_keys)
    assert leaked_keys(json.loads(raw), FORBIDDEN_TO_SELLER) == []


def test_a_seller_cannot_fetch_a_document_that_is_not_theirs(
        client, auth_headers, world):
    ok(client.patch("/wholesale/documents/%s" % world["public_doc"],
                    headers=auth_headers,
                    json={"doc_type": "repair_estimate", "buyer_visible": True}))
    publish(client, auth_headers, world["deal_id"], "seller")
    link = mint(client, auth_headers, world["deal_id"], "seller")
    # Published to investors, never to the seller.
    assert client.get("/wholesale-rooms/seller/%s/document/%s"
                      % (link["token"], world["public_doc"])).status_code == 404


def test_the_seller_page_has_no_action_endpoints_at_all(client):
    """A seller cannot write anything from their page, by construction."""
    from app.main import app
    seller_writes = [r.path for r in app.routes
                     if r.path.startswith("/wholesale-rooms/seller")
                     and "POST" in getattr(r, "methods", set())]
    assert seller_writes == []


# ══════════════════════════════════════════════════════════════════════════
#  PHASE 6 — the new surfaces, attacked the same way
# ══════════════════════════════════════════════════════════════════════════

def test_one_investor_cannot_read_another_investors_response(
        client, auth_headers, world):
    """The single worst thing two links on one deal could do.

    Both investors are looking at the same property, published by the same
    person, through the same endpoint. What each may see of the OTHER is
    nothing: not their name, not their status, not their offer.
    """
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE)

    second = ok(client.post("/wholesale/buyers", headers=auth_headers,
                            json={"company_name": "Rival Capital",
                                  "email": "rival@example.com"}))
    ok(client.post("/wholesale/deals/%s/disposition" % world["deal_id"],
                   headers=auth_headers,
                   json={"buyer_ids": [world["buyer_id"], second["id"]],
                         "channel": "email"}))

    a = mint(client, auth_headers, world["deal_id"], "buyer",
             buyer_id=world["buyer_id"], recipient_name="Lone Oak Capital")
    b = mint(client, auth_headers, world["deal_id"], "buyer",
             buyer_id=second["id"], recipient_name="Rival Capital")

    # A makes an offer nobody else may see.
    ok(client.post("/wholesale-rooms/buyer/%s/action" % a["token"],
                   json={"action": "offer", "amount": 171000,
                         "message": "RivalMustNotSeeThis"}))

    body = client.get("/wholesale-rooms/buyer/%s" % b["token"])
    assert body.status_code == 200
    text = body.text
    assert "171000" not in text
    assert "RivalMustNotSeeThis" not in text
    assert "Lone Oak" not in text
    # B's own block is B's own — empty, because B has said nothing.
    payload = body.json()
    assert payload.get("you", {}).get("your_offer") is None


def test_a_seller_link_cannot_fetch_an_investor_photo_and_the_reverse(
        client, auth_headers, world, storage):
    """Two audiences, two columns, two routes, and no path between them."""
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE)
    publish(client, auth_headers, world["deal_id"], "seller")

    # One photo for investors only, one for the owner only.
    ok(client.patch("/wholesale/files/%s" % world["hidden_photo"],
                    headers=auth_headers, json={"seller_visible": True}))

    buyer_link = mint(client, auth_headers, world["deal_id"], "buyer",
                      buyer_id=world["buyer_id"])
    seller_link = mint(client, auth_headers, world["deal_id"], "seller")

    # The investor's own photo serves; the owner's does not, through their link.
    assert client.get("/wholesale-rooms/buyer/%s/photo/%s"
                      % (buyer_link["token"], world["shown_photo"])).status_code == 200
    assert client.get("/wholesale-rooms/buyer/%s/photo/%s"
                      % (buyer_link["token"], world["hidden_photo"])).status_code == 404

    # And the same in reverse.
    assert client.get("/wholesale-rooms/seller/%s/photo/%s"
                      % (seller_link["token"], world["hidden_photo"])).status_code == 200
    assert client.get("/wholesale-rooms/seller/%s/photo/%s"
                      % (seller_link["token"], world["shown_photo"])).status_code == 404

    # A seller token on the buyer photo route is refused by the audience check
    # before the photo is even looked for.
    assert client.get("/wholesale-rooms/buyer/%s/photo/%s"
                      % (seller_link["token"], world["shown_photo"])).status_code == 404


def test_the_seller_page_lists_only_photos_published_to_the_owner(
        client, auth_headers, world, storage):
    publish(client, auth_headers, world["deal_id"], "seller")
    ok(client.patch("/wholesale/files/%s" % world["hidden_photo"],
                    headers=auth_headers, json={"seller_visible": True}))
    link = mint(client, auth_headers, world["deal_id"], "seller")

    page = ok(client.get("/wholesale-rooms/seller/%s" % link["token"]))
    ids = [p["id"] for p in page.get("photos", [])]
    assert world["hidden_photo"] in ids
    assert world["shown_photo"] not in ids


def test_branding_carries_no_deal_information(client, auth_headers, world):
    """A brand block is a name, a mark and a colour. Nothing else.

    It is the one part of these payloads assembled from a DIFFERENT table, so
    it is the one most likely to arrive carrying something it should not.
    """
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE)
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])
    brand = ok(client.get("/wholesale-rooms/buyer/%s" % link["token"]))["brand"]

    assert set(brand) == {"name", "logo_url", "accent", "support_email",
                          "support_phone", "website"}
    blob = json.dumps(brand)
    for secret in (SELLER_SECRET, SELLER_NAME, INTERNAL_NOTE,
                   str(MAO_VALUE), str(ASSIGNMENT_FEE), str(CONTRACT_PRICE)):
        assert secret not in blob


def test_the_offer_an_investor_submits_does_not_come_back_to_them_enriched(
        client, auth_headers, world):
    """Their own offer, and only the parts of it they gave us.

    An investor submitting an offer must not learn, from the response or the
    reloaded page, what we make on it — which is the one number that becomes
    computable the moment an offer exists.
    """
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE)
    ok(client.post("/wholesale/deals/%s/disposition" % world["deal_id"],
                   headers=auth_headers,
                   json={"buyer_ids": [world["buyer_id"]], "channel": "email"}))
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])

    res = client.post("/wholesale-rooms/buyer/%s/action" % link["token"],
                      json={"action": "offer", "amount": 171000,
                            "closing_date": "2026-11-20", "financing": "cash",
                            "proof_of_funds": "can_provide",
                            "contact_name": "Ray Whitfield"})
    assert res.status_code == 200
    assert str(CONTRACT_PRICE) not in res.text
    assert str(ASSIGNMENT_FEE) not in res.text
    assert str(MAO_VALUE) not in res.text

    reloaded = client.get("/wholesale-rooms/buyer/%s" % link["token"])
    assert str(CONTRACT_PRICE) not in reloaded.text
    assert str(ASSIGNMENT_FEE) not in reloaded.text


def test_a_proof_of_funds_claim_is_recorded_as_claimed_not_verified(
        client, auth_headers, world):
    """The investor said it. Nobody has looked at a document."""
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE)
    ok(client.post("/wholesale/deals/%s/disposition" % world["deal_id"],
                   headers=auth_headers,
                   json={"buyer_ids": [world["buyer_id"]], "channel": "email"}))
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])

    ok(client.post("/wholesale-rooms/buyer/%s/action" % link["token"],
                   json={"action": "offer", "amount": 171000,
                         "proof_of_funds": "on_file"}))

    board = ok(client.get("/wholesale/deals/%s/buyer-board" % world["deal_id"],
                          headers=auth_headers))
    row = [b for b in board["buyers"] if b["buyer_id"] == world["buyer_id"]][0]
    assert row["pof_status"] == "claimed"
    assert row["pof_status"] != "verified"


def test_a_nonsense_financing_or_date_is_refused_rather_than_stored(
        client, auth_headers, world):
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE)
    ok(client.post("/wholesale/deals/%s/disposition" % world["deal_id"],
                   headers=auth_headers,
                   json={"buyer_ids": [world["buyer_id"]], "channel": "email"}))
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])

    bad_fin = client.post("/wholesale-rooms/buyer/%s/action" % link["token"],
                          json={"action": "offer", "amount": 1,
                                "financing": "bearer-bonds"})
    assert bad_fin.status_code == 400

    bad_date = client.post("/wholesale-rooms/buyer/%s/action" % link["token"],
                           json={"action": "offer", "amount": 1,
                                 "closing_date": "whenever"})
    assert bad_date.status_code == 400
    assert "date" in bad_date.json()["detail"].lower()


def test_the_respondents_details_do_not_overwrite_the_buyers_crm_record(
        client, auth_headers, world):
    """A public page does not get to edit somebody's buyer list.

    Whoever opened the link may not be the person in the CRM — it may be an
    acquisitions assistant, or the link may have been forwarded. Recording what
    they typed AGAINST THE RESPONSE is right; writing it over the buyer's own
    contact details would quietly corrupt the list.
    """
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE)
    ok(client.post("/wholesale/deals/%s/disposition" % world["deal_id"],
                   headers=auth_headers,
                   json={"buyer_ids": [world["buyer_id"]], "channel": "email"}))
    link = mint(client, auth_headers, world["deal_id"], "buyer",
                buyer_id=world["buyer_id"])

    ok(client.post("/wholesale-rooms/buyer/%s/action" % link["token"],
                   json={"action": "interested",
                         "contact_name": "Somebody Else",
                         "contact_email": "forwarded@example.com"}))

    buyers = ok(client.get("/wholesale/buyers", headers=auth_headers,
                           params={"include_test": True}))
    row = [b for b in buyers["buyers"] if b["id"] == world["buyer_id"]][0]
    assert row["email"] == "ray@loneoak.example.com"      # untouched
    assert row["contact_name"] == "Ray Whitfield"         # untouched

    board = ok(client.get("/wholesale/deals/%s/buyer-board" % world["deal_id"],
                          headers=auth_headers))
    entry = [b for b in board["buyers"] if b["buyer_id"] == world["buyer_id"]][0]
    assert entry["respondent_name"] == "Somebody Else"
    assert entry["respondent_email"] == "forwarded@example.com"


def test_no_external_payload_contains_a_database_id_of_anything_else(
        client, auth_headers, world):
    """Nothing to enumerate.

    The deal's own id is on the payload because the page is that deal. What
    must not be there is any OTHER row's id — a buyer, a seller profile, a
    property, an unpublished file — because an id in a public payload is an
    invitation to try it somewhere else.
    """
    publish(client, auth_headers, world["deal_id"], "buyer",
            buyer_room_asking_price=ASKING_PRICE)
    publish(client, auth_headers, world["deal_id"], "seller")
    blink = mint(client, auth_headers, world["deal_id"], "buyer",
                 buyer_id=world["buyer_id"])
    slink = mint(client, auth_headers, world["deal_id"], "seller")

    forbidden_ids = [world["buyer_id"], world["property_id"],
                     world["hidden_photo"], world["private_doc"],
                     world["seller"]["id"]]

    for token, path in ((blink["token"], "buyer"), (slink["token"], "seller")):
        text = client.get("/wholesale-rooms/%s/%s" % (path, token)).text
        for bad in forbidden_ids:
            assert bad not in text, "%s payload carries %s" % (path, bad)
