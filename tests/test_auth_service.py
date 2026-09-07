"""
Tests for app/services/auth_service.py
"""

from app.services.auth_service import (
    hash_password, verify_password, create_access_token, decode_access_token, authenticate_user,
)


def test_password_hash_and_verify_round_trip():
    hashed = hash_password("MySecurePass1!")
    assert verify_password("MySecurePass1!", hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_authenticate_user_succeeds_with_correct_credentials(db_session, sample_advisor):
    user = authenticate_user(db_session, "advisor1@restland.com", "TestPass123!")
    assert user is not None
    assert user.id == sample_advisor.id


def test_authenticate_user_fails_with_wrong_password(db_session, sample_advisor):
    user = authenticate_user(db_session, "advisor1@restland.com", "WrongPassword")
    assert user is None


def test_authenticate_user_fails_for_unknown_email(db_session, sample_org):
    user = authenticate_user(db_session, "nobody@restland.com", "anything")
    assert user is None


def test_access_token_round_trip(sample_advisor, db_session):
    token = create_access_token(sample_advisor, db_session)
    decoded = decode_access_token(token)
    assert decoded["sub"] == sample_advisor.id
    assert decoded["org_id"] == sample_advisor.organization_id
    assert decoded["role"] == "advisor"


def test_new_accounts_default_to_must_change_password(db_session, sample_org):
    """A new account must force a password change on first login.

    This deliberately does NOT use the sample_advisor fixture. That fixture
    passes must_change_password=False on purpose - the model default is True,
    and every router test built on it was hitting the password-change guard in
    deps.py and getting 403 before the route under test ever ran (see commit
    fd98232). So the fixture overrides the very default this test exists to
    assert, and asserting through it could only ever fail.

    A security control has to be checked where it actually applies: on a User
    constructed WITHOUT the flag, which is what any code path that forgets to
    set it produces.
    """
    from app.models.models import User

    fresh = User(
        organization_id=sample_org.id,
        email="brand-new-hire@restland.com",
        password_hash=hash_password("TempPass123!"),
        full_name="Brand New Hire",
        role="advisor",
    )
    db_session.add(fresh)
    db_session.commit()
    db_session.refresh(fresh)

    assert fresh.must_change_password is True, (
        "A User created without an explicit must_change_password must default "
        "to True. If this fails, an account can be provisioned that never "
        "forces the temporary password to be replaced."
    )


def test_admin_created_accounts_force_a_password_change(client, admin_auth_headers, db_session):
    """The real provisioning path, end to end.

    The model default above is the backstop; this is the route an
    administrator actually uses. Both are asserted because either one alone
    can regress without the other noticing - a route that stops passing the
    flag would still pass the test above, and a model whose default flipped
    would still pass this one.
    """
    from app.models.models import User

    response = client.post(
        "/admin/users",
        headers=admin_auth_headers,
        json={
            "email": "provisioned@restland.com",
            "full_name": "Provisioned Advisor",
            "role": "advisor",
        },
    )
    assert response.status_code in (200, 201), response.text

    created = db_session.query(User).filter(User.email == "provisioned@restland.com").first()
    assert created is not None
    assert created.must_change_password is True


def test_password_change_invalidates_old_password(db_session, sample_advisor):
    old_hash = sample_advisor.password_hash
    sample_advisor.password_hash = hash_password("BrandNewPassword1!")
    sample_advisor.must_change_password = False
    db_session.commit()

    assert verify_password("TestPass123!", sample_advisor.password_hash) is False
    assert verify_password("BrandNewPassword1!", sample_advisor.password_hash) is True
    assert sample_advisor.must_change_password is False
