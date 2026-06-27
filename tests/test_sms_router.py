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
        .filter(SuppressionEntry.organization_id == sample_org.id, SuppressionEntry.phone == "12145556666")
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


# ---------------------------------------------------------------------------
# Auto-send candidate wiring - confirms the inbound webhook actually
# triggers the candidate check end-to-end, and critically, that an
# advisor on the default "off" phase sees ZERO behavior change at all -
# this feature must be fully invisible until explicitly opted into.
# ---------------------------------------------------------------------------

def test_inbound_webhook_creates_no_candidate_when_advisor_phase_is_off(client, db_session, sample_lead, sample_advisor):
    """The default, safe state - confirms the new wiring changes NOTHING for an advisor who hasn't opted in."""
    from app.models.models import AutoSendCandidate
    sample_lead.phone = "12145559600"
    sample_lead.assigned_to_id = sample_advisor.id
    db_session.commit()
    assert sample_advisor.auto_send_phase == "off"

    response = client.post("/sms/webhook/inbound", data={
        "From": "+12145559600", "To": "+19998887777",
        "Body": "What time works for you?", "MessageSid": "SM_autosend_off_test",
    })

    assert response.status_code == 200
    assert db_session.query(AutoSendCandidate).count() == 0


def test_inbound_webhook_creates_a_candidate_when_eligible_and_phase_is_candidate(client, db_session, sample_lead, sample_advisor):
    from unittest.mock import patch
    from app.models.models import AutoSendCandidate, Reply, ReplyClassification
    from datetime import datetime, timedelta, timezone

    sample_lead.phone = "12145559601"
    sample_lead.assigned_to_id = sample_advisor.id
    sample_advisor.auto_send_phase = "candidate"
    db_session.commit()
    # Establish prior context so this isn't treated as the first reply.
    db_session.add(Reply(lead_id=sample_lead.id, body="Hi there", classification=ReplyClassification.NEUTRAL,
                          received_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)))
    db_session.commit()

    with patch("app.services.auto_send_eligibility_service.check_auto_send_eligibility") as mock_check:
        mock_check.return_value = {"eligible": True, "confidence": "high", "reasoning": "Simple scheduling question."}

        response = client.post("/sms/webhook/inbound", data={
            "From": "+12145559601", "To": "+19998887777",
            "Body": "What time works for you?", "MessageSid": "SM_autosend_candidate_test",
        })

    assert response.status_code == 200
    candidates = db_session.query(AutoSendCandidate).filter(AutoSendCandidate.lead_id == sample_lead.id).all()
    assert len(candidates) == 1
    assert candidates[0].advisor_id == sample_advisor.id


def test_inbound_webhook_still_responds_correctly_even_if_candidate_check_fails(client, db_session, sample_lead, sample_advisor):
    """A failure in the new auto-send wiring must NEVER break the actual Twilio webhook response - this is the real safety property of the try/except wrapping."""
    from unittest.mock import patch
    sample_lead.phone = "12145559602"
    sample_lead.assigned_to_id = sample_advisor.id
    sample_advisor.auto_send_phase = "candidate"
    db_session.commit()

    with patch("app.services.auto_send_candidate_service.maybe_create_candidate") as mock_maybe:
        mock_maybe.side_effect = Exception("Unexpected failure in candidate service")

        response = client.post("/sms/webhook/inbound", data={
            "From": "+12145559602", "To": "+19998887777",
            "Body": "What time works for you?", "MessageSid": "SM_autosend_failure_test",
        })

    assert response.status_code == 200
    assert response.json()["status"] == "received"
