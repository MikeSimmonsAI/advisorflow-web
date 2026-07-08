"""
Tests for app/routers/outcomes_router.py - the "what does this family
have/not have" tracker Mike specifically asked for.
"""

from app.models.models import LeadOutcome, Organization, User
from app.services.auth_service import hash_password, create_access_token


def test_record_outcome_requires_auth(client):
    response = client.post("/outcomes/", json={"lead_id": "x"})
    assert response.status_code == 401


def test_record_outcome_creates_entry(client, auth_headers, sample_lead):
    response = client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True,
        "has_cemetery_property": True,
        "has_marker": False,
        "has_memorial": False,
        "resulted_in_sale": True,
        "sale_items": "Cemetery plot, opening/closing service",
    }, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["has_marker"] is False
    assert data["resulted_in_sale"] is True


def test_record_outcome_distinguishes_unknown_from_confirmed_no(client, auth_headers, db_session, sample_lead):
    """
    Real distinction Mike needs: has_marker=None (never asked) must be
    different from has_marker=False (confirmed they don't have one).
    """
    response = client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_marker": False,
        # has_memorial deliberately omitted - should come back as None, not False
    }, headers=auth_headers)
    data = response.json()
    assert data["has_marker"] is False
    assert data["has_memorial"] is None


def test_record_outcome_validates_open_closed_status(client, auth_headers, sample_lead):
    response = client.post("/outcomes/", json={
        "lead_id": sample_lead.id, "has_open_closed_status": "sideways",
    }, headers=auth_headers)
    assert response.status_code == 400


def test_record_outcome_rejects_lead_from_other_org(client, auth_headers, db_session):
    other_org = Organization(name="Other", slug="other-outcome-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="otheroutcome@test.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    from app.models.models import Lead
    foreign_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                         first_name="Foreign", last_name="Lead", phone="12145559999")
    db_session.add(foreign_lead)
    db_session.commit()

    response = client.post("/outcomes/", json={"lead_id": foreign_lead.id}, headers=auth_headers)
    assert response.status_code == 404


def test_list_outcomes_returns_most_recent_first(client, auth_headers, db_session, sample_lead, sample_advisor):
    """
    NOTE: created_at has only second-level precision on some databases
    (confirmed earlier this session for BookingLink, same root cause
    here) - two records created within the same second get identical
    timestamps, making "most recent" briefly ambiguous at the database
    level. Not a practical issue (a human filling out and submitting
    two separate appointment outcomes takes far longer than a second),
    but this test uses explicit, distinct timestamps to avoid being
    flaky rather than relying on real-world insertion speed.
    """
    from datetime import datetime, timezone, timedelta
    older = LeadOutcome(lead_id=sample_lead.id, recorded_by_id=sample_advisor.id, has_marker=False)
    db_session.add(older)
    db_session.commit()
    older.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    response = client.post("/outcomes/", json={
        "lead_id": sample_lead.id, "has_marker": True, "notes": "Bought marker on second visit",
    }, headers=auth_headers)
    assert response.status_code == 200

    list_response = client.get(f"/outcomes/lead/{sample_lead.id}", headers=auth_headers)
    data = list_response.json()
    assert len(data) == 2
    assert data[0]["notes"] == "Bought marker on second visit"  # most recent first


def test_latest_gaps_returns_no_data_when_nothing_recorded(client, auth_headers, sample_lead):
    response = client.get(f"/outcomes/lead/{sample_lead.id}/latest-gaps", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["has_outcome_data"] is False


def test_latest_gaps_lists_confirmed_missing_items(client, auth_headers, sample_lead):
    client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True,
        "has_cemetery_property": True,
        "has_marker": False,
        "has_memorial": False,
    }, headers=auth_headers)

    response = client.get(f"/outcomes/lead/{sample_lead.id}/latest-gaps", headers=auth_headers)
    data = response.json()
    assert data["has_outcome_data"] is True
    assert "marker" in data["gaps"]
    assert "memorial" in data["gaps"]
    assert "funeral_arrangement" not in data["gaps"]  # they DO have this, not a gap


def test_latest_gaps_excludes_unknown_fields_from_gaps_list(client, auth_headers, sample_lead):
    """A None (never asked) field should NOT appear in the gaps list - only confirmed False does."""
    client.post("/outcomes/", json={
        "lead_id": sample_lead.id, "has_marker": False,
        # has_memorial omitted -> None, should not appear as a "gap"
    }, headers=auth_headers)

    response = client.get(f"/outcomes/lead/{sample_lead.id}/latest-gaps", headers=auth_headers)
    data = response.json()
    assert "marker" in data["gaps"]
    assert "memorial" not in data["gaps"]


def test_latest_gaps_uses_most_recent_outcome_only(client, auth_headers, db_session, sample_lead, sample_advisor):
    """
    If an earlier visit had a gap that's since been filled, the latest
    record should reflect that, not the old one. Uses an explicit older
    timestamp for the same reason as test_list_outcomes_returns_most_recent_first above.
    """
    from datetime import datetime, timezone, timedelta
    older = LeadOutcome(lead_id=sample_lead.id, recorded_by_id=sample_advisor.id, has_marker=False)
    db_session.add(older)
    db_session.commit()
    older.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.commit()

    client.post("/outcomes/", json={"lead_id": sample_lead.id, "has_marker": True}, headers=auth_headers)

    response = client.get(f"/outcomes/lead/{sample_lead.id}/latest-gaps", headers=auth_headers)
    assert "marker" not in response.json()["gaps"]
