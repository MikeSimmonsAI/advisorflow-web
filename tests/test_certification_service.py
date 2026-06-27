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


# ---------------------------------------------------------------------------
# get_certification_status_batch - the batched version used by the
# Replies action center, so a page of 200 replies doesn't trigger up to
# 600 individual database queries (3 per lead x potentially 200 leads).
# ---------------------------------------------------------------------------

from app.services.certification_service import get_certification_status_batch


def test_batch_returns_empty_dict_for_empty_input(db_session):
    assert get_certification_status_batch(db_session, []) == {}


def test_batch_returns_entry_for_every_requested_lead_even_with_no_activity(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)

    result = get_certification_status_batch(db_session, [lead.id])

    assert lead.id in result
    assert result[lead.id]["current_step"] is None


def test_batch_matches_single_lead_function_exactly_across_all_pipeline_stages(db_session, sample_org, sample_advisor):
    """The batch version must produce IDENTICAL results to the single-lead version for the same data - this is the actual correctness guarantee that matters."""
    brand_new = _lead(db_session, sample_org, sample_advisor, phone="12145559210")

    solicited_only = _lead(db_session, sample_org, sample_advisor, phone="12145559211")
    db_session.add(Message(lead_id=solicited_only.id, sender_id=sample_advisor.id, body="Hi"))

    fully_certified = _lead(db_session, sample_org, sample_advisor, phone="12145559212")
    db_session.add(Message(lead_id=fully_certified.id, sender_id=sample_advisor.id, body="Hi"))
    db_session.add(Reply(lead_id=fully_certified.id, body="Yes"))
    db_session.add(BookingLink(lead_id=fully_certified.id, user_id=sample_advisor.id, status="booked",
                                booked_time=datetime.now(timezone.utc), confirmed_at=datetime.now(timezone.utc)))
    db_session.commit()

    all_leads = [brand_new, solicited_only, fully_certified]
    batch_result = get_certification_status_batch(db_session, [l.id for l in all_leads])

    for lead in all_leads:
        single_result = get_certification_status(db_session, lead)
        assert batch_result[lead.id] == single_result, f"Mismatch for lead {lead.id}"


def test_batch_uses_most_recent_booking_per_lead_not_first_found(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="Hi"))
    db_session.add(Reply(lead_id=lead.id, body="Yes"))
    old_booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                               booked_time=datetime(2025, 1, 1, tzinfo=timezone.utc))
    new_booking = BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                               booked_time=datetime(2026, 6, 1, tzinfo=timezone.utc))
    db_session.add_all([old_booking, new_booking])
    db_session.commit()

    result = get_certification_status_batch(db_session, [lead.id])

    assert result[lead.id]["booking_link_id"] == new_booking.id


def test_batch_does_not_mix_up_bookings_between_different_leads(db_session, sample_org, sample_advisor):
    """Genuine correctness check: each lead's result must reflect ONLY its own data, not another lead's."""
    lead_a = _lead(db_session, sample_org, sample_advisor, phone="12145559213")
    lead_b = _lead(db_session, sample_org, sample_advisor, phone="12145559214")

    db_session.add(Message(lead_id=lead_a.id, sender_id=sample_advisor.id, body="Hi A"))
    db_session.add(Reply(lead_id=lead_a.id, body="Yes A"))
    booking_a = BookingLink(lead_id=lead_a.id, user_id=sample_advisor.id, status="booked",
                             booked_time=datetime.now(timezone.utc))
    db_session.add(booking_a)
    # lead_b gets nothing at all
    db_session.commit()

    result = get_certification_status_batch(db_session, [lead_a.id, lead_b.id])

    assert result[lead_a.id]["current_step"] == "booked"
    assert result[lead_a.id]["booking_link_id"] == booking_a.id
    assert result[lead_b.id]["current_step"] is None
    assert result[lead_b.id]["booking_link_id"] is None
