"""PUSH REGISTRATION AND FIELD UPLOADS.

Two things are being protected here, and neither of them is "does the endpoint
return 200".

  1. A PUSH TOKEN HAS ONE OWNER. Phones are reset, sold and handed to the next
     hire, and the push token can outlive the handover. A registration that
     duplicated instead of moving would put one person's notifications on
     another person's lock screen — a leak whose viewport is the notification
     shade, outside every authorisation boundary the API has.

  2. A PAYLOAD CARRIES IDS, NOT CONTENT. Delivery is asynchronous, so authority
     checked at creation time can be stale by the time the phone buzzes. The
     tap re-fetches through the normal authorised endpoint; the payload itself
     must never be worth reading.
"""

import pytest

from app.models.device_models import DevicePushToken
from app.services import push_service
from app.services.auth_service import create_access_token

TOKEN_A = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"
TOKEN_B = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"


def _headers(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _register(client, headers, token=TOKEN_A, **extra):
    body = {"token": token, "platform": "ios", "device_id": "dev-1",
            "device_name": "Mike's iPhone", "app_version": "1.0.0"}
    body.update(extra)
    return client.post("/me/devices", json=body, headers=headers)


# ── registration ────────────────────────────────────────────────────────────

def test_a_device_registers_and_is_listed(client, db_session, sample_advisor):
    h = _headers(db_session, sample_advisor)
    assert _register(client, h).status_code == 200

    body = client.get("/me/devices", headers=h).json()
    assert len(body["devices"]) == 1
    assert body["devices"][0]["device_name"] == "Mike's iPhone"


def test_the_device_list_never_returns_the_token(client, db_session, sample_advisor):
    h = _headers(db_session, sample_advisor)
    _register(client, h)
    body = client.get("/me/devices", headers=h).json()
    for d in body["devices"]:
        assert "token" not in d


def test_registration_is_owned_by_the_callers_session_not_the_body(
        client, db_session, sample_advisor):
    """A client that could name its own session id could attach its push token
    to somebody else's device. The body has no such field, and the session is
    read from the row get_current_user already resolved."""
    h = _headers(db_session, sample_advisor)
    _register(client, h, session_id="somebody-elses-session")
    row = db_session.query(DevicePushToken).filter(
        DevicePushToken.token == TOKEN_A).first()
    assert row.session_id != "somebody-elses-session"
    assert row.user_id == sample_advisor.id


def test_the_same_token_moves_between_owners_instead_of_duplicating(
        client, db_session, sample_advisor, second_advisor):
    _register(client, _headers(db_session, sample_advisor))
    _register(client, _headers(db_session, second_advisor))

    rows = db_session.query(DevicePushToken).filter(
        DevicePushToken.token == TOKEN_A).all()
    assert len(rows) == 1, "one physical device held two live registrations"
    assert rows[0].user_id == second_advisor.id


def test_a_reassigned_token_stops_reaching_the_previous_owner(
        client, db_session, sample_advisor, second_advisor):
    _register(client, _headers(db_session, sample_advisor))
    _register(client, _headers(db_session, second_advisor))

    assert push_service.tokens_for_user(db_session, sample_advisor.id) == []
    assert len(push_service.tokens_for_user(db_session, second_advisor.id)) == 1


def test_one_person_never_sees_another_persons_devices(
        client, db_session, sample_advisor, second_advisor):
    _register(client, _headers(db_session, second_advisor), token=TOKEN_B)
    mine = _headers(db_session, sample_advisor)
    _register(client, mine, token=TOKEN_A)

    body = client.get("/me/devices", headers=mine).json()
    assert len(body["devices"]) == 1


def test_unregistering_someone_elses_token_changes_nothing(
        client, db_session, sample_advisor, second_advisor):
    _register(client, _headers(db_session, second_advisor), token=TOKEN_B)
    mine = _headers(db_session, sample_advisor)

    r = client.request("DELETE", "/me/devices", json={"token": TOKEN_B},
                       headers=mine)
    assert r.status_code == 200          # idempotent, and reveals nothing
    row = db_session.query(DevicePushToken).filter(
        DevicePushToken.token == TOKEN_B).first()
    assert row.is_active is True


def test_a_person_can_unregister_their_own_device(client, db_session, sample_advisor):
    h = _headers(db_session, sample_advisor)
    _register(client, h)
    assert client.request("DELETE", "/me/devices", json={"token": TOKEN_A},
                          headers=h).status_code == 200
    assert client.get("/me/devices", headers=h).json()["devices"] == []


def test_an_unknown_platform_is_refused(client, db_session, sample_advisor):
    h = _headers(db_session, sample_advisor)
    assert _register(client, h, platform="blackberry").status_code == 400


def test_registration_requires_authentication(client):
    assert client.post("/me/devices", json={"token": TOKEN_A,
                                            "platform": "ios"}).status_code == 401


def test_ending_a_session_stops_pushing_to_that_device(
        client, db_session, sample_advisor):
    """Signing out is not only about the API. A device that keeps receiving
    notifications after the person signed out of it is still receiving their
    work on a screen they may have handed to somebody else."""
    from app.services import session_service

    r = client.post("/auth/login",
                    data={"username": sample_advisor.email,
                          "password": "TestPass123!"},
                    headers={"X-Device-Id": "dev-1", "X-Client-Platform": "ios"})
    h = {"Authorization": "Bearer %s" % r.json()["access_token"]}
    _register(client, h)

    client.post("/auth/logout", headers=h)
    assert push_service.tokens_for_user(db_session, sample_advisor.id) == []


# ── payload discipline ──────────────────────────────────────────────────────

def test_a_payload_carries_ids_and_a_title_and_nothing_else():
    p = push_service.build_payload(
        category=push_service.CAT_LEAD,
        title="A lead needs attention",
        record_type="lead", record_id="lead-123",
        # Everything below is the kind of thing a caller adds "just this once".
        lead_name="Angela Ruiz", phone="+12145550000",
        message="Please call me back about my mother's plot",
    )
    flat = str(p)
    assert "Angela" not in flat
    assert "2145550000" not in flat
    assert "mother" not in flat
    assert p["data"]["record_id"] == "lead-123"


def test_free_text_is_truncated_rather_than_trusted():
    p = push_service.build_payload(category=push_service.CAT_SYSTEM,
                                   title="x" * 500, body="y" * 500)
    assert len(p["title"]) <= 80
    assert len(p["body"]) <= 120


def test_an_unknown_category_falls_back_rather_than_passing_through():
    p = push_service.build_payload(category="../../etc/passwd", title="hi")
    assert p["data"]["category"] == push_service.CAT_SYSTEM


def test_sending_is_off_until_it_is_deliberately_turned_on(
        client, db_session, sample_advisor, monkeypatch):
    """A deploy must not start talking to a third party by accident, and a
    staging environment sharing a database must not wake real phones."""
    monkeypatch.delenv("EXPO_PUSH_ENABLED", raising=False)
    _register(client, _headers(db_session, sample_advisor))
    report = push_service.notify(db_session, sample_advisor.id,
                                 category=push_service.CAT_SYSTEM, title="hi")
    assert report["sent"] == 0
    assert report["skipped"] == "push_disabled"


def test_a_user_with_no_devices_is_not_an_error(db_session, sample_advisor):
    report = push_service.notify(db_session, sample_advisor.id,
                                 category=push_service.CAT_SYSTEM, title="hi")
    assert report["skipped"] == "no_devices"


# ── uploads ─────────────────────────────────────────────────────────────────

def test_the_app_is_told_the_truth_about_storage(client, db_session, sample_advisor,
                                                 monkeypatch):
    monkeypatch.delenv("MEDIA_STORAGE_BACKEND", raising=False)
    h = _headers(db_session, sample_advisor)
    body = client.get("/me/upload-capability", headers=h).json()
    assert body["durable"] is False
    assert body["uploads_enabled"] is False
    assert body["reason"]


def test_an_upload_into_ephemeral_storage_is_refused_not_swallowed(
        client, db_session, sample_advisor, monkeypatch):
    """A 200 that evaporates on the next restart is worse than a refusal: the
    capture is gone from the device queue and gone from the server."""
    monkeypatch.delenv("MEDIA_STORAGE_BACKEND", raising=False)
    h = _headers(db_session, sample_advisor)
    r = client.post("/me/uploads", headers=h,
                    files={"file": ("plot.jpg", b"\xff\xd8\xff", "image/jpeg")})
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


def test_a_half_configured_bucket_does_not_report_durable(monkeypatch):
    from app.services import mobile_storage
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("MEDIA_S3_BUCKET", "evosys-media")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    assert mobile_storage.capability()["durable"] is False


def test_an_upload_key_never_contains_the_uploaders_filename(monkeypatch):
    from app.services import mobile_storage

    class _U:
        id = "user-1"

    key = mobile_storage._object_key(_U(), "../../../etc/passwd.jpg", "capture")
    assert ".." not in key
    assert "passwd" not in key
    assert key.endswith(".jpg")


def test_upload_capability_requires_authentication(client):
    assert client.get("/me/upload-capability").status_code == 401
