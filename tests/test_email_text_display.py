"""Sent emails are stored as HTML (apostrophes become &#x27;). Every screen that
shows the email as text must turn those back into normal characters."""
from datetime import datetime

from app.models.models import EmailMessage, Lead
from app.services.programs.email_touches import text_to_html as plain_text_to_html


def test_conversation_shows_apostrophes_not_codes(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Jeff",
                last_name="T", phone="12145557399", status="new")
    db_session.add(lead); db_session.commit()
    html_body = plain_text_to_html("Hi Jeff, It's Kerry. I'll get back to you & yours.")
    assert "&#x27;" in html_body or "&#39;" in html_body
    db_session.add(EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id, subject="S",
                                body_html=html_body, status="sent", sent_at=datetime.utcnow()))
    db_session.commit()
    r = client.get(f"/leads/{lead.id}/timeline", headers=auth_headers)
    assert r.status_code == 200, r.text
    bodies = [e["body"] for e in r.json()["events"] if e["channel"] == "email"]
    assert bodies and "It's Kerry" in bodies[0] and "I'll" in bodies[0] and "& yours" in bodies[0]
    assert "&#" not in bodies[0] and "&amp;" not in bodies[0]
