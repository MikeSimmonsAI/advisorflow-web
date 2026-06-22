from datetime import datetime, timedelta, timezone

from app.models.models import (
    CadenceState,
    CadenceStatus,
    EngagementTemperature,
    Lead,
    LeadStatus,
    LeadTier,
    MessageTrack,
    Organization,
    Reply,
    User,
)
from app.services.auth_service import create_access_token, hash_password


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _headers_for(user):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def _advisor(db_session, org, *, email, name="Chart Advisor"):
    advisor = User(
        organization_id=org.id,
        email=email,
        password_hash=hash_password("TestPass123!"),
        full_name=name,
        role="advisor",
    )
    db_session.add(advisor)
    db_session.commit()
    return advisor


def _lead(
    db_session,
    org,
    advisor,
    *,
    first_name,
    phone,
    status=LeadStatus.NEW,
    temperature=EngagementTemperature.UNKNOWN,
):
    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id,
        first_name=first_name,
        last_name="Charts",
        phone=phone,
        tier=LeadTier.PRE_NEED,
        message_track=MessageTrack.PRE_NEED_LOCK_PRICE,
        status=status,
        engagement_temperature=temperature,
    )
    db_session.add(lead)
    db_session.commit()
    return lead


def test_reply_activity_by_day_counts_exactly_and_scopes_to_current_advisor(client, db_session, sample_org, sample_advisor, second_advisor):
    now = _now()
    today = now.date()
    other_org = Organization(name="Other Chart Org", slug="other-chart-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_org_advisor = _advisor(db_session, other_org, email="other-chart@example.com")

    own_lead = _lead(db_session, sample_org, sample_advisor, first_name="Own", phone="12145556001")
    own_second_lead = _lead(db_session, sample_org, sample_advisor, first_name="OwnTwo", phone="12145556002")
    same_org_other_lead = _lead(db_session, sample_org, second_advisor, first_name="OtherAdvisor", phone="12145556003")
    other_org_lead = _lead(db_session, other_org, other_org_advisor, first_name="OtherOrg", phone="12145556004")

    db_session.add_all([
        Reply(lead_id=own_lead.id, body="today 1", received_at=now.replace(hour=9, minute=0, second=0, microsecond=0)),
        Reply(lead_id=own_second_lead.id, body="today 2", received_at=now.replace(hour=10, minute=0, second=0, microsecond=0)),
        Reply(lead_id=own_lead.id, body="yesterday", received_at=now - timedelta(days=1)),
        Reply(lead_id=own_lead.id, body="outside range", received_at=now - timedelta(days=4)),
        Reply(lead_id=same_org_other_lead.id, body="same org other advisor", received_at=now),
        Reply(lead_id=other_org_lead.id, body="other org", received_at=now),
    ])
    db_session.commit()

    response = client.get("/sms/replies/activity-by-day?days=3", headers=_headers_for(sample_advisor))

    assert response.status_code == 200
    assert response.json() == [
        {"date": (today - timedelta(days=2)).isoformat(), "count": 0},
        {"date": (today - timedelta(days=1)).isoformat(), "count": 1},
        {"date": today.isoformat(), "count": 2},
    ]


def test_engagement_breakdown_counts_exactly_and_scopes_to_current_advisor(client, db_session, sample_org, sample_advisor, second_advisor):
    _lead(db_session, sample_org, sample_advisor, first_name="HotOne", phone="12145556101", temperature=EngagementTemperature.HOT)
    _lead(db_session, sample_org, sample_advisor, first_name="HotTwo", phone="12145556102", temperature=EngagementTemperature.HOT)
    _lead(db_session, sample_org, sample_advisor, first_name="Warm", phone="12145556103", temperature=EngagementTemperature.WARM)
    _lead(db_session, sample_org, sample_advisor, first_name="Cold", phone="12145556104", temperature=EngagementTemperature.COLD)
    _lead(db_session, sample_org, sample_advisor, first_name="Unknown", phone="12145556105", temperature=EngagementTemperature.UNKNOWN)
    _lead(db_session, sample_org, second_advisor, first_name="OtherAdvisorHot", phone="12145556106", temperature=EngagementTemperature.HOT)

    response = client.get("/leads/engagement-breakdown", headers=_headers_for(sample_advisor))

    assert response.status_code == 200
    assert response.json() == {
        "hot": 2,
        "warm": 1,
        "cold": 1,
        "unknown": 1,
    }


def test_cadence_health_summary_counts_and_formula_are_exact_and_scoped(client, db_session, sample_org, sample_advisor, second_advisor):
    now = _now()
    healthy_active = _lead(db_session, sample_org, sample_advisor, first_name="Healthy", phone="12145556201")
    overdue_active = _lead(db_session, sample_org, sample_advisor, first_name="Overdue", phone="12145556202")
    completed = _lead(db_session, sample_org, sample_advisor, first_name="Completed", phone="12145556203")
    stopped = _lead(db_session, sample_org, sample_advisor, first_name="Stopped", phone="12145556204")
    other_advisor_active = _lead(db_session, sample_org, second_advisor, first_name="OtherActive", phone="12145556205")

    db_session.add_all([
        CadenceState(lead_id=healthy_active.id, status=CadenceStatus.ACTIVE, next_touch_due_at=now + timedelta(days=1)),
        CadenceState(lead_id=overdue_active.id, status=CadenceStatus.ACTIVE, next_touch_due_at=now - timedelta(minutes=1)),
        CadenceState(lead_id=completed.id, status=CadenceStatus.COMPLETED, next_touch_due_at=now - timedelta(days=1)),
        CadenceState(lead_id=stopped.id, status=CadenceStatus.STOPPED_REPLIED, next_touch_due_at=now - timedelta(days=1)),
        CadenceState(lead_id=other_advisor_active.id, status=CadenceStatus.ACTIVE, next_touch_due_at=now + timedelta(days=1)),
    ])
    db_session.commit()

    response = client.get("/cadence/health-summary", headers=_headers_for(sample_advisor))

    assert response.status_code == 200
    body = response.json()
    assert body["active_count"] == 2
    assert body["healthy_active_count"] == 1
    assert body["overdue_active_count"] == 1
    assert body["health_score"] == 50.0
    assert body["counts"]["active"] == 2
    assert body["counts"]["completed"] == 1
    assert body["counts"]["stopped_replied"] == 1


def test_cadence_health_summary_treats_unset_next_touch_due_at_as_healthy_not_overdue(
    client, db_session, sample_org, sample_advisor
):
    # An active cadence with no next_touch_due_at yet has nothing scheduled,
    # so it cannot be overdue. It must count as healthy per the documented
    # formula, not silently fall into the overdue bucket.
    unset_due_active = _lead(db_session, sample_org, sample_advisor, first_name="UnsetDue", phone="12145556210")

    db_session.add(CadenceState(lead_id=unset_due_active.id, status=CadenceStatus.ACTIVE, next_touch_due_at=None))
    db_session.commit()

    response = client.get("/cadence/health-summary", headers=_headers_for(sample_advisor))

    assert response.status_code == 200
    body = response.json()
    assert body["active_count"] == 1
    assert body["healthy_active_count"] == 1
    assert body["overdue_active_count"] == 0
    assert body["health_score"] == 100.0


def test_status_funnel_counts_exactly_and_scopes_to_current_advisor(client, db_session, sample_org, sample_advisor, second_advisor):
    for i in range(3):
        _lead(db_session, sample_org, sample_advisor, first_name=f"New{i}", phone=f"1214555630{i}", status=LeadStatus.NEW)
    for i in range(2):
        _lead(db_session, sample_org, sample_advisor, first_name=f"Sent{i}", phone=f"1214555640{i}", status=LeadStatus.SENT)
    _lead(db_session, sample_org, sample_advisor, first_name="Replied", phone="12145556501", status=LeadStatus.REPLIED)
    _lead(db_session, sample_org, sample_advisor, first_name="Hot", phone="12145556502", status=LeadStatus.HOT)
    _lead(db_session, sample_org, sample_advisor, first_name="Booked", phone="12145556503", status=LeadStatus.BOOKED)
    _lead(db_session, sample_org, sample_advisor, first_name="DncExcluded", phone="12145556504", status=LeadStatus.DNC)
    _lead(db_session, sample_org, second_advisor, first_name="OtherAdvisorNew", phone="12145556505", status=LeadStatus.NEW)

    response = client.get("/leads/status-funnel", headers=_headers_for(sample_advisor))

    assert response.status_code == 200
    assert response.json() == [
        {"status": "new", "label": "New", "count": 3},
        {"status": "sent", "label": "Sent", "count": 2},
        {"status": "replied", "label": "Replied", "count": 1},
        {"status": "hot", "label": "Hot", "count": 1},
        {"status": "booked", "label": "Booked", "count": 1},
    ]
