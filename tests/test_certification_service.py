"""
Tests for app/services/certification_service.py - the Certified
Appointment pipeline. Mike's exact, direct definition: "certified
means that we've already solicited. We had to contact them. They
booked the appointment. We confirmed. Now we're just waiting for them
to come in." A real, auditable sequence of events, not an AI-judged
score - every test here checks an actual underlying fact (a Message
row exists, a Reply row exists, etc.), not a derived status field.
"""

from datetime import datetime, timezone

from app.models.models import Lead, Message, EmailMessage, Reply, BookingLink, LeadStatus
from app.services.certification_service import (
    get_certification_status, confirm_appointment,
    STEP_SOLICITED, STEP_CONTACTED, STEP_BOOKED, STEP_CONFIRMED, STEP_WAITING,
)


def _lead(db_session, org, advisor, phone="12145559200"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Cert", last_name="Test", phone=phone, status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.commit()
    return lead


def test_brand_new_lead_has_no_current_step(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)

    result = get_certification_status(db_session, lead)

    assert result["current_step"] is None
    assert result["is_certified"] is False
    assert all(v is False for v in result["steps_completed"].values())


def test_after_solicited_only_current_step_is_solicited(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Hi there"))
    db_session.commit()

    result = get_certification_status(db_session, lead)

    assert result["current_step"] == STEP_SOLICITED
    assert result["steps_completed"][STEP_SOLICITED] is True
    assert result["steps_completed"][STEP_CONTACTED] is False
    assert result["is_certified"] is False


def test_email_only_solicitation_also_counts(db_session, sample_org, sample_advisor):
    """A lead contacted only by email (no SMS) must still count as solicited - both channels count."""
    lead = _lead(db_session, sample_org, sample_advisor)
    db_session.add(EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id, subject="Hi", body_html="<p>Hi</p>"))
    db_session.commit()

    result = get_certification_status(db_session, lead)

    assert result["steps_completed"][STEP_SOLICITED] is True


def test_after_contacted_current_step_is_contacted(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Hi there"))
    db_session.add(Reply(lead_id=lead.id, body="Yes I'm interested"))
    db_session.commit()

    result = get_certification_status(db_session, lead)

    assert result["current_step"] == STEP_CONTACTED
    assert result["steps_completed"][STEP_CONTACTED] is True
    assert result["steps_completed"][STEP_BOOKED] is False


def test_after_booked_current_step_is_booked_not_yet_confirmed(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Hi"))
    db_session.add(Reply(lead_id=lead.id, body="Yes"))
    booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked", booked_time=datetime.now(timezone.utc))
    db_session.add(booking)
    db_session.commit()

    result = get_certification_status(db_session, lead)

    assert result["current_step"] == STEP_BOOKED
    assert result["steps_completed"][STEP_BOOKED] is True
    assert result["steps_completed"][STEP_CONFIRMED] is False
    assert result["booking_link_id"] == booking.id


def test_pending_booking_link_does_not_count_as_booked(db_session, sample_org, sample_advisor):
    """A BookingLink with status='pending' (link sent, not yet acted on) must NOT count as a real booking."""
    lead = _lead(db_session, sample_org, sample_advisor)
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Hi"))
    db_session.add(Reply(lead_id=lead.id, body="Yes"))
    db_session.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="pending"))
    db_session.commit()

    result = get_certification_status(db_session, lead)

    assert result["current_step"] == STEP_CONTACTED
    assert result["steps_completed"][STEP_BOOKED] is False


def test_fully_confirmed_lead_is_certified_and_waiting(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Hi"))
    db_session.add(Reply(lead_id=lead.id, body="Yes"))
    booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                           booked_time=datetime.now(timezone.utc), confirmed_at=datetime.now(timezone.utc))
    db_session.add(booking)
    db_session.commit()

    result = get_certification_status(db_session, lead)

    assert result["current_step"] == STEP_WAITING
    assert result["is_certified"] is True
    assert all(result["steps_completed"].values())


def test_uses_most_recent_booked_link_when_multiple_exist(db_session, sample_org, sample_advisor):
    """If a lead has multiple booking links over time, the most recent BOOKED one is what matters."""
    lead = _lead(db_session, sample_org, sample_advisor)
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Hi"))
    db_session.add(Reply(lead_id=lead.id, body="Yes"))
    old_booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="cancelled",
                               booked_time=datetime(2025, 1, 1, tzinfo=timezone.utc))
    new_booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                               booked_time=datetime(2026, 6, 1, tzinfo=timezone.utc))
    db_session.add_all([old_booking, new_booking])
    db_session.commit()

    result = get_certification_status(db_session, lead)

    assert result["booking_link_id"] == new_booking.id
    assert result["current_step"] == STEP_BOOKED


# ---------------------------------------------------------------------------
# confirm_appointment - the deliberate, separate confirmation action
# ---------------------------------------------------------------------------

def test_confirm_appointment_sets_confirmed_at(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                           booked_time=datetime.now(timezone.utc))
    db_session.add(booking)
    db_session.commit()

    assert booking.confirmed_at is None
    confirm_appointment(db_session, booking)

    db_session.refresh(booking)
    assert booking.confirmed_at is not None


def test_confirm_appointment_is_idempotent(db_session, sample_org, sample_advisor):
    """Confirming an already-confirmed booking preserves the ORIGINAL confirmation timestamp."""
    lead = _lead(db_session, sample_org, sample_advisor)
    booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                           booked_time=datetime.now(timezone.utc))
    db_session.add(booking)
    db_session.commit()

    confirm_appointment(db_session, booking)
    db_session.refresh(booking)
    first_confirmed_at = booking.confirmed_at

    confirm_appointment(db_session, booking)
    db_session.refresh(booking)

    assert booking.confirmed_at == first_confirmed_at
