"""Tests for admin Lead Merge & Cleanup Center."""

import pytest

from app.models.models import (
    CadenceState,
    Lead,
    LeadOutcome,
    LeadStatus,
    Message,
    Organization,
    Reply,
    User,
)
from app.services.auth_service import hash_password


def _lead(db_session, org, advisor, *, first_name="Test", last_name="Merge", phone="12145550101", email=None, status=LeadStatus.NEW, is_duplicate=False):
    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id if advisor else None,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        phone_raw=phone,
        email=email,
        status=status,
        is_duplicate=is_duplicate,
    )
    db_session.add(lead)
    db_session.flush()
    return lead


def _message(db_session, lead, advisor, idx):
    msg = Message(lead_id=lead.id, sender_id=advisor.id, body=f"Message {idx}", twilio_status="sent")
    db_session.add(msg)
    db_session.flush()
    return msg


def _reply(db_session, lead, idx):
    reply = Reply(lead_id=lead.id, body=f"Reply {idx}")
    db_session.add(reply)
    db_session.flush()
    return reply


def _outcome(db_session, lead, advisor, idx):
    outcome = LeadOutcome(lead_id=lead.id, recorded_by_id=advisor.id, notes=f"Outcome {idx}")
    db_session.add(outcome)
    db_session.flush()
    return outcome


def _cadence(db_session, lead):
    cadence = CadenceState(lead_id=lead.id)
    db_session.add(cadence)
    db_session.flush()
    return cadence


def _other_org_with_admin(db_session):
    other_org = Organization(name="Other Cleanup Org", slug="other-cleanup", plan="trial")
    db_session.add(other_org)
    db_session.flush()
    other_admin = User(
        organization_id=other_org.id,
        email="cleanup-admin@other.com",
        password_hash=hash_password("OtherPass123!"),
        full_name="Other Cleanup Admin",
        role="org_admin",
    )
    db_session.add(other_admin)
    db_session.flush()
    return other_org, other_admin


def test_potential_duplicates_requires_corroborating_signal_not_just_last_name(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    """
    Regression test for the exact bug Mike flagged: two leads sharing only
    a last name (different first names, no shared email, no shared phone)
    must NOT be grouped as potential duplicates. A common surname between
    strangers means nothing by itself - "Jay Johnson" and "Ray Johnson"
    are not a likely duplicate pair just because they're both Johnsons.
    """
    phone_a = _lead(db_session, sample_org, sample_advisor, first_name="PhoneA", last_name="Alpha", phone="(214) 555-0101")
    phone_b = _lead(db_session, sample_org, sample_advisor, first_name="PhoneB", last_name="Beta", phone="1-214-555-0101")

    # Same last name, different first name, no email on either - must NOT group.
    surname_only_a = _lead(db_session, sample_org, sample_advisor, first_name="Jay", last_name="Johnson", phone="12145550110")
    surname_only_b = _lead(db_session, sample_org, sample_advisor, first_name="Ray", last_name="Johnson", phone="12145550111")

    caught_duplicate = _lead(
        db_session, sample_org, sample_advisor,
        first_name="Caught", last_name="Alpha", phone="12145550101", is_duplicate=True,
    )
    other_org, other_admin = _other_org_with_admin(db_session)
    other_lead = _lead(db_session, other_org, other_admin, first_name="Other", last_name="Alpha", phone="12145550101")
    db_session.commit()

    response = client.get("/admin/leads/potential-duplicates", headers=admin_auth_headers)
    assert response.status_code == 200
    groups = response.json()

    all_group_ids = {lead["id"] for group in groups for lead in group["leads"]}
    assert phone_a.id in all_group_ids
    assert phone_b.id in all_group_ids
    assert caught_duplicate.id not in all_group_ids
    assert other_lead.id not in all_group_ids

    # The actual bug fix: same-surname-only pair must not appear anywhere.
    assert surname_only_a.id not in all_group_ids
    assert surname_only_b.id not in all_group_ids

    phone_groups = [group for group in groups if group["match_type"] == "phone" and group["match_key"] == "12145550101"]
    assert len(phone_groups) == 1
    assert {lead["id"] for lead in phone_groups[0]["leads"]} == {phone_a.id, phone_b.id}


def test_potential_duplicates_matches_on_last_name_plus_email(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    """Same last name + same email IS a real corroborating signal and should group."""
    lead_a = _lead(db_session, sample_org, sample_advisor, first_name="Roberta", last_name="ONeil", phone="12145550120", email="oneil.family@example.com")
    lead_b = _lead(db_session, sample_org, sample_advisor, first_name="Bob", last_name="O'Neil", phone="12145550121", email="ONeil.Family@example.com")

    db_session.commit()

    response = client.get("/admin/leads/potential-duplicates", headers=admin_auth_headers)
    groups = response.json()

    email_groups = [g for g in groups if g["match_type"] == "name_and_email"]
    assert len(email_groups) == 1
    assert {lead["id"] for lead in email_groups[0]["leads"]} == {lead_a.id, lead_b.id}


def test_potential_duplicates_matches_on_last_name_plus_first_name(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    """Same first AND last name (normalized) is a real corroborating signal - the realistic 'imported twice' case."""
    lead_a = _lead(db_session, sample_org, sample_advisor, first_name="John", last_name="Smith", phone="12145550130")
    lead_b = _lead(db_session, sample_org, sample_advisor, first_name="JOHN", last_name="smith", phone="12145550131")

    db_session.commit()

    response = client.get("/admin/leads/potential-duplicates", headers=admin_auth_headers)
    groups = response.json()

    name_groups = [g for g in groups if g["match_type"] == "name_and_first_name"]
    assert len(name_groups) == 1
    assert {lead["id"] for lead in name_groups[0]["leads"]} == {lead_a.id, lead_b.id}


def test_merge_moves_message_reply_cadence_and_outcome_history_then_deletes_merged_lead(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    keep = _lead(db_session, sample_org, sample_advisor, first_name="Keep", last_name="Merge", phone="12145550101")
    merge = _lead(db_session, sample_org, sample_advisor, first_name="Merge", last_name="Merge", phone="12145550102")

    _message(db_session, keep, sample_advisor, 1)
    _message(db_session, merge, sample_advisor, 2)
    _message(db_session, merge, sample_advisor, 3)
    _reply(db_session, keep, 1)
    _reply(db_session, merge, 2)
    _outcome(db_session, keep, sample_advisor, 1)
    _outcome(db_session, merge, sample_advisor, 2)
    _cadence(db_session, merge)
    db_session.commit()
    keep_id = keep.id
    merge_id = merge.id

    response = client.post(
        "/admin/leads/merge",
        headers=admin_auth_headers,
        json={"keep_lead_id": keep_id, "merge_lead_ids": [merge_id]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["merged_count"] == 1
    assert data["moved_messages"] == 2
    assert data["moved_replies"] == 1
    assert data["moved_cadence_states"] == 1
    assert data["moved_outcomes"] == 1

    assert db_session.query(Lead).filter(Lead.id == merge_id).first() is None
    assert db_session.query(Message).filter(Message.lead_id == keep_id).count() == 3
    assert db_session.query(Reply).filter(Reply.lead_id == keep_id).count() == 2
    assert db_session.query(LeadOutcome).filter(LeadOutcome.lead_id == keep_id).count() == 2
    assert db_session.query(CadenceState).filter(CadenceState.lead_id == keep_id).count() == 1


def test_failed_merge_rolls_back_all_partial_history_moves(
    client, admin_auth_headers, db_session, sample_org, sample_advisor, monkeypatch
):
    keep = _lead(db_session, sample_org, sample_advisor, first_name="Keep", phone="12145550201")
    merge = _lead(db_session, sample_org, sample_advisor, first_name="Merge", phone="12145550202")
    keep_msg = _message(db_session, keep, sample_advisor, 1)
    merge_msg = _message(db_session, merge, sample_advisor, 2)
    merge_reply = _reply(db_session, merge, 1)
    db_session.commit()

    import app.routers.admin_router as admin_router

    def explode_after_moves(db, merge_leads):
        raise RuntimeError("simulated delete failure")

    monkeypatch.setattr(admin_router, "_delete_merged_lead_records", explode_after_moves)

    response = client.post(
        "/admin/leads/merge",
        headers=admin_auth_headers,
        json={"keep_lead_id": keep.id, "merge_lead_ids": [merge.id]},
    )
    assert response.status_code == 500
    assert "rolled back" in response.json()["detail"]

    db_session.expire_all()
    assert db_session.query(Lead).filter(Lead.id == keep.id).first() is not None
    assert db_session.query(Lead).filter(Lead.id == merge.id).first() is not None
    assert db_session.get(Message, keep_msg.id).lead_id == keep.id
    assert db_session.get(Message, merge_msg.id).lead_id == merge.id
    assert db_session.get(Reply, merge_reply.id).lead_id == merge.id


def test_merge_rejects_self_merge_with_clear_error(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, first_name="Self", phone="12145550301")
    db_session.commit()

    response = client.post(
        "/admin/leads/merge",
        headers=admin_auth_headers,
        json={"keep_lead_id": lead.id, "merge_lead_ids": [lead.id]},
    )
    assert response.status_code == 400
    assert "cannot be merged into itself" in response.json()["detail"]


def test_merge_org_isolation_rejects_other_org_lead(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    keep = _lead(db_session, sample_org, sample_advisor, first_name="Keep", phone="12145550401")
    other_org, other_admin = _other_org_with_admin(db_session)
    other_lead = _lead(db_session, other_org, other_admin, first_name="Other", phone="12145550402")
    db_session.commit()

    response = client.post(
        "/admin/leads/merge",
        headers=admin_auth_headers,
        json={"keep_lead_id": keep.id, "merge_lead_ids": [other_lead.id]},
    )
    assert response.status_code == 404
    assert db_session.query(Lead).filter(Lead.id == other_lead.id).first() is not None


def test_fix_contact_info_normalizes_phone_updates_email_and_respects_org_isolation(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    lead = _lead(db_session, sample_org, sample_advisor, first_name="FixMe", phone="12145550501", email="old@example.com")
    other_org, other_admin = _other_org_with_admin(db_session)
    other_lead = _lead(db_session, other_org, other_admin, first_name="OtherFix", phone="12145550502")
    db_session.commit()

    response = client.patch(
        f"/admin/leads/{lead.id}/fix-contact-info",
        headers=admin_auth_headers,
        json={"phone": "(214) 555-0599", "email": "new@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["phone"] == "12145550599"
    assert response.json()["email"] == "new@example.com"

    db_session.refresh(lead)
    assert lead.phone == "12145550599"
    assert lead.phone_raw == "(214) 555-0599"
    assert lead.email == "new@example.com"

    other_response = client.patch(
        f"/admin/leads/{other_lead.id}/fix-contact-info",
        headers=admin_auth_headers,
        json={"phone": "2145550600"},
    )
    assert other_response.status_code == 404
    db_session.refresh(other_lead)
    assert other_lead.phone == "12145550502"


# ---------------------------------------------------------------------------
# Name correction - added per Mike's explicit feedback that clicking into a
# lead from the Cleanup Center didn't actually let him "clean up anything"
# about that person. A misspelled name matters specifically here because
# duplicate-group matching keys on normalized last_name.
# ---------------------------------------------------------------------------

def test_fix_contact_info_corrects_first_and_last_name(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, first_name="Jhon", last_name="Smyth", phone="12145550701")
    db_session.commit()

    response = client.patch(
        f"/admin/leads/{lead.id}/fix-contact-info",
        headers=admin_auth_headers,
        json={"first_name": "John", "last_name": "Smith"},
    )

    assert response.status_code == 200
    assert response.json()["first_name"] == "John"
    assert response.json()["last_name"] == "Smith"
    db_session.refresh(lead)
    assert lead.first_name == "John"
    assert lead.last_name == "Smith"


def test_fix_contact_info_rejects_blank_last_name(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145550702")
    db_session.commit()

    response = client.patch(
        f"/admin/leads/{lead.id}/fix-contact-info",
        headers=admin_auth_headers,
        json={"last_name": "   "},
    )

    assert response.status_code == 400


def test_fix_contact_info_allows_blanking_first_name(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    """first_name is optional on a lead, unlike last_name - blanking it out should be allowed."""
    lead = _lead(db_session, sample_org, sample_advisor, first_name="Temp", phone="12145550703")
    db_session.commit()

    response = client.patch(
        f"/admin/leads/{lead.id}/fix-contact-info",
        headers=admin_auth_headers,
        json={"first_name": ""},
    )

    assert response.status_code == 200
    assert response.json()["first_name"] is None


def test_fix_contact_info_requires_at_least_one_field(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145550704")
    db_session.commit()

    response = client.patch(f"/admin/leads/{lead.id}/fix-contact-info", headers=admin_auth_headers, json={})

    assert response.status_code == 400


def test_fix_contact_info_resyncs_registry_when_only_last_name_changes(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    """
    Regression test for the registry-staleness bug: previously the
    contact registry was only re-synced when phone changed. If ONLY the
    last name was corrected (phone unchanged), the registry entry kept
    pointing at the old, misspelled normalized last name - meaning a real
    future duplicate (correctly spelled) would never get caught against
    this lead, and this lead's own entry would silently go stale.
    """
    from app.models.models import ContactRegistry

    lead = _lead(db_session, sample_org, sample_advisor, last_name="Smyth", phone="12145550705")
    db_session.commit()

    registry_entry = ContactRegistry(
        organization_id=sample_org.id,
        normalized_phone="12145550705",
        normalized_last_name="smyth",
        first_seen_lead_id=lead.id,
        owning_user_id=sample_advisor.id,
    )
    db_session.add(registry_entry)
    db_session.commit()

    response = client.patch(
        f"/admin/leads/{lead.id}/fix-contact-info",
        headers=admin_auth_headers,
        json={"last_name": "Smith"},
    )

    assert response.status_code == 200
    db_session.refresh(registry_entry)
    assert registry_entry.normalized_last_name == "smith"


def test_fix_contact_info_name_change_can_surface_a_real_duplicate(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    """
    If correcting a typo'd last name now matches an existing registry
    entry under a DIFFERENT lead, this lead should get flagged as a
    duplicate of that original - same behavior the phone-correction path
    already had, now also triggered by a name-only correction.
    """
    from app.models.models import ContactRegistry

    original_lead = _lead(db_session, sample_org, sample_advisor, first_name="Original", last_name="Johnson", phone="12145550706")
    db_session.add(ContactRegistry(
        organization_id=sample_org.id,
        normalized_phone="12145550706",
        normalized_last_name="johnson",
        first_seen_lead_id=original_lead.id,
        owning_user_id=sample_advisor.id,
    ))
    typo_lead = _lead(db_session, sample_org, sample_advisor, first_name="Typo", last_name="Jonson", phone="12145550706")
    db_session.commit()

    response = client.patch(
        f"/admin/leads/{typo_lead.id}/fix-contact-info",
        headers=admin_auth_headers,
        json={"last_name": "Johnson"},
    )

    assert response.status_code == 200
    db_session.refresh(typo_lead)
    assert typo_lead.is_duplicate is True
    assert typo_lead.duplicate_of_lead_id == original_lead.id


def test_fix_contact_info_name_and_phone_together(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, first_name="Jhon", last_name="Smyth", phone="12145550707")
    db_session.commit()

    response = client.patch(
        f"/admin/leads/{lead.id}/fix-contact-info",
        headers=admin_auth_headers,
        json={"first_name": "John", "last_name": "Smith", "phone": "2145550799"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "John"
    assert body["last_name"] == "Smith"
    assert body["phone"] == "12145550799"
