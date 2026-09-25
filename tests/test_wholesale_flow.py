"""The acceptance test: one property, all the way through, over HTTP.

This is the flow from the mission, run end to end against the real application:

    property -> owner -> contact details -> AI qualification -> analysis ->
    comps -> offer -> approval -> under contract -> buyer matching ->
    buyer outreach -> buyer selected -> assignment -> title -> closed -> fee

Every record it creates is marked TEST, which is the same flag
`app/services/test_records.py` uses for leads, so nothing here can reach a real
outreach path or a real performance number.

What this file is really checking is that DATA ENTERED IN ONE PART OF THE SYSTEM
DRIVES THE NEXT PART. A suite of screens that each work alone would pass a
per-endpoint test and fail this one.
"""

import io
import json

import pytest


@pytest.fixture()
def wholesale(client, auth_headers):
    """A tiny helper so the flow below reads like the flow, not like HTTP."""
    class W:
        def get(self, path, **params):
            return client.get("/wholesale" + path, headers=auth_headers, params=params)

        def post(self, path, body=None):
            return client.post("/wholesale" + path, headers=auth_headers,
                               json=body if body is not None else {})

        def patch(self, path, body):
            return client.patch("/wholesale" + path, headers=auth_headers, json=body)

        def delete(self, path):
            return client.delete("/wholesale" + path, headers=auth_headers)

        def upload(self, path, data, form=None, name="evidence.png",
                   content_type="image/png"):
            """Multipart, through the same client as everything else here."""
            return client.post("/wholesale" + path, headers=auth_headers,
                               files={"file": (name, data, content_type)},
                               data=form or {})
    return W()


def ok(response):
    assert response.status_code in (200, 201), \
        "%s -> %s %s" % (response.request.url, response.status_code, response.text[:400])
    return response.json()


# ── The whole flow ──────────────────────────────────────────────────────────

def test_a_real_property_runs_the_whole_way_through(wholesale):
    # 1. Settings exist on first read, with the module's defaults.
    settings = ok(wholesale.get("/settings"))
    assert settings["investor_percentage"] == 70.0
    assert settings["require_offer_approval"] is True
    assert len(settings["pipeline_stages_effective"]) >= 15
    # The provider status indicator, computed from the environment.
    assert settings["providers"]["enrichment"][0]["key"] == "manual"

    # 2. A property, with almost nothing known about it.
    prop = ok(wholesale.post("/properties", {
        "street_address": "123 Test Property Ln", "city": "Dallas", "state": "TX",
        "zip_code": "75201", "county": "Dallas", "market": "DFW",
        "property_type": "single_family", "bedrooms": 3, "bathrooms": 2,
        "square_feet": 1500, "year_built": 1985,
        "is_test": True, "test_note": "Sandbox scenario"}))
    assert prop["deal"]["stage"] == "new_property"
    property_id, deal_id = prop["id"], prop["deal"]["id"]

    # 3. The owner. Becomes a Lead, which is what carries DNC and consent.
    seller = ok(wholesale.post("/properties/%s/seller" % property_id, {
        "first_name": "Pat", "last_name": "Owner",
        "owner_status": "owner_of_record"}))
    assert seller["lead_id"]
    assert seller["is_dnc"] is False

    # 4. No skip-trace provider is connected, so enrichment says so honestly and
    #    invents nothing.
    enrich = ok(wholesale.post("/enrichment/run", {"property_ids": [property_id]}))
    assert enrich["provider"] == "manual"
    assert enrich["results"][0]["status"] == "manual"
    assert enrich["results"][0]["phones"] == []
    assert "no skip-trace provider" in enrich["results"][0]["message"].lower()

    # 5. The manual fallback carries the workflow forward, as a first-class result.
    manual = ok(wholesale.post("/properties/%s/manual-contact" % property_id, {
        "phone": "2145557788", "email": "pat.owner@example.com"}))
    assert manual["status"] == "succeeded"
    assert manual["stage"] == "ready_for_outreach"

    # 6. A seller message is read into structured answers.
    reply = ok(wholesale.post("/deals/%s/seller-reply" % deal_id, {
        "message": ("Yes I would sell. It's vacant and in rough shape, needs work. "
                    "I want $120,000 and I need it done asap. Just me deciding."),
        "mode": "manual"}))
    assert reply["reading"]["intent"] in ("interested", "qualified_opportunity")
    assert reply["reading"]["timeline"] == "asap"
    assert reply["reading"]["occupancy"] == "vacant"
    assert reply["reading"]["asking_price"] == 120000
    assert reply["qualification"]["band"] in ("high", "medium", "review")
    assert reply["qualification"]["reasons"]

    # 7. Comps, entered by hand because no comps provider is connected.
    for price, sqft in ((300000, 1500), (290000, 1450), (310000, 1520)):
        result = ok(wholesale.post("/deals/%s/comps" % deal_id, {
            "street_address": "%d Comp St" % price, "sale_price": price,
            "square_feet": sqft, "sale_date": "2026-06-01"}))
    analysis = result["analysis"]
    assert analysis["arv"] is not None
    assert analysis["arv_source"] == "estimated"
    assert analysis["arv_method"].startswith("comps:")

    # 8. Repairs, and the offer the formula allows.
    analysis = ok(wholesale.patch("/deals/%s/analysis" % deal_id, {
        "repair_estimate": 45000, "desired_wholesale_fee": 12000}))
    assert analysis["repair_estimate_source"] == "manual"
    assert analysis["max_allowable_offer"] is not None
    assert [s["label"] for s in analysis["steps"]][-1] == "= maximum allowable offer"
    mao = analysis["max_allowable_offer"]

    # 9. Propose an offer. The deal moves to OFFER REVIEW, not to OFFER SENT.
    ok(wholesale.patch("/deals/%s/analysis" % deal_id, {"proposed_offer": mao}))
    approval = ok(wholesale.post("/deals/%s/approvals" % deal_id, {
        "kind": "offer", "amount": mao,
        "recommendation": "Offer the maximum allowable amount",
        "reasoning": "Seller is motivated and the spread holds."}))
    assert approval["status"] == "pending"
    # The numbers are SNAPSHOTTED, so a later edit cannot rewrite what was approved.
    assert approval["inputs"]["max_allowable_offer"] == mao

    # 10. THE GATE. The offer cannot be sent until a person approves it.
    refused = wholesale.post("/deals/%s/stage" % deal_id, {"stage": "offer_sent"})
    assert refused.status_code == 409
    assert "needs an approved offer" in refused.json()["detail"]

    ok(wholesale.post("/approvals/%s/decide" % approval["id"],
                      {"approve": True, "comments": "Go"}))
    moved = ok(wholesale.post("/deals/%s/stage" % deal_id, {"stage": "offer_sent"}))
    assert moved["stage"] == "offer_sent"

    # 11. Under contract — a second gate, a second approval.
    refused = wholesale.post("/deals/%s/stage" % deal_id, {"stage": "under_contract"})
    assert refused.status_code == 409
    contract_approval = ok(wholesale.post("/deals/%s/approvals" % deal_id, {
        "kind": "contract", "amount": mao}))
    ok(wholesale.post("/approvals/%s/decide" % contract_approval["id"],
                      {"approve": True}))
    ok(wholesale.patch("/deals/%s/contract" % deal_id, {
        "contract_price": mao, "contract_status": "signed",
        "close_of_escrow_target": "2026-11-15"}))
    under_contract = ok(wholesale.post("/deals/%s/stage" % deal_id,
                                       {"stage": "under_contract"}))
    assert under_contract["stage"] == "under_contract"

    # 12. Three buyers with genuinely different buy boxes.
    buyers = {}
    for key, buyer_payload, box_payload in (
        ("perfect",
         {"company_name": "Exact Fit Capital", "email": "exact@example.com",
          "is_test": True},
         {"states": ["TX"], "counties": ["Dallas"], "property_types": ["single_family"],
          "strategies": ["flip"], "min_price": 100000, "max_price": 250000,
          "min_beds": 2, "min_sqft": 1000, "rehab_tolerance": "heavy"}),
        ("partial",
         {"company_name": "Wide Net Holdings", "email": "wide@example.com",
          "is_test": True},
         {"states": ["TX"], "min_price": 50000, "max_price": 500000,
          "rehab_tolerance": "light"}),
        ("outside",
         {"company_name": "Oklahoma Only LLC", "email": "ok@example.com",
          "is_test": True},
         {"states": ["OK"], "min_price": 100000, "max_price": 250000}),
    ):
        buyer = ok(wholesale.post("/buyers", buyer_payload))
        ok(wholesale.post("/buyers/%s/buy-boxes" % buyer["id"], box_payload))
        buyers[key] = buyer

    # 13. Matching, ranked, with reasons.
    matches = ok(wholesale.post("/deals/%s/match-buyers" % deal_id))["matches"]
    assert len(matches) == 3
    top = matches[0]
    assert top["buyer_id"] == buyers["perfect"]["id"]
    assert top["score"] > 0
    assert top["disqualified"] is False
    assert any(f["detail"] for f in top["factors"])
    outside = [m for m in matches if m["buyer_id"] == buyers["outside"]["id"]][0]
    assert outside["disqualified"] is True
    assert "geography" in outside["disqualified_reason"]

    # 14. The deal sheet, previewed before it goes anywhere. No seller in it.
    preview = ok(wholesale.post("/deals/%s/disposition/preview" % deal_id, {
        "buyer_ids": [buyers["perfect"]["id"]], "asking_price": mao + 12000}))
    assert "Pat" not in preview["body"]
    assert "pat.owner@example.com" not in preview["body"]
    assert "2145557788" not in preview["body"]
    assert "estimates" in preview["body"]

    # Sending is attempted for real now. THIS DEAL IS A SANDBOX DEAL, so the
    # first of the six refusals fires and nothing reaches a provider — which is
    # the single most important thing about sandbox mode and is asserted here
    # rather than assumed. The enabled send path, with a fake provider, is
    # tested in test_wholesale_disposition.py.
    sent = ok(wholesale.post("/deals/%s/disposition" % deal_id, {
        "buyer_ids": [buyers["perfect"]["id"], buyers["partial"]["id"]],
        "asking_price": mao + 12000}))
    assert sent["sent"] == 0
    assert sent["failed"] == 2
    assert len(sent["results"]) == 2
    assert all(r["code"] == "sandbox" for r in sent["results"])
    assert "sandbox record" in sent["results"][0]["reason"]
    # The rows still exist and still carry the composed sheet, so the rest of
    # the disposition workflow can be rehearsed on them.
    assert all(r["outreach_id"] for r in sent["results"])

    # 15. A buyer says yes.
    room = ok(wholesale.get("/deals/%s" % deal_id))
    outreach_id = [o["id"] for o in room["buyer_outreach"]
                   if o["buyer_id"] == buyers["perfect"]["id"]][0]
    ok(wholesale.patch("/outreach/%s" % outreach_id, {
        "status": "offer_submitted", "offer_amount": mao + 12000,
        "response_note": "We'll take it."}))

    # 16. Assignment, behind its own approval gate.
    assigned = ok(wholesale.post("/deals/%s/assign" % deal_id, {
        "buyer_id": buyers["perfect"]["id"], "buyer_price": mao + 12000}))
    assert assigned["assignment_fee"] == 12000

    refused = wholesale.post("/deals/%s/stage" % deal_id, {"stage": "assignment_pending"})
    assert refused.status_code == 409
    assignment_approval = ok(wholesale.post("/deals/%s/approvals" % deal_id,
                                            {"kind": "assignment"}))
    ok(wholesale.post("/approvals/%s/decide" % assignment_approval["id"],
                      {"approve": True}))
    ok(wholesale.post("/deals/%s/stage" % deal_id, {"stage": "assignment_pending"}))

    # 17. Documents — slots and states, never a generated legal form.
    doc = ok(wholesale.post("/deals/%s/documents" % deal_id, {
        "doc_type": "assignment_agreement", "title": "Assignment - 123 Test",
        "parties": [{"name": "Pat Owner", "role": "seller"},
                    {"name": "Exact Fit Capital", "role": "assignee"}]}))
    assert doc["status"] == "needed"
    signed = ok(wholesale.patch("/documents/%s" % doc["id"], {
        "doc_type": "assignment_agreement", "file_name": "signed.pdf",
        "signature_status": "signed"}))
    assert signed["status"] == "executed"
    assert signed["executed_at"]

    # 18. Title, then closing and the fee — recorded by a person.
    ok(wholesale.patch("/deals/%s/title" % deal_id, {
        "title_company": "Lone Star Title", "title_status": "opened"}))
    closed = ok(wholesale.post("/deals/%s/close" % deal_id, {
        "wholesale_fee_collected": 12000, "closing_date": "2026-11-10",
        "deal_result": "closed_won"}))
    assert closed["stage"] == "closed"
    assert closed["wholesale_fee_collected"] == 12000

    # 19. The dashboard reflects all of it — from rows, not constants.
    board = ok(wholesale.get("/dashboard", include_test=True))
    assert board["properties_imported"] == 1
    assert board["sellers_identified"] == 1
    assert board["closed_deals"] == 1
    assert board["gross_assignment_fees"] == 12000
    assert board["buyers_total"] == 3
    assert board["buyer_responses"] == 1

    # And a test record is invisible to the real one, which is the whole point
    # of the sandbox flag.
    real_board = ok(wholesale.get("/dashboard"))
    assert real_board["properties_imported"] == 0
    assert real_board["closed_deals"] == 0
    assert real_board["gross_assignment_fees"] == 0

    # 20. The audit trail names every actor, including the non-human ones.
    events = ok(wholesale.get("/events", deal_id=deal_id))["events"]
    actions = {e["action"] for e in events}
    for expected in ("property.created", "seller.attached", "seller.qualified",
                     "analysis.updated", "approval.requested", "approval.decided",
                     "deal.stage_changed", "buyers.matched", "disposition.composed",
                     "buyer_outreach.blocked", "assignment.set", "deal.closed"):
        assert expected in actions, "missing audit event: %s" % expected
    assert {"user", "ai", "automation", "system"} & {e["actor_type"] for e in events}


# ── The deal room ───────────────────────────────────────────────────────────

def test_the_deal_room_returns_every_section_in_one_request(wholesale):
    prop = ok(wholesale.post("/properties", {
        "street_address": "9 Room St", "city": "Dallas", "state": "TX",
        "is_test": True}))
    room = ok(wholesale.get("/deals/%s" % prop["deal"]["id"]))
    for section in ("deal", "property", "seller", "analysis", "arv_calculation",
                    "comps", "approvals", "documents", "buyer_matches",
                    "buyer_outreach", "communications", "events", "stages"):
        assert section in room, "deal room is missing %s" % section


# ── CSV import ──────────────────────────────────────────────────────────────

def test_csv_import_creates_properties_sellers_and_reports_what_it_skipped(
        client, auth_headers):
    csv_text = (
        "Address,City,State,Zip,Beds,Baths,SqFt,Owner Name,Phone,Email,Weird Column\n"
        "1 Import Way,Dallas,TX,75201,3,2,1400,Alex Owner,2145551234,alex@example.com,keepme\n"
        "2 Import Way,Dallas,TX,75201,4,3,2100,Sam Owner,,,\n"
        ",,,,,,,,,,\n")
    response = client.post(
        "/wholesale/properties/import?list_name=Night%20List&is_test=true",
        headers=auth_headers,
        files={"file": ("list.csv", io.BytesIO(csv_text.encode()), "text/csv")})
    body = ok(response)
    assert body["created"] == 2
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["row"] == 4
    assert "Weird Column" in body["unmapped_columns"]

    listing = ok(client.get("/wholesale/properties", headers=auth_headers,
                            params={"include_test": True}))
    stages = {p["street_address"]: p["deal"]["stage"] for p in listing["properties"]}
    # The one with contact details is ready to work; the one without needs enrichment.
    assert stages["1 Import Way"] == "ready_for_outreach"
    assert stages["2 Import Way"] == "enrichment_needed"
    # The unrecognised column was kept, not discarded.
    kept = [p for p in listing["properties"] if p["street_address"] == "1 Import Way"][0]
    assert "keepme" in (kept["notes"] or "")


def test_buyer_import_builds_buy_boxes_and_rejects_uncontactable_rows(
        client, auth_headers):
    csv_text = (
        "Company,Email,States,Counties,Min Price,Max Price,Rehab,Strategy\n"
        "Import Capital,ic@example.com,TX,Dallas,100000,300000,heavy,flip\n"
        "No Contact LLC,,TX,Dallas,100000,300000,light,rental\n")
    body = ok(client.post(
        "/wholesale/buyers/import?is_test=true", headers=auth_headers,
        files={"file": ("buyers.csv", io.BytesIO(csv_text.encode()), "text/csv")}))
    assert body["created"] == 1
    assert body["buy_boxes_created"] == 1
    assert body["skipped"][0]["reason"].startswith("no email and no phone")

    buyers = ok(client.get("/wholesale/buyers", headers=auth_headers,
                           params={"include_test": True}))["buyers"]
    box = buyers[0]["buy_boxes"][0]
    assert box["states"] == ["tx"]
    assert box["rehab_tolerance"] == "heavy"
    assert box["max_price"] == 300000


# ═══════════════════════════════════════════════════════════════════════
# PHASE 3 — the same journey, through the screens a person actually uses
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def storage(tmp_path, monkeypatch):
    """File storage turned on for this walk, the same way the files suite does
    it. Without it the module correctly refuses every upload, which is its own
    test elsewhere — here the point is the journey with files in it."""
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WHOLESALE_LOCAL_MEDIA_ROOT", str(tmp_path / "media"))
    return tmp_path


def test_the_phase_three_journey_lead_to_fee_collected(wholesale, storage):
    """LEAD → CONVERSATION → ANALYSIS → OFFER → CONTRACT → DISPOSITION →
    CASH BUYER → TITLE → CLOSING → ASSIGNMENT FEE COLLECTED.

    The Phase 1 flow test above proves the pipeline moves. This one proves the
    PRODUCTION-USABILITY surface — the editing, the photographs, the comp
    arithmetic, the negotiation ledger, the disposition board, the economics
    lock — works on the same journey, in order, through the endpoints the
    screens actually call.
    """
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 96

    # ── LEAD ────────────────────────────────────────────────────────────────
    prop = ok(wholesale.post("/properties", {
        "street_address": "1207 Magnolia Dr", "city": "Dallas", "state": "TX",
        "county": "Dallas", "zip_code": "75201", "property_type": "single_family",
        "bedrooms": 3, "bathrooms": 2, "square_feet": 1520, "is_test": True}))
    property_id, deal_id = prop["id"], prop["deal"]["id"]

    # The property is EDITABLE after creation, and an emptied field clears.
    ok(wholesale.patch("/properties/%s" % property_id,
                       {"year_built": 1968, "occupancy_status": "vacant"}))
    ok(wholesale.patch("/properties/%s" % property_id, {"occupancy_status": None}))
    room = ok(wholesale.get("/deals/%s" % deal_id))
    assert room["property"]["year_built"] == 1968
    assert room["property"]["occupancy_status"] is None

    # A photograph of the actual house.
    cap = ok(wholesale.get("/files/capability"))
    assert cap["uploads_enabled"], "local file storage should be on under test"
    photo = ok(wholesale.upload("/properties/%s/photos" % property_id, png))
    assert photo["is_primary"] is True, "the first photo becomes the cover"

    # ── SELLER CONVERSATION ─────────────────────────────────────────────────
    ok(wholesale.post("/properties/%s/seller" % property_id, {
        "first_name": "Marisol", "last_name": "Alvarez", "phone": "2145550137"}))
    ok(wholesale.post("/deals/%s/seller-reply" % deal_id, {
        "message": ("Yes I would sell. It is vacant and needs work. I want "
                    "$150,000 and I need it done quickly."),
        "mode": "manual"}))
    room = ok(wholesale.get("/deals/%s" % deal_id))
    # The thread is what the screen renders; both halves must be there.
    assert room["communications"]["inbound"], "the reply is on the thread"
    for entry in room["communications"]["inbound"]:
        # Never an enum repr. This was drawn on screen as REPLYCLASSIFICATION.HOT.
        assert "." not in (entry["classification"] or ""), entry["classification"]

    # ── ANALYSIS ────────────────────────────────────────────────────────────
    comps = []
    for address, price, sqft in (("A", 300000, 1500), ("B", 290000, 1450),
                                 ("C", 480000, 1480)):
        comps.append(ok(wholesale.post("/deals/%s/comps" % deal_id, {
            "street_address": address, "sale_price": price, "square_feet": sqft,
            "sale_date": "2026-06-01", "year_built": 1970}))["comp"])
    ok(wholesale.upload("/comps/%s/photo" % comps[0]["id"], png))

    room = ok(wholesale.get("/deals/%s" % deal_id))
    stats = room["comp_statistics"]
    assert stats["included_count"] == 3
    # Median and average disagree because C is an outlier — which is the whole
    # reason both are shown.
    assert stats["median_price_per_sqft"] != stats["average_price_per_sqft"]
    assert room["comps"][0]["photo"] is not None or room["comps"][-1]["photo"] is not None

    # EXCLUDE FROM ARV is not DELETE COMP: the row survives, the maths changes.
    ok(wholesale.patch("/comps/%s" % comps[2]["id"], {"included": False}))
    room = ok(wholesale.get("/deals/%s" % deal_id))
    assert len(room["comps"]) == 3
    assert room["comp_statistics"]["included_count"] == 2
    assert room["comp_statistics"]["excluded_count"] == 1

    ok(wholesale.patch("/deals/%s/analysis" % deal_id,
                       {"repair_estimate": 45000, "desired_wholesale_fee": 12000}))
    analysis = ok(wholesale.post("/deals/%s/analysis/recalculate" % deal_id))
    mao = analysis["max_allowable_offer"]
    assert mao is not None

    # ── OFFER ───────────────────────────────────────────────────────────────
    first = ok(wholesale.post("/deals/%s/offers" % deal_id,
                              {"amount": mao - 5000, "direction": "us"}))
    assert first["over_mao"] is False
    ok(wholesale.post("/deals/%s/offers" % deal_id,
                      {"amount": mao + 20000, "direction": "seller",
                       "notes": "Wants more"}))
    ours = ok(wholesale.post("/deals/%s/offers" % deal_id,
                             {"amount": mao, "direction": "us"}))
    ok(wholesale.patch("/offers/%s" % ours["offer"]["id"], {"status": "accepted"}))

    offers = ok(wholesale.get("/deals/%s/offers" % deal_id))["offers"]
    assert [o["direction"] for o in offers] == ["us", "seller", "us"]
    # Their counter is their position, never ours.
    assert ok(wholesale.get("/deals/%s" % deal_id))["analysis"]["proposed_offer"] == mao
    # Each move keeps the MAO as it stood, so a later edit cannot rewrite history.
    assert all(o["mao_at_time"] is not None for o in offers)

    # ── CONTRACT ────────────────────────────────────────────────────────────
    approval = ok(wholesale.post("/deals/%s/approvals" % deal_id,
                                 {"kind": "offer", "amount": mao}))
    ok(wholesale.post("/approvals/%s/decide" % approval["id"], {"approve": True}))
    ok(wholesale.post("/deals/%s/stage" % deal_id, {"stage": "offer_sent"}))
    contract_approval = ok(wholesale.post("/deals/%s/approvals" % deal_id,
                                          {"kind": "contract", "amount": mao}))
    ok(wholesale.post("/approvals/%s/decide" % contract_approval["id"],
                      {"approve": True}))
    ok(wholesale.patch("/deals/%s/contract" % deal_id, {
        "contract_price": mao, "contract_status": "signed",
        "contract_date": "2026-09-21", "seller_signed_at": "2026-09-21",
        "effective_date": "2026-09-22", "earnest_money": 2500,
        "earnest_money_due": "2026-09-24", "closing_deadline": "2026-11-14"}))
    ok(wholesale.post("/deals/%s/stage" % deal_id, {"stage": "under_contract"}))

    # The signed contract as an actual stored file, not a filename.
    uploaded = ok(wholesale.upload("/deals/%s/documents/upload" % deal_id, png,
                                   {"doc_type": "purchase_contract"}))
    assert uploaded["file"]["id"]
    # And it comes back only to somebody who is allowed to have it.
    assert wholesale.get("/files/%s" % uploaded["file"]["id"]).status_code == 200

    # ── DISPOSITION ─────────────────────────────────────────────────────────
    buyers = {}
    for key, name, close_days in (("fast", "Lone Star Capital", 10),
                                  ("slow", "Trinity Rentals", 30)):
        b = ok(wholesale.post("/buyers", {
            "company_name": name, "email": "%s@example.com" % key,
            "typical_close_days": close_days, "is_test": True}))
        ok(wholesale.post("/buyers/%s/buy-boxes" % b["id"], {
            "label": "DFW", "states": ["TX"], "counties": ["Dallas"],
            "property_types": ["single_family"],
            "min_price": 50000, "max_price": 400000}))
        buyers[key] = b

    matches = ok(wholesale.post("/deals/%s/match-buyers" % deal_id))["matches"]
    assert len(matches) >= 2
    ok(wholesale.post("/deals/%s/disposition" % deal_id, {
        "buyer_ids": [buyers["fast"]["id"], buyers["slow"]["id"]],
        "asking_price": mao + 20000}))

    board = ok(wholesale.get("/deals/%s/buyer-board" % deal_id))
    assert len(board["buyers"]) == 2
    assert board["contract_price"] == mao
    by_name = {r["buyer_name"]: r for r in board["buyers"]}

    # ── CASH BUYER ──────────────────────────────────────────────────────────
    # What a person heard, typed in. The slow buyer offers MORE.
    ok(wholesale.post("/outreach/%s/response" % by_name["Lone Star Capital"]["outreach_id"],
                      {"status": "offer_submitted", "offer_amount": mao + 20000,
                       "response_note": "Walked it Tuesday, cash",
                       "target_close_date": "2026-11-09"}))
    ok(wholesale.post("/outreach/%s/response" % by_name["Trinity Rentals"]["outreach_id"],
                      {"status": "offer_submitted", "offer_amount": mao + 26000,
                       "response_note": "Needs 30 days"}))
    # Proof of funds is a person's judgement, and only the fast buyer has it.
    ok(wholesale.upload(
        "/outreach/%s/proof-of-funds" % by_name["Lone Star Capital"]["outreach_id"],
        png))
    ok(wholesale.post("/outreach/%s/pof-status" % by_name["Lone Star Capital"]["outreach_id"],
                      {"status": "verified"}))

    board = ok(wholesale.get("/deals/%s/buyer-board" % deal_id))
    by_name = {r["buyer_name"]: r for r in board["buyers"]}
    assert by_name["Trinity Rentals"]["offer_amount"] > by_name["Lone Star Capital"]["offer_amount"]
    assert by_name["Lone Star Capital"]["pof_status"] == "verified"
    assert all(r["spread"] is not None for r in board["buyers"])
    # NOTHING auto-selected the higher number.
    assert board["selected_buyer_id"] is None
    assert not any(r["is_selected"] for r in board["buyers"])

    # A person picks the LOWER offer, because that buyer can actually pay.
    chosen = by_name["Lone Star Capital"]
    picked = ok(wholesale.post("/deals/%s/select-buyer" % deal_id,
                               {"outreach_id": chosen["outreach_id"],
                                "note": "Verified funds, closes in 10 days"}))
    assert picked["selected_buyer_id"] == buyers["fast"]["id"]
    assert picked["buyer_price"] == chosen["offer_amount"]
    assert picked["assignment_fee"] == chosen["offer_amount"] - mao

    # ── TITLE ───────────────────────────────────────────────────────────────
    assignment_approval = ok(wholesale.post("/deals/%s/approvals" % deal_id,
                                            {"kind": "assignment"}))
    ok(wholesale.post("/approvals/%s/decide" % assignment_approval["id"],
                      {"approve": True}))
    ok(wholesale.patch("/deals/%s/title" % deal_id, {
        "title_company": "Lone Star Title", "title_escrow_officer": "Ramona Vega",
        "title_phone": "2145550188", "title_status": "title_search",
        "title_issues": "Second lien from 2011, payoff requested",
        "closing_date": "2026-11-09", "closing_time": "10:30",
        "closing_location": "Oak Lawn"}))
    ok(wholesale.patch("/deals/%s/title" % deal_id, {"title_status": "clear_to_close"}))

    deal = ok(wholesale.get("/deals/%s" % deal_id))["deal"]
    assert deal["title_escrow_officer"] == "Ramona Vega"
    assert deal["closing_location"] == "Oak Lawn"

    # ── CLOSING AND THE FEE ─────────────────────────────────────────────────
    expected = picked["assignment_fee"]
    ok(wholesale.post("/deals/%s/close" % deal_id, {
        "wholesale_fee_collected": expected - 800,
        "closing_date": "2026-11-09", "deal_result": "closed_won",
        "note": "Title deducted an $800 lien payoff"}))

    deal = ok(wholesale.get("/deals/%s" % deal_id))["deal"]
    assert deal["stage"] == "closed"
    assert deal["wholesale_fee_collected"] == expected - 800
    assert deal["economics_locked"] is True

    # The lock is real: the money cannot be changed in passing.
    refused = wholesale.patch("/deals/%s/contract" % deal_id,
                              {"contract_price": 1})
    assert refused.status_code == 409
    assert "economics correction" in refused.text

    # A correction is possible, deliberate, and audited before-and-after.
    ok(wholesale.post("/deals/%s/economics-correction" % deal_id, {
        "reason": "Title deducted an $800 lien payoff at the table",
        "other_costs": 800}))

    room = ok(wholesale.get("/deals/%s" % deal_id))
    actions = [e["action"] for e in room["events"]]
    for expected_event in ("comp.excluded", "offer.recorded", "offer.countered",
                          "document.uploaded", "buyer.pof_status",
                          "buyer.selected", "deal.closed", "economics.corrected"):
        assert expected_event in actions, "missing audit event: %s" % expected_event

    # And the deal room says there is nothing left to do.
    assert room["next_action"]["label"] == "Closed"
