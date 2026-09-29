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
