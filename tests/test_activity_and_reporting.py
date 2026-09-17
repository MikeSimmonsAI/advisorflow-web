"""
SS3 and SS4 — today's activity, and the email reporting that never existed.

SS3's defect was not the query, it was the location: the Activity screen
fetched 300 rows from a 30-day endpoint and decided "today" in the browser,
so an organization sending more than 300 messages a month undercounted today
with a confident number on screen, and the day boundary came from the viewer's
laptop rather than the business's clock.

SS4's defect was total: every report aggregated `Message` and nothing else.
EmailMessage was not imported by reports_router at all.
"""

from datetime import datetime, timedelta

import pytest

from app.models.models import (
    CadenceState, CadenceTouchLog, EmailMessage, Lead, Message, Organization,
    Reply, User,
)
from app.services import activity_reporting as ar
from app.services import cadence_service as cs
from app.services import send_source
from app.services.auth_service import create_access_token, hash_password


def _lead(db_session, org, advisor, phone="12145557101", email="a@example.com"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Act", last_name="Ivity", phone=phone,
                phone_raw=phone, email=email, status="new")
    db_session.add(lead); db_session.commit(); return lead


def _sms(db_session, lead, advisor, *, when=None, source=None, actor=None):
    row = Message(lead_id=lead.id, sender_id=advisor.id, body="hello",
                  twilio_sid="SM1", twilio_status="sent",
                  sent_at=when or datetime.utcnow(),
                  send_source=source, sent_by_user_id=actor)
    db_session.add(row); db_session.commit(); return row


def _email(db_session, lead, advisor, *, when=None, source=None, actor=None,
           status="sent"):
    row = EmailMessage(lead_id=lead.id, sender_id=advisor.id, subject="Subj",
                       body_html="<p>b</p>", status=status,
                       sent_at=when or datetime.utcnow(),
                       send_source=source, sent_by_user_id=actor)
    db_session.add(row); db_session.commit(); return row


def _headers(db_session, user):
    return {"Authorization": f"Bearer {create_access_token(user, db_session)}"}


# ── SS3 ─────────────────────────────────────────────────────────────────────

def test_today_is_answered_by_the_server_and_excludes_yesterday(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    _sms(db_session, lead, sample_advisor, when=datetime.utcnow())
    _sms(db_session, lead, sample_advisor,
         when=datetime.utcnow() - timedelta(days=3))

    out = ar.sent_today(db_session, sample_org.id, tzname="UTC")
    assert out["total"] == 1
    assert out["by_channel"] == {"sms": 1}


def test_today_survives_a_volume_that_defeated_the_browser_filter(
        db_session, sample_org, sample_advisor):
    """The old path fetched 300 rows from a 30-day window and filtered client
    side, so today's count silently capped. 400 sends today, and the server
    counts all of them."""
    lead = _lead(db_session, sample_org, sample_advisor)
    now = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    for i in range(400):
        db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id,
                               body=f"m{i}", twilio_status="sent",
                               sent_at=now - timedelta(seconds=i)))
    db_session.commit()

    out = ar.sent_today(db_session, sample_org.id, limit=2000,
                        on=now, tzname="UTC")
    assert out["total"] == 400


def test_today_separates_the_actor_from_the_family_facing_advisor(
        db_session, sample_org, sample_advisor, second_advisor):
    lead = _lead(db_session, sample_org, second_advisor)
    _sms(db_session, lead, second_advisor, source=send_source.BULK_AI,
         actor=sample_advisor.id)

    row = ar.sent_today(db_session, sample_org.id, tzname="UTC")["items"][0]
    assert row["advisor_user_id"] == second_advisor.id
    assert row["sent_by_user_id"] == sample_advisor.id
    assert row["advisor_name"] and row["sent_by_name"]
    assert row["send_source"] == send_source.BULK_AI


def test_today_counts_repeat_touches_per_family(
        db_session, sample_org, sample_advisor):
    """Nothing could answer "did we contact this family twice today"."""
    twice = _lead(db_session, sample_org, sample_advisor, phone="12145557111")
    once = _lead(db_session, sample_org, sample_advisor, phone="12145557112")
    _sms(db_session, twice, sample_advisor)
    _email(db_session, twice, sample_advisor)
    _sms(db_session, once, sample_advisor)

    out = ar.sent_today(db_session, sample_org.id, tzname="UTC")
    assert out["leads_contacted"] == 2
    assert out["contacted_more_than_once"] == 1
    by_lead = {r["lead_id"]: r["touches_today"] for r in out["items"]}
    assert by_lead[twice.id] == 2 and by_lead[once.id] == 1


def test_an_unattributed_row_is_reported_as_unrecorded_not_guessed(
        db_session, sample_org, sample_advisor):
    """Rows written before attribution existed have no source. Labelling them
    'manual' would be inventing history."""
    lead = _lead(db_session, sample_org, sample_advisor)
    _sms(db_session, lead, sample_advisor, source=None)
    out = ar.sent_today(db_session, sample_org.id, tzname="UTC")
    assert out["by_source"] == {ar.UNRECORDED: 1}
    assert out["items"][0]["send_source"] is None


def test_today_is_org_scoped(db_session, sample_org, sample_advisor):
    other = Organization(name="Other Act Co", slug="other-act", plan="trial")
    db_session.add(other); db_session.flush()
    outsider = User(organization_id=other.id, email="a@other.test",
                    password_hash=hash_password("APass123!"), full_name="A",
                    role="advisor", must_change_password=False)
    db_session.add(outsider); db_session.commit()
    foreign = _lead(db_session, other, outsider, phone="12145557199")
    _sms(db_session, foreign, outsider)

    mine = _lead(db_session, sample_org, sample_advisor)
    _sms(db_session, mine, sample_advisor)

    out = ar.sent_today(db_session, sample_org.id, tzname="UTC")
    assert out["total"] == 1
    assert out["items"][0]["lead_id"] == mine.id


def test_an_advisor_sees_their_own_book_and_a_manager_sees_the_team(
        client, db_session, sample_org, sample_advisor, second_advisor,
        admin_auth_headers):
    mine = _lead(db_session, sample_org, sample_advisor, phone="12145557121")
    theirs = _lead(db_session, sample_org, second_advisor, phone="12145557122")
    _sms(db_session, mine, sample_advisor)
    _sms(db_session, theirs, second_advisor)

    advisor_view = client.get("/activity/today",
                              headers=_headers(db_session, sample_advisor))
    assert advisor_view.status_code == 200
    assert {r["lead_id"] for r in advisor_view.json()["items"]} == {mine.id}

    manager_view = client.get("/activity/today", headers=admin_auth_headers)
    assert {r["lead_id"] for r in manager_view.json()["items"]} == {mine.id, theirs.id}


# ── SS4 ─────────────────────────────────────────────────────────────────────

def test_email_performance_counts_real_delivery_state(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    _email(db_session, lead, sample_advisor, source=send_source.AUTO_SEND)
    _email(db_session, lead, sample_advisor, source=send_source.AUTO_SEND,
           status="failed")
    _email(db_session, lead, sample_advisor, source=send_source.MANUAL,
           status="delivered")

    out = ar.email_performance(db_session, sample_org.id)
    assert out["totals"]["all"] == 3
    assert out["totals"]["sent"] == 1
    assert out["totals"]["failed"] == 1
    assert out["totals"]["delivered"] == 1


def test_email_performance_groups_by_source(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    _email(db_session, lead, sample_advisor, source=send_source.AUTO_SEND)
    _email(db_session, lead, sample_advisor, source=send_source.AUTO_SEND)
    _email(db_session, lead, sample_advisor, source=None)

    out = ar.email_performance(db_session, sample_org.id, group_by="source")
    groups = {g["key"]: g["total"] for g in out["groups"]}
    assert groups[send_source.AUTO_SEND] == 2
    assert groups[ar.UNRECORDED] == 1


def test_email_performance_groups_by_actor(
        db_session, sample_org, sample_advisor, second_advisor):
    lead = _lead(db_session, sample_org, second_advisor)
    _email(db_session, lead, second_advisor, actor=sample_advisor.id)
    out = ar.email_performance(db_session, sample_org.id, group_by="user")
    assert out["groups"][0]["key"] == sample_advisor.id
    assert out["groups"][0]["label"] == sample_advisor.full_name


def test_the_report_says_what_it_cannot_answer_rather_than_returning_zero(
        db_session, sample_org, sample_advisor):
    out = ar.email_performance(db_session, sample_org.id)
    assert "generated_not_sent" in out["not_available"]
    assert "replies_per_email" in out["not_available"]
    assert "skipped_suppressed" in out["not_available"]


def test_email_performance_is_org_scoped(db_session, sample_org, sample_advisor):
    other = Organization(name="Other Rep Co", slug="other-rep", plan="trial")
    db_session.add(other); db_session.flush()
    outsider = User(organization_id=other.id, email="r@other.test",
                    password_hash=hash_password("RPass123!"), full_name="R",
                    role="advisor", must_change_password=False)
    db_session.add(outsider); db_session.commit()
    foreign = _lead(db_session, other, outsider, phone="12145557299")
    _email(db_session, foreign, outsider)

    out = ar.email_performance(db_session, sample_org.id)
    assert out["totals"]["all"] == 0


def test_cadence_outcomes_report_counts_refusals_not_just_sends(
        db_session, sample_org, sample_advisor):
    """The only place the platform can currently say how much outbound was
    refused rather than how much succeeded."""
    lead = _lead(db_session, sample_org, sample_advisor)
    state = CadenceState(lead_id=lead.id, status="active",
                         current_touch_number=0,
                         cadence_started_at=datetime.utcnow(),
                         next_touch_due_at=datetime.utcnow())
    db_session.add(state); db_session.flush()
    for i, outcome in enumerate((cs.OUTCOME_SENT, cs.OUTCOME_BLOCKED,
                                 cs.OUTCOME_BLOCKED, cs.OUTCOME_FAILED)):
        db_session.add(CadenceTouchLog(
            cadence_state_id=state.id, lead_id=lead.id,
            organization_id=sample_org.id, touch_number=i + 1, attempt_seq=1,
            outcome=outcome, channel="sms", attempted_at=datetime.utcnow()))
    db_session.commit()

    out = ar.cadence_outcomes(db_session, sample_org.id)
    assert out["by_outcome"][cs.OUTCOME_BLOCKED] == 2
    assert out["by_outcome"][cs.OUTCOME_SENT] == 1
    assert out["by_outcome"][cs.OUTCOME_FAILED] == 1


def test_the_reporting_endpoints_require_a_tenant_user(client):
    assert client.get("/reports/email-performance").status_code == 401
    assert client.get("/reports/cadence-outcomes").status_code == 401
    assert client.get("/activity/today").status_code == 401


def test_the_email_report_endpoint_returns_the_shape(
        client, admin_auth_headers, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    _email(db_session, lead, sample_advisor, source=send_source.AUTO_SEND)
    response = client.get("/reports/email-performance?days=7&group_by=source",
                          headers=admin_auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["totals"]["all"] == 1
    assert body["group_by"] == "source"
