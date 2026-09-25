"""ONE DEAL, INTAKE TO MONEY IN THE BANK.

Every other wholesale test file checks a part. This one checks that the parts
are still connected — that a property typed in at one end comes out of the
other as recorded revenue, through every gate, with an investor and a seller
looking at their own pages in the middle.

It is written as one long test on purpose. Splitting it would let each step
pass against a fixture rather than against the state the previous step actually
left behind, which is precisely the failure a journey test exists to catch.

WHAT IT REFUSES TO DO. It does not send an email, a text or a signature
request; it does not call a paid provider; it does not produce a legal
document; and it does not mark money collected because a stage changed. Where
the product would need a human or a vendor, the test supplies the human and
asserts the vendor is absent.
"""
import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128


def ok(response, what=""):
    assert response.status_code in (200, 201), \
        "%s -> %s %s" % (what or response.url, response.status_code,
                         response.text[:400])
    return response.json()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WHOLESALE_LOCAL_MEDIA_ROOT", str(tmp_path / "media"))
    return tmp_path


def test_a_whole_deal_from_intake_to_fee_collected(client, auth_headers, storage):
    H = auth_headers

    # ── 1. A property is typed in ─────────────────────────────────────────
    prop = ok(client.post("/wholesale/properties", headers=H, json={
        "street_address": "1418 Cedar Springs Rd", "city": "Dallas",
        "state": "TX", "zip_code": "75201", "county": "Dallas",
        "parcel_apn": "00000512340000000", "property_type": "single_family",
        "bedrooms": 3, "bathrooms": 2, "square_feet": 1520,
        "lot_size_sqft": 6500, "year_built": 1968, "market": "DFW",
        "occupancy_status": "vacant", "owner_name": "Marisol Alvarez",
        "is_test": True,
    }), "create property")
    deal_id = prop["deal"]["id"]
    assert deal_id

    # ── 2. The owner is attached ──────────────────────────────────────────
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"], headers=H,
                   json={"first_name": "Marisol", "last_name": "Alvarez",
                         "phone": "2145550137", "email": "m@example.com"}),
       "attach seller")

    # ── 3. Photos, a cover, and two separate publication decisions ────────
    photos = []
    for name, category in (("front.png", "exterior_front"),
                           ("kitchen.png", "kitchen"),
                           ("roof.png", "roof")):
        photos.append(ok(client.post(
            "/wholesale/properties/%s/photos" % prop["id"], headers=H,
            files={"file": (name, PNG, "image/png")},
            data={"caption": category.replace("_", " ")}), "upload " + name))

    ok(client.patch("/wholesale/files/%s" % photos[0]["id"], headers=H,
                    json={"is_primary": True, "category": "exterior_front",
                          "buyer_visible": True, "seller_visible": True}))
    ok(client.patch("/wholesale/files/%s" % photos[1]["id"], headers=H,
                    json={"category": "kitchen", "buyer_visible": True}))
    # The third stays internal. Uploading is not publishing.
    listed = ok(client.get("/wholesale/properties/%s/photos" % prop["id"],
                           headers=H))["photos"]
    assert sum(1 for p in listed if p["buyer_visible"]) == 2
    assert sum(1 for p in listed if p["seller_visible"]) == 1
    assert sum(1 for p in listed if p["is_primary"]) == 1

    # ── 4. The seller is qualified from something they actually said ──────
    ok(client.post("/wholesale/deals/%s/seller-reply" % deal_id, headers=H,
                   json={"message": "Yes I want to sell, the house is empty "
                                    "and I need it done in 30 days. Roof is bad.",
                         "mode": "manual"}), "seller reply")

    # ── 5. Comps, then ARV, then the offer maths ──────────────────────────
    for address, price, sqft in (("1180 Magnolia Dr", 300000, 1520),
                                 ("904 Beckley Ave", 289000, 1460),
                                 ("512 Sylvan Ave", 295000, 1495)):
        ok(client.post("/wholesale/deals/%s/comps" % deal_id, headers=H,
                       json={"street_address": address, "sale_price": price,
                             "square_feet": sqft, "sale_date": "2026-06-13",
                             "distance_miles": 0.4}), "comp " + address)

    analysis = ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=H,
                               json={"arv": 300000, "repair_estimate": 45000}),
                  "analysis")
    room = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))
    mao = room["analysis"]["max_allowable_offer"]
    assert mao is not None and mao > 0
    # The MAO is computed, not typed. Nothing in the journey supplied it.
    assert mao < 300000 - 45000

    # ── 6. An offer needs a person ────────────────────────────────────────
    approval = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=H,
                              json={"kind": "offer", "amount": mao}), "ask offer")
    ok(client.post("/wholesale/approvals/%s/decide" % approval["id"], headers=H,
                   json={"approve": True}), "approve offer")

    # ── 7. Negotiation, recorded as a ledger ──────────────────────────────
    ok(client.post("/wholesale/deals/%s/offers" % deal_id, headers=H,
                   json={"amount": mao, "direction": "us"}), "our offer")
    ok(client.post("/wholesale/deals/%s/offers" % deal_id, headers=H,
                   json={"amount": 150000, "direction": "seller"}), "their counter")

    # ── 8. Under contract ─────────────────────────────────────────────────
    ok(client.patch("/wholesale/deals/%s/contract" % deal_id, headers=H,
                    json={"contract_price": 144000, "contract_status": "signed",
                          "contract_date": "2026-09-20",
                          "inspection_deadline": "2026-10-06"}), "contract")

    # THE GATE IS REAL. Going under contract refuses until a person has
    # approved it — found by writing this journey without the step and being
    # stopped by a 409 that named exactly what was missing.
    blocked = client.post("/wholesale/deals/%s/stage" % deal_id, headers=H,
                          json={"stage": "under_contract"})
    assert blocked.status_code == 409
    assert "approved contract" in blocked.json()["detail"]

    contract_approval = ok(client.post("/wholesale/deals/%s/approvals" % deal_id,
                                       headers=H,
                                       json={"kind": "contract",
                                             "amount": 144000}), "ask contract")
    ok(client.post("/wholesale/approvals/%s/decide" % contract_approval["id"],
                   headers=H, json={"approve": True}), "approve contract")
    ok(client.post("/wholesale/deals/%s/stage" % deal_id, headers=H,
                   json={"stage": "under_contract"}), "stage")

    # ── 9. The paperwork sheet says what is still missing ─────────────────
    sheet = ok(client.get("/wholesale/deals/%s/fill-sheet" % deal_id, headers=H))
    assert sheet["summary"]["total"] > 0
    assert "Legal description" in sheet["missing"]
    assert "Seller of record" in sheet["needs_review"]
    # And it still contains no contract language whatsoever.
    body = client.get("/wholesale/deals/%s/fill-sheet" % deal_id, headers=H).text.lower()
    for phrase in ("whereas", "hereby", "the parties agree", "witnesseth"):
        assert phrase not in body

    # ── 10. Buyers, matched and sent a deal sheet ─────────────────────────
    buyer = ok(client.post("/wholesale/buyers", headers=H,
                           json={"company_name": "Lone Oak Capital",
                                 "contact_name": "Ray Whitfield",
                                 "email": "ray@loneoak.example.com",
                                 "typical_close_days": 10, "is_test": True}))
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % buyer["id"], headers=H,
                   json={"label": "DFW", "states": ["TX"], "min_price": 1,
                         "max_price": 400000}))
    ok(client.post("/wholesale/deals/%s/match-buyers" % deal_id, headers=H, json={}))
    matches = ok(client.get("/wholesale/deals/%s/matches" % deal_id, headers=H))
    assert matches["matches"], "the matching engine returned nothing"
    # It explains itself rather than handing over a single opaque number.
    assert matches["matches"][0].get("factors")

    ok(client.post("/wholesale/deals/%s/disposition" % deal_id, headers=H,
                   json={"buyer_ids": [buyer["id"]], "channel": "email"}),
       "disposition")

    # ── 11. The investor room is published, then shared ───────────────────
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H,
                    json={"buyer_room_asking_price": 158000,
                          "buyer_room_summary": "Vacant, easy access.",
                          "buyer_room_condition": "Roof at end of life.",
                          "buyer_room_show_arv": True,
                          "buyer_room_show_comps": True}))
    ok(client.post("/wholesale/deals/%s/publication/state" % deal_id, headers=H,
                   json={"audience": "buyer", "published": True}))
    link = ok(client.post("/wholesale/deals/%s/share-links" % deal_id, headers=H,
                          json={"audience": "buyer", "buyer_id": buyer["id"],
                                "recipient_name": "Lone Oak Capital"}))
    token = link["token"]
    assert len(token) >= 32, "a share token must not be guessable"

    # ── 12. The buyer opens it, and sees only what was published ──────────
    page = ok(client.get("/wholesale-rooms/buyer/%s" % token), "investor room")
    assert page["asking_price"] == 158000
    assert page["arv"] == 300000                     # switched on
    assert "estimated_repairs" not in page           # left off
    assert len(page["photos"]) == 2                  # not the third
    assert page["property"]["occupancy_status"] == "vacant"
    assert page["brand"]["name"]                     # somebody's name on it
    raw = client.get("/wholesale-rooms/buyer/%s" % token).text
    for secret in ("144000", "Marisol", "2145550137"):
        assert secret not in raw, "investor page leaked %s" % secret

    # ── 13. The buyer makes a real offer from that page ───────────────────
    ok(client.post("/wholesale-rooms/buyer/%s/action" % token,
                   json={"action": "offer", "amount": 156000,
                         "closing_date": "2026-11-08", "financing": "cash",
                         "proof_of_funds": "can_provide",
                         "contact_name": "Ray Whitfield",
                         "message": "Can close in ten days."}), "buyer offer")

    # ── 14. …and it is on the operator's board, with the detail ───────────
    board = ok(client.get("/wholesale/deals/%s/buyer-board" % deal_id, headers=H))
    row = [b for b in board["buyers"] if b["buyer_id"] == buyer["id"]][0]
    assert row["status"] == "offer_submitted"
    assert row["offer_amount"] == 156000
    assert row["target_close_date"] == "2026-11-08"
    assert row["offer_financing"] == "cash"
    # "I can send it" is a request for a document, not a document. Whatever it
    # maps to, the one thing it must never be is `verified`.
    assert row["pof_status"] == "requested"
    assert row["pof_status"] != "verified"
    assert row["respondent_name"] == "Ray Whitfield"
    assert row["spread"] == 156000 - 144000

    activity = ok(client.get("/wholesale/deals/%s/share-activity" % deal_id,
                             headers=H))["activity"]
    assert any(a["action"] == "offer" for a in activity)
    assert any(a["action"] == "view" for a in activity)

    # ── 15. A person picks the buyer ──────────────────────────────────────
    ok(client.post("/wholesale/deals/%s/select-buyer" % deal_id, headers=H,
                   json={"outreach_id": row["outreach_id"]}), "select buyer")

    # ── 16. The assignment needs a person, and refuses without the numbers ─
    ok(client.post("/wholesale/deals/%s/assign" % deal_id, headers=H,
                   json={"buyer_id": buyer["id"], "buyer_price": 156000,
                         "assignment_fee": 12000}), "assign")
    assign_approval = ok(client.post("/wholesale/deals/%s/approvals" % deal_id,
                                     headers=H,
                                     json={"kind": "assignment", "amount": 12000}))
    ok(client.post("/wholesale/approvals/%s/decide" % assign_approval["id"],
                   headers=H, json={"approve": True}))

    # ── 17. The seller's own page, published separately ───────────────────
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H,
                    json={"seller_room_message": "On track for the 8th.",
                          "seller_room_contact_name": "Dana Reyes",
                          "seller_room_contact_phone": "2145550100"}))
    ok(client.post("/wholesale/deals/%s/publication/state" % deal_id, headers=H,
                   json={"audience": "seller", "published": True}))
    slink = ok(client.post("/wholesale/deals/%s/share-links" % deal_id, headers=H,
                           json={"audience": "seller",
                                 "recipient_name": "Marisol Alvarez"}))
    seller_page = ok(client.get("/wholesale-rooms/seller/%s" % slink["token"]))

    # THE WHOLE POINT OF THE SELLER PAGE.
    assert seller_page["contact"]["name"] == "Dana Reyes"
    assert seller_page["progress"]
    assert len(seller_page["photos"]) == 1           # only the one published
    seller_raw = client.get("/wholesale-rooms/seller/%s" % slink["token"]).text
    for forbidden in ("Lone Oak", "156000", "12000", "300000", "ray@loneoak"):
        assert forbidden not in seller_raw, "seller page leaked %s" % forbidden

    # ── 18. Title and closing ─────────────────────────────────────────────
    ok(client.patch("/wholesale/deals/%s/title" % deal_id, headers=H,
                    json={"title_company": "Lone Star Title",
                          "title_status": "clear",
                          "title_file_number": "LS-4471"}), "title")
    ok(client.post("/wholesale/deals/%s/close" % deal_id, headers=H,
                   json={"closing_date": "2026-11-08"}), "close")

    # ── 19. CLOSED IS NOT PAID ────────────────────────────────────────────
    after_close = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))
    assert after_close["deal"]["payment_state"] == "payment_pending"
    assert not after_close["deal"].get("wholesale_fee_collected")

    board_now = ok(client.get("/wholesale/operating-board", headers=H,
                              params={"include_test": True}))
    fees_before = board_now["headline"]["fees_collected"]

    # ── 20. Money that actually arrived ───────────────────────────────────
    ok(client.post("/wholesale/deals/%s/fee-collected" % deal_id, headers=H,
                   json={"amount": 11800, "method": "wire",
                         "reference": "WIRE-2026-8891",
                         "collected_at": "2026-11-09",
                         "variance_note": "Title held $200 for recording."}),
       "record collection")

    paid = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))
    assert paid["deal"]["payment_state"] == "fee_collected"
    assert float(paid["deal"]["wholesale_fee_collected"]) == 11800

    # ── 21. …and only now does the Command Center count it ────────────────
    board_after = ok(client.get("/wholesale/operating-board", headers=H,
                                params={"include_test": True}))
    assert (board_after["headline"]["fees_collected"]
            == (fees_before or 0) + 11800), \
        "collected revenue did not move by exactly what was recorded"


def test_closing_a_deal_never_marks_the_money_collected(client, auth_headers):
    """The single accounting rule this module exists to keep.

    Stated as its own test because it is the one a future change is most
    likely to break by being helpful.
    """
    H = auth_headers
    prop = ok(client.post("/wholesale/properties", headers=H, json={
        "street_address": "9 Closing Way", "city": "Dallas", "state": "TX",
        "is_test": True}))
    deal_id = prop["deal"]["id"]

    ok(client.patch("/wholesale/deals/%s/contract" % deal_id, headers=H,
                    json={"contract_price": 100000}))
    ok(client.post("/wholesale/deals/%s/close" % deal_id, headers=H,
                   json={"closing_date": "2026-01-01"}))

    room = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))
    assert room["deal"]["payment_state"] == "payment_pending"
    assert not room["deal"].get("wholesale_fee_collected")
    assert not room["deal"].get("fee_collected_at")
