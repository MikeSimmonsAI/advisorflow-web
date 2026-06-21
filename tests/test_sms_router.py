"""
Router-level tests for app/routers/sms_router.py - specifically the
inbound Twilio webhook, which had zero direct test coverage before this
even though it's one of the most important real-time entry points in
the app (every lead reply flows through it).
"""

from app.models.models import Lead, LeadStatus, EngagementTemperature
from app.services.cadence_service import start_cadence


def test_inbound_webhook_with_unknown_sender_does_not_crash(client):
    response = client.post("/sms/webhook/inbound", data={
        "From": "+19995551234", "To": "+12145550000",
        "Body": "hello", "MessageSid": "SM_unknown",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "no_matching_lead"


def test_inbound_webhook_creates_reply_and_reclassifies_hot(client, db_session, sample_lead):
    sample_lead.phone = "12145559999"
    db_session.commit()

    response = client.post("/sms/webhook/inbound", data={
        "From": "+12145559999", "To": "+19998887777",
        "Body": "Yes I'm interested, please call me", "MessageSid": "SM_hot_test",
    })
    assert response.status_code == 200
    assert response.json()["is_hot"] is True

    db_session.refresh(sample_lead)
    assert sample_lead.status == "hot"
    # Confirms the engagement reclassification wired into the webhook
    # actually ran and persisted, not just the status field.
    assert sample_lead.engagement_temperature == EngagementTemperature.HOT


def test_inbound_webhook_stop_keyword_sets_dnc_and_cold(client, db_session, sample_lead):
    sample_lead.phone = "12145558888"
    db_session.commit()
    start_cadence(db_session, sample_lead)

    response = client.post("/sms/webhook/inbound", data={
        "From": "+12145558888", "To": "+19998887777",
        "Body": "STOP", "MessageSid": "SM_stop_test",
    })
    assert response.status_code == 200

    db_session.refresh(sample_lead)
    assert sample_lead.status == "dnc"
    assert sample_lead.engagement_temperature == EngagementTemperature.COLD


def test_inbound_webhook_stop_reply_creates_suppression_entry(client, db_session, sample_lead, sample_org):
    """
    Confirms the real gap fix: a STOP reply must also create a
    SuppressionEntry with source=reply_stop, connecting the previously
    separate reply-handling and Compliance Center suppression systems.
    """
    from app.models.models import SuppressionEntry, SuppressionSource
    sample_lead.phone = "12145556666"
    db_session.commit()

    client.post("/sms/webhook/inbound", data={
        "From": "+12145556666", "To": "+19998887777",
        "Body": "STOP", "MessageSid": "SM_suppression_test",
    })

    entry = (
        db_session.query(SuppressionEntry)
        .filter(SuppressionEntry.organization_id == sample_org.id, SuppressionEntry.phone == "+12145556666")
        .first()
    )
    assert entry is not None
    assert entry.source == SuppressionSource.REPLY_STOP


def test_inbound_webhook_neutral_reply_does_not_set_hot(client, db_session, sample_lead):
    """
    NOTE: the hot-keyword matcher in sms_router.py does plain substring
    matching, not real intent detection - phrases like "not sure" would
    incorrectly match the "sure" keyword. Using a phrase here that's
    genuinely free of every current keyword to test the neutral path
    correctly; the substring-matching limitation itself is a separate,
    pre-existing issue worth flagging to Mike rather than silently
    working around in this test.
    """
    sample_lead.phone = "12145557777"
    db_session.commit()

    response = client.post("/sms/webhook/inbound", data={
        "From": "+12145557777", "To": "+19998887777",
        "Body": "What time does your office close today", "MessageSid": "SM_neutral_test",
    })
    assert response.status_code == 200
    assert response.json()["is_hot"] is False

    db_session.refresh(sample_lead)
    assert sample_lead.status == "replied"
