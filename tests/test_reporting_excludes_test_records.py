"""
Internal test leads (Lead.is_test) must not inflate reporting.

app/services/test_records.py says test records are excluded from "reporting
meant to reflect real performance". Before this, every /reports/* endpoint,
the Activity page's sent-today/email/cadence numbers, the executive portfolio
rows and the EvoSense command center counted them like any other lead.

Every test below builds the same shape: one REAL lead and one TEST lead with
identical activity, and asserts the report counts exactly the real one.
"""

from datetime import datetime, timedelta

import pytest

from app.models.models import (
    BookingLink, CadenceState, CadenceTouchLog, EmailMessage, Lead, LeadOutcome,
    Message, Organization, Platform, Reply, ReplyClassification,
)
from app.services import activity_reporting as ar
from app.services import cadence_service as cs


START, END = "2026-01-01", "2026-01-31"
IN_WINDOW = datetime(2026, 1, 15, 12, 0, 0)


def _lead(db, org, advisor, idx, *, is_test=False, list_name="Client A"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name=f"Rpt{idx}", last_name="Real" if not is_test else "Test",
                phone=f"1214555{idx:04d}", phone_raw=f"1214555{idx:04d}",
                email=f"rpt{idx}@example.com", status="new",
                import_list_name=list_name, is_test=is_test,
                created_at=IN_WINDOW)
    db.add(lead)
    db.flush()
    return lead


def _full_activity(db, lead, advisor, when=IN_WINDOW):
    """A message, a hot reply, a booking and a sale - all inside the window."""
    db.add(Message(lead_id=lead.id, sender_id=advisor.id, body="hi", sent_at=when))
    db.add(Reply(lead_id=lead.id, body="yes", classification=ReplyClassification.NEUTRAL,
                 is_hot=True, received_at=when))
    db.add(BookingLink(lead_id=lead.id, user_id=advisor.id, status="booked", booked_time=when))
    db.add(LeadOutcome(lead_id=lead.id, recorded_by_id=advisor.id, resulted_in_sale=True,
                       created_at=when, has_marker=True))
    db.flush()


@pytest.fixture()
def real_and_test(db_session, sample_org, sample_advisor):
    real = _lead(db_session, sample_org, sample_advisor, 1)
    test = _lead(db_session, sample_org, sample_advisor, 2, is_test=True)
    _full_activity(db_session, real, sample_advisor)
    _full_activity(db_session, test, sample_advisor)
    db_session.commit()
    return {"real": real, "test": test}


def _get(client, headers, path):
    r = client.get(f"{path}?start_date={START}&end_date={END}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── /reports/* ──────────────────────────────────────────────────────────────

def test_conversion_trend_counts_only_real_leads(client, admin_auth_headers, real_and_test):
    body = _get(client, admin_auth_headers, "/reports/conversion-trend")
    assert body["totals"] == {"replies": 1, "hot_replies": 1, "booked": 1, "sold": 1}


def test_engagement_vs_conversion_counts_only_real_leads(client, admin_auth_headers,
                                                         real_and_test, sample_advisor):
    body = _get(client, admin_auth_headers, "/reports/engagement-vs-conversion")
    row = next(a for a in body["advisors"] if a["advisor_id"] == sample_advisor.id)
    assert (row["leads_messaged"], row["replies"], row["hot_replies"],
            row["booked"], row["sold"]) == (1, 1, 1, 1, 1)


def test_by_client_list_counts_only_real_leads(client, admin_auth_headers, real_and_test):
    body = _get(client, admin_auth_headers, "/reports/by-client-list")
    row = next(r for r in body["lists"] if r["list_name"] == "Client A")
    assert (row["leads_received"], row["contacted"], row["replies"],
            row["hot_replies"], row["booked"], row["sold"]) == (1, 1, 1, 1, 1, 1)


def test_by_client_list_omits_a_list_made_only_of_test_leads(client, admin_auth_headers,
                                                             db_session, sample_org,
                                                             sample_advisor, real_and_test):
    qa = _lead(db_session, sample_org, sample_advisor, 3, is_test=True, list_name="QA only")
    _full_activity(db_session, qa, sample_advisor)
    db_session.commit()
    body = _get(client, admin_auth_headers, "/reports/by-client-list")
    assert "QA only" not in {r["list_name"] for r in body["lists"]}


def test_revenue_by_period_counts_only_real_leads(client, admin_auth_headers, real_and_test):
    body = _get(client, admin_auth_headers, "/reports/revenue-by-period")
    assert body["total_sales"] == 1
    assert body["product_mix"]["marker"] == 1
    assert sum(r["sale_count"] for r in body["by_advisor"]) == 1


def test_email_performance_endpoint_counts_only_real_leads(client, admin_auth_headers, db_session,
                                                           sample_advisor, real_and_test):
    now = datetime.utcnow()
    for lead in (real_and_test["real"], real_and_test["test"]):
        db_session.add(EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id, subject="S",
                                    body_html="<p>b</p>", status="sent", sent_at=now))
    db_session.commit()
    r = client.get("/reports/email-performance", headers=admin_auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["totals"]["all"] == 1


# ── activity_reporting ──────────────────────────────────────────────────────

def test_sent_today_excludes_test_leads_from_list_and_counts(db_session, sample_org,
                                                             sample_advisor, real_and_test):
    now = datetime.utcnow()
    real, test = real_and_test["real"], real_and_test["test"]
    for lead in (real, test):
        db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="today",
                               sent_at=now, send_source="manual"))
        db_session.add(EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id, subject="S",
                                    body_html="<p>b</p>", status="sent", sent_at=now,
                                    send_source="manual"))
    db_session.commit()

    out = ar.sent_today(db_session, sample_org.id, tzname="UTC")
    assert out["total"] == 2
    assert out["leads_contacted"] == 1
    assert out["by_channel"] == {"sms": 1, "email": 1}
    assert out["by_source"] == {"manual": 2}
    assert {i["lead_id"] for i in out["items"]} == {real.id}
    # Shape is unchanged: every documented key is still there.
    for key in ("organization_id", "timezone", "window", "total", "returned",
                "leads_contacted", "contacted_more_than_once", "by_channel",
                "by_source", "items"):
        assert key in out


def test_email_performance_service_excludes_test_leads(db_session, sample_org, sample_advisor,
                                                       real_and_test):
    now = datetime.utcnow()
    for lead in (real_and_test["real"], real_and_test["test"]):
        db_session.add(EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id, subject="S",
                                    body_html="<p>b</p>", status="sent", sent_at=now))
    db_session.commit()
    out = ar.email_performance(db_session, sample_org.id)
    assert out["totals"]["all"] == 1 and out["totals"]["sent"] == 1


def test_cadence_outcomes_excludes_test_leads(db_session, sample_org, real_and_test):
    for lead in (real_and_test["real"], real_and_test["test"]):
        state = CadenceState(lead_id=lead.id, status="active", current_touch_number=0,
                             cadence_started_at=datetime.utcnow(),
                             next_touch_due_at=datetime.utcnow())
        db_session.add(state)
        db_session.flush()
        db_session.add(CadenceTouchLog(cadence_state_id=state.id, lead_id=lead.id,
                                       organization_id=sample_org.id, touch_number=1,
                                       attempt_seq=1, outcome=cs.OUTCOME_BLOCKED, channel="sms",
                                       attempted_at=datetime.utcnow()))
    db_session.commit()
    out = ar.cadence_outcomes(db_session, sample_org.id)
    assert out["by_outcome"] == {cs.OUTCOME_BLOCKED: 1}


# ── executive portfolio ─────────────────────────────────────────────────────

def test_executive_rows_exclude_test_leads(db_session, sample_advisor):
    from app.services import executive_portfolio as portfolio

    platform = Platform(name="Brand", slug="brand-test-records")
    db_session.add(platform)
    db_session.commit()
    org = Organization(name="Exec Org", slug="exec-test-records", platform_id=platform.id,
                       plan="starter", is_active=True,
                       created_at=datetime.utcnow() - timedelta(days=120))
    db_session.add(org)
    db_session.commit()

    now = datetime.utcnow()
    for idx, is_test in ((11, False), (12, True)):
        lead = Lead(organization_id=org.id, first_name=f"E{idx}", phone=f"1214556{idx:04d}",
                    status="sent", is_test=is_test, created_at=now - timedelta(days=1),
                    last_messaged_at=now - timedelta(days=1))
        db_session.add(lead)
        db_session.flush()
        db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="x",
                               sent_at=now - timedelta(days=1)))
        db_session.add(Reply(lead_id=lead.id, body="y", received_at=now - timedelta(days=1)))
        db_session.add(BookingLink(lead_id=lead.id, user_id=sample_advisor.id, status="booked",
                                   booked_time=now + timedelta(days=2)))
    db_session.commit()

    (row,) = portfolio.rows(db_session, platform.id, org_ids=[org.id], now=now)
    assert row["leads_total"] == 1
    assert row["leads_sent"] == 1
    assert row["leads_added_recently"] == 1
    assert row["leads_worked_recently"] == 1
    assert row["messages_recently"] == 1
    assert row["replies_total"] == 1
    assert row["replies_unreviewed"] == 1
    assert row["appointments_total"] == 1
    assert row["appointments_upcoming"] == 1


def test_executive_last_activity_ignores_a_test_only_org(db_session, sample_advisor):
    """An organization whose only activity is on a test lead has NO activity -
    it must not read as healthy because QA poked it yesterday."""
    from app.services import executive_portfolio as portfolio

    platform = Platform(name="Brand2", slug="brand2-test-records")
    db_session.add(platform)
    db_session.commit()
    org = Organization(name="QA Only", slug="qa-only-test-records", platform_id=platform.id,
                       plan="starter", is_active=True,
                       created_at=datetime.utcnow() - timedelta(days=120))
    db_session.add(org)
    db_session.commit()
    lead = Lead(organization_id=org.id, first_name="QA", phone="12145560099",
                status="sent", is_test=True)
    db_session.add(lead)
    db_session.flush()
    db_session.add(Message(lead_id=lead.id, sender_id=sample_advisor.id, body="x",
                           sent_at=datetime.utcnow()))
    db_session.commit()

    (row,) = portfolio.rows(db_session, platform.id, org_ids=[org.id])
    assert row["leads_total"] == 0
    assert row["messages_recently"] == 0
    assert row["last_activity"] is None


# ── EvoSense command center ─────────────────────────────────────────────────

def test_evosense_command_center_excludes_test_rows_by_default(db_session, sample_org,
                                                               sample_advisor):
    from app.models.evosense_models import EvoSenseHandoff, EvoSenseProperty
    from app.services.evosense import sandbox_seed as SS
    from app.services.evosense import views as V

    SS.seed_review(db_session, sample_org.id, sample_advisor, replies=True)
    db_session.commit()

    with_test = V.command_center(db_session, sample_org.id, include_test=True)
    assert with_test["needs_you"] and with_test["found"]
    assert any(with_test["happened"][k] for k in ("properties_discovered", "handoffs"))

    default = V.command_center(db_session, sample_org.id)
    assert default["include_test"] is False
    assert default["needs_you"] == [] and default["found"] == []
    assert default["happened"]["properties_discovered"] == 0
    assert default["happened"]["handoffs"] == 0
    # Not filtered: the sandbox banner still tells the truth about the workspace.
    assert default["sandbox"]["banner"]

    # A sandbox-only workspace is shown its sandbox rows by the endpoint...
    assert V.sandbox_only(db_session, sample_org.id) is True

    # ...but once a real property exists, test rows no longer are.
    handoff = db_session.query(EvoSenseHandoff).filter(
        EvoSenseHandoff.organization_id == sample_org.id).first()
    prop = db_session.query(EvoSenseProperty).get(handoff.property_id)
    prop.is_test = False
    handoff.is_test = False
    db_session.commit()
    assert V.sandbox_only(db_session, sample_org.id) is False
    default = V.command_center(db_session, sample_org.id)
    assert [n["id"] for n in default["needs_you"]] == [handoff.id]
