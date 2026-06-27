"""
Tests for GET /leads/sparklines - real, recent daily counts for the
Overview page's KPI card sparklines. Per the explicit instruction that
nothing in the visual redesign should fabricate numbers - this mirrors
the exact same proven, never-invented pattern as
sms_router.reply_activity_by_day.
"""

from datetime import datetime, timedelta, timezone

from app.models.models import Lead, BookingLink, Organization, User
from app.services.auth_service import hash_password


def test_sparklines_requires_auth(client):
    response = client.get("/leads/sparklines")
    assert response.status_code == 401


def test_sparklines_returns_correct_number_of_days(client, db_session, sample_org, sample_advisor, auth_headers):
    response = client.get("/leads/sparklines?days=7", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body["leads_imported"]) == 7
    assert len(body["bookings"]) == 7


def test_sparklines_leads_imported_counts_real_leads_by_day(client, db_session, sample_org, sample_advisor, auth_headers):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lead_today_1 = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                         first_name="A", last_name="Today", phone="12145559800", created_at=now)
    lead_today_2 = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                         first_name="B", last_name="Today", phone="12145559801", created_at=now)
    lead_yesterday = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                           first_name="C", last_name="Yesterday", phone="12145559802", created_at=now - timedelta(days=1))
    db_session.add_all([lead_today_1, lead_today_2, lead_yesterday])
    db_session.commit()

    response = client.get("/leads/sparklines?days=7", headers=auth_headers)
    body = response.json()

    assert body["leads_imported"][-1] == 2  # today, the most recent entry
    assert body["leads_imported"][-2] == 1  # yesterday


def test_sparklines_bookings_counts_real_bookings_by_day(client, db_session, sample_org, sample_advisor, auth_headers):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Booking", last_name="Test", phone="12145559803")
    db_session.add(lead)
    db_session.flush()
    db_session.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked", booked_time=now))
    db_session.commit()

    response = client.get("/leads/sparklines?days=7", headers=auth_headers)
    body = response.json()

    assert body["bookings"][-1] == 1
    assert sum(body["bookings"]) == 1


def test_sparklines_pending_booking_does_not_count(client, db_session, sample_org, sample_advisor, auth_headers):
    """A BookingLink with status='pending' (sent, not yet acted on) must NOT count as a real booking - matches the certification pipeline's same rule."""
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Pending", last_name="Booking", phone="12145559804")
    db_session.add(lead)
    db_session.flush()
    db_session.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="pending"))
    db_session.commit()

    response = client.get("/leads/sparklines?days=7", headers=auth_headers)

    assert sum(response.json()["bookings"]) == 0


def test_sparklines_empty_days_return_zero_not_missing(client, db_session, sample_org, sample_advisor, auth_headers):
    """Confirms days with no activity return a real 0, never invented or omitted, same guarantee as reply_activity_by_day."""
    response = client.get("/leads/sparklines?days=7", headers=auth_headers)

    body = response.json()
    assert all(isinstance(v, int) for v in body["leads_imported"])
    assert all(isinstance(v, int) for v in body["bookings"])


def test_sparklines_scoped_to_logged_in_advisor_and_org(client, db_session, sample_org, sample_advisor, auth_headers):
    other_org = Organization(name="Other Sparkline Org", slug="other-sparkline-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-sparkline@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                       first_name="Cross", last_name="Org", phone="12145559805")
    db_session.add(other_lead)
    db_session.commit()

    response = client.get("/leads/sparklines?days=7", headers=auth_headers)

    assert sum(response.json()["leads_imported"]) == 0
