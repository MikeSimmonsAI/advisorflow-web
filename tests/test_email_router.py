from app.models.models import Lead, LeadStatus, Organization, User
from app.services.auth_service import create_access_token, hash_password


def _email_lead(db_session, org_id, advisor_id, first_name, last_name, email, phone=None):
    lead = Lead(
        organization_id=org_id,
        assigned_to_id=advisor_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        contact_channel="email_only",
        status=LeadStatus.NEW,
    )
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


def test_email_queue_search_filters_by_partial_name_and_email(client, db_session, sample_org, sample_advisor, auth_headers):
    alice = _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Alice",
        "Stone",
        "alice.stone@example.com",
        phone="12145550101",
    )
    bob = _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Robert",
        "Lane",
        "bob.match@example.com",
        phone=None,
    )
    _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Carol",
        "Ignore",
        "carol.ignore@example.com",
        phone="12145550103",
    )

    name_response = client.get("/email/queue?search=Ali", headers=auth_headers)
    assert name_response.status_code == 200
    name_rows = name_response.json()
    assert [row["id"] for row in name_rows] == [alice.id]
    assert name_rows[0]["phone"] == "12145550101"

    email_response = client.get("/email/queue?search=match", headers=auth_headers)
    assert email_response.status_code == 200
    email_rows = email_response.json()
    assert [row["id"] for row in email_rows] == [bob.id]
    assert email_rows[0]["phone"] is None


def test_email_queue_phone_is_present_or_null(client, db_session, sample_org, sample_advisor, auth_headers):
    with_phone = _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Phone",
        "Present",
        "phone.present@example.com",
        phone="19725550101",
    )
    without_phone = _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Phone",
        "Missing",
        "phone.missing@example.com",
        phone=None,
    )

    response = client.get("/email/queue", headers=auth_headers)
    assert response.status_code == 200
    rows = {row["id"]: row for row in response.json()}

    assert rows[with_phone.id]["phone"] == "19725550101"
    assert rows[without_phone.id]["phone"] is None


def test_email_queue_search_stays_scoped_to_logged_in_advisor_org(client, db_session, sample_org, sample_advisor, auth_headers):
    other_org = Organization(name="Other Cemetery", slug="other", plan="standard")
    db_session.add(other_org)
    db_session.commit()

    other_user = User(
        organization_id=other_org.id,
        email="other@example.com",
        password_hash=hash_password("TestPass123!"),
        full_name="Other Advisor",
        role="advisor",
    )
    db_session.add(other_user)
    db_session.commit()

    _email_lead(
        db_session,
        other_org.id,
        other_user.id,
        "Alice",
        "Foreign",
        "alice.foreign@example.com",
        phone="12145559999",
    )
    _email_lead(
        db_session,
        sample_org.id,
        sample_advisor.id,
        "Alice",
        "Local",
        "alice.local@example.com",
        phone="12145550000",
    )

    response = client.get("/email/queue?search=alice", headers=auth_headers)
    assert response.status_code == 200
    rows = response.json()

    assert len(rows) == 1
    assert rows[0]["email"] == "alice.local@example.com"
