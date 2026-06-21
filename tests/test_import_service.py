"""
Tests for app/services/import_service.py
"""

import pytest
from app.services.import_service import import_leads_from_excel, parse_excel_file
from app.models.models import Lead, LeadTier, MessageTrack, LeadStatus


def test_real_restland_file_imports_with_expected_breakdown(db_session, sample_org, sample_advisor, real_restland_file):
    """
    Locks in the exact numbers verified by hand against the real 1,000-row
    Restland export, so a future code change that silently shifts these
    numbers gets caught immediately instead of discovered after 5 advisors
    are already relying on it.
    """
    result = import_leads_from_excel(
        db_session, real_restland_file, sample_org.id, sample_advisor.id,
        source_year=2012, source_filename="All_Active_Leads__2012_.xlsx",
    )
    assert result["total_rows"] == 1000
    assert result["imported"] == 856
    assert result["new_active_sms_leads"] == 775
    assert result["email_only_leads_queued"] == 55
    assert result["duplicates_flagged"] == 1
    assert result["flagged_call_restricted"] == 25
    assert result["flagged_needs_tier_review"] == 368
    assert result["tier_breakdown"]["contract_sold"] == 340
    assert result["tier_breakdown"]["pre_need"] == 86


def test_contract_sold_leads_get_upsell_track_not_excluded(db_session, sample_org, sample_advisor, real_restland_file):
    """The corrected rule: Contract Sold leads stay active with an upsell offer, never excluded."""
    import_leads_from_excel(db_session, real_restland_file, sample_org.id, sample_advisor.id, 2012, "test.xlsx")
    sold_lead = db_session.query(Lead).filter(Lead.tier == LeadTier.CONTRACT_SOLD).first()
    assert sold_lead is not None
    assert sold_lead.message_track == MessageTrack.UPSELL_EXISTING_CUSTOMER
    assert sold_lead.status != LeadStatus.DNC


def test_untyped_leads_are_held_for_review_not_defaulted_to_pre_need(db_session, sample_org, sample_advisor, real_restland_file):
    """Blank Lead Type must never be silently assumed to be Pre-Need."""
    import_leads_from_excel(db_session, real_restland_file, sample_org.id, sample_advisor.id, 2012, "test.xlsx")
    review_lead = db_session.query(Lead).filter(Lead.tier == LeadTier.PARTIAL).first()
    assert review_lead is not None
    assert review_lead.status == LeadStatus.NEEDS_TIER_REVIEW
    assert review_lead.tier != LeadTier.PRE_NEED


def test_email_only_leads_are_not_discarded(db_session, sample_org, sample_advisor, real_restland_file):
    """No phone, has email -> imported with contact_channel=email_only, not dropped."""
    import_leads_from_excel(db_session, real_restland_file, sample_org.id, sample_advisor.id, 2012, "test.xlsx")
    email_lead = db_session.query(Lead).filter(Lead.contact_channel == "email_only").first()
    assert email_lead is not None
    assert email_lead.phone is None
    assert email_lead.email is not None


def test_call_restricted_leads_are_blocked_regardless_of_tier(db_session, sample_org, sample_advisor, real_restland_file):
    """Allow Phone Calls? = Do Not Allow is a hard compliance exclusion."""
    import_leads_from_excel(db_session, real_restland_file, sample_org.id, sample_advisor.id, 2012, "test.xlsx")
    restricted = db_session.query(Lead).filter(Lead.status == LeadStatus.DNC, Lead.is_duplicate == False).first()
    assert restricted is not None


def test_dry_run_leaves_database_completely_untouched(db_session, sample_org, sample_advisor, real_restland_file):
    """Preview mode must never persist anything - verified to roll back fully."""
    before_count = db_session.query(Lead).count()
    import_leads_from_excel(
        db_session, real_restland_file, sample_org.id, sample_advisor.id,
        2012, "test.xlsx", dry_run=True,
    )
    after_count = db_session.query(Lead).count()
    assert before_count == after_count == 0


def test_dry_run_and_real_run_produce_identical_numbers(db_session, sample_org, sample_advisor, real_restland_file):
    """
    Compares every COUNT/STAT field between a dry run and the real run -
    these must match exactly, since that's the whole point of dry_run
    (preview the exact numbers before committing).

    created_lead_ids is deliberately excluded from this comparison: a
    dry run rolls back and never actually persists any leads, so it
    always returns an empty list for that field by design (see
    import_service.py) - that's correct, intentional behavior, not a
    discrepancy between the two runs' underlying logic.
    """
    dry_result = import_leads_from_excel(
        db_session, real_restland_file, sample_org.id, sample_advisor.id, 2012, "test.xlsx", dry_run=True,
    )
    real_result = import_leads_from_excel(
        db_session, real_restland_file, sample_org.id, sample_advisor.id, 2012, "test.xlsx", dry_run=False,
    )

    dry_stats = {k: v for k, v in dry_result.items() if k != "created_lead_ids"}
    real_stats = {k: v for k, v in real_result.items() if k != "created_lead_ids"}
    assert dry_stats == real_stats

    # And confirm the created_lead_ids behavior itself is correct:
    assert dry_result["created_lead_ids"] == []
    assert len(real_result["created_lead_ids"]) == real_result["imported"]


def test_internal_nsmg_distribution_lists_are_filtered(db_session, sample_org, sample_advisor, real_restland_file):
    result = import_leads_from_excel(db_session, real_restland_file, sample_org.id, sample_advisor.id, 2012, "test.xlsx")
    assert result["skipped_internal_records"] >= 0  # present in schema even if 0 after upstream filtering
    nsmg_leads = db_session.query(Lead).filter(Lead.email.like("%@nsmg.com")).all()
    assert len(nsmg_leads) == 0


def test_missing_required_columns_raises_clear_error(db_session, tmp_path):
    import pandas as pd
    bad_file = tmp_path / "bad.xlsx"
    pd.DataFrame({"Some Random Column": ["x", "y"]}).to_excel(bad_file, index=False)
    with pytest.raises(ValueError):
        parse_excel_file(str(bad_file))
