"""
Tests for the needs_attention filter on GET /sms/replies - implements
Mike's specific request: "only hand me a hot lead when I'm ready to
book", filtering down to Interested + Callback only.
"""

from app.models.models import Reply, ReplyClassification


def test_needs_attention_filter_excludes_neutral_and_dnc(client, auth_headers, db_session, sample_lead):
    interested = Reply(lead_id=sample_lead.id, body="Yes!", classification=ReplyClassification.INTERESTED)
    callback = Reply(lead_id=sample_lead.id, body="Call me", classification=ReplyClassification.CALLBACK)
    neutral = Reply(lead_id=sample_lead.id, body="What time?", classification=ReplyClassification.NEUTRAL)
    dnc = Reply(lead_id=sample_lead.id, body="Stop", classification=ReplyClassification.DNC)
    db_session.add_all([interested, callback, neutral, dnc])
    db_session.commit()

    response = client.get("/sms/replies?needs_attention=true", headers=auth_headers)
    assert response.status_code == 200
    bodies = [r["body"] for r in response.json()]
    assert "Yes!" in bodies
    assert "Call me" in bodies
    assert "What time?" not in bodies
    assert "Stop" not in bodies


def test_needs_attention_false_shows_everything(client, auth_headers, db_session, sample_lead):
    neutral = Reply(lead_id=sample_lead.id, body="Neutral reply", classification=ReplyClassification.NEUTRAL)
    db_session.add(neutral)
    db_session.commit()

    response = client.get("/sms/replies", headers=auth_headers)
    bodies = [r["body"] for r in response.json()]
    assert "Neutral reply" in bodies


def test_needs_attention_only_shows_own_leads(client, auth_headers, db_session, sample_org, second_advisor):
    from app.models.models import Lead
    other_lead = Lead(organization_id=sample_org.id, assigned_to_id=second_advisor.id,
                       first_name="Other", last_name="Advisor", phone="12145559090")
    db_session.add(other_lead)
    db_session.commit()
    other_reply = Reply(lead_id=other_lead.id, body="Yes interested", classification=ReplyClassification.INTERESTED)
    db_session.add(other_reply)
    db_session.commit()

    response = client.get("/sms/replies?needs_attention=true", headers=auth_headers)
    bodies = [r["body"] for r in response.json()]
    assert "Yes interested" not in bodies
