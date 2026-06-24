"""
Tests for GET /admin/dashboard/team-activity - the activity board Mike
asked for directly: one screen showing every advisor's last login and
last real action, so he can see who's gone quiet without clicking into
each person's individual detail page.
"""

from datetime import datetime, timedelta, timezone

from app.models.models import Lead, LeadOutcome, LeadStatus, Message, Organization, User
from app.services.auth_service import hash_password


def _lead(db_session, org, advisor, idx):
    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id,
        first_name=f"Activity{idx}",
        last_name="Board",
        phone=f"12145553{idx:03d}",
        status=LeadStatus.NEW,
    )
    db_session.add(lead)
    db_session.flush()
    return lead


def test_team_activity_requires_admin(client, auth_headers):
    response = client.get("/admin/dashboard/team-activity", headers=auth_headers)
    assert response.status_code == 403


def test_team_activity_includes_last_login(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    login_time = datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc)
    sample_advisor.last_login_at = login_time
    db_session.commit()

    response = client.get("/admin/dashboard/team-activity", headers=admin_auth_headers)

    assert response.status_code == 200
    row = next(r for r in response.json()["advisors"] if r["advisor_id"] == sample_advisor.id)
    assert row["last_login_at"] is not None


def test_team_activity_last_action_uses_most_recent_message(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, 1)
    older = Message(lead_id=lead.id, sender_id=sample_advisor.id, body="older",
                     sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    newer = Message(lead_id=lead.id, sender_id=sample_advisor.id, body="newer",
                     sent_at=datetime(2026, 1, 10, tzinfo=timezone.utc))
    db_session.add_all([older, newer])
    db_session.commit()

    response = client.get("/admin/dashboard/team-activity", headers=admin_auth_headers)
    row = next(r for r in response.json()["advisors"] if r["advisor_id"] == sample_advisor.id)

    assert row["last_action_type"] == "sent_message"
    assert row["last_action_at"].startswith("2026-01-10")


def test_team_activity_last_action_prefers_more_recent_outcome_over_older_message(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, 2)
    message = Message(lead_id=lead.id, sender_id=sample_advisor.id, body="msg",
                       sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    outcome = LeadOutcome(lead_id=lead.id, recorded_by_id=sample_advisor.id, resulted_in_sale=False,
                           created_at=datetime(2026, 1, 20, tzinfo=timezone.utc))
    db_session.add_all([message, outcome])
    db_session.commit()

    response = client.get("/admin/dashboard/team-activity", headers=admin_auth_headers)
    row = next(r for r in response.json()["advisors"] if r["advisor_id"] == sample_advisor.id)

    assert row["last_action_type"] == "recorded_outcome"
    assert row["last_action_at"].startswith("2026-01-20")


def test_team_activity_advisor_with_no_activity_returns_nulls_not_error(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    response = client.get("/admin/dashboard/team-activity", headers=admin_auth_headers)

    assert response.status_code == 200
    row = next(r for r in response.json()["advisors"] if r["advisor_id"] == sample_advisor.id)
    assert row["last_action_at"] is None
    assert row["last_action_type"] is None
    assert row["last_login_at"] is None


def test_team_activity_org_isolated(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    other_org = Organization(name="Other Activity Org", slug="other-activity-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-activity@example.com",
                          password_hash=hash_password("x"), full_name="Other Advisor", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()

    response = client.get("/admin/dashboard/team-activity", headers=admin_auth_headers)
    advisor_ids = {r["advisor_id"] for r in response.json()["advisors"]}

    assert other_advisor.id not in advisor_ids


def test_team_activity_includes_org_admins_not_just_advisors(client, db_session, sample_org, admin_auth_headers):
    """An org_admin who also works leads should still show up on the activity board, not just plain advisors."""
    response = client.get("/admin/dashboard/team-activity", headers=admin_auth_headers)

    advisor_roles = {r["role"] for r in response.json()["advisors"]}
    assert "org_admin" in advisor_roles


def test_team_activity_each_advisor_appears_exactly_once(client, db_session, sample_org, sample_advisor, second_advisor, admin_auth_headers):
    lead1 = _lead(db_session, sample_org, sample_advisor, 3)
    lead2 = _lead(db_session, sample_org, second_advisor, 4)
    db_session.add_all([
        Message(lead_id=lead1.id, sender_id=sample_advisor.id, body="a", sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        Message(lead_id=lead1.id, sender_id=sample_advisor.id, body="b", sent_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
        Message(lead_id=lead2.id, sender_id=second_advisor.id, body="c", sent_at=datetime(2026, 1, 3, tzinfo=timezone.utc)),
    ])
    db_session.commit()

    response = client.get("/admin/dashboard/team-activity", headers=admin_auth_headers)
    advisor_ids = [r["advisor_id"] for r in response.json()["advisors"]]

    assert advisor_ids.count(sample_advisor.id) == 1
    assert advisor_ids.count(second_advisor.id) == 1
