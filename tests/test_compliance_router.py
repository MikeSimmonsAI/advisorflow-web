"""
Tests for app/routers/compliance_router.py

ORIGIN NOTE: the original test cases (org isolation, duplicate phone
handling, permanent-DNC-updates-matching-lead) were drafted by ChatGPT,
then corrected here to use this project's real test fixtures (client,
admin_auth_headers, sample_org, etc. from conftest.py) instead of a
standalone FastAPI app/fixture setup that assumed Integer IDs and a
different module layout. The test SCENARIOS are preserved; the
plumbing is fixed to match how every other test file in this project
actually works.
"""

from app.models.models import SuppressionEntry, SuppressionSource, Lead, Organization, User
from app.services.auth_service import hash_password


def test_list_suppression_requires_admin(client, auth_headers):
    response = client.get("/compliance/suppression-list", headers=auth_headers)
    assert response.status_code == 403


def test_org_isolation_for_list_and_delete(client, admin_auth_headers, db_session, sample_org):
    other_org = Organization(name="Other Org", slug="other-org-compliance", plan="trial")
    db_session.add(other_org)
    db_session.commit()

    own_entry = SuppressionEntry(organization_id=sample_org.id, phone="+12145550101",
                                  reason="Manual DNC", source=SuppressionSource.MANUAL)
    other_entry = SuppressionEntry(organization_id=other_org.id, phone="+19725550101",
                                    reason="Other org DNC", source=SuppressionSource.MANUAL)
    db_session.add_all([own_entry, other_entry])
    db_session.commit()

    response = client.get("/compliance/suppression-list", headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    phones = [row["phone"] for row in body["entries"]]

    assert body["stats"]["total"] == 1
    assert "+12145550101" in phones
    assert "+19725550101" not in phones

    # An admin in one org must not be able to delete another org's entry
    delete_other_org = client.delete(f"/compliance/suppression-list/{other_entry.id}", headers=admin_auth_headers)
    assert delete_other_org.status_code == 404

    db_session.refresh(other_entry)
    assert other_entry is not None  # untouched


def test_duplicate_phone_number_returns_existing_without_creating_duplicate(client, admin_auth_headers, db_session, sample_org):
    payload = {"phone": "(214) 555-0101", "reason": "Customer requested DNC", "source": "manual"}

    first = client.post("/compliance/suppression-list", json=payload, headers=admin_auth_headers)
    second = client.post("/compliance/suppression-list", json={**payload, "phone": "1-214-555-0101"}, headers=admin_auth_headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["phone"] == "+12145550101"

    rows = db_session.query(SuppressionEntry).filter_by(organization_id=sample_org.id, phone="+12145550101").all()
    assert len(rows) == 1


def test_permanent_dnc_updates_matching_lead_in_same_org_only(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    other_org = Organization(name="Other Org 2", slug="other-org-compliance-2", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="othercompliance@test.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()

    same_org_lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                          first_name="Same", last_name="Org", phone="+12145550101", status="new")
    other_org_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                           first_name="Other", last_name="Org", phone="+12145550101", status="new")
    db_session.add_all([same_org_lead, other_org_lead])
    db_session.commit()

    response = client.post("/compliance/permanent-dnc", json={
        "phone": "214-555-0101", "reason": "Permanent DNC requested",
    }, headers=admin_auth_headers)

    assert response.status_code == 201
    assert response.json()["phone"] == "+12145550101"
    assert response.json()["source"] == "manual"

    db_session.refresh(same_org_lead)
    db_session.refresh(other_org_lead)

    assert same_org_lead.status == "dnc"
    assert other_org_lead.status == "new"  # untouched - different org, even though phone matches


def test_permanent_dnc_works_even_with_no_matching_lead(client, admin_auth_headers):
    """Adding a permanent DNC for a number with no matching Lead yet should still succeed."""
    response = client.post("/compliance/permanent-dnc", json={
        "phone": "972-555-9999", "reason": "Preemptive block",
    }, headers=admin_auth_headers)
    assert response.status_code == 201


def test_normalize_phone_rejects_invalid_length(client, admin_auth_headers):
    response = client.post("/compliance/suppression-list", json={
        "phone": "12345", "reason": "test",
    }, headers=admin_auth_headers)
    assert response.status_code == 422


def test_delete_suppression_entry_succeeds_for_own_org(client, admin_auth_headers, db_session, sample_org):
    entry = SuppressionEntry(organization_id=sample_org.id, phone="+12145559999", reason="test")
    db_session.add(entry)
    db_session.commit()
    entry_id = entry.id

    response = client.delete(f"/compliance/suppression-list/{entry_id}", headers=admin_auth_headers)
    assert response.status_code == 204

    remaining = db_session.query(SuppressionEntry).filter(SuppressionEntry.id == entry_id).first()
    assert remaining is None
