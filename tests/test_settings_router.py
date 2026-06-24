"""
Tests for app/routers/settings_router.py - GET /settings/profile,
PUT /settings/twilio, PUT /settings/notifications.

This router had ZERO test coverage before. That gap is exactly how two
real bugs shipped unnoticed: (1) microsoft_365_connected and
microsoft_email_address were missing entirely from ProfileResponse, so
the frontend had no way to even know if Microsoft 365 was connected, and
(2) Settings.jsx referenced setMicrosoftMessage in a useEffect without
ever declaring that state - which would throw a ReferenceError and crash
the page the moment a real Microsoft OAuth redirect ever completed.
"""


def test_get_profile_includes_microsoft_365_fields(client, db_session, sample_advisor, auth_headers):
    """Regression test: these two fields were missing entirely from the response before this fix."""
    response = client.get("/settings/profile", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert "microsoft_365_connected" in body
    assert "microsoft_email_address" in body
    assert body["microsoft_365_connected"] is False
    assert body["microsoft_email_address"] is None


def test_get_profile_reflects_microsoft_365_connected_true(client, db_session, sample_advisor, auth_headers):
    sample_advisor.microsoft_365_connected = True
    sample_advisor.microsoft_email_address = "advisor@restland.onmicrosoft.com"
    db_session.commit()

    response = client.get("/settings/profile", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["microsoft_365_connected"] is True
    assert body["microsoft_email_address"] == "advisor@restland.onmicrosoft.com"


def test_get_profile_includes_google_calendar_status(client, db_session, sample_advisor, auth_headers):
    sample_advisor.google_calendar_connected = True
    db_session.commit()

    response = client.get("/settings/profile", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["google_calendar_connected"] is True


def test_get_profile_twilio_configured_requires_both_sid_and_token(client, db_session, sample_advisor, auth_headers):
    """twilio_configured should be False if only one of sid/token is set, not just based on sid alone."""
    sample_advisor.twilio_account_sid = "ACxxxx"
    sample_advisor.twilio_auth_token_encrypted = None
    db_session.commit()

    response = client.get("/settings/profile", headers=auth_headers)

    assert response.json()["twilio_configured"] is False


def test_update_twilio_config_persists_all_fields(client, db_session, sample_advisor, auth_headers):
    response = client.put("/settings/twilio", json={
        "twilio_account_sid": "ACnewsid",
        "twilio_auth_token": "newtoken",
        "twilio_phone_number": "+12145559999",
        "twilio_caller_id_name": "Restland Test",
    }, headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(sample_advisor)
    assert sample_advisor.twilio_account_sid == "ACnewsid"
    assert sample_advisor.twilio_phone_number == "+12145559999"
    assert sample_advisor.twilio_caller_id_name == "Restland Test"
    assert sample_advisor.twilio_auth_token_encrypted is not None
    # Never returns the raw token back
    assert "twilio_auth_token" not in response.json() or response.json().get("twilio_auth_token") != "newtoken"


def test_update_notifications_persists_fields(client, db_session, sample_advisor, auth_headers):
    response = client.put("/settings/notifications", json={
        "notification_email": "mike@simmonsstrong.com",
        "notify_on_hot_reply": False,
    }, headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(sample_advisor)
    assert sample_advisor.notification_email == "mike@simmonsstrong.com"
    assert sample_advisor.notify_on_hot_reply is False


# ---------------------------------------------------------------------------
# notification_phone / notify_via_sms - added for SMS-to-advisor alerts,
# the "fastest channel" Mike explicitly asked for, on top of email.
# ---------------------------------------------------------------------------

def test_profile_includes_notification_phone_and_notify_via_sms_fields(client, db_session, sample_advisor, auth_headers):
    response = client.get("/settings/profile", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert "notification_phone" in body
    assert "notify_via_sms" in body
    assert body["notification_phone"] is None
    assert body["notify_via_sms"] is False


def test_update_notifications_persists_phone_and_sms_toggle(client, db_session, sample_advisor, auth_headers):
    response = client.put("/settings/notifications", json={
        "notification_email": "mike@simmonsstrong.com",
        "notification_phone": "+12145551234",
        "notify_on_hot_reply": True,
        "notify_via_sms": True,
    }, headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(sample_advisor)
    assert sample_advisor.notification_phone == "+12145551234"
    assert sample_advisor.notify_via_sms is True


def test_update_notifications_rejects_sms_enabled_with_no_phone_in_request_or_existing(client, db_session, sample_advisor, auth_headers):
    """Enabling SMS alerts with no phone number anywhere would silently do nothing - this must be a clear 400, not a no-op."""
    sample_advisor.notification_phone = None
    db_session.commit()

    response = client.put("/settings/notifications", json={
        "notify_on_hot_reply": True,
        "notify_via_sms": True,
    }, headers=auth_headers)

    assert response.status_code == 400


def test_update_notifications_allows_sms_enabled_when_phone_already_set_previously(client, db_session, sample_advisor, auth_headers):
    """If a phone was already saved in an earlier request, enabling SMS now (without resending the phone) should still work."""
    sample_advisor.notification_phone = "+12145551111"
    db_session.commit()

    response = client.put("/settings/notifications", json={
        "notify_on_hot_reply": True,
        "notify_via_sms": True,
    }, headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(sample_advisor)
    assert sample_advisor.notify_via_sms is True
