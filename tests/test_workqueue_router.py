"""
Tests for the Advisor Daily Work Queue.

The work queue is advisor-facing, not admin-facing. These tests make sure it is
scoped to the logged-in advisor and that each bucket follows the exact criteria
from the feature request.
"""

from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.deps import get_current_user, get_db
from app.models.models import (
    CadenceState,
    CadenceStatus,
    Lead,
    LeadOutcome,
    LeadStatus,
    LeadTier,
    MessageTrack,
    Organization,
    Reply,
    ReplyClassification,
    User,
)
from app.routers.workqueue_router import router as workqueue_router
from app.services.auth_service import hash_password


def _make_test_client(db_session, user):
    app = FastAPI()
    app.include_router(workqueue_router)

    def _override_get_db():
        yield db_session

    def _override_get_current_user():
        return user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return TestClient(app)


def _lead(db_session, org, advisor, *, first_name, last_name="Lead", phone="12145550100", status=LeadStatus.NEW):
    lead = Lead(
        organization_id=org.id,
        assigned_to_id=advisor.id,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        tier=LeadTier.PRE_NEED,
        message_track=MessageTrack.PRE_NEED_LOCK_PRICE,
        status=status,
    )
    db_session.add(lead)
    db_session.commit()
    return lead


def _advisor(db_session, org, *, email, name="Advisor"):
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


def test_workqueue_scopes_to_logged_in_advisor_and_org(db_session, sample_org, sample_advisor, second_advisor):
    other_org = Organization(name="Other Work Org", slug="other-work-org", plan="trial")
    db_session.add(other_org)
    db_session.commit()
    other_org_advisor = _advisor(db_session, other_org, email="other-work-advisor@example.com", name="Other Advisor")

    own_lead = _lead(db_session, sample_org, sample_advisor, first_name="Own", phone="12145550101")
    other_advisor_lead = _lead(db_session, sample_org, second_advisor, first_name="SameOrgOtherAdvisor", phone="12145550102")
    other_org_lead = _lead(db_session, other_org, other_org_advisor, first_name="OtherOrg", phone="12145550103")

    client = _make_test_client(db_session, sample_advisor)
    response = client.get("/workqueue/today")

    assert response.status_code == 200
    body = response.json()
    ids = {item["lead_id"] for item in body["needs_text"]}

    assert own_lead.id in ids
    assert other_advisor_lead.id not in ids
    assert other_org_lead.id not in ids


def test_workqueue_includes_and_excludes_each_bucket_correctly(db_session, sample_org, sample_advisor, second_advisor):
    now = datetime.utcnow()

    needs_text = _lead(db_session, sample_org, sample_advisor, first_name="NeedsText", phone="12145551001", status=LeadStatus.NEW)
    already_sent = _lead(db_session, sample_org, sample_advisor, first_name="AlreadySent", phone="12145551002", status=LeadStatus.SENT)

    interested_lead = _lead(db_session, sample_org, sample_advisor, first_name="Interested", phone="12145551003", status=LeadStatus.REPLIED)
    interested_reply = Reply(
        lead_id=interested_lead.id,
        body="Yes, call me back",
        classification=ReplyClassification.INTERESTED,
        reviewed_at=None,
    )
    callback_lead = _lead(db_session, sample_org, sample_advisor, first_name="Callback", phone="12145551004", status=LeadStatus.REPLIED)
    callback_reply = Reply(
        lead_id=callback_lead.id,
        body="Call tomorrow",
        classification=ReplyClassification.CALLBACK,
        reviewed_at=None,
    )
    neutral_lead = _lead(db_session, sample_org, sample_advisor, first_name="Neutral", phone="12145551005", status=LeadStatus.REPLIED)
    neutral_reply = Reply(
        lead_id=neutral_lead.id,
        body="Thanks",
        classification=ReplyClassification.NEUTRAL,
        reviewed_at=None,
    )
    reviewed_lead = _lead(db_session, sample_org, sample_advisor, first_name="Reviewed", phone="12145551006", status=LeadStatus.REPLIED)
    reviewed_reply = Reply(
        lead_id=reviewed_lead.id,
        body="Interested but already reviewed",
        classification=ReplyClassification.INTERESTED,
        reviewed_at=now,
    )
    db_session.add_all([interested_reply, callback_reply, neutral_reply, reviewed_reply])

    due_lead = _lead(db_session, sample_org, sample_advisor, first_name="CadenceDue", phone="12145551007", status=LeadStatus.SENT)
    future_lead = _lead(db_session, sample_org, sample_advisor, first_name="CadenceFuture", phone="12145551008", status=LeadStatus.SENT)
    paused_lead = _lead(db_session, sample_org, sample_advisor, first_name="CadencePaused", phone="12145551009", status=LeadStatus.SENT)
    db_session.add_all([
        CadenceState(lead_id=due_lead.id, status=CadenceStatus.ACTIVE, current_touch_number=1, next_touch_due_at=now - timedelta(minutes=5)),
        CadenceState(lead_id=future_lead.id, status=CadenceStatus.ACTIVE, current_touch_number=1, next_touch_due_at=now + timedelta(days=1)),
        CadenceState(lead_id=paused_lead.id, status=CadenceStatus.PAUSED, current_touch_number=1, next_touch_due_at=now - timedelta(minutes=5)),
    ])

    outcome_needed = _lead(db_session, sample_org, sample_advisor, first_name="OutcomeNeeded", phone="12145551010", status=LeadStatus.BOOKED)
    outcome_done = _lead(db_session, sample_org, sample_advisor, first_name="OutcomeDone", phone="12145551011", status=LeadStatus.BOOKED)
    not_booked = _lead(db_session, sample_org, sample_advisor, first_name="NotBooked", phone="12145551012", status=LeadStatus.SENT)
    db_session.add(LeadOutcome(lead_id=outcome_done.id, recorded_by_id=sample_advisor.id, notes="Completed file review"))

    other_advisor_new = _lead(db_session, sample_org, second_advisor, first_name="OtherAdvisorNew", phone="12145551013", status=LeadStatus.NEW)

    db_session.commit()

    client = _make_test_client(db_session, sample_advisor)
    response = client.get("/workqueue/today")

    assert response.status_code == 200
    body = response.json()

    needs_text_ids = {item["lead_id"] for item in body["needs_text"]}
    assert needs_text.id in needs_text_ids
    assert already_sent.id not in needs_text_ids
    assert other_advisor_new.id not in needs_text_ids

    needs_reply_ids = {item["lead_id"] for item in body["needs_reply"]}
    assert interested_lead.id in needs_reply_ids
    assert callback_lead.id in needs_reply_ids
    assert neutral_lead.id not in needs_reply_ids
    assert reviewed_lead.id not in needs_reply_ids

    cadence_due_ids = {item["lead_id"] for item in body["cadence_due"]}
    assert due_lead.id in cadence_due_ids
    assert future_lead.id not in cadence_due_ids
    assert paused_lead.id not in cadence_due_ids

    outcomes_needed_ids = {item["lead_id"] for item in body["outcomes_needed"]}
    assert outcome_needed.id in outcomes_needed_ids
    assert outcome_done.id not in outcomes_needed_ids
    assert not_booked.id not in outcomes_needed_ids
