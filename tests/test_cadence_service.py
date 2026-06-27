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


# ---------------------------------------------------------------------------
# Channel mixing - per Mike's explicit, direct correction: NOT two
# parallel tracks running at once for a lead with both phone and email
# (that would solicit the same touch on both channels simultaneously,
# which he called "kinda fucking stupid"), and NOT one fixed channel
# for the whole sequence either. One sequence, same 9 touches, but each
# touch's CHANNEL is deliberately mixed - text for speed early on,
# email for touches that have had time to build something worth
# saying. Only applies to leads with BOTH contact methods - a lead with
# only one always uses that one for every touch.
# ---------------------------------------------------------------------------

from app.services.cadence_service import _channel_for_touch, MIXED_CHANNEL_PATTERN


def test_channel_for_touch_phone_only_lead_always_sms(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Phone", last_name="Only", phone="12145559500", email=None)
    for touch in range(1, 10):
        assert _channel_for_touch(lead, touch) == "sms"


def test_channel_for_touch_email_only_lead_always_email(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Email", last_name="Only", phone=None, email="emailonly@example.com")
    for touch in range(1, 10):
        assert _channel_for_touch(lead, touch) == "email"


def test_channel_for_touch_both_contact_methods_uses_mixed_pattern(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Both", last_name="Methods", phone="12145559501", email="both@example.com")

    actual_channels = [_channel_for_touch(lead, touch) for touch in range(1, 10)]

    assert actual_channels == MIXED_CHANNEL_PATTERN


def test_mixed_pattern_never_has_two_emails_in_a_row():
    """Real correctness check on the pattern itself, not just per-lead behavior - confirms it never doubles up on the slower channel back to back."""
    for i in range(len(MIXED_CHANNEL_PATTERN) - 1):
        assert not (MIXED_CHANNEL_PATTERN[i] == "email" and MIXED_CHANNEL_PATTERN[i + 1] == "email"), \
            f"Two emails in a row at positions {i}, {i+1}"


def test_mixed_pattern_has_nine_entries_matching_total_touches():
    from app.services.cadence_service import TOTAL_TOUCHES
    assert len(MIXED_CHANNEL_PATTERN) == TOTAL_TOUCHES


def test_channel_for_touch_with_neither_contact_method_defaults_to_sms_rather_than_crashing(db_session, sample_org, sample_advisor):
    """Should not happen in practice (start_cadence excludes such leads) but must never raise if ever reached."""
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Neither", last_name="Method", phone=None, email=None)
    assert _channel_for_touch(lead, 1) == "sms"


# ---------------------------------------------------------------------------
# End-to-end: run_due_cadences actually sends through the right channel
# for a lead with both phone and email, and never requires Twilio
# config for a touch that's going out as email (the real bug fixed
# alongside this feature - previously EVERY touch unconditionally
# required advisor.twilio_phone_number, even ones that should have
# gone out as email).
# ---------------------------------------------------------------------------

from unittest.mock import patch, MagicMock


def test_run_due_cadences_sends_sms_for_an_sms_touch(db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="SmsTouch", last_name="Lead", phone="12145559600", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.flush()
    _due_state(db_session, lead)  # current_touch_number=0 -> this will be touch 1 = sms
    db_session.commit()

    with patch("app.services.sms_service.get_twilio_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(sid="SM_touch1", status="queued")
        mock_get_client.return_value = mock_client

        result = run_due_cadences(db_session, organization_id=sample_org.id)

    assert result["errors"] == 0
    assert result["sent"] == 1
    from app.models.models import Message, EmailMessage
    assert db_session.query(Message).filter(Message.lead_id == lead.id).count() == 1
    assert db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).count() == 0


def test_run_due_cadences_sends_email_for_an_email_touch_no_twilio_required(db_session, sample_org, sample_advisor):
    """
    The actual bug fix: touch 3 (Day 7) is an email touch in the mixed
    pattern. An advisor with NO Twilio configured at all must still be
    able to send this touch successfully, since it never needs Twilio.
    """
    sample_advisor.twilio_phone_number = None
    sample_advisor.twilio_account_sid = None
    db_session.commit()

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="EmailTouch", last_name="Lead", phone="12145559601",
                email="emailtouch@example.com", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.flush()
    state = _due_state(db_session, lead)
    state.current_touch_number = 2  # next touch is touch 3 = email in the mixed pattern
    db_session.commit()

    with patch("app.services.email_service.send_email_via_provider") as mock_send_email:
        mock_send_email.return_value = {"success": True, "provider_message_id": "sg_touch3", "error": None}

        result = run_due_cadences(db_session, organization_id=sample_org.id)

    assert result["errors"] == 0, result["error_details"]
    assert result["sent"] == 1
    from app.models.models import Message, EmailMessage
    assert db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).count() == 1
    assert db_session.query(Message).filter(Message.lead_id == lead.id).count() == 0


def test_run_due_cadences_never_sends_both_channels_for_the_same_touch(db_session, sample_org, sample_advisor):
    """The core safety property Mike was explicit about - never the same touch on both channels at once."""
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="BothMethods", last_name="Lead", phone="12145559602",
                email="bothmethods@example.com", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.flush()
    _due_state(db_session, lead)  # touch 1 = sms per the mixed pattern
    db_session.commit()

    with patch("app.services.sms_service.get_twilio_client") as mock_get_client, \
         patch("app.services.email_service.send_email_via_provider") as mock_send_email:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(sid="SM_both_test", status="queued")
        mock_get_client.return_value = mock_client
        mock_send_email.return_value = {"success": True, "provider_message_id": "sg_both", "error": None}

        run_due_cadences(db_session, organization_id=sample_org.id)

    from app.models.models import Message, EmailMessage
    sms_count = db_session.query(Message).filter(Message.lead_id == lead.id).count()
    email_count = db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).count()
    # Touch 1 is sms in the pattern - exactly one channel fired, never both
    assert sms_count == 1
    assert email_count == 0


# ---------------------------------------------------------------------------
# Compliance Preflight wiring in the cadence job - the real gap fixed:
# the previous re-check only ever looked at Lead.status == DNC, never
# the suppression list. A suppressed number whose Lead.status had
# drifted out of sync would have still been texted by this automated,
# no-human-in-the-loop job.
# ---------------------------------------------------------------------------

def test_run_due_cadences_stops_a_suppressed_lead_even_when_status_is_not_dnc(db_session, sample_org, sample_advisor):
    """THE REAL FIX: status says NEW, but the phone is suppressed - the cadence job must still stop and never send."""
    from app.models.models import SuppressionEntry, Message

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Suppressed", last_name="StatusDrift", phone="12145559610", status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.flush()
    _due_state(db_session, lead)
    db_session.add(SuppressionEntry(organization_id=sample_org.id, phone="12145559610", reason="Manually suppressed"))
    db_session.commit()

    with patch("app.services.sms_service.get_twilio_client") as mock_get_client:
        result = run_due_cadences(db_session, organization_id=sample_org.id)

        mock_get_client.assert_not_called()  # the real proof: Twilio is never even reached

    assert result["sent"] == 0
    assert db_session.query(Message).filter(Message.lead_id == lead.id).count() == 0

    from app.models.models import CadenceState, CadenceStatus
    state = db_session.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    assert state.status == CadenceStatus.STOPPED_DNC


def test_run_due_cadences_stops_dnc_status_lead_as_before(db_session, sample_org, sample_advisor):
    """Confirms the original, already-working DNC-status behavior is preserved by the refactor, not just the new suppression case."""
    from app.models.models import Message, CadenceState, CadenceStatus

    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="DncStatus", last_name="Lead", phone="12145559611", status=LeadStatus.DNC)
    db_session.add(lead)
    db_session.flush()
    _due_state(db_session, lead)
    db_session.commit()

    with patch("app.services.sms_service.get_twilio_client") as mock_get_client:
        run_due_cadences(db_session, organization_id=sample_org.id)
        mock_get_client.assert_not_called()

    assert db_session.query(Message).filter(Message.lead_id == lead.id).count() == 0
    state = db_session.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    assert state.status == CadenceStatus.STOPPED_DNC
