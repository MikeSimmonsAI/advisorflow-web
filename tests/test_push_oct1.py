"""WEB PUSH (VAPID) — config, ownership, outbox, delivery. Nothing real is sent:
every delivery test injects a fake sender, and the not-configured tests prove
the real adapter is never reached without keys."""

from datetime import datetime, timezone

import pytest

from app.models.models import Notification, NotificationType
from app.models.push_models import (EVENT_FAILED, EVENT_NOT_CONFIGURED, EVENT_SENT,
                                    PushEvent, PushSubscription, TYPE_HOT_REPLY,
                                    TYPE_REPLY)
from app.services import web_push_service as wps
from app.services.auth_service import create_access_token

EP_A = "https://push.example.test/send/aaaaaaaaaaaa"
EP_B = "https://push.example.test/send/bbbbbbbbbbbb"
KEYS = {"p256dh": "BPpkeyAAAAAAAAAAAAAAAAAAAAAAAA", "auth": "authAAAAAAAA"}


@pytest.fixture(autouse=True)
def _mount_router(client):
    from app.main import app
    if not any(getattr(r, "path", "").startswith("/push/") for r in app.routes):
        from app.routers.push_router import router
        app.include_router(router)
    yield


@pytest.fixture()
def no_vapid(monkeypatch):
    for k in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture()
def vapid_on(monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BPublicKeyForTestsOnly")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "private-test-only")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:ops@example.test")
    monkeypatch.setattr(wps, "sender_available", lambda: True)

    def _never(*a, **k):
        raise AssertionError("real web push adapter must not run in tests")
    monkeypatch.setattr(wps, "_pywebpush_sender", _never)


def _h(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _sub(client, headers, endpoint=EP_A):
    return client.post("/push/subscribe", headers=headers,
                       json={"endpoint": endpoint, "keys": KEYS, "user_agent": "UA"})


class FakeSender:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}

    def __call__(self, info, data, key, claims, ttl):
        self.calls.append((info["endpoint"], data))
        code = self.fail.get(info["endpoint"])
        if code:
            raise wps.PushSendError("gone", code)


# ── config ──────────────────────────────────────────────────────────────────

def test_config_not_configured_without_keys(client, no_vapid):
    body = client.get("/push/config").json()
    assert body["configured"] is False and body["enabled"] is False
    assert body["vapid_public_key"] is None
    assert body["reason"]


def test_config_keys_but_no_library_is_not_configured(client, monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:x@example.test")
    monkeypatch.setattr(wps, "sender_available", lambda: False)
    body = client.get("/push/config").json()
    assert body["configured"] is False and body["vapid_public_key"] is None
    assert "library" in body["reason"]


def test_config_configured_exposes_public_key_only(client, vapid_on):
    body = client.get("/push/config").json()
    assert body["configured"] is True
    assert body["vapid_public_key"] == "BPublicKeyForTestsOnly"
    assert "private-test-only" not in str(body)


# ── subscriptions ───────────────────────────────────────────────────────────

def test_subscribe_and_status_never_return_endpoint_or_keys(client, db_session, sample_advisor, no_vapid):
    h = _h(db_session, sample_advisor)
    r = _sub(client, h)
    assert r.status_code == 201
    st = client.get("/push/status", headers=h).json()
    assert st["active_subscriptions"] == 1 and st["configured"] is False
    assert EP_A not in str(st) and KEYS["auth"] not in str(st)
    row = db_session.query(PushSubscription).one()
    assert row.user_id == sample_advisor.id
    assert row.organization_id == sample_advisor.organization_id


def test_subscribe_rejects_non_https(client, db_session, sample_advisor):
    r = _sub(client, _h(db_session, sample_advisor), endpoint="http://insecure.test/x/yyyy")
    assert r.status_code == 422


def test_unsubscribe_is_scoped_to_caller(client, db_session, sample_advisor, second_advisor):
    ha, hb = _h(db_session, sample_advisor), _h(db_session, second_advisor)
    _sub(client, ha, EP_A)
    sid_b = _sub(client, hb, EP_B).json()["id"]
    # A cannot revoke B's by id or by endpoint.
    assert client.delete("/push/subscribe?id=%s" % sid_b, headers=ha).status_code == 404
    assert client.request("DELETE", "/push/subscribe", headers=ha,
                          json={"endpoint": EP_B}).status_code == 404
    b = db_session.query(PushSubscription).filter(PushSubscription.id == sid_b).one()
    db_session.refresh(b)
    assert b.revoked_at is None
    # A revokes its own; B is still untouched.
    assert client.request("DELETE", "/push/subscribe", headers=ha,
                          json={"endpoint": EP_A}).status_code == 200
    db_session.refresh(b)
    assert b.revoked_at is None
    assert client.get("/push/status", headers=ha).json()["active_subscriptions"] == 0
    assert client.get("/push/status", headers=hb).json()["active_subscriptions"] == 1


def test_same_endpoint_moves_owner(client, db_session, sample_advisor, second_advisor):
    _sub(client, _h(db_session, sample_advisor), EP_A)
    _sub(client, _h(db_session, second_advisor), EP_A)
    rows = db_session.query(PushSubscription).all()
    assert len(rows) == 1 and rows[0].user_id == second_advisor.id


# ── outbox: enqueue on real reply paths ─────────────────────────────────────

def test_email_reply_enqueues_one_event_idempotently(db_session, sample_lead, sample_advisor):
    from app.services.inbound_mailbox_service import _store_reply
    when = datetime.now(timezone.utc).replace(tzinfo=None)
    _store_reply(db_session, sample_lead, "Yes please call me at 214-555-0000", when)
    db_session.commit()
    evs = db_session.query(PushEvent).all()
    assert len(evs) == 1
    ev = evs[0]
    assert ev.target_user_id == sample_advisor.id and ev.type == TYPE_REPLY
    assert ev.organization_id == sample_lead.organization_id
    assert ev.url == "/m/conversations/%s" % sample_lead.id
    # The same message again (dedupe) creates neither a reply nor an event.
    _store_reply(db_session, sample_lead, "Yes please call me at 214-555-0000", when)
    db_session.commit()
    assert db_session.query(PushEvent).count() == 1
    # Re-enqueueing the same notification is a no-op.
    n = db_session.query(Notification).one()
    wps.enqueue_for_notification(db_session, n)
    db_session.commit()
    assert db_session.query(PushEvent).count() == 1


def test_payload_has_no_pii(db_session, sample_lead):
    from app.services.inbound_mailbox_service import _store_reply
    _store_reply(db_session, sample_lead, "My SSN thoughts and phone 2145559999",
                 datetime.now(timezone.utc))
    db_session.commit()
    ev = db_session.query(PushEvent).one()
    p = wps.payload_for(ev)
    assert set(p) == {"title", "url", "notification_id", "event_id", "workspace_id", "type"}
    blob = str(p) + ev.title
    for bad in ("Jane", "Doe", "2145559999", "jane@example.com", "SSN", "phone"):
        assert bad not in blob


def test_hot_sms_reply_enqueues(db_session, sample_lead, sample_advisor, monkeypatch):
    from app.models.models import Reply
    from app.services import notification_service
    monkeypatch.setattr(notification_service, "send_email_via_provider",
                        lambda *a, **k: {"success": False})
    reply = Reply(lead_id=sample_lead.id, body="interested", source="sms",
                  received_at=datetime.now(timezone.utc), is_hot=True)
    db_session.add(reply)
    db_session.commit()
    notification_service.notify_hot_reply(db_session, sample_advisor, sample_lead, reply)
    ev = db_session.query(PushEvent).one()
    assert ev.type == TYPE_HOT_REPLY and ev.target_user_id == sample_advisor.id
    assert "Jane" not in ev.title


def test_one_event_per_target(db_session, sample_lead, sample_advisor, second_advisor):
    for uid in (sample_advisor.id, second_advisor.id):
        n = Notification(user_id=uid, lead_id=sample_lead.id,
                         type=NotificationType.REPLY_RECEIVED, message="x")
        db_session.add(n)
        wps.enqueue_for_notification(db_session, n)
        wps.enqueue_for_notification(db_session, n)
    db_session.commit()
    evs = db_session.query(PushEvent).all()
    assert sorted(e.target_user_id for e in evs) == sorted([sample_advisor.id, second_advisor.id])


# ── delivery ────────────────────────────────────────────────────────────────

def _event(db, user, org_id):
    n = Notification(user_id=user.id, type=NotificationType.REPLY_RECEIVED, message="x")
    db.add(n)
    ev = wps.enqueue_for_notification(db, n, organization_id=org_id)
    db.commit()
    return ev


def test_delivery_skipped_when_not_configured(db_session, sample_advisor, no_vapid, monkeypatch):
    called = []
    monkeypatch.setattr(wps, "_pywebpush_sender", lambda *a, **k: called.append(1))
    wps.subscribe(db_session, sample_advisor, organization_id=sample_advisor.organization_id,
                  endpoint=EP_A, p256dh=KEYS["p256dh"], auth=KEYS["auth"])
    ev = _event(db_session, sample_advisor, sample_advisor.organization_id)
    assert wps.deliver_pending(db_session)["skipped"] == "not_configured"
    r = wps.deliver_event(db_session, ev)
    assert r["status"] == EVENT_NOT_CONFIGURED and called == []


def test_test_endpoint_refuses_when_not_configured(client, db_session, sample_advisor, no_vapid):
    h = _h(db_session, sample_advisor)
    _sub(client, h)
    assert client.post("/push/test", headers=h).status_code == 409


def test_revoke_on_410_and_send_to_live(db_session, sample_advisor, vapid_on):
    org = sample_advisor.organization_id
    for ep in (EP_A, EP_B):
        wps.subscribe(db_session, sample_advisor, organization_id=org, endpoint=ep,
                      p256dh=KEYS["p256dh"], auth=KEYS["auth"])
    ev = _event(db_session, sample_advisor, org)
    sender = FakeSender(fail={EP_B: 410})
    r = wps.deliver_event(db_session, ev, sender=sender)
    assert r["sent"] == 1 and r["revoked"] == 1
    gone = db_session.query(PushSubscription).filter(PushSubscription.endpoint == EP_B).one()
    assert gone.revoked_at is not None and gone.revoked_reason == "expired_410"
    live = db_session.query(PushSubscription).filter(PushSubscription.endpoint == EP_A).one()
    assert live.revoked_at is None and live.last_success_at is not None


def test_all_404_marks_failed(db_session, sample_advisor, vapid_on):
    org = sample_advisor.organization_id
    wps.subscribe(db_session, sample_advisor, organization_id=org, endpoint=EP_A,
                  p256dh=KEYS["p256dh"], auth=KEYS["auth"])
    ev = _event(db_session, sample_advisor, org)
    r = wps.deliver_event(db_session, ev, sender=FakeSender(fail={EP_A: 404}))
    assert r["status"] == EVENT_FAILED


def test_delivery_respects_workspace(db_session, sample_advisor, vapid_on):
    wps.subscribe(db_session, sample_advisor, organization_id="other-workspace",
                  endpoint=EP_A, p256dh=KEYS["p256dh"], auth=KEYS["auth"])
    ev = _event(db_session, sample_advisor, sample_advisor.organization_id)
    sender = FakeSender()
    r = wps.deliver_event(db_session, ev, sender=sender)
    assert sender.calls == [] and r["sent"] == 0


def test_test_push_goes_only_to_callers_subscriptions(client, db_session, sample_advisor,
                                                      second_advisor, vapid_on, monkeypatch):
    sender = FakeSender()
    monkeypatch.setattr(wps, "_pywebpush_sender", sender)
    ha, hb = _h(db_session, sample_advisor), _h(db_session, second_advisor)
    _sub(client, ha, EP_A)
    _sub(client, hb, EP_B)
    r = client.post("/push/test", headers=ha)
    assert r.status_code == 200 and r.json()["status"] == EVENT_SENT
    assert [c[0] for c in sender.calls] == [EP_A]


def test_unsubscribe_by_endpoint_hash(client, db_session, sample_advisor, second_advisor):
    import hashlib
    ha, hb = _h(db_session, sample_advisor), _h(db_session, second_advisor)
    _sub(client, hb, EP_B)
    hb_hash = hashlib.sha256(EP_B.encode()).hexdigest()
    assert client.delete("/push/subscribe?endpoint_sha256=" + hb_hash, headers=ha).status_code == 404
    assert client.delete("/push/subscribe?endpoint_sha256=" + hb_hash, headers=hb).status_code == 200
