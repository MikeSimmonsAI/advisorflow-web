"""
Tests for app/services/notification_service.py
"""

from unittest.mock import patch
from app.services.notification_service import notify_hot_reply, get_unread_notifications, mark_notification_read
from app.models.models import Reply, Notification, NotificationType, ReplyClassification


@patch("app.services.notification_service.send_email_via_provider")
def test_notify_hot_reply_always_logs_notification_even_if_email_fails(mock_send, db_session, sample_lead, sample_advisor):
    """
    Critical behavior: the in-app Notification record must be created
    regardless of whether the email actually sends - so advisors never
    lose visibility into a hot reply just because SendGrid had a bad day.
    """
    mock_send.return_value = {"success": False, "provider_message_id": None, "error": "SendGrid down"}

    reply = Reply(lead_id=sample_lead.id, body="Yes, interested!", is_hot=True)
    db_session.add(reply)
    db_session.commit()

    notification = notify_hot_reply(db_session, sample_advisor, sample_lead, reply)

    assert notification.id is not None
    assert notification.type == NotificationType.HOT_REPLY
    assert notification.is_sent is False  # email failed, correctly reflected
    assert "Jane" in notification.message  # sample_lead.first_name


@patch("app.services.notification_service.send_email_via_provider")
def test_notify_hot_reply_marks_sent_on_success(mock_send, db_session, sample_lead, sample_advisor):
    mock_send.return_value = {"success": True, "provider_message_id": "msg123", "error": None}

    reply = Reply(lead_id=sample_lead.id, body="Yes please!", is_hot=True)
    db_session.add(reply)
    db_session.commit()

    notification = notify_hot_reply(db_session, sample_advisor, sample_lead, reply)
    assert notification.is_sent is True
    assert notification.sent_at is not None


@patch("app.services.notification_service.send_email_via_provider")
def test_notify_hot_reply_respects_notify_on_hot_reply_false(mock_send, db_session, sample_lead, sample_advisor):
    """
    If the advisor has turned off hot-reply email alerts in Settings, the
    Notification record still gets created (visible in-app) but no email
    attempt is made at all.
    """
    sample_advisor.notify_on_hot_reply = False
    db_session.commit()

    reply = Reply(lead_id=sample_lead.id, body="Interested", is_hot=True)
    db_session.add(reply)
    db_session.commit()

    notification = notify_hot_reply(db_session, sample_advisor, sample_lead, reply)
    assert notification.id is not None
    mock_send.assert_not_called()


@patch("app.services.notification_service.send_email_via_provider")
def test_notify_hot_reply_uses_notification_email_over_login_email(mock_send, db_session, sample_lead, sample_advisor):
    mock_send.return_value = {"success": True, "provider_message_id": "x", "error": None}
    sample_advisor.notification_email = "alerts@mikesimmons.com"
    db_session.commit()

    reply = Reply(lead_id=sample_lead.id, body="Yes", is_hot=True)
    db_session.add(reply)
    db_session.commit()

    notify_hot_reply(db_session, sample_advisor, sample_lead, reply)
    call_args = mock_send.call_args
    assert call_args[0][0] == "alerts@mikesimmons.com"  # first positional arg is to_email


@patch("app.services.notification_service.send_email_via_provider")
def test_notify_hot_reply_falls_back_to_login_email_when_no_notification_email_set(mock_send, db_session, sample_lead, sample_advisor):
    mock_send.return_value = {"success": True, "provider_message_id": "x", "error": None}
    assert sample_advisor.notification_email is None  # confirm fixture default

    reply = Reply(lead_id=sample_lead.id, body="Yes", is_hot=True)
    db_session.add(reply)
    db_session.commit()

    notify_hot_reply(db_session, sample_advisor, sample_lead, reply)
    call_args = mock_send.call_args
    assert call_args[0][0] == sample_advisor.email


def test_get_unread_notifications_excludes_read_ones(db_session, sample_advisor):
    n1 = Notification(user_id=sample_advisor.id, type=NotificationType.HOT_REPLY, message="First", is_read=False)
    n2 = Notification(user_id=sample_advisor.id, type=NotificationType.HOT_REPLY, message="Second", is_read=True)
    db_session.add_all([n1, n2])
    db_session.commit()

    unread = get_unread_notifications(db_session, sample_advisor.id)
    assert len(unread) == 1
    assert unread[0].message == "First"


def test_mark_notification_read_succeeds_for_owner(db_session, sample_advisor):
    n = Notification(user_id=sample_advisor.id, type=NotificationType.HOT_REPLY, message="Test", is_read=False)
    db_session.add(n)
    db_session.commit()

    result = mark_notification_read(db_session, n.id, sample_advisor.id)
    assert result is True
    db_session.refresh(n)
    assert n.is_read is True


def test_mark_notification_read_fails_for_wrong_user(db_session, sample_advisor, second_advisor):
    n = Notification(user_id=sample_advisor.id, type=NotificationType.HOT_REPLY, message="Test", is_read=False)
    db_session.add(n)
    db_session.commit()

    # second_advisor trying to mark advisor_one's notification as read should fail
    result = mark_notification_read(db_session, n.id, second_advisor.id)
    assert result is False
    db_session.refresh(n)
    assert n.is_read is False  # unchanged


# ---------------------------------------------------------------------------
# notify_reply - expanded per Mike's explicit request: alerts on EVERY
# reply classification, not just hot ones, plus SMS-to-advisor as an
# additional fast channel, plus tracking WHY an alert failed instead of
# silently leaving is_sent=False with no explanation anywhere.
# ---------------------------------------------------------------------------

from app.services.notification_service import notify_reply


@patch("app.services.notification_service.send_email_via_provider")
def test_notify_reply_fires_for_neutral_classification_not_just_hot(mock_send, db_session, sample_lead, sample_advisor):
    """The actual behavior change: a non-hot reply now still triggers a real alert, not silence."""
    mock_send.return_value = {"success": True, "provider_message_id": "x", "error": None}

    reply = Reply(lead_id=sample_lead.id, body="What time works?", classification=ReplyClassification.QUESTION)
    db_session.add(reply)
    db_session.commit()

    notification = notify_reply(db_session, sample_advisor, sample_lead, reply)

    assert notification.id is not None
    assert notification.type == NotificationType.REPLY_RECEIVED
    mock_send.assert_called_once()


@patch("app.services.notification_service.send_email_via_provider")
def test_notify_reply_fires_for_dnc_classification(mock_send, db_session, sample_lead, sample_advisor):
    mock_send.return_value = {"success": True, "provider_message_id": "x", "error": None}

    reply = Reply(lead_id=sample_lead.id, body="STOP", classification=ReplyClassification.DNC)
    db_session.add(reply)
    db_session.commit()

    notification = notify_reply(db_session, sample_advisor, sample_lead, reply)

    assert notification.type == NotificationType.REPLY_RECEIVED
    subject_used = mock_send.call_args[0][1]
    assert "DNC" in subject_used


@patch("app.services.notification_service.send_email_via_provider")
def test_notify_reply_interested_classification_is_hot_reply_type(mock_send, db_session, sample_lead, sample_advisor):
    mock_send.return_value = {"success": True, "provider_message_id": "x", "error": None}

    reply = Reply(lead_id=sample_lead.id, body="Yes!", classification=ReplyClassification.INTERESTED, is_hot=True)
    db_session.add(reply)
    db_session.commit()

    notification = notify_reply(db_session, sample_advisor, sample_lead, reply)

    assert notification.type == NotificationType.HOT_REPLY


@patch("app.services.notification_service.send_email_via_provider")
def test_notify_reply_records_failure_reason_when_email_fails(mock_send, db_session, sample_lead, sample_advisor):
    """
    Regression test for the silent-failure fix: previously a failed
    email left is_sent=False with zero trace of WHY. Now the actual
    error is captured.
    """
    mock_send.return_value = {"success": False, "provider_message_id": None, "error": "SendGrid API key invalid"}

    reply = Reply(lead_id=sample_lead.id, body="Hi", classification=ReplyClassification.NEUTRAL)
    db_session.add(reply)
    db_session.commit()

    notification = notify_reply(db_session, sample_advisor, sample_lead, reply)

    assert notification.is_sent is False
    assert notification.send_failure_reason == "SendGrid API key invalid"


@patch("app.services.notification_service.send_email_via_provider")
def test_notify_reply_with_unset_classification_uses_model_default_neutral(mock_send, db_session, sample_lead, sample_advisor):
    """
    Reply.classification has a model-level default of NEUTRAL (see
    models.py), so a Reply created without explicitly setting it never
    actually ends up None after a commit - confirming that default
    holds, and that notify_reply handles it correctly as a normal
    neutral-classification alert, not a crash.
    """
    mock_send.return_value = {"success": True, "provider_message_id": "x", "error": None}

    reply = Reply(lead_id=sample_lead.id, body="...")
    db_session.add(reply)
    db_session.commit()
    assert reply.classification == ReplyClassification.NEUTRAL  # confirms the model default actually applies

    notification = notify_reply(db_session, sample_advisor, sample_lead, reply)
    assert notification.id is not None
    assert notification.type == NotificationType.REPLY_RECEIVED


@patch("app.services.notification_service.send_email_via_provider")
@patch("app.services.sms_service.send_plain_sms")
def test_notify_reply_sends_sms_when_enabled_and_phone_set(mock_sms, mock_email, db_session, sample_lead, sample_advisor):
    mock_email.return_value = {"success": True, "provider_message_id": "x", "error": None}
    mock_sms.return_value = {"success": True, "twilio_sid": "SM123", "error": None}
    sample_advisor.notify_via_sms = True
    sample_advisor.notification_phone = "+12145559999"
    db_session.commit()

    reply = Reply(lead_id=sample_lead.id, body="Yes", classification=ReplyClassification.INTERESTED, is_hot=True)
    db_session.add(reply)
    db_session.commit()

    notify_reply(db_session, sample_advisor, sample_lead, reply)

    mock_sms.assert_called_once()
    call_args = mock_sms.call_args[0]
    assert call_args[1] == "+12145559999"


@patch("app.services.notification_service.send_email_via_provider")
@patch("app.services.sms_service.send_plain_sms")
def test_notify_reply_skips_sms_when_disabled(mock_sms, mock_email, db_session, sample_lead, sample_advisor):
    """notify_via_sms defaults to False - SMS alerts must be opt-in, not automatic."""
    mock_email.return_value = {"success": True, "provider_message_id": "x", "error": None}
    sample_advisor.notification_phone = "+12145559999"  # phone set but SMS not enabled
    db_session.commit()

    reply = Reply(lead_id=sample_lead.id, body="Yes", classification=ReplyClassification.INTERESTED)
    db_session.add(reply)
    db_session.commit()

    notify_reply(db_session, sample_advisor, sample_lead, reply)

    mock_sms.assert_not_called()


@patch("app.services.notification_service.send_email_via_provider")
@patch("app.services.sms_service.send_plain_sms")
def test_notify_reply_skips_sms_when_enabled_but_no_phone_set(mock_sms, mock_email, db_session, sample_lead, sample_advisor):
    """notify_via_sms=True with no notification_phone set must not crash or attempt a send to nowhere."""
    mock_email.return_value = {"success": True, "provider_message_id": "x", "error": None}
    sample_advisor.notify_via_sms = True
    sample_advisor.notification_phone = None
    db_session.commit()

    reply = Reply(lead_id=sample_lead.id, body="Yes", classification=ReplyClassification.INTERESTED)
    db_session.add(reply)
    db_session.commit()

    notification = notify_reply(db_session, sample_advisor, sample_lead, reply)

    mock_sms.assert_not_called()
    assert notification.id is not None


@patch("app.services.notification_service.send_email_via_provider")
@patch("app.services.sms_service.send_plain_sms")
def test_notify_reply_sms_failure_recorded_without_breaking_email_success(mock_sms, mock_email, db_session, sample_lead, sample_advisor):
    """SMS failing must never overwrite or block the email channel succeeding - SMS is a best-effort add-on."""
    mock_email.return_value = {"success": True, "provider_message_id": "x", "error": None}
    mock_sms.return_value = {"success": False, "twilio_sid": None, "error": "Invalid phone number"}
    sample_advisor.notify_via_sms = True
    sample_advisor.notification_phone = "+1bad"
    db_session.commit()

    reply = Reply(lead_id=sample_lead.id, body="Yes", classification=ReplyClassification.INTERESTED)
    db_session.add(reply)
    db_session.commit()

    notification = notify_reply(db_session, sample_advisor, sample_lead, reply)

    assert notification.is_sent is True  # email channel still succeeded
    assert "SMS alert failed" in notification.send_failure_reason
    assert "Invalid phone number" in notification.send_failure_reason
