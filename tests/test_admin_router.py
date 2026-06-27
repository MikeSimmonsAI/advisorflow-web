"""
Router-level tests for app/routers/admin_router.py

Unlike the service-level tests elsewhere, these go through the actual
HTTP layer (FastAPI TestClient) - real auth headers, real dependency
injection, real role checks. This catches a class of bug the service
tests can't: a typo in a route path, a missing auth dependency, or a
role check that's wired to the wrong field.
"""

from app.models.models import Lead, Message, Reply, User
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


# ---------------------------------------------------------------------------
# reply_count and org-wide totals - added so the redesigned Master
# Dashboard can compute a genuine response rate (replies / messages
# sent), instead of an unexplained percentage with no real backing
# calculation. The frontend owns the division; this just provides the
# two real, raw counts needed to do it honestly.
# ---------------------------------------------------------------------------

def test_admin_dashboard_includes_reply_count_per_advisor(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Reply", last_name="Count", phone="12145559990")
    db_session.add(lead)
    db_session.flush()
    db_session.add_all([
        Reply(lead_id=lead.id, body="Yes", is_hot=True),
        Reply(lead_id=lead.id, body="Ok", is_hot=False),
    ])
    db_session.commit()

    response = client.get("/admin/dashboard", headers=admin_auth_headers)
    data = response.json()
    advisor_entry = next(a for a in data["advisors"] if a["advisor_id"] == sample_advisor.id)

    assert advisor_entry["reply_count"] == 2
    assert advisor_entry["hot_replies"] == 1


def test_admin_dashboard_total_replies_and_messages_sum_correctly_across_advisors(client, admin_auth_headers, db_session, sample_org, sample_advisor, second_advisor):
    lead_a = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                  first_name="A", last_name="Lead", phone="12145559991")
    lead_b = Lead(organization_id=sample_org.id, assigned_to_id=second_advisor.id,
                  first_name="B", last_name="Lead", phone="12145559992")
    db_session.add_all([lead_a, lead_b])
    db_session.flush()
    db_session.add_all([
        Message(lead_id=lead_a.id, sender_id=sample_advisor.id, body="Hi"),
        Message(lead_id=lead_b.id, sender_id=second_advisor.id, body="Hi"),
        Message(lead_id=lead_b.id, sender_id=second_advisor.id, body="Following up"),
        Reply(lead_id=lead_a.id, body="Yes"),
        Reply(lead_id=lead_b.id, body="Ok"),
    ])
    db_session.commit()

    response = client.get("/admin/dashboard", headers=admin_auth_headers)
    data = response.json()

    assert data["total_messages_sent"] == 3
    assert data["total_replies"] == 2


def test_admin_dashboard_total_replies_matches_sum_of_per_advisor_reply_counts(client, admin_auth_headers, db_session, sample_org, sample_advisor, second_advisor):
    """The real correctness guarantee: the org-wide total must always agree with adding up every advisor's own row."""
    lead_a = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                  first_name="A", last_name="Sum", phone="12145559993")
    lead_b = Lead(organization_id=sample_org.id, assigned_to_id=second_advisor.id,
                  first_name="B", last_name="Sum", phone="12145559994")
    db_session.add_all([lead_a, lead_b])
    db_session.flush()
    db_session.add_all([Reply(lead_id=lead_a.id, body="x"), Reply(lead_id=lead_b.id, body="y"), Reply(lead_id=lead_b.id, body="z")])
    db_session.commit()

    response = client.get("/admin/dashboard", headers=admin_auth_headers)
    data = response.json()

    summed_from_advisors = sum(a["reply_count"] for a in data["advisors"])
    assert data["total_replies"] == summed_from_advisors


# ---------------------------------------------------------------------------
# GET /admin/dashboard/status-distribution - genuinely mutually-exclusive
# lead status counts for the Master Dashboard's "Lead distribution"
# donut. Different from dashboard_funnel, which is a sequential funnel
# where stages overlap (a booked lead is also counted in sent/replied).
# A donut needs non-overlapping categories - this gives that.
# ---------------------------------------------------------------------------

def test_status_distribution_requires_admin(client, auth_headers):
    response = client.get("/admin/dashboard/status-distribution", headers=auth_headers)
    assert response.status_code == 403


def test_status_distribution_counts_each_lead_exactly_once(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    from app.models.models import LeadStatus

    leads = [
        Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="A", last_name="New", phone="12145559980", status=LeadStatus.NEW),
        Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="B", last_name="Sent", phone="12145559981", status=LeadStatus.SENT),
        Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="C", last_name="Booked", phone="12145559982", status=LeadStatus.BOOKED),
    ]
    db_session.add_all(leads)
    db_session.commit()

    response = client.get("/admin/dashboard/status-distribution", headers=admin_auth_headers)

    assert response.status_code == 200
    data = response.json()
    by_status = {row["status"]: row["count"] for row in data}
    assert by_status["new"] == 1
    assert by_status["sent"] == 1
    assert by_status["booked"] == 1
    # The real correctness check: total across all buckets must equal
    # total leads created - no double-counting, no leads missing.
    assert sum(row["count"] for row in data) == 3


def test_status_distribution_is_org_wide_not_advisor_scoped(client, admin_auth_headers, db_session, sample_org, sample_advisor, second_advisor):
    from app.models.models import LeadStatus

    lead_a = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="A", last_name="One", phone="12145559983", status=LeadStatus.NEW)
    lead_b = Lead(organization_id=sample_org.id, assigned_to_id=second_advisor.id, first_name="B", last_name="Two", phone="12145559984", status=LeadStatus.NEW)
    db_session.add_all([lead_a, lead_b])
    db_session.commit()

    response = client.get("/admin/dashboard/status-distribution", headers=admin_auth_headers)
    data = response.json()
    by_status = {row["status"]: row["count"] for row in data}

    assert by_status["new"] == 2


def test_status_distribution_excludes_other_organizations(client, admin_auth_headers, db_session, sample_org):
    from app.models.models import LeadStatus, Organization, User
    from app.services.auth_service import hash_password

    other_org = Organization(name="Other Distribution Org", slug="other-distribution-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-distribution@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                       first_name="Cross", last_name="Org", phone="12145559985", status=LeadStatus.NEW)
    db_session.add(other_lead)
    db_session.commit()

    response = client.get("/admin/dashboard/status-distribution", headers=admin_auth_headers)
    data = response.json()
    by_status = {row["status"]: row["count"] for row in data}

    assert by_status["new"] == 0


# ---------------------------------------------------------------------------
# GET /admin/dashboard/hot-replies - real, recent hot-reply content
# org-wide for the Master Dashboard preview widget. Real reply text and
# lead names, not a count - uses the same HOT_REPLY_CLASSIFICATIONS
# definition as the rest of this dashboard for consistency.
# ---------------------------------------------------------------------------

def test_dashboard_hot_replies_requires_admin(client, auth_headers):
    response = client.get("/admin/dashboard/hot-replies", headers=auth_headers)
    assert response.status_code == 403


def test_dashboard_hot_replies_returns_real_content(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    from app.models.models import ReplyClassification
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Hot", last_name="Reply", phone="12145559990")
    db_session.add(lead)
    db_session.flush()
    db_session.add(Reply(lead_id=lead.id, body="Yes I'm interested!", classification=ReplyClassification.INTERESTED))
    db_session.commit()

    response = client.get("/admin/dashboard/hot-replies", headers=admin_auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["body"] == "Yes I'm interested!"
    assert data[0]["lead_name"] == "Hot Reply"
    assert data[0]["lead_id"] == lead.id


def test_dashboard_hot_replies_excludes_neutral_replies(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    from app.models.models import ReplyClassification
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Neutral", last_name="Reply", phone="12145559991")
    db_session.add(lead)
    db_session.flush()
    db_session.add(Reply(lead_id=lead.id, body="ok", classification=ReplyClassification.NEUTRAL))
    db_session.commit()

    response = client.get("/admin/dashboard/hot-replies", headers=admin_auth_headers)

    assert response.json() == []


def test_dashboard_hot_replies_respects_limit(client, admin_auth_headers, db_session, sample_org, sample_advisor):
    from app.models.models import ReplyClassification
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Many", last_name="Replies", phone="12145559992")
    db_session.add(lead)
    db_session.flush()
    for i in range(8):
        db_session.add(Reply(lead_id=lead.id, body=f"Reply {i}", classification=ReplyClassification.INTERESTED))
    db_session.commit()

    response = client.get("/admin/dashboard/hot-replies?limit=3", headers=admin_auth_headers)

    assert len(response.json()) == 3


def test_dashboard_hot_replies_scoped_to_own_org(client, admin_auth_headers, db_session, sample_org):
    from app.models.models import ReplyClassification, Organization, User
    from app.services.auth_service import hash_password

    other_org = Organization(name="Other Hot Reply Org", slug="other-hot-reply-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-hotreply@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                       first_name="Cross", last_name="Org", phone="12145559993")
    db_session.add(other_lead)
    db_session.flush()
    db_session.add(Reply(lead_id=other_lead.id, body="Cross org hot reply", classification=ReplyClassification.INTERESTED))
    db_session.commit()

    response = client.get("/admin/dashboard/hot-replies", headers=admin_auth_headers)

    assert response.json() == []
