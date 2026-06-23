"""
Tests for GET /admin/dashboard/revenue - the Master Control Board /
revenue analytics feature (step 6 of the original 8-step build plan,
never started until now).

Key thing under test, beyond the basic counts: this endpoint must NEVER
sum or parse sale_amount as currency. That field is documented (on the
LeadOutcome model itself) as a free-text sales note an advisor types in,
not a structured currency column - reporting a summed "total revenue"
number from it would look precise but be unreliable. This file asserts
the response shape only reports counts and surfaces sale_amount verbatim
per-sale, never aggregated.
"""

from datetime import datetime, timedelta, timezone

from app.models.models import Lead, LeadOutcome, LeadStatus, Organization, User
from app.services.auth_service import hash_password


def _lead(db_session, org, advisor, idx):
    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id if advisor else None,
        first_name=f"RevLead{idx}",
        last_name="Revenue",
        phone=f"12145551{idx:03d}",
        status=LeadStatus.NEW,
    )
    db_session.add(lead)
    db_session.flush()
    return lead


def _sale(db_session, lead, advisor, *, has_property=False, has_marker=False, has_memorial=False,
          has_funeral=False, sale_amount=None, sale_items=None, appointment_date=None):
    outcome = LeadOutcome(
        lead_id=lead.id,
        recorded_by_id=advisor.id,
        resulted_in_sale=True,
        has_cemetery_property=has_property,
        has_marker=has_marker,
        has_memorial=has_memorial,
        has_funeral_arrangement=has_funeral,
        sale_amount=sale_amount,
        sale_items=sale_items,
        appointment_date=appointment_date,
    )
    db_session.add(outcome)
    db_session.flush()
    return outcome


def test_revenue_dashboard_requires_admin(client, auth_headers):
    response = client.get("/admin/dashboard/revenue", headers=auth_headers)
    assert response.status_code == 403


def test_revenue_dashboard_reports_total_sale_count(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead1 = _lead(db_session, sample_org, sample_advisor, 1)
    lead2 = _lead(db_session, sample_org, sample_advisor, 2)
    _sale(db_session, lead1, sample_advisor, has_property=True)
    _sale(db_session, lead2, sample_advisor, has_marker=True)
    db_session.commit()

    response = client.get("/admin/dashboard/revenue", headers=admin_auth_headers)

    assert response.status_code == 200
    assert response.json()["total_sales"] == 2


def test_revenue_dashboard_never_sums_sale_amount_as_currency(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    """
    Regression-style guardrail test: even with sale_amount values that
    LOOK numeric, the response must not contain any summed/aggregated
    dollar total field. sale_amount must only appear verbatim, per-sale,
    in recent_sale_notes.
    """
    lead1 = _lead(db_session, sample_org, sample_advisor, 1)
    lead2 = _lead(db_session, sample_org, sample_advisor, 2)
    _sale(db_session, lead1, sample_advisor, sale_amount="$3,200")
    _sale(db_session, lead2, sample_advisor, sale_amount="2800")
    db_session.commit()

    response = client.get("/admin/dashboard/revenue", headers=admin_auth_headers)
    body = response.json()

    # No field anywhere in the top-level response should represent a
    # summed currency total - only total_sales (a count) is allowed.
    assert "total_revenue" not in body
    assert "total_amount" not in body
    assert "sum" not in str(body.keys()).lower()

    amounts_seen = {note["sale_amount"] for note in body["recent_sale_notes"]}
    assert amounts_seen == {"$3,200", "2800"}


def test_revenue_dashboard_breaks_down_by_advisor(client, db_session, sample_org, sample_advisor, second_advisor, admin_auth_headers):
    lead1 = _lead(db_session, sample_org, sample_advisor, 1)
    lead2 = _lead(db_session, sample_org, sample_advisor, 2)
    lead3 = _lead(db_session, sample_org, second_advisor, 3)
    _sale(db_session, lead1, sample_advisor)
    _sale(db_session, lead2, sample_advisor)
    _sale(db_session, lead3, second_advisor)
    db_session.commit()

    response = client.get("/admin/dashboard/revenue", headers=admin_auth_headers)
    by_advisor = {row["advisor_id"]: row["sale_count"] for row in response.json()["by_advisor"]}

    assert by_advisor[sample_advisor.id] == 2
    assert by_advisor[second_advisor.id] == 1


def test_revenue_dashboard_by_advisor_sorted_descending(client, db_session, sample_org, sample_advisor, second_advisor, admin_auth_headers):
    lead1 = _lead(db_session, sample_org, second_advisor, 1)
    lead2 = _lead(db_session, sample_org, sample_advisor, 2)
    lead3 = _lead(db_session, sample_org, sample_advisor, 3)
    lead4 = _lead(db_session, sample_org, sample_advisor, 4)
    _sale(db_session, lead1, second_advisor)
    _sale(db_session, lead2, sample_advisor)
    _sale(db_session, lead3, sample_advisor)
    _sale(db_session, lead4, sample_advisor)
    db_session.commit()

    response = client.get("/admin/dashboard/revenue", headers=admin_auth_headers)
    by_advisor = response.json()["by_advisor"]

    assert by_advisor[0]["advisor_id"] == sample_advisor.id
    assert by_advisor[0]["sale_count"] == 3
    assert by_advisor[1]["sale_count"] == 1


def test_revenue_dashboard_product_mix_counts_structured_fields(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead1 = _lead(db_session, sample_org, sample_advisor, 1)
    lead2 = _lead(db_session, sample_org, sample_advisor, 2)
    lead3 = _lead(db_session, sample_org, sample_advisor, 3)
    _sale(db_session, lead1, sample_advisor, has_property=True, has_marker=True)
    _sale(db_session, lead2, sample_advisor, has_property=True)
    _sale(db_session, lead3, sample_advisor, has_memorial=True, has_funeral=True)
    db_session.commit()

    response = client.get("/admin/dashboard/revenue", headers=admin_auth_headers)
    mix = response.json()["product_mix"]

    assert mix["cemetery_property"] == 2
    assert mix["marker"] == 1
    assert mix["memorial"] == 1
    assert mix["funeral_arrangement"] == 1


def test_revenue_dashboard_monthly_trend_groups_by_month(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    jan = datetime(2026, 1, 15, tzinfo=timezone.utc)
    jan2 = datetime(2026, 1, 20, tzinfo=timezone.utc)
    feb = datetime(2026, 2, 5, tzinfo=timezone.utc)

    lead1 = _lead(db_session, sample_org, sample_advisor, 1)
    lead2 = _lead(db_session, sample_org, sample_advisor, 2)
    lead3 = _lead(db_session, sample_org, sample_advisor, 3)
    _sale(db_session, lead1, sample_advisor, appointment_date=jan)
    _sale(db_session, lead2, sample_advisor, appointment_date=jan2)
    _sale(db_session, lead3, sample_advisor, appointment_date=feb)
    db_session.commit()

    response = client.get("/admin/dashboard/revenue", headers=admin_auth_headers)
    trend = {row["month"]: row["sale_count"] for row in response.json()["monthly_trend"]}

    assert trend["2026-01"] == 2
    assert trend["2026-02"] == 1


def test_revenue_dashboard_org_isolation(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    other_org = Organization(name="Other Revenue Org", slug="other-revenue-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-revenue@example.com",
                          password_hash=hash_password("x"), full_name="Other Advisor", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = Lead(organization_id=other_org.id, assigned_to_id=other_advisor.id,
                       first_name="Other", last_name="OrgLead", phone="12145559999")
    db_session.add(other_lead)
    db_session.flush()
    _sale(db_session, other_lead, other_advisor)

    own_lead = _lead(db_session, sample_org, sample_advisor, 1)
    _sale(db_session, own_lead, sample_advisor)
    db_session.commit()

    response = client.get("/admin/dashboard/revenue", headers=admin_auth_headers)

    assert response.json()["total_sales"] == 1


def test_revenue_dashboard_with_no_sales_returns_empty_shape_not_error(client, db_session, sample_org, admin_auth_headers):
    response = client.get("/admin/dashboard/revenue", headers=admin_auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total_sales"] == 0
    assert body["by_advisor"] == []
    assert body["monthly_trend"] == []
    assert body["recent_sale_notes"] == []
    assert body["product_mix"] == {
        "funeral_arrangement": 0, "cemetery_property": 0, "marker": 0, "memorial": 0,
    }


def test_revenue_dashboard_recent_sale_notes_capped_and_newest_first(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    for i in range(25):
        lead = _lead(db_session, sample_org, sample_advisor, i)
        outcome = _sale(db_session, lead, sample_advisor, sale_items=f"item-{i}")
        # Stagger created_at so ordering is deterministic and testable.
        outcome.created_at = datetime.now(timezone.utc) - timedelta(minutes=(25 - i))
    db_session.commit()

    response = client.get("/admin/dashboard/revenue", headers=admin_auth_headers)
    notes = response.json()["recent_sale_notes"]

    assert len(notes) == 20
    assert notes[0]["sale_items"] == "item-24"
