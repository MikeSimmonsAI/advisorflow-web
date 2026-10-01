"""S5 — WHOLESALE / EVOSENSE OPERATIONAL PROOF, through the real HTTP API.

One finding walked the whole way like a wholesaler would, every hop feeding the
next from the state the previous one actually left behind:

  discovery finding -> property -> owner -> contact (manual; no provider is
  configured, so no vendor call) -> qualification/conversation (a call the
  operator writes down) -> seller intent -> NEEDS YOU -> promote -> deal
  analyzer -> comps / ARV evidence (insufficient first, then enough) -> MAO ->
  human approval -> contract / documents -> buyer (a counterparty, not a Lead)
  -> match WITH reasons -> disposition -> assignment / title -> closing -> fee.

Plus tenant isolation and entitlement on a sample of the endpoints touched.

Regressions pinned here (both found by writing this walk):
  * a seller conversation typed in by hand 409'd ("No conversation is open")
    whenever platform outreach was BLOCKED - e.g. low contact confidence - so
    the manual path dead-ended exactly where a real operator picks up the phone;
  * the SELLER STATED facts from that conversation (asking price, timeline,
    occupancy) were dropped on promotion when the seller came from a manually
    entered contact, so the deal started blank.

Nothing here sends a message, calls a provider or an AI model.
"""
import json

import pytest

from app.models.models import Lead, Organization, User
from app.services.auth_service import create_access_token, hash_password

ES = "/wholesale/evosense"


def ok(response, what=""):
    assert response.status_code in (200, 201), \
        "%s -> %s %s" % (what or response.url, response.status_code, response.text[:400])
    return response.json()


@pytest.fixture()
def other_headers(db_session):
    org = Organization(name="Other Wholesaler", slug="other-wholesaler-s5",
                       plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    user = User(organization_id=org.id, email="other@s5.test",
                password_hash=hash_password("TestPass123!"), full_name="Other Operator",
                role="org_admin", must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(user, db_session)}


def _finding(client, H, street="4410 Bluebonnet Ln"):
    out = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": street, "city": "Dallas", "state": "TX", "zip_code": "75216",
        "county": "Dallas", "property_type": "single_family", "square_feet": 1400,
        "estimated_value": 210000, "signals": ["VACANT", "TAX_DELINQUENT"],
        "is_test": True}), "discovery finding")
    assert out["outcome"] == "created" and out["property_id"]
    return out["property_id"]


def test_finding_to_fee_through_the_api(client, auth_headers, db_session, sample_org):
    H = auth_headers

    # ── DISCOVERY: a finding exists and is not a dead end ─────────────────
    pid = _finding(client, H)
    detail = ok(client.get(ES + "/properties/%s" % pid, headers=H))
    assert detail["actions"], "a finding must offer operational next steps"
    assert detail["next"] and detail["next"].get("key")
    # SCORE DOMAINS stay distinct; no single blended number.
    scores = detail["scores"]
    assert "property_opportunity" in scores
    assert scores["property_opportunity"]["factors"], "score without contributing signals"
    assert detail["property"]["contact_confidence"] is None   # no contact yet
    assert detail["property"]["seller_intent"] is None        # nobody has spoken
    # Occupancy is not a scored factor.
    labels = " ".join(f["label"].lower()
                      for f in scores["property_opportunity"]["factors"])
    assert "occupan" not in labels
    # A modelled market estimate is NOT an ARV.
    assert detail["facts"]["arv"]["value"] is None
    assert detail["facts"]["arv"]["status"] == "insufficient"

    # ── OWNER ────────────────────────────────────────────────────────────
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={
        "name": "Ruth Delacroix", "mailing_street": "88 Elm St", "mailing_city": "Plano",
        "mailing_state": "TX", "mailing_zip": "75023"}), "owner")

    # ── ENRICHMENT: not configured -> truthful refusal, no vendor call ────
    enr = ok(client.post(ES + "/properties/%s/enrich" % pid, headers=H,
                         json={"approved": True}), "enrich")
    assert enr["decision"] != "PURCHASE" and "manual" in " ".join(enr["reasons"]).lower()
    enrich_action = [a for a in ok(client.get(ES + "/properties/%s" % pid, headers=H))["actions"]
                     if a["key"] == "run_enrichment"][0]
    assert enrich_action["enabled"] is False and "NOT CONFIGURED" in enrich_action["reason_if_disabled"]
    ledger = ok(client.get("/wholesale/ops/skip-trace", headers=H))["ledger"]
    assert ledger["total_charged_cents"] == 0 and ledger["attempts"] == 0

    # ── CONTACT, entered by hand ─────────────────────────────────────────
    ok(client.post(ES + "/properties/%s/contacts" % pid, headers=H, json={
        "kind": "phone", "value": "2145550142", "person_name": "Ruth Delacroix"}), "contact")
    after_contact = ok(client.get(ES + "/properties/%s" % pid, headers=H))["property"]
    assert after_contact["contact_confidence"] is not None
    assert after_contact["seller_intent"] is None   # contact is not intent

    # One sold comp only: ARV must still say insufficient.
    one = ok(client.post(ES + "/properties/%s/comps" % pid, headers=H, json={
        "street_address": "4500 Bluebonnet Ln", "sale_price": 205000, "sale_date": "2026-07-01",
        "source_reference": "county deed 2026-111", "square_feet": 1380,
        "property_type": "single_family", "distance_miles": 0.3, "sale_type": "standard"}))
    assert one["arv"]["value"] is None and one["arv"]["status"] == "insufficient"

    # Outreach is refused (low contact confidence, no consent) - nothing sent.
    out = ok(client.post(ES + "/properties/%s/outreach" % pid, headers=H))
    assert out["started"] is False

    # ── CONVERSATION / QUALIFICATION: the operator phoned and writes it down
    # REGRESSION: this used to 409 because the engagement was "blocked".
    reply = ok(client.post(ES + "/properties/%s/reply" % pid, headers=H, json={
        "text": "Yes I'd sell, the house is empty since mom passed last year. "
                "I want around $150,000 and need to close fast.",
        "delivery": "manual_entry"}), "manual seller conversation")
    assert reply["outcome"] == "INTERESTED"
    facts = {f["fact_type"]: f for f in reply["facts"]}
    assert facts["asking_price"]["value"] == "150000" and facts["asking_price"]["quote"]

    # ── SELLER INTENT: its own domain, from seller-stated facts ──────────
    d = ok(client.get(ES + "/properties/%s" % pid, headers=H))
    assert d["property"]["seller_intent"] is not None
    assert d["scores"]["seller_intent"]["factors"]
    assert d["property"]["seller_intent"] != d["property"]["opportunity_score"] or \
        d["property"]["seller_intent"] != d["property"]["contact_confidence"]

    # ── NEEDS YOU: the hot seller response is a judgment item ─────────────
    ny = ok(client.get("/wholesale/needs-you", headers=H, params={"include_test": True}))
    hot = [i for i in ny["items"] if i.get("evosense_property_id") == pid]
    assert hot and hot[0]["why"] and hot[0]["link"], "hot seller reply missing from NEEDS YOU"
    kinds_before = {i["kind"] for i in ny["items"]}

    # ── PROMOTE into deal operations ──────────────────────────────────────
    leads_before = db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count()
    pr = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}), "promote")
    deal_id = pr["deal_id"]
    assert deal_id and pr["property_id"]
    again = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))
    assert again["already"] and again["deal_id"] == deal_id      # idempotent
    room = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))
    seller = room["seller"]
    assert seller["first_name"] == "Ruth" and seller["lead_id"]
    # REGRESSION: the hand-recorded conversation's facts travel to the deal.
    assert seller["asking_price"] == 150000 and seller["asking_price_source"] == "manual"
    assert seller["considering_selling"] is True and seller["timeline"] == "asap"
    assert seller["occupancy"] == "vacant"
    assert seller["sms_consent"] is False      # a typed number is not permission
    assert room["deal"]["stage"] == "seller_engaged"
    # The handoff left NEEDS YOU when a person acted on it.
    ny = ok(client.get("/wholesale/needs-you", headers=H, params={"include_test": True}))
    assert not [i for i in ny["items"] if i.get("evosense_property_id") == pid]

    # ── DEAL ANALYZER: the one carried comp is evidence, not an ARV ───────
    assert len(room["comps"]) == 1 and room["comps"][0]["origin"] == "MANUAL"
    an = room["analysis"]
    assert an["arv"] is None and an["max_allowable_offer"] is None
    assert an["mao_gate"]["status"] == "NOT_CALCULATED"
    assert any("insufficient" in r.lower() for r in an["mao_gate"]["reasons"])

    # ── COMPS -> ARV EVIDENCE -> MAO ──────────────────────────────────────
    for addr, price, sqft in (("4512 Bluebonnet Ln", 212000, 1420),
                              ("4300 Bluebonnet Ln", 209000, 1395)):
        ok(client.post("/wholesale/deals/%s/comps" % deal_id, headers=H, json={
            "street_address": addr, "sale_price": price, "square_feet": sqft,
            "sale_date": "2026-08-01", "distance_miles": 0.4,
            "property_type": "single_family", "sale_type": "standard",
            "source_reference": "county deed %s" % addr}), "comp " + addr)
    an = ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=H,
                         json={"repair_estimate": 35000}), "repairs")
    room = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))
    for c in room["comps"]:
        assert c["origin"] == "MANUAL" and c["verification_state"] == "manual"
    arv = room["analysis"]["arv"]
    mao = room["analysis"]["max_allowable_offer"]
    if arv is None:
        # Three manual comps are still not a verified ARV under the policy:
        # the operator states one, and it is labelled MANUAL, never "sold comp".
        ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=H,
                        json={"arv": 210000}))
        room = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))
        arv = room["analysis"]["arv"]
        assert room["analysis"]["arv_source"] == "manual"
        mao = room["analysis"]["max_allowable_offer"]
    assert arv and mao and 0 < mao < arv - 35000, "MAO must be computed below ARV - repairs"
    # An ARV derived from comps is an ESTIMATE from manual comp evidence -
    # never labelled verified, never presented as a sold comp itself.
    assert room["analysis"]["arv_source"] in ("estimated", "manual")
    assert room["analysis"]["arv_source"] != "verified"

    # ── HUMAN APPROVAL: a pending approval is a NEEDS YOU item ───────────
    appr = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=H,
                          json={"kind": "offer", "amount": mao}), "ask offer approval")
    ny = ok(client.get("/wholesale/needs-you", headers=H, params={"include_test": True}))
    assert any(i.get("deal_id") == deal_id or deal_id in json.dumps(i) for i in ny["items"]), \
        "a pending approval must surface in NEEDS YOU"
    ny_kinds = {i["kind"] for i in ny["items"] if deal_id in json.dumps(i)}
    assert "approval" in ny_kinds
    ok(client.post("/wholesale/approvals/%s/decide" % appr["id"], headers=H,
                   json={"approve": True}), "approve offer")
    ok(client.post("/wholesale/deals/%s/offers" % deal_id, headers=H,
                   json={"amount": mao, "direction": "us"}), "our offer")

    # ── CONTRACT / DOCUMENTS ──────────────────────────────────────────────
    price = round(min(mao, 140000))
    ok(client.patch("/wholesale/deals/%s/contract" % deal_id, headers=H, json={
        "contract_price": price, "contract_status": "signed", "contract_date": "2026-09-20",
        "inspection_deadline": "2026-10-06"}), "contract")
    blocked = client.post("/wholesale/deals/%s/stage" % deal_id, headers=H,
                          json={"stage": "under_contract"})
    assert blocked.status_code == 409          # the human gate is real
    ca = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=H,
                        json={"kind": "contract", "amount": price}))
    ok(client.post("/wholesale/approvals/%s/decide" % ca["id"], headers=H, json={"approve": True}))
    ok(client.post("/wholesale/deals/%s/stage" % deal_id, headers=H,
                   json={"stage": "under_contract"}), "under contract")
    sheet = ok(client.get("/wholesale/deals/%s/fill-sheet" % deal_id, headers=H))
    assert sheet["summary"]["total"] > 0 and sheet["missing"]

    # ── BUYER: a counterparty, not a seller Lead ─────────────────────────
    leads_mid = db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count()
    buyer = ok(client.post("/wholesale/buyers", headers=H, json={
        "company_name": "Trinity Flip Co", "contact_name": "Dee Park",
        "email": "dee@trinityflip.example.com", "typical_close_days": 14, "is_test": True}))
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % buyer["id"], headers=H, json={
        "label": "South Dallas", "states": ["TX"], "min_price": 50000, "max_price": 250000}))
    assert db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count() == leads_mid, \
        "creating a buyer must not create a seller Lead"
    assert leads_mid >= leads_before

    # ── MATCH, with reasons ───────────────────────────────────────────────
    ok(client.post("/wholesale/deals/%s/match-buyers" % deal_id, headers=H, json={}))
    matches = ok(client.get("/wholesale/deals/%s/matches" % deal_id, headers=H))["matches"]
    mine = [m for m in matches if m.get("buyer_id") == buyer["id"]
            or (m.get("buyer") or {}).get("id") == buyer["id"]]
    assert mine, "the buyer whose box fits was not matched"
    assert mine[0].get("factors"), "a match must say WHY it matched"

    # ── DISPOSITION -> ASSIGNMENT / TITLE -> CLOSING -> FEE ───────────────
    ok(client.post("/wholesale/deals/%s/disposition" % deal_id, headers=H,
                   json={"buyer_ids": [buyer["id"]], "channel": "email"}), "disposition")
    board = ok(client.get("/wholesale/deals/%s/buyer-board" % deal_id, headers=H))
    row = [b for b in board["buyers"] if b["buyer_id"] == buyer["id"]][0]
    ok(client.post("/wholesale/deals/%s/select-buyer" % deal_id, headers=H,
                   json={"outreach_id": row["outreach_id"]}), "select buyer")
    fee = 12000
    ok(client.post("/wholesale/deals/%s/assign" % deal_id, headers=H, json={
        "buyer_id": buyer["id"], "buyer_price": price + fee, "assignment_fee": fee}), "assign")
    aa = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=H,
                        json={"kind": "assignment", "amount": fee}))
    ok(client.post("/wholesale/approvals/%s/decide" % aa["id"], headers=H, json={"approve": True}))
    ok(client.patch("/wholesale/deals/%s/title" % deal_id, headers=H, json={
        "title_company": "Fixture Title Co", "title_status": "clear",
        "title_file_number": "FX-1"}), "title")
    ok(client.post("/wholesale/deals/%s/close" % deal_id, headers=H,
                   json={"closing_date": "2026-11-08"}), "close")
    closed = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))["deal"]
    assert closed["payment_state"] == "payment_pending"           # closed is not paid
    ok(client.post("/wholesale/deals/%s/fee-collected" % deal_id, headers=H, json={
        "amount": fee, "method": "wire", "reference": "WIRE-FX-1",
        "collected_at": "2026-11-09"}), "fee collected")
    paid = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))["deal"]
    assert paid["payment_state"] == "fee_collected"
    assert float(paid["wholesale_fee_collected"]) == fee

    # The EvoSense record still points at the deal it became.
    assert ok(client.get(ES + "/properties/%s" % pid, headers=H))["property"]["promoted_deal_id"] == deal_id
    assert kinds_before  # sanity: the feed carried at least the hot reply


def test_manual_conversation_without_an_owner_still_refuses(client, auth_headers):
    """The dead-end fix does not invent a conversation: with no owner of record
    there is nobody to have spoken to."""
    pid = _finding(client, auth_headers, "77 Nobody St")
    r = client.post(ES + "/properties/%s/reply" % pid, headers=auth_headers,
                    json={"text": "Yes I'd sell", "delivery": "manual_entry"})
    assert r.status_code == 409


def test_needs_you_has_no_routine_work(client, auth_headers):
    """A fresh finding with nothing to decide is not a NEEDS YOU item."""
    pid = _finding(client, auth_headers, "12 Routine Rd")
    ny = ok(client.get("/wholesale/needs-you", headers=auth_headers, params={"include_test": True}))
    assert not [i for i in ny["items"] if i.get("evosense_property_id") == pid]


def test_tenant_isolation_on_the_path(client, auth_headers, other_headers):
    H = auth_headers
    pid = _finding(client, H, "900 Tenant Ave")
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Tess Owner"}))
    ok(client.post(ES + "/properties/%s/contacts" % pid, headers=H,
                   json={"kind": "phone", "value": "2145550177"}))
    ok(client.post(ES + "/properties/%s/reply" % pid, headers=H,
                   json={"text": "Yes I want to sell for 120,000", "delivery": "manual_entry"}))
    deal_id = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))["deal_id"]

    for method, path, body in (
            ("get", ES + "/properties/%s" % pid, None),
            ("post", ES + "/properties/%s/reply" % pid, {"text": "x", "delivery": "manual_entry"}),
            ("post", ES + "/properties/%s/contacts" % pid, {"kind": "phone", "value": "2145550199"}),
            ("post", ES + "/properties/%s/promote" % pid, {}),
            ("get", "/wholesale/deals/%s" % deal_id, None),
            ("patch", "/wholesale/deals/%s/analysis" % deal_id, {"arv": 1}),
            ("post", "/wholesale/deals/%s/approvals" % deal_id, {"kind": "offer", "amount": 1}),
            ("post", "/wholesale/deals/%s/match-buyers" % deal_id, {})):
        kw = {"headers": other_headers}
        if body is not None:
            kw["json"] = body
        r = getattr(client, method)(path, **kw)
        assert r.status_code in (403, 404), "%s %s -> %s" % (method, path, r.status_code)

    feed = ok(client.get("/wholesale/needs-you", headers=other_headers,
                         params={"include_test": True}))
    assert pid not in json.dumps(feed) and deal_id not in json.dumps(feed)
    inbox = ok(client.get(ES + "/inbox", headers=other_headers))
    assert pid not in json.dumps(inbox)


def test_entitlement_is_enforced_on_the_path(client, auth_headers, db_session, sample_org):
    sample_org.enabled_features = json.dumps(["leads"])
    db_session.commit()
    for method, path in (("get", "/wholesale/needs-you"),
                         ("get", ES + "/inbox"),
                         ("post", ES + "/properties"),
                         ("get", "/wholesale/buyers")):
        kw = {"headers": auth_headers}
        if method == "post":
            kw["json"] = {"street_address": "1 X St", "zip_code": "75201"}
        r = getattr(client, method)(path, **kw)
        assert r.status_code == 402, "%s %s -> %s" % (method, path, r.status_code)
        assert "wholesale_real_estate" in r.json()["detail"]
