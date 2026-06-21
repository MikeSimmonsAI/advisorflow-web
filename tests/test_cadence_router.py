"""
Router-level tests for app/routers/cadence_router.py
"""

from app.models.models import Lead, LeadStatus, CadenceStatus
from app.services.cadence_service import start_cadence


def test_cadence_active_requires_auth(client):
    response = client.get("/cadence/active")
    assert response.status_code == 401


def test_cadence_active_returns_empty_list_when_none_active(client, auth_headers):
    response = client.get("/cadence/active", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_cadence_active_returns_active_leads_for_current_advisor(client, auth_headers, db_session, sample_lead):
    start_cadence(db_session, sample_lead)

    response = client.get("/cadence/active", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["lead_id"] == sample_lead.id
    assert data[0]["lead_name"] == "Jane Doe"
    assert data[0]["current_touch_number"] == 0
    assert data[0]["total_touches"] == 9


def test_cadence_active_excludes_other_advisors_leads(client, auth_headers, db_session, sample_org, second_advisor):
    """An advisor should only see their own active cadences, not a colleague's."""
    other_lead = Lead(organization_id=sample_org.id, assigned_to_id=second_advisor.id,
                       first_name="Other", last_name="Advisor", phone="12145550000", status=LeadStatus.NEW)
    db_session.add(other_lead)
    db_session.commit()
    start_cadence(db_session, other_lead)

    response = client.get("/cadence/active", headers=auth_headers)
    assert response.json() == []


def test_cadence_active_excludes_stopped_cadences(client, auth_headers, db_session, sample_lead):
    from app.services.cadence_service import stop_cadence_for_lead
    start_cadence(db_session, sample_lead)
    stop_cadence_for_lead(db_session, sample_lead.id, CadenceStatus.STOPPED_REPLIED)

    response = client.get("/cadence/active", headers=auth_headers)
    assert response.json() == []


def test_cadence_summary_requires_auth(client):
    response = client.get("/cadence/summary")
    assert response.status_code == 401


def test_cadence_summary_returns_counts(client, auth_headers, db_session, sample_lead):
    start_cadence(db_session, sample_lead)
    response = client.get("/cadence/summary", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data.get("active") == 1
