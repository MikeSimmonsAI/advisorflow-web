"""Tests for admin Lead Merge & Cleanup Center."""

import pytest

from app.models.models import (
    CadenceState,
    CRMContact,
    EmailMessage,
    Lead,
    LeadOutcome,
    LeadStatus,
    Message,
    Notification,
    NotificationType,
    Organization,
    PipelineConversation,
    Reply,
    User,
    VoiceCall,
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


def _email(db_session, lead, advisor, idx):
    row = EmailMessage(
        lead_id=lead.id,
        sender_id=advisor.id,
        subject=f"Email {idx}",
        body_html=f"<p>Email {idx}</p>",
        status="sent",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _voice_call(db_session, lead, advisor, org, idx):
    row = VoiceCall(
        lead_id=lead.id,
        advisor_id=advisor.id,
        organization_id=org.id,
        to_phone=f"1214555{idx:04d}",
        status="completed",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _pipeline_conversation(db_session, lead, advisor, org):
    row = PipelineConversation(organization_id=org.id, lead_id=lead.id, advisor_id=advisor.id)
    db_session.add(row)
    db_session.flush()
    return row


def _notification(db_session, lead, advisor, idx):
    row = Notification(
        user_id=advisor.id,
        lead_id=lead.id,
        type=NotificationType.HOT_REPLY,
        message=f"Notification {idx}",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _crm_contact(db_session, lead, org, idx):
    row = CRMContact(organization_id=org.id, lead_id=lead.id, first_name=f"CRM {idx}")
    db_session.add(row)
    db_session.flush()
    return row


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
        must_change_password=False,
    )
    db_session.add(other_admin)
    db_session.flush()
    return other_org, other_admin


def test_potential_duplicates_groups_uncaught_same_phone_or_email_and_is_org_isolated(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    """
    Grouping is phone (Tier 1) then email (Tier 2).

    Last-name grouping was deliberately removed: a shared surname is not a
    duplicate signal - Acosta, Jones and Smith would each become one enormous
    false-positive group - so a contact identifier (phone OR email) is now
    required. The pair that used to prove name grouping is now a shared-EMAIL
    pair with two DIFFERENT phone numbers, so it exercises Tier 2 and would
    fail if email grouping regressed.
    """
    phone_a = _lead(db_session, sample_org, sample_advisor, first_name="PhoneA", last_name="Alpha", phone="(214) 555-0101")
    phone_b = _lead(db_session, sample_org, sample_advisor, first_name="PhoneB", last_name="Beta", phone="1-214-555-0101")
    # Different phones, same email in different casing - Tier 2 only.
    email_a = _lead(db_session, sample_org, sample_advisor, first_name="EmailA", last_name="Gamma",
                    phone="12145550102", email="Shared.Person@Example.com")
    email_b = _lead(db_session, sample_org, sample_advisor, first_name="EmailB", last_name="Delta",
                    phone="12145550103", email="shared.person@example.com")
    caught_duplicate = _lead(
        db_session,
        sample_org,
        sample_advisor,
        first_name="Caught",
        last_name="Alpha",
        phone="12145550101",
        is_duplicate=True,
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
    assert email_a.id in all_group_ids
    assert email_b.id in all_group_ids
    assert caught_duplicate.id not in all_group_ids  # already flagged at import
    assert other_lead.id not in all_group_ids        # different org

    phone_groups = [group for group in groups if group["match_type"] == "phone" and group["match_key"] == "12145550101"]
    assert len(phone_groups) == 1
    assert {lead["id"] for lead in phone_groups[0]["leads"]} == {phone_a.id, phone_b.id}

    email_groups = [group for group in groups
                    if group["match_type"] == "email" and group["match_key"] == "shared.person@example.com"]
    assert len(email_groups) == 1
    assert {lead["id"] for lead in email_groups[0]["leads"]} == {email_a.id, email_b.id}

    # And no group is formed on a name alone: Alpha/Beta/Gamma/Delta are all
    # distinct surnames here, and the only surname repeated in the org
    # ("Alpha", on caught_duplicate and other_lead) produces nothing.
    assert all(group["match_type"] in ("phone", "email") for group in groups)


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


def test_merge_preserves_email_history_that_was_previously_destroyed(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    """
    REGRESSION: email history did not survive a merge.

    email_messages.lead_id is ondelete="CASCADE" and the merge route moved
    messages, replies, outcomes and cadence but never email. The merged lead
    was then deleted, so on Postgres every email ever sent to that duplicate
    was destroyed - silently, with a 200 and a success payload that counted
    the SMS it had saved.

    This test does not depend on the database enforcing the cascade (the test
    engine is SQLite, which does not by default). It asserts the thing the fix
    actually changes: after a merge, no email row still points at a lead that
    no longer exists, and all of them point at the survivor.
    """
    keep = _lead(db_session, sample_org, sample_advisor, first_name="Keep", phone="12145551101")
    merge = _lead(db_session, sample_org, sample_advisor, first_name="Merge", phone="12145551102")

    _email(db_session, keep, sample_advisor, 1)
    _email(db_session, merge, sample_advisor, 2)
    _email(db_session, merge, sample_advisor, 3)
    db_session.commit()
    keep_id, merge_id = keep.id, merge.id

    response = client.post(
        "/admin/leads/merge",
        headers=admin_auth_headers,
        json={"keep_lead_id": keep_id, "merge_lead_ids": [merge_id]},
    )
    assert response.status_code == 200
    assert response.json()["moved_email_messages"] == 2

    db_session.expire_all()
    assert db_session.query(Lead).filter(Lead.id == merge_id).first() is None
    # All three emails survive, on the kept lead.
    assert db_session.query(EmailMessage).filter(EmailMessage.lead_id == keep_id).count() == 3
    # Nothing is left pointing at the deleted lead.
    assert db_session.query(EmailMessage).filter(EmailMessage.lead_id == merge_id).count() == 0
    assert db_session.query(EmailMessage).count() == 3


def test_merge_moves_every_channel_and_leaves_no_row_pointing_at_a_deleted_lead(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    """
    The four tables with no ondelete - voice_calls, pipeline_conversations,
    notifications, crm_contacts - were the opposite failure: on Postgres the
    lead delete raised IntegrityError and the whole merge rolled back with a
    500, so a lead that had ever been called could not be merged at all.

    One merge, every channel, and a sweep asserting nothing at all still
    references the deleted lead.
    """
    keep = _lead(db_session, sample_org, sample_advisor, first_name="Keep", phone="12145551201")
    merge = _lead(db_session, sample_org, sample_advisor, first_name="Merge", phone="12145551202")

    _message(db_session, merge, sample_advisor, 1)
    _reply(db_session, merge, 1)
    _outcome(db_session, merge, sample_advisor, 1)
    _cadence(db_session, merge)
    _email(db_session, merge, sample_advisor, 1)
    _voice_call(db_session, merge, sample_advisor, sample_org, 1)
    _voice_call(db_session, merge, sample_advisor, sample_org, 2)
    _pipeline_conversation(db_session, merge, sample_advisor, sample_org)
    _notification(db_session, merge, sample_advisor, 1)
    _crm_contact(db_session, merge, sample_org, 1)
    db_session.commit()
    keep_id, merge_id = keep.id, merge.id

    response = client.post(
        "/admin/leads/merge",
        headers=admin_auth_headers,
        json={"keep_lead_id": keep_id, "merge_lead_ids": [merge_id]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["moved_messages"] == 1
    assert data["moved_replies"] == 1
    assert data["moved_outcomes"] == 1
    assert data["moved_cadence_states"] == 1
    assert data["moved_email_messages"] == 1
    assert data["moved_voice_calls"] == 2
    assert data["moved_pipeline_conversations"] == 1
    assert data["moved_notifications"] == 1
    assert data["moved_crm_contacts"] == 1

    db_session.expire_all()
    assert db_session.query(Lead).filter(Lead.id == merge_id).first() is None

    for model in (Message, Reply, LeadOutcome, CadenceState, EmailMessage,
                  VoiceCall, PipelineConversation, Notification, CRMContact):
        orphaned = db_session.query(model).filter(model.lead_id == merge_id).count()
        assert orphaned == 0, f"{model.__name__} still points at the deleted lead"
        survived = db_session.query(model).filter(model.lead_id == keep_id).count()
        assert survived >= 1, f"{model.__name__} history did not reach the kept lead"


def test_merge_of_every_channel_stays_inside_the_workspace_org(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    """
    The new moves are bulk UPDATEs keyed only on lead_id, so the org guard has
    to come from the lead lookup above them. A lead in another org must still
    be rejected before any row is touched.
    """
    other_org, _other_admin = _other_org_with_admin(db_session)
    keep = _lead(db_session, sample_org, sample_advisor, first_name="Keep", phone="12145551301")
    foreign = _lead(db_session, other_org, None, first_name="Foreign", phone="12145551302")
    foreign_email = EmailMessage(
        lead_id=foreign.id,
        sender_id=sample_advisor.id,
        subject="Foreign",
        body_html="<p>Foreign</p>",
        status="sent",
    )
    db_session.add(foreign_email)
    db_session.commit()
    foreign_id, foreign_email_id = foreign.id, foreign_email.id

    response = client.post(
        "/admin/leads/merge",
        headers=admin_auth_headers,
        json={"keep_lead_id": keep.id, "merge_lead_ids": [foreign_id]},
    )
    assert response.status_code == 404

    db_session.expire_all()
    assert db_session.query(Lead).filter(Lead.id == foreign_id).first() is not None
    assert db_session.get(EmailMessage, foreign_email_id).lead_id == foreign_id


def test_failed_merge_rolls_back_the_new_channel_moves_too(
    client, admin_auth_headers, db_session, sample_org, sample_advisor, monkeypatch
):
    """
    The existing rollback test predates these moves. If the delete seam raises,
    email and voice must go back to the merged lead along with everything else.
    """
    keep = _lead(db_session, sample_org, sample_advisor, first_name="Keep", phone="12145551401")
    merge = _lead(db_session, sample_org, sample_advisor, first_name="Merge", phone="12145551402")
    merge_email = _email(db_session, merge, sample_advisor, 1)
    merge_call = _voice_call(db_session, merge, sample_advisor, sample_org, 1)
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

    db_session.expire_all()
    assert db_session.get(EmailMessage, merge_email.id).lead_id == merge.id
    assert db_session.get(VoiceCall, merge_call.id).lead_id == merge.id
