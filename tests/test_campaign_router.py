"""Tests for the Campaign Builder feature."""

from app.models.models import (
    CadenceState,
    Campaign,
    Lead,
    LeadStatus,
    LeadTier,
    MessageTrack,
    Organization,
    User,
)
from app.services.auth_service import hash_password


def _lead(db_session, org, advisor, *, first_name, tier, source_year, status=LeadStatus.NEW, track=None):
    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id if advisor else None,
        first_name=first_name,
        last_name="Campaign",
        phone=f"1214555{source_year or 0}{len(first_name):03d}"[:11],
        tier=tier,
        source_year=source_year,
        status=status,
        message_track=track or MessageTrack.PRE_NEED_LOCK_PRICE,
    )
    db_session.add(lead)
    db_session.flush()
    return lead


def _create_campaign(client, admin_auth_headers, *, criteria, message_track="upsell_existing", name="2012 Pre-Need Campaign"):
    response = client.post(
        "/campaigns",
        headers=admin_auth_headers,
        json={"name": name, "filter_criteria": criteria, "message_track": message_track},
    )
    assert response.status_code == 200
    return response.json()


def test_campaign_preview_combines_tier_and_source_year_without_modifying_data(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    match_one = _lead(db_session, sample_org, sample_advisor, first_name="MatchOne", tier=LeadTier.PRE_NEED, source_year=2012)
    match_two = _lead(db_session, sample_org, sample_advisor, first_name="MatchTwo", tier=LeadTier.PRE_NEED, source_year=2012)
    wrong_year = _lead(db_session, sample_org, sample_advisor, first_name="WrongYear", tier=LeadTier.PRE_NEED, source_year=2013)
    wrong_tier = _lead(db_session, sample_org, sample_advisor, first_name="WrongTier", tier=LeadTier.AT_NEED, source_year=2012)
    dnc_match = _lead(db_session, sample_org, sample_advisor, first_name="DncMatch", tier=LeadTier.PRE_NEED, source_year=2012, status=LeadStatus.DNC)
    db_session.commit()

    campaign = _create_campaign(
        client,
        admin_auth_headers,
        criteria={"tier": "pre_need", "source_year": 2012},
        message_track="upsell_existing",
    )

    before_tracks = {
        lead.id: lead.message_track
        for lead in [match_one, match_two, wrong_year, wrong_tier, dnc_match]
    }

    response = client.post(f"/campaigns/{campaign['id']}/preview", headers=admin_auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["matching_count"] == 3
    assert data["eligible_count"] == 2
    assert data["skipped_dnc_count"] == 1
    sample_ids = {item["id"] for item in data["sample"]}
    assert match_one.id in sample_ids
    assert match_two.id in sample_ids
    assert dnc_match.id in sample_ids
    assert wrong_year.id not in sample_ids
    assert wrong_tier.id not in sample_ids

    for lead in [match_one, match_two, wrong_year, wrong_tier, dnc_match]:
        db_session.refresh(lead)
        assert lead.message_track == before_tracks[lead.id]


def test_campaign_apply_updates_only_matching_org_leads_and_skips_dnc(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    match_one = _lead(db_session, sample_org, sample_advisor, first_name="MatchOne", tier=LeadTier.PRE_NEED, source_year=2012)
    match_two = _lead(db_session, sample_org, sample_advisor, first_name="MatchTwo", tier=LeadTier.PRE_NEED, source_year=2012)
    dnc_match = _lead(db_session, sample_org, sample_advisor, first_name="DncMatch", tier=LeadTier.PRE_NEED, source_year=2012, status=LeadStatus.DNC)
    wrong_year = _lead(db_session, sample_org, sample_advisor, first_name="WrongYear", tier=LeadTier.PRE_NEED, source_year=2013)

    other_org = Organization(name="Other Campaign Org", slug="other-campaign", plan="trial")
    db_session.add(other_org)
    db_session.flush()
    other_admin = User(
        organization_id=other_org.id,
        email="other-campaign-admin@example.com",
        password_hash=hash_password("OtherPass123!"),
        full_name="Other Campaign Admin",
        role="org_admin",
    )
    db_session.add(other_admin)
    db_session.flush()
    other_lead = _lead(db_session, other_org, other_admin, first_name="OtherOrgMatch", tier=LeadTier.PRE_NEED, source_year=2012)
    db_session.commit()

    campaign = _create_campaign(
        client,
        admin_auth_headers,
        criteria={"tier": "pre_need", "source_year": 2012},
        message_track="upsell_existing",
    )

    response = client.post(
        f"/campaigns/{campaign['id']}/apply",
        headers=admin_auth_headers,
        json={"start_cadence": False},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["matched_count"] == 3
    assert data["updated_count"] == 2
    assert data["skipped_dnc_count"] == 1
    assert data["cadence_started_count"] == 0

    for lead in [match_one, match_two]:
        db_session.refresh(lead)
        assert lead.message_track == MessageTrack.UPSELL_EXISTING_CUSTOMER

    for lead in [dnc_match, wrong_year, other_lead]:
        db_session.refresh(lead)
        assert lead.message_track == MessageTrack.PRE_NEED_LOCK_PRICE


def test_campaign_apply_can_start_cadence_for_eligible_matching_leads(
    client, admin_auth_headers, db_session, sample_org, sample_advisor
):
    eligible = _lead(db_session, sample_org, sample_advisor, first_name="CadenceReady", tier=LeadTier.PRE_NEED, source_year=2012)
    dnc_match = _lead(db_session, sample_org, sample_advisor, first_name="DncCadence", tier=LeadTier.PRE_NEED, source_year=2012, status=LeadStatus.DNC)
    db_session.commit()

    campaign = _create_campaign(
        client,
        admin_auth_headers,
        criteria={"tier": "pre_need", "source_year": 2012},
        message_track="pre_need_lock_price",
    )

    response = client.post(
        f"/campaigns/{campaign['id']}/apply",
        headers=admin_auth_headers,
        json={"start_cadence": True},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["matched_count"] == 2
    assert data["updated_count"] == 1
    assert data["skipped_dnc_count"] == 1
    assert data["cadence_started_count"] == 1

    assert db_session.query(CadenceState).filter(CadenceState.lead_id == eligible.id).count() == 1
    assert db_session.query(CadenceState).filter(CadenceState.lead_id == dnc_match.id).count() == 0


def test_campaign_list_is_org_isolated(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    own_campaign = _create_campaign(
        client,
        admin_auth_headers,
        criteria={"status": "new"},
        name="Own Campaign",
    )

    other_org = Organization(name="Other List Org", slug="other-list-campaign", plan="trial")
    db_session.add(other_org)
    db_session.flush()
    other_campaign = Campaign(
        organization_id=other_org.id,
        name="Other Campaign",
        created_by_id=sample_advisor.id,
        filter_criteria='{"status": "new"}',
        message_track=MessageTrack.PRE_NEED_LOCK_PRICE,
    )
    db_session.add(other_campaign)
    db_session.commit()

    response = client.get("/campaigns", headers=admin_auth_headers)
    assert response.status_code == 200
    ids = {campaign["id"] for campaign in response.json()}

    assert own_campaign["id"] in ids
    assert other_campaign.id not in ids
