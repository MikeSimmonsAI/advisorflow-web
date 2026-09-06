"""GOD MODE PASSWORD RESET — who may do it, and what it is not allowed to leak.

The endpoint is POST /admin/users/{id}/reset-password. It already existed and
was already hardened; what is new is that an administrator can now decide
whether the person must replace the password at next sign-in, and that the
confirmation is checked on the server rather than only in the browser.

FOUR THINGS THIS FILE IS DEFENDING

  1. WHO. god_admin and super_admin only. An org_admin administering their own
     organization must not be able to set anybody's password, and neither may
     an advisor. This is a stricter gate than require_admin and the tests have
     to fail if it is ever loosened to one.

  2. WHOSE. A super_admin may not reach a god_admin, and may not reach another
     platform's users. That pair is what closed the August takeover, where one
     platform operator set the owner's password and signed in as the owner.
     god_admin reaches everyone - that is the owner control plane.

  3. THE PASSWORD NEVER ESCAPES. Not in the response, not in the audit log,
     not in plaintext on the row. Only a bcrypt hash produced by the
     application's own hash_password.

  4. must_change_password IS THE CALLER'S CHOICE, and defaults to the old
     behaviour when they do not express one.
"""

import json

import pytest

from app.models.models import AuditLogEntry, Organization, Platform, User
from app.services.auth_service import (create_access_token, hash_password,
                                       verify_password)

NEW_PASSWORD = "Str0ngTemp!2026"
OLD_PASSWORD = "TestPass123!"


def _headers(db, user):
    return {"Authorization": f"Bearer {create_access_token(user, db)}"}


@pytest.fixture()
def god_admin(db_session):
    """The owner. organization_id IS NULL by positive assertion - that is how
    this architecture says somebody belongs to the control plane and to no
    tenant, and it is also the shape log_action has to tolerate."""
    god = User(organization_id=None, email="owner@evosyspro.live",
               password_hash=hash_password("owner-pass-not-used"),
               full_name="Platform Owner", role="god_admin",
               must_change_password=False)
    db_session.add(god)
    db_session.commit()
    return god


@pytest.fixture()
def god_headers(db_session, god_admin):
    return _headers(db_session, god_admin)


@pytest.fixture()
def super_admin(db_session, sample_org):
    su = User(organization_id=sample_org.id, email="platformop@restland.com",
              password_hash=hash_password("su-pass"), full_name="Platform Op",
              role="super_admin", must_change_password=False)
    db_session.add(su)
    db_session.commit()
    return su


# ═════════════════════════════════════════════════════════════════════════════
# 1. WHO may reset a password
# ═════════════════════════════════════════════════════════════════════════════

def test_advisor_cannot_reset_a_password(client, auth_headers, second_advisor):
    r = client.post(f"/admin/users/{second_advisor.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD},
                    headers=auth_headers)
    assert r.status_code == 403


def test_org_admin_cannot_reset_a_password(client, admin_auth_headers, sample_advisor):
    """The boundary Mike drew explicitly: an org_admin runs their organization
    but does not set credentials. If this ever returns 200, the guard has been
    downgraded from require_super_admin to require_admin."""
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD},
                    headers=admin_auth_headers)
    assert r.status_code == 403


def test_unauthenticated_cannot_reset_a_password(client, sample_advisor):
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD})
    assert r.status_code == 401


def test_org_admin_refusal_leaves_the_password_untouched(
        client, db_session, admin_auth_headers, sample_advisor):
    """A 403 that still wrote the hash would be worse than no guard at all."""
    before = sample_advisor.password_hash
    client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
                headers=admin_auth_headers)
    db_session.refresh(sample_advisor)
    assert sample_advisor.password_hash == before
    assert verify_password(OLD_PASSWORD, sample_advisor.password_hash)


def test_god_admin_can_reset_a_customer_user(client, db_session, god_headers, sample_advisor):
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD},
                    headers=god_headers)
    assert r.status_code == 200
    db_session.refresh(sample_advisor)
    assert verify_password(NEW_PASSWORD, sample_advisor.password_hash)


def test_god_admin_can_reset_a_super_admin(client, db_session, god_headers, super_admin):
    """The owner reaches every account. That is what the control plane is."""
    r = client.post(f"/admin/users/{super_admin.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD},
                    headers=god_headers)
    assert r.status_code == 200
    db_session.refresh(super_admin)
    assert verify_password(NEW_PASSWORD, super_admin.password_hash)


# ═════════════════════════════════════════════════════════════════════════════
# 2. WHOSE password — the takeover guard, unchanged
# ═════════════════════════════════════════════════════════════════════════════

def test_super_admin_cannot_reset_the_god_admin(client, db_session, super_admin, god_admin):
    """404, not 403: a 403 on a record you may not touch confirms it exists."""
    headers = _headers(db_session, super_admin)
    before = god_admin.password_hash
    r = client.post(f"/admin/users/{god_admin.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD},
                    headers=headers)
    assert r.status_code == 404
    db_session.refresh(god_admin)
    assert god_admin.password_hash == before


def test_super_admin_cannot_reset_another_platforms_user(client, db_session, super_admin):
    """Tenant isolation. The caller is a real platform operator; that says
    nothing about WHICH platform, which is exactly what load_user_in_scope
    exists to ask."""
    other_platform = Platform(name="Other Brand", slug="other-brand")
    db_session.add(other_platform)
    db_session.commit()
    other_org = Organization(name="Other Customer", slug="other-customer",
                             plan="standard", platform_id=other_platform.id)
    db_session.add(other_org)
    db_session.commit()
    stranger = User(organization_id=other_org.id, email="stranger@other.com",
                    password_hash=hash_password(OLD_PASSWORD),
                    full_name="Stranger", role="advisor",
                    must_change_password=False)
    db_session.add(stranger)
    db_session.commit()
    before = stranger.password_hash

    r = client.post(f"/admin/users/{stranger.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD},
                    headers=_headers(db_session, super_admin))
    assert r.status_code == 404
    db_session.refresh(stranger)
    assert stranger.password_hash == before


def test_resetting_one_user_does_not_touch_another(
        client, db_session, god_headers, sample_advisor, second_advisor):
    other_before = second_advisor.password_hash
    client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
                headers=god_headers)
    db_session.refresh(second_advisor)
    assert second_advisor.password_hash == other_before
    assert verify_password(OLD_PASSWORD, second_advisor.password_hash)


# ═════════════════════════════════════════════════════════════════════════════
# 3. must_change_password is the caller's choice
# ═════════════════════════════════════════════════════════════════════════════

def test_force_change_is_honoured(client, db_session, god_headers, sample_advisor):
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD,
                          "must_change_password": True},
                    headers=god_headers)
    assert r.status_code == 200
    assert r.json()["must_change_password"] is True
    db_session.refresh(sample_advisor)
    assert sample_advisor.must_change_password is True


def test_force_change_can_be_declined(client, db_session, god_headers, sample_advisor):
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD,
                          "must_change_password": False},
                    headers=god_headers)
    assert r.status_code == 200
    assert r.json()["must_change_password"] is False
    db_session.refresh(sample_advisor)
    assert sample_advisor.must_change_password is False


def test_omitting_the_flag_keeps_the_previous_behaviour(
        client, db_session, god_headers, sample_advisor):
    """Every caller that existed before this field did sent no flag and got
    must_change_password=False. None of them may change behaviour."""
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD},
                    headers=god_headers)
    assert r.status_code == 200
    db_session.refresh(sample_advisor)
    assert sample_advisor.must_change_password is False


def test_the_one_time_link_path_still_forces_a_change(
        client, db_session, god_headers, sample_advisor):
    """No password supplied means nobody knows the credential, so a change is
    not optional there and the flag is ignored."""
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"must_change_password": False},
                    headers=god_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["setup_url"]
    assert body["must_change_password"] is True
    db_session.refresh(sample_advisor)
    assert sample_advisor.must_change_password is True


# ═════════════════════════════════════════════════════════════════════════════
# 4. Confirmation, enforced server-side
# ═════════════════════════════════════════════════════════════════════════════

def test_mismatched_confirmation_is_refused(client, db_session, god_headers, sample_advisor):
    before = sample_advisor.password_hash
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD + "typo"},
                    headers=god_headers)
    assert r.status_code == 400
    assert "match" in r.json()["detail"].lower()
    db_session.refresh(sample_advisor)
    assert sample_advisor.password_hash == before
    assert verify_password(OLD_PASSWORD, sample_advisor.password_hash)


def test_a_short_password_is_refused(client, db_session, god_headers, sample_advisor):
    before = sample_advisor.password_hash
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": "short", "confirm_password": "short"},
                    headers=god_headers)
    assert r.status_code in (400, 422)
    db_session.refresh(sample_advisor)
    assert sample_advisor.password_hash == before


# ═════════════════════════════════════════════════════════════════════════════
# 5. The password never escapes
# ═════════════════════════════════════════════════════════════════════════════

def test_the_response_never_contains_the_password(client, god_headers, sample_advisor):
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD,
                          "must_change_password": True},
                    headers=god_headers)
    assert r.status_code == 200
    assert NEW_PASSWORD not in r.text
    assert "temp_password" not in r.text


def test_the_row_stores_a_bcrypt_hash_and_not_the_password(
        client, db_session, god_headers, sample_advisor):
    client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
                headers=god_headers)
    db_session.refresh(sample_advisor)
    stored = sample_advisor.password_hash
    assert stored != NEW_PASSWORD
    assert NEW_PASSWORD not in stored
    # bcrypt, produced by the application's own hash_password - not a hash
    # computed somewhere else and not a plaintext write.
    assert stored.startswith("$2")
    assert verify_password(NEW_PASSWORD, stored)


def test_the_audit_entry_records_who_whom_and_when_but_not_the_password(
        client, db_session, god_admin, god_headers, sample_advisor):
    client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD,
                      "must_change_password": True},
                headers=god_headers)

    entry = (db_session.query(AuditLogEntry)
             .filter(AuditLogEntry.action == "user.reset_password")
             .order_by(AuditLogEntry.created_at.desc())
             .first())
    assert entry is not None
    assert entry.actor_user_id == god_admin.id          # WHO
    assert entry.target_id == sample_advisor.id         # TO WHOM
    assert entry.target_type == "user"
    assert entry.created_at is not None                 # WHEN

    # NEVER THE PASSWORD. Not the value, not a fragment, anywhere on the row.
    blob = json.dumps({
        "details": entry.details, "note": entry.note,
        "before": entry.before_state, "after": entry.after_state,
    })
    assert NEW_PASSWORD not in blob
    assert sample_advisor.password_hash not in blob
    # What it SHOULD say is legible without it.
    assert sample_advisor.email in (entry.details or "")
    assert "admin_set_password" in (entry.details or "")


def test_a_god_admin_with_no_organization_can_still_be_audited(
        client, db_session, god_headers, sample_advisor):
    """log_action's organization_id is the ACTOR's, and the owner has none.
    A NOT NULL there would make every control-plane reset a 500."""
    r = client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                    json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
                    headers=god_headers)
    assert r.status_code == 200
    entry = (db_session.query(AuditLogEntry)
             .filter(AuditLogEntry.action == "user.reset_password").first())
    assert entry is not None
    assert entry.organization_id is None


# ═════════════════════════════════════════════════════════════════════════════
# 6. Login still works, and the old session does not
# ═════════════════════════════════════════════════════════════════════════════

def test_the_new_password_signs_in(client, god_headers, sample_advisor):
    client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
                headers=god_headers)
    r = client.post("/auth/login", data={"username": sample_advisor.email,
                                         "password": NEW_PASSWORD})
    assert r.status_code == 200
    assert r.json().get("access_token")


def test_the_old_password_stops_working(client, god_headers, sample_advisor):
    client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
                headers=god_headers)
    r = client.post("/auth/login", data={"username": sample_advisor.email,
                                         "password": OLD_PASSWORD})
    assert r.status_code == 401


def test_the_targets_existing_session_is_ended(
        client, db_session, god_headers, sample_advisor):
    """A reset that leaves the old JWT working is not a reset. The token's jti
    no longer matches users.session_token, so single-session enforcement
    refuses it - and only for the person who was reset."""
    victim_headers = _headers(db_session, sample_advisor)
    assert client.get("/auth/my-contexts", headers=victim_headers).status_code == 200

    client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
                headers=god_headers)

    assert client.get("/auth/my-contexts", headers=victim_headers).status_code == 401


def test_the_actors_own_session_survives(client, db_session, god_headers, sample_advisor):
    """Ending the target's session must not end the administrator's."""
    client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
                headers=god_headers)
    assert client.get("/auth/my-contexts", headers=god_headers).status_code == 200


def test_a_forced_change_still_lets_them_reach_the_change_screen(
        client, db_session, god_headers, sample_advisor):
    """must_change_password blocks every path that is not /auth/*. Sign-in and
    the change endpoint itself must remain reachable, or the flag would lock
    the account rather than prompt it."""
    client.post(f"/admin/users/{sample_advisor.id}/reset-password",
                json={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD,
                      "must_change_password": True},
                headers=god_headers)
    login = client.post("/auth/login", data={"username": sample_advisor.email,
                                             "password": NEW_PASSWORD})
    assert login.status_code == 200
    token = login.json()["access_token"]
    me = client.get("/auth/my-contexts", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
