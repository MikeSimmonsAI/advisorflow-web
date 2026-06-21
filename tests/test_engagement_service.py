"""
Tests for app/services/engagement_service.py - the hot/warm/cold
classification that was missing from the web app entirely (desktop's
Re-Engagement screen has HOT/WARM/COLD tabs; web had nothing).
"""

from app.models.models import Lead, LeadStatus, LeadTier, EngagementTemperature, Reply, CadenceState, CadenceStatus
from app.services.engagement_service import classify_lead_temperature, recompute_and_save, recompute_for_organization
from app.services.cadence_service import start_cadence


def test_brand_new_lead_is_unknown(db_session, sample_lead):
    result = classify_lead_temperature(db_session, sample_lead)
    assert result == EngagementTemperature.UNKNOWN


def test_booked_lead_is_always_hot(db_session, sample_lead):
    sample_lead.status = LeadStatus.BOOKED
    db_session.commit()
    result = classify_lead_temperature(db_session, sample_lead)
    assert result == EngagementTemperature.HOT


def test_lead_with_hot_reply_is_hot(db_session, sample_lead):
    reply = Reply(lead_id=sample_lead.id, body="Yes I'm interested!", is_hot=True)
    db_session.add(reply)
    db_session.commit()
    result = classify_lead_temperature(db_session, sample_lead)
    assert result == EngagementTemperature.HOT


def test_lead_with_non_hot_reply_is_not_automatically_hot(db_session, sample_lead):
    """A neutral reply (is_hot=False) shouldn't make a non-imminent lead hot."""
    reply = Reply(lead_id=sample_lead.id, body="Please don't text me at this time", is_hot=False)
    db_session.add(reply)
    db_session.commit()
    result = classify_lead_temperature(db_session, sample_lead)
    assert result != EngagementTemperature.HOT


def test_imminent_lead_with_any_reply_is_hot(db_session, sample_lead):
    """Imminent-need urgency elevates even a neutral reply to hot."""
    sample_lead.tier = LeadTier.IMMINENT
    reply = Reply(lead_id=sample_lead.id, body="ok", is_hot=False)
    db_session.add(reply)
    db_session.commit()
    result = classify_lead_temperature(db_session, sample_lead)
    assert result == EngagementTemperature.HOT


def test_active_cadence_with_no_reply_is_warm(db_session, sample_lead):
    start_cadence(db_session, sample_lead)
    result = classify_lead_temperature(db_session, sample_lead)
    assert result == EngagementTemperature.WARM


def test_dnc_lead_is_cold(db_session, sample_lead):
    sample_lead.status = LeadStatus.DNC
    db_session.commit()
    result = classify_lead_temperature(db_session, sample_lead)
    assert result == EngagementTemperature.COLD


def test_dead_lead_is_cold(db_session, sample_lead):
    sample_lead.status = LeadStatus.DEAD
    db_session.commit()
    result = classify_lead_temperature(db_session, sample_lead)
    assert result == EngagementTemperature.COLD


def test_completed_cadence_with_no_resolution_is_cold(db_session, sample_lead):
    start_cadence(db_session, sample_lead)
    sample_lead.cadence_state.status = CadenceStatus.COMPLETED
    db_session.commit()
    result = classify_lead_temperature(db_session, sample_lead)
    assert result == EngagementTemperature.COLD


def test_stopped_dnc_cadence_is_cold(db_session, sample_lead):
    start_cadence(db_session, sample_lead)
    sample_lead.cadence_state.status = CadenceStatus.STOPPED_DNC
    db_session.commit()
    result = classify_lead_temperature(db_session, sample_lead)
    assert result == EngagementTemperature.COLD


def test_recompute_and_save_persists_to_database(db_session, sample_lead):
    sample_lead.status = LeadStatus.BOOKED
    db_session.commit()
    recompute_and_save(db_session, sample_lead)
    db_session.refresh(sample_lead)
    assert sample_lead.engagement_temperature == EngagementTemperature.HOT


def test_recompute_for_organization_processes_every_lead(db_session, sample_org, sample_advisor):
    lead1 = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                 first_name="A", last_name="One", phone="12145551111", status=LeadStatus.BOOKED)
    lead2 = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                 first_name="B", last_name="Two", phone="12145552222", status=LeadStatus.DNC)
    db_session.add_all([lead1, lead2])
    db_session.commit()

    counts = recompute_for_organization(db_session, sample_org.id)
    assert counts["hot"] >= 1
    assert counts["cold"] >= 1

    db_session.refresh(lead1)
    db_session.refresh(lead2)
    assert lead1.engagement_temperature == EngagementTemperature.HOT
    assert lead2.engagement_temperature == EngagementTemperature.COLD
