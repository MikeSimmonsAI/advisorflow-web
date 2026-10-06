"""RESEND DELIVERY EVENTS - signed, and they change what we know and who we mail.

Sent is not delivered. A hard bounce flags the address (every send path's
preflight then refuses it); a complaint is an email opt-out of record.
"""
import base64
import hashlib
import hmac
import json
import time

import pytest

from app.models.models import EmailMessage, Lead

SECRET = base64.b64encode(b"test-webhook-secret-bytes").decode()


def _signed(body: dict, secret=SECRET, ts=None, event_id=None):
    import uuid
    eid = event_id or "msg_%s" % uuid.uuid4().hex[:12]
    raw = json.dumps(body).encode()
    ts = str(int(ts or time.time()))
    sig = base64.b64encode(hmac.new(base64.b64decode(secret), ("%s.%s." % (eid, ts)).encode() + raw,
                                    hashlib.sha256).digest()).decode()
    return raw, {"svix-id": eid, "svix-timestamp": ts, "svix-signature": "v1,%s" % sig,
                 "content-type": "application/json"}


@pytest.fixture()
def sent(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="E",
                last_name="V", email="ev@example.com", status="sent")
    db_session.add(lead)
    db_session.flush()
    msg = EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id, subject="s", body_html="b",
                       provider_message_id="re_123", status="sent")
    db_session.add(msg)
    db_session.commit()
    return lead, msg


def test_unconfigured_is_refused(client, monkeypatch):
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    assert client.post("/email/events/resend", json={}).status_code == 503


def test_bad_or_stale_signature_is_refused(client, monkeypatch, sent):
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "whsec_" + SECRET)
    raw, h = _signed({"type": "email.delivered", "data": {"email_id": "re_123"}})
    h["svix-signature"] = "v1,AAAA"
    assert client.post("/email/events/resend", content=raw, headers=h).status_code == 401
    raw, h = _signed({"type": "email.delivered", "data": {"email_id": "re_123"}}, ts=time.time() - 3600)
    assert client.post("/email/events/resend", content=raw, headers=h).status_code == 401


def test_delivered_bounce_and_complaint(client, monkeypatch, db_session, sent):
    lead, msg = sent
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "whsec_" + SECRET)
    raw, h = _signed({"type": "email.delivered", "data": {"email_id": "re_123"}})
    assert client.post("/email/events/resend", content=raw, headers=h).json()["action"] == "delivered"
    db_session.refresh(msg)
    assert msg.status == "delivered"
    raw, h = _signed({"type": "email.bounced", "data": {"email_id": "re_123", "bounce": {"type": "Permanent"}}})
    r = client.post("/email/events/resend", content=raw, headers=h).json()
    db_session.refresh(msg)
    db_session.refresh(lead)
    assert msg.status == "bounced" and lead.manual_flag == "bad_email" and "flagged" in r["action"]
    raw, h = _signed({"type": "email.delivered", "data": {"email_id": "re_123"}})
    client.post("/email/events/resend", content=raw, headers=h)
    db_session.refresh(msg)
    assert msg.status == "bounced"                         # never downgraded
    raw, h = _signed({"type": "email.complained", "data": {"email_id": "re_123"}})
    client.post("/email/events/resend", content=raw, headers=h)
    db_session.refresh(lead)
    assert lead.allow_email is False
    from app.services.compliance_service import check_compliance_preflight
    with pytest.raises(Exception):
        check_compliance_preflight(db_session, lead, channel="email")


def test_soft_bounce_does_not_flag_and_unknown_ids_are_acknowledged(client, monkeypatch, db_session, sent):
    lead, msg = sent
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "whsec_" + SECRET)
    raw, h = _signed({"type": "email.bounced", "data": {"email_id": "re_123", "bounce": {"type": "Transient"}}})
    client.post("/email/events/resend", content=raw, headers=h)
    db_session.refresh(lead)
    assert lead.manual_flag is None
    raw, h = _signed({"type": "email.delivered", "data": {"email_id": "nope"}})
    assert client.post("/email/events/resend", content=raw, headers=h).json()["ignored"] == "unknown email"


def test_unsubscribe_link_is_signed_and_a_get_changes_nothing(client, db_session, sent):
    from app.services.programs.unsubscribe import make_token, read_token
    lead, _ = sent
    tok = make_token(lead.id)
    assert read_token(tok) == lead.id and read_token(tok[:-2] + "xx") is None
    assert client.get("/email/unsubscribe/%s" % tok).status_code == 200
    db_session.refresh(lead)
    assert lead.allow_email is not False                     # a scanner's GET does nothing
    assert client.post("/email/unsubscribe/%s" % tok).status_code == 200
    db_session.refresh(lead)
    assert lead.allow_email is False
    assert client.post("/email/unsubscribe/bogus").status_code == 404


def test_a_redelivered_event_is_acknowledged_once(client, db_session, sent, monkeypatch):
    from app.models.program_models import EmailProviderEvent
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "whsec_" + SECRET)
    lead, msg = sent
    raw, h = _signed({"type": "email.bounced", "data": {"email_id": msg.provider_message_id,
                                                        "bounce": {"type": "hard"}}}, event_id="evt_same")
    first = client.post("/email/events/resend", content=raw, headers=h).json()
    again = client.post("/email/events/resend", content=raw, headers=h).json()
    assert "bounced" in first["action"] and again == {"ok": True, "duplicate": True}
    assert db_session.query(EmailProviderEvent).filter_by(event_id="evt_same").count() == 1
