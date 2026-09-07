"""
The New Inquiry lead tier - brand-new web/cold leads with no prior Restland
relationship.

WHAT THIS FILE IS
-----------------
It was written as the SPECIFICATION for a half-present feature. The vocabulary
(LeadTier.NEW_INQUIRY, MessageTrack.NEW_INQUIRY_INTRO) and the API parameter
(force_new_inquiry) all existed and advertised that an import could produce a
New Inquiry lead, while nothing in the pipeline could actually do it. Those
tests were individually marked xfail(strict=True) so the gap stayed visible in
the suite instead of hiding behind a skip.

The feature is now implemented in app/services/import_service.py:
  * _is_new_inquiry_source(source_raw) classifies a Source cell
  * _infer_tier(raw_value, status_reason, source_raw="") can return NEW_INQUIRY
  * HEADER_MAP maps a "Source" column, so its value reaches tiering instead of
    being parked in custom_fields where nothing reads it
  * import_leads_from_excel reads force_new_inquiry

so the xfail markers are gone and every one of these is an ordinary test. The
tripwire that pinned the old two-argument _infer_tier signature went with
them: it asserted the ABSENCE of the feature and existed only to detect the
un-implemented state, so implementing the feature is what retires it.

THE PRIORITY RULES THESE TESTS PIN
----------------------------------
  1. Status Reason "Contract Sold" beats everything, a web source included -
     a re-engaged old customer is not a new inquiry.
  2. A new-inquiry Source beats the Lead Type column.
  3. The Lead Type column's own text ("New Inquiry", "Cold Lead").
  4. force_new_inquiry, the manual batch override, beats all three.
  5. A blank or absent Source changes NOTHING. That is the rule that keeps
     this feature from silently re-tiering imports that already ran, and it
     has its own test rather than being left as an implied property.
"""

import pandas as pd

from app.models.models import Lead, LeadTier, MessageTrack
from app.services.import_service import (_infer_tier, _is_new_inquiry_source,
                                         import_leads_from_excel)


def _write_xlsx(tmp_path, filename, rows: list[dict]):
    path = tmp_path / filename
    pd.DataFrame(rows).to_excel(path, index=False)
    return str(path)


# ═════════════════════════════════════════════════════════════════════════════
# The vocabulary and the API surface.
# ═════════════════════════════════════════════════════════════════════════════

def test_new_inquiry_tier_and_track_still_exist():
    """The enum values the rest of this file is written against. If these ever
    disappear, everything below is dead specification and should go with
    them."""
    assert LeadTier.NEW_INQUIRY == "new_inquiry"
    assert MessageTrack.NEW_INQUIRY_INTRO == "new_inquiry_intro"


def test_import_still_accepts_force_new_inquiry():
    """leads_router passes this through on every upload, so the signature is
    part of the contract: keyword-named, defaulting to False. The behaviour
    behind it is asserted by
    test_import_force_new_inquiry_override_tags_every_row."""
    import inspect
    params = inspect.signature(import_leads_from_excel).parameters
    assert "force_new_inquiry" in params
    assert params["force_new_inquiry"].default is False


def test_infer_tier_contract_sold_status_wins_over_the_lead_type_column():
    assert _infer_tier(raw_value="Pre-Need", status_reason="Contract Sold") == LeadTier.CONTRACT_SOLD


def test_infer_tier_reads_the_lead_type_column():
    assert _infer_tier(raw_value="Pre-Need", status_reason="") == LeadTier.PRE_NEED
    assert _infer_tier(raw_value="At Need", status_reason="") == LeadTier.AT_NEED
    assert _infer_tier(raw_value="Imminent", status_reason="") == LeadTier.IMMINENT


def test_infer_tier_blank_lead_type_falls_back_to_partial_not_pre_need():
    """Blank must never be silently assumed to be Pre-Need - PARTIAL is the
    'needs manual review' answer."""
    assert _infer_tier(raw_value="", status_reason="") == LeadTier.PARTIAL


def test_blank_source_column_does_not_retier_existing_imports(
        db_session, sample_org, sample_advisor, tmp_path):
    """THE NO-REGRESSION RULE, stated as a test rather than assumed.

    Source-based detection is new behaviour on a code path every historical
    import already ran through, so the thing that actually matters is what it
    does when there is NO usable source: a file with a blank Source cell, and
    a file with no Source column at all, must tier exactly as they did before
    the feature existed. If this ever fails, an import that ran fine last year
    would come out differently today."""
    # Blank cell in a Source column that IS present.
    blank_cell = _write_xlsx(tmp_path, "blank_source.xlsx", [
        {"First Name": "Blank", "Last Name": "SourceCell", "Phone": "214-555-0401",
         "Email": "", "Lead Type": "Pre-Need", "Source": ""},
        {"First Name": "BlankUntyped", "Last Name": "SourceCell", "Phone": "214-555-0402",
         "Email": "", "Lead Type": "", "Source": ""},
    ])
    import_leads_from_excel(db_session, blank_cell, sample_org.id, sample_advisor.id,
                            source_year=2026, source_filename="blank_source.xlsx")

    assert db_session.query(Lead).filter(
        Lead.first_name == "Blank").first().tier == LeadTier.PRE_NEED
    assert db_session.query(Lead).filter(
        Lead.first_name == "BlankUntyped").first().tier == LeadTier.PARTIAL

    # No Source column whatsoever - the shape of every export that predates
    # this feature.
    no_column = _write_xlsx(tmp_path, "no_source_column.xlsx", [
        {"First Name": "NoCol", "Last Name": "SourceAbsent", "Phone": "214-555-0403",
         "Email": "", "Lead Type": "Pre-Need"},
    ])
    import_leads_from_excel(db_session, no_column, sample_org.id, sample_advisor.id,
                            source_year=2026, source_filename="no_source_column.xlsx")

    lead = db_session.query(Lead).filter(Lead.first_name == "NoCol").first()
    assert lead.tier == LeadTier.PRE_NEED
    assert lead.message_track == MessageTrack.PRE_NEED_LOCK_PRICE


def test_a_non_web_source_does_not_override_the_lead_type_column(
        db_session, sample_org, sample_advisor, tmp_path):
    """The other half of the same guarantee: a Source column that is populated
    but names an existing relationship must leave tiering alone too. Only the
    values _is_new_inquiry_source recognises may change a tier."""
    file_path = _write_xlsx(tmp_path, "relationship_sources.xlsx", [
        {"First Name": "Ref", "Last Name": "Existing", "Phone": "214-555-0404",
         "Email": "", "Lead Type": "Pre-Need", "Source": "Referral"},
        {"First Name": "Walk", "Last Name": "Existing", "Phone": "214-555-0405",
         "Email": "", "Lead Type": "At Need", "Source": "Walk-in"},
    ])
    import_leads_from_excel(db_session, file_path, sample_org.id, sample_advisor.id,
                            source_year=2026, source_filename="relationship_sources.xlsx")

    assert db_session.query(Lead).filter(
        Lead.first_name == "Ref").first().tier == LeadTier.PRE_NEED
    assert db_session.query(Lead).filter(
        Lead.first_name == "Walk").first().tier == LeadTier.AT_NEED


def test_other_tiers_still_collapse_to_email_only_when_no_phone(
        db_session, sample_org, sample_advisor, tmp_path):
    """Existing, unrelated behaviour that the New Inquiry work must not break
    when it lands: a phoneless Pre-Need lead is still routed to EMAIL_ONLY."""
    file_path = _write_xlsx(tmp_path, "preneed_email_only.xlsx", [
        {"First Name": "Taylor", "Last Name": "EmailOnly", "Phone": "",
         "Email": "taylor@example.com", "Lead Type": "Pre-Need"},
    ])

    import_leads_from_excel(db_session, file_path, sample_org.id, sample_advisor.id,
                            source_year=2026, source_filename="preneed_email_only.xlsx")

    lead = db_session.query(Lead).filter(Lead.first_name == "Taylor").first()
    assert lead is not None
    assert lead.tier == LeadTier.EMAIL_ONLY
    assert lead.message_track == MessageTrack.EMAIL_ONLY_NURTURE


# ═════════════════════════════════════════════════════════════════════════════
# The New Inquiry behaviour itself. These were the xfail(strict=True)
# specification; they are now the tests for shipped behaviour.
# ═════════════════════════════════════════════════════════════════════════════

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
    """A web source should win even if the Lead Type column says Pre-Need - it
    is the stronger signal for a never-before-seen contact."""
    assert _infer_tier(raw_value="Pre-Need", status_reason="",
                       source_raw="Web Form") == LeadTier.NEW_INQUIRY


def test_infer_tier_contract_sold_still_wins_over_a_web_source():
    """Contract Sold must keep priority even for a web-ish source - a
    re-engaged old customer is not a new inquiry."""
    assert _infer_tier(raw_value="", status_reason="Contract Sold",
                       source_raw="Web Form") == LeadTier.CONTRACT_SOLD


def test_infer_tier_recognizes_new_inquiry_in_tier_column_text():
    assert _infer_tier(raw_value="New Inquiry", status_reason="") == LeadTier.NEW_INQUIRY
    assert _infer_tier(raw_value="Cold Lead", status_reason="") == LeadTier.NEW_INQUIRY


def test_import_auto_detects_new_inquiry_from_source_column(
        db_session, sample_org, sample_advisor, tmp_path):
    file_path = _write_xlsx(tmp_path, "web_leads.xlsx", [
        {"First Name": "Casey", "Last Name": "Web", "Phone": "214-555-0301",
         "Email": "", "Source": "Web Form"},
        {"First Name": "Jordan", "Last Name": "Normal", "Phone": "214-555-0302",
         "Email": "", "Source": "Referral", "Lead Type": "Pre-Need"},
    ])

    result = import_leads_from_excel(db_session, file_path, sample_org.id, sample_advisor.id,
                                     source_year=2026, source_filename="web_leads.xlsx")

    assert result["tier_breakdown"]["new_inquiry"] == 1
    web_lead = db_session.query(Lead).filter(Lead.first_name == "Casey").first()
    assert web_lead.tier == LeadTier.NEW_INQUIRY
    assert web_lead.message_track == MessageTrack.NEW_INQUIRY_INTRO

    normal_lead = db_session.query(Lead).filter(Lead.first_name == "Jordan").first()
    assert normal_lead.tier == LeadTier.PRE_NEED


def test_import_force_new_inquiry_override_tags_every_row(
        db_session, sample_org, sample_advisor, tmp_path):
    """The manual override for a spreadsheet with no usable source column:
    tag the whole batch regardless of the tier/source columns."""
    file_path = _write_xlsx(tmp_path, "all_cold.xlsx", [
        {"First Name": "Pat", "Last Name": "ColdOne", "Phone": "214-555-0303",
         "Email": "", "Lead Type": "Pre-Need"},
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


def test_new_inquiry_email_only_lead_keeps_new_inquiry_tier(
        db_session, sample_org, sample_advisor, tmp_path):
    """The bug caught while the feature was first built, kept as part of the
    specification: a New Inquiry lead with no phone must not be overwritten to
    the generic EMAIL_ONLY tier by the channel-routing step, which would fall
    back to copy that assumes an existing (just phoneless) relationship."""
    file_path = _write_xlsx(tmp_path, "web_email_only.xlsx", [
        {"First Name": "Robin", "Last Name": "NoPhone", "Phone": "",
         "Email": "robin@example.com", "Source": "Web Form"},
    ])

    import_leads_from_excel(db_session, file_path, sample_org.id, sample_advisor.id,
                            source_year=2026, source_filename="web_email_only.xlsx")

    lead = db_session.query(Lead).filter(Lead.first_name == "Robin").first()
    assert lead is not None
    assert lead.contact_channel == "email_only"
    assert lead.tier == LeadTier.NEW_INQUIRY
    assert lead.message_track == MessageTrack.NEW_INQUIRY_INTRO


def test_new_inquiry_tier_routes_to_new_inquiry_track_for_sms(
        db_session, sample_org, sample_advisor, tmp_path):
    file_path = _write_xlsx(tmp_path, "web_sms.xlsx", [
        {"First Name": "Drew", "Last Name": "SmsWeb", "Phone": "214-555-0399",
         "Email": "", "Source": "Online"},
    ])

    import_leads_from_excel(db_session, file_path, sample_org.id, sample_advisor.id,
                            source_year=2026, source_filename="web_sms.xlsx")

    lead = db_session.query(Lead).filter(Lead.first_name == "Drew").first()
    assert lead.contact_channel == "sms"
    assert lead.tier == LeadTier.NEW_INQUIRY
    assert lead.message_track == MessageTrack.NEW_INQUIRY_INTRO
