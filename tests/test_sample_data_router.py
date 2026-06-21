"""
Tests for app/routers/sample_data_router.py

The most important guarantee tested here: clearing sample data must
NEVER touch real (non-sample-tagged) leads, even when both exist
side-by-side in the same organization. That's the whole safety design
of this feature - the literal source_file == "SAMPLE_DATA" tag is the
only thing the clear endpoint matches on.
"""

from app.models.models import Lead, Reply, CadenceState, Message, Organization, User
from app.services.auth_service import hash_password


def test_generate_requires_super_admin(client, admin_auth_headers):
    """org_admin (not super_admin) must be rejected - same tier as password reset."""
    response = client.post("/sample-data/generate", headers=admin_auth_headers)
    assert response.status_code == 403


def test_generate_requires_super_admin_rejects_plain_advisor(client, auth_headers):
    response = client.post("/sample-data/generate", headers=auth_headers)
    assert response.status_code == 403


def _make_super_admin_headers(db_session, org, email_suffix=""):
    from app.services.auth_service import create_access_token
    import uuid
    unique_email = f"superadmin-sampledata-{email_suffix or uuid.uuid4().hex[:8]}@test.com"
    super_admin = User(organization_id=org.id, email=unique_email,
                        password_hash=hash_password("x"), full_name="Super Admin", role="super_admin")
    db_session.add(super_admin)
    db_session.commit()
    return {"Authorization": f"Bearer {create_access_token(super_admin)}"}, super_admin


def test_generate_creates_leads_with_sample_tag(client, db_session, sample_org):
    headers, _ = _make_super_admin_headers(db_session, sample_org)
    response = client.post("/sample-data/generate", headers=headers)
    assert response.status_code == 200
    assert response.json()["created_count"] > 0

    sample_leads = db_session.query(Lead).filter(
        Lead.organization_id == sample_org.id, Lead.source_file == "SAMPLE_DATA"
    ).all()
    assert len(sample_leads) == response.json()["created_count"]


def test_generate_creates_variety_of_tiers_and_statuses(client, db_session, sample_org):
    headers, _ = _make_super_admin_headers(db_session, sample_org)
    client.post("/sample-data/generate", headers=headers)

    sample_leads = db_session.query(Lead).filter(
        Lead.organization_id == sample_org.id, Lead.source_file == "SAMPLE_DATA"
    ).all()
    tiers = {l.tier for l in sample_leads}
    statuses = {l.status for l in sample_leads}
    assert len(tiers) > 1  # real variety, not all the same tier
    assert len(statuses) > 1


def test_generate_creates_replies_and_cadence_state(client, db_session, sample_org):
    headers, _ = _make_super_admin_headers(db_session, sample_org)
    client.post("/sample-data/generate", headers=headers)

    sample_lead_ids = [
        l.id for l in db_session.query(Lead).filter(
            Lead.organization_id == sample_org.id, Lead.source_file == "SAMPLE_DATA"
        ).all()
    ]
    replies = db_session.query(Reply).filter(Reply.lead_id.in_(sample_lead_ids)).all()
    cadences = db_session.query(CadenceState).filter(CadenceState.lead_id.in_(sample_lead_ids)).all()
    assert len(replies) > 0
    assert len(cadences) > 0


def test_clear_requires_super_admin(client, admin_auth_headers):
    response = client.delete("/sample-data/clear", headers=admin_auth_headers)
    assert response.status_code == 403


def test_clear_removes_only_sample_tagged_leads_never_real_ones(client, db_session, sample_org, sample_advisor):
    """
    THE CRITICAL SAFETY TEST: a real lead (no SAMPLE_DATA tag) must
    survive completely untouched even when sample data exists in the
    same organization and gets cleared.
    """
    real_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                      first_name="Real", last_name="Customer", phone="12145551234",
                      source_file="actual_restland_export_2026.xlsx")
    db_session.add(real_lead)
    db_session.commit()
    real_lead_id = real_lead.id

    headers, _ = _make_super_admin_headers(db_session, sample_org)
    client.post("/sample-data/generate", headers=headers)

    sample_count_before = db_session.query(Lead).filter(
        Lead.organization_id == sample_org.id, Lead.source_file == "SAMPLE_DATA"
    ).count()
    assert sample_count_before > 0

    response = client.delete("/sample-data/clear", headers=headers)
    assert response.status_code == 200
    assert response.json()["deleted_leads"] == sample_count_before

    # The real lead must still exist, completely untouched
    still_real = db_session.query(Lead).filter(Lead.id == real_lead_id).first()
    assert still_real is not None
    assert still_real.first_name == "Real"
    assert still_real.source_file == "actual_restland_export_2026.xlsx"

    # No sample-tagged leads should remain
    remaining_sample = db_session.query(Lead).filter(
        Lead.organization_id == sample_org.id, Lead.source_file == "SAMPLE_DATA"
    ).count()
    assert remaining_sample == 0


def test_clear_also_removes_associated_replies_and_cadence_state(client, db_session, sample_org):
    headers, _ = _make_super_admin_headers(db_session, sample_org)
    client.post("/sample-data/generate", headers=headers)

    sample_lead_ids_before = [
        l.id for l in db_session.query(Lead).filter(
            Lead.organization_id == sample_org.id, Lead.source_file == "SAMPLE_DATA"
        ).all()
    ]
    assert db_session.query(Reply).filter(Reply.lead_id.in_(sample_lead_ids_before)).count() > 0

    client.delete("/sample-data/clear", headers=headers)

    remaining_replies = db_session.query(Reply).filter(Reply.lead_id.in_(sample_lead_ids_before)).count()
    remaining_cadences = db_session.query(CadenceState).filter(CadenceState.lead_id.in_(sample_lead_ids_before)).count()
    assert remaining_replies == 0
    assert remaining_cadences == 0


def test_clear_with_no_sample_data_returns_zero_gracefully(client, db_session, sample_org):
    headers, _ = _make_super_admin_headers(db_session, sample_org)
    response = client.delete("/sample-data/clear", headers=headers)
    assert response.status_code == 200
    assert response.json()["deleted_leads"] == 0


def test_clear_does_not_affect_other_organizations_sample_data(client, db_session, sample_org):
    """An admin clearing their own org's sample data must not touch a different org's sample data."""
    other_org = Organization(name="Other Org", slug="other-org-sampledata", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_headers, other_admin = _make_super_admin_headers(db_session, other_org, email_suffix="other-org")
    client.post("/sample-data/generate", headers=other_headers)

    own_headers, _ = _make_super_admin_headers(db_session, sample_org, email_suffix="own-org")
    client.delete("/sample-data/clear", headers=own_headers)

    other_org_sample_count = db_session.query(Lead).filter(
        Lead.organization_id == other_org.id, Lead.source_file == "SAMPLE_DATA"
    ).count()
    assert other_org_sample_count > 0  # untouched by the other org's clear call
