"""
Router-level tests for app/routers/admin_router.py

Unlike the service-level tests elsewhere, these go through the actual
HTTP layer (FastAPI TestClient) - real auth headers, real dependency
injection, real role checks. This catches a class of bug the service
tests can't: a typo in a route path, a missing auth dependency, or a
role check that's wired to the wrong field.
"""

from app.models.models import Lead, Message, User
from app.services.auth_service import hash_password


def test_admin_dashboard_requires_auth(client):
    response = client.get("/admin/dashboard")
    assert response.status_code == 401


def test_admin_dashboard_rejects_regular_advisor(client, auth_headers):
    """sample_advisor has role='advisor', not org_admin/super_admin - must be rejected."""
    response = client.get("/admin/dashboard", headers=auth_headers)
    assert response.status_code == 403


def test_admin_dashboard_accepts_org_admin(client, admin_auth_headers):
    response = client.get("/admin/dashboard", headers=admin_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "total_leads" in data
    assert "advisors" in data


def test_admin_dashboard_shows_correct_per_advisor_breakdown(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Test", last_name="Lead", phone="12145559999")
    db_session.add(lead)
    db_session.commit()

    response = client.get("/admin/dashboard", headers=admin_auth_headers)
    data = response.json()
    advisor_entry = next((a for a in data["advisors"] if a["advisor_id"] == sample_advisor.id), None)
    assert advisor_entry is not None
    assert advisor_entry["leads_owned"] == 1


def test_admin_leads_endpoint_requires_admin_role(client, auth_headers):
    response = client.get("/admin/leads", headers=auth_headers)
    assert response.status_code == 403


def test_admin_leads_includes_advisor_name_not_just_id(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    """
    Real bug caught during enhancement work: this endpoint originally
    returned the raw Lead ORM object, which only has assigned_to_id (a
    bare UUID) - meaningless on a dashboard. Confirms the fix actually
    joins in the advisor's real name.
    """
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Test", last_name="Lead", phone="12145559999")
    db_session.add(lead)
    db_session.commit()

    response = client.get("/admin/leads", headers=admin_auth_headers)
    assert response.status_code == 200
    data = response.json()
    matching = next((l for l in data if l["id"] == lead.id), None)
    assert matching is not None
    assert matching["assigned_to_name"] == "Advisor One"  # sample_advisor.full_name


def test_admin_leads_shows_unassigned_for_leads_with_no_advisor(client, admin_auth_headers, db_session, sample_org):
    unassigned_lead = Lead(organization_id=sample_org.id, assigned_to_id=None,
                            first_name="No", last_name="Advisor", phone="12145550001")
    db_session.add(unassigned_lead)
    db_session.commit()

    response = client.get("/admin/leads", headers=admin_auth_headers)
    data = response.json()
    matching = next((l for l in data if l["id"] == unassigned_lead.id), None)
    assert matching["assigned_to_name"] == "Unassigned"


def test_admin_dashboard_only_shows_own_organization(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    """
    Critical org-isolation check: an admin in Restland must never see
    leads/data belonging to a different organization, even if that org
    exists in the same database (relevant once North Star Memorial Group
    or other customers share this platform).
    """
    from app.models.models import Organization
    other_org = Organization(name="Other Org", slug="other-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()

    other_advisor = User(organization_id=other_org.id, email="other@otherorg.com",
                          password_hash=hash_password("x"), full_name="Other Advisor", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()

    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                       first_name="Should", last_name="NotAppear", phone="19999999999")
    db_session.add(other_lead)
    db_session.commit()

    response = client.get("/admin/dashboard", headers=admin_auth_headers)
    data = response.json()
    assert data["organization_id"] == sample_org.id
    advisor_ids_shown = [a["advisor_id"] for a in data["advisors"]]
    assert other_advisor.id not in advisor_ids_shown
