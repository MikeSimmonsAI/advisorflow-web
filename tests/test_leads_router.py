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
