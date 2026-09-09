"""PER-DEVICE SESSIONS — the tests the mobile app cannot ship without.

The failure these exist to prevent is not subtle and it is not theoretical: for
the life of `users.session_token`, signing in on a phone signed the desktop out,
and the web client's 30-minute refresh signed the phone out twice an hour.
Multi-device support is easy to add by DELETING the session check, which is why
half of this file is about revocation still working afterwards. Every test below
that ends in "...is refused" is guarding against the cheap fix.
"""

import pytest

from app.services.auth_service import create_access_token
from app.services import session_service
from app.models.session_models import UserSession

PASSWORD = "TestPass123!"

PHONE = {"X-Client-Platform": "ios", "X-Device-Id": "device-phone-1",
         "X-Device-Name": "Mike's iPhone", "X-App-Version": "1.0.0"}
LAPTOP = {"X-Client-Platform": "web", "X-Device-Id": "device-laptop-1",
          "X-Device-Name": "Office MacBook", "X-App-Version": "web"}


@pytest.fixture()
def god_headers(db_session):
    """The owner. `organization_id IS NULL` by positive assertion — control
    plane, no tenant. /admin/users/{id}/reset-password is above org_admin."""
    from app.models.models import User
    from app.services.auth_service import hash_password
    god = User(organization_id=None, email="owner@evosyspro.live",
               password_hash=hash_password("owner-pass-not-used"),
               full_name="Platform Owner", role="god_admin",
               must_change_password=False)
    db_session.add(god)
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(god, db_session)}


def _login(client, email, headers=None):
    r = client.post("/auth/login", data={"username": email, "password": PASSWORD},
                    headers=headers or {})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer %s" % r.json()["access_token"]}


def _alive(client, headers):
    return client.get("/auth/my-contexts", headers=headers).status_code == 200


# ── the thing that was impossible before ────────────────────────────────────

def test_a_phone_and_a_desktop_are_signed_in_at_the_same_time(client, sample_advisor):
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)
    assert _alive(client, phone), "the second login signed the first one out"
    assert _alive(client, laptop)


def test_more_than_two_devices_all_stay_live(client, sample_advisor):
    sessions = [_login(client, sample_advisor.email,
                       {**PHONE, "X-Device-Id": "d-%d" % i}) for i in range(4)]
    assert all(_alive(client, h) for h in sessions)


def test_refresh_on_one_device_leaves_the_other_alone(client, sample_advisor):
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)

    r = client.post("/auth/refresh", headers=laptop)
    assert r.status_code == 200
    refreshed = {"Authorization": "Bearer %s" % r.json()["access_token"]}

    assert _alive(client, phone), "a desktop refresh killed the phone session"
    assert _alive(client, refreshed)


def test_the_token_a_refresh_replaced_stops_working(client, sample_advisor):
    """Rotation is in place: the old jti no longer exists, so the old token is
    dead. Multi-device support must not turn refresh into 'both tokens work
    forever', which would leave a stolen token valid for its full lifetime."""
    laptop = _login(client, sample_advisor.email, LAPTOP)
    r = client.post("/auth/refresh", headers=laptop)
    assert r.status_code == 200
    assert not _alive(client, laptop)


def test_refresh_does_not_pile_up_sessions(client, db_session, sample_advisor):
    """The old endpoint minted a session per refresh. Every 30 minutes, forever."""
    laptop = _login(client, sample_advisor.email, LAPTOP)
    before = len(session_service.live_sessions_for_user(db_session, sample_advisor.id))
    for _ in range(3):
        r = client.post("/auth/refresh", headers=laptop)
        laptop = {"Authorization": "Bearer %s" % r.json()["access_token"]}
    after = len(session_service.live_sessions_for_user(db_session, sample_advisor.id))
    assert after == before


def test_signing_in_again_on_the_same_device_replaces_its_session(
        client, db_session, sample_advisor):
    _login(client, sample_advisor.email, PHONE)
    _login(client, sample_advisor.email, PHONE)
    live = session_service.live_sessions_for_user(db_session, sample_advisor.id)
    assert len([s for s in live if s.device_id == "device-phone-1"]) == 1


# ── logout, per device and everywhere ───────────────────────────────────────

def test_logging_out_on_the_phone_leaves_the_desktop_signed_in(client, sample_advisor):
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)

    r = client.post("/auth/logout", headers=phone)
    assert r.status_code == 200
    assert r.json()["scope"] == "this_device"

    assert not _alive(client, phone)
    assert _alive(client, laptop)


def test_logout_all_ends_every_device(client, sample_advisor):
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)

    r = client.post("/auth/logout-all", headers=laptop)
    assert r.status_code == 200
    assert r.json()["sessions_ended"] >= 2

    assert not _alive(client, phone)
    assert not _alive(client, laptop)


def test_a_revoked_session_cannot_refresh_itself_back_to_life(client, sample_advisor):
    """The one property that makes revocation worth having."""
    phone = _login(client, sample_advisor.email, PHONE)
    client.post("/auth/logout", headers=phone)
    assert client.post("/auth/refresh", headers=phone).status_code == 401


def test_an_expired_session_row_is_refused(client, db_session, sample_advisor):
    from datetime import datetime, timedelta, timezone
    phone = _login(client, sample_advisor.email, PHONE)
    row = session_service.live_sessions_for_user(db_session, sample_advisor.id)[0]
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    assert not _alive(client, phone)


# ── the session list ────────────────────────────────────────────────────────

def test_the_session_list_shows_this_persons_devices_and_marks_the_current_one(
        client, sample_advisor):
    _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)

    r = client.get("/auth/sessions", headers=laptop)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    names = {s["device_name"] for s in body["sessions"]}
    assert names == {"Mike's iPhone", "Office MacBook"}
    assert sum(1 for s in body["sessions"] if s["is_current"]) == 1


def test_the_session_list_never_returns_a_credential(client, sample_advisor):
    laptop = _login(client, sample_advisor.email, LAPTOP)
    body = client.get("/auth/sessions", headers=laptop).json()
    for s in body["sessions"]:
        assert "jti" not in s


def test_the_session_list_is_scoped_to_the_caller(client, sample_advisor,
                                                  second_advisor):
    _login(client, second_advisor.email, PHONE)
    mine = _login(client, sample_advisor.email, LAPTOP)
    body = client.get("/auth/sessions", headers=mine).json()
    assert body["count"] == 1


def test_revoking_someone_elses_session_is_a_404(client, db_session,
                                                 sample_advisor, second_advisor):
    _login(client, second_advisor.email, PHONE)
    theirs = session_service.live_sessions_for_user(db_session, second_advisor.id)[0]
    mine = _login(client, sample_advisor.email, LAPTOP)

    r = client.delete("/auth/sessions/%s" % theirs.id, headers=mine)
    assert r.status_code == 404
    assert theirs.is_live, "another user's session was ended"


def test_a_person_can_sign_out_one_of_their_own_devices(client, db_session,
                                                        sample_advisor):
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)
    phone_row = [s for s in session_service.live_sessions_for_user(
        db_session, sample_advisor.id) if s.device_id == "device-phone-1"][0]

    r = client.delete("/auth/sessions/%s" % phone_row.id, headers=laptop)
    assert r.status_code == 200
    assert not _alive(client, phone)
    assert _alive(client, laptop)


# ── revocation must not have got weaker ─────────────────────────────────────

def test_a_password_change_ends_every_device(client, sample_advisor):
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)

    r = client.post("/auth/change-password", headers=laptop,
                    json={"current_password": PASSWORD,
                          "new_password": "BrandNewPass123!",
                          "confirm_password": "BrandNewPass123!"})
    assert r.status_code == 200
    assert not _alive(client, phone), "the phone stayed signed in after a password change"
    assert not _alive(client, laptop)


def test_an_administrative_force_logout_ends_every_device(
        client, db_session, sample_advisor, admin_auth_headers):
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)

    r = client.post("/admin/users/%s/force-logout" % sample_advisor.id,
                    headers=admin_auth_headers)
    assert r.status_code == 200
    assert not _alive(client, phone)
    assert not _alive(client, laptop)


def test_deactivating_an_account_ends_every_device(
        client, db_session, sample_advisor, admin_auth_headers):
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)

    r = client.patch("/admin/users/%s/deactivate" % sample_advisor.id,
                     headers=admin_auth_headers)
    assert r.status_code == 200
    assert not _alive(client, phone)
    assert not _alive(client, laptop)


def test_an_administrative_reset_ends_every_device(
        client, db_session, sample_advisor, god_headers):
    phone = _login(client, sample_advisor.email, PHONE)
    laptop = _login(client, sample_advisor.email, LAPTOP)

    r = client.post("/admin/users/%s/reset-password" % sample_advisor.id,
                    headers=god_headers,
                    json={"new_password": "ResetPass123!",
                          "confirm_password": "ResetPass123!"})
    assert r.status_code == 200
    assert not _alive(client, phone)
    assert not _alive(client, laptop)


def test_one_persons_logout_all_does_not_touch_anybody_else(
        client, sample_advisor, second_advisor):
    theirs = _login(client, second_advisor.email, PHONE)
    mine = _login(client, sample_advisor.email, LAPTOP)
    client.post("/auth/logout-all", headers=mine)
    assert _alive(client, theirs)


# ── tokens minted before this table existed ─────────────────────────────────

def test_a_legacy_token_with_no_session_row_still_works(client, db_session,
                                                        sample_advisor):
    """A deploy must not sign the whole customer base out.

    A token issued under the old rules has a jti that matches
    users.session_token and no row of its own. `get_current_user` falls back to
    the column for exactly that case.
    """
    token = create_access_token(sample_advisor, db_session)
    headers = {"Authorization": "Bearer %s" % token}
    db_session.query(UserSession).filter(
        UserSession.user_id == sample_advisor.id).delete()
    db_session.commit()
    assert _alive(client, headers)


def test_a_legacy_token_dies_when_the_column_is_cleared(client, db_session,
                                                        sample_advisor):
    token = create_access_token(sample_advisor, db_session)
    headers = {"Authorization": "Bearer %s" % token}
    db_session.query(UserSession).filter(
        UserSession.user_id == sample_advisor.id).delete()
    sample_advisor.session_token = None
    db_session.commit()
    assert not _alive(client, headers)


def test_a_revoked_row_beats_a_matching_legacy_column(client, db_session,
                                                      sample_advisor):
    """THE ORDERING TEST. If the column were consulted first — or consulted as
    a fallback after a revoked row — revoking a session would do nothing while
    users.session_token still happened to hold the same jti. It does hold it:
    create_access_token writes both."""
    token = create_access_token(sample_advisor, db_session)
    headers = {"Authorization": "Bearer %s" % token}
    row = session_service.live_sessions_for_user(db_session, sample_advisor.id)[0]
    assert sample_advisor.session_token == row.jti     # the trap is real
    session_service.revoke(db_session, row)
    assert not _alive(client, headers)
