"""
Tests for check_compliance_preflight in app/services/compliance_service.py
- the single, real gate every send path (SMS or email) must call
before sending anything. This is the most safety-critical function in
the whole compliance system: a false negative here (saying "safe to
send" when it isn't) means a real legal/compliance violation, not just
a bug.
"""

import pytest

from app.models.models import Lead, LeadStatus, SuppressionEntry
from app.services.compliance_service import check_compliance_preflight


def _lead(db_session, sample_org, sample_advisor, phone="12145559900", email=None, status=LeadStatus.NEW):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Preflight", last_name="Test", phone=phone, email=email, status=status)
    db_session.add(lead)
    db_session.commit()
    return lead


# ---------------------------------------------------------------------------
# Rule 1 - Lead.status == DNC blocks EVERY channel, regardless of which
# channel triggered it. This is the real, channel-agnostic signal.
# ---------------------------------------------------------------------------

def test_dnc_status_blocks_a_lead_with_only_a_phone(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145559901", email=None, status=LeadStatus.DNC)

    with pytest.raises(ValueError, match="DNC"):
        check_compliance_preflight(db_session, lead)


def test_dnc_status_blocks_a_lead_with_only_an_email(db_session, sample_org, sample_advisor):
    """The real gap this fixes: an email-only lead, DNC'd by status alone (no phone to suppress), must still be blocked."""
    lead = _lead(db_session, sample_org, sample_advisor, phone=None, email="dnc-email-only@example.com", status=LeadStatus.DNC)

    with pytest.raises(ValueError, match="DNC"):
        check_compliance_preflight(db_session, lead)


def test_dnc_status_blocks_a_lead_with_both_phone_and_email(db_session, sample_org, sample_advisor):
    """The actual scenario Mike described: a STOP on text must also block email for the same lead."""
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145559902", email="both-methods@example.com", status=LeadStatus.DNC)

    with pytest.raises(ValueError, match="DNC"):
        check_compliance_preflight(db_session, lead)


# ---------------------------------------------------------------------------
# Rule 2 - phone suppression is an ADDITIONAL guard, independent of
# whatever Lead.status currently says.
# ---------------------------------------------------------------------------

def test_phone_suppression_blocks_even_when_status_is_not_dnc(db_session, sample_org, sample_advisor):
    """REAL ENFORCEMENT GAP: a number could be suppressed while its lead's status was never updated to DNC."""
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145559903", status=LeadStatus.NEW)
    db_session.add(SuppressionEntry(organization_id=sample_org.id, phone="12145559903", reason="Manually suppressed"))
    db_session.commit()

    with pytest.raises(ValueError, match="suppression"):
        check_compliance_preflight(db_session, lead)


def test_email_only_lead_with_no_phone_skips_suppression_check_without_erroring(db_session, sample_org, sample_advisor):
    """A lead with no phone at all must not crash the suppression check - it just has nothing to check there."""
    lead = _lead(db_session, sample_org, sample_advisor, phone=None, email="noPhoneAtAll@example.com", status=LeadStatus.NEW)

    check_compliance_preflight(db_session, lead)  # must not raise


# ---------------------------------------------------------------------------
# The clear, expected case - a normal, non-DNC, non-suppressed lead is
# genuinely fine to contact.
# ---------------------------------------------------------------------------

def test_normal_lead_passes_preflight_with_no_exception(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145559904", status=LeadStatus.NEW)

    result = check_compliance_preflight(db_session, lead)

    assert result is None  # explicit return contract: None means "clear to send"


def test_hot_or_replied_status_does_not_block(db_session, sample_org, sample_advisor):
    """Confirms this isn't accidentally blocking on any non-NEW status - only the real DNC status specifically."""
    for idx, status in enumerate((LeadStatus.SENT, LeadStatus.REPLIED, LeadStatus.HOT, LeadStatus.BOOKED)):
        lead = _lead(db_session, sample_org, sample_advisor, phone=f"1214555990{idx}", status=status)
        check_compliance_preflight(db_session, lead)  # must not raise for any of these
