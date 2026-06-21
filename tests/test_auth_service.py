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


def test_access_token_round_trip(sample_advisor):
    token = create_access_token(sample_advisor)
    decoded = decode_access_token(token)
    assert decoded["sub"] == sample_advisor.id
    assert decoded["org_id"] == sample_advisor.organization_id
    assert decoded["role"] == "advisor"


def test_new_accounts_default_to_must_change_password(sample_advisor):
    """Every seeded account should force a password change on first login."""
    assert sample_advisor.must_change_password is True


def test_password_change_invalidates_old_password(db_session, sample_advisor):
    old_hash = sample_advisor.password_hash
    sample_advisor.password_hash = hash_password("BrandNewPassword1!")
    sample_advisor.must_change_password = False
    db_session.commit()

    assert verify_password("TestPass123!", sample_advisor.password_hash) is False
    assert verify_password("BrandNewPassword1!", sample_advisor.password_hash) is True
    assert sample_advisor.must_change_password is False
