"""
Tests for app/routers/reports_router.py - the dedicated Reports/Analytics
space Mike asked for, with the date-range filtering NO existing
analytics endpoint supported, plus a conversion trend over time and an
engagement-vs-conversion comparison per advisor ("conversions versus
engagements", his words).
"""

from datetime import datetime, timedelta, timezone

from app.models.models import (
    Lead, LeadStatus, Message, Reply, ReplyClassification,
    BookingLink, LeadOutcome, Organization, User,
)
from app.services.auth_service import hash_password


def _lead(db_session, org, advisor, idx, **kwargs):
    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id if advisor else None,
        first_name=f"Report{idx}",
        last_name="Test",
        phone=f"12145552{idx:03d}",
        status=kwargs.pop("status", LeadStatus.NEW),
        **kwargs,
    )
    db_session.add(lead)
    db_session.flush()
    return lead


def _message(db_session, lead, advisor, sent_at=None):
    msg = Message(lead_id=lead.id, sender_id=advisor.id, body="test message", sent_at=sent_at)
    db_session.add(msg)
    db_session.flush()
    return msg


def _reply(db_session, lead, received_at=None, classification=ReplyClassification.NEUTRAL, is_hot=False):
    reply = Reply(lead_id=lead.id, body="test reply", classification=classification, is_hot=is_hot, received_at=received_at)
    db_session.add(reply)
    db_session.flush()
    return reply


def _booking(db_session, lead, advisor, booked_time=None):
    booking = BookingLink(lead_id=lead.id, user_id=advisor.id, status="booked", booked_time=booked_time)
    db_session.add(booking)
    db_session.flush()
    return booking


def _sale(db_session, lead, advisor, created_at=None, **product_flags):
    outcome = LeadOutcome(lead_id=lead.id, recorded_by_id=advisor.id, resulted_in_sale=True, created_at=created_at, **product_flags)
    db_session.add(outcome)
    db_session.flush()
    return outcome


# --- Date range resolution ---

def test_reports_require_admin(client, auth_headers):
    response = client.get("/reports/conversion-trend", headers=auth_headers)
    assert response.status_code == 403


def test_conversion_trend_defaults_to_last_30_days_when_no_range_given(client, db_session, sample_org, admin_auth_headers):
    response = client.get("/reports/conversion-trend", headers=admin_auth_headers)

    assert response.status_code == 200
    body = response.json()
    start = datetime.strptime(body["start_date"], "%Y-%m-%d")
    end = datetime.strptime(body["end_date"], "%Y-%m-%d")
    assert (end - start).days == 30


def test_conversion_trend_rejects_invalid_date_format(client, admin_auth_headers):
    response = client.get("/reports/conversion-trend?start_date=not-a-date", headers=admin_auth_headers)
    assert response.status_code == 400


def test_conversion_trend_rejects_start_after_end(client, admin_auth_headers):
    response = client.get("/reports/conversion-trend?start_date=2026-06-20&end_date=2026-06-01", headers=admin_auth_headers)
    assert response.status_code == 400


def test_conversion_trend_respects_explicit_date_range(client, db_session, sample_org, admin_auth_headers):
    response = client.get("/reports/conversion-trend?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["start_date"] == "2026-01-01"
    assert body["end_date"] == "2026-01-31"


# --- Conversion trend ---

def test_conversion_trend_counts_replies_bookings_sales_on_correct_days(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead1 = _lead(db_session, sample_org, sample_advisor, 1)
    lead2 = _lead(db_session, sample_org, sample_advisor, 2)
    lead3 = _lead(db_session, sample_org, sample_advisor, 3)

    jan5 = datetime(2026, 1, 5, 10, 0, 0)
    jan10 = datetime(2026, 1, 10, 10, 0, 0)

    _reply(db_session, lead1, received_at=jan5, classification=ReplyClassification.INTERESTED, is_hot=True)
    _reply(db_session, lead2, received_at=jan5, classification=ReplyClassification.NEUTRAL)
    _booking(db_session, lead1, sample_advisor, booked_time=jan10)
    _sale(db_session, lead1, sample_advisor, created_at=jan10)
    db_session.commit()

    response = client.get("/reports/conversion-trend?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)
    body = response.json()

    by_date = {row["date"]: row for row in body["trend"]}
    assert by_date["2026-01-05"]["replies"] == 2
    assert by_date["2026-01-05"]["hot_replies"] == 1
    assert by_date["2026-01-10"]["booked"] == 1
    assert by_date["2026-01-10"]["sold"] == 1
    assert body["totals"]["replies"] == 2
    assert body["totals"]["hot_replies"] == 1
    assert body["totals"]["booked"] == 1
    assert body["totals"]["sold"] == 1


def test_conversion_trend_excludes_events_outside_date_range(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, 1)
    _reply(db_session, lead, received_at=datetime(2025, 12, 1))  # outside range
    db_session.commit()

    response = client.get("/reports/conversion-trend?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)
    body = response.json()

    assert body["totals"]["replies"] == 0


def test_conversion_trend_org_isolated(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    other_org = Organization(name="Other Reports Org", slug="other-reports-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-reports@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()
    other_lead = _lead(db_session, other_org, other_advisor, 1)
    _reply(db_session, other_lead, received_at=datetime(2026, 1, 5))
    db_session.commit()

    response = client.get("/reports/conversion-trend?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)
    body = response.json()

    assert body["totals"]["replies"] == 0


# --- Engagement vs conversion ---

def test_engagement_vs_conversion_computes_rates_per_advisor(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead1 = _lead(db_session, sample_org, sample_advisor, 1)
    lead2 = _lead(db_session, sample_org, sample_advisor, 2)
    jan5 = datetime(2026, 1, 5)

    _message(db_session, lead1, sample_advisor, sent_at=jan5)
    _message(db_session, lead2, sample_advisor, sent_at=jan5)
    _reply(db_session, lead1, received_at=jan5)
    _booking(db_session, lead1, sample_advisor, booked_time=jan5)
    db_session.commit()

    response = client.get("/reports/engagement-vs-conversion?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)
    body = response.json()

    row = next(r for r in body["advisors"] if r["advisor_id"] == sample_advisor.id)
    assert row["leads_messaged"] == 2
    assert row["replies"] == 1
    assert row["booked"] == 1
    assert row["engagement_rate"] == 50.0
    assert row["conversion_rate"] == 50.0


def test_engagement_vs_conversion_zero_messages_does_not_divide_by_zero(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    response = client.get("/reports/engagement-vs-conversion?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)

    assert response.status_code == 200
    row = next(r for r in response.json()["advisors"] if r["advisor_id"] == sample_advisor.id)
    assert row["leads_messaged"] == 0
    assert row["engagement_rate"] == 0.0
    assert row["conversion_rate"] == 0.0


def test_engagement_vs_conversion_counts_reply_even_if_outside_window_when_message_inside(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    """
    A message sent near the end of the window might not get a reply
    until after the window closes - that reply should still count
    toward this advisor's engagement for the lead they worked in-window.
    """
    lead = _lead(db_session, sample_org, sample_advisor, 1)
    _message(db_session, lead, sample_advisor, sent_at=datetime(2026, 1, 29))
    _reply(db_session, lead, received_at=datetime(2026, 2, 3))  # after window closes
    db_session.commit()

    response = client.get("/reports/engagement-vs-conversion?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)
    row = next(r for r in response.json()["advisors"] if r["advisor_id"] == sample_advisor.id)

    assert row["leads_messaged"] == 1
    assert row["replies"] == 1


def test_engagement_vs_conversion_org_isolated(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    other_org = Organization(name="Other EVC Org", slug="other-evc-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_advisor = User(organization_id=other_org.id, email="other-evc@example.com",
                          password_hash=hash_password("x"), full_name="Other", role="advisor")
    db_session.add(other_advisor)
    db_session.commit()

    response = client.get("/reports/engagement-vs-conversion?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)
    advisor_ids = {r["advisor_id"] for r in response.json()["advisors"]}

    assert other_advisor.id not in advisor_ids


# --- Revenue by period ---

def test_revenue_by_period_counts_sales_in_window_only(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    lead1 = _lead(db_session, sample_org, sample_advisor, 1)
    lead2 = _lead(db_session, sample_org, sample_advisor, 2)
    _sale(db_session, lead1, sample_advisor, created_at=datetime(2026, 1, 15), has_marker=True)
    _sale(db_session, lead2, sample_advisor, created_at=datetime(2026, 3, 1), has_cemetery_property=True)  # outside window
    db_session.commit()

    response = client.get("/reports/revenue-by-period?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)
    body = response.json()

    assert body["total_sales"] == 1
    assert body["product_mix"]["marker"] == 1
    assert body["product_mix"]["cemetery_property"] == 0


def test_revenue_by_period_never_reports_a_summed_dollar_total(client, db_session, sample_org, sample_advisor, admin_auth_headers):
    """Same guardrail as test_admin_revenue_dashboard.py - sale_amount must never be parsed/summed."""
    lead = _lead(db_session, sample_org, sample_advisor, 1)
    _sale(db_session, lead, sample_advisor, created_at=datetime(2026, 1, 15), sale_amount="$5,000")
    db_session.commit()

    response = client.get("/reports/revenue-by-period?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)
    body = response.json()

    assert "total_revenue" not in body
    assert "total_amount" not in body
    assert "sum" not in str(body.keys()).lower()


def test_revenue_by_period_by_advisor_breakdown(client, db_session, sample_org, sample_advisor, second_advisor, admin_auth_headers):
    lead1 = _lead(db_session, sample_org, sample_advisor, 1)
    lead2 = _lead(db_session, sample_org, second_advisor, 2)
    _sale(db_session, lead1, sample_advisor, created_at=datetime(2026, 1, 10))
    _sale(db_session, lead2, second_advisor, created_at=datetime(2026, 1, 12))
    db_session.commit()

    response = client.get("/reports/revenue-by-period?start_date=2026-01-01&end_date=2026-01-31", headers=admin_auth_headers)
    by_advisor = {row["advisor_id"]: row["sale_count"] for row in response.json()["by_advisor"]}

    assert by_advisor[sample_advisor.id] == 1
    assert by_advisor[second_advisor.id] == 1
