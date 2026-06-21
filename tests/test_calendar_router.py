"""
Router-level tests for app/routers/calendar_router.py

The cancel_booking test below specifically proves a real security fix:
this endpoint previously had NO ownership check at all, meaning any
logged-in advisor (in any organization) could cancel another advisor's
booking just by knowing or guessing the booking_id.
"""

from unittest.mock import patch
from app.models.models import Lead, BookingLink, Organization, User
from app.services.auth_service import hash_password, create_access_token


def test_cancel_booking_requires_auth(client):
    response = client.post("/calendar/cancel-booking/fake-id")
    assert response.status_code == 401


def test_cancel_booking_404s_for_nonexistent_booking(client, auth_headers):
    response = client.post("/calendar/cancel-booking/does-not-exist", headers=auth_headers)
    assert response.status_code == 404


def test_cancel_booking_blocks_cross_org_access(client, db_session, sample_lead, sample_advisor):
    """
    THE SECURITY FIX: an advisor in a DIFFERENT organization must not be
    able to cancel a booking that belongs to sample_org. Before the fix,
    this endpoint had zero ownership check and would have let this through.
    """
    booking = BookingLink(lead_id=sample_lead.id, user_id=sample_advisor.id, status="pending")
    db_session.add(booking)
    db_session.commit()

    other_org = Organization(name="Attacker Org", slug="attacker-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()

    attacker = User(organization_id=other_org.id, email="attacker@evil.com",
                     password_hash=hash_password("x"), full_name="Attacker", role="advisor")
    db_session.add(attacker)
    db_session.commit()
    attacker_token = create_access_token(attacker)
    attacker_headers = {"Authorization": f"Bearer {attacker_token}"}

    response = client.post(f"/calendar/cancel-booking/{booking.id}", headers=attacker_headers)
    # Must be rejected (404, not found from the attacker's perspective) -
    # NOT 200, which is what the old unfixed code would have returned.
    assert response.status_code == 404

    db_session.refresh(booking)
    assert booking.status == "pending"  # untouched - the attack did not succeed


def test_cancel_booking_works_for_legitimate_owner(client, auth_headers, db_session, sample_lead, sample_advisor):
    booking = BookingLink(lead_id=sample_lead.id, user_id=sample_advisor.id, status="pending")
    db_session.add(booking)
    db_session.commit()

    with patch("app.routers.calendar_router.cancel_calendar_event") as mock_cancel:
        mock_cancel.return_value = {"success": True}
        response = client.post(f"/calendar/cancel-booking/{booking.id}", headers=auth_headers)

    assert response.status_code == 200


def test_connect_calendar_requires_auth(client):
    response = client.get("/calendar/connect")
    assert response.status_code == 401


def test_connect_calendar_returns_authorization_url(client, auth_headers):
    response = client.get("/calendar/connect", headers=auth_headers)
    assert response.status_code == 200
    assert "authorization_url" in response.json()


def test_confirm_booking_404s_for_invalid_token(client):
    response = client.post("/calendar/confirm-booking", json={
        "booking_token": "nonexistent-token",
        "booked_datetime": "2026-07-01T09:00:00",
    })
    assert response.status_code == 404
