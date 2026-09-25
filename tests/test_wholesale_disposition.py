"""Buyer disposition — the six refusals, the real send, and the privacy promise.

NOTHING IN THIS FILE REACHES A PROVIDER. The one test that exercises a
successful send patches `email_service.send_email` with a fake that records what
it was asked to do; every other test asserts a refusal that happens BEFORE any
provider is touched. A test suite that could mail somebody is a test suite
nobody can run twice.
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
def enabled(monkeypatch):
    """Turn the deployment switch on for the tests that are about what happens
    AFTER it. Everything else runs with it off, which is the default and the
    state a fresh deployment is in."""
    monkeypatch.setenv("OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION", "true")


class FakeProvider:
    """Stands in for Resend. Records, never sends."""

    def __init__(self, success=True, error=None):
        self.success = success
        self.error = error
        self.calls = []

    def __call__(self, db=None, org_id=None, to_email=None, to_name=None,
                 subject=None, body=None, **kw):
        self.calls.append({"to_email": to_email, "to_name": to_name,
                           "subject": subject, "body": body, "org_id": org_id})
        if self.success:
            return {"success": True, "provider_message_id": "fake-msg-1",
                    "error": None}
        return {"success": False, "provider_message_id": None,
                "error": self.error or "provider said no"}


@pytest.fixture()
def live_deal(client, auth_headers):
    """A NON-sandbox deal with one contactable buyer, ready to dispose."""
    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "100 Disposition Way",
                                "city": "Dallas", "state": "TX",
                                "zip_code": "75201", "county": "Dallas",
                                "property_type": "single_family",
                                "bedrooms": 3, "bathrooms": 2,
                                "square_feet": 1500}))
    ok(client.post("/wholesale/properties/%s/seller" % prop["id"],
                   headers=auth_headers,
                   json={"first_name": "Dana", "last_name": "Seller",
                         "phone": "2145557001", "email": "dana.seller@example.com",
                         "motivation": "relocating for work, wants it gone fast",
                         "asking_price": 120000}))
    ok(client.patch("/wholesale/deals/%s/analysis" % prop["deal"]["id"],
                    headers=auth_headers,
                    json={"arv": 300000, "repair_estimate": 40000,
                          "proposed_offer": 150000}))
    buyer = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Live Buyer Capital",
                                 "email": "live.buyer@example.com",
                                 "phone": "2145558001"}))
    return {"property": prop, "deal_id": prop["deal"]["id"], "buyer": buyer}


# ── The switch ──────────────────────────────────────────────────────────────

def test_with_the_switch_off_nothing_is_sent_and_the_variable_is_named(
        client, auth_headers, live_deal):
    result = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                            headers=auth_headers,
                            json={"buyer_ids": [live_deal["buyer"]["id"]]}))
    assert result["sent"] == 0
    row = result["results"][0]
    assert row["code"] == "not_enabled"
    assert "OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION" in row["reason"]


def test_the_channel_status_endpoint_reports_the_switch(client, auth_headers):
    body = ok(client.get("/wholesale/disposition/channels", headers=auth_headers))
    by_channel = {c["channel"]: c for c in body["channels"]}
    assert by_channel["email"]["enabled"] is False
    assert by_channel["email"]["env"] == "OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION"
    assert by_channel["sms"]["env"] == "OUTBOUND_SMS_WHOLESALE_BUYER_DISPOSITION"


def test_with_the_switch_on_it_reports_ready(client, auth_headers, enabled):
    body = ok(client.get("/wholesale/disposition/channels", headers=auth_headers))
    by_channel = {c["channel"]: c for c in body["channels"]}
    assert by_channel["email"]["enabled"] is True
    assert by_channel["email"]["status"] == "ready"


# ── The real send ───────────────────────────────────────────────────────────

def test_an_enabled_send_reaches_the_provider_and_records_what_came_back(
        client, auth_headers, live_deal, enabled, monkeypatch):
    from app.services import email_service

    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)

    result = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                            headers=auth_headers,
                            json={"buyer_ids": [live_deal["buyer"]["id"]],
                                  "asking_price": 165000}))
    assert result["sent"] == 1
    row = result["results"][0]
    assert row["sent"] is True
    assert row["status"] == "sent"
    assert row["provider_message_id"] == "fake-msg-1"

    assert len(fake.calls) == 1
    assert fake.calls[0]["to_email"] == "live.buyer@example.com"
    assert fake.calls[0]["subject"]
    assert fake.calls[0]["body"]

    room = ok(client.get("/wholesale/deals/%s" % live_deal["deal_id"],
                         headers=auth_headers))
    outreach = room["buyer_outreach"][0]
    assert outreach["status"] == "sent"
    assert outreach["sent_at"]


def test_a_provider_failure_is_recorded_as_a_failure_not_rounded_up(
        client, auth_headers, live_deal, enabled, monkeypatch):
    from app.services import email_service

    monkeypatch.setattr(email_service, "send_email",
                        FakeProvider(success=False, error="domain not verified"))

    result = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                            headers=auth_headers,
                            json={"buyer_ids": [live_deal["buyer"]["id"]]}))
    assert result["sent"] == 0
    row = result["results"][0]
    assert row["code"] == "provider_error"
    assert "domain not verified" in row["reason"]

    room = ok(client.get("/wholesale/deals/%s" % live_deal["deal_id"],
                         headers=auth_headers))
    assert room["buyer_outreach"][0]["status"] == "failed"


def test_a_provider_that_raises_is_still_a_failure_not_a_crash(
        client, auth_headers, live_deal, enabled, monkeypatch):
    from app.services import email_service

    def boom(**kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(email_service, "send_email", boom)
    result = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                            headers=auth_headers,
                            json={"buyer_ids": [live_deal["buyer"]["id"]]}))
    assert result["sent"] == 0
    assert "connection reset" in result["results"][0]["reason"]


# ── Retry safety ────────────────────────────────────────────────────────────

def test_sending_twice_does_not_mail_the_buyer_twice(
        client, auth_headers, live_deal, enabled, monkeypatch):
    """A double-click is not a second email."""
    from app.services import email_service

    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)

    payload = {"buyer_ids": [live_deal["buyer"]["id"]]}
    first = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                           headers=auth_headers, json=payload))
    second = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                            headers=auth_headers, json=payload))

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert second["results"][0]["code"] == "already_sent"
    assert len(fake.calls) == 1


def test_resend_is_the_deliberate_way_past_that_guard(
        client, auth_headers, live_deal, enabled, monkeypatch):
    from app.services import email_service

    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)

    first = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                           headers=auth_headers,
                           json={"buyer_ids": [live_deal["buyer"]["id"]]}))
    outreach_id = first["results"][0]["outreach_id"]

    again = ok(client.post("/wholesale/outreach/%s/resend" % outreach_id,
                           headers=auth_headers))
    assert again["sent"] is True
    assert again["attempts"] == 2
    assert len(fake.calls) == 2


def test_one_row_per_buyer_however_many_times_it_is_composed(
        client, auth_headers, live_deal):
    payload = {"buyer_ids": [live_deal["buyer"]["id"]]}
    for _ in range(3):
        client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                    headers=auth_headers, json=payload)
    room = ok(client.get("/wholesale/deals/%s" % live_deal["deal_id"],
                         headers=auth_headers))
    assert len(room["buyer_outreach"]) == 1


# ── The refusals, each on its own ───────────────────────────────────────────

def test_sandbox_blocks_the_send_before_any_provider_is_touched(
        client, auth_headers, enabled, monkeypatch):
    from app.services import email_service

    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)

    prop = ok(client.post("/wholesale/properties", headers=auth_headers,
                          json={"street_address": "101 Sandbox Way", "state": "TX",
                                "is_test": True}))
    buyer = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Sandbox Buyer",
                                 "email": "sb@example.com", "is_test": True}))
    result = ok(client.post("/wholesale/deals/%s/disposition" % prop["deal"]["id"],
                            headers=auth_headers, json={"buyer_ids": [buyer["id"]]}))
    assert result["sent"] == 0
    assert result["results"][0]["code"] == "sandbox"
    assert fake.calls == []


def test_a_sandbox_buyer_blocks_a_live_deal_too(
        client, auth_headers, live_deal, enabled, monkeypatch):
    from app.services import email_service

    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)

    test_buyer = ok(client.post("/wholesale/buyers", headers=auth_headers,
                                json={"company_name": "Test Buyer",
                                      "email": "tb@example.com", "is_test": True}))
    result = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                            headers=auth_headers,
                            json={"buyer_ids": [test_buyer["id"]]}))
    assert result["results"][0]["code"] == "sandbox"
    assert fake.calls == []


def test_an_opted_out_buyer_is_refused_by_name(
        client, auth_headers, live_deal, enabled, monkeypatch):
    from app.services import email_service

    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)

    quiet = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Quiet Capital",
                                 "email": "quiet@example.com",
                                 "do_not_contact": True,
                                 "do_not_contact_reason": "asked to stop in March"}))
    result = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                            headers=auth_headers, json={"buyer_ids": [quiet["id"]]}))
    assert result["results"][0]["code"] == "opted_out"
    assert "asked to stop in March" in result["results"][0]["reason"]
    assert fake.calls == []


def test_a_buyer_with_no_address_on_the_chosen_channel_is_refused(
        client, auth_headers, live_deal, enabled):
    result = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                            headers=auth_headers,
                            json={"buyer_ids": [live_deal["buyer"]["id"]],
                                  "channel": "sms"}))
    # This buyer HAS a phone, so the refusal is the SMS switch, not the address.
    assert result["results"][0]["code"] == "not_enabled"
    assert "OUTBOUND_SMS_WHOLESALE_BUYER_DISPOSITION" in result["results"][0]["reason"]


def test_a_phone_channel_is_refused_outright(client, auth_headers, live_deal):
    response = client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                           headers=auth_headers,
                           json={"buyer_ids": [live_deal["buyer"]["id"]],
                                 "channel": "phone"})
    assert response.status_code == 400


def test_a_hundred_buyers_is_the_limit_on_one_deliberate_action(
        client, auth_headers, live_deal):
    response = client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                           headers=auth_headers,
                           json={"buyer_ids": ["x"] * 101})
    assert response.status_code == 400
    assert "deliberate" in response.json()["detail"]


# ── The privacy promise ─────────────────────────────────────────────────────

SELLER_SECRETS = ["Dana", "Seller", "2145557001", "dana.seller@example.com",
                  "relocating for work, wants it gone fast", "120000"]


def test_the_deal_sheet_carries_no_seller_information(
        client, auth_headers, live_deal, enabled, monkeypatch):
    """Asserted against what the PROVIDER was handed, not against the preview.

    A preview that is clean while the sent body is not would be the worst
    possible version of this bug, so the check is on the real payload.
    """
    from app.services import email_service
    from app.services.wholesale_disposition import seller_leak_check

    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)

    ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                   headers=auth_headers,
                   json={"buyer_ids": [live_deal["buyer"]["id"]],
                         "asking_price": 165000}))

    body = fake.calls[0]["body"] + " " + (fake.calls[0]["subject"] or "")
    leaked = seller_leak_check(body, SELLER_SECRETS)
    assert leaked == [], "buyer deal sheet leaked seller data: %s" % leaked


def test_the_preview_is_the_same_body_that_gets_sent(
        client, auth_headers, live_deal, enabled, monkeypatch):
    from app.services import email_service

    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)

    preview = ok(client.post(
        "/wholesale/deals/%s/disposition/preview" % live_deal["deal_id"],
        headers=auth_headers,
        json={"buyer_ids": [live_deal["buyer"]["id"]], "asking_price": 165000}))
    ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                   headers=auth_headers,
                   json={"buyer_ids": [live_deal["buyer"]["id"]],
                         "asking_price": 165000}))
    assert fake.calls[0]["body"] == preview["body"]
    assert fake.calls[0]["subject"] == preview["subject"]


def test_the_stored_outreach_row_carries_no_seller_information(
        client, auth_headers, live_deal, enabled, monkeypatch):
    from app.services import email_service
    from app.services.wholesale_disposition import seller_leak_check

    monkeypatch.setattr(email_service, "send_email", FakeProvider())
    ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                   headers=auth_headers,
                   json={"buyer_ids": [live_deal["buyer"]["id"]]}))
    room = ok(client.get("/wholesale/deals/%s" % live_deal["deal_id"],
                         headers=auth_headers))
    stored = room["buyer_outreach"][0]
    leaked = seller_leak_check((stored["body"] or "") + (stored["subject"] or ""),
                               SELLER_SECRETS)
    assert leaked == []


# ── Audit ───────────────────────────────────────────────────────────────────

def test_a_send_and_a_refusal_are_both_audited(
        client, auth_headers, live_deal, enabled, monkeypatch):
    from app.services import email_service

    monkeypatch.setattr(email_service, "send_email", FakeProvider())
    quiet = ok(client.post("/wholesale/buyers", headers=auth_headers,
                           json={"company_name": "Audit Quiet",
                                 "email": "aq@example.com",
                                 "do_not_contact": True}))
    ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                   headers=auth_headers,
                   json={"buyer_ids": [live_deal["buyer"]["id"], quiet["id"]]}))

    events = ok(client.get("/wholesale/events", headers=auth_headers,
                           params={"deal_id": live_deal["deal_id"]}))["events"]
    actions = {e["action"] for e in events}
    assert "buyer_outreach.sent" in actions
    assert "buyer_outreach.blocked" in actions
    assert "disposition.composed" in actions


# ── Cross-tenant ────────────────────────────────────────────────────────────

@pytest.fixture()
def attacker_headers(db_session):
    org = Organization(name="Attacker Wholesaler", slug="attacker-wholesale",
                       plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    user = User(organization_id=org.id, email="attacker@wholesale.test",
                password_hash=hash_password("TestPass123!"),
                full_name="Attacker", role="org_admin", must_change_password=False)
    db_session.add(user)
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(user, db_session)}


def test_another_tenant_cannot_send_on_your_deal(client, auth_headers,
                                                 attacker_headers, live_deal,
                                                 enabled, monkeypatch):
    from app.services import email_service

    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)

    response = client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                           headers=attacker_headers,
                           json={"buyer_ids": [live_deal["buyer"]["id"]]})
    assert response.status_code == 404
    assert fake.calls == []


def test_another_tenant_cannot_resend_your_outreach(client, auth_headers,
                                                    attacker_headers, live_deal,
                                                    enabled, monkeypatch):
    from app.services import email_service

    fake = FakeProvider()
    monkeypatch.setattr(email_service, "send_email", fake)
    first = ok(client.post("/wholesale/deals/%s/disposition" % live_deal["deal_id"],
                           headers=auth_headers,
                           json={"buyer_ids": [live_deal["buyer"]["id"]]}))
    outreach_id = first["results"][0]["outreach_id"]

    response = client.post("/wholesale/outreach/%s/resend" % outreach_id,
                           headers=attacker_headers)
    assert response.status_code == 404
    assert len(fake.calls) == 1
