from datetime import datetime, timedelta, timezone

from app.models.models import (
    BookingLink,
    CadenceState,
    CadenceStatus,
    Lead,
    LeadStatus,
    LeadTier,
    MessageTrack,
    Organization,
    Reply,
    ReplyClassification,
    User,
)
from app.services.auth_service import create_access_token, hash_password


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _lead(db_session, org, advisor, *, first_name, phone, created_at=None, status=LeadStatus.NEW):
    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id,
        first_name=first_name,
        last_name="Briefing",
        phone=phone,
        tier=LeadTier.PRE_NEED,
        message_track=MessageTrack.PRE_NEED_LOCK_PRICE,
        status=status,
        created_at=created_at or _now(),
    )
    db_session.add(lead)
    db_session.commit()
    return lead


def _advisor(db_session, org, *, email, name="Other Advisor"):
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


def _headers_for(user):
    token = create_access_token(user)
    return {"Authorization": f"Bearer {token}"}


def test_daily_briefing_counts_are_exact_for_current_advisor(client, db_session, sample_org, sample_advisor):
    now = _now()

    attention_lead_1 = _lead(db_session, sample_org, sample_advisor, first_name="AttentionOne", phone="12145552001", created_at=now - timedelta(hours=2))
    attention_lead_2 = _lead(db_session, sample_org, sample_advisor, first_name="AttentionTwo", phone="12145552002", created_at=now - timedelta(hours=3))
    ignored_reply_lead = _lead(db_session, sample_org, sample_advisor, first_name="Neutral", phone="12145552003", created_at=now - timedelta(hours=4))

    db_session.add_all([
        Reply(lead_id=attention_lead_1.id, body="Yes", classification=ReplyClassification.INTERESTED),
        Reply(lead_id=attention_lead_2.id, body="Call me", classification=ReplyClassification.CALLBACK),
        Reply(lead_id=ignored_reply_lead.id, body="Thanks", classification=ReplyClassification.NEUTRAL),
    ])

    due_today_1 = _lead(db_session, sample_org, sample_advisor, first_name="DueOne", phone="12145552004", created_at=now - timedelta(days=2))
    due_today_2 = _lead(db_session, sample_org, sample_advisor, first_name="DueTwo", phone="12145552005", created_at=now - timedelta(days=2))
    future_due = _lead(db_session, sample_org, sample_advisor, first_name="FutureDue", phone="12145552006", created_at=now - timedelta(days=2))
    paused_due = _lead(db_session, sample_org, sample_advisor, first_name="PausedDue", phone="12145552007", created_at=now - timedelta(days=2))

    db_session.add_all([
        CadenceState(lead_id=due_today_1.id, status=CadenceStatus.ACTIVE, next_touch_due_at=now - timedelta(minutes=5)),
        CadenceState(lead_id=due_today_2.id, status=CadenceStatus.ACTIVE, next_touch_due_at=now.replace(hour=23, minute=30, second=0, microsecond=0)),
        CadenceState(lead_id=future_due.id, status=CadenceStatus.ACTIVE, next_touch_due_at=now + timedelta(days=1)),
        CadenceState(lead_id=paused_due.id, status=CadenceStatus.PAUSED, next_touch_due_at=now - timedelta(minutes=5)),
    ])

    _lead(db_session, sample_org, sample_advisor, first_name="ImportedOne", phone="12145552008", created_at=now - timedelta(hours=1))
    _lead(db_session, sample_org, sample_advisor, first_name="ImportedTwo", phone="12145552009", created_at=now - timedelta(hours=23))
    _lead(db_session, sample_org, sample_advisor, first_name="OldImport", phone="12145552010", created_at=now - timedelta(days=2))

    booked_recent_1 = _lead(db_session, sample_org, sample_advisor, first_name="BookedOne", phone="12145552011", status=LeadStatus.BOOKED, created_at=now - timedelta(days=30))
    booked_recent_2 = _lead(db_session, sample_org, sample_advisor, first_name="BookedTwo", phone="12145552012", status=LeadStatus.BOOKED, created_at=now - timedelta(days=30))
    booked_old = _lead(db_session, sample_org, sample_advisor, first_name="BookedOld", phone="12145552013", status=LeadStatus.BOOKED, created_at=now - timedelta(days=30))

    db_session.add_all([
        BookingLink(lead_id=booked_recent_1.id, user_id=sample_advisor.id, status="booked", booked_time=now - timedelta(days=1)),
        BookingLink(lead_id=booked_recent_2.id, user_id=sample_advisor.id, status="booked", booked_time=now - timedelta(days=6)),
        BookingLink(lead_id=booked_old.id, user_id=sample_advisor.id, status="booked", booked_time=now - timedelta(days=8)),
    ])
    db_session.commit()

    response = client.get("/leads/daily-briefing", headers=_headers_for(sample_advisor))

    assert response.status_code == 200
    assert response.json() == {
        "replies_needing_attention": 2,
        "cadence_touches_due_today": 2,
        "leads_imported_last_24h": 5,
        "bookings_last_7_days": 2,
    }


def test_daily_briefing_is_scoped_to_current_advisor_and_org(client, db_session, sample_org, sample_advisor, second_advisor):
    now = _now()
    other_org = Organization(name="Other Briefing Org", slug="other-briefing-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_org_advisor = _advisor(db_session, other_org, email="other-briefing@example.com", name="Other Org Advisor")

    own_lead = _lead(db_session, sample_org, sample_advisor, first_name="Own", phone="12145553001", created_at=now - timedelta(hours=1))
    same_org_other_advisor = _lead(db_session, sample_org, second_advisor, first_name="OtherAdvisor", phone="12145553002", created_at=now - timedelta(hours=1))
    other_org_lead = _lead(db_session, other_org, other_org_advisor, first_name="OtherOrg", phone="12145553003", created_at=now - timedelta(hours=1))

    db_session.add_all([
        Reply(lead_id=own_lead.id, body="Own yes", classification=ReplyClassification.INTERESTED),
        Reply(lead_id=same_org_other_advisor.id, body="Same org other advisor yes", classification=ReplyClassification.INTERESTED),
        Reply(lead_id=other_org_lead.id, body="Other org yes", classification=ReplyClassification.INTERESTED),
        CadenceState(lead_id=own_lead.id, status=CadenceStatus.ACTIVE, next_touch_due_at=now - timedelta(minutes=5)),
        CadenceState(lead_id=same_org_other_advisor.id, status=CadenceStatus.ACTIVE, next_touch_due_at=now - timedelta(minutes=5)),
        CadenceState(lead_id=other_org_lead.id, status=CadenceStatus.ACTIVE, next_touch_due_at=now - timedelta(minutes=5)),
        BookingLink(lead_id=own_lead.id, user_id=sample_advisor.id, status="booked", booked_time=now - timedelta(days=1)),
        BookingLink(lead_id=same_org_other_advisor.id, user_id=second_advisor.id, status="booked", booked_time=now - timedelta(days=1)),
        BookingLink(lead_id=other_org_lead.id, user_id=other_org_advisor.id, status="booked", booked_time=now - timedelta(days=1)),
    ])
    db_session.commit()

    response = client.get("/leads/daily-briefing", headers=_headers_for(sample_advisor))

    assert response.status_code == 200
    assert response.json() == {
        "replies_needing_attention": 1,
        "cadence_touches_due_today": 1,
        "leads_imported_last_24h": 1,
        "bookings_last_7_days": 1,
    }
