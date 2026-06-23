"""
Tests for the Audit Log feature.

Covers the two non-negotiables for this project:
- organization isolation
- helper persistence
"""

from app.models.models import AuditLogEntry, Organization, User
from app.routers.audit_log_router import log_action
from app.services.auth_service import create_access_token, hash_password


def _admin_user(db_session):
    return db_session.query(User).filter(User.email == "admin@restland.com").first()


def test_audit_log_requires_admin(client, auth_headers):
    response = client.get("/audit-log", headers=auth_headers)
    assert response.status_code == 403


def test_audit_log_org_isolation_and_action_filter(client, admin_auth_headers, db_session, sample_org):
    admin = _admin_user(db_session)

    other_org = Organization(name="Other Audit Org", slug="other-audit-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()

    other_admin = User(
        organization_id=other_org.id,
        email="other-audit-admin@example.com",
        password_hash=hash_password("AdminPass123!"),
        full_name="Other Audit Admin",
        role="org_admin",
    )
    db_session.add(other_admin)
    db_session.commit()

    own_entry = AuditLogEntry(
        organization_id=sample_org.id,
        actor_user_id=admin.id,
        action="lead_reassigned",
        target_type="lead",
        target_id="lead-1",
        details="Moved from Advisor A to Advisor B",
    )
    own_password_entry = AuditLogEntry(
        organization_id=sample_org.id,
        actor_user_id=admin.id,
        action="password_reset",
        target_type="user",
        target_id="user-1",
        details="Temporary password generated",
    )
    other_entry = AuditLogEntry(
        organization_id=other_org.id,
        actor_user_id=other_admin.id,
        action="lead_reassigned",
        target_type="lead",
        target_id="lead-other",
        details="Different org event",
    )
    db_session.add_all([own_entry, own_password_entry, other_entry])
    db_session.commit()

    response = client.get("/audit-log", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    returned_ids = {entry["id"] for entry in body["entries"]}
    assert own_entry.id in returned_ids
    assert own_password_entry.id in returned_ids
    assert other_entry.id not in returned_ids

    filtered = client.get("/audit-log?action=lead_reassigned", headers=admin_auth_headers)
    assert filtered.status_code == 200
    filtered_body = filtered.json()

    assert filtered_body["total"] == 1
    assert filtered_body["entries"][0]["id"] == own_entry.id
    assert filtered_body["entries"][0]["action"] == "lead_reassigned"


def test_log_action_helper_persists_entry(db_session, sample_org, sample_advisor):
    entry = log_action(
        db=db_session,
        organization_id=sample_org.id,
        actor_user_id=sample_advisor.id,
        action="suppression_entry_deleted",
        target_type="suppression_entry",
        target_id="suppression-123",
        details={"phone": "12145550101", "reason": "Customer requested no contact"},
    )

    persisted = db_session.query(AuditLogEntry).filter(AuditLogEntry.id == entry.id).first()

    assert persisted is not None
    assert persisted.organization_id == sample_org.id
    assert persisted.actor_user_id == sample_advisor.id
    assert persisted.action == "suppression_entry_deleted"
    assert persisted.target_type == "suppression_entry"
    assert persisted.target_id == "suppression-123"
    assert '"phone": "12145550101"' in persisted.details
    assert persisted.created_at is not None


def test_audit_log_list_includes_resolved_actor_name(client, admin_auth_headers, db_session, sample_org):
    """
    Regression coverage: actor_user_id alone is a raw UUID, which doesn't
    tell an admin who actually performed the action. The list endpoint
    must resolve and include the actor's full_name.
    """
    admin = _admin_user(db_session)
    entry = AuditLogEntry(
        organization_id=sample_org.id,
        actor_user_id=admin.id,
        action="lead.reassign",
        target_type="lead",
        target_id="lead-name-test",
        details="test",
    )
    db_session.add(entry)
    db_session.commit()

    response = client.get("/audit-log", headers=admin_auth_headers)
    assert response.status_code == 200
    matching = next(e for e in response.json()["entries"] if e["id"] == entry.id)
    assert matching["actor_name"] == admin.full_name
