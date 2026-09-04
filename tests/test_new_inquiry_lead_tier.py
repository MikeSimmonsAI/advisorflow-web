"""
The New Inquiry lead tier - brand-new web/cold leads with no prior Restland
relationship.

READ THIS BEFORE CHANGING ANYTHING HERE
---------------------------------------
This feature is currently HALF PRESENT in production, and this file is
deliberately structured so that the missing half stays visible in the test
suite instead of being hidden behind a skip.

What still exists:
  * LeadTier.NEW_INQUIRY                     (app/models/models.py)
  * MessageTrack.NEW_INQUIRY_INTRO           (app/models/models.py)
  * force_new_inquiry, accepted by leads_router as a form field and passed
    through to import_leads_from_excel

What is missing:
  * _is_new_inquiry_source does not exist in app/services/import_service.py
  * _infer_tier(raw_value, status_reason) takes no source_raw argument and
    can never return NEW_INQUIRY
  * import_leads_from_excel accepts force_new_inquiry and never reads it

So an import today cannot produce a NEW_INQUIRY lead by any route, while the
enum values and the API parameter all still advertise that it can.

HOW THIS FILE IS ORGANISED
--------------------------
  * Tests for behaviour that EXISTS run normally and must stay green.
  * Tests that specify the MISSING New Inquiry behaviour are marked
    xfail(strict=True) individually, with the precise reason on each. They
    are the written specification for the feature. strict=True is
    deliberate: if the behaviour is implemented, these turn into XPASS
    failures, which is the prompt to delete the markers rather than let a
    silently-passing xfail hide finished work.
  * Nothing here manufactures _is_new_inquiry_source and nothing here
    changes lead qualification or tiering rules. Restoring the feature is a
    business-rule decision and belongs in its own batch.
"""

import pandas as pd
import pytest

from app.models.models import Lead, LeadTier, MessageTrack
from app.services.import_service import _infer_tier, import_leads_from_excel


def _write_xlsx(tmp_path, filename, rows: list[dict]):
    path = tmp_path / filename
    pd.DataFrame(rows).to_excel(path, index=False)
    return str(path)


def _load_is_new_inquiry_source():
    """Imported inside the test, not at module scope.

    This symbol does not exist, and importing it at the top of the file is
    exactly what turned this module into a collection error. Loading it here
    keeps the whole file collectable while letting the individual tests that
    need it fail as xfail rather than taking the rest of the module down."""
    from app.services.import_service import _is_new_inquiry_source
    return _is_new_inquiry_source


# ═════════════════════════════════════════════════════════════════════════════
# The half of the feature that still exists. These must stay green.
# ═════════════════════════════════════════════════════════════════════════════

def test_new_inquiry_tier_and_track_still_exist():
    """The vocabulary survived the removal of the logic that produced it.

    Kept as a live test because it is the other half of the inconsistency:
    if these ever disappear too, the xfails below stop being a TODO and
    become dead specification that should be deleted with them."""
    assert LeadTier.NEW_INQUIRY == "new_inquiry"
    assert MessageTrack.NEW_INQUIRY_INTRO == "new_inquiry_intro"


def test_import_still_accepts_force_new_inquiry():
    """leads_router passes this through on every upload. It is accepted and
    then never read - asserted here so the dead parameter is visible in the
    suite rather than only in a code review."""
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


def test_infer_tier_currently_takes_no_source_argument():
    """TRIPWIRE, not an endorsement.

    This pins the present two-argument signature so the gap is a stated fact.
    When source-based detection is implemented, this test fails - and that
    failure is the reminder to delete it along with the xfail markers below."""
    with pytest.raises(TypeError):
        _infer_tier(raw_value="Pre-Need", status_reason="", source_raw="Web Form")


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
# The specification for the missing half. Each of these is the behaviour the
# feature is supposed to have; each fails today for the reason on its marker.
# ═════════════════════════════════════════════════════════════════════════════

_NO_HELPER = ("_is_new_inquiry_source does not exist in app/services/import_service.py. "
              "Nothing classifies a lead source as a new inquiry today.")
_NO_SOURCE_ARG = ("_infer_tier(raw_value, status_reason) accepts no source_raw argument "
                  "and has no branch that can return NEW_INQUIRY.")
_NO_TIER_TEXT = ("_infer_tier matches only imminent / at need / pre need and falls through "
                 "to PARTIAL, so 'New Inquiry' and 'Cold Lead' in the Lead Type column are "
                 "not recognised.")
_NO_IMPORT_PATH = ("import_leads_from_excel cannot produce a NEW_INQUIRY lead: no source "
                   "detection, and force_new_inquiry is accepted but never read.")


@pytest.mark.xfail(strict=True, reason=_NO_HELPER)
def test_is_new_inquiry_source_matches_web_variants():
    is_new_inquiry_source = _load_is_new_inquiry_source()
    assert is_new_inquiry_source("Web") is True
    assert is_new_inquiry_source("Web Form") is True
    assert is_new_inquiry_source("web-lead") is True
    assert is_new_inquiry_source("Online Inquiry") is True
    assert is_new_inquiry_source("Google Ads") is True
    assert is_new_inquiry_source("Facebook Lead Gen") is True
    assert is_new_inquiry_source("Final Expense Generator") is True


@pytest.mark.xfail(strict=True, reason=_NO_HELPER)
def test_is_new_inquiry_source_false_for_unrelated_values():
    is_new_inquiry_source = _load_is_new_inquiry_source()
    assert is_new_inquiry_source("Referral") is False
    assert is_new_inquiry_source("Walk-in") is False
    assert is_new_inquiry_source("") is False
    assert is_new_inquiry_source(None) is False


@pytest.mark.xfail(strict=True, reason=_NO_SOURCE_ARG)
def test_infer_tier_source_signal_takes_priority_over_tier_column():
    """A web source should win even if the Lead Type column says Pre-Need - it
    is the stronger signal for a never-before-seen contact."""
    assert _infer_tier(raw_value="Pre-Need", status_reason="",
                       source_raw="Web Form") == LeadTier.NEW_INQUIRY


@pytest.mark.xfail(strict=True, reason=_NO_SOURCE_ARG)
def test_infer_tier_contract_sold_still_wins_over_a_web_source():
    """Contract Sold must keep priority even for a web-ish source - a
    re-engaged old customer is not a new inquiry."""
    assert _infer_tier(raw_value="", status_reason="Contract Sold",
                       source_raw="Web Form") == LeadTier.CONTRACT_SOLD


@pytest.mark.xfail(strict=True, reason=_NO_TIER_TEXT)
def test_infer_tier_recognizes_new_inquiry_in_tier_column_text():
    assert _infer_tier(raw_value="New Inquiry", status_reason="") == LeadTier.NEW_INQUIRY
    assert _infer_tier(raw_value="Cold Lead", status_reason="") == LeadTier.NEW_INQUIRY


@pytest.mark.xfail(strict=True, reason=_NO_IMPORT_PATH)
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


@pytest.mark.xfail(strict=True, reason=_NO_IMPORT_PATH)
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


@pytest.mark.xfail(strict=True, reason=_NO_IMPORT_PATH)
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


@pytest.mark.xfail(strict=True, reason=_NO_IMPORT_PATH)
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
