"""DEAL ROOM setup, owner management and the authorized AI concierge
(#415 follow-up: Buyers command center + Deal Room redesign).

Practice records only (.invalid investors, sandbox deals). The AI path is
exercised with a FAKE model client through the gateway's test seam - no
provider is called and nothing is spent.
"""
import io
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.wholesale_investor_models import DealListing, DealRoomSettings, ExclusiveHold
from app.services import wholesale_deal_room_chat as CH
from tests.test_deal_room import DRS, _accept, _invite, _published_deal, ok
from tests.test_wholesale_deal_package import _deal


class FakeAI:
    """Mimics the SDK shape: client.chat.completions.create(...)."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        content = self.reply(kw) if callable(self.reply) else self.reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))],
                               usage=None)


def _setup(client, A, **extra):
    body = {"name": "Practice Deal Room", "support_email": "support@practice.invalid", "categories": ["single_family"],
            "hold_presets": ["24h", "48h"], **extra}
    team = ok(client.get(DRS + "/settings", headers=A))["options"]["team"]
    body.setdefault("team_user_ids", [team[0]["user_id"]] if team else [])
    return ok(client.put(DRS + "/settings", headers=A, json=body))


def test_settings_prefill_save_persist_and_real_activation(client, admin_auth_headers, auth_headers, db_session):
    A = admin_auth_headers
    v = ok(client.get(DRS + "/settings", headers=A))
    assert v["saved"] is False and v["status"] == "setup" and v["settings"]["name"]       # prefilled, nothing saved
    assert "name" in v["prefilled"]
    # nothing to activate yet: the server refuses and says why (no fake success)
    r = ok(client.post(DRS + "/activate", headers=A))
    assert r["activated"] is False and "Settings saved" in r["message"]
    # an advisor cannot change the settings
    assert client.put(DRS + "/settings", headers=auth_headers, json={"name": "x"}).status_code == 403
    assert client.put(DRS + "/settings", headers=A, json={"support_email": "nope"}).status_code == 400
    saved = _setup(client, A, welcome_message="Hello investors", default_publish_days=10, ai_concierge_enabled=False)
    assert saved["saved"] and saved["settings"]["welcome_message"] == "Hello investors"
    again = ok(client.get(DRS + "/settings", headers=A))                                  # persists after reload
    assert again["settings"]["default_publish_days"] == 10 and again["settings"]["ai_concierge_enabled"] is False
    rd = ok(client.get(DRS + "/readiness", headers=A))
    assert rd["can_activate"] and not rd["blockers"]
    r = ok(client.post(DRS + "/activate", headers=A))
    assert r["activated"] is True and r["status"] == "active"
    row = db_session.query(DealRoomSettings).first()
    assert row.status == "active" and row.activated_by_id and row.activated_at
    # settings change later without restarting setup
    ok(client.put(DRS + "/settings", headers=A, json={"tagline": "Updated"}))
    assert ok(client.get(DRS + "/settings", headers=A))["status"] == "active"
    assert ok(client.post(DRS + "/pause", headers=A))["status"] == "paused"


def test_real_invites_and_publication_need_an_active_room(client, admin_auth_headers, auth_headers, db_session):
    A, H = admin_auth_headers, auth_headers
    r = client.post(DRS + "/invites", headers=A, json={"name": "Real Person", "email": "real.person@example.org"})
    assert r.status_code == 409 and "Activate" in r.json()["detail"]
    ok(client.post(DRS + "/invites", headers=A, json={"name": "Practice", "email": "p1@practice.invalid", "is_test": True}))
    # a practice deal publishes before activation
    deal_id = _published_deal(client, H, A, db_session, street="5 Setup Practice Way")
    lst = ok(client.post(DRS + "/deals/%s/listing" % deal_id, headers=H))
    ok(client.post(DRS + "/listings/%s/approve" % lst["id"], headers=H))
    ok(client.post(DRS + "/listings/%s/publish" % lst["id"], headers=H))
    # default closing window comes from the settings
    _setup(client, A, default_publish_days=7)
    ok(client.post(DRS + "/activate", headers=A))
    deal2 = _published_deal(client, H, A, db_session, street="6 Window Way")
    l2 = ok(client.post(DRS + "/deals/%s/listing" % deal2, headers=H))
    ok(client.post(DRS + "/listings/%s/approve" % l2["id"], headers=H))
    pub = ok(client.post(DRS + "/listings/%s/publish" % l2["id"], headers=H))
    exp = datetime.fromisoformat(pub["expires_at"].rstrip("Z"))
    assert timedelta(days=6) < exp - datetime.utcnow() <= timedelta(days=7)


def test_add_deal_candidates_explain_the_missing_step(client, admin_auth_headers, auth_headers, db_session):
    A, H = admin_auth_headers, auth_headers
    ready = _published_deal(client, H, A, db_session, street="7 Ready Rd")
    draft = _deal(client, H, db_session, street="8 Draft Dr")                    # no package yet
    rows = {r["deal_id"]: r for r in ok(client.get(DRS + "/candidates?include_test=true", headers=H))["deals"]}
    assert rows[ready]["ready"] and not rows[ready]["steps"]
    assert not rows[draft]["ready"] and any("Create the investor package" in s for s in rows[draft]["steps"])
    found = ok(client.get(DRS + "/candidates?include_test=true&q=draft", headers=H))["deals"]
    assert [r["deal_id"] for r in found] == [draft]
    # add with options in one step: audience, window, visible documents
    lst = ok(client.post(DRS + "/deals/%s/listing" % ready, headers=H, json={
        "audience": "all", "expires_at": (datetime.utcnow() + timedelta(days=3)).isoformat()[:19],
        "visible_document_ids": []}))
    assert lst["audience"] == "all" and lst["expires_at"]
    rows = {r["deal_id"]: r for r in ok(client.get(DRS + "/candidates?include_test=true", headers=H))["deals"]}
    assert rows[ready]["already_listed"]
    pv = ok(client.get(DRS + "/listings/%s/preview" % lst["id"], headers=H))
    blob = json.dumps(pv)
    assert pv["serving"] and "Secret Seller Name" not in blob and "98765" not in blob
    # a package that changed after approval drops out of the Deal Room
    ok(client.post(DRS + "/listings/%s/approve" % lst["id"], headers=H))
    ok(client.post(DRS + "/listings/%s/publish" % lst["id"], headers=H))
    ok(client.patch("/wholesale/deals/%s/publication" % ready, headers=H, json={"buyer_room_summary": "Edited after approval"}))
    assert ok(client.get(DRS + "/listings/%s/preview" % lst["id"], headers=H))["serving"] is False
    prev = ok(client.get(DRS + "/portal-preview", headers=H))
    assert all(o["id"] != lst["id"] for o in prev["opportunities"])


def _room_with_investor(client, A, H, db_session, street="9 Concierge Ct"):
    deal_id = _published_deal(client, H, A, db_session, street=street)
    lst = ok(client.post(DRS + "/deals/%s/listing" % deal_id, headers=H, json={"audience": "all"}))
    ok(client.post(DRS + "/listings/%s/approve" % lst["id"], headers=H))
    ok(client.post(DRS + "/listings/%s/publish" % lst["id"], headers=H))
    _r, tok = _invite(client, A, "Ada Practice", "ada@practice.invalid")
    I = _accept(client, tok, full_name="Ada Practice", kind="business")
    ok(client.patch("/investor/me/identity", headers=I, json={"kind": "business", "legal_name": "Ada Practice",
                                                               "company_legal_name": "Ada Capital LLC"}))
    return deal_id, lst["id"], I


def test_owner_sees_investors_entities_documents_activity_and_assigns(client, admin_auth_headers, auth_headers,
                                                                      db_session, sample_advisor):
    A, H = admin_auth_headers, auth_headers
    deal_id, lid, I = _room_with_investor(client, A, H, db_session)
    inv = ok(client.get(DRS + "/investors?include_test=true", headers=H))["investors"]
    ada = [x for x in inv if x["legal_name"] == "Ada Practice"][0]
    assert ada["company"] == "Ada Capital LLC" and [e["name"] for e in ada["entities"]] == ["Ada Capital LLC"]
    assert ada["intel"] and "readiness" in ada["intel"]
    assert ok(client.get(DRS + "/investors?include_test=true&q=nobody-matches", headers=H))["investors"] == []
    pdf = b"%PDF-1.4\n" + b"0" * (600 * 1024) + b"\n%%EOF"
    ok(client.post("/investor/files", headers=I, data={"category": "proof_of_funds"},
                   files={"file": ("pof.pdf", io.BytesIO(pdf), "application/pdf")}))
    docs = ok(client.get(DRS + "/documents?include_test=true", headers=H))
    assert any(f["name"] == "pof.pdf" and f["buyer_name"] for f in docs["files"])
    ov = ok(client.get(DRS + "/overview?include_test=true", headers=H))
    assert ov["counts"]["investors"] >= 1 and ov["counts"]["files_to_review"] >= 1 and ov["counts"]["listings_live"] >= 1
    th = ok(client.get("/investor/threads", headers=I))["threads"][0]
    team = ok(client.get(DRS + "/team", headers=H))["people"]
    t = ok(client.post(DRS + "/threads/%s/assign" % th["id"], headers=H, json={"user_id": sample_advisor.id}))
    assert t["assigned_user_id"] == sample_advisor.id
    assert client.post(DRS + "/threads/%s/assign" % th["id"], headers=H, json={"user_id": "not-on-team"}).status_code == 400
    acts = ok(client.get(DRS + "/activity", headers=H))["events"]
    assert any("assigned" in (e["summary"] or "") for e in acts) and team


def test_investor_saves_offers_and_reviews_lists(client, admin_auth_headers, auth_headers, db_session):
    A, H = admin_auth_headers, auth_headers
    deal_id, lid, I = _room_with_investor(client, A, H, db_session, street="10 Saved St")
    assert ok(client.post("/investor/opportunities/%s/save" % lid, headers=I, json={"saved": True}))["saved"]
    dash = ok(client.get("/investor/dashboard", headers=I))
    assert dash["counts"]["saved"] == 1 and dash["room"]["name"]
    assert [o for o in dash["opportunities"] if o["id"] == lid][0]["saved"]
    ok(client.post("/investor/opportunities/%s/interest" % lid, headers=I, json={"kind": "offer", "amount": 120500}))
    offers = ok(client.get("/investor/offers", headers=I))["offers"]
    assert offers[0]["offer"] == 120500 and offers[0]["listing_id"] == lid
    assert ok(client.get("/investor/dashboard", headers=I))["counts"]["offers"] == 1
    ok(client.post("/investor/opportunities/%s/exclusive-review" % lid, headers=I, json={"note": "lender"}))
    holds = ok(client.get("/investor/holds", headers=I))
    assert holds["holds"][0]["status"] == "requested" and "not an option" in holds["notice"]
    det = ok(client.get("/investor/opportunities/%s" % lid, headers=I))
    assert "signatures" in det


def test_hold_rules_from_settings_and_expiry_notifies_staff(client, admin_auth_headers, auth_headers, db_session):
    A, H = admin_auth_headers, auth_headers
    _setup(client, A, hold_require_note=True, hold_presets=["24h"], hold_allow_custom=False)
    deal_id, lid, I = _room_with_investor(client, A, H, db_session, street="11 Rules Rd")
    assert client.post("/investor/opportunities/%s/exclusive-review" % lid, headers=I, json={}).status_code == 400
    req = ok(client.post("/investor/opportunities/%s/exclusive-review" % lid, headers=I, json={"note": "inspection"}))
    assert client.post(DRS + "/holds/%s" % req["id"], headers=A, json={"decision": "approve", "preset": "48h"}).status_code == 400
    assert client.post(DRS + "/holds/%s" % req["id"], headers=A, json={"decision": "approve", "minutes": 90}).status_code == 400
    ok(client.post(DRS + "/holds/%s" % req["id"], headers=A, json={"decision": "approve", "preset": "24h"}))
    h = db_session.query(ExclusiveHold).filter(ExclusiveHold.id == req["id"]).first()
    h.ends_at = datetime.utcnow() - timedelta(seconds=5)
    db_session.commit()
    holds = ok(client.get(DRS + "/holds", headers=H))["holds"]
    assert [x for x in holds if x["id"] == req["id"]][0]["status"] == "ended"
    from app.models.models import Notification
    assert db_session.query(Notification).filter(Notification.message.like("Exclusive review ended%")).count() >= 1
    assert db_session.query(DealListing).filter(DealListing.id == lid).first().state == "published"


def test_ai_concierge_answers_only_from_authorized_records(client, admin_auth_headers, auth_headers, db_session,
                                                           monkeypatch):
    A, H = admin_auth_headers, auth_headers
    deal_id, lid, I = _room_with_investor(client, A, H, db_session, street="12 Grounded Ln")
    dt = ok(client.post("/investor/threads", headers=I, json={"kind": "deal", "listing_id": lid}))

    def grounded(kw):
        ctx = [m["content"] for m in kw["messages"] if m["content"].startswith("CONTEXT: ")][0]
        assert "Secret Seller Name" not in ctx and "98765" not in ctx                 # no seller, no contract price
        assert "12 Grounded Ln" in ctx
        return {"answer": "The asking price published for this property is $129,000.", "grounded": True,
                "wants_person": False}
    fake = FakeAI(grounded)
    monkeypatch.setattr(CH, "_get_client", lambda: fake)
    monkeypatch.setenv("AI_MANUAL_ACTIONS_ENABLED", "true")
    r = ok(client.post("/investor/threads/%s/messages" % dt["id"], headers=I, json={"text": "What is the asking price?"}))
    assert "$129,000" in r["reply"] and fake.calls
    assert fake.calls[0].get("model")                                                   # the gateway chose the model
    msgs = ok(client.get("/investor/threads/%s" % dt["id"], headers=I))["messages"]
    assert msgs[-1]["engine"] == "ai"
    # an invented figure is thrown away: the team is asked instead
    monkeypatch.setattr(CH, "_get_client", lambda: FakeAI({"answer": "Expect an 18% ROI and $2,450/mo cash flow.",
                                                           "grounded": True, "wants_person": False}))
    r = ok(client.post("/investor/threads/%s/messages" % dt["id"], headers=I, json={"text": "What return will I get?"}))
    assert "won't guess" in r["reply"] and "18%" not in r["reply"]
    # the AI is unavailable: the rules answer, labelled truthfully
    def boom(kw):
        raise RuntimeError("provider down")
    monkeypatch.setattr(CH, "_get_client", lambda: FakeAI(boom))
    r = ok(client.post("/investor/threads/%s/messages" % dt["id"], headers=I, json={"text": "What is the asking price?"}))
    assert "$129,000" in r["reply"]
    assert ok(client.get("/investor/threads/%s" % dt["id"], headers=I))["messages"][-1]["engine"] == "automatic"
    # the owner can switch the AI off
    _setup(client, A, ai_concierge_enabled=False)
    calls = FakeAI(grounded)
    monkeypatch.setattr(CH, "_get_client", lambda: calls)
    ok(client.post("/investor/threads/%s/messages" % dt["id"], headers=I, json={"text": "What is the asking price?"}))
    assert not calls.calls


def test_ai_capacity_limit_falls_back_to_rules(client, admin_auth_headers, auth_headers, db_session, monkeypatch):
    A, H = admin_auth_headers, auth_headers
    deal_id, lid, I = _room_with_investor(client, A, H, db_session, street="13 Capped Ct")
    dt = ok(client.post("/investor/threads", headers=I, json={"kind": "deal", "listing_id": lid}))
    fake = FakeAI({"answer": "The asking price published for this property is $129,000.", "grounded": True,
                   "wants_person": False})
    monkeypatch.setattr(CH, "_get_client", lambda: fake)
    monkeypatch.setenv("DEAL_ROOM_AI_PER_HOUR", "2")
    CH._AI_HITS.clear()
    for _ in range(4):
        ok(client.post("/investor/threads/%s/messages" % dt["id"], headers=I, json={"text": "What is the asking price?"}))
    assert len(fake.calls) == 2
    msgs = ok(client.get("/investor/threads/%s" % dt["id"], headers=I))["messages"]
    assert msgs[-1]["engine"] == "automatic"
    CH._AI_HITS.clear()


def test_investor_cannot_reach_owner_management(client, admin_auth_headers, auth_headers, db_session):
    A, H = admin_auth_headers, auth_headers
    deal_id, lid, I = _room_with_investor(client, A, H, db_session, street="14 Fence Way")
    for path in ("/wholesale/deal-room/settings", "/wholesale/deal-room/overview", "/wholesale/deal-room/investors",
                 "/wholesale/deal-room/portal-preview", "/wholesale/deal-room/candidates"):
        assert client.get(path, headers=I).status_code in (401, 403), path
    assert client.put(DRS + "/settings", headers=I, json={"name": "x"}).status_code in (401, 403)


def test_owner_dashboard_and_activity_feed_use_real_records(client, admin_auth_headers, auth_headers, db_session):
    A, H = admin_auth_headers, auth_headers
    empty = ok(client.get(DRS + "/dashboard?include_test=true", headers=A))
    assert empty["metrics"]["total_investors"] == 0 and empty["listings"] == []
    assert empty["growth"]["enough"] is False                                         # no invented history
    labels = {s["key"] if "key" in s else s["label"]: s for s in empty["status_items"]}
    assert labels and all("value" in s or "detail" in s for s in empty["status_items"])
    deal_id, lid, I = _room_with_investor(client, A, H, db_session, street="15 Feed Ave")
    d = ok(client.get(DRS + "/dashboard?include_test=true", headers=A))
    assert d["metrics"]["total_investors"] == 1 and d["metrics"]["active_listings"] == 1
    assert d["owner_first_name"] and d["listings"][0]["id"] == lid
    hidden = ok(client.get(DRS + "/dashboard", headers=A))                            # practice records excluded by default
    assert hidden["metrics"]["total_investors"] == 0
    ok(client.post("/investor/opportunities/%s/exclusive-review" % lid, headers=I, json={"note": "lender"}))
    rows = ok(client.get(DRS + "/feed?kind=holds&include_test=true", headers=A))["rows"]
    assert rows and rows[0]["status"] and rows[0]["deal"]["listing_id"] == lid and rows[0]["open"]["view"] == "holds"
    act = ok(client.get(DRS + "/feed?kind=activity&include_test=true", headers=A))["rows"]
    joined = [r for r in act if r["status"] == "Joined"]
    assert joined and joined[0]["investor"] != "Your team" and joined[0]["open"].get("buyer_id")
    assert ok(client.get(DRS + "/feed?kind=signups&include_test=true", headers=A))["rows"]
    assert client.get(DRS + "/feed?kind=bogus", headers=A).status_code in (200, 400)
    for path in ("/dashboard", "/feed?kind=activity"):
        assert client.get(DRS + path, headers=I).status_code in (401, 403)


def test_investor_criteria_extras_are_validated(client, admin_auth_headers, auth_headers, db_session):
    A, H = admin_auth_headers, auth_headers
    deal_id, lid, I = _room_with_investor(client, A, H, db_session, street="16 Criteria Rd")
    boxes = ok(client.get("/investor/me", headers=I))["buy_boxes"]
    box = boxes[0] if boxes else ok(client.post("/investor/me/buy-boxes", headers=I, json={"property_types": ["single_family"]}))
    box = box.get("box", box)
    ok(client.put("/investor/me/buy-boxes/%s" % box["id"], headers=I, json={
        "criteria": {"min_cash_flow": "500", "timeline": "0_30", "preferences": "  Turnkey rentals  "}}))
    box = ok(client.get("/investor/me", headers=I))["buy_boxes"][0]
    assert box["criteria"]["min_cash_flow"] == 500.0 and box["criteria"]["timeline"] == "0_30"
    assert box["criteria"]["preferences"] == "Turnkey rentals"
    bad = client.put("/investor/me/buy-boxes/%s" % box["id"], headers=I, json={"criteria": {"timeline": "someday"}})
    assert bad.status_code == 400
