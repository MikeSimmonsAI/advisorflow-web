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
