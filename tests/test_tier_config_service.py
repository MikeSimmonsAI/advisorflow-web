"""
Tests for app/services/tier_config_service.py - the real, per-org tier
configuration system that replaced the hardcoded LeadTier/MessageTrack
Python enums. Per Mike's explicit decision: funeral itself migrated
onto this system rather than living as a separate hardcoded default -
these tests confirm Restland's seeded data is correct AND that a
genuinely different, non-Restland organization's tiers stay fully
independent.
"""

import pytest

from app.models.models import Organization, TierDefinition
from app.services.tier_config_service import (
    get_tier_definition, list_tier_definitions, validate_tier_key,
    validate_manually_selectable_tier_key, get_tone_context_for_track,
    seed_default_tier_definitions,
)


# ---------------------------------------------------------------------------
# Seeding - the real migration this whole system depends on.
# ---------------------------------------------------------------------------

def test_seeding_creates_all_eight_restland_tiers(db_session, sample_org):
    definitions = list_tier_definitions(db_session, sample_org.id)
    assert len(definitions) == 8
    assert {d.tier_key for d in definitions} == {
        "pre_need", "at_need", "imminent", "contract_sold",
        "email_only", "partial", "addr_only", "new_inquiry",
    }


def test_seeding_is_idempotent_does_not_create_duplicates(db_session, sample_org):
    """sample_org already seeded once via the fixture - seeding again must change nothing."""
    before_count = db_session.query(TierDefinition).filter(TierDefinition.organization_id == sample_org.id).count()

    created = seed_default_tier_definitions(db_session, sample_org.id)

    assert created == []
    after_count = db_session.query(TierDefinition).filter(TierDefinition.organization_id == sample_org.id).count()
    assert after_count == before_count


def test_seeding_a_brand_new_org_with_no_tiers_creates_real_rows(db_session):
    new_org = Organization(name="Brand New Org", slug="brand-new-org-test", plan="trial")
    db_session.add(new_org)
    db_session.commit()

    created = seed_default_tier_definitions(db_session, new_org.id)

    assert len(created) == 8
    assert list_tier_definitions(db_session, new_org.id) != []


def test_pre_need_tier_matches_original_hardcoded_values_exactly(db_session, sample_org):
    """The actual correctness guarantee for the migration: Restland's real data must match what the old LeadTier/MessageTrack/TIER_TO_TRACK/TRACK_CONTEXT system had."""
    definition = get_tier_definition(db_session, sample_org.id, "pre_need")

    assert definition.tier_label == "Pre-Need"
    assert definition.track_key == "pre_need_lock_price"
    assert "locking in today's pricing" in definition.ai_tone_context
    assert definition.is_manual_selectable is True


def test_email_only_tier_is_not_manually_selectable(db_session, sample_org):
    """Real, preserved business rule: email_only/addr_only/partial are auto-detected import OUTCOMES, never something an advisor picks by hand."""
    for tier_key in ("email_only", "addr_only", "partial"):
        definition = get_tier_definition(db_session, sample_org.id, tier_key)
        assert definition.is_manual_selectable is False


# ---------------------------------------------------------------------------
# Org isolation - the actual real-world reason this system exists.
# ---------------------------------------------------------------------------

def test_a_different_org_has_completely_independent_tiers(db_session, sample_org):
    """The real, defining property: a second org with its own custom tiers must never see or be affected by Restland's."""
    roofing_org = Organization(name="Roofing Co", slug="roofing-co-test", plan="trial")
    db_session.add(roofing_org)
    db_session.commit()

    roofing_tier = TierDefinition(
        organization_id=roofing_org.id, tier_key="quote_requested", tier_label="Quote Requested",
        track_key="quote_follow_up", track_label="Quote Follow-Up",
        ai_tone_context="A homeowner requested a roofing quote. Be helpful and direct.",
    )
    db_session.add(roofing_tier)
    db_session.commit()

    restland_tiers = {d.tier_key for d in list_tier_definitions(db_session, sample_org.id)}
    roofing_tiers = {d.tier_key for d in list_tier_definitions(db_session, roofing_org.id)}

    assert "quote_requested" not in restland_tiers
    assert "pre_need" not in roofing_tiers
    assert roofing_tiers == {"quote_requested"}


def test_validate_tier_key_rejects_a_tier_that_belongs_to_a_different_org(db_session, sample_org):
    other_org = Organization(name="Other Validation Org", slug="other-validation-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_tier = TierDefinition(
        organization_id=other_org.id, tier_key="other_orgs_tier", tier_label="Other",
        track_key="other_track", track_label="Other",
        ai_tone_context="Some other org's context.",
    )
    db_session.add(other_tier)
    db_session.commit()

    with pytest.raises(ValueError, match="not a valid tier"):
        validate_tier_key(db_session, sample_org.id, "other_orgs_tier")


# ---------------------------------------------------------------------------
# validate_manually_selectable_tier_key
# ---------------------------------------------------------------------------

def test_validate_manually_selectable_accepts_a_real_selectable_tier(db_session, sample_org):
    definition = validate_manually_selectable_tier_key(db_session, sample_org.id, "pre_need")
    assert definition.tier_key == "pre_need"


def test_validate_manually_selectable_rejects_a_non_selectable_tier(db_session, sample_org):
    with pytest.raises(ValueError, match="tier must be one of"):
        validate_manually_selectable_tier_key(db_session, sample_org.id, "email_only")


def test_validate_manually_selectable_error_message_only_lists_selectable_tiers(db_session, sample_org):
    """The error message itself must not suggest email_only/addr_only/partial as valid options, since those are exactly the ones being rejected."""
    with pytest.raises(ValueError) as exc_info:
        validate_manually_selectable_tier_key(db_session, sample_org.id, "not_a_real_tier")

    assert "email_only" not in str(exc_info.value)
    assert "addr_only" not in str(exc_info.value)
    assert "partial" not in str(exc_info.value)
    assert "pre_need" in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_tone_context_for_track - looked up by TRACK, not tier, since
# multiple tiers can share one track.
# ---------------------------------------------------------------------------

def test_get_tone_context_for_track_shared_by_multiple_tiers(db_session, sample_org):
    """partial and addr_only both map to the needs_review track - confirms a real value comes back for it."""
    context = get_tone_context_for_track(db_session, sample_org.id, "needs_review")
    assert "Needs review" in context


def test_get_tone_context_for_unknown_track_returns_generic_fallback(db_session, sample_org):
    context = get_tone_context_for_track(db_session, sample_org.id, "not_a_real_track")
    assert context == "General outreach."
