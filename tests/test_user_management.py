"""
Tests for the user-management endpoints in app/routers/admin_router.py
(list_users, create_user, deactivate_user, reactivate_user).

This is the real replacement for running seed.py by hand - Mike
specifically asked for an in-app way to create advisor accounts.
"""

from app.models.models import User, Organization, Lead
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


# ---------------------------------------------------------------------------
# Password reset - super_admin only. This is the security boundary Mike
# explicitly asked for: an org_admin should NOT be able to reset anyone's
# password, even within their own organization.
# ---------------------------------------------------------------------------

def test_reset_password_blocked_for_org_admin(client, admin_auth_headers, sample_advisor):
    """
    admin_auth_headers fixture creates an org_admin (not super_admin) -
    this must be rejected with 403, confirming the stricter
    require_super_admin layer actually works and isn't just an alias
    for require_admin.
    """
    response = client.post(f"/admin/users/{sample_advisor.id}/reset-password", headers=admin_auth_headers)
    assert response.status_code == 403


def test_reset_password_blocked_for_advisor(client, auth_headers, sample_advisor):
    response = client.post(f"/admin/users/{sample_advisor.id}/reset-password", headers=auth_headers)
    assert response.status_code == 403


def test_reset_password_works_for_super_admin(client, db_session, sample_org, sample_advisor):
    from app.services.auth_service import hash_password, create_access_token
    super_admin = User(organization_id=sample_org.id, email="super@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    response = client.post(f"/admin/users/{sample_advisor.id}/reset-password", headers=super_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == sample_advisor.email
    assert data["temp_password"] is not None

    # the new temp password should actually work for login
    login_response = client.post("/auth/login", data={
        "username": sample_advisor.email, "password": data["temp_password"],
    })
    assert login_response.status_code == 200
    assert login_response.json()["must_change_password"] is True


def test_reset_password_invalidates_old_password(client, db_session, sample_org, sample_advisor):
    from app.services.auth_service import hash_password, create_access_token
    super_admin = User(organization_id=sample_org.id, email="super2@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    client.post(f"/admin/users/{sample_advisor.id}/reset-password", headers=super_headers)

    old_password_login = client.post("/auth/login", data={
        "username": sample_advisor.email, "password": "TestPass123!",
    })
    assert old_password_login.status_code == 401


# ---------------------------------------------------------------------------
# Lead reassignment - manual routing across the org's lead pool.
# ---------------------------------------------------------------------------

def test_reassign_leads_moves_to_new_advisor(client, admin_auth_headers, db_session, sample_org, sample_advisor, second_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Reassign", last_name="Me", phone="12145559001")
    db_session.add(lead)
    db_session.commit()

    response = client.post("/admin/leads/reassign", json={
        "lead_ids": [lead.id], "new_assigned_to_id": second_advisor.id,
    }, headers=admin_auth_headers)
    assert response.status_code == 200
    assert response.json()["reassigned_count"] == 1

    db_session.refresh(lead)
    assert lead.assigned_to_id == second_advisor.id


def test_reassign_leads_can_unassign_back_to_pool(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Pool", last_name="Bound", phone="12145559002")
    db_session.add(lead)
    db_session.commit()

    response = client.post("/admin/leads/reassign", json={
        "lead_ids": [lead.id], "new_assigned_to_id": None,
    }, headers=admin_auth_headers)
    assert response.status_code == 200

    db_session.refresh(lead)
    assert lead.assigned_to_id is None


def test_reassign_leads_rejects_cross_org_target_advisor(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    """An admin must not be able to assign a lead to an advisor in a different organization."""
    other_org = Organization(name="Other Org", slug="other-org-5", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other5@test.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Stay", last_name="Put", phone="12145559003")
    db_session.add(lead)
    db_session.commit()

    response = client.post("/admin/leads/reassign", json={
        "lead_ids": [lead.id], "new_assigned_to_id": other_advisor.id,
    }, headers=admin_auth_headers)
    assert response.status_code == 404

    db_session.refresh(lead)
    assert lead.assigned_to_id == sample_advisor.id  # untouched


def test_reassign_leads_skips_leads_from_other_orgs(client, admin_auth_headers, db_session, sample_advisor):
    """A lead belonging to a different org should be silently skipped, not reassigned."""
    other_org = Organization(name="Other Org", slug="other-org-6", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other6@test.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    foreign_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                         first_name="Foreign", last_name="Lead", phone="12145559004")
    db_session.add(foreign_lead)
    db_session.commit()

    response = client.post("/admin/leads/reassign", json={
        "lead_ids": [foreign_lead.id], "new_assigned_to_id": sample_advisor.id,
    }, headers=admin_auth_headers)
    assert response.status_code == 200
    assert response.json()["reassigned_count"] == 0
    assert response.json()["skipped_count"] == 1

    db_session.refresh(foreign_lead)
    assert foreign_lead.assigned_to_id == other_advisor.id  # untouched


def test_list_unassigned_leads_returns_only_pool_leads(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    assigned_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                          first_name="Has", last_name="Owner", phone="12145559005")
    unassigned_lead = Lead(organization_id=sample_org.id, assigned_to_id=None,
                            first_name="No", last_name="Owner", phone="12145559006")
    db_session.add_all([assigned_lead, unassigned_lead])
    db_session.commit()

    response = client.get("/admin/leads/unassigned", headers=admin_auth_headers)
    assert response.status_code == 200
    ids = [l["id"] for l in response.json()]
    assert unassigned_lead.id in ids
    assert assigned_lead.id not in ids


def test_lead_detail_reassign_uses_existing_single_lead_endpoint(client, admin_auth_headers, db_session, sample_org, sample_advisor, second_advisor):
    """
    The Lead Detail page posts a single lead ID to the same /admin/leads/reassign
    endpoint used by the Master Dashboard unassigned-pool flow. This confirms the
    existing endpoint cleanly supports that second entry point without a wrapper.
    """
    lead = Lead(
        organization_id=sample_org.id,
        assigned_to_id=sample_advisor.id,
        first_name="Detail",
        last_name="Reassign",
        phone="12145559009",
    )
    db_session.add(lead)
    db_session.commit()

    response = client.post("/admin/leads/reassign", json={
        "lead_ids": [lead.id],
        "new_assigned_to_id": second_advisor.id,
    }, headers=admin_auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "reassigned_count": 1,
        "skipped_count": 0,
        "skipped_ids": [],
    }
    db_session.refresh(lead)
    assert lead.assigned_to_id == second_advisor.id


def test_plain_advisor_cannot_call_lead_detail_reassign_endpoint_directly(client, auth_headers, db_session, sample_org, sample_advisor, second_advisor):
    """Even if a non-admin manually calls the endpoint used by Lead Detail, require_admin blocks it."""
    lead = Lead(
        organization_id=sample_org.id,
        assigned_to_id=sample_advisor.id,
        first_name="Advisor",
        last_name="Blocked",
        phone="12145559010",
    )
    db_session.add(lead)
    db_session.commit()

    response = client.post("/admin/leads/reassign", json={
        "lead_ids": [lead.id],
        "new_assigned_to_id": second_advisor.id,
    }, headers=auth_headers)

    assert response.status_code == 403
    db_session.refresh(lead)
    assert lead.assigned_to_id == sample_advisor.id


# ---------------------------------------------------------------------------
# Edit user details - super_admin only. Mike (super_admin) needs to be able
# to fix a misspelled name or wrong email on an existing account directly,
# without that being something only he can't do despite being the account
# owner. Same security boundary pattern as password reset above.
# ---------------------------------------------------------------------------

def test_update_user_blocked_for_org_admin(client, admin_auth_headers, sample_advisor):
    """admin_auth_headers fixture is an org_admin, not super_admin - must be blocked."""
    response = client.patch(f"/admin/users/{sample_advisor.id}", json={
        "full_name": "Should Not Apply",
    }, headers=admin_auth_headers)
    assert response.status_code == 403


def test_update_user_blocked_for_plain_advisor(client, auth_headers, sample_advisor):
    response = client.patch(f"/admin/users/{sample_advisor.id}", json={
        "full_name": "Should Not Apply",
    }, headers=auth_headers)
    assert response.status_code == 403


def test_update_user_fixes_misspelled_name_for_super_admin(client, db_session, sample_org, sample_advisor):
    super_admin = User(organization_id=sample_org.id, email="super-edit@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    response = client.patch(f"/admin/users/{sample_advisor.id}", json={
        "full_name": "Corrected Name",
    }, headers=super_headers)

    assert response.status_code == 200
    assert response.json()["full_name"] == "Corrected Name"
    db_session.refresh(sample_advisor)
    assert sample_advisor.full_name == "Corrected Name"


def test_update_user_can_change_email_and_rejects_duplicate(client, db_session, sample_org, sample_advisor):
    super_admin = User(organization_id=sample_org.id, email="super-edit2@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    other_advisor = User(organization_id=sample_org.id, email="other-advisor@restland.com",
                          password_hash=hash_password("x"), full_name="Other Advisor", role="advisor")
    db_session.add_all([super_admin, other_advisor])
    db_session.commit()
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    # Successful email change
    response = client.patch(f"/admin/users/{sample_advisor.id}", json={
        "email": "corrected-email@restland.com",
    }, headers=super_headers)
    assert response.status_code == 200
    assert response.json()["email"] == "corrected-email@restland.com"

    # Rejected: email already used by a different account
    dup_response = client.patch(f"/admin/users/{other_advisor.id}", json={
        "email": "corrected-email@restland.com",
    }, headers=super_headers)
    assert dup_response.status_code == 400


def test_update_user_can_change_role(client, db_session, sample_org, sample_advisor):
    super_admin = User(organization_id=sample_org.id, email="super-edit3@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    response = client.patch(f"/admin/users/{sample_advisor.id}", json={
        "role": "org_admin",
    }, headers=super_headers)

    assert response.status_code == 200
    assert response.json()["role"] == "org_admin"
    db_session.refresh(sample_advisor)
    assert sample_advisor.role == "org_admin"


def test_update_user_rejects_invalid_role(client, db_session, sample_org, sample_advisor):
    super_admin = User(organization_id=sample_org.id, email="super-edit4@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    response = client.patch(f"/admin/users/{sample_advisor.id}", json={
        "role": "super_admin",
    }, headers=super_headers)

    assert response.status_code == 400


def test_update_user_cannot_change_super_admin_role(client, db_session, sample_org, sample_advisor):
    super_admin = User(organization_id=sample_org.id, email="super-edit5@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    response = client.patch(f"/admin/users/{super_admin.id}", json={
        "role": "advisor",
    }, headers=super_headers)

    assert response.status_code == 400


def test_update_user_rejects_blank_name(client, db_session, sample_org, sample_advisor):
    super_admin = User(organization_id=sample_org.id, email="super-edit6@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    response = client.patch(f"/admin/users/{sample_advisor.id}", json={
        "full_name": "   ",
    }, headers=super_headers)

    assert response.status_code == 400


def test_update_user_org_isolation(client, db_session, sample_org, sample_advisor):
    """A super_admin in one org cannot edit a user belonging to a different org."""
    other_org = Organization(name="Other Edit Org", slug="other-edit-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_super_admin = User(organization_id=other_org.id, email="other-super@example.com",
                              password_hash=hash_password("x"), full_name="Other Super", role="super_admin")
    db_session.add(other_super_admin)
    db_session.commit()
    other_headers = {"Authorization": f"Bearer {create_access_token(other_super_admin)}"}

    response = client.patch(f"/admin/users/{sample_advisor.id}", json={
        "full_name": "Should Not Apply",
    }, headers=other_headers)

    assert response.status_code == 404
    db_session.refresh(sample_advisor)
    assert sample_advisor.full_name != "Should Not Apply"


def test_update_user_partial_update_leaves_other_fields_unchanged(client, db_session, sample_org, sample_advisor):
    super_admin = User(organization_id=sample_org.id, email="super-edit7@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}
    original_email = sample_advisor.email

    response = client.patch(f"/admin/users/{sample_advisor.id}", json={
        "full_name": "Only Name Changed",
    }, headers=super_headers)

    assert response.status_code == 200
    assert response.json()["full_name"] == "Only Name Changed"
    assert response.json()["email"] == original_email


# ---------------------------------------------------------------------------
# Per-user detail page - lets an admin click into a specific user and see
# their actual performance/activity, which previously didn't exist anywhere
# in the app (clicking a name in User Management went nowhere).
# ---------------------------------------------------------------------------

def test_get_user_detail_returns_profile_and_metrics(client, db_session, sample_org, sample_advisor, sample_lead, admin_auth_headers):
    from app.models.models import Message
    message = Message(lead_id=sample_lead.id, sender_id=sample_advisor.id, body="Hi there, following up.")
    db_session.add(message)
    db_session.commit()

    response = client.get(f"/admin/users/{sample_advisor.id}/detail", headers=admin_auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == sample_advisor.id
    assert body["full_name"] == sample_advisor.full_name
    assert body["email"] == sample_advisor.email
    assert "metrics" in body
    assert body["metrics"]["leads_owned"] >= 1
    assert body["metrics"]["messages_sent"] >= 1


def test_get_user_detail_includes_recent_activity_feed(client, db_session, sample_org, sample_advisor, sample_lead, admin_auth_headers):
    from app.models.models import Message, Reply
    message = Message(lead_id=sample_lead.id, sender_id=sample_advisor.id, body="Following up on your inquiry.")
    reply = Reply(lead_id=sample_lead.id, body="Yes, I'm interested!", classification="interested")
    db_session.add_all([message, reply])
    db_session.commit()

    response = client.get(f"/admin/users/{sample_advisor.id}/detail", headers=admin_auth_headers)

    assert response.status_code == 200
    activity = response.json()["recent_activity"]
    assert len(activity) == 2
    types = {item["type"] for item in activity}
    assert types == {"sent", "reply"}
    sent_item = next(item for item in activity if item["type"] == "sent")
    assert sent_item["lead_name"] == f"{sample_lead.first_name} {sample_lead.last_name}".strip()
    reply_item = next(item for item in activity if item["type"] == "reply")
    assert reply_item["classification"] == "interested"


def test_get_user_detail_activity_feed_is_chronologically_sorted(client, db_session, sample_org, sample_advisor, sample_lead, admin_auth_headers):
    from app.models.models import Message
    from datetime import datetime, timedelta, timezone
    older = Message(lead_id=sample_lead.id, sender_id=sample_advisor.id, body="Older message",
                     sent_at=datetime.now(timezone.utc) - timedelta(days=2))
    newer = Message(lead_id=sample_lead.id, sender_id=sample_advisor.id, body="Newer message",
                     sent_at=datetime.now(timezone.utc))
    db_session.add_all([older, newer])
    db_session.commit()

    response = client.get(f"/admin/users/{sample_advisor.id}/detail", headers=admin_auth_headers)

    activity = response.json()["recent_activity"]
    assert activity[0]["body"] == "Newer message"
    assert activity[1]["body"] == "Older message"


def test_get_user_detail_404_for_user_in_different_org(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    other_org = Organization(name="Other Detail Org", slug="other-detail-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_user = User(organization_id=other_org.id, email="otherdetail@example.com",
                       password_hash=hash_password("x"), full_name="Other User", role="advisor")
    db_session.add(other_user)
    db_session.commit()

    response = client.get(f"/admin/users/{other_user.id}/detail", headers=admin_auth_headers)

    assert response.status_code == 404


def test_get_user_detail_requires_admin_role(client, auth_headers, sample_advisor):
    response = client.get(f"/admin/users/{sample_advisor.id}/detail", headers=auth_headers)
    assert response.status_code == 403


def test_get_user_detail_org_admin_can_view_any_user_in_org_not_just_super_admin(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    """org_admin (not just super_admin) should be able to view detail - this is read-only, unlike PATCH edit which is super_admin only."""
    response = client.get(f"/admin/users/{sample_advisor.id}/detail", headers=admin_auth_headers)
    assert response.status_code == 200


def test_get_user_detail_includes_last_login(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    from datetime import datetime, timezone
    sample_advisor.last_login_at = datetime.now(timezone.utc)
    db_session.commit()

    response = client.get(f"/admin/users/{sample_advisor.id}/detail", headers=admin_auth_headers)

    assert response.status_code == 200
    assert response.json()["last_login_at"] is not None
