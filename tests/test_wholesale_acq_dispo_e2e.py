"""ORIGINAL ACQUISITIONS + NEW DEAL INTELLIGENCE / DISPOSITION, together, through the API.

The preservation rule for the Deal Run work: every new feature plugs into the
existing workflow and nothing in it is removed, bypassed or degraded. This walk
runs the original acquisitions path (finding -> owner -> contact -> seller
conversation typed by the operator -> NEEDS YOU -> promote) and then, on the SAME
deal, the new Deal analysis and buyer intelligence, and finishes through the
existing approval, contract, disposition, assignment, closing and fee path.
At each hop it checks the original behaviour still holds.

Nothing here sends a message, calls a provider or an AI model.
"""
import io

from app.models.models import Lead

ES = "/wholesale/evosense"


def ok(r, what=""):
    assert r.status_code in (200, 201), "%s -> %s %s" % (what or r.url, r.status_code, r.text[:400])
    return r.json()


COMPS = ("address,sale_price,sale_date,sqft,beds,baths,distance_miles,mls_number\n"
         "4512 Pecan Ln,212000,2026-08-01,1420,3,2,0.4,deed-4512\n"
         "4300 Pecan Ln,209000,2026-08-01,1395,3,2,0.4,deed-4300\n"
         "4600 Pecan Ln,207000,2026-07-15,1400,3,2,0.5,deed-4600\n")


def test_acquisition_then_analysis_then_disposition(client, auth_headers, db_session, sample_org,
                                                    admin_auth_headers):
    H = auth_headers

    # ── ORIGINAL ACQUISITIONS PATH ───────────────────────────────────────
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": "4410 Pecan Ln", "city": "Dallas", "state": "TX", "zip_code": "75216",
        "county": "Dallas", "property_type": "single_family", "square_feet": 1400,
        "signals": ["VACANT", "TAX_DELINQUENT"], "is_test": True}))["property_id"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Test Owner Pecan"}))
    ok(client.post(ES + "/properties/%s/contacts" % pid, headers=H, json={
        "kind": "phone", "value": "2145550199", "person_name": "Test Owner Pecan"}))
    out = ok(client.post(ES + "/properties/%s/outreach" % pid, headers=H))
    assert out["started"] is False                     # consent rules still refuse, nothing sent
    reply = ok(client.post(ES + "/properties/%s/reply" % pid, headers=H, json={
        "text": "Yes I'd sell, it's empty. I want around $150,000 and need to close fast.",
        "delivery": "manual_entry"}))
    assert reply["outcome"] == "INTERESTED"
    ny = ok(client.get("/wholesale/needs-you", headers=H, params={"include_test": True}))
    assert [i for i in ny["items"] if i.get("evosense_property_id") == pid]
    deal_id = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))["deal_id"]
    room = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))
    assert room["seller"]["asking_price"] == 150000 and room["seller"]["sms_consent"] is False
    assert room["deal"]["stage"] == "seller_engaged"

    # ── NEW: DEAL ANALYSIS on the promoted deal ─────────────────────────
    uw = ok(client.get("/wholesale/deals/%s/underwriting" % deal_id, headers=H))
    assert uw["offer"]["mao"] is None and uw["missing"] and uw["offer"]["can_request_approval"] is False
    assert uw["profile"]["evosense_property_id"] == pid           # linked to the acquisition record
    up = ok(client.post("/wholesale/deals/%s/comps/import" % deal_id, headers=H,
                        files={"file": ("c.csv", io.BytesIO(COMPS.encode()), "text/csv")}))
    assert up["added"] == 3
    ok(client.post("/wholesale/deals/%s/repairs/itemized" % deal_id, headers=H, json={
        "items": [{"category": "Roof", "item": "Roof", "quantity": 1, "unit_cost": 15000},
                  {"category": "Kitchen", "item": "Cabinets", "quantity": 1, "unit_cost": 15000}],
        "contingency_pct": 10}))
    run = ok(client.post("/wholesale/deals/%s/underwriting" % deal_id, headers=H, json={"assumptions": {}}))
    if run["arv"]["value"] is None:
        ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=H, json={"arv": 210000}))
        run = ok(client.post("/wholesale/deals/%s/underwriting" % deal_id, headers=H, json={"assumptions": {}}))
    mao = run["offer"]["mao"]
    assert mao and run["offer"]["can_request_approval"]
    # the original analyzer and the new screen agree - one MAO, not two
    room = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))
    assert room["analysis"]["max_allowable_offer"] == mao
    assert room["analysis"]["arv_source"] != "verified"

    # ── ORIGINAL APPROVAL / OFFER / CONTRACT (the new screen does not bypass it)
    appr = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=H,
                          json={"kind": "offer", "amount": run["offer"]["range_high"]}))
    ny = ok(client.get("/wholesale/needs-you", headers=H, params={"include_test": True}))
    assert any(deal_id in str(i) and i["kind"] == "approval" for i in ny["items"])
    ok(client.post("/wholesale/approvals/%s/decide" % appr["id"], headers=H, json={"approve": True}))
    price = round(min(mao, 140000))
    ok(client.patch("/wholesale/deals/%s/contract" % deal_id, headers=H, json={
        "contract_price": price, "contract_status": "signed", "contract_date": "2026-09-20"}))
    assert client.post("/wholesale/deals/%s/stage" % deal_id, headers=H,
                       json={"stage": "under_contract"}).status_code == 409   # human gate intact
    ca = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=H, json={"kind": "contract", "amount": price}))
    ok(client.post("/wholesale/approvals/%s/decide" % ca["id"], headers=H, json={"approve": True}))
    ok(client.post("/wholesale/deals/%s/stage" % deal_id, headers=H, json={"stage": "under_contract"}))

    # ── BUYERS: existing CRM, new intelligence on top ───────────────────
    leads_before = db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count()
    claimed = ok(client.post("/wholesale/buyers", headers=H, json={
        "company_name": "Claims A Lot LLC", "email": "claims@example.test", "past_deals_count": 50, "is_test": True}))
    real = ok(client.post("/wholesale/buyers", headers=H, json={
        "company_name": "Pecan Flip Co", "email": "pecan@example.test", "is_test": True}))
    for b in (claimed, real):
        ok(client.post("/wholesale/buyers/%s/buy-boxes" % b["id"], headers=H, json={
            "label": "South Dallas", "states": ["TX"], "min_price": 50000, "max_price": 250000}))
    assert db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count() == leads_before
    intel = ok(client.get("/wholesale/deals/%s/buyer-intel" % deal_id, headers=H))
    tiers = {x["buyer_id"]: x["tier"]["tier"] for x in intel["buyers"]}
    assert tiers[claimed["id"]] == "UNVERIFIED_CONTACT"            # 50 typed deals count for nothing
    assert all(x["explain"] and x["fit_factors"] for x in intel["buyers"])

    # ── NEW: INVESTOR DEAL PACKAGE, approved through the Approval Center ─
    from tests.test_wholesale_deal_package import _photo
    ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={
        "buyer_room_summary": "Vacant 3/2, roof and kitchen.", "buyer_room_asking_price": price + 12000}))
    _photo(db_session, deal_id)
    pk = ok(client.post("/wholesale/deals/%s/package/submit" % deal_id, headers=H))
    assert pk["status"] == "review"
    ok(client.post("/wholesale/approvals/%s/decide" % pk["approval"]["id"], headers=admin_auth_headers,
                   json={"approve": True}))
    assert ok(client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=admin_auth_headers))["status"] == "published"

    # ── ORIGINAL MATCH + DISPOSITION ────────────────────────────────────
    ok(client.post("/wholesale/deals/%s/match-buyers" % deal_id, headers=H, json={}))
    assert ok(client.get("/wholesale/deals/%s/matches" % deal_id, headers=H))["matches"]
    ok(client.post("/wholesale/deals/%s/disposition" % deal_id, headers=H,
                   json={"buyer_ids": [real["id"]], "channel": "email"}))
    board = ok(client.get("/wholesale/deals/%s/buyer-board" % deal_id, headers=H))
    row = [b for b in board["buyers"] if b["buyer_id"] == real["id"]][0]
    # the buyer phones back with an offer - a person records it
    ok(client.post("/wholesale/outreach/%s/response" % row["outreach_id"], headers=H,
                   json={"status": "offer_submitted", "offer_amount": price + 12000, "response_note": "by phone"}))
    assert ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))["status"] == "under_offer"
    ds = ok(client.get("/wholesale/deals/%s/disposition-summary" % deal_id, headers=H))
    f = {x["key"]: x["count"] for x in ds["funnel"]}
    assert f["offers"] == 1 and f["opened"] == 0                    # no tracked open, so none claimed
    assert ds["offers"][0]["net"] == 12000 and ds["offers"][0]["buyer"] == "Pecan Flip Co"
    assert any("proof of funds" in x["why"] for x in ds["follow_ups"])
    assert ds["closing_checklist"]["next"] == "Buyer selected"
    learned = ok(client.get("/wholesale/buyers/%s/intel" % real["id"], headers=H))
    assert learned["tier"]["tier"] == "VERIFIED_ACTIVE_BUYER"      # an offer + a reply, both real
    assert learned["learned"]["zips"] == {"75216": 1}
    ok(client.post("/wholesale/deals/%s/select-buyer" % deal_id, headers=H, json={"outreach_id": row["outreach_id"]}))
    fee = 12000
    ok(client.post("/wholesale/deals/%s/assign" % deal_id, headers=H, json={
        "buyer_id": real["id"], "buyer_price": price + fee, "assignment_fee": fee}))
    aa = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=H, json={"kind": "assignment", "amount": fee}))
    ok(client.post("/wholesale/approvals/%s/decide" % aa["id"], headers=H, json={"approve": True}))
    ok(client.patch("/wholesale/deals/%s/title" % deal_id, headers=H, json={
        "title_company": "Fixture Title Co", "title_status": "clear", "title_file_number": "FX-2"}))
    ok(client.post("/wholesale/deals/%s/close" % deal_id, headers=H, json={"closing_date": "2026-11-08"}))
    ok(client.post("/wholesale/deals/%s/fee-collected" % deal_id, headers=H, json={
        "amount": fee, "method": "wire", "reference": "WIRE-FX-2", "collected_at": "2026-11-09"}))
    paid = ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))["deal"]
    assert paid["payment_state"] == "fee_collected"
    assert ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))["status"] == "closed"
    ck = ok(client.get("/wholesale/deals/%s/disposition-summary" % deal_id, headers=H))["closing_checklist"]
    done = {x["label"]: x["done"] for x in ck["steps"]}
    assert done["Closed"] and done["Fee collected"] and done["Assignment approved"] and done["Deal package approved"]
    an = ok(client.get("/wholesale/disposition/analytics", headers=H, params={"include_test": True}))
    assert an["deals_closed"] >= 1 and an["offers"] >= 1 and an["deal_sheets_sent"] >= 0
    real_only = ok(client.get("/wholesale/disposition/analytics", headers=H))
    assert real_only["deals_closed"] == 0                           # a test deal never counts as real business

    # the workflow strip sees the whole path, and management reporting still loads
    steps = {w["key"]: w["state"] for w in ok(client.get("/wholesale/deals/%s/underwriting" % deal_id, headers=H))["workflow"]}
    assert steps["analyze"] == "done" and steps["repairs"] == "done" and steps["offer"] == "done"
    ok(client.get("/wholesale/dashboard", headers=H))
    assert ok(client.get(ES + "/properties/%s" % pid, headers=H))["property"]["promoted_deal_id"] == deal_id
