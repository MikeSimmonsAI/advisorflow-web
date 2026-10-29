"""PRIVATE BUYER DEAL ROOM (#415) - the owner's acceptance journey A..AC, plus
security, upload, messaging, e-sign and exclusive-review concurrency.

Practice records only: .invalid investor emails, sandbox (is_test) deals,
nothing is emailed to anyone, no paid lookup runs, no real obligation exists.
"""
import io
import zipfile
from datetime import datetime, timedelta

import pytest

from app.models.wholesale_investor_models import DealListing, ExclusiveHold, InvestorAccount
from app.models.wholesale_models import WholesaleBuyer, WholesaleBuyerOutreach
from app.services import wholesale_deal_room as DR
from tests.test_wholesale_deal_package import _approve, _deal, _photo, _review

DRS = "/wholesale/deal-room"


def ok(r, what=""):
    assert r.status_code in (200, 201), "%s -> %s %s" % (what or r.url, r.status_code, r.text[:500])
    return r.json()


def _published_deal(client, H, A, db_session, street="91 Deal Room Ct"):
    deal_id = _deal(client, H, db_session, street=street)
    ok(client.post("/wholesale/deals/%s/package" % deal_id, headers=H))
    ok(client.patch("/wholesale/deals/%s/publication" % deal_id, headers=H, json={
        "buyer_room_summary": "Vacant 3/2, needs a roof. Practice listing.", "buyer_room_asking_price": 129000}))
    _photo(db_session, deal_id, name="%s.jpg" % street.split()[0])
    _approve(client, A, _review(client, H, deal_id))
    ok(client.post("/wholesale/deals/%s/package/publish" % deal_id, headers=A))
    return deal_id


def _invite(client, A, name, email, buyer_id=None):
    body = {"name": name, "email": email, "is_test": True}
    if buyer_id:
        body["buyer_id"] = buyer_id
    r = ok(client.post(DRS + "/invites", headers=A, json=body))
    return r, r["link"].rsplit("/", 1)[-1]


def _accept(client, token, password="Practice-Pass-2026", **kw):
    r = ok(client.post("/investor/invite/%s/accept" % token, json={"password": password, **kw}))
    return {"Authorization": "Bearer %s" % r["access_token"]}


@pytest.fixture()
def room(client, auth_headers, admin_auth_headers, db_session):
    H, A = auth_headers, admin_auth_headers
    deal_id = _published_deal(client, H, A, db_session)
    # A competing practice investor already exists as a buyer with an offer on record.
    rival_buyer = ok(client.post("/wholesale/buyers", headers=H, json={
        "company_name": "Rival Practice LLC", "email": "rival@practice.invalid", "is_test": True}))
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % rival_buyer["id"], headers=H,
                   json={"label": "dallas", "property_types": ["single_family"], "counties": ["Dallas"]}))
    _r, rtok = _invite(client, A, "Rita Rival", "rival@practice.invalid", buyer_id=rival_buyer["id"])
    RH = _accept(client, rtok)
    return {"H": H, "A": A, "deal_id": deal_id, "rival": RH, "rival_buyer": rival_buyer["id"]}


def test_full_investor_journey_a_to_ac(client, room, db_session, sample_org):
    H, A, deal_id = room["H"], room["A"], room["deal_id"]

    # A. staff invites a practice investor (practice address: nothing emailed, link shown once)
    inv, token = _invite(client, A, "Pat Practice", "pat@practice.invalid")
    assert inv["status"] == "pending" and inv["delivery"].startswith("not_sent: practice")
    assert ok(client.get(DRS + "/invites?include_test=true", headers=A))["invites"]

    # B/C. accept, register as a business
    pv = ok(client.get("/investor/invite/%s" % token))
    assert pv["next"] == "register" and pv["email_masked"].startswith("pa***@")
    I = _accept(client, token, full_name="Pat Practice", kind="business")
    assert client.get("/investor/invite/%s" % token).status_code in (404, 410)        # single use
    ok(client.patch("/investor/me/identity", headers=I, json={
        "kind": "business", "legal_name": "Pat Practice", "preferred_name": "Pat",
        "company_legal_name": "Practice Capital LLC", "entity_type": "llc", "position": "Managing member",
        "phone": "+12145550188"}))

    # D. the entity links to ONE canonical buyer record
    me = ok(client.get("/investor/me", headers=I))
    assert me["account"]["identity_complete"] and me["account"]["email_verified"]
    assert [e["name"] for e in me["entities"]] == ["Practice Capital LLC"]
    acct = db_session.query(InvestorAccount).filter(InvestorAccount.is_test.is_(True),
                                                    InvestorAccount.legal_name == "Pat Practice").first()
    buyer_id = acct.buyer_id
    assert db_session.query(WholesaleBuyer).filter(WholesaleBuyer.email == "pat@practice.invalid").count() == 1

    # E. the AI concierge greets and learns (rules reader; no AI spend in tests)
    th = ok(client.get("/investor/threads", headers=I))["threads"]
    concierge = [t for t in th if t["kind"] == "concierge"][0]
    assert "Welcome to your Deal Room, Pat" in concierge["messages"][0]["text"]
    for msg in ("Yes, actively buying", "Single family houses", "Dallas County", "Up to $200k"):
        ok(client.post("/investor/threads/%s/messages" % concierge["id"], headers=I, json={"text": msg}))
    # G. enrichment moves only on evidence: buying status + stated box, nothing documented
    bi = ok(client.get("/wholesale/buyer-intel/buyers/%s" % buyer_id, headers=H))
    assert bi["assessment"]["readiness"]["funding"]["documented"] is False
    assert any(b["source"] == "ai_conversation" and not b["confirmed_at"] for b in bi["buy_boxes"])

    # F. the investor reviews and CONFIRMS their buy box
    box = [b for b in ok(client.get("/investor/me", headers=I))["buy_boxes"] if b["source"] == "ai_conversation"][0]
    assert box["fields"]["max_price"]["label"] == "BUYER STATED" and box["fields"]["zips"]["label"] == "UNKNOWN"
    ok(client.put("/investor/me/buy-boxes/%s" % box["id"], headers=I, json={
        "property_types": ["single_family"], "counties": ["Dallas"], "max_price": 200000}))
    # H. staff see the investor in Buyer Intelligence, with the box confirmed
    bi = ok(client.get("/wholesale/buyer-intel/buyers/%s" % buyer_id, headers=H))
    assert bi["assessment"]["buy_box"]["status"] == "CONFIRMED"
    assert ok(client.get(DRS + "/buyers/%s" % buyer_id, headers=H))["accounts"][0]["identity_complete"]

    # I. staff publish the approved practice deal to the Deal Room (matched audience)
    lst = ok(client.post(DRS + "/deals/%s/listing" % deal_id, headers=H))
    assert client.post(DRS + "/listings/%s/publish" % lst["id"], headers=H).status_code == 409   # not approved yet
    ok(client.post(DRS + "/listings/%s/approve" % lst["id"], headers=H))
    lst = ok(client.post(DRS + "/listings/%s/publish" % lst["id"], headers=H))
    assert lst["state"] == "published"

    # J. the investor sees the personalised deal - and nothing internal
    dash = ok(client.get("/investor/dashboard", headers=I))
    assert dash["counts"]["opportunities"] == 1
    card = dash["opportunities"][0]
    assert card["asking_price"] == 129000 and card["match"]["score"] is not None
    detail = ok(client.get("/investor/opportunities/%s" % card["id"], headers=I))
    from app.services import wholesale_publication as P
    assert not P.leaked_keys(detail["room"], P.FORBIDDEN_TO_BUYER)
    assert "Secret Seller Name" not in str(detail) and "98765" not in str(detail)       # seller + contract price

    # K/L. a question answered from authorized deal information only
    dt = ok(client.post("/investor/threads", headers=I, json={"kind": "deal", "listing_id": card["id"]}))
    r = ok(client.post("/investor/threads/%s/messages" % dt["id"], headers=I, json={"text": "What is the asking price?"}))
    assert "$129,000" in r["reply"]
    r = ok(client.post("/investor/threads/%s/messages" % dt["id"], headers=I, json={"text": "What are the property taxes?"}))
    assert "won't guess" in r["reply"]

    # M/N. the investor asks for a person; staff get it, take over, reply, return to AI
    r = ok(client.post("/investor/threads/%s/messages" % concierge["id"], headers=I,
                       json={"text": "Can I talk to Michael please?"}))
    assert "asked the team" in r["reply"] and not r["thread"]["ai_active"]
    inbox = ok(client.get(DRS + "/inbox?include_test=true", headers=H))["threads"]
    assert [t for t in inbox if t["id"] == concierge["id"] and t["unread"] >= 1]
    ok(client.post(DRS + "/threads/%s/note" % concierge["id"], headers=H, json={"text": "INTERNAL: margin is thin"}))
    ok(client.post(DRS + "/threads/%s/reply" % concierge["id"], headers=H, json={"text": "Hi Pat, Michael here."}))
    seen = ok(client.get("/investor/threads/%s" % concierge["id"], headers=I))
    assert any(m["role"] == "staff" for m in seen["messages"])
    assert "INTERNAL" not in str(seen) and "margin" not in str(seen)                      # internal notes never leak
    r = ok(client.post("/investor/threads/%s/messages" % concierge["id"], headers=I, json={"text": "Thanks!"}))
    assert r["reply"] is None                                                             # AI stays quiet after takeover
    ok(client.post(DRS + "/threads/%s/return-to-ai" % concierge["id"], headers=H, json={}))

    # O/P. a document over 500 KB uploads; staff review and download it
    pdf = b"%PDF-1.4\n" + b"0" * (900 * 1024) + b"\n%%EOF"
    up = ok(client.post("/investor/files", headers=I, data={"category": "proof_of_funds"},
                        files={"file": ("pof.pdf", io.BytesIO(pdf), "application/pdf")}))
    assert up["size"] > 500 * 1024 and up["scan_status"] == "clean" and up["review_status"] == "pending"
    got = client.get(DRS + "/files/%s" % up["id"], headers=H)
    assert got.status_code == 200 and len(got.content) == len(pdf)
    ok(client.post(DRS + "/files/%s/review" % up["id"], headers=H,
                   json={"status": "accepted", "pof_expires": (datetime.utcnow() + timedelta(days=60)).strftime("%Y-%m-%d")}))
    assert ok(client.get("/investor/me", headers=I))["funding"]["documented"] is True

    # Q/R. staff prepare the approved practice disclosure; both parties sign in-app
    from tests.conftest import TEST_TWILIO_ACCOUNT_SID  # noqa: F401 - conftest loaded
    ok(client.post("/wholesale/deals/%s/assign" % deal_id, headers=H, json={"buyer_id": buyer_id, "buyer_price": 129000}))
    sent = ok(client.post("/wholesale/deals/%s/contracts/assignee_disclosure/send" % deal_id, headers=H, json={}))
    assert sent["sent"], sent
    env_id = sent["external_ref"]
    docs = ok(client.get("/investor/signatures", headers=I))["documents"]
    assert docs and docs[0]["your_turn"] and docs[0]["your_role"] == "Assignee"
    ses = ok(client.post("/investor/signatures/%s/session" % env_id, headers=I))
    first = ok(client.post("/esign/sign/%s/sign" % ses["token"], json={"consent": True, "typed_name": "Pat Practice"}))
    assert not first["completed"]
    staff = ok(client.post(DRS + "/envelopes/%s/staff-session" % env_id, headers=H))
    done = ok(client.post("/esign/sign/%s/sign" % staff["token"], json={"consent": True, "typed_name": "Advisor One"}))
    assert done["completed"]
    assert ok(client.get("/investor/signatures", headers=I))["documents"][0]["completed"]

    # rival made an offer before the hold - it must survive the hold (V)
    rcard = ok(client.get("/investor/opportunities", headers=room["rival"]))["items"][0]
    ok(client.post("/investor/opportunities/%s/interest" % rcard["id"], headers=room["rival"],
                   json={"kind": "offer", "amount": 120000, "financing": "cash"}))

    # S/T. exclusive review requested and approved for a controlled window
    req = ok(client.post("/investor/opportunities/%s/exclusive-review" % card["id"], headers=I, json={"note": "Need 2 days"}))
    assert "not an option, a contract or a right to purchase" in req["notice"]
    h = ok(client.post(DRS + "/holds/%s" % req["id"], headers=room["A"], json={"decision": "approve", "preset": "48h"}))
    assert h["status"] == "active" and 47 * 3600 < h["seconds_left"] <= 48 * 3600
    mine = ok(client.get("/investor/opportunities/%s" % card["id"], headers=I))
    assert mine["hold"]["yours"] and mine["hold"]["seconds_left"] > 0

    # U. the competing buyer loses visibility - list, direct link, interest and threads
    R = room["rival"]
    assert ok(client.get("/investor/opportunities", headers=R))["items"] == []
    assert client.get("/investor/opportunities/%s" % card["id"], headers=R).status_code == 404
    assert client.post("/investor/opportunities/%s/interest" % card["id"], headers=R,
                       json={"kind": "offer", "amount": 125000}).status_code == 404
    assert client.post("/investor/threads", headers=R, json={"kind": "deal", "listing_id": card["id"]}).status_code == 404

    # V. existing offers remain recorded
    offer = db_session.query(WholesaleBuyerOutreach).filter(WholesaleBuyerOutreach.buyer_id == room["rival_buyer"]).first()
    assert offer.status == "offer_submitted" and float(offer.offer_amount) == 120000

    # W. the window expires (server time) and visibility returns
    hold = db_session.query(ExclusiveHold).filter(ExclusiveHold.id == req["id"]).first()
    hold.ends_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()
    back = ok(client.get("/investor/opportunities", headers=R))["items"]
    assert len(back) == 1 and back[0]["hold"] is None
    assert db_session.query(DealListing).filter(DealListing.id == card["id"]).first().state == "published"

    # X/Y/Z. the internal Wholesale side still works
    assert ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))
    assert client.get("/wholesale/evosense/inbox", headers=H).status_code == 200
    assert client.get("/wholesale/foreclosures/summary", headers=H).status_code in (200, 404)
    m = ok(client.post("/wholesale/buyer-intel/match", headers=H, json={"subject_type": "deal", "subject_id": deal_id}))
    assert any(b["buyer_id"] == buyer_id for b in m["buyers"])

    # AC. nothing was emailed for real, no paid lookup ran
    from app.models.wholesale_models import WholesaleEnrichmentRequest
    assert db_session.query(WholesaleEnrichmentRequest).count() == 0


def test_investor_login_is_fenced_out_of_everything_else(client, room):
    I = room["rival"]
    for path in ("/wholesale/buyers", "/wholesale/deals/%s" % room["deal_id"], "/leads/", "/god/overview",
                 "/wholesale/evosense/inbox", "/wholesale/buyer-intel/summary", "/wholesale/deal-room/inbox",
                 "/auth/my-contexts", "/org-settings/"):
        r = client.get(path, headers=I)
        assert r.status_code in (401, 403, 404), (path, r.status_code)
    assert client.get("/investor/me", headers=I).status_code == 200
    # staff cannot use the investor API either
    assert client.get("/investor/me", headers=room["H"]).status_code == 403


def test_another_tenant_and_direct_links_are_denied(client, room, db_session):
    from app.models.models import Organization, User
    from app.services.auth_service import create_access_token, hash_password
    I = room["rival"]
    card = ok(client.get("/investor/opportunities", headers=I))["items"][0] if ok(
        client.get("/investor/opportunities", headers=I))["items"] else None
    org = Organization(name="Other DR Co", slug="other-dr", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="dr.other@example.test", full_name="O", role="org_admin",
             password_hash=hash_password("TestPass123!"), must_change_password=False)
    db_session.add(u)
    db_session.commit()
    X = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    assert ok(client.get(DRS + "/invites?include_test=true", headers=X))["invites"] == []
    assert ok(client.get(DRS + "/listings?include_test=true", headers=X))["listings"] == []
    assert ok(client.get(DRS + "/inbox?include_test=true", headers=X))["threads"] == []
    assert client.get(DRS + "/buyers/%s" % room["rival_buyer"], headers=X).status_code == 404
    # a file id from another investor is not reachable
    up = ok(client.post("/investor/files", headers=I, data={"category": "identity"},
                        files={"file": ("id.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64), "image/png")}))
    assert client.get(DRS + "/files/%s" % up["id"], headers=X).status_code == 404
    _r, tok = _invite(client, room["A"], "Second Practice", "second@practice.invalid")
    S = _accept(client, tok)
    assert client.get("/investor/files/%s" % up["id"], headers=S).status_code == 404
    assert client.post("/investor/threads/%s/messages" % "nope", headers=S, json={"text": "hi"}).status_code == 404
    if card:
        # an unmatched investor (no buy box) cannot open a matched listing by its id
        assert client.get("/investor/opportunities/%s" % card["id"], headers=S).status_code == 404


def test_invites_are_single_use_revocable_and_refuse_staff_emails(client, room, sample_advisor):
    A = room["A"]
    inv, tok = _invite(client, A, "Revoke Me", "revoke@practice.invalid")
    ok(client.post(DRS + "/invites/%s/revoke" % inv["id"], headers=A))
    assert client.get("/investor/invite/%s" % tok).status_code in (404, 410)
    r = client.post(DRS + "/invites", headers=A, json={"name": "Staff", "email": sample_advisor.email, "is_test": True})
    assert r.status_code == 409
    inv, tok = _invite(client, A, "Resend Me", "resend@practice.invalid")
    re = ok(client.post(DRS + "/invites/%s/resend" % inv["id"], headers=A))
    assert client.get("/investor/invite/%s" % tok).status_code == 404               # the old link stopped working
    assert ok(client.get("/investor/invite/%s" % re["link"].rsplit("/", 1)[-1]))["next"] == "register"
    bad = client.post("/investor/invite/%s/accept" % re["link"].rsplit("/", 1)[-1], json={"password": "short"})
    assert bad.status_code == 400


def test_exclusive_holds_are_exclusive_and_staff_only(client, room, db_session):
    A, R = room["A"], room["rival"]
    lst = ok(client.post(DRS + "/deals/%s/listing" % room["deal_id"], headers=room["H"]))
    ok(client.post(DRS + "/listings/%s/approve" % lst["id"], headers=room["H"]))
    ok(client.post(DRS + "/listings/%s/publish" % lst["id"], headers=room["H"]))
    _r, tok = _invite(client, A, "Second Bidder", "bidder2@practice.invalid", buyer_id=None)
    S = _accept(client, tok)
    sb = db_session.query(InvestorAccount).filter(InvestorAccount.legal_name == "Second Bidder").first()
    ok(client.put("/investor/me/buy-boxes/%s" % ok(client.post("/investor/me/buy-boxes", headers=S, json={
        "property_types": ["single_family"], "counties": ["Dallas"]}))["id"], headers=S, json={}))
    r1 = ok(client.post("/investor/opportunities/%s/exclusive-review" % lst["id"], headers=R, json={}))
    r2 = ok(client.post("/investor/opportunities/%s/exclusive-review" % lst["id"], headers=S, json={}))
    # an advisor (not owner/manager) cannot approve
    assert client.post(DRS + "/holds/%s" % r1["id"], headers=room["H"],
                       json={"decision": "approve", "preset": "24h"}).status_code == 403
    assert client.post(DRS + "/holds/%s" % r1["id"], headers=A,
                       json={"decision": "approve", "minutes": 60 * 24 * 40}).status_code == 400
    ok(client.post(DRS + "/holds/%s" % r1["id"], headers=A, json={"decision": "approve", "preset": "24h"}))
    # a second simultaneous hold is refused
    assert client.post(DRS + "/holds/%s" % r2["id"], headers=A,
                       json={"decision": "approve", "preset": "24h"}).status_code == 409
    assert db_session.query(ExclusiveHold).filter(ExclusiveHold.listing_id == lst["id"],
                                                  ExclusiveHold.status == "active").count() == 1
    h = ok(client.post(DRS + "/holds/%s" % r1["id"], headers=A, json={"decision": "extend", "preset": "24h"}))
    assert h["duration_minutes"] == 48 * 60 and h["extended_count"] == 1
    ok(client.post(DRS + "/holds/%s" % r1["id"], headers=A, json={"decision": "release"}))
    assert ok(client.get("/investor/opportunities", headers=S))["items"]
    # the database itself refuses two active holds on one listing
    a = db_session.query(ExclusiveHold).filter(ExclusiveHold.id == r2["id"]).first()
    b = db_session.query(ExclusiveHold).filter(ExclusiveHold.id == r1["id"]).first()
    a.status = b.status = "active"
    with pytest.raises(Exception):
        db_session.commit()
    db_session.rollback()


def test_upload_limits_types_and_active_content_are_enforced(client, room, monkeypatch):
    I = room["rival"]
    bad_pdf = b"%PDF-1.4\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>\n%%EOF"
    r = client.post("/investor/files", headers=I, data={"category": "other"},
                    files={"file": ("x.pdf", io.BytesIO(bad_pdf), "application/pdf")})
    assert r.status_code == 400 and "active content" in r.json()["detail"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("word/vbaProject.bin", "macro")
    r = client.post("/investor/files", headers=I, data={"category": "other"},
                    files={"file": ("m.docx", io.BytesIO(buf.getvalue()),
                                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert r.status_code == 400 and "macros" in r.json()["detail"]
    r = client.post("/investor/files", headers=I, data={"category": "other"},
                    files={"file": ("e.exe", io.BytesIO(b"MZ" + b"0" * 100), "application/octet-stream")})
    assert r.status_code == 400
    monkeypatch.setenv("DEAL_ROOM_MAX_FILE_MB", "1")
    r = client.post("/investor/files", headers=I, data={"category": "other"},
                    files={"file": ("big.pdf", io.BytesIO(b"%PDF-1.4\n" + b"0" * (1100 * 1024)), "application/pdf")})
    assert r.status_code == 413
    xlsx = io.BytesIO()
    with zipfile.ZipFile(xlsx, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("xl/workbook.xml", "<w/>")
    ok(client.post("/investor/files", headers=I, data={"category": "rent_roll"},
                   files={"file": ("rr.xlsx", io.BytesIO(xlsx.getvalue()),
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}))


def test_opt_out_in_chat_stops_ai_and_outreach_but_keeps_the_room(client, room, db_session):
    I = room["rival"]
    th = ok(client.get("/investor/threads", headers=I))["threads"][0]
    r = ok(client.post("/investor/threads/%s/messages" % th["id"], headers=I, json={"text": "STOP sending me messages"}))
    assert "won't receive outreach" in r["reply"] and not r["thread"]["ai_active"]
    b = db_session.query(WholesaleBuyer).filter(WholesaleBuyer.id == room["rival_buyer"]).first()
    assert b.do_not_contact
    assert client.post(DR.__name__ and "/wholesale/deal-room/threads/%s/return-to-ai" % th["id"], headers=room["H"],
                       json={}).status_code == 409
