"""The guards: tenant isolation, entitlement, opt-out, and failing providers.

These are the tests that would still matter if every screen were deleted. A
wholesale module that leaks one customer's buyer list into another's, or texts
somebody who said STOP, is worse than no wholesale module.
"""

import json

import pytest

from app.models.models import Organization, User
from app.services.auth_service import create_access_token, hash_password


def ok(response):
    assert response.status_code in (200, 201), \
        "%s %s" % (response.status_code, response.text[:400])
    return response.json()


@pytest.fixture()
def other_org(db_session):
    org = Organization(name="Other Wholesaler", slug="other-wholesaler",
                       plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    return org


@pytest.fixture()
def other_headers(db_session, other_org):
    user = User(organization_id=other_org.id, email="other@wholesale.test",
                password_hash=hash_password("TestPass123!"),
                full_name="Other Advisor", role="org_admin",
                must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(user, db_session)}


# ── Tenant isolation ────────────────────────────────────────────────────────

def test_one_tenants_deal_is_invisible_to_another(client, auth_headers, other_headers):
    mine = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "1 Mine St", "state": "TX",
                                "is_test": True}))
    deal_id = mine["deal"]["id"]

    # 404 rather than 403, so an id cannot be probed for existence from outside.
    assert client.get("/wholesale/deals/%s" % deal_id,
                      headers=other_headers).status_code == 404
    assert client.post("/wholesale/deals/%s/stage" % deal_id,
                       headers=other_headers,
                       json={"stage": "dead"}).status_code == 404

    listing = ok(client.get("/wholesale/properties", headers=other_headers,
                            params={"include_test": True}))
    assert listing["total"] == 0

    board = ok(client.get("/wholesale/dashboard", headers=other_headers,
                          params={"include_test": True}))
    assert board["properties_imported"] == 0


def test_a_buyer_list_does_not_cross_tenants(client, auth_headers, other_headers):
    ok(client.post("/wholesale/buyers", headers=auth_headers,
                   json={"company_name": "Mine Capital", "email": "m@example.com",
                         "is_test": True}))
    mine = ok(client.get("/wholesale/buyers", headers=auth_headers,
                         params={"include_test": True}))
    theirs = ok(client.get("/wholesale/buyers", headers=other_headers,
                           params={"include_test": True}))
    assert mine["total"] == 1
    assert theirs["total"] == 0


def test_each_tenant_gets_its_own_settings_row(client, auth_headers, other_headers):
    ok(client.patch("/wholesale/settings", headers=auth_headers,
                    json={"investor_percentage": 65}))
    mine = ok(client.get("/wholesale/settings", headers=auth_headers))
    theirs = ok(client.get("/wholesale/settings", headers=other_headers))
    assert mine["investor_percentage"] == 65
    assert theirs["investor_percentage"] == 70    # the default, untouched


# ── Entitlement ─────────────────────────────────────────────────────────────

def test_an_organization_without_the_module_is_refused_by_the_server(
        client, auth_headers, db_session, sample_org):
    """Hiding the nav item is not access control. This is."""
    sample_org.enabled_features = json.dumps(["leads"])
    db_session.commit()
    for method, path in (("get", "/wholesale/dashboard"),
                         ("get", "/wholesale/properties"),
                         ("get", "/wholesale/buyers"),
                         ("get", "/wholesale/settings")):
        response = getattr(client, method)(path, headers=auth_headers)
        assert response.status_code == 402, path
        assert "wholesale_real_estate" in response.json()["detail"]


def test_the_module_requires_leads_and_the_registry_says_so():
    from app.services.entitlements import REQUIRES, dependency_gaps

    assert REQUIRES["wholesale_real_estate"] == ("leads",)
    gaps = dependency_gaps(["wholesale_real_estate"])
    assert gaps and gaps[0]["requires"] == "leads"


def test_every_plan_preset_is_still_coherent():
    """The import-time assertion in entitlements.py, restated as a test."""
    from app.services.entitlements import PLAN_FEATURES, dependency_gaps

    for plan, keys in PLAN_FEATURES.items():
        if keys is None:
            continue
        assert dependency_gaps(keys) == [], plan


# ── Opt-out ─────────────────────────────────────────────────────────────────

def test_stop_is_recognised_without_the_ai_and_stops_everything(
        client, auth_headers, db_session):
    """Recognising an opt-out must not depend on a provider being up."""
    from app.models.models import Lead

    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "5 Stop St", "state": "TX",
                                "is_test": True}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Chris", "last_name": "Owner",
                         "phone": "2145559999"}))
    result = ok(client.post("/wholesale/deals/%s/seller-reply" % prop["deal"]["id"],
                            headers=auth_headers,
                            json={"message": "STOP. Do not text me again."}))

    assert result["reading"]["intent"] == "do_not_contact"
    assert result["reading"]["source"] == "rules"
    assert result["qualification"]["band"] == "excluded"

    room = ok(client.get("/wholesale/deals/%s" % prop["deal"]["id"],
                         headers=auth_headers))
    assert room["seller"]["is_dnc"] is True
    lead = db_session.query(Lead).filter(Lead.id == room["seller"]["lead_id"]).first()
    assert lead.status == "dnc"


def test_not_interested_kills_the_deal_rather_than_leaving_it_open(
        client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "6 No St", "state": "TX",
                                "is_test": True}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Jo", "last_name": "Owner",
                         "phone": "2145558888"}))
    result = ok(client.post("/wholesale/deals/%s/seller-reply" % prop["deal"]["id"],
                            headers=auth_headers,
                            json={"message": "Not interested, I'm not selling."}))
    assert result["stage"] == "dead"
    assert result["qualification"]["band"] == "excluded"


def test_an_opted_out_buyer_is_blocked_visibly_not_silently(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "7 Disp St", "state": "TX",
                                "is_test": True}))
    quiet = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Quiet Capital",
                                 "email": "q@example.com", "is_test": True,
                                 "do_not_contact": True,
                                 "do_not_contact_reason": "asked to stop"}))
    loud = ok(client.post("/wholesale/buyers", headers=auth_headers,
                          json={"company_name": "Loud Capital",
                                "email": "l@example.com", "is_test": True}))
    result = ok(client.post("/wholesale/deals/%s/disposition" % prop["deal"]["id"],
                            headers=auth_headers,
                            json={"buyer_ids": [quiet["id"], loud["id"]]}))
    # Both are refused here — the deal is a sandbox deal — but the REASONS differ,
    # and that is the point: the opted-out buyer is refused for opting out, by
    # name, not lumped in with the rest.
    assert result["sent"] == 0
    by_buyer = {r["buyer_id"]: r for r in result["results"]}
    assert by_buyer[quiet["id"]]["code"] == "sandbox"
    assert len(result["blocked"]) == 2

    # On a LIVE deal the opt-out is the reason, and the loud buyer gets as far
    # as the deployment switch — which is a different refusal with a different fix.
    live = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "7b Live Disp St", "state": "TX"}))
    live_quiet = ok(client.post("/wholesale/buyers", headers=auth_headers,
                                json={"company_name": "Quiet Live",
                                      "email": "ql@example.com",
                                      "do_not_contact": True,
                                      "do_not_contact_reason": "asked to stop"}))
    live_loud = ok(client.post("/wholesale/buyers", headers=auth_headers,
                               json={"company_name": "Loud Live",
                                     "email": "ll@example.com"}))
    result = ok(client.post("/wholesale/deals/%s/disposition" % live["deal"]["id"],
                            headers=auth_headers,
                            json={"buyer_ids": [live_quiet["id"], live_loud["id"]]}))
    by_buyer = {r["buyer_id"]: r for r in result["results"]}
    assert by_buyer[live_quiet["id"]]["code"] == "opted_out"
    assert "asked to stop" in by_buyer[live_quiet["id"]]["reason"]
    assert by_buyer[live_loud["id"]]["code"] == "not_enabled"
    assert result["sent"] == 0


def test_an_opted_out_buyer_is_not_even_matched(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "8 Match St", "state": "TX",
                                "is_test": True}))
    quiet = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Quiet Two", "email": "q2@example.com",
                                 "is_test": True, "do_not_contact": True}))
    ok(client.post("/wholesale/buyers/%s/buy-boxes" % quiet["id"],
                   headers=auth_headers, json={"states": ["TX"]}))
    matches = ok(client.post("/wholesale/deals/%s/match-buyers" % prop["deal"]["id"],
                             headers=auth_headers))["matches"]
    assert matches == []


# ── Providers that are not there ────────────────────────────────────────────

def test_enrichment_with_no_provider_never_fabricates_a_contact(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "10 Nobody St", "state": "TX",
                                "owner_name": "Unknown Owner", "is_test": True}))
    result = ok(client.post("/wholesale/enrichment/run", headers=auth_headers,
                            json={"property_ids": [prop["id"]]}))
    assert result["results"][0]["status"] == "manual"
    assert result["results"][0]["phones"] == []
    assert result["results"][0]["emails"] == []

    history = ok(client.get("/wholesale/properties/%s/enrichment" % prop["id"],
                            headers=auth_headers))
    assert history["requests"][0]["status"] == "manual"
    assert history["requests"][0]["billable"] is False


def test_a_failing_ai_still_qualifies_the_seller(client, auth_headers, monkeypatch):
    """An AI outage degrades the reading. It does not stop the pipeline."""
    from app.services import ai_gateway

    def boom(**kwargs):
        raise ai_gateway.ProviderCircuitOpen("provider is down")

    monkeypatch.setattr(ai_gateway, "chat_completion", boom)

    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "11 Outage St", "state": "TX",
                                "is_test": True}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Robin", "last_name": "Owner",
                         "phone": "2145557777"}))
    result = ok(client.post("/wholesale/deals/%s/seller-reply" % prop["deal"]["id"],
                            headers=auth_headers,
                            json={"message": "Yes, interested. Asking $95,000, asap."}))
    assert result["reading"]["source"] == "rules"
    assert result["reading"]["asking_price"] == 95000
    assert result["reading"]["timeline"] == "asap"
    assert result["qualification"]["band"] in ("high", "medium", "low", "review")


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]
        self.usage = None


class _FakeCompletions:
    def __init__(self, content):
        self._content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self._content)


class _FakeClient:
    def __init__(self, content):
        self.chat = type("chat", (), {"completions": _FakeCompletions(content)})()


def test_the_ai_reading_is_parsed_and_values_outside_the_vocabulary_are_dropped(
        client, auth_headers, monkeypatch):
    """The model's answer is filtered, not trusted.

    `timeline` here is a value the platform does not recognise, and an
    unrecognised string stored in that column would be read by the qualification
    engine as a timeline nobody scores — a silent wrong answer. It comes back
    as None instead.
    """
    from app.services import ai_gateway, wholesale_ai

    payload = json.dumps({
        "intent": "interested", "is_available": True, "considering_selling": True,
        "asking_price": "$187,500", "timeline": "sometime next decade",
        "property_condition": "poor", "occupancy": "vacant",
        "major_repairs": "roof and HVAC", "motivation": "relocating",
        "decision_makers": "me and my sister", "summary": "Owner wants to sell",
        "confidence": 90})
    fake = _FakeClient(payload)
    monkeypatch.setattr(ai_gateway, "manual_enabled", lambda: True)
    monkeypatch.setattr(wholesale_ai, "_get_client", lambda: fake)

    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "20 Parse St", "state": "TX",
                                "is_test": True}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Lee", "last_name": "Owner",
                         "phone": "2145554444"}))
    result = ok(client.post("/wholesale/deals/%s/seller-reply" % prop["deal"]["id"],
                            headers=auth_headers,
                            json={"message": "I'd sell it, roof is shot.",
                                  "mode": "manual"}))

    assert result["reading"]["source"] == "ai"
    assert result["reading"]["asking_price"] == 187500.0
    assert result["reading"]["property_condition"] == "poor"
    assert result["reading"]["timeline"] is None       # dropped, not stored
    assert result["qualification"]["score"] > 0

    # The gateway was asked for a capability, never for a model.
    sent = fake.chat.completions.calls[0]
    assert sent["model"] == "gpt-4o-mini"
    assert sent["temperature"] == 0


def test_the_rules_reader_overrides_an_ai_that_missed_an_opt_out(
        client, auth_headers, monkeypatch):
    """The one direction the cheaper reader always wins, because it is the one
    with a legal consequence."""
    from app.services import ai_gateway, wholesale_ai

    fake = _FakeClient(json.dumps({"intent": "interested", "confidence": 95,
                                   "summary": "Owner seems keen"}))
    monkeypatch.setattr(ai_gateway, "manual_enabled", lambda: True)
    monkeypatch.setattr(wholesale_ai, "_get_client", lambda: fake)

    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "21 Override St", "state": "TX",
                                "is_test": True}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Sam", "last_name": "Owner",
                         "phone": "2145553333"}))
    result = ok(client.post("/wholesale/deals/%s/seller-reply" % prop["deal"]["id"],
                            headers=auth_headers,
                            json={"message": "unsubscribe", "mode": "manual"}))
    assert result["reading"]["intent"] == "do_not_contact"
    # The model was never even consulted: a deterministic opt-out short-circuits
    # before the gateway is called.
    assert fake.chat.completions.calls == []


def test_an_unreadable_message_is_sent_to_a_person_not_scored_optimistically(
        client, auth_headers, monkeypatch):
    from app.services import ai_gateway

    monkeypatch.setattr(ai_gateway, "chat_completion",
                        lambda **kw: (_ for _ in ()).throw(ai_gateway.AIDisabled("off")))

    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "12 Huh St", "state": "TX",
                                "is_test": True}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Pat", "last_name": "Q", "phone": "2145556666"}))
    result = ok(client.post("/wholesale/deals/%s/seller-reply" % prop["deal"]["id"],
                            headers=auth_headers,
                            json={"message": "wat"}))
    assert result["reading"]["needs_human"] is True
    assert result["qualification"]["band"] == "review"


def test_outreach_to_a_sandbox_record_is_refused_before_anything_is_sent(
        client, auth_headers):
    """A rehearsal that could text a real phone would not be a rehearsal."""
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "17 Sandbox St", "state": "TX",
                                "is_test": True}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Test", "last_name": "Owner",
                         "phone": "2145552222"}))
    response = client.post("/wholesale/deals/%s/outreach" % prop["deal"]["id"],
                           headers=auth_headers,
                           json={"message": "Hi there", "channel": "sms"})
    assert response.status_code == 409
    assert "Nothing was sent" in response.json()["detail"]
    assert "test record" in response.json()["detail"]

    # And the refusal is recorded, not swallowed.
    events = ok(client.get("/wholesale/events", headers=auth_headers,
                           params={"deal_id": prop["deal"]["id"]}))["events"]
    assert any(e["action"] == "outreach.blocked" for e in events)


def test_outreach_to_an_opted_out_seller_is_refused(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "18 Stop St", "state": "TX",
                                "is_test": False}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Real", "last_name": "Owner",
                         "phone": "2145551111"}))
    ok(client.post("/wholesale/deals/%s/seller-reply" % prop["deal"]["id"],
                   headers=auth_headers, json={"message": "STOP"}))
    response = client.post("/wholesale/deals/%s/outreach" % prop["deal"]["id"],
                           headers=auth_headers, json={"message": "Hi again"})
    assert response.status_code == 409
    # The reason comes from test_records.blocked_reason, which is the platform's
    # own wording — asserted verbatim so this test fails if that gate is ever
    # replaced by a private copy inside the wholesale module.
    assert "DNC list" in response.json()["detail"]


def test_outreach_without_a_phone_number_is_refused_clearly(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "19 Nophone St", "state": "TX"}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Silent", "last_name": "Owner"}))
    response = client.post("/wholesale/deals/%s/outreach" % prop["deal"]["id"],
                           headers=auth_headers, json={"message": "Hi"})
    assert response.status_code == 400
    assert "No phone number" in response.json()["detail"]


# ── A seller is a lead, so a seller costs a lead ────────────────────────────
#
# The other half of the "reuse the Lead" decision. Inheriting DNC, consent and
# suppression for free also means inheriting the plan's active-lead allowance —
# a module that created sellers outside `plan_limits` would let an import walk
# straight through a package the customer is paying for. `tests/
# test_lead_capacity_matrix.py` is the platform-wide guard; these two are the
# behaviour it guards.

def test_attaching_a_seller_consumes_plan_capacity(client, auth_headers,
                                                   db_session, sample_org,
                                                   monkeypatch):
    from app.services import plan_limits

    # A plan with room for exactly one more lead. sample_lead is not created in
    # this test, so the org starts empty.
    monkeypatch.setattr(plan_limits, "limit_for",
                        lambda db, org, key: 1 if key == plan_limits.LIMIT_LEADS else None)

    first = ok(client.post("/wholesale/properties", headers=auth_headers,
                           json={"street_address": "30 Cap St", "state": "TX"}))
    ok(client.post("/wholesale/properties/%s/seller" % first["id"],
                   headers=auth_headers,
                   json={"first_name": "One", "last_name": "Owner",
                         "phone": "2145550011"}))

    second = ok(client.post("/wholesale/properties", headers=auth_headers,
                            json={"street_address": "31 Cap St", "state": "TX"}))
    response = client.post("/wholesale/properties/%s/seller" % second["id"],
                           headers=auth_headers,
                           json={"first_name": "Two", "last_name": "Owner",
                                 "phone": "2145550022"})
    assert response.status_code == 402
    assert "Upgrade the plan" in response.json()["detail"]


def test_an_import_past_the_allowance_keeps_the_properties_and_says_so(
        client, auth_headers, monkeypatch):
    """The limit stops the next addition; it does not throw away the file."""
    import io

    from app.services import plan_limits
    monkeypatch.setattr(plan_limits, "limit_for",
                        lambda db, org, key: 1 if key == plan_limits.LIMIT_LEADS else None)

    csv_text = (
        "Address,City,State,Phone\n"
        "40 Cap Way,Dallas,TX,2145550040\n"
        "41 Cap Way,Dallas,TX,2145550041\n"
        "42 Cap Way,Dallas,TX,2145550042\n")
    body = ok(client.post(
        "/wholesale/properties/import", headers=auth_headers,
        files={"file": ("cap.csv", io.BytesIO(csv_text.encode()), "text/csv")}))

    # Every property was created — properties are not leads and cost nothing.
    assert body["created"] == 3
    # One owner fitted; the other two are reported, not silently dropped.
    assert len(body["capacity_blocked"]) == 2
    assert "allowance is full" in body["capacity_note"]

    listing = ok(client.get("/wholesale/properties", headers=auth_headers))
    stages = {p["street_address"]: p["deal"]["stage"] for p in listing["properties"]}
    assert stages["40 Cap Way"] == "ready_for_outreach"
    assert stages["41 Cap Way"] == "enrichment_needed"
    assert stages["42 Cap Way"] == "enrichment_needed"


# ── Nothing binds without a person ──────────────────────────────────────────

def test_the_gates_can_be_switched_off_by_the_customer_and_only_by_them(
        client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "13 Gate St", "state": "TX",
                                "is_test": True}))
    deal_id = prop["deal"]["id"]
    assert client.post("/wholesale/deals/%s/stage" % deal_id, headers=auth_headers,
                       json={"stage": "offer_sent"}).status_code == 409

    ok(client.patch("/wholesale/settings", headers=auth_headers,
                    json={"require_offer_approval": False}))
    assert client.post("/wholesale/deals/%s/stage" % deal_id, headers=auth_headers,
                       json={"stage": "offer_sent"}).status_code == 200


def test_an_unknown_stage_is_refused_with_the_list_of_real_ones(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "14 Bad St", "state": "TX",
                                "is_test": True}))
    response = client.post("/wholesale/deals/%s/stage" % prop["deal"]["id"],
                           headers=auth_headers, json={"stage": "make_it_so"})
    assert response.status_code == 400
    assert "Configured stages" in response.json()["detail"]


def test_an_approval_cannot_be_decided_twice(client, auth_headers):
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "15 Once St", "state": "TX",
                                "is_test": True}))
    approval = ok(client.post("/wholesale/deals/%s/approvals" % prop["deal"]["id"],
                              headers=auth_headers, json={"kind": "offer"}))
    ok(client.post("/wholesale/approvals/%s/decide" % approval["id"],
                   headers=auth_headers, json={"approve": True}))
    second = client.post("/wholesale/approvals/%s/decide" % approval["id"],
                         headers=auth_headers, json={"approve": False})
    assert second.status_code == 409


# ── Configuration validation ────────────────────────────────────────────────

def test_an_impossible_threshold_pair_is_refused(client, auth_headers):
    response = client.patch("/wholesale/settings", headers=auth_headers,
                            json={"high_threshold": 40, "medium_threshold": 60})
    assert response.status_code == 400
    assert "above the MEDIUM" in response.json()["detail"]


def test_an_impossible_buy_box_is_refused(client, auth_headers):
    buyer = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Impossible", "email": "i@example.com",
                                 "is_test": True}))
    response = client.post("/wholesale/buyers/%s/buy-boxes" % buyer["id"],
                           headers=auth_headers,
                           json={"min_price": 500000, "max_price": 100000})
    assert response.status_code == 400
    assert "nothing could ever match" in response.json()["detail"]


def test_a_buyer_with_no_way_to_be_contacted_is_refused(client, auth_headers):
    response = client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Ghost Capital"})
    assert response.status_code == 400
    assert "email or a phone" in response.json()["detail"]


def test_a_custom_pipeline_is_stored_and_used(client, auth_headers):
    stages = [{"key": "sourced", "label": "Sourced", "order": 10},
              {"key": "working", "label": "Working", "order": 20},
              {"key": "closed", "label": "Closed", "order": 30, "terminal": True}]
    settings = ok(client.patch("/wholesale/settings", headers=auth_headers,
                               json={"pipeline_stages": stages}))
    assert [s["key"] for s in settings["pipeline_stages_effective"]] == \
        ["sourced", "working", "closed"]

    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "16 Custom St", "state": "TX",
                                "is_test": True}))
    moved = ok(client.post("/wholesale/deals/%s/stage" % prop["deal"]["id"],
                           headers=auth_headers, json={"stage": "working"}))
    assert moved["stage"] == "working"
    assert moved["stage_label"] == "Working"
    # A default stage this organization no longer has is refused.
    assert client.post("/wholesale/deals/%s/stage" % prop["deal"]["id"],
                       headers=auth_headers,
                       json={"stage": "under_contract"}).status_code == 400


def test_an_empty_pipeline_is_refused(client, auth_headers):
    response = client.patch("/wholesale/settings", headers=auth_headers,
                            json={"pipeline_stages": []})
    assert response.status_code == 400
