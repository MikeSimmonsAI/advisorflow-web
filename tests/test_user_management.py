"""
Tests for the user-management endpoints in app/routers/admin_router.py
(list_users, create_user, deactivate_user, reactivate_user).

This is the real replacement for running seed.py by hand - Mike
specifically asked for an in-app way to create advisor accounts.
"""

from app.models.models import User, Organization
from app.services.auth_service import hash_password, verify_password, create_access_token


def test_create_user_requires_admin_role(client, auth_headers):
    response = client.post("/admin/users", json={
        "email": "newadvisor@restland.com", "full_name": "New Advisor",
    }, headers=auth_headers)
    assert response.status_code == 403


def test_create_user_succeeds_for_admin(client, admin_auth_headers, db_session, sample_org):
    response = client.post("/admin/users", json={
        "email": "newadvisor@restland.com", "full_name": "New Advisor", "role": "advisor",
    }, headers=admin_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "newadvisor@restland.com"
    assert data["must_change_password"] is True
    assert data["temp_password"] is not None  # returned once, at creation

    # confirm it's actually in the database, in the right org
    created = db_session.query(User).filter(User.email == "newadvisor@restland.com").first()
    assert created is not None
    assert created.organization_id == sample_org.id
    assert created.must_change_password is True


def test_create_user_temp_password_actually_works_for_login(client, admin_auth_headers):
    response = client.post("/admin/users", json={
        "email": "logintest@restland.com", "full_name": "Login Test",
    }, headers=admin_auth_headers)
    temp_password = response.json()["temp_password"]

    login_response = client.post("/auth/login", data={
        "username": "logintest@restland.com", "password": temp_password,
    })
    assert login_response.status_code == 200
    assert login_response.json()["must_change_password"] is True


def test_create_user_rejects_duplicate_email(client, admin_auth_headers, sample_advisor):
    response = client.post("/admin/users", json={
        "email": sample_advisor.email, "full_name": "Duplicate",
    }, headers=admin_auth_headers)
    assert response.status_code == 400


def test_create_user_rejects_invalid_role(client, admin_auth_headers):
    response = client.post("/admin/users", json={
        "email": "bad@restland.com", "full_name": "Bad Role", "role": "super_admin",
    }, headers=admin_auth_headers)
    assert response.status_code == 400


def test_create_user_rejects_malformed_email(client, admin_auth_headers):
    response = client.post("/admin/users", json={
        "email": "not-an-email", "full_name": "Bad Email",
    }, headers=admin_auth_headers)
    assert response.status_code == 422  # pydantic validation error


def test_list_users_shows_only_own_organization(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    """An admin should never see users from a different organization."""
    other_org = Organization(name="Other Org", slug="other-org-3", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_user = User(organization_id=other_org.id, email="other3@test.com",
                       password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_user)
    db_session.commit()

    response = client.get("/admin/users", headers=admin_auth_headers)
    emails = [u["email"] for u in response.json()]
    assert sample_advisor.email in emails
    assert "other3@test.com" not in emails


def test_deactivate_user_blocks_login(client, admin_auth_headers, db_session, sample_advisor):
    response = client.patch(f"/admin/users/{sample_advisor.id}/deactivate", headers=admin_auth_headers)
    assert response.status_code == 200

    db_session.refresh(sample_advisor)
    assert sample_advisor.is_active is False

    login_response = client.post("/auth/login", data={
        "username": sample_advisor.email, "password": "TestPass123!",
    })
    assert login_response.status_code == 401  # deactivated accounts can't log in


def test_cannot_deactivate_own_account(client, admin_auth_headers, db_session, sample_org):
    """Prevents an admin from accidentally locking themselves out."""
    admin_user = db_session.query(User).filter(User.role == "org_admin", User.organization_id == sample_org.id).first()
    response = client.patch(f"/admin/users/{admin_user.id}/deactivate", headers=admin_auth_headers)
    assert response.status_code == 400


def test_cannot_deactivate_cross_org_user(client, admin_auth_headers, db_session):
    """An admin from one org cannot deactivate a user in a different org."""
    other_org = Organization(name="Other Org", slug="other-org-4", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_user = User(organization_id=other_org.id, email="other4@test.com",
                       password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_user)
    db_session.commit()

    response = client.patch(f"/admin/users/{other_user.id}/deactivate", headers=admin_auth_headers)
    assert response.status_code == 404  # not found, from this admin's perspective

    db_session.refresh(other_user)
    assert other_user.is_active is True  # untouched


def test_reactivate_user_restores_login(client, admin_auth_headers, db_session, sample_advisor):
    client.patch(f"/admin/users/{sample_advisor.id}/deactivate", headers=admin_auth_headers)
    response = client.patch(f"/admin/users/{sample_advisor.id}/reactivate", headers=admin_auth_headers)
    assert response.status_code == 200

    db_session.refresh(sample_advisor)
    assert sample_advisor.is_active is True

    login_response = client.post("/auth/login", data={
        "username": sample_advisor.email, "password": "TestPass123!",
    })
    assert login_response.status_code == 200
