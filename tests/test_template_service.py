"""
Tests for app/services/template_service.py
"""

from app.services.template_service import (
    get_sms_template, upsert_template, reset_template_to_default, list_all_templates_with_defaults,
)
from app.models.models import MessageTrack


def test_no_override_returns_none(db_session, sample_org):
    result = get_sms_template(db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE)
    assert result is None


def test_upsert_creates_override_and_get_returns_it(db_session, sample_org, sample_advisor):
    upsert_template(
        db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE, "sms",
        "Custom message: {first_name}, lock in your price now!", sample_advisor.id,
    )
    result = get_sms_template(db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE)
    assert result == "Custom message: {first_name}, lock in your price now!"


def test_upsert_twice_updates_rather_than_duplicates(db_session, sample_org, sample_advisor):
    from app.models.models import MessageTemplate
    upsert_template(db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE, "sms", "First version", sample_advisor.id)
    upsert_template(db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE, "sms", "Second version", sample_advisor.id)

    count = db_session.query(MessageTemplate).filter(
        MessageTemplate.organization_id == sample_org.id,
        MessageTemplate.message_track == MessageTrack.PRE_NEED_LOCK_PRICE,
        MessageTemplate.channel == "sms",
    ).count()
    assert count == 1

    result = get_sms_template(db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE)
    assert result == "Second version"


def test_reset_removes_override(db_session, sample_org, sample_advisor):
    upsert_template(db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE, "sms", "Custom", sample_advisor.id)
    reset_template_to_default(db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE, "sms")
    result = get_sms_template(db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE)
    assert result is None


def test_list_all_templates_shows_defaults_when_uncustomized(db_session, sample_org):
    results = list_all_templates_with_defaults(db_session, sample_org.id)
    sms_pre_need = next(r for r in results if r["message_track"] == "pre_need_lock_price" and r["channel"] == "sms")
    assert sms_pre_need["is_customized"] is False
    assert len(sms_pre_need["body_template"]) > 0  # has the hardcoded default, not empty


def test_list_all_templates_reflects_customization(db_session, sample_org, sample_advisor):
    upsert_template(db_session, sample_org.id, MessageTrack.UPSELL_EXISTING_CUSTOMER, "sms", "My custom upsell text", sample_advisor.id)
    results = list_all_templates_with_defaults(db_session, sample_org.id)
    upsell_sms = next(r for r in results if r["message_track"] == "upsell_existing" and r["channel"] == "sms")
    assert upsell_sms["is_customized"] is True
    assert upsell_sms["body_template"] == "My custom upsell text"


def test_different_orgs_have_independent_templates(db_session, sample_org, sample_advisor):
    from app.models.models import Organization
    other_org = Organization(name="Other Org", slug="other", plan="trial")
    db_session.add(other_org)
    db_session.commit()

    upsert_template(db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE, "sms", "Restland's custom text", sample_advisor.id)

    restland_result = get_sms_template(db_session, sample_org.id, MessageTrack.PRE_NEED_LOCK_PRICE)
    other_org_result = get_sms_template(db_session, other_org.id, MessageTrack.PRE_NEED_LOCK_PRICE)

    assert restland_result == "Restland's custom text"
    assert other_org_result is None  # other org has no override, unaffected by Restland's customization
