"""
Tests for app/services/dedup_service.py

This is the single most important piece of logic for a 5-advisor
rollout: if this breaks, two advisors could text the same grieving
family, which is a real trust and compliance problem, not just a bug.
"""

from app.services.dedup_service import (
    normalize_phone, normalize_last_name, check_and_register, PLACEHOLDER_LAST_NAME,
)
from app.models.models import ContactRegistry


def test_normalize_phone_handles_various_formats():
    assert normalize_phone("214-555-0101") == "12145550101"
    assert normalize_phone("(214) 555-0101") == "12145550101"
    assert normalize_phone("214.555.0101") == "12145550101"
    assert normalize_phone("2145550101") == "12145550101"
    assert normalize_phone("12145550101") == "12145550101"
    assert normalize_phone("") == ""
    assert normalize_phone(None) == ""


def test_normalize_last_name_strips_punctuation_and_case():
    assert normalize_last_name("Smith") == "smith"
    assert normalize_last_name("O'Brien") == "obrien"
    assert normalize_last_name("  Jones  ") == "jones"
    assert normalize_last_name("") == ""


def test_first_lead_is_never_a_duplicate(db_session, sample_org, sample_advisor):
    is_dup, entry = check_and_register(
        db_session, sample_org.id, "214-555-0101", "Smith", "lead-1", sample_advisor.id
    )
    assert is_dup is False
    assert entry is not None
    assert entry.normalized_phone == "12145550101"


def test_exact_phone_and_lastname_match_is_duplicate(db_session, sample_org, sample_advisor):
    check_and_register(db_session, sample_org.id, "214-555-0101", "Smith", "lead-1", sample_advisor.id)
    is_dup, entry = check_and_register(
        db_session, sample_org.id, "(214) 555-0101", "Smith", "lead-2", sample_advisor.id
    )
    assert is_dup is True


def test_different_phone_formats_still_match(db_session, sample_org, sample_advisor):
    """The exact bug class that was tested manually earlier: dashes vs no dashes vs parens."""
    check_and_register(db_session, sample_org.id, "214-555-0101", "Smith", "lead-1", sample_advisor.id)
    is_dup_dots, _ = check_and_register(db_session, sample_org.id, "214.555.0101", "Smith", "lead-2", sample_advisor.id)
    is_dup_plain, _ = check_and_register(db_session, sample_org.id, "2145550101", "Smith", "lead-3", sample_advisor.id)
    assert is_dup_dots is True
    assert is_dup_plain is True


def test_cross_advisor_duplicate_is_caught(db_session, sample_org, sample_advisor, second_advisor):
    """Advisor 1's 2012 batch vs Advisor 2's 2013 batch containing the same person."""
    check_and_register(db_session, sample_org.id, "214-555-0101", "Smith", "lead-1", sample_advisor.id)
    is_dup, entry = check_and_register(
        db_session, sample_org.id, "2145550101", "Smith", "lead-2", second_advisor.id
    )
    assert is_dup is True
    assert entry.owning_user_id == sample_advisor.id  # original owner, not the second advisor


def test_household_sharing_same_phone_different_last_names_is_not_blocked(db_session, sample_org, sample_advisor):
    """
    Critical edge case Mike specifically flagged: a phone number can
    represent two different real people in the same household. Different
    last names at the same phone must NOT be treated as duplicates.
    """
    is_dup_a, _ = check_and_register(db_session, sample_org.id, "214-555-9999", "Johnson", "lead-a", sample_advisor.id)
    is_dup_b, _ = check_and_register(db_session, sample_org.id, "214-555-9999", "Williams", "lead-b", sample_advisor.id)
    assert is_dup_a is False
    assert is_dup_b is False


def test_placeholder_historical_entries_block_resends(db_session, sample_org, sample_advisor):
    """
    Historical numbers seeded from the old desktop pipeline's sent log
    (see scripts/seed_registry_from_sent_log.py) only have a phone number,
    no real last name. A new import with the real last name must still
    be caught as a duplicate against that placeholder entry.
    """
    placeholder = ContactRegistry(
        organization_id=sample_org.id,
        normalized_phone="12145551234",
        normalized_last_name=PLACEHOLDER_LAST_NAME,
    )
    db_session.add(placeholder)
    db_session.commit()

    is_dup, entry = check_and_register(
        db_session, sample_org.id, "214-555-1234", "Smith", "lead-1", sample_advisor.id
    )
    assert is_dup is True


def test_placeholder_fallback_does_not_break_household_sharing(db_session, sample_org, sample_advisor):
    """
    Confirms the placeholder fallback is scoped narrowly and does NOT
    regress into a general phone-only dedup rule - this was a real bug
    caught during manual testing before it shipped.
    """
    is_dup_a, _ = check_and_register(db_session, sample_org.id, "214-555-7777", "Garcia", "lead-a", sample_advisor.id)
    is_dup_b, _ = check_and_register(db_session, sample_org.id, "214-555-7777", "Lee", "lead-b", sample_advisor.id)
    assert is_dup_a is False
    assert is_dup_b is False  # different last name, same phone, NOT a placeholder entry -> not blocked


def test_missing_phone_or_lastname_skips_dedup_without_registering(db_session, sample_org, sample_advisor):
    is_dup, entry = check_and_register(db_session, sample_org.id, "", "Smith", "lead-1", sample_advisor.id)
    assert is_dup is False
    assert entry is None

    is_dup2, entry2 = check_and_register(db_session, sample_org.id, "214-555-0101", "", "lead-2", sample_advisor.id)
    assert is_dup2 is False
    assert entry2 is None
