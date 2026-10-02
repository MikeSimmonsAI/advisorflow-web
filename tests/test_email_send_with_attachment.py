"""Send with attachment (owner, 2026-09-29: "Something went wrong on our end").

The attachment route called the email provider FIRST and then crashed on a
missing import (acting_advisor) while recording the send: the lead got the
mail, the page showed an error, nothing was logged, and a retry mailed twice.
It also dropped the booking button and sent the composer's text unformatted.
Now both routes share one send (_send_custom_email) that resolves everything
before the provider is called. The provider is faked - nothing is sent.
"""
import pytest

from app.models.models import EmailMessage


@pytest.fixture()
def sent(monkeypatch):
    calls = []

    def fake(to, subject, body_html, attachments=None, org=None, **kw):
        calls.append({"to": to, "subject": subject, "body": body_html, "attachments": attachments})
        return {"success": True, "provider_message_id": "fake-1", "error": None}
    monkeypatch.setattr("app.services.email_service.send_email_via_provider", fake)
    return calls


def _lead_with_email(db, lead):
    lead.email = "joshua@example.com"
    lead.status = "new"
    db.commit()
    return lead


def test_send_with_attachment_sends_once_and_records_it(client, db_session, sample_lead, auth_headers, sent):
    lead = _lead_with_email(db_session, sample_lead)
    r = client.post(f"/email/send-with-attachment/{lead.id}", headers=auth_headers,
                    data={"subject": "Your energy options", "body_html": "Hi Joshua,\n\nLine two.",
                          "include_booking_link": "true", "appt_label": "Energy Rate Review"},
                    files={"file": ("flyer.png", b"\x89PNG fake", "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["has_attachment"] is True
    assert len(sent) == 1
    body = sent[0]["body"]
    assert "Hi Joshua,<br><br>Line two." in body
    assert "Energy Rate Review" in body and "/book/" in body
    assert sent[0]["attachments"][0]["filename"] == "flyer.png"
    rows = db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).all()
    assert len(rows) == 1 and rows[0].provider_message_id == "fake-1"


def test_a_provider_refusal_is_reported_and_not_recorded(client, db_session, sample_lead, auth_headers, monkeypatch):
    lead = _lead_with_email(db_session, sample_lead)
    monkeypatch.setattr("app.services.email_service.send_email_via_provider",
                        lambda *a, **k: {"success": False, "error": "Domain not verified"})
    r = client.post(f"/email/send-with-attachment/{lead.id}", headers=auth_headers,
                    data={"subject": "s", "body_html": "b"},
                    files={"file": ("f.png", b"x", "image/png")})
    assert r.status_code == 502 and "Domain not verified" in r.json()["detail"]
    assert db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).count() == 0


def test_oversized_attachment_is_refused_before_sending(client, db_session, sample_lead, auth_headers, sent):
    lead = _lead_with_email(db_session, sample_lead)
    r = client.post(f"/email/send-with-attachment/{lead.id}", headers=auth_headers,
                    data={"subject": "s", "body_html": "b"},
                    files={"file": ("big.pdf", b"0" * (10 * 1024 * 1024 + 1), "application/pdf")})
    assert r.status_code == 413 and sent == []


def test_plain_send_still_works(client, db_session, sample_lead, auth_headers, sent):
    lead = _lead_with_email(db_session, sample_lead)
    r = client.post(f"/email/send/{lead.id}", headers=auth_headers,
                    json={"subject": "s", "body": "Hello\nthere", "include_booking_link": False})
    assert r.status_code == 200, r.text
    assert len(sent) == 1 and sent[0]["body"] == "Hello<br>there" and sent[0]["attachments"] is None


def test_an_identical_resend_is_held_until_confirmed(client, db_session, sample_lead, auth_headers, sent):
    lead = _lead_with_email(db_session, sample_lead)
    payload = {"subject": "Same subject", "body": "Same body", "include_booking_link": False}
    assert client.post(f"/email/send/{lead.id}", headers=auth_headers, json=payload).status_code == 200
    r = client.post(f"/email/send/{lead.id}", headers=auth_headers, json=payload)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_send"
    assert len(sent) == 1
    r = client.post(f"/email/send-with-attachment/{lead.id}", headers=auth_headers,
                    data={"subject": "Same subject", "body_html": "x"},
                    files={"file": ("f.png", b"x", "image/png")})
    assert r.status_code == 409 and len(sent) == 1
    r = client.post(f"/email/send/{lead.id}", headers=auth_headers, json={**payload, "allow_duplicate": True})
    assert r.status_code == 200 and len(sent) == 2


def test_a_record_failure_after_sending_is_never_an_error(client, db_session, sample_lead, auth_headers,
                                                          sent, monkeypatch):
    lead = _lead_with_email(db_session, sample_lead)
    from app.services import send_record
    monkeypatch.setattr(send_record, "record_after_send", lambda *a, **k: None)
    r = client.post(f"/email/send/{lead.id}", headers=auth_headers,
                    json={"subject": "s2", "body": "b", "include_booking_link": False})
    assert r.status_code == 200
    assert r.json()["recorded"] is False and "Do not send it again" in r.json()["warning"]
    assert len(sent) == 1


def test_record_after_send_retries_then_gives_up_quietly(db_session, sample_lead, sample_advisor):
    from app.services.send_record import record_after_send
    calls = []

    def bad():
        calls.append(1)
        return EmailMessage(lead_id=sample_lead.id, sender_id=None, subject="s", body_html="b")  # NOT NULL fails
    assert record_after_send(db_session, bad, lead=sample_lead, channel="email") is None
    assert len(calls) == 2
    ok = record_after_send(db_session, lambda: EmailMessage(lead_id=sample_lead.id, sender_id=sample_advisor.id,
                                                            subject="s", body_html="b", status="sent"),
                           lead=sample_lead, channel="email")
    assert ok is not None and ok.id


def test_record_sent_email_sends_nothing_and_is_manager_only(client, db_session, sample_lead, sample_advisor,
                                                             auth_headers, sent):
    lead = _lead_with_email(db_session, sample_lead)
    payload = {"subject": "Recorded", "body": "Hi\nthere", "sent_at": "2026-09-29T19:29:00Z", "note": "backfill"}
    assert client.post(f"/email/record-sent/{lead.id}", headers=auth_headers, json=payload).status_code == 403
    sample_advisor.role = "org_admin"
    db_session.commit()
    from app.services.auth_service import create_access_token
    h = {"Authorization": "Bearer " + create_access_token(sample_advisor, db_session)}
    r = client.post(f"/email/record-sent/{lead.id}", headers=h, json=payload)
    assert r.status_code == 200, r.text
    row = db_session.query(EmailMessage).get(r.json()["email_id"])
    assert row.send_source == "recorded" and row.provider_message_id is None and "backfill" in row.body_html
    assert sent == []
    assert client.post(f"/email/record-sent/{lead.id}", headers=h, json=payload).status_code == 409


def test_a_connected_microsoft_365_mailbox_is_used_for_one_off_sends(client, db_session, sample_lead,
                                                                      sample_advisor, auth_headers, sent,
                                                                      monkeypatch):
    lead = _lead_with_email(db_session, sample_lead)
    lead.assigned_to_id = sample_advisor.id
    sample_advisor.microsoft_365_connected = True
    sample_advisor.microsoft_email_address = "advisor@example.com"
    db_session.commit()
    graph = []

    def fake_graph(advisor, to, subject, body_html, attachments=None):
        graph.append({"to": to, "attachments": attachments})
        return {"success": True, "provider_message_id": None, "error": None}
    monkeypatch.setattr("app.services.microsoft_email_service.send_email_via_microsoft_graph", fake_graph)
    r = client.post(f"/email/send-with-attachment/{lead.id}", headers=auth_headers,
                    data={"subject": "via graph", "body_html": "b"},
                    files={"file": ("f.png", b"x", "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["sent_via"] == "microsoft_365"
    assert len(graph) == 1 and graph[0]["attachments"][0]["filename"] == "f.png" and sent == []


def test_provider_mail_carries_a_plain_text_part():
    from app.services.email_service import html_to_text
    t = html_to_text('Hi Joshua,<br><br>Line two.<table><tr><td><a href="https://x/book/abc">Energy Rate Review</a></td></tr></table>')
    assert t == "Hi Joshua,\n\nLine two.\n\nEnergy Rate Review: https://x/book/abc"


def test_a_send_already_in_flight_is_refused_not_sent_twice(client, db_session, sample_lead, auth_headers, sent):
    """Double-click: the second request arrives while the first is still at the
    provider (no EmailMessage row yet). It must not reach the provider."""
    from app.routers import email_router as ER
    from app.models.idempotency_models import ActionLease
    lead = _lead_with_email(db_session, sample_lead)
    key = (lead.id, "in flight")
    token = ER._claim_send(key, db_session)     # the first request - on ANY instance - still sending
    assert token
    try:
        r = client.post(f"/email/send/{lead.id}", headers=auth_headers,
                        json={"subject": "In flight", "body": "b", "include_booking_link": False})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_send"
        assert sent == []
    finally:
        ER._release_send(key, db_session, token)
    # Released (first request finished without recording) -> a send goes through.
    r = client.post(f"/email/send/{lead.id}", headers=auth_headers,
                    json={"subject": "In flight", "body": "b", "include_booking_link": False})
    assert r.status_code == 200 and len(sent) == 1
    assert db_session.query(ActionLease).count() == 0          # released after the send
