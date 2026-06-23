"""
Tests confirming sensitive admin/compliance/campaign/template actions
actually write an audit log entry now.

The audit log table and read endpoint (GET /audit-log) existed for a
while with NOTHING calling log_action() outside its own tests - the
module docstring in audit_log_router.py said as much explicitly. This
file covers the wiring added across admin_router.py, campaign_router.py,
compliance_router.py, and templates_router.py so merges, reassignments,
user edits, campaign applies, suppression list changes, and template
edits all leave a real trail.
"""

import json

from app.models.models import AuditLogEntry, Lead, LeadStatus, MessageTrack, SuppressionEntry, SuppressionSource, User
from app.services.auth_service import hash_password


def _latest_entry(db_session, organization_id, action):
    return (
        db_session.query(AuditLogEntry)
        .filter(AuditLogEntry.organization_id == organization_id, AuditLogEntry.action == action)
        .order_by(AuditLogEntry.created_at.desc())
        .first()
    )


# --- User management ---

def test_create_user_logs_action(client, db_session, sample_org, admin_auth_headers):
    response = client.post("/admin/users", json={
        "email": "newperson@restland.com", "full_name": "New Person", "role": "advisor",
    }, headers=admin_auth_headers)
    assert response.status_code == 200

    entry = _latest_entry(db_session, sample_org.id, "user.create")
    assert entry is not None
    details = json.loads(entry.details)
    assert details["email"] == "newperson@restland.com"


def test_deactivate_user_logs_action(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    response = client.patch(f"/admin/users/{sample_advisor.id}/deactivate", headers=admin_auth_headers)
    assert response.status_code == 200

    entry = _latest_entry(db_session, sample_org.id, "user.deactivate")
    assert entry is not None
    assert entry.target_id == sample_advisor.id


def test_reactivate_user_logs_action(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    sample_advisor.is_active = False
    db_session.commit()

    response = client.patch(f"/admin/users/{sample_advisor.id}/reactivate", headers=admin_auth_headers)
    assert response.status_code == 200

    entry = _latest_entry(db_session, sample_org.id, "user.reactivate")
    assert entry is not None


def test_reset_password_logs_action_without_leaking_temp_password(client, db_session, sample_org, sample_advisor):
    super_admin = User(organization_id=sample_org.id, email="super-audit@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    from app.services.auth_service import create_access_token
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    response = client.post(f"/admin/users/{sample_advisor.id}/reset-password", headers=super_headers)
    assert response.status_code == 200
    temp_password = response.json()["temp_password"]

    entry = _latest_entry(db_session, sample_org.id, "user.reset_password")
    assert entry is not None
    # CRITICAL: the temp password must never appear in the audit log
    assert temp_password not in entry.details


def test_update_user_logs_only_changed_fields(client, db_session, sample_org, sample_advisor):
    super_admin = User(organization_id=sample_org.id, email="super-audit2@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    from app.services.auth_service import create_access_token
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    response = client.patch(f"/admin/users/{sample_advisor.id}", json={"full_name": "Corrected Name"}, headers=super_headers)
    assert response.status_code == 200

    entry = _latest_entry(db_session, sample_org.id, "user.update")
    assert entry is not None
    details = json.loads(entry.details)
    assert "full_name" in details
    assert details["full_name"]["to"] == "Corrected Name"
    assert "email" not in details  # unchanged field shouldn't appear


def test_update_user_with_no_actual_changes_does_not_log(client, db_session, sample_org, sample_advisor):
    """Sending the same value back shouldn't create a noisy no-op log entry."""
    super_admin = User(organization_id=sample_org.id, email="super-audit3@restland.com",
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    from app.services.auth_service import create_access_token
    super_headers = {"Authorization": f"Bearer {create_access_token(super_admin)}"}

    response = client.patch(f"/admin/users/{sample_advisor.id}", json={"full_name": sample_advisor.full_name}, headers=super_headers)
    assert response.status_code == 200

    entry = _latest_entry(db_session, sample_org.id, "user.update")
    assert entry is None


# --- Lead reassignment and merge ---

def test_reassign_leads_logs_action(client, db_session, sample_org, sample_advisor, sample_lead, admin_auth_headers):
    response = client.post("/admin/leads/reassign", json={
        "lead_ids": [sample_lead.id], "new_assigned_to_id": sample_advisor.id,
    }, headers=admin_auth_headers)
    assert response.status_code == 200

    entry = _latest_entry(db_session, sample_org.id, "lead.reassign")
    assert entry is not None
    details = json.loads(entry.details)
    assert sample_lead.id in details["lead_ids"]


def test_merge_leads_logs_action(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    keep = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Keep", last_name="Lead", phone="12145550801")
    merge = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Merge", last_name="Lead", phone="12145550801")
    db_session.add_all([keep, merge])
    db_session.commit()
    keep_id = keep.id
    merge_id = merge.id  # captured before the API call deletes this row

    response = client.post("/admin/leads/merge", json={
        "keep_lead_id": keep_id, "merge_lead_ids": [merge_id],
    }, headers=admin_auth_headers)
    assert response.status_code == 200

    entry = _latest_entry(db_session, sample_org.id, "lead.merge")
    assert entry is not None
    details = json.loads(entry.details)
    assert details["kept_lead_id"] == keep_id
    assert merge_id in details["merged_lead_ids"]


def test_fix_contact_info_logs_only_changed_fields(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Fix", last_name="Me", phone="12145550802", email="old@example.com")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/admin/leads/{lead.id}/fix-contact-info", json={"email": "new@example.com"}, headers=admin_auth_headers)
    assert response.status_code == 200

    entry = _latest_entry(db_session, sample_org.id, "lead.fix_contact_info")
    assert entry is not None
    details = json.loads(entry.details)
    assert "email" in details
    assert details["email"]["to"] == "new@example.com"
    assert "phone" not in details


# --- Compliance ---

def test_add_suppression_entry_logs_action(client, db_session, sample_org, admin_auth_headers):
    response = client.post("/compliance/suppression-list", json={
        "phone": "214-555-0900", "reason": "Customer requested no contact", "source": "manual",
    }, headers=admin_auth_headers)
    assert response.status_code == 201

    entry = _latest_entry(db_session, sample_org.id, "compliance.suppress")
    assert entry is not None
    details = json.loads(entry.details)
    assert details["phone"] == "12145550900"


def test_add_permanent_dnc_logs_action_and_matched_lead(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="DNC", last_name="Target", phone="12145550901")
    db_session.add(lead)
    db_session.commit()

    response = client.post("/compliance/permanent-dnc", json={
        "phone": "214-555-0901", "reason": "Legal opt-out",
    }, headers=admin_auth_headers)
    assert response.status_code == 201

    entry = _latest_entry(db_session, sample_org.id, "compliance.permanent_dnc")
    assert entry is not None
    details = json.loads(entry.details)
    assert details["matched_lead_id"] == lead.id


def test_delete_suppression_entry_logs_action_with_details_before_deletion(client, db_session, sample_org, admin_auth_headers):
    """
    The highest-stakes compliance action: removing a number from
    suppression makes it contactable again. Must log the phone/reason
    BEFORE the row is deleted, since the ORM object won't be queryable
    afterward.
    """
    entry_to_delete = SuppressionEntry(
        organization_id=sample_org.id, phone="12145550902",
        reason="Originally suppressed by mistake", source=SuppressionSource.MANUAL,
    )
    db_session.add(entry_to_delete)
    db_session.commit()
    deleted_id = entry_to_delete.id

    response = client.delete(f"/compliance/suppression-list/{deleted_id}", headers=admin_auth_headers)
    assert response.status_code == 204

    log_entry = _latest_entry(db_session, sample_org.id, "compliance.unsuppress")
    assert log_entry is not None
    assert log_entry.target_id == deleted_id
    details = json.loads(log_entry.details)
    assert details["phone"] == "12145550902"
    assert details["original_reason"] == "Originally suppressed by mistake"


# --- Templates ---

def test_update_template_logs_action(client, db_session, sample_org, admin_auth_headers):
    response = client.put("/templates/", json={
        "message_track": "pre_need_lock_price", "channel": "sms", "body_template": "Hi {first_name}, custom message.",
    }, headers=admin_auth_headers)
    assert response.status_code == 200

    entry = _latest_entry(db_session, sample_org.id, "template.update")
    assert entry is not None
    details = json.loads(entry.details)
    assert details["message_track"] == "pre_need_lock_price"
    assert details["channel"] == "sms"


def test_reset_template_logs_action_only_when_a_customization_existed(client, db_session, sample_org, admin_auth_headers):
    # No customization exists yet - resetting should be a no-op, no log entry.
    response = client.delete("/templates/pre_need_lock_price/sms", headers=admin_auth_headers)
    assert response.status_code == 200
    assert response.json()["reset"] is False
    assert _latest_entry(db_session, sample_org.id, "template.reset_to_default") is None

    # Customize it, then reset - this time it should log.
    client.put("/templates/", json={
        "message_track": "pre_need_lock_price", "channel": "sms", "body_template": "Custom text",
    }, headers=admin_auth_headers)
    response2 = client.delete("/templates/pre_need_lock_price/sms", headers=admin_auth_headers)
    assert response2.status_code == 200
    assert response2.json()["reset"] is True
    assert _latest_entry(db_session, sample_org.id, "template.reset_to_default") is not None


# --- Campaigns ---

def test_apply_campaign_logs_action(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Campaign", last_name="Target",
                phone="12145550903", status=LeadStatus.NEW, tier="pre_need")
    db_session.add(lead)
    db_session.commit()

    create_response = client.post("/campaigns", json={
        "name": "Test Campaign",
        "filter_criteria": {"tier": "pre_need"},
        "message_track": "pre_need_lock_price",
    }, headers=admin_auth_headers)
    assert create_response.status_code == 200
    campaign_id = create_response.json()["id"]

    apply_response = client.post(f"/campaigns/{campaign_id}/apply", headers=admin_auth_headers)
    assert apply_response.status_code == 200

    entry = _latest_entry(db_session, sample_org.id, "campaign.apply")
    assert entry is not None
    details = json.loads(entry.details)
    assert details["campaign_name"] == "Test Campaign"
    assert details["updated_count"] >= 1
