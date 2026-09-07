from app.models.models import (
    Lead,
    LeadOutcome,
    LeadStatus,
    Message,
    Organization,
    Reply,
    ReplyClassification,
    User,
)
from app.services.auth_service import hash_password


def _lead(db_session, org, advisor, idx, status=LeadStatus.NEW, is_duplicate=False):
    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id if advisor else None,
        first_name=f"Lead{idx}",
        last_name="Metrics",
        phone=f"12145550{idx:03d}",
        status=status,
        is_duplicate=is_duplicate,
    )
    db_session.add(lead)
    db_session.flush()
    return lead


def _message(db_session, lead, advisor, idx):
    msg = Message(
        lead_id=lead.id,
        sender_id=advisor.id,
        body=f"Outbound message {idx}",
        twilio_sid=f"SM{idx:04d}",
        twilio_status="sent",
    )
    db_session.add(msg)
    db_session.flush()
    return msg


def _reply(db_session, lead, idx, classification=ReplyClassification.NEUTRAL, is_hot=False):
    reply = Reply(
        lead_id=lead.id,
        body=f"Inbound reply {idx}",
        twilio_sid=f"RM{idx:04d}",
        classification=classification,
        is_hot=is_hot,
    )
    db_session.add(reply)
    db_session.flush()
    return reply


def _sale(db_session, lead, advisor):
    outcome = LeadOutcome(
        lead_id=lead.id,
        recorded_by_id=advisor.id,
        resulted_in_sale=True,
        notes="Closed from metrics fixture",
    )
    db_session.add(outcome)
    db_session.flush()
    return outcome


def _seed_exact_ten_leads(db_session, sample_org, sample_advisor, second_advisor):
    """
    Exactly 10 lead ROWS in sample_org, 8 of which are real.

    Advisor One owns 5 real + 1 duplicate:
    - 4 messages, 3 replies, 2 hot/interested replies, 2 booked, 1 dnc, 1 duplicate.

    Advisor Two owns 3 real + 1 duplicate:
    - 2 messages, 1 reply, 1 hot/interested reply, 1 booked, 1 dnc, 1 duplicate.

    The two duplicate rows are deliberately NOT part of leads_owned: every count
    query behind /admin/dashboard/metrics filters `Lead.is_duplicate == False`, and
    duplicates are reported separately as duplicate_leads_prevented. Rate
    denominators therefore use the real 5 / 3 / 8, not the raw row counts.

    /admin/dashboard/funnel is a different report and deliberately does NOT
    exclude duplicates, so its total_leads is still 10.

    Org totals (metrics):
    - 8 real leads owned (+2 duplicates prevented), 6 sent, 4 replied,
      3 hot/interested, 3 booked, 2 sold.
    """
    a1 = _lead(db_session, sample_org, sample_advisor, 1, status=LeadStatus.BOOKED)
    a2 = _lead(db_session, sample_org, sample_advisor, 2, status=LeadStatus.BOOKED)
    a3 = _lead(db_session, sample_org, sample_advisor, 3, status=LeadStatus.REPLIED)
    a4 = _lead(db_session, sample_org, sample_advisor, 4, status=LeadStatus.SENT)
    a5 = _lead(db_session, sample_org, sample_advisor, 5, status=LeadStatus.DNC)
    a6 = _lead(db_session, sample_org, sample_advisor, 6, status=LeadStatus.NEW, is_duplicate=True)

    b1 = _lead(db_session, sample_org, second_advisor, 7, status=LeadStatus.BOOKED)
    b2 = _lead(db_session, sample_org, second_advisor, 8, status=LeadStatus.SENT)
    b3 = _lead(db_session, sample_org, second_advisor, 9, status=LeadStatus.DNC)
    b4 = _lead(db_session, sample_org, second_advisor, 10, status=LeadStatus.NEW, is_duplicate=True)

    for idx, lead in enumerate([a1, a2, a3, a4], start=1):
        _message(db_session, lead, sample_advisor, idx)
    for idx, lead in enumerate([b1, b2], start=5):
        _message(db_session, lead, second_advisor, idx)

    _reply(db_session, a1, 1, ReplyClassification.INTERESTED)
    _reply(db_session, a2, 2, ReplyClassification.CALLBACK)
    _reply(db_session, a3, 3, ReplyClassification.NEUTRAL)
    _reply(db_session, b1, 4, ReplyClassification.INTERESTED)

    _sale(db_session, a1, sample_advisor)
    _sale(db_session, b1, second_advisor)

    db_session.commit()


def _row_by_name(rows, name):
    return next(row for row in rows if row["advisor_name"] == name)


def test_dashboard_metrics_exact_rates_with_ten_leads(
    client, admin_auth_headers, db_session, sample_org, sample_advisor, second_advisor
):
    _seed_exact_ten_leads(db_session, sample_org, sample_advisor, second_advisor)

    response = client.get("/admin/dashboard/metrics", headers=admin_auth_headers)
    assert response.status_code == 200
    data = response.json()

    one = _row_by_name(data["advisors"], "Advisor One")
    assert one["leads_owned"] == 5  # 6 rows, 1 of them a duplicate
    assert one["messages_sent"] == 4
    assert one["replies"] == 3
    assert one["hot_replies"] == 2
    assert one["booked_leads"] == 2
    assert one["dnc_leads"] == 1
    assert one["duplicate_leads_prevented"] == 1
    assert one["reply_rate"] == 75.0        # 3 / 4
    assert one["hot_reply_rate"] == 50.0    # 2 / 4
    assert one["booking_rate"] == 40.0      # 2 / 5
    assert one["dnc_rate"] == 20.0          # 1 / 5

    two = _row_by_name(data["advisors"], "Advisor Two")
    assert two["leads_owned"] == 3  # 4 rows, 1 of them a duplicate
    assert two["messages_sent"] == 2
    assert two["replies"] == 1
    assert two["hot_replies"] == 1
    assert two["booked_leads"] == 1
    assert two["dnc_leads"] == 1
    assert two["duplicate_leads_prevented"] == 1
    assert two["reply_rate"] == 50.0        # 1 / 2
    assert two["hot_reply_rate"] == 50.0    # 1 / 2
    assert two["booking_rate"] == 33.33     # 1 / 3, rounded to 2dp
    assert two["dnc_rate"] == 33.33         # 1 / 3, rounded to 2dp

    totals = data["totals"]
    assert totals["leads_owned"] == 8       # 10 rows, 2 of them duplicates
    assert totals["messages_sent"] == 6
    assert totals["replies"] == 4
    assert totals["hot_replies"] == 3
    assert totals["booked_leads"] == 3
    assert totals["dnc_leads"] == 2
    assert totals["duplicate_leads_prevented"] == 2
    assert totals["reply_rate"] == 66.67    # 4 / 6, rounded to 2dp
    assert totals["hot_reply_rate"] == 50.0  # 3 / 6
    assert totals["booking_rate"] == 37.5   # 3 / 8
    assert totals["dnc_rate"] == 25.0       # 2 / 8

    # The duplicates are excluded from leads_owned, not lost: every row is still
    # accounted for across the two buckets.
    assert totals["leads_owned"] + totals["duplicate_leads_prevented"] == 10


def test_dashboard_funnel_exact_counts_with_ten_leads(
    client, admin_auth_headers, db_session, sample_org, sample_advisor, second_advisor
):
    _seed_exact_ten_leads(db_session, sample_org, sample_advisor, second_advisor)

    response = client.get("/admin/dashboard/funnel", headers=admin_auth_headers)
    assert response.status_code == 200
    data = response.json()

    assert data["total_leads"] == 10
    assert data["sent"] == 6
    assert data["replied"] == 4
    assert data["hot_interested"] == 3
    assert data["booked"] == 3
    assert data["sold"] == 2
    assert [stage["count"] for stage in data["stages"]] == [10, 6, 4, 3, 3, 2]


def test_dashboard_metrics_org_isolation(
    client, admin_auth_headers, db_session, sample_org, sample_advisor, second_advisor
):
    _seed_exact_ten_leads(db_session, sample_org, sample_advisor, second_advisor)

    other_org = Organization(name="Other Org", slug="other-metrics", plan="trial")
    db_session.add(other_org)
    db_session.flush()
    other_advisor = User(
        organization_id=other_org.id,
        email="advisor@other.com",
        password_hash=hash_password("OtherPass123!"),
        full_name="Other Advisor",
        role="advisor",
        must_change_password=False,
    )
    db_session.add(other_advisor)
    db_session.flush()
    other_lead = _lead(db_session, other_org, other_advisor, 99, status=LeadStatus.BOOKED)
    _message(db_session, other_lead, other_advisor, 99)
    _reply(db_session, other_lead, 99, ReplyClassification.INTERESTED)
    _sale(db_session, other_lead, other_advisor)
    db_session.commit()

    # The other org contributed 1 booked lead, 1 message, 1 interested reply and
    # 1 sale. None of it may appear in these numbers, and none of its people may
    # appear in the advisor list.
    metrics = client.get("/admin/dashboard/metrics", headers=admin_auth_headers).json()
    assert metrics["organization_id"] == sample_org.id
    assert metrics["totals"]["leads_owned"] == 8  # sample_org's 10 rows less its 2 duplicates
    assert metrics["totals"]["duplicate_leads_prevented"] == 2  # not 3 - other org's row is real
    assert metrics["totals"]["messages_sent"] == 6
    assert metrics["totals"]["booked_leads"] == 3
    assert "Other Advisor" not in [row["advisor_name"] for row in metrics["advisors"]]

    # The funnel deliberately does NOT exclude duplicates, so 10 here is
    # sample_org's own row count - still with nothing from the other org.
    funnel = client.get("/admin/dashboard/funnel", headers=admin_auth_headers).json()
    assert funnel["organization_id"] == sample_org.id
    assert funnel["total_leads"] == 10
    assert funnel["sent"] == 6
    assert funnel["sold"] == 2


def test_dashboard_metrics_zero_division_returns_zero(client, admin_auth_headers, sample_advisor, db_session):
    response = client.get("/admin/dashboard/metrics", headers=admin_auth_headers)
    assert response.status_code == 200
    data = response.json()
    one = _row_by_name(data["advisors"], "Advisor One")

    assert one["leads_owned"] == 0
    assert one["messages_sent"] == 0
    assert one["reply_rate"] == 0
    assert one["hot_reply_rate"] == 0
    assert one["booking_rate"] == 0
    assert one["dnc_rate"] == 0

    assert data["totals"]["reply_rate"] == 0
    assert data["totals"]["hot_reply_rate"] == 0
    assert data["totals"]["booking_rate"] == 0
    assert data["totals"]["dnc_rate"] == 0
