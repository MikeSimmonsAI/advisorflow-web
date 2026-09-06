"""CHANGING YOUR OWN PASSWORD — including the platform owner's.

POST /auth/change-password. It existed and was correct on the part that matters
most: it demands the CURRENT password before setting a new one, even though the
JWT already authenticates the caller. What it had no coverage for was anything,
and what it did not do was end the sessions the old password had opened.

WHY THIS IS NOT THE ADMINISTRATIVE RESET

/admin/users/{id}/reset-password never asks for a current password - it does not
need to, because the caller is proving authority over somebody else's account,
not over their own. Pointing that at yourself would mean a borrowed unlocked
browser is enough to take an account permanently. So self-change is a different
endpoint with a different question at the front of it, and the God Mode user
screen refuses self-actions for exactly this reason.

WHAT THESE TESTS DEFEND

  * the current password is required, and a wrong one changes nothing
  * a successful change works for a god_admin whose organization_id is NULL
  * the old password stops working and the new one starts
  * every previously issued token for that account stops working, and the
    caller is handed a working replacement rather than being locked out
  * nobody else's session is touched
  * no password is ever returned or logged
"""

import json

import pytest

from app.models.models import AuditLogEntry, User
from app.services.auth_service import (create_access_token, hash_password,
                                       verify_password)

OLD_PASSWORD = "TestPass123!"
NEW_PASSWORD = "MyOwnN3wPass!"


def _headers(db, user):
    return {"Authorization": f"Bearer {create_access_token(user, db)}"}


def _reload(db, user):
    """Re-read the row instead of Session.refresh().

    get_current_user() calls db.expunge(user) for a god_admin (see the comment
    in change_password about detached rows), so the object this test handed to
    _headers is no longer in the identity map by the time the request finishes
    and refresh() raises "not persistent within this Session". Re-querying by
    id is unaffected and reads exactly what was committed.
    """
    return db.query(User).filter(User.id == user.id).one()


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    """/auth/change-password is limited to 10/hour PER CLIENT, and every test
    in this file is the same client ("testclient"), so without this the file
    silently starts returning 429 partway down and the assertions that follow
    are testing the rate limiter rather than the endpoint. Reset between tests
    so each one starts from a clean allowance."""
    from app.main import app
    limiter = getattr(app.state, "limiter", None)
    previously_enabled = getattr(limiter, "enabled", None) if limiter else None
    if limiter is not None:
        try:
            limiter.reset()
        except Exception:
            limiter.enabled = False
    yield
    if limiter is not None and previously_enabled is not None:
        limiter.enabled = previously_enabled


@pytest.fixture()
def god_admin(db_session):
    """organization_id IS NULL — the control-plane shape. get_current_user
    expunges such a user in some paths, which is why change_password re-reads
    the row; a god_admin is therefore the case most worth testing."""
    god = User(organization_id=None, email="owner@evosyspro.live",
               password_hash=hash_password(OLD_PASSWORD),
               full_name="Platform Owner", role="god_admin",
               must_change_password=False)
    db_session.add(god)
    db_session.commit()
    return god


# ═════════════════════════════════════════════════════════════════════════════
# 1. The current password is the gate
# ═════════════════════════════════════════════════════════════════════════════

def test_wrong_current_password_is_refused(client, db_session, god_admin):
    before = god_admin.password_hash
    r = client.post("/auth/change-password",
                    json={"current_password": "not-my-password",
                          "new_password": NEW_PASSWORD},
                    headers=_headers(db_session, god_admin))
    assert r.status_code == 400
    assert "current password" in r.json()["detail"].lower()
    after = _reload(db_session, god_admin)
    assert after.password_hash == before
    assert verify_password(OLD_PASSWORD, after.password_hash)


def test_a_wrong_current_password_does_not_end_the_session(client, db_session, god_admin):
    """A failed attempt must not rotate the session token - that would let
    anyone who can reach the endpoint sign the real owner out by guessing."""
    headers = _headers(db_session, god_admin)
    client.post("/auth/change-password",
                json={"current_password": "wrong", "new_password": NEW_PASSWORD},
                headers=headers)
    assert client.get("/auth/my-contexts", headers=headers).status_code == 200


def test_unauthenticated_cannot_change_a_password(client):
    r = client.post("/auth/change-password",
                    json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD})
    assert r.status_code == 401


def test_a_short_new_password_is_refused(client, db_session, god_admin):
    before = god_admin.password_hash
    r = client.post("/auth/change-password",
                    json={"current_password": OLD_PASSWORD, "new_password": "short"},
                    headers=_headers(db_session, god_admin))
    # 422 from the schema's min_length, 400 from the handler's own check -
    # both are refusals, and which one fires is not the point of this test.
    assert r.status_code in (400, 422)
    assert _reload(db_session, god_admin).password_hash == before


def test_a_mismatched_confirmation_is_refused(client, db_session, god_admin):
    """Enforced on the SERVER. The form compares them too, but anything calling
    the API directly would skip that, and a mistyped new password on your own
    account is unrecoverable without an administrator."""
    before = god_admin.password_hash
    r = client.post("/auth/change-password",
                    json={"current_password": OLD_PASSWORD,
                          "new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD + "typo"},
                    headers=_headers(db_session, god_admin))
    assert r.status_code == 400
    assert "match" in r.json()["detail"].lower()
    after = _reload(db_session, god_admin)
    assert after.password_hash == before
    assert verify_password(OLD_PASSWORD, after.password_hash)


def test_a_matching_confirmation_is_accepted(client, db_session, god_admin):
    r = client.post("/auth/change-password",
                    json={"current_password": OLD_PASSWORD,
                          "new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD},
                    headers=_headers(db_session, god_admin))
    assert r.status_code == 200
    assert verify_password(NEW_PASSWORD, _reload(db_session, god_admin).password_hash)


# ═════════════════════════════════════════════════════════════════════════════
# 2. The successful change
# ═════════════════════════════════════════════════════════════════════════════

def test_the_god_admin_can_change_their_own_password(client, db_session, god_admin):
    r = client.post("/auth/change-password",
                    json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                    headers=_headers(db_session, god_admin))
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert verify_password(NEW_PASSWORD, _reload(db_session, god_admin).password_hash)


def test_an_ordinary_user_can_still_change_their_own_password(
        client, db_session, sample_advisor):
    """The endpoint is not owner-only and must not become so."""
    r = client.post("/auth/change-password",
                    json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                    headers=_headers(db_session, sample_advisor))
    assert r.status_code == 200
    assert verify_password(NEW_PASSWORD, _reload(db_session, sample_advisor).password_hash)


def test_the_old_password_is_rejected_afterwards(client, db_session, god_admin):
    client.post("/auth/change-password",
                json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                headers=_headers(db_session, god_admin))
    r = client.post("/auth/login", data={"username": god_admin.email,
                                         "password": OLD_PASSWORD})
    assert r.status_code == 401


def test_the_new_password_is_accepted_afterwards(client, db_session, god_admin):
    client.post("/auth/change-password",
                json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                headers=_headers(db_session, god_admin))
    r = client.post("/auth/login", data={"username": god_admin.email,
                                         "password": NEW_PASSWORD})
    assert r.status_code == 200
    assert r.json().get("access_token")


def test_a_forced_change_clears_the_flag(client, db_session, god_admin):
    god_admin.must_change_password = True
    db_session.commit()
    r = client.post("/auth/change-password",
                    json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                    headers=_headers(db_session, god_admin))
    assert r.status_code == 200
    assert _reload(db_session, god_admin).must_change_password is False


def test_an_administrative_reset_then_a_self_change_works_end_to_end(
        client, db_session, god_admin, sample_advisor):
    """The two flows compose: the owner resets somebody with a forced change,
    that person signs in and sets their own password, and the temporary one
    dies. This is the whole point of the must_change_password option."""
    temp = "Temp0rary!Pass"
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": temp, "confirm_password": temp,
                          "must_change_password": True},
                    headers=_headers(db_session, god_admin))
    assert r.status_code == 200

    login = client.post("/auth/login", data={"username": sample_advisor.email,
                                             "password": temp})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    changed = client.post("/auth/change-password",
                          json={"current_password": temp, "new_password": NEW_PASSWORD},
                          headers=headers)
    assert changed.status_code == 200

    db_session.refresh(sample_advisor)
    assert sample_advisor.must_change_password is False
    assert client.post("/auth/login", data={"username": sample_advisor.email,
                                            "password": temp}).status_code == 401
    assert client.post("/auth/login", data={"username": sample_advisor.email,
                                            "password": NEW_PASSWORD}).status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# 3. Every session dies, including the caller's — a fresh login is required
# ═════════════════════════════════════════════════════════════════════════════

def test_the_callers_own_token_stops_working(client, db_session, god_admin):
    """A fresh login is REQUIRED afterwards. The token that made the change is
    itself refused on the next request, because it was minted from a session
    that began under the old password."""
    caller = _headers(db_session, god_admin)
    assert client.get("/auth/my-contexts", headers=caller).status_code == 200
    r = client.post("/auth/change-password",
                    json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                    headers=caller)
    assert r.status_code == 200
    assert r.json().get("reauthenticate") is True
    assert client.get("/auth/my-contexts", headers=caller).status_code == 401


def test_no_replacement_token_is_handed_back(client, db_session, god_admin):
    """The response must not carry a credential. Returning one would keep the
    browser signed in on a session that predates the change, which is the
    opposite of requiring a fresh login."""
    r = client.post("/auth/change-password",
                    json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                    headers=_headers(db_session, god_admin))
    assert r.status_code == 200
    assert "access_token" not in r.json()


def test_a_second_previously_issued_token_also_stops_working(client, db_session, god_admin):
    """The stolen-token case. Somebody else holding a token for this account
    loses it the moment the password changes - that is the entire reason a
    person changes a password they believe is known."""
    stolen = _headers(db_session, god_admin)
    caller = _headers(db_session, god_admin)
    client.post("/auth/change-password",
                json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                headers=caller)
    assert client.get("/auth/my-contexts", headers=stolen).status_code == 401


def test_signing_in_again_with_the_new_password_restores_access(
        client, db_session, god_admin):
    """The required fresh login works and yields a usable session."""
    client.post("/auth/change-password",
                json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                headers=_headers(db_session, god_admin))
    login = client.post("/auth/login", data={"username": god_admin.email,
                                             "password": NEW_PASSWORD})
    assert login.status_code == 200
    fresh = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/auth/my-contexts", headers=fresh).status_code == 200


def test_nobody_elses_session_is_affected(client, db_session, god_admin, sample_advisor):
    advisor_headers = _headers(db_session, sample_advisor)
    assert client.get("/auth/my-contexts", headers=advisor_headers).status_code == 200
    client.post("/auth/change-password",
                json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                headers=_headers(db_session, god_admin))
    assert client.get("/auth/my-contexts", headers=advisor_headers).status_code == 200
    assert verify_password(OLD_PASSWORD,
                           _reload(db_session, sample_advisor).password_hash)


# ═════════════════════════════════════════════════════════════════════════════
# 4. The password never escapes
# ═════════════════════════════════════════════════════════════════════════════

def test_no_password_appears_in_the_response(client, db_session, god_admin):
    r = client.post("/auth/change-password",
                    json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                    headers=_headers(db_session, god_admin))
    assert r.status_code == 200
    assert NEW_PASSWORD not in r.text
    assert OLD_PASSWORD not in r.text


def test_the_row_stores_a_bcrypt_hash_and_not_the_password(client, db_session, god_admin):
    client.post("/auth/change-password",
                json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                headers=_headers(db_session, god_admin))
    stored = _reload(db_session, god_admin).password_hash
    assert stored != NEW_PASSWORD
    assert NEW_PASSWORD not in stored
    assert stored.startswith("$2")        # bcrypt, via auth_service.hash_password
    assert verify_password(NEW_PASSWORD, stored)


def test_no_audit_row_carries_the_password(client, db_session, god_admin):
    """This endpoint writes no audit row today. If one is ever added, it must
    not carry the credential - so the assertion is about content, not count."""
    client.post("/auth/change-password",
                json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
                headers=_headers(db_session, god_admin))
    rows = db_session.query(AuditLogEntry).all()
    blob = json.dumps([{"a": r.action, "d": r.details, "n": r.note,
                        "b": r.before_state, "f": r.after_state} for r in rows])
    assert NEW_PASSWORD not in blob
    assert OLD_PASSWORD not in blob
