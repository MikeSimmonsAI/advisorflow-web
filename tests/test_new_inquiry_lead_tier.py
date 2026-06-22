"""
Tests for the New Inquiry lead tier - brand-new web/cold leads with no
prior Restland relationship, added per Mike's explicit request.

Covers:
  - Auto-detection from a "source" column (web/lead gen/online/etc.)
  - Manual override (force_new_inquiry) when no usable source column exists
  - Correct message_track routing for both SMS and email channels
  - That New Inquiry does NOT get collapsed into EMAIL_ONLY like other
    tiers do when a lead has no phone - this was a real bug caught while
    building the feature: the existing channel-override logic ran after
    tier assignment and would have silently overwritten NEW_INQUIRY with
    EMAIL_ONLY for any cold lead lacking a phone number.

Uses synthetic in-memory Excel files (same tmp_path + pandas.to_excel
pattern as test_missing_required_columns_raises_clear_error in
test_import_service.py) rather than the real Restland export fixture,
since these tests need full control over the source/tier columns.
"""

import pandas as pd
import pytest

from app.services.import_service import import_leads_from_excel, parse_excel_file, _infer_tier, _is_new_inquiry_source
from app.models.models import Lead, LeadTier, MessageTrack


def _write_xlsx(tmp_path, filename, rows: list[dict]):
    path = tmp_path / filename
    pd.DataFrame(rows).to_excel(path, index=False)
    return str(path)


# --- Unit-level: _is_new_inquiry_source and _infer_tier ---

def test_is_new_inquiry_source_matches_web_variants():
    assert _is_new_inquiry_source("Web") is True
    assert _is_new_inquiry_source("Web Form") is True
    assert _is_new_inquiry_source("web-lead") is True
    assert _is_new_inquiry_source("Online Inquiry") is True
    assert _is_new_inquiry_source("Google Ads") is True
    assert _is_new_inquiry_source("Facebook Lead Gen") is True
    assert _is_new_inquiry_source("Final Expense Generator") is True


def test_is_new_inquiry_source_false_for_unrelated_values():
    assert _is_new_inquiry_source("Referral") is False
    assert _is_new_inquiry_source("Walk-in") is False
    assert _is_new_inquiry_source("") is False
    assert _is_new_inquiry_source(None) is False


def test_infer_tier_source_signal_takes_priority_over_tier_column():
    """A web source should win even if the Lead Type column says Pre-Need - it's a stronger signal for a never-before-seen contact."""
    tier = _infer_tier(raw_value="Pre-Need", status_reason="", source_raw="Web Form")
    assert tier == LeadTier.NEW_INQUIRY


def test_infer_tier_contract_sold_status_still_wins_over_source():
    """Status Reason=Contract Sold must still take priority, even with a web-ish source value (e.g. a re-engaged old lead)."""
    tier = _infer_tier(raw_value="", status_reason="Contract Sold", source_raw="Web Form")
    assert tier == LeadTier.CONTRACT_SOLD


def test_infer_tier_recognizes_new_inquiry_in_tier_column_text():
    tier = _infer_tier(raw_value="New Inquiry", status_reason="", source_raw="")
    assert tier == LeadTier.NEW_INQUIRY

    tier2 = _infer_tier(raw_value="Cold Lead", status_reason="", source_raw="")
    assert tier2 == LeadTier.NEW_INQUIRY


def test_infer_tier_no_source_no_match_falls_back_to_normal_rules():
    tier = _infer_tier(raw_value="Pre-Need", status_reason="", source_raw="Referral")
    assert tier == LeadTier.PRE_NEED


# --- Integration: full import with a source column ---

def test_import_auto_detects_new_inquiry_from_source_column(db_session, sample_org, sample_advisor, tmp_path):
    file_path = _write_xlsx(tmp_path, "web_leads.xlsx", [
        {"First Name": "Casey", "Last Name": "Web", "Phone": "214-555-0301", "Email": "", "Source": "Web Form"},
        {"First Name": "Jordan", "Last Name": "Normal", "Phone": "214-555-0302", "Email": "", "Source": "Referral", "Lead Type": "Pre-Need"},
    ])

    result = import_leads_from_excel(db_session, file_path, sample_org.id, sample_advisor.id, source_year=2026, source_filename="web_leads.xlsx")

    assert result["tier_breakdown"]["new_inquiry"] == 1
    web_lead = db_session.query(Lead).filter(Lead.first_name == "Casey").first()
    assert web_lead.tier == LeadTier.NEW_INQUIRY
    assert web_lead.message_track == MessageTrack.NEW_INQUIRY_INTRO

    normal_lead = db_session.query(Lead).filter(Lead.first_name == "Jordan").first()
    assert normal_lead.tier == LeadTier.PRE_NEED


def test_import_force_new_inquiry_override_tags_every_row(db_session, sample_org, sample_advisor, tmp_path):
    """The manual override Mike asked for: tag the whole batch regardless of tier/source columns."""
    file_path = _write_xlsx(tmp_path, "all_cold.xlsx", [
        {"First Name": "Pat", "Last Name": "ColdOne", "Phone": "214-555-0303", "Email": "", "Lead Type": "Pre-Need"},
        {"First Name": "Sam", "Last Name": "ColdTwo", "Phone": "214-555-0304", "Email": ""},
    ])

    result = import_leads_from_excel(
        db_session, file_path, sample_org.id, sample_advisor.id,
        source_year=2026, source_filename="all_cold.xlsx", force_new_inquiry=True,
    )

    assert result["tier_breakdown"]["new_inquiry"] == 2
    leads = db_session.query(Lead).filter(Lead.last_name.like("Cold%")).all()
    assert len(leads) == 2
    assert all(lead.tier == LeadTier.NEW_INQUIRY for lead in leads)
    assert all(lead.message_track == MessageTrack.NEW_INQUIRY_INTRO for lead in leads)


def test_new_inquiry_email_only_lead_keeps_new_inquiry_tier_not_generic_email_only(db_session, sample_org, sample_advisor, tmp_path):
    """
    Regression test for the bug caught while building this: a New Inquiry
    lead with no phone used to silently get overwritten to the generic
    LeadTier.EMAIL_ONLY by the channel-routing logic, losing the New
    Inquiry distinction and falling back to email_only_nurture copy that
    assumes an existing (just phoneless) Restland relationship.
    """
    file_path = _write_xlsx(tmp_path, "web_email_only.xlsx", [
        {"First Name": "Robin", "Last Name": "NoPhone", "Phone": "", "Email": "robin@example.com", "Source": "Web Form"},
    ])

    import_leads_from_excel(db_session, file_path, sample_org.id, sample_advisor.id, source_year=2026, source_filename="web_email_only.xlsx")

    lead = db_session.query(Lead).filter(Lead.first_name == "Robin").first()
    assert lead is not None
    assert lead.contact_channel == "email_only"
    assert lead.tier == LeadTier.NEW_INQUIRY
    assert lead.message_track == MessageTrack.NEW_INQUIRY_INTRO


def test_other_tiers_still_collapse_to_email_only_when_no_phone(db_session, sample_org, sample_advisor, tmp_path):
    """Confirms the NEW_INQUIRY exception didn't break the existing behavior for every other tier."""
    file_path = _write_xlsx(tmp_path, "preneed_email_only.xlsx", [
        {"First Name": "Taylor", "Last Name": "EmailOnly", "Phone": "", "Email": "taylor@example.com", "Lead Type": "Pre-Need"},
    ])

    import_leads_from_excel(db_session, file_path, sample_org.id, sample_advisor.id, source_year=2026, source_filename="preneed_email_only.xlsx")

    lead = db_session.query(Lead).filter(Lead.first_name == "Taylor").first()
    assert lead is not None
    assert lead.tier == LeadTier.EMAIL_ONLY
    assert lead.message_track == MessageTrack.EMAIL_ONLY_NURTURE


def test_new_inquiry_tier_routes_to_new_inquiry_track_for_sms(db_session, sample_org, sample_advisor, tmp_path):
    file_path = _write_xlsx(tmp_path, "web_sms.xlsx", [
        {"First Name": "Drew", "Last Name": "SmsWeb", "Phone": "214-555-0399", "Email": "", "Source": "Online"},
    ])

    import_leads_from_excel(db_session, file_path, sample_org.id, sample_advisor.id, source_year=2026, source_filename="web_sms.xlsx")

    lead = db_session.query(Lead).filter(Lead.first_name == "Drew").first()
    assert lead.contact_channel == "sms"
    assert lead.tier == LeadTier.NEW_INQUIRY
    assert lead.message_track == MessageTrack.NEW_INQUIRY_INTRO
