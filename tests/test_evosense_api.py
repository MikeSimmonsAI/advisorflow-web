"""EvoSense over HTTP: gates, tenant isolation, roles, lifecycle, and what must
never leave the workspace (Investor Deal Room, Seller Portal, credentials)."""
import json
import re
import time

import pytest

from app.models.evosense_models import EvoSenseProperty, EvoSenseStrategy
from app.models.models import Organization, User
from app.services.auth_service import create_access_token, hash_password
from app.services.evosense import sandbox_seed as SS


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:500])
    return r.json()


@pytest.fixture()
def seeded(db_session, sample_org, sample_advisor):
    out = SS.seed_review(db_session, sample_org.id, sample_advisor, replies=True)
    db_session.commit()
    return out


@pytest.fixture()
def org_b(db_session):
    org = Organization(name="Org B Wholesale", slug="org-b-wholesale", plan="standard",
                       industry="real_estate")
    db_session.add(org)
    db_session.commit()
    user = User(organization_id=org.id, email="b@orgb.test", password_hash=hash_password("TestPass123!"),
                full_name="B Admin", role="org_admin", must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return org, user, {"Authorization": "Bearer %s" % create_access_token(user, db_session)}


def _flagship_id(db, org_id):
    return SS.prop_at(db, org_id, "1418 Cedar Springs Rd").id


# ── gates ───────────────────────────────────────────────────────────────────

def test_without_the_wholesale_module_every_evosense_route_is_refused(client, auth_headers, db_session,
                                                                     sample_org):
    sample_org.enabled_features = json.dumps(["leads"])
    db_session.commit()
    for path in ("/wholesale/evosense/command-center", "/wholesale/evosense/inbox",
                 "/wholesale/evosense/strategies", "/wholesale/evosense/providers"):
        r = client.get(path, headers=auth_headers)
        assert r.status_code == 402, path


def test_unauthenticated_is_refused(client):
    assert client.get("/wholesale/evosense/command-center").status_code in (401, 403)


# ── screens ─────────────────────────────────────────────────────────────────

def test_command_center_inbox_and_property_pages(client, auth_headers, db_session, sample_org, seeded):
    cc = ok(client.get("/wholesale/evosense/command-center", headers=auth_headers))
    assert cc["sandbox"]["banner"] and cc["happened"]["hunts"] == 2
    assert cc["spent"]["today_cents"] > 0 and cc["spent"]["note"]
    assert any(n["address"] == "1418 Cedar Springs Rd" for n in cc["needs_you"])
    buckets = {b["key"]: b["count"] for b in cc["buckets"]}
    for k in ("needs_you", "budget_blocked", "waiting_for_data", "suppressed", "nurture",
              "needs_review", "closed_out"):
        assert buckets[k] >= 1, k
    inbox = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers,
                          params={"bucket": "budget_blocked"}))
    assert inbox["total"] == 2 and all(i["is_test"] for i in inbox["items"])
    pid = _flagship_id(db_session, sample_org.id)
    d = ok(client.get("/wholesale/evosense/properties/%s" % pid, headers=auth_headers))
    assert d["scores"]["property_opportunity"]["factors"]
    assert d["scores"]["seller_intent"]["value"] >= 70
    assert {f["fact_type"] for f in d["seller_facts"]} >= {"asking_price", "estate_context"}
    assert all(f["truth"] == "SELLER STATED" for f in d["seller_facts"])
    assert d["contacts"][0]["connector_label"] == "SANDBOX"
    assert d["spent_cents"] >= 18 and d["economics"]["mao"] is None     # no verified ARV, no MAO
    assert d["economics"]["arv"]["label"] == "Insufficient comparable sales"
    assert d["handoff"]["status"] == "open"


def test_inbox_filters_search_and_paging(client, auth_headers, seeded):
    all_ = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"limit": 5}))
    assert len(all_["items"]) == 5 and all_["total"] > 5
    page2 = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"limit": 5, "offset": 5}))
    assert {i["id"] for i in page2["items"]}.isdisjoint({i["id"] for i in all_["items"]})
    q = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"q": "Cedar"}))
    assert [i["address"] for i in q["items"]] == ["1418 Cedar Springs Rd"]
    sig = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers, params={"signal": "PROBATE"}))
    assert sig["total"] == 3


# ── the HTTP journey: promote ───────────────────────────────────────────────

def test_promote_over_http_and_the_rooms_never_see_acquisition_intel(client, auth_headers, db_session,
                                                                    sample_org, seeded):
    pid = _flagship_id(db_session, sample_org.id)
    out = ok(client.post("/wholesale/evosense/properties/%s/promote" % pid, headers=auth_headers, json={}))
    again = ok(client.post("/wholesale/evosense/properties/%s/promote" % pid, headers=auth_headers, json={}))
    assert again["already"] and again["deal_id"] == out["deal_id"]
    deal = ok(client.get("/wholesale/deals/%s" % out["deal_id"], headers=auth_headers))
    assert deal["deal"]["stage"] == "seller_engaged"
    assert deal["property"]["is_test"] is True
    assert float(deal["seller"]["asking_price"]) == 150000

    # Publish both rooms with the most permissive fields the operator has, and
    # read them as an outsider would.
    ok(client.patch("/wholesale/deals/%s/publication" % out["deal_id"], headers=auth_headers,
                    json={"buyer_room_summary": "Three bed in Dallas."}))
    for audience in ("buyer", "seller"):
        ok(client.post("/wholesale/deals/%s/publication/state" % out["deal_id"], headers=auth_headers,
                       json={"audience": audience, "published": True}))
    buyer = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Room Test Capital", "email": "r@example.com",
                                 "is_test": True}))
    blink = ok(client.post("/wholesale/deals/%s/share-links" % out["deal_id"], headers=auth_headers,
                           json={"audience": "buyer", "buyer_id": buyer["id"]}))
    slink = ok(client.post("/wholesale/deals/%s/share-links" % out["deal_id"], headers=auth_headers,
                           json={"audience": "seller"}))
    rooms = {"investor": client.get("/wholesale-rooms/buyer/%s" % blink["token"]),
             "seller": client.get("/wholesale-rooms/seller/%s" % slink["token"])}
    forbidden = ["evosense", "seller_intent", "opportunity", "contact_confidence", "signal",
                 "TAX_DELINQUENT", "Tax delinquent", "ABSENTEE", "skiptrace", "sandbox_",
                 "inherited", "Inherited", "+12145550142", "2145550142", "mao", "acquisition_cost",
                 "cost_cents", "0.18", "Evelyn", "Harper", "motivation"]
    for name, r in rooms.items():
        assert r.status_code == 200, (name, r.text[:300])
        body = r.text
        for word in forbidden:
            if name == "seller" and word in ("Evelyn", "Harper"):
                continue          # the seller is looking at their own page
            assert word not in body, "%s room leaks %r" % (name, word)


def test_room_modules_do_not_import_evosense():
    import pathlib
    for f in ("app/routers/wholesale_rooms_router.py", "app/services/wholesale_publication.py"):
        p = pathlib.Path(f)
        if p.exists():
            assert "evosense" not in p.read_text(encoding="utf-8").lower(), f


# ── tenant isolation ────────────────────────────────────────────────────────

def test_two_organizations_share_nothing(client, auth_headers, db_session, sample_org, sample_advisor,
                                         seeded, org_b):
    org, user, hb = org_b
    org.enabled_features = sample_org.enabled_features
    db_session.commit()
    pid = _flagship_id(db_session, sample_org.id)
    assert client.get("/wholesale/evosense/properties/%s" % pid, headers=hb).status_code == 404
    assert client.post("/wholesale/evosense/properties/%s/promote" % pid, headers=hb,
                       json={}).status_code == 404
    assert ok(client.get("/wholesale/evosense/inbox", headers=hb))["total"] == 0
    sid = seeded["strategies"]["dfw"]
    assert client.get("/wholesale/evosense/strategies/%s" % sid, headers=hb).status_code == 404
    assert client.post("/wholesale/evosense/strategies/%s/hunt" % sid, headers=hb).status_code == 404
    # Org B hunts the SAME synthetic market: it gets its own rows, and none of
    # Org A's suppression, conversations or spend.
    SS.seed_review(db_session, org.id, user, replies=False)
    a = {p.id for p in db_session.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == sample_org.id)}
    b = {p.id for p in db_session.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org.id)}
    assert a and b and a.isdisjoint(b)
    flag_b = SS.prop_at(db_session, org.id, "1418 Cedar Springs Rd")
    assert flag_b.seller_intent is None, "Org A's conversation is not Org B's"
    cc_b = ok(client.get("/wholesale/evosense/command-center", headers=hb))
    assert cc_b["needs_you"] == []


# ── roles, lifecycle, controls ──────────────────────────────────────────────

def test_strategy_lifecycle_over_http(client, admin_auth_headers, auth_headers):
    body = {"name": "Builder test", "counties": ["Dallas"], "states": ["TX"],
            "property_types": ["single_family"], "min_value": 100000, "max_value": 400000,
            "min_equity_pct": 35, "preferred_signals": ["VACANT", "TAX_DELINQUENT"],
            "daily_budget_cents": 500, "handoff_intent_threshold": 70, "is_test": True}
    prev = ok(client.post("/wholesale/evosense/strategies/preview", headers=auth_headers, json=body))
    assert "Dallas" in prev["summary"] and "$5/day" in prev["summary"] and not prev["problems"]
    assert client.post("/wholesale/evosense/strategies", headers=auth_headers,
                       json=body).status_code == 403, "an advisor cannot give a strategy a budget"
    s = ok(client.post("/wholesale/evosense/strategies", headers=admin_auth_headers, json=body))
    assert s["status"] == "draft" and s["version"] == 1
    bad = ok(client.post("/wholesale/evosense/strategies", headers=admin_auth_headers,
                         json={"name": "Nowhere", "property_types": ["single_family"]}))
    assert client.post("/wholesale/evosense/strategies/%s/activate" % bad["id"],
                       headers=admin_auth_headers).status_code == 409
    assert ok(client.post("/wholesale/evosense/strategies/%s/activate" % s["id"],
                          headers=admin_auth_headers))["status"] == "active"
    e = ok(client.patch("/wholesale/evosense/strategies/%s" % s["id"], headers=admin_auth_headers,
                        json={"min_equity_pct": 40}))
    assert e["version"] == 2
    assert client.patch("/wholesale/evosense/strategies/%s" % s["id"], headers=admin_auth_headers,
                        json={"required_signals": ["VACANT"], "excluded_signals": ["VACANT"]}).status_code == 422
    c = ok(client.post("/wholesale/evosense/strategies/%s/clone" % s["id"], headers=admin_auth_headers))
    assert c["status"] == "draft" and c["name"].endswith("(copy)")
    assert ok(client.post("/wholesale/evosense/strategies/%s/pause" % s["id"],
                          headers=admin_auth_headers))["status"] == "paused"
    assert ok(client.post("/wholesale/evosense/strategies/%s/resume" % s["id"],
                          headers=admin_auth_headers))["status"] == "active"
    assert ok(client.post("/wholesale/evosense/strategies/%s/archive" % s["id"],
                          headers=admin_auth_headers))["status"] == "archived"
    listed = ok(client.get("/wholesale/evosense/strategies", headers=auth_headers,
                           params={"include_archived": True}))
    assert s["id"] in {x["id"] for x in listed["items"]}, "archived, never deleted"


def test_anyone_can_pause_only_an_admin_can_resume_or_spend(client, auth_headers, admin_auth_headers):
    ok(client.patch("/wholesale/evosense/controls", headers=auth_headers, json={"paused_all": True}))
    assert client.patch("/wholesale/evosense/controls", headers=auth_headers,
                        json={"paused_all": False}).status_code == 403
    assert client.patch("/wholesale/evosense/controls", headers=auth_headers,
                        json={"org_daily_budget_cents": 10000}).status_code == 403
    assert ok(client.patch("/wholesale/evosense/controls", headers=admin_auth_headers,
                           json={"paused_all": False}))["paused_all"] is False
    assert client.patch("/wholesale/evosense/providers", headers=auth_headers,
                        json={"key": "sandbox_skiptrace", "enabled": True}).status_code == 403


def test_provider_payload_never_contains_credentials(client, auth_headers, seeded, monkeypatch):
    monkeypatch.setenv("SOME_VENDOR_API_KEY", "sk_live_do_not_leak_123")
    body = client.get("/wholesale/evosense/providers", headers=auth_headers).text
    assert "sk_live_do_not_leak_123" not in body
    data = json.loads(body)
    for p in data["providers"]:
        for k in p:
            assert not re.search(r"(secret|token|password|api_?key|credential)", k, re.I), k
    kinds = {c["capability"]: c["label"] for c in data["capabilities"]}
    assert kinds["CONTACT_ENRICHMENT"] == "SANDBOX" and kinds["COMPS"] == "INTERFACE ONLY"
    assert kinds["EMAIL_VALIDATION"] == "INTERFACE ONLY" and kinds["ENTITY_RESOLUTION"] == "INTERFACE ONLY"
    assert kinds["LISTING"] == "MANUAL"
    assert data["real_connectors"] == []


def test_simulated_replies_only_for_sandbox_and_manual_contact_fallback(client, admin_auth_headers,
                                                                        db_session, sample_org, seeded):
    real = ok(client.post("/wholesale/evosense/properties", headers=admin_auth_headers,
                          json={"street_address": "77 Real Ave", "city": "Dallas", "state": "TX",
                                "zip_code": "75201", "owner_name": "Pat Real",
                                "mailing_street": "1 Other St", "mailing_zip": "78701",
                                "mailing_state": "TX", "signals": ["VACANT"]}))
    pid = real["property_id"]
    assert client.post("/wholesale/evosense/properties/%s/reply" % pid, headers=admin_auth_headers,
                       json={"text": "yes", "delivery": "sandbox_simulated"}).status_code == 409
    added = ok(client.post("/wholesale/evosense/properties/%s/contacts" % pid, headers=admin_auth_headers,
                           json={"kind": "phone", "value": "(214) 555-0199", "person_name": "Pat Real"}))
    assert added["contacts"]
    d = ok(client.get("/wholesale/evosense/properties/%s" % pid, headers=admin_auth_headers))
    assert d["contacts"][0]["connector_label"] == "MANUAL"
    assert not d["property"]["is_test"]


def test_feedback_is_recorded_and_bad_kinds_refused(client, auth_headers, db_session, sample_org, seeded):
    pid = _flagship_id(db_session, sample_org.id)
    ok(client.post("/wholesale/evosense/properties/%s/feedback" % pid, headers=auth_headers,
                   json={"kind": "GOOD_FIND", "reason": "exactly the kind we want"}))
    assert client.post("/wholesale/evosense/properties/%s/feedback" % pid, headers=auth_headers,
                       json={"kind": "LOVE_IT"}).status_code == 422
    d = ok(client.get("/wholesale/evosense/properties/%s" % pid, headers=auth_headers))
    assert d["feedback"][0]["kind"] == "GOOD_FIND"


def test_nurture_presets_over_http(client, auth_headers, db_session, sample_org, seeded):
    p = SS.prop_at(db_session, sample_org.id, "6120 Wedgwood Dr")
    r = ok(client.post("/wholesale/evosense/properties/%s/nurture" % p.id, headers=auth_headers,
                       json={"choice": "90_days", "reason": "call back in spring"}))
    assert r["status"] == "nurture"
    assert client.post("/wholesale/evosense/properties/%s/nurture" % p.id, headers=auth_headers,
                       json={"choice": "date", "date": "2001-01-01"}).status_code == 422


# ── scale ───────────────────────────────────────────────────────────────────

def test_inbox_stays_fast_and_bounded_at_scale(client, auth_headers, db_session, sample_org):
    rows = [EvoSenseProperty(organization_id=sample_org.id, street_address="%d Scale St" % i,
                             city="Dallas", state="TX", zip_code="75201", status="low_opportunity"
                             if i % 3 else "high_opportunity", opportunity_score=i % 100,
                             is_test=True) for i in range(6000)]
    db_session.bulk_save_objects(rows)
    db_session.commit()
    t = time.time()
    r = ok(client.get("/wholesale/evosense/inbox", headers=auth_headers,
                      params={"bucket": "high_opportunity", "limit": 50}))
    assert time.time() - t < 3.0
    assert r["total"] == 2000 and len(r["items"]) == 50
    assert r["items"][0]["opportunity_score"] == 99
    t = time.time()
    ok(client.get("/wholesale/evosense/command-center", headers=auth_headers))
    assert time.time() - t < 3.0
