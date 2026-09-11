"""AN INTEGRATION SETUP TOKEN IS NOT A LOGIN.

THE CONFIRMED DEFECT THESE GUARD. `setup_router` signs a 48-hour JWT so an
advisor can connect Google or Microsoft from a link an admin emails them.
It resolved its signing key as `SECRET_KEY or JWT_SECRET`, and SECRET_KEY is
not set in production — so it was signed with the ACCESS TOKEN key. The token
carries `sub`, so `decode_access_token` verified it and `get_current_user`
loaded that advisor and served the request. It carries no `jti`, so the session
block was skipped entirely and it kept working after every session had been
revoked. Reproduced independently: HTTP 200 on a protected route with no user
session, still 200 after revocation, and 401 once the keys were separated.

EVERY TEST BELOW IS AGAINST THE CHEAP FIX. There are two independent locks —
a purpose claim that must be present and correct, and a signing key that is not
the access-token key — and the tests assert BOTH, separately, because removing
either one silently restores the crossing while the other keeps the obvious
test green.
"""

import jwt
import pytest
from datetime import datetime, timedelta, timezone

from app.models.models import User
from app.routers import setup_router
from app.services import session_service
from app.services.auth_service import (ACCESS_TOKEN_PURPOSE, JWT_SECRET,
                                       create_access_token, hash_password)

PROTECTED = "/auth/my-contexts"


def _bearer(token):
    return {"Authorization": "Bearer %s" % token}


@pytest.fixture()
def org_admin(db_session, sample_org):
    admin = User(organization_id=sample_org.id, email="setupadmin@restland.com",
                 password_hash=hash_password("AdminPass123!"),
                 full_name="Setup Admin", role="org_admin",
                 must_change_password=False)
    db_session.add(admin)
    db_session.commit()
    return admin


@pytest.fixture()
def admin_headers(db_session, org_admin):
    return _bearer(create_access_token(org_admin, db_session))


# ══ THE ATTACK, IN THE EXACT SHAPE IT WAS REPRODUCED ════════════════════════

def test_a_real_setup_token_cannot_authenticate_as_its_subject(
        client, db_session, sample_advisor, admin_headers):
    """The end-to-end version: get a genuine setup link, present its token as a
    bearer credential, be refused."""
    made = client.post("/admin/setup-link/%s" % sample_advisor.id,
                       headers=admin_headers)
    assert made.status_code == 200, made.text
    token = made.json()["link"].split("token=", 1)[1]

    # It IS a valid setup token — the setup endpoints accept it.
    assert client.get("/setup/verify", params={"token": token}).status_code == 200

    # And it is not a credential for anything else.
    r = client.get(PROTECTED, headers=_bearer(token))
    assert r.status_code == 401, (
        "an integration setup token authenticated to a protected API: %s" % r.text)


def test_setup_token_forged_with_the_access_token_key_is_refused(
        client, sample_advisor):
    """LOCK ONE, ON ITS OWN.

    Assume the key separation is gone — an attacker signs the setup payload
    with JWT_SECRET itself. The purpose claim must still refuse it. If this
    fails, `decode_access_token` has stopped checking `purpose`.
    """
    forged = jwt.encode({"sub": str(sample_advisor.id),
                         "purpose": setup_router.TOKEN_PURPOSE,
                         "exp": datetime.now(timezone.utc) + timedelta(hours=48)},
                        JWT_SECRET, algorithm="HS256")
    assert client.get(PROTECTED, headers=_bearer(forged)).status_code == 401


def test_setup_token_is_not_signed_with_the_access_token_key(sample_advisor):
    """LOCK TWO, ON ITS OWN.

    Assume the purpose check is gone. The real setup token must still fail
    signature verification against the access-token key.
    """
    assert setup_router.SETUP_SIGNING_KEY != JWT_SECRET
    real = setup_router._generate_token(sample_advisor.id)
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(real, JWT_SECRET, algorithms=["HS256"])


def test_a_purposeless_token_signed_with_the_access_key_is_refused(
        client, sample_advisor):
    """FAIL CLOSED ON ABSENCE, not just on a wrong value.

    Every non-access token this codebase has ever minted looks like this to
    `decode_access_token`: a valid signature, a `sub`, and nothing that says
    what it is for. Accepting it "because it does not claim to be something
    else" is the vulnerability restated.
    """
    bare = jwt.encode({"sub": str(sample_advisor.id),
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      JWT_SECRET, algorithm="HS256")
    assert client.get(PROTECTED, headers=_bearer(bare)).status_code == 401


def test_an_access_purpose_token_without_a_session_is_refused(
        client, sample_advisor):
    """THE SESSION REQUIREMENT IS NOT OPTIONAL.

    A token that names the right purpose but carries no `jti` cannot name a
    session, and normal authenticated access requires one. This is the check
    that stops the next purpose-less-token family from walking through the same
    door — the setup token's real trick was skipping session enforcement, not
    the claim it carried.
    """
    no_jti = jwt.encode({"sub": str(sample_advisor.id),
                         "purpose": ACCESS_TOKEN_PURPOSE,
                         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                        JWT_SECRET, algorithm="HS256")
    assert client.get(PROTECTED, headers=_bearer(no_jti)).status_code == 401


def test_a_jti_that_matches_no_session_and_no_column_is_refused(
        client, db_session, sample_advisor):
    """A fabricated session identifier is not a session."""
    made_up = jwt.encode({"sub": str(sample_advisor.id),
                          "purpose": ACCESS_TOKEN_PURPOSE,
                          "jti": "not-a-session-that-was-ever-issued",
                          "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                         JWT_SECRET, algorithm="HS256")
    assert client.get(PROTECTED, headers=_bearer(made_up)).status_code == 401


# ══ AFTER REVOCATION — the property the original bug broke ══════════════════

def test_setup_token_still_cannot_authenticate_after_session_revocation(
        client, db_session, sample_advisor, admin_headers):
    """The reported behaviour was "it continued working after session
    revocation". Revoke everything, then try again."""
    made = client.post("/admin/setup-link/%s" % sample_advisor.id,
                       headers=admin_headers)
    token = made.json()["link"].split("token=", 1)[1]

    session_service.revoke_all_for_user(db_session, sample_advisor.id)
    db_session.commit()

    assert client.get(PROTECTED, headers=_bearer(token)).status_code == 401


def test_a_real_access_token_dies_when_its_session_is_revoked(
        client, db_session, sample_advisor):
    """The control. If this ever fails, revocation itself is broken and the
    test above proves nothing."""
    headers = _bearer(create_access_token(sample_advisor, db_session))
    assert client.get(PROTECTED, headers=headers).status_code == 200

    session_service.revoke_all_for_user(db_session, sample_advisor.id)
    db_session.commit()

    assert client.get(PROTECTED, headers=headers).status_code == 401


# ══ THE OTHER DIRECTION ═════════════════════════════════════════════════════

def test_an_access_token_is_not_accepted_as_a_setup_token(
        client, db_session, sample_advisor):
    """Crossing must be refused both ways. An access token presented to the
    setup endpoints is not a setup link."""
    access = create_access_token(sample_advisor, db_session)
    assert client.get("/setup/verify",
                      params={"token": access}).status_code in (400, 401)
    assert client.get("/setup/google-connect",
                      params={"token": access}).status_code in (400, 401)


# ══ LEGITIMATE BEHAVIOUR MUST SURVIVE ═══════════════════════════════════════

def test_the_setup_link_flow_still_works_end_to_end(
        client, db_session, sample_advisor, admin_headers):
    made = client.post("/admin/setup-link/%s" % sample_advisor.id,
                       headers=admin_headers)
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["advisor_name"] == sample_advisor.full_name
    token = body["link"].split("token=", 1)[1]

    verified = client.get("/setup/verify", params={"token": token})
    assert verified.status_code == 200, verified.text
    assert verified.json()["user_id"] == sample_advisor.id

    started = client.get("/setup/google-connect", params={"token": token})
    assert started.status_code == 200, started.text
    assert started.json()["authorization_url"].startswith("https://accounts.google.com")


def test_an_expired_setup_token_is_refused_with_a_useful_message(
        client, sample_advisor):
    expired = jwt.encode({"sub": str(sample_advisor.id),
                          "purpose": setup_router.TOKEN_PURPOSE,
                          "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                         setup_router.SETUP_SIGNING_KEY, algorithm="HS256")
    r = client.get("/setup/verify", params={"token": expired})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"].lower()


def test_ordinary_login_still_produces_a_working_token(client, sample_advisor):
    r = client.post("/auth/login", data={"username": sample_advisor.email,
                                         "password": "TestPass123!"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    assert claims["purpose"] == ACCESS_TOKEN_PURPOSE
    assert claims["jti"]
    assert client.get(PROTECTED, headers=_bearer(token)).status_code == 200


# ══ THE ADMIN GATE ON SETUP LINKS IS UNCHANGED ══════════════════════════════

def test_a_plain_advisor_cannot_generate_a_setup_link(
        client, auth_headers, second_advisor):
    r = client.post("/admin/setup-link/%s" % second_advisor.id,
                    headers=auth_headers)
    assert r.status_code == 403


def test_an_org_admin_cannot_generate_a_link_for_another_orgs_advisor(
        client, db_session, admin_headers):
    from app.models.models import Organization
    other = Organization(name="Somebody Else", slug="somebody-else",
                         plan="standard")
    db_session.add(other)
    db_session.commit()
    outsider = User(organization_id=other.id, email="outsider@elsewhere.com",
                    password_hash=hash_password("TestPass123!"),
                    full_name="Outsider", role="advisor",
                    must_change_password=False)
    db_session.add(outsider)
    db_session.commit()

    r = client.post("/admin/setup-link/%s" % outsider.id, headers=admin_headers)
    assert r.status_code == 403
