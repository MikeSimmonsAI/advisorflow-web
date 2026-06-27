"""
Router-level tests for app/routers/leads_router.py
"""

from app.models.models import Lead, BookingLink, Message, Reply, Organization, User
from app.services.auth_service import hash_password, create_access_token


def test_get_lead_requires_auth(client):
    response = client.get("/leads/some-id")
    assert response.status_code == 401


def test_get_lead_404s_for_nonexistent_lead(client, auth_headers):
    response = client.get("/leads/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_get_lead_blocks_cross_org_access(client, db_session, sample_lead):
    """An advisor from a different org must not be able to view another org's lead."""
    other_org = Organization(name="Other Org", slug="other-org-2", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other2@test.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_token = create_access_token(other_advisor)

    response = client.get(f"/leads/{sample_lead.id}", headers={"Authorization": f"Bearer {other_token}"})
    assert response.status_code == 404


def test_timeline_includes_booking_info_when_one_exists(client, auth_headers, db_session, sample_lead, sample_advisor):
    """
    Confirms the enhancement: booking status (pending/booked/cancelled,
    calendar event presence) is now surfaced on the timeline response,
    not just sitting unused in the database.
    """
    booking = BookingLink(lead_id=sample_lead.id, user_id=sample_advisor.id, status="booked",
                           calendar_event_id="gcal_evt_123")
    db_session.add(booking)
    db_session.commit()

    response = client.get(f"/leads/{sample_lead.id}/timeline", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["booking"] is not None
    assert data["booking"]["status"] == "booked"
    assert data["booking"]["calendar_event_id"] == "gcal_evt_123"


def test_timeline_booking_is_null_when_none_exists(client, auth_headers, sample_lead):
    response = client.get(f"/leads/{sample_lead.id}/timeline", headers=auth_headers)
    data = response.json()
    assert data["booking"] is None


def test_timeline_uses_most_recent_booking_when_multiple_exist(client, auth_headers, db_session, sample_lead, sample_advisor):
    """
    If a lead has multiple booking links created at clearly different
    times, the timeline should show the latest one. (Note: if two
    bookings are created within the same second, ordering is genuinely
    ambiguous at the database level - confirmed during testing - so this
    test uses explicit, distinct timestamps rather than relying on
    real-world insertion speed.)
    """
    from datetime import datetime, timezone, timedelta

    older = BookingLink(lead_id=sample_lead.id, user_id=sample_advisor.id, status="cancelled")
    db_session.add(older)
    db_session.commit()
    older.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    newer = BookingLink(lead_id=sample_lead.id, user_id=sample_advisor.id, status="pending")
    db_session.add(newer)
    db_session.commit()

    response = client.get(f"/leads/{sample_lead.id}/timeline", headers=auth_headers)
    data = response.json()
    assert data["booking"]["id"] == newer.id
    assert data["booking"]["status"] == "pending"


def test_timeline_merges_messages_and_replies_chronologically(client, auth_headers, db_session, sample_lead, sample_advisor):
    msg = Message(lead_id=sample_lead.id, sender_id=sample_advisor.id, body="Hello!", twilio_status="delivered")
    reply = Reply(lead_id=sample_lead.id, body="Hi back!", is_hot=False)
    db_session.add_all([msg, reply])
    db_session.commit()

    response = client.get(f"/leads/{sample_lead.id}/timeline", headers=auth_headers)
    data = response.json()
    assert len(data["events"]) == 2
    types = {e["type"] for e in data["events"]}
    assert types == {"outbound", "inbound"}


# ---------------------------------------------------------------------------
# PATCH /{lead_id}/tier - had ZERO test coverage before. Added alongside
# audit logging for this endpoint during the permissions/audit pass.
# ---------------------------------------------------------------------------

def test_set_lead_tier_updates_tier_track_and_status(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import LeadStatus, MessageTrack
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Tier", last_name="Test",
                phone="12145550950", status=LeadStatus.NEEDS_TIER_REVIEW, tier=None)
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/tier?new_tier=pre_need", headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(lead)
    assert lead.tier.value == "pre_need"
    assert lead.message_track == MessageTrack.PRE_NEED_LOCK_PRICE
    assert lead.status == LeadStatus.NEW


def test_set_lead_tier_rejects_invalid_tier(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Tier", last_name="Invalid", phone="12145550951")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/tier?new_tier=not_a_real_tier", headers=auth_headers)

    assert response.status_code == 400


def test_set_lead_tier_404_for_lead_in_different_org(client, db_session, sample_org, auth_headers):
    other_org = Organization(name="Other Tier Org", slug="other-tier-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-tier-advisor@example.com",
                          password_hash=hash_password("x"), full_name="Other Advisor", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id, first_name="Other", last_name="Org", phone="12145550952")
    db_session.add(other_lead)
    db_session.commit()

    response = client.patch(f"/leads/{other_lead.id}/tier?new_tier=pre_need", headers=auth_headers)

    assert response.status_code == 404


def test_set_lead_tier_allows_any_advisor_in_org_not_just_assignee(client, db_session, sample_org, sample_advisor, auth_headers):
    """
    Deliberate scope: an advisor can retier a lead assigned to a DIFFERENT
    advisor in the same org. This is intentional (a reversible
    data-correction action, not restricted to the owning advisor), unlike
    GET /needs-review which only lists the calling advisor's own leads.
    """
    from app.models.models import LeadStatus
    other_advisor = User(organization_id=sample_org.id, email="other-tier-owner@example.com",
                          password_hash=hash_password("x"), full_name="Other Owner", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    lead = Lead(organization_id=sample_org.id, assigned_to_id=other_advisor.id, first_name="NotMine", last_name="Lead",
                phone="12145550953", status=LeadStatus.NEEDS_TIER_REVIEW)
    db_session.add(lead)
    db_session.commit()

    # auth_headers belongs to sample_advisor, NOT other_advisor (the lead's owner)
    response = client.patch(f"/leads/{lead.id}/tier?new_tier=at_need", headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(lead)
    assert lead.tier.value == "at_need"


def test_set_lead_tier_logs_audit_action_with_before_and_after(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import AuditLogEntry, LeadTier
    import json
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Audit", last_name="Tier",
                phone="12145550954", tier=LeadTier.PARTIAL)
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/tier?new_tier=imminent", headers=auth_headers)
    assert response.status_code == 200

    entry = (
        db_session.query(AuditLogEntry)
        .filter(AuditLogEntry.organization_id == sample_org.id, AuditLogEntry.action == "lead.set_tier", AuditLogEntry.target_id == lead.id)
        .order_by(AuditLogEntry.created_at.desc())
        .first()
    )
    assert entry is not None
    details = json.loads(entry.details)
    assert details["from"] == "partial"
    assert details["to"] == "imminent"


# ---------------------------------------------------------------------------
# PATCH /{lead_id}/mark-dnc - the quick-DNC button Mike asked for, for when
# an advisor reads a reply themselves and spots STOP language the
# automatic classification missed. Any advisor can use this (not just
# admins) since a missed STOP gets worse the longer it sits unactioned.
# Deliberately mirrors the automatic DNC path in the SMS webhook exactly.
# ---------------------------------------------------------------------------

def test_mark_dnc_sets_lead_status(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import LeadStatus
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Quick", last_name="DNC", phone="12145550960")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/mark-dnc", json={}, headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(lead)
    assert lead.status == LeadStatus.DNC


def test_mark_dnc_stops_active_cadence(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import CadenceState, CadenceStatus
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Cadence", last_name="Stop", phone="12145550961")
    db_session.add(lead)
    db_session.flush()
    cadence = CadenceState(lead_id=lead.id, status=CadenceStatus.ACTIVE)
    db_session.add(cadence)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/mark-dnc", json={}, headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(cadence)
    assert cadence.status == CadenceStatus.STOPPED_DNC


def test_mark_dnc_adds_to_suppression_list_with_advisor_flagged_source(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import SuppressionEntry, SuppressionSource
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Suppress", last_name="Test", phone="12145550962")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/mark-dnc", json={"reason": "Said don't text me again"}, headers=auth_headers)

    assert response.status_code == 200
    entry = db_session.query(SuppressionEntry).filter(
        SuppressionEntry.organization_id == sample_org.id, SuppressionEntry.phone == "12145550962"
    ).first()
    assert entry is not None
    assert entry.source == SuppressionSource.ADVISOR_FLAGGED
    assert entry.reason == "Said don't text me again"


def test_mark_dnc_works_without_a_reason(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="NoReason", last_name="Test", phone="12145550963")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/mark-dnc", json={}, headers=auth_headers)

    assert response.status_code == 200
    from app.models.models import SuppressionEntry
    entry = db_session.query(SuppressionEntry).filter(SuppressionEntry.phone == "12145550963").first()
    assert entry is not None
    assert "Quick" not in entry.reason  # sanity: just confirms a default reason string was generated, not blank
    assert sample_advisor.full_name in entry.reason


def test_mark_dnc_with_no_phone_still_sets_status_but_skips_suppression(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import LeadStatus, SuppressionEntry
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="NoPhone", last_name="Test", phone=None, email="nophone@example.com")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/mark-dnc", json={}, headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(lead)
    assert lead.status == LeadStatus.DNC
    assert db_session.query(SuppressionEntry).count() == 0


def test_mark_dnc_is_idempotent_does_not_duplicate_suppression_entry(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import SuppressionEntry
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Idempotent", last_name="Test", phone="12145550964")
    db_session.add(lead)
    db_session.commit()

    client.patch(f"/leads/{lead.id}/mark-dnc", json={}, headers=auth_headers)
    response2 = client.patch(f"/leads/{lead.id}/mark-dnc", json={}, headers=auth_headers)

    assert response2.status_code == 200
    count = db_session.query(SuppressionEntry).filter(SuppressionEntry.phone == "12145550964").count()
    assert count == 1


def test_mark_dnc_does_not_overwrite_existing_suppression_source(client, db_session, sample_org, sample_advisor, auth_headers):
    """
    If a number was already suppressed automatically (REPLY_STOP) before
    an advisor clicks the quick-DNC button on that same lead, the
    original source attribution must NOT get silently overwritten to
    ADVISOR_FLAGGED - the existing record stands as-is.
    """
    from app.models.models import SuppressionEntry, SuppressionSource
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="AlreadySuppressed", last_name="Test", phone="12145550965")
    db_session.add(lead)
    db_session.flush()
    existing_entry = SuppressionEntry(
        organization_id=sample_org.id, phone="12145550965", reason="Original auto STOP", source=SuppressionSource.REPLY_STOP,
    )
    db_session.add(existing_entry)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/mark-dnc", json={}, headers=auth_headers)

    assert response.status_code == 200
    db_session.refresh(existing_entry)
    assert existing_entry.source == SuppressionSource.REPLY_STOP
    assert existing_entry.reason == "Original auto STOP"


def test_mark_dnc_404s_for_lead_in_different_org(client, db_session, sample_org, auth_headers):
    other_org = Organization(name="Other DNC Org", slug="other-dnc-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-dnc-advisor@example.com",
                          password_hash=hash_password("x"), full_name="Other Advisor", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id, first_name="Other", last_name="OrgLead", phone="12145550966")
    db_session.add(other_lead)
    db_session.commit()

    response = client.patch(f"/leads/{other_lead.id}/mark-dnc", json={}, headers=auth_headers)

    assert response.status_code == 404


def test_mark_dnc_logs_audit_action(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import AuditLogEntry
    import json
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Audit", last_name="DNC", phone="12145550967")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/mark-dnc", json={"reason": "Test reason"}, headers=auth_headers)
    assert response.status_code == 200

    entry = (
        db_session.query(AuditLogEntry)
        .filter(AuditLogEntry.organization_id == sample_org.id, AuditLogEntry.action == "lead.mark_dnc", AuditLogEntry.target_id == lead.id)
        .first()
    )
    assert entry is not None
    details = json.loads(entry.details)
    assert details["reason"] == "Test reason"
    assert details["was_already_dnc"] is False


def test_mark_dnc_allowed_for_plain_advisor_not_just_admin(client, db_session, sample_org, sample_advisor, auth_headers):
    """Explicit confirmation of Mike's requirement: any advisor, not just admins, can use this."""
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="AnyAdvisor", last_name="Test", phone="12145550968")
    db_session.add(lead)
    db_session.commit()

    # auth_headers fixture is a plain advisor, not org_admin/super_admin
    response = client.patch(f"/leads/{lead.id}/mark-dnc", json={}, headers=auth_headers)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST /leads/manual - the manual single-lead entry form Mike asked for
# directly: "if I get one person's information, I need to be able to enter
# that person directly into the system without uploading a spreadsheet."
# Deliberately reuses the SAME dedup registry and tier-to-track mapping
# the real Excel import uses, not a simplified separate version.
# ---------------------------------------------------------------------------

def test_create_lead_manually_with_phone(client, db_session, sample_org, sample_advisor, auth_headers):
    response = client.post("/leads/manual", json={
        "first_name": "Walk", "last_name": "In", "phone": "12145559500", "tier": "pre_need",
    }, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "Walk"
    assert body["phone"] == "12145559500"
    assert body["tier"] == "pre_need"
    assert body["message_track"] == "pre_need_lock_price"
    assert body["assigned_to_id"] == sample_advisor.id
    assert body["contact_channel"] == "sms"
    assert body["is_duplicate"] is False


def test_create_lead_manually_with_email_only(client, db_session, sample_org, auth_headers):
    response = client.post("/leads/manual", json={
        "first_name": "Email", "last_name": "Only", "email": "emailonly@example.com", "tier": "at_need",
    }, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["contact_channel"] == "email_only"
    assert body["message_track"] == "at_need_support"


def test_create_lead_manually_requires_first_and_last_name(client, auth_headers):
    response = client.post("/leads/manual", json={
        "first_name": "", "last_name": "Test", "phone": "12145559501",
    }, headers=auth_headers)
    assert response.status_code == 400


def test_create_lead_manually_requires_phone_or_email(client, auth_headers):
    response = client.post("/leads/manual", json={
        "first_name": "No", "last_name": "Contact",
    }, headers=auth_headers)
    assert response.status_code == 400


def test_create_lead_manually_rejects_invalid_tier(client, auth_headers):
    response = client.post("/leads/manual", json={
        "first_name": "Bad", "last_name": "Tier", "phone": "12145559502", "tier": "email_only",
    }, headers=auth_headers)
    assert response.status_code == 400
    assert "tier must be one of" in response.json()["detail"]


def test_create_lead_manually_detects_real_duplicate_via_same_registry_as_import(client, db_session, sample_org, sample_advisor, auth_headers):
    """The actual point of reusing check_and_register: a manual entry must be caught as a duplicate the same way an imported row would be."""
    first = client.post("/leads/manual", json={
        "first_name": "First", "last_name": "Entry", "phone": "12145559503", "tier": "pre_need",
    }, headers=auth_headers)
    assert first.json()["is_duplicate"] is False

    second = client.post("/leads/manual", json={
        "first_name": "Different", "last_name": "Entry", "phone": "12145559503", "tier": "at_need",
    }, headers=auth_headers)

    assert second.status_code == 200
    assert second.json()["is_duplicate"] is True
    assert second.json()["duplicate_of_lead_id"] == first.json()["id"]


def test_create_lead_manually_registers_new_contact_with_correct_lead_id(client, db_session, sample_org, sample_advisor, auth_headers):
    """Confirms the first_seen_lead_id backfill step actually works - the one piece that can't be a straight reuse of the batch import logic."""
    from app.models.models import ContactRegistry

    response = client.post("/leads/manual", json={
        "first_name": "Registry", "last_name": "Backfill", "phone": "12145559504", "tier": "pre_need",
    }, headers=auth_headers)
    lead_id = response.json()["id"]

    entry = db_session.query(ContactRegistry).filter(
        ContactRegistry.organization_id == sample_org.id, ContactRegistry.normalized_phone == "12145559504",
    ).first()
    assert entry is not None
    assert entry.first_seen_lead_id == lead_id


def test_create_lead_manually_can_assign_to_another_advisor_same_org(client, db_session, sample_org, sample_advisor, second_advisor, auth_headers):
    response = client.post("/leads/manual", json={
        "first_name": "Assigned", "last_name": "ToOther", "phone": "12145559505",
        "tier": "pre_need", "assigned_to_id": second_advisor.id,
    }, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["assigned_to_id"] == second_advisor.id


def test_create_lead_manually_rejects_assigning_to_advisor_in_different_org(client, db_session, sample_org, auth_headers):
    from app.models.models import Organization, User
    from app.services.auth_service import hash_password

    other_org = Organization(name="Other Manual Org", slug="other-manual-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-manual@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()

    response = client.post("/leads/manual", json={
        "first_name": "Cross", "last_name": "OrgAttempt", "phone": "12145559506",
        "tier": "pre_need", "assigned_to_id": other_advisor.id,
    }, headers=auth_headers)

    assert response.status_code == 404


def test_create_lead_manually_logs_audit_action(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import AuditLogEntry

    response = client.post("/leads/manual", json={
        "first_name": "Audit", "last_name": "Manual", "phone": "12145559507", "tier": "imminent",
    }, headers=auth_headers)
    lead_id = response.json()["id"]

    entry = (
        db_session.query(AuditLogEntry)
        .filter(AuditLogEntry.organization_id == sample_org.id, AuditLogEntry.action == "lead.create_manual", AuditLogEntry.target_id == lead_id)
        .first()
    )
    assert entry is not None


# ---------------------------------------------------------------------------
# Regression coverage for a real bug found tonight: set_lead_tier and
# mark_lead_dnc both committed via log_action and then returned the SAME
# (now-expired) object with no refresh - FastAPI's jsonable_encoder
# silently serialized that to {} with no error, since no response_model
# was declared on either route. Never caught before because the existing
# tests above assert against db_session.refresh(lead) (a fresh read),
# never against response.json() itself. These tests check the actual
# HTTP response body directly, which is what a real frontend consumes.
# ---------------------------------------------------------------------------

def test_set_lead_tier_response_body_contains_real_lead_data_not_empty(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="BodyCheck", last_name="Tier", phone="12145559600")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/tier?new_tier=imminent", headers=auth_headers)

    body = response.json()
    assert body != {}
    assert body["first_name"] == "BodyCheck"
    assert body["tier"] == "imminent"


def test_mark_dnc_response_body_contains_real_lead_data_not_empty(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="BodyCheck", last_name="Dnc", phone="12145559601")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/mark-dnc", json={}, headers=auth_headers)

    body = response.json()
    assert body != {}
    assert body["first_name"] == "BodyCheck"
    assert body["status"] == "dnc"


# ---------------------------------------------------------------------------
# PATCH /{lead_id}/details - advisor-facing contact info/notes editing.
# Mike's explicit complaint: Lead Detail let him VIEW phone/email but
# never edit them, with no clear Save button. Deliberately ADVISOR-SCOPED
# (own leads only) per his explicit call - a different scope rule than
# set_lead_tier's intentionally org-wide access. Reuses the same
# registry-resync logic as admin_router.py's fix_lead_contact_info.
# ---------------------------------------------------------------------------

def test_update_lead_details_phone_email_notes(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Edit", last_name="Me", phone="12145559700")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/details", json={
        "phone": "(214) 555-9701", "email": "newemail@example.com", "notes": "Called twice, left voicemail.",
    }, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "12145559701"
    assert body["email"] == "newemail@example.com"
    assert body["notes"] == "Called twice, left voicemail."
    db_session.refresh(lead)
    assert lead.notes == "Called twice, left voicemail."


def test_update_lead_details_blocks_advisor_editing_someone_elses_lead(client, db_session, sample_org, sample_advisor, second_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=second_advisor.id, first_name="NotMine", last_name="Lead", phone="12145559702")
    db_session.add(lead)
    db_session.commit()

    # auth_headers belongs to sample_advisor, not second_advisor (the lead's owner)
    response = client.patch(f"/leads/{lead.id}/details", json={"notes": "trying to edit"}, headers=auth_headers)

    assert response.status_code == 403


def test_update_lead_details_admin_can_edit_any_lead_in_org(client, db_session, sample_org, second_advisor, admin_auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=second_advisor.id, first_name="OtherPersons", last_name="Lead", phone="12145559703")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/details", json={"notes": "admin override"}, headers=admin_auth_headers)

    assert response.status_code == 200
    assert response.json()["notes"] == "admin override"


def test_update_lead_details_requires_at_least_one_field(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Empty", last_name="Request", phone="12145559704")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/details", json={}, headers=auth_headers)

    assert response.status_code == 400


def test_update_lead_details_rejects_invalid_phone(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Bad", last_name="Phone", phone="12145559705")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/details", json={"phone": "abc"}, headers=auth_headers)

    assert response.status_code == 400


def test_update_lead_details_404_for_lead_in_different_org(client, db_session, sample_org, auth_headers):
    other_org = Organization(name="Other Details Org", slug="other-details-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-details@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id, first_name="Cross", last_name="OrgLead", phone="12145559706")
    db_session.add(other_lead)
    db_session.commit()

    response = client.patch(f"/leads/{other_lead.id}/details", json={"notes": "x"}, headers=auth_headers)

    assert response.status_code == 404


def test_update_lead_details_resyncs_registry_on_phone_change(client, db_session, sample_org, sample_advisor, auth_headers):
    """Confirms the reused registry-resync logic actually runs - same dedup protection as admin's fix_lead_contact_info."""
    from app.models.models import ContactRegistry

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Registry", last_name="Resync", phone="12145559707")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/details", json={"phone": "12145559799"}, headers=auth_headers)

    assert response.status_code == 200
    entry = db_session.query(ContactRegistry).filter(
        ContactRegistry.organization_id == sample_org.id, ContactRegistry.normalized_phone == "12145559799",
    ).first()
    assert entry is not None


def test_update_lead_details_logs_audit_action_with_diff(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import AuditLogEntry
    import json

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Audit", last_name="Details", phone="12145559708", notes="old note")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/details", json={"notes": "new note"}, headers=auth_headers)
    assert response.status_code == 200

    entry = (
        db_session.query(AuditLogEntry)
        .filter(AuditLogEntry.organization_id == sample_org.id, AuditLogEntry.action == "lead.update_details", AuditLogEntry.target_id == lead.id)
        .first()
    )
    assert entry is not None
    details = json.loads(entry.details)
    assert details["notes"]["from"] == "old note"
    assert details["notes"]["to"] == "new note"


def test_update_lead_details_partial_update_leaves_other_fields_unchanged(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Partial", last_name="Update",
                phone="12145559709", email="original@example.com")
    db_session.add(lead)
    db_session.commit()

    response = client.patch(f"/leads/{lead.id}/details", json={"notes": "just adding a note"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["email"] == "original@example.com"
    assert response.json()["phone"] == "12145559709"


# ---------------------------------------------------------------------------
# Referral leads - per Mike's explicit, concrete scenario: "I'm dealing
# with Deborah Brown and... she's now given me Lisa and Tom [via a
# permission-to-access form]... I need to be able to send out some
# messages to Lisa and Tom... I need to get them in for a pre-need
# [conversation]." Lisa and Tom become REAL, separate Lead records
# (their own cadence, replies, outcomes), not notes on Deborah's record.
# ---------------------------------------------------------------------------

def test_create_referral_creates_real_separate_lead(client, db_session, sample_org, sample_advisor, auth_headers):
    source_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                        first_name="Deborah", last_name="Brown", phone="12145559100")
    db_session.add(source_lead)
    db_session.commit()

    response = client.post(f"/leads/{source_lead.id}/referrals", json={
        "first_name": "Lisa", "last_name": "Brown", "phone": "12145559101",
        "relationship_type": "child", "tier": "pre_need",
    }, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["first_name"] == "Lisa"
    assert body["id"] != source_lead.id
    assert body["tier"] == "pre_need"
    assert body["source_file"] == "referral"

    # Confirm it's a genuinely real, independently-queryable Lead row
    referred_lead = db_session.query(Lead).filter(Lead.id == body["id"]).first()
    assert referred_lead is not None
    assert referred_lead.organization_id == sample_org.id


def test_create_referral_records_the_link_with_correct_relationship(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import LeadReferral, RelationshipType

    source_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                        first_name="Deborah", last_name="Brown", phone="12145559102")
    db_session.add(source_lead)
    db_session.commit()

    response = client.post(f"/leads/{source_lead.id}/referrals", json={
        "first_name": "Tom", "last_name": "Brown", "phone": "12145559103",
        "relationship_type": "power_of_attorney",
    }, headers=auth_headers)
    referred_lead_id = response.json()["id"]

    link = db_session.query(LeadReferral).filter(
        LeadReferral.source_lead_id == source_lead.id, LeadReferral.referred_lead_id == referred_lead_id,
    ).first()
    assert link is not None
    assert link.relationship_type == RelationshipType.POWER_OF_ATTORNEY


def test_create_referral_uses_same_dedup_check_as_manual_entry(client, db_session, sample_org, sample_advisor, auth_headers):
    """A referral lead matching an existing lead's phone must be caught as a duplicate, same as any other creation path."""
    # Created via the real endpoint, not a bare Lead() row - check_and_register
    # matches against ContactRegistry, which only gets populated by going
    # through an actual creation path (manual/import/referral), not by
    # inserting a Lead row directly and bypassing that registration step.
    client.post("/leads/manual", json={
        "first_name": "Lisa", "last_name": "Existing", "phone": "12145559104", "tier": "pre_need",
    }, headers=auth_headers)

    source_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                        first_name="Deborah", last_name="Brown", phone="12145559105")
    db_session.add(source_lead)
    db_session.commit()

    response = client.post(f"/leads/{source_lead.id}/referrals", json={
        "first_name": "Lisa", "last_name": "Existing", "phone": "12145559104",
        "relationship_type": "child",
    }, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["is_duplicate"] is True


def test_create_referral_rejects_invalid_relationship_type(client, db_session, sample_org, sample_advisor, auth_headers):
    source_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                        first_name="Deborah", last_name="Brown", phone="12145559106")
    db_session.add(source_lead)
    db_session.commit()

    response = client.post(f"/leads/{source_lead.id}/referrals", json={
        "first_name": "Lisa", "last_name": "Brown", "phone": "12145559107",
        "relationship_type": "best_friend",
    }, headers=auth_headers)

    assert response.status_code == 400
    assert "relationship_type must be one of" in response.json()["detail"]


def test_create_referral_404_for_source_lead_in_different_org(client, db_session, sample_org, auth_headers):
    other_org = Organization(name="Other Referral Org", slug="other-referral-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-referral@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                       first_name="Cross", last_name="OrgSource", phone="12145559108")
    db_session.add(other_lead)
    db_session.commit()

    response = client.post(f"/leads/{other_lead.id}/referrals", json={
        "first_name": "Lisa", "last_name": "Brown", "phone": "12145559109", "relationship_type": "child",
    }, headers=auth_headers)

    assert response.status_code == 404


def test_create_referral_logs_audit_action_with_source_lead_context(client, db_session, sample_org, sample_advisor, auth_headers):
    from app.models.models import AuditLogEntry
    import json

    source_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                        first_name="Deborah", last_name="Brown", phone="12145559110")
    db_session.add(source_lead)
    db_session.commit()

    response = client.post(f"/leads/{source_lead.id}/referrals", json={
        "first_name": "Lisa", "last_name": "Brown", "phone": "12145559111", "relationship_type": "child",
    }, headers=auth_headers)
    referred_lead_id = response.json()["id"]

    entry = (
        db_session.query(AuditLogEntry)
        .filter(AuditLogEntry.organization_id == sample_org.id, AuditLogEntry.action == "lead.create_referral", AuditLogEntry.target_id == referred_lead_id)
        .first()
    )
    assert entry is not None
    details = json.loads(entry.details)
    assert details["source_lead_id"] == source_lead.id
    assert details["relationship_type"] == "child"


def test_list_referrals_shows_who_a_lead_referred(client, db_session, sample_org, sample_advisor, auth_headers):
    source_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                        first_name="Deborah", last_name="Brown", phone="12145559112")
    db_session.add(source_lead)
    db_session.commit()

    client.post(f"/leads/{source_lead.id}/referrals", json={
        "first_name": "Lisa", "last_name": "Brown", "phone": "12145559113", "relationship_type": "child",
    }, headers=auth_headers)
    client.post(f"/leads/{source_lead.id}/referrals", json={
        "first_name": "Tom", "last_name": "Brown", "phone": "12145559114", "relationship_type": "child",
    }, headers=auth_headers)

    response = client.get(f"/leads/{source_lead.id}/referrals", headers=auth_headers)

    assert response.status_code == 200
    names = {r["first_name"] for r in response.json()["referred"]}
    assert names == {"Lisa", "Tom"}
    assert response.json()["referred_by"] is None


def test_list_referrals_shows_who_referred_a_lead(client, db_session, sample_org, sample_advisor, auth_headers):
    source_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                        first_name="Deborah", last_name="Brown", phone="12145559115")
    db_session.add(source_lead)
    db_session.commit()

    referral_response = client.post(f"/leads/{source_lead.id}/referrals", json={
        "first_name": "Lisa", "last_name": "Brown", "phone": "12145559116", "relationship_type": "child",
    }, headers=auth_headers)
    referred_lead_id = referral_response.json()["id"]

    response = client.get(f"/leads/{referred_lead_id}/referrals", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["referred_by"]["first_name"] == "Deborah"
    assert response.json()["referred_by"]["relationship_type"] == "child"
    assert response.json()["referred"] == []


def test_referral_lead_is_independently_eligible_for_cadence(client, db_session, sample_org, sample_advisor, auth_headers):
    """Confirms the referred lead is a genuinely normal, full Lead row - not a restricted/special type."""
    from app.models.models import LeadStatus
    source_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                        first_name="Deborah", last_name="Brown", phone="12145559117")
    db_session.add(source_lead)
    db_session.commit()

    response = client.post(f"/leads/{source_lead.id}/referrals", json={
        "first_name": "Lisa", "last_name": "Brown", "phone": "12145559118", "relationship_type": "child",
    }, headers=auth_headers)
    referred_lead = db_session.query(Lead).filter(Lead.id == response.json()["id"]).first()

    assert referred_lead.status == LeadStatus.NEW
    assert referred_lead.message_track is not None
    assert referred_lead.assigned_to_id == sample_advisor.id


# ---------------------------------------------------------------------------
# Certified Appointment pipeline endpoints - Mike's exact definition:
# "we've already solicited. We had to contact them. They booked the
# appointment. We confirmed. Now we're just waiting for them to come
# in." Real, auditable sequence of events, not a score.
# ---------------------------------------------------------------------------

def test_get_certification_returns_real_status(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Cert", last_name="Endpoint", phone="12145559300")
    db_session.add(lead)
    db_session.commit()

    response = client.get(f"/leads/{lead.id}/certification", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["current_step"] is None
    assert response.json()["is_certified"] is False


def test_get_certification_404_for_lead_in_different_org(client, db_session, sample_org, auth_headers):
    other_org = Organization(name="Other Cert Org", slug="other-cert-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-cert@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                       first_name="Cross", last_name="Org", phone="12145559301")
    db_session.add(other_lead)
    db_session.commit()

    response = client.get(f"/leads/{other_lead.id}/certification", headers=auth_headers)
    assert response.status_code == 404


def test_confirm_appointment_endpoint_requires_a_real_booking(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="NoBooking", last_name="Yet", phone="12145559302")
    db_session.add(lead)
    db_session.commit()

    response = client.post(f"/leads/{lead.id}/certification/confirm", headers=auth_headers)

    assert response.status_code == 400
    assert "no booked appointment" in response.json()["detail"]


def test_confirm_appointment_endpoint_advances_to_waiting(client, db_session, sample_org, sample_advisor, auth_headers):
    from datetime import datetime, timezone

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="ReadyToConfirm", last_name="Test", phone="12145559303")
    db_session.add(lead)
    db_session.flush()
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Hi"))
    db_session.add(Reply(lead_id=lead.id, body="Yes"))
    db_session.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                                booked_time=datetime.now(timezone.utc)))
    db_session.commit()

    before = client.get(f"/leads/{lead.id}/certification", headers=auth_headers)
    assert before.json()["current_step"] == "booked"

    response = client.post(f"/leads/{lead.id}/certification/confirm", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["current_step"] == "waiting"
    assert response.json()["is_certified"] is True


def test_confirm_appointment_logs_audit_action(client, db_session, sample_org, sample_advisor, auth_headers):
    from datetime import datetime, timezone
    from app.models.models import AuditLogEntry

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="AuditCert", last_name="Test", phone="12145559304")
    db_session.add(lead)
    db_session.flush()
    booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                           booked_time=datetime.now(timezone.utc))
    db_session.add(booking)
    db_session.commit()

    client.post(f"/leads/{lead.id}/certification/confirm", headers=auth_headers)

    entry = (
        db_session.query(AuditLogEntry)
        .filter(AuditLogEntry.organization_id == sample_org.id, AuditLogEntry.action == "lead.confirm_appointment", AuditLogEntry.target_id == lead.id)
        .first()
    )
    assert entry is not None
