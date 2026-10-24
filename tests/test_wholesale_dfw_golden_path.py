"""DFW GOLDEN PATH - the whole operating workflow, one test, through the real API.

Original acquisitions (discovery -> evidence -> owner -> enrichment -> outreach rules
-> seller conversation -> qualification -> NEEDS YOU -> appointment -> rep ->
inspection -> photos) feeding underwriting (comps -> repairs -> MAO -> offer ->
approval -> contract) and disposition (buyer import -> shortlist -> package ->
approval -> publish -> engagement -> buyer offer -> comparison -> human selection
-> assignment -> closing -> fee -> reporting), all on ONE deal, with no duplicate
records. Then the negative cases.

Synthetic records only (is_test, example.test, 555 numbers). External services are
fakes: no provider is called, nothing is sent, nothing is bought.
"""
import io
import json

import pytest

from app.models.models import Lead
from app.models.wholesale_models import WholesaleBuyer, WholesaleDeal, WholesaleProperty

ES = "/wholesale/evosense"
STEPS = []


def ok(r, step=""):
    assert r.status_code in (200, 201), "%s -> %s %s" % (step or r.url, r.status_code, r.text[:400])
    return r.json()


def step(n, name):
    STEPS.append((n, name))


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WHOLESALE_LOCAL_MEDIA_ROOT", str(tmp_path / "media"))


def _jpeg(seed):
    from tests.test_wholesale_photo_repairs import jpeg
    return jpeg(seed=seed)


def test_dfw_golden_path(client, auth_headers, admin_auth_headers, db_session, sample_org, second_advisor,
                         storage, monkeypatch):
    H, A = auth_headers, admin_auth_headers
    STEPS.clear()

    # 1. discover a distressed DFW property
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": "4410 Golden Path Ln", "city": "Dallas", "state": "TX", "zip_code": "75216",
        "county": "Dallas", "property_type": "single_family", "square_feet": 1450, "year_built": 1984,
        "signals": ["VACANT", "TAX_DELINQUENT"], "is_test": True}), "discover")["property_id"]
    step(1, "discovered")
    # 2. evidence: scored, nothing invented (an estimate is not an ARV)
    d = ok(client.get(ES + "/properties/%s" % pid, headers=H))
    assert d["scores"]["property_opportunity"]["factors"] and d["facts"]["arv"]["value"] is None
    step(2, "evidence shown, no invented ARV")
    # 3. ownership
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={
        "name": "GOLDEN TEST OWNER", "mailing_street": "1 Elsewhere Rd", "mailing_city": "Plano",
        "mailing_state": "TX", "mailing_zip": "75023"}))
    step(3, "owner of record")
    # 4. enrichment requested: no paid provider is configured -> truthful refusal, $0, status reported
    enr = ok(client.post(ES + "/properties/%s/enrich" % pid, headers=A, json={"approved": True}))
    assert enr["decision"] != "PURCHASE"
    assert ok(client.get("/wholesale/ops/skip-trace", headers=H))["ledger"]["total_charged_cents"] == 0
    st = ok(client.get(ES + "/properties/%s/enrichment-status" % pid, headers=H))
    assert st["status"] in ("NOT_REQUESTED", "NO_VERIFIED_MATCH", "FAILED", "PROCESSING")
    ok(client.post(ES + "/properties/%s/contacts" % pid, headers=H, json={
        "kind": "phone", "value": "2145550150", "person_name": "Golden Test Owner"}))
    step(4, "enrichment requested; contact entered by a person")
    # 5. outreach eligibility enforced (no consent, low confidence) - nothing sent
    assert ok(client.post(ES + "/properties/%s/outreach" % pid, headers=H))["started"] is False
    step(5, "outreach refused without permission")
    # 6-8. seller conversation, kept, qualified
    reply = ok(client.post(ES + "/properties/%s/reply" % pid, headers=H, json={
        "text": "Yes I'd sell, it's been empty a year. Around $150,000 and I need to close fast.",
        "delivery": "manual_entry"}))
    assert reply["outcome"] == "INTERESTED"
    d = ok(client.get(ES + "/properties/%s" % pid, headers=H))
    convo = json.dumps(d.get("engagements") or d)
    assert "150,000" in convo or "150000" in convo
    assert d["property"]["seller_intent"] is not None
    step(6, "seller conversation"); step(7, "conversation kept"); step(8, "motivation qualified")
    # 9. human attention
    ny = ok(client.get("/wholesale/needs-you", headers=H, params={"include_test": True}))
    assert [i for i in ny["items"] if i.get("evosense_property_id") == pid]
    step(9, "NEEDS YOU")
    deal_id = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))["deal_id"]
    deal = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    # 10-11. appointment scheduled and assigned to a rep
    appt = ok(client.post("/wholesale/appointments", headers=A, json={
        "starts_at": "2026-11-02T10:00:00-06:00", "evosense_property_id": pid, "deal_id": deal_id,
        "assigned_to_id": second_advisor.id}), "appointment")
    assert appt["assigned_to_id"] == second_advisor.id
    step(10, "appointment"); step(11, "rep assigned")
    # 12. inspection details
    v = ok(client.put("/wholesale/appointments/%s/visit" % appt["id"], headers=A, json={
        "interview": {"motivation": "Inherited, lives out of state", "timeline": "30_days", "occupancy": "vacant"},
        "repairs": {"condition": "poor", "items": {"Roof": {"needed": True, "amount": 12000}}}}))
    assert v["repairs"]["total"] == 12000
    step(12, "inspection notes")
    # 13. photos on the property
    for i in range(2):
        ok(client.post("/wholesale/properties/%s/photos" % deal.property_id, headers=H,
                       files={"file": ("g%d.jpg" % i, io.BytesIO(_jpeg(i + 3)), "image/jpeg")},
                       data={"category": "exterior_front", "buyer_visible": "true"}))
    step(13, "photos")
    # 14. comps: the honest gap first, then sourced prices from a person
    from app.models.evosense_models import EvoSenseProperty
    from datetime import date, timedelta
    for i, (sqft, days) in enumerate(((1430, 40), (1500, 80), (1470, 120))):
        db_session.add(EvoSenseProperty(organization_id=sample_org.id, street_address="%d Golden Path Ln" % (4420 + i * 10),
                                        city="Dallas", state="TX", zip_code="75216", county="Dallas",
                                        square_feet=sqft, year_built=1985, property_type="single_family", is_test=True,
                                        last_deed_transfer_date=date.today() - timedelta(days=days)))
    db_session.commit()
    found = ok(client.post("/wholesale/deals/%s/comps/find" % deal_id, headers=H, json={}))
    assert found["counts"]["closed_sales_usable"] == 0 and "Texas does not publish" in found["gap"]
    for c, price in zip([c for c in found["candidates"] if c["needs_price"]][:3], (205000, 212000, 209000)):
        ok(client.post("/wholesale/deals/%s/comps/find/add" % deal_id, headers=H,
                       json={"key": c["key"], "sale_price": price, "source_reference": "MLS GP-%d" % price}))
    step(14, "comps: gap explained, sourced prices added")
    # 15. repairs from the photos (fake vision client), reviewed and saved by a person
    from app.routers import wholesale_underwriting_router as R
    from tests.test_wholesale_photo_repairs import AI, FakeClient
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(R, "_vision_client", lambda: FakeClient(AI))
    draft = ok(client.post("/wholesale/deals/%s/repairs/photo-analysis" % deal_id, headers=H, json={}))
    assert draft["ai"]["used"] and draft["lines"]
    ok(client.post("/wholesale/deals/%s/repairs/itemized" % deal_id, headers=H, json={
        "items": [{"category": l["category"], "item": l["item"], "quantity": l["quantity"], "unit": l["unit"],
                   "unit_cost": l["unit_cost"]} for l in draft["lines"]], "basis": "photo", "contingency_pct": 10}))
    step(15, "repair estimate")
    # 16. MAO
    run = ok(client.post("/wholesale/deals/%s/underwriting" % deal_id, headers=H, json={"assumptions": {}}))
    if run["offer"]["mao"] is None:
        # three sourced sales but without map positions the ARV is LOW confidence and this
        # workspace requires medium: the system refuses a MAO until a person states the ARV
        assert any("confidence" in m.lower() or "arv" in m.lower() for m in run["missing"])
        ok(client.patch("/wholesale/deals/%s/analysis" % deal_id, headers=H, json={"arv": 209000}))
        run = ok(client.post("/wholesale/deals/%s/underwriting" % deal_id, headers=H, json={"assumptions": {}}))
    mao = run["offer"]["mao"]
    assert mao and run["offer"]["can_request_approval"], (run["offer"], run["arv"], run["repairs"], run["missing"])
    step(16, "MAO")
    # 17-18. offer, approval
    appr = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=H, json={"kind": "offer", "amount": mao}))
    ok(client.post("/wholesale/approvals/%s/decide" % appr["id"], headers=A, json={"approve": True}))
    ok(client.post("/wholesale/deals/%s/offers" % deal_id, headers=H, json={"amount": mao, "direction": "us"}))
    step(17, "offer"); step(18, "offer approved")
    # 19. contract generated (filled document), signed, approved
    pv = ok(client.post("/wholesale/deals/%s/contracts/purchase_agreement/preview" % deal_id, headers=H, json={}))
    assert pv["html"] and "4410" in pv["html"]
    price = round(min(mao, 140000))
    ok(client.patch("/wholesale/deals/%s/contract" % deal_id, headers=H, json={
        "contract_price": price, "contract_status": "signed", "contract_date": "2026-10-20"}))
    ca = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=H, json={"kind": "contract", "amount": price}))
    ok(client.post("/wholesale/approvals/%s/decide" % ca["id"], headers=A, json={"approve": True}))
    ok(client.post("/wholesale/deals/%s/stage" % deal_id, headers=H, json={"stage": "under_contract"}))
    step(19, "contract")
    # 20. buyer network import (synthetic file with a duplicate row)
    csv = ("Company,Contact Name,Email,Phone,Counties,Max Price,Strategy\n"
           "SYNTH GP Flip LLC,Gail Test,gail@gp.example.test,214-555-0161,Dallas,250000,flip\n"
           "SYNTH GP Flip LLC,Gail Test,gail@gp.example.test,214-555-0161,Dallas,250000,flip\n"
           "SYNTH Florida Only,Fred Test,fred@fl.example.test,813-555-0162,,400000,rental\n")
    leads_before = db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count()
    b = ok(client.post("/wholesale/buyers/imports", headers=H, files={"file": ("gp.csv", io.BytesIO(csv.encode()), "text/csv")},
                       data={"is_test": "true", "list_name": "GP synthetic"}))
    ok(client.post("/wholesale/buyers/imports/%s/analyze" % b["id"], headers=H))
    res = ok(client.post("/wholesale/buyers/imports/%s/commit" % b["id"], headers=H))["result"]
    assert res["created"] == 2 and res["skipped"] == 1
    assert db_session.query(Lead).filter(Lead.organization_id == sample_org.id).count() == leads_before
    gail = db_session.query(WholesaleBuyer).filter(WholesaleBuyer.email == "gail@gp.example.test").one()
    step(20, "buyer imported")
    # 21. DFW shortlist with reasons
    net = ok(client.get("/wholesale/buyers/network", headers=H, params={"segment": "dfw", "include_test": True}))
    assert gail.id in [x["buyer_id"] for x in net["buyers"]]
    assert "SYNTH Florida Only" not in [x["name"] for x in net["buyers"]]
    ok(client.post("/wholesale/deals/%s/match-buyers" % deal_id, headers=H, json={}))   # the existing matcher
    intel = ok(client.get("/wholesale/deals/%s/buyer-intel" % deal_id, headers=H))
    me = [x for x in intel["buyers"] if x["buyer_id"] == gail.id][0]
    assert me["tier"]["tier"] == "POTENTIAL_BUYER" or me["tier"]["tier"] == "UNVERIFIED_CONTACT"
    assert me["explain"]
    step(21, "shortlist")
    # 22-24. package, approval (admin), publish through the authorized route
    ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={
        "buyer_room_summary": "Vacant 3/2, roof needed.", "buyer_room_asking_price": price + 12000}))
    pk = ok(client.post("/wholesale/deals/%s/package/submit" % deal_id, headers=H))
    assert client.post("/wholesale/approvals/%s/decide" % pk["approval"]["id"], headers=H,
                       json={"approve": True}).status_code == 403
    ok(client.post("/wholesale/approvals/%s/decide" % pk["approval"]["id"], headers=A, json={"approve": True}))
    assert ok(client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=A))["live"] is True
    step(22, "package"); step(23, "package approved"); step(24, "published")
    # 25-26. the buyer opens it and offers through the investor form (deal sheet send is sandboxed)
    ok(client.post("/wholesale/deals/%s/disposition" % deal_id, headers=H, json={"buyer_ids": [gail.id], "channel": "email"}))
    link = ok(client.post("/wholesale/deals/%s/share-links" % deal_id, headers=H, json={"audience": "buyer", "buyer_id": gail.id}))
    page = ok(client.get("/wholesale-rooms/buyer/%s" % link["token"]))
    leak = json.dumps(page)
    assert "GOLDEN TEST OWNER" not in leak.upper() and str(price) not in leak
    ok(client.post("/wholesale-rooms/buyer/%s/action" % link["token"], json={
        "action": "offer", "amount": price + 11000, "closing_date": "2026-11-20", "financing": "cash",
        "proof_of_funds": "can_provide", "contact_name": "Gail Test", "contact_email": "gail@gp.example.test"}))
    step(25, "engagement recorded"); step(26, "buyer offer received")
    # 27. compare
    ds = ok(client.get("/wholesale/deals/%s/disposition-summary" % deal_id, headers=H))
    f = {x["key"]: x["count"] for x in ds["funnel"]}
    assert f["offers"] == 1 and f["viewed"] == 1 and f["opened"] == 0
    assert ds["offers"][0]["net"] == 11000
    rd = ds["readiness"]
    assert rd["ready"] is True and rd["eligible_buyers"] >= 1
    step(27, "offers compared")
    # 28-29. a person selects; assignment approved
    board = ok(client.get("/wholesale/deals/%s/buyer-board" % deal_id, headers=H))
    row = [x for x in board["buyers"] if x["buyer_id"] == gail.id][0]
    ok(client.post("/wholesale/deals/%s/select-buyer" % deal_id, headers=H, json={"outreach_id": row["outreach_id"]}))
    fee = 11000
    ok(client.post("/wholesale/deals/%s/assign" % deal_id, headers=H, json={
        "buyer_id": gail.id, "buyer_price": price + fee, "assignment_fee": fee}))
    aa = ok(client.post("/wholesale/deals/%s/approvals" % deal_id, headers=H, json={"kind": "assignment", "amount": fee}))
    ok(client.post("/wholesale/approvals/%s/decide" % aa["id"], headers=A, json={"approve": True}))
    step(28, "buyer selected by a person"); step(29, "assignment approved")
    # 30-31. closing and fee
    ok(client.patch("/wholesale/deals/%s/title" % deal_id, headers=H, json={
        "title_company": "Fixture Title Co", "title_status": "clear", "title_file_number": "GP-1"}))
    ok(client.post("/wholesale/deals/%s/close" % deal_id, headers=H, json={"closing_date": "2026-11-20"}))
    ok(client.post("/wholesale/deals/%s/fee-collected" % deal_id, headers=H, json={
        "amount": fee, "method": "wire", "reference": "WIRE-GP-1", "collected_at": "2026-11-21"}))
    step(30, "closed"); step(31, "fee recorded")
    # 32. reporting
    ck = ok(client.get("/wholesale/deals/%s/disposition-summary" % deal_id, headers=H))["closing_checklist"]
    assert {s["label"]: s["done"] for s in ck["steps"]}["Fee collected"] is True
    an = ok(client.get("/wholesale/disposition/analytics", headers=H, params={"include_test": True}))
    assert an["deals_closed"] >= 1
    ok(client.get("/wholesale/dashboard", headers=H))
    assert ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))["status"] == "closed"
    step(32, "reporting")

    # ONE property, ONE deal, ONE seller record, end to end
    assert db_session.query(WholesaleDeal).filter(WholesaleDeal.property_id == deal.property_id).count() == 1
    assert db_session.query(WholesaleProperty).filter(WholesaleProperty.organization_id == sample_org.id,
                                                      WholesaleProperty.street_address.ilike("4410 Golden%")).count() == 1
    assert ok(client.get(ES + "/properties/%s" % pid, headers=H))["property"]["promoted_deal_id"] == deal_id
    assert [n for n, _ in STEPS] == list(range(1, 33))


# ── negative cases ──────────────────────────────────────────────────────────

def _quick_deal(client, H, street="9 Negative Ln"):
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": street, "city": "Dallas", "state": "TX", "zip_code": "75216", "county": "Dallas",
        "property_type": "single_family", "is_test": True}))["property_id"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Negative Owner"}))
    return pid, ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))["deal_id"]


def test_negative_cases(client, auth_headers, admin_auth_headers, db_session, monkeypatch, storage):
    from app.models.models import Organization, User
    from app.services.auth_service import create_access_token, hash_password
    H, A = auth_headers, admin_auth_headers
    pid, deal_id = _quick_deal(client, H)
    # wrong tenant
    org = Organization(name="GP Rival", slug="gp-rival", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="gp.rival@example.test", full_name="R", role="org_admin",
             password_hash=hash_password("TestPass123!"), must_change_password=False)
    db_session.add(u)
    db_session.commit()
    rival = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    for m, path in (("get", "/wholesale/deals/%s/underwriting" % deal_id), ("post", "/wholesale/deals/%s/comps/find" % deal_id),
                    ("post", "/wholesale/deals/%s/package/publish" % deal_id), ("get", "/wholesale/deals/%s/buyer-intel" % deal_id)):
        r = getattr(client, m)(path, headers=rival, **({"json": {}} if m == "post" else {}))
        assert r.status_code in (403, 404), path
    # wrong role: an advisor cannot publish
    ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    assert client.post("/wholesale/deals/%s/publication/state" % deal_id, headers=H,
                       json={"audience": "buyer", "published": True}).status_code == 403
    # unapproved publication
    assert client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=A).status_code == 409
    # missing comps / evidence: no ARV is invented
    uw = ok(client.get("/wholesale/deals/%s/underwriting" % deal_id, headers=H))
    assert uw["arv"]["value"] is None and uw["offer"]["mao"] is None
    # missing contact data / outreach permission: nothing sent
    assert ok(client.post(ES + "/properties/%s/outreach" % pid, headers=H))["started"] is False
    # missing contract approval: the stage gate holds
    ok(client.patch("/wholesale/deals/%s/contract" % deal_id, headers=H, json={"contract_price": 1000, "contract_status": "signed"}))
    assert client.post("/wholesale/deals/%s/stage" % deal_id, headers=H, json={"stage": "under_contract"}).status_code == 409
    # invalid / fake sale data
    bad = "address,sale_price,sale_date,mls_number\n1 A St,100000,2099-01-01,MLS1\n2 B St,0,2026-01-01,MLS2\n3 C St,90000,2026-01-01,\n"
    r = ok(client.post("/wholesale/deals/%s/comps/import" % deal_id, headers=H,
                       files={"file": ("c.csv", io.BytesIO(bad.encode()), "text/csv")}))
    assert r["added"] == 0 and len(r["refused"]) == 3
    assert client.post("/wholesale/buyers/imports", headers=H, files={"file": ("x.pdf", io.BytesIO(b"%PDF"), "application/pdf")}).status_code == 400
    # duplicate buyer detection
    ok(client.post("/wholesale/buyers", headers=H, json={"company_name": "Dup Co", "email": "dup@example.test", "is_test": True}))
    b = ok(client.post("/wholesale/buyers/imports", headers=H, data={"is_test": "true"},
                       files={"file": ("d.csv", io.BytesIO(b"Company,Email\nDup Co,DUP@example.test\n"), "text/csv")}))
    assert ok(client.post("/wholesale/buyers/imports/%s/analyze" % b["id"], headers=H))["counts"] == {"update": 1}
    # provider unavailable / failed job: reported, never faked
    from app.routers import wholesale_underwriting_router as R
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")

    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    raise TimeoutError("provider down")
    from tests.test_wholesale_photo_repairs import jpeg
    d = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    ok(client.post("/wholesale/properties/%s/photos" % d.property_id, headers=H,
                   files={"file": ("n.jpg", io.BytesIO(jpeg(seed=4)), "image/jpeg")}, data={"category": "kitchen"}))
    monkeypatch.setattr(R, "_vision_client", lambda: Boom())
    out = ok(client.post("/wholesale/deals/%s/repairs/photo-analysis" % deal_id, headers=H, json={}))
    assert out["ai"]["used"] is False and "did not answer" in out["ai"]["why"] and out["lines"] == []
    found = ok(client.post("/wholesale/deals/%s/comps/find" % deal_id, headers=H, json={}))
    reapi = [s for s in found["sources"] if s["key"] == "reapi_comps"][0]
    assert reapi["available"] is False and "REAPI_API_KEY" in reapi["why"]
    # stale approval: content changed after approval goes dark
    from tests.wholesale_publish_helpers import approve_and_publish
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={"buyer_room_summary": "v1"}))
    approve_and_publish(client, H, A, deal_id)
    buyer = ok(client.post("/wholesale/buyers", headers=H, json={"company_name": "Neg Viewer", "email": "nv@example.test", "is_test": True}))
    tok = ok(client.post("/wholesale/deals/%s/share-links" % deal_id, headers=H,
                         json={"audience": "buyer", "buyer_id": buyer["id"]}))["token"]
    assert client.get("/wholesale-rooms/buyer/%s" % tok).status_code == 200
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={"buyer_room_summary": "v2"}))
    assert client.get("/wholesale-rooms/buyer/%s" % tok).status_code == 404
    # sensitive data never reaches the investor package content
    from app.services import wholesale_publication as pub
    content = ok(client.get("/wholesale/deals/%s/package" % deal_id, headers=H))["content"]
    assert pub.leaked_keys(content, pub.FORBIDDEN_TO_BUYER) == [] and "Negative Owner" not in json.dumps(content)
