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
    Real distinction Mike needs: has_memorial=None (never asked) must be
    different from has_marker=False (confirmed they don't have one).
    has_memorial is deliberately the one field left at None here, since
    it's not in MANDATORY_OUTCOME_FIELDS... wait, it IS mandatory now -
    see test_record_outcome_requires_all_mandatory_fields below for the
    actual validation behavior. This test confirms the underlying
    None-vs-False storage distinction still holds for the OPTIONAL
    context fields (e.g. is_veteran), which is where "never asked" vs
    "confirmed no" genuinely still applies post-validation.
    """
    response = client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True,
        "has_cemetery_property": True,
        "has_marker": False,
        "has_memorial": True,
        # is_veteran deliberately omitted - should come back as None, not False
    }, headers=auth_headers)
    data = response.json()
    assert data["has_marker"] is False
    assert data["is_veteran"] is None


def test_record_outcome_validates_open_closed_status(client, auth_headers, sample_lead):
    response = client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True, "has_cemetery_property": True, "has_marker": True, "has_memorial": True,
        "has_open_closed_status": "sideways",
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
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True, "has_cemetery_property": True, "has_marker": True, "has_memorial": True,
        "notes": "Bought marker on second visit",
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
    """A confirmed True (has it) should NOT appear in the gaps list - only confirmed False does."""
    client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True, "has_cemetery_property": True,
        "has_marker": False, "has_memorial": True,
    }, headers=auth_headers)

    response = client.get(f"/outcomes/lead/{sample_lead.id}/latest-gaps", headers=auth_headers)
    data = response.json()
    assert "marker" in data["gaps"]
    assert "memorial" not in data["gaps"]


def test_latest_gaps_treats_optional_field_left_unanswered_as_not_a_gap(client, auth_headers, sample_lead):
    """is_veteran (an optional context field) left at None must never appear in the sellable-items gaps list at all."""
    client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True, "has_cemetery_property": True, "has_marker": True, "has_memorial": True,
        # is_veteran deliberately omitted
    }, headers=auth_headers)

    response = client.get(f"/outcomes/lead/{sample_lead.id}/latest-gaps", headers=auth_headers)
    assert response.json()["gaps"] == []


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

    client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True, "has_cemetery_property": True, "has_marker": True, "has_memorial": True,
    }, headers=auth_headers)

    response = client.get(f"/outcomes/lead/{sample_lead.id}/latest-gaps", headers=auth_headers)
    assert "marker" not in response.json()["gaps"]


# ---------------------------------------------------------------------------
# Mandatory outcome fields - the actual fix for Mike's explicit complaint:
# "I do not want users clicking through without actually selecting what
# happened." Previously every has_X field defaulted to None and the
# endpoint accepted that silently. The four directly-sellable items
# (funeral arrangement, cemetery property, marker, memorial) must now be
# explicitly true/false. The new context fields (preneed planning,
# insurance/funding, veteran status) are deliberately left OPTIONAL,
# since they shape which conversation to have next rather than being a
# missed sale themselves - forcing a guess there would hurt data quality.
# ---------------------------------------------------------------------------

def test_record_outcome_rejects_when_mandatory_fields_missing(client, auth_headers, sample_lead):
    response = client.post("/outcomes/", json={"lead_id": sample_lead.id}, headers=auth_headers)

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "funeral arrangement" in detail
    assert "cemetery property" in detail
    assert "marker" in detail
    assert "memorial" in detail


def test_record_outcome_rejects_when_partially_missing(client, auth_headers, sample_lead):
    """Even ONE missing mandatory field should block the save, not just all-missing."""
    response = client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True, "has_cemetery_property": True, "has_marker": True,
        # has_memorial deliberately omitted
    }, headers=auth_headers)

    assert response.status_code == 400
    assert "memorial" in response.json()["detail"]
    assert "funeral arrangement" not in response.json()["detail"]


def test_record_outcome_succeeds_with_all_mandatory_fields_set_to_false(client, auth_headers, sample_lead):
    """Confirmed-false is a valid, complete answer - this must NOT be confused with 'missing'."""
    response = client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": False, "has_cemetery_property": False, "has_marker": False, "has_memorial": False,
    }, headers=auth_headers)

    assert response.status_code == 200


def test_record_outcome_accepts_optional_context_fields(client, auth_headers, sample_lead):
    response = client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True, "has_cemetery_property": True, "has_marker": True, "has_memorial": True,
        "has_preneed_planning": False, "has_insurance_funding": True, "is_veteran": True,
        "next_step": "Schedule family meeting next Tuesday",
    }, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["has_preneed_planning"] is False
    assert body["has_insurance_funding"] is True
    assert body["is_veteran"] is True
    assert body["next_step"] == "Schedule family meeting next Tuesday"


def test_record_outcome_optional_context_fields_default_to_none_not_required(client, auth_headers, sample_lead):
    """The optional fields must NOT trigger the same mandatory-field rejection as the four sellable items."""
    response = client.post("/outcomes/", json={
        "lead_id": sample_lead.id,
        "has_funeral_arrangement": True, "has_cemetery_property": True, "has_marker": True, "has_memorial": True,
        # has_preneed_planning, has_insurance_funding, is_veteran, next_step all omitted
    }, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["has_preneed_planning"] is None
    assert body["has_insurance_funding"] is None
    assert body["is_veteran"] is None
    assert body["next_step"] is None
