"""The webhook ENDPOINT: signature verification and HTTP status contract.

Separate from test_billing_webhooks.py, which tests the dispatcher directly.
This file tests the two things only the route can enforce: that an unsigned or
badly signed request is refused before anything is read, and that a transient
failure answers 500 so Stripe retries rather than 200 so the event is lost.

NO LIVE STRIPE. The signature is computed locally with the same HMAC scheme
Stripe uses, against a test secret that exists only in this file.
"""
import hashlib
import hmac
import json
import time

import pytest

from app.models.billing_models import StripeWebhookEvent
from app.models.models import Organization

WEBHOOK_SECRET = "whsec_testonly_not_a_real_secret"
CUST = "cus_ROUTE_TEST"


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET, timestamp: int = None) -> str:
    """Stripe's scheme: t=<ts>,v1=<hmac_sha256(ts + '.' + body)>."""
    ts = timestamp or int(time.time())
    signed = b"%d.%s" % (ts, payload)
    mac = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return "t=%d,v1=%s" % (ts, mac)


@pytest.fixture()
def webhook_env(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_notreal")
    return WEBHOOK_SECRET


@pytest.fixture()
def route_org(db_session):
    org = Organization(name="Route Co", slug="route-co", plan="starter",
                       stripe_customer_id=CUST, billing_status="active")
    db_session.add(org)
    db_session.commit()
    return org


def _event(event_id="evt_route_1", etype="invoice.payment_failed"):
    return {
        "id": event_id, "type": etype, "created": int(time.time()),
        "data": {"object": {
            "id": "in_route_1", "object": "invoice", "customer": CUST,
            "status": "open", "currency": "usd", "number": "INV-R1",
            "subtotal": 49700, "total": 49700, "amount_due": 49700,
            "amount_paid": 0, "attempt_count": 1,
            "account_name": "EVO INTEGRATED SOLUTIONS LLC",
            "status_transitions": {}, "lines": {"data": []},
        }},
    }


def test_a_correctly_signed_event_is_accepted(client, db_session, webhook_env, route_org):
    body = json.dumps(_event()).encode()
    r = client.post("/billing/webhook", content=body,
                    headers={"stripe-signature": _sign(body),
                             "content-type": "application/json"})
    assert r.status_code == 200
    assert r.json()["received"] is True
    assert r.json()["duplicate"] is False

    db_session.refresh(route_org)
    assert route_org.billing_status == "past_due"


def test_an_invalid_signature_is_refused_and_writes_nothing(
        client, db_session, webhook_env, route_org):
    body = json.dumps(_event(event_id="evt_bad_sig")).encode()
    r = client.post("/billing/webhook", content=body,
                    headers={"stripe-signature": "t=1,v1=deadbeef",
                             "content-type": "application/json"})
    assert r.status_code == 400
    assert db_session.query(StripeWebhookEvent).count() == 0
    db_session.refresh(route_org)
    assert route_org.billing_status == "active", "unsigned request changed state"


def test_a_missing_signature_header_is_refused(client, webhook_env):
    body = json.dumps(_event(event_id="evt_no_sig")).encode()
    r = client.post("/billing/webhook", content=body,
                    headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_a_body_altered_after_signing_is_refused(client, db_session, webhook_env, route_org):
    """The signature covers the raw body. Any middleware that rewrote it would
    break this test, which is the point of having it."""
    body = json.dumps(_event(event_id="evt_tamper")).encode()
    sig = _sign(body)
    tampered = json.dumps(_event(event_id="evt_tamper_2")).encode()
    r = client.post("/billing/webhook", content=tampered,
                    headers={"stripe-signature": sig,
                             "content-type": "application/json"})
    assert r.status_code == 400
    assert db_session.query(StripeWebhookEvent).count() == 0


def test_duplicate_delivery_returns_200_and_says_so(
        client, db_session, webhook_env, route_org):
    body = json.dumps(_event(event_id="evt_dup_route")).encode()
    headers = {"stripe-signature": _sign(body), "content-type": "application/json"}

    first = client.post("/billing/webhook", content=body, headers=headers)
    second = client.post("/billing/webhook", content=body, headers=headers)

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert db_session.query(StripeWebhookEvent).count() == 1


def test_a_processing_failure_answers_500_so_stripe_retries(
        client, db_session, webhook_env, route_org, monkeypatch):
    """A 200 over a swallowed exception loses the event permanently."""
    from app.services import stripe_sync

    def boom(*a, **kw):
        raise RuntimeError("transient database error")

    monkeypatch.setattr(stripe_sync, "upsert_invoice_from_stripe", boom)

    body = json.dumps(_event(event_id="evt_500")).encode()
    r = client.post("/billing/webhook", content=body,
                    headers={"stripe-signature": _sign(body),
                             "content-type": "application/json"})
    assert r.status_code == 500


def test_webhook_is_not_configured_returns_503_not_a_crash(client, monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    body = json.dumps(_event(event_id="evt_unconfigured")).encode()
    r = client.post("/billing/webhook", content=body,
                    headers={"stripe-signature": "t=1,v1=x",
                             "content-type": "application/json"})
    assert r.status_code == 503
