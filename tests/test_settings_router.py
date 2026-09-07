"""
Tests for app/routers/settings_router.py - GET /settings/profile,
PUT /settings/twilio (now retired, 410 Gone),
PUT /settings/admin/twilio/{user_id} (its replacement),
PUT /settings/notifications.

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


def test_self_service_twilio_write_is_retired_and_changes_nothing(client, db_session, sample_advisor, auth_headers):
    """
    PUT /settings/twilio is gone, not merely gated.

    It carried no role check at all, so any advisor could write their own
    Twilio account SID and auth token and point their sends at an account
    of their choosing - off the organization's registered A2P campaign and
    its carrier reputation, and billed wherever they liked. Credentials now
    belong to the organization, so the route answers 410 Gone: this is not
    a permission that could be granted, it is a capability that no longer
    exists.

    The important half of the assertion is the second one - a 410 that
    still quietly persisted the credentials would be worse than the
    original bug.
    """
    before_sid = sample_advisor.twilio_account_sid
    before_token = sample_advisor.twilio_auth_token_encrypted

    response = client.put("/settings/twilio", json={
        "twilio_account_sid": "ACnewsid",
        "twilio_auth_token": "newtoken",
        "twilio_phone_number": "+12145559999",
        "twilio_caller_id_name": "Restland Test",
    }, headers=auth_headers)

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert "organization" in detail.lower()  # explains where credentials do live

    db_session.refresh(sample_advisor)
    assert sample_advisor.twilio_account_sid == before_sid
    assert sample_advisor.twilio_auth_token_encrypted == before_token
    assert sample_advisor.twilio_phone_number != "+12145559999"


def test_admin_assign_twilio_persists_all_fields(client, db_session, sample_advisor, admin_auth_headers):
    """
    The replacement route, and the persistence behaviour the retired test
    was actually there to cover: an org admin assigning a sending number
    (and, optionally, credentials) to an advisor in their own organization.
    """
    response = client.put(f"/settings/admin/twilio/{sample_advisor.id}", json={
        "twilio_account_sid": "ACnewsid",
        "twilio_auth_token": "newtoken",
        "twilio_phone_number": "+12145559999",
        "twilio_caller_id_name": "Restland Test",
    }, headers=admin_auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["twilio_phone_number"] == "+12145559999"
    assert body["twilio_configured"] is True

    db_session.refresh(sample_advisor)
    assert sample_advisor.twilio_account_sid == "ACnewsid"
    assert sample_advisor.twilio_phone_number == "+12145559999"
    assert sample_advisor.twilio_caller_id_name == "Restland Test"
    assert sample_advisor.twilio_auth_token_encrypted is not None
    # Stored encrypted at rest, never as the plaintext token...
    assert sample_advisor.twilio_auth_token_encrypted != "newtoken"
    # ...and never handed back in the response.
    assert "twilio_auth_token" not in body


def test_admin_assign_twilio_rejects_a_plain_advisor(client, sample_advisor, auth_headers):
    """The replacement route is admin-only - retiring the self-service write
    would mean nothing if any advisor could just call this one instead."""
    response = client.put(f"/settings/admin/twilio/{sample_advisor.id}", json={
        "twilio_phone_number": "+12145559999",
    }, headers=auth_headers)

    assert response.status_code == 403


def test_update_notifications_persists_fields(client, db_session, sample_advisor, auth_headers):
    response = client.put("/settings/notifications", json={
        "notification_email": "mike@simmonsstrong.com",
        "notify_on_hot_reply": False,
    }, headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(sample_advisor)
    assert sample_advisor.notification_email == "mike@simmonsstrong.com"
    assert sample_advisor.notify_on_hot_reply is False
