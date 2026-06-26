"""
Tests for app/services/cadence_service.py
"""

from app.services.cadence_service import (
    start_cadence, stop_cadence_for_lead, CADENCE_SCHEDULE_DAYS, TOTAL_TOUCHES,
)
from app.models.models import CadenceState, CadenceStatus, LeadStatus, Lead, LeadTier


def test_cadence_schedule_matches_spec():
    """Day 1, 3, 7, 10, 14, 21, 30, 45, 60 - the exact spec Mike gave."""
    assert CADENCE_SCHEDULE_DAYS == [1, 3, 7, 10, 14, 21, 30, 45, 60]
    assert TOTAL_TOUCHES == 9


def test_start_cadence_creates_active_state(db_session, sample_lead):
    state = start_cadence(db_session, sample_lead)
    assert state is not None
    assert state.status == CadenceStatus.ACTIVE
    assert state.current_touch_number == 0
    assert state.next_touch_due_at is not None


def test_start_cadence_is_idempotent(db_session, sample_lead):
    """Starting a cadence twice for the same lead should not create two states."""
    state1 = start_cadence(db_session, sample_lead)
    state2 = start_cadence(db_session, sample_lead)
    assert state1.id == state2.id
    count = db_session.query(CadenceState).filter(CadenceState.lead_id == sample_lead.id).count()
    assert count == 1


def test_dnc_leads_are_excluded_from_cadence(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Bad", last_name="Lead", phone="12145550000", status=LeadStatus.DNC)
    db_session.add(lead)
    db_session.commit()
    state = start_cadence(db_session, lead)
    assert state is None


def test_needs_tier_review_leads_are_excluded_from_cadence(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Untyped", last_name="Lead", phone="12145550001",
                status=LeadStatus.NEEDS_TIER_REVIEW, tier=LeadTier.PARTIAL)
    db_session.add(lead)
    db_session.commit()
    state = start_cadence(db_session, lead)
    assert state is None


def test_duplicate_leads_are_excluded_from_cadence(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Dup", last_name="Lead", phone="12145550002",
                status=LeadStatus.NEW, is_duplicate=True)
    db_session.add(lead)
    db_session.commit()
    state = start_cadence(db_session, lead)
    assert state is None


def test_email_only_leads_are_excluded_from_sms_cadence(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Email", last_name="Only", email="x@example.com",
                contact_channel="email_only", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.commit()
    state = start_cadence(db_session, lead)
    assert state is None


def test_stop_cadence_marks_correct_status(db_session, sample_lead):
    start_cadence(db_session, sample_lead)
    stop_cadence_for_lead(db_session, sample_lead.id, CadenceStatus.STOPPED_REPLIED)
    state = db_session.query(CadenceState).filter(CadenceState.lead_id == sample_lead.id).first()
    assert state.status == CadenceStatus.STOPPED_REPLIED
    assert state.completed_at is not None


def test_stop_cadence_on_already_stopped_lead_is_a_no_op(db_session, sample_lead):
    start_cadence(db_session, sample_lead)
    stop_cadence_for_lead(db_session, sample_lead.id, CadenceStatus.STOPPED_REPLIED)
    state_after_first_stop = db_session.query(CadenceState).filter(CadenceState.lead_id == sample_lead.id).first()
    first_completed_at = state_after_first_stop.completed_at

    stop_cadence_for_lead(db_session, sample_lead.id, CadenceStatus.STOPPED_DNC)
    state_after_second_call = db_session.query(CadenceState).filter(CadenceState.lead_id == sample_lead.id).first()
    assert state_after_second_call.status == CadenceStatus.STOPPED_REPLIED  # unchanged
    assert state_after_second_call.completed_at == first_completed_at


# ---------------------------------------------------------------------------
# run_due_cadences - real production bug found via Render's cron logs: a
# deactivated test advisor ("Advisor Three") with no Twilio configured had
# a real lead assigned to them, and the daily job flagged it as an error
# EVERY SINGLE DAY since the job had no awareness of advisor.is_active at
# all. Deactivating the account alone would not have fixed this - the job
# query needed to actually skip deactivated advisors' leads cleanly.
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone
from app.services.cadence_service import run_due_cadences
from app.models.models import User, Organization
from app.services.auth_service import hash_password


def _due_state(db_session, lead):
    state = CadenceState(
        lead_id=lead.id,
        status=CadenceStatus.ACTIVE,
        current_touch_number=0,
        next_touch_due_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(state)
    db_session.flush()
    return state


def test_run_due_cadences_skips_deactivated_advisor_without_counting_as_error(db_session, sample_org, sample_advisor):
    sample_advisor.is_active = False
    db_session.commit()

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Inactive", last_name="AdvisorLead", phone="12145559001", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.flush()
    _due_state(db_session, lead)
    db_session.commit()

    result = run_due_cadences(db_session, organization_id=sample_org.id)

    assert result["errors"] == 0
    assert not any("no Twilio" in e for e in result["error_details"])


def test_run_due_cadences_still_errors_for_active_advisor_with_no_twilio(db_session, sample_org):
    """The original, still-valid case: an ACTIVE advisor genuinely missing Twilio setup should still surface as a real error to fix."""
    advisor = User(organization_id=sample_org.id, email="no-twilio@restland.com",
                   password_hash=hash_password("x"), full_name="No Twilio Advisor", role="advisor",
                   is_active=True, twilio_phone_number=None)
    db_session.add(advisor)
    db_session.flush()

    lead = Lead(organization_id=sample_org.id, assigned_to_id=advisor.id,
                first_name="NoTwilio", last_name="Lead", phone="12145559002", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.flush()
    _due_state(db_session, lead)
    db_session.commit()

    result = run_due_cadences(db_session, organization_id=sample_org.id)

    assert result["errors"] == 1
    assert any("no Twilio" in e for e in result["error_details"])


def test_run_due_cadences_skips_lead_with_no_assigned_advisor_at_all(db_session, sample_org):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=None,
                first_name="Unassigned", last_name="Lead", phone="12145559003", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.flush()
    _due_state(db_session, lead)
    db_session.commit()

    result = run_due_cadences(db_session, organization_id=sample_org.id)

    assert result["errors"] == 0
