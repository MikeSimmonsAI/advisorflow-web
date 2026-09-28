"""
TEST RECORDS NEVER INFLATE KPIs.

Every aggregate below is fed ONE real lead and ONE internal test lead
(Lead.is_test = True) with identical attributes and identical child rows
(message, reply, booking, sale, cadence, CRM contact). Each endpoint must report
exactly what the real lead alone would produce. If a count comes back as 2, a
staff member's QA record is being reported to a manager or executive as real
business performance.

The one deliberate exception is asserted too: the daily briefing's
work-queue counts (replies needing attention, cadence touches due) still
include test records, because they must match the inbox / cadence queue the
rep clicks through to, and those surfaces still show test records with their
TEST badge. See app/services/test_records.py for the rule.
"""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from app.models.models import (
    BookingLink, CadenceState, CadenceStatus, CRMContact, EngagementTemperature,
    Lead, LeadOutcome, LeadStatus, Message, Organization, Platform, Reply,
    ReplyClassification, User,
)
from app.models.sales_models import (ROLE_BRAND_EXECUTIVE, SCOPE_CUSTOMER_ORG,
                                     SCOPE_PLATFORM, Membership)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _h(db, user):
    return {"Authorization": "Bearer " + create_access_token(user, db)}


def _user(db, org_id, role, name):
    u = User(organization_id=org_id, email="kpi%d@example.test" % next(_SEQ),
             password_hash=hash_password("TestPass123!"), full_name=name,
             role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _lead_with_history(db, org, advisor, *, is_test, idx, now):
    """One lead plus every child row the KPIs read. Real and test leads are
    built by this same function, so the ONLY difference is `is_test`."""
    lead = Lead(
        organization_id=org.id, assigned_to_id=advisor.id,
        first_name="Twin", last_name="Kpi", phone="1214555%04d" % idx,
        email="twin%d@example.test" % idx,
        status=LeadStatus.BOOKED, engagement_temperature=EngagementTemperature.HOT,
        created_at=now - timedelta(hours=1), is_test=is_test,
    )
    db.add(lead)
    db.flush()
    db.add_all([
        Message(lead_id=lead.id, sender_id=advisor.id, body="hello",
                twilio_sid="SMKPI%04d" % idx, twilio_status="sent",
                # The test lead's message is the LATER one, so if it leaked
                # into team-activity it would win "last action".
                sent_at=now - timedelta(minutes=30 if is_test else 50)),
        Reply(lead_id=lead.id, body="Yes please", twilio_sid="RMKPI%04d" % idx,
              classification=ReplyClassification.INTERESTED, is_hot=True,
              received_at=now - timedelta(minutes=20)),
        BookingLink(lead_id=lead.id, user_id=advisor.id, status="booked",
                    booked_time=now - timedelta(hours=2)),
        LeadOutcome(lead_id=lead.id, recorded_by_id=advisor.id,
                    resulted_in_sale=True, notes="sold"),
        CadenceState(lead_id=lead.id, status=CadenceStatus.ACTIVE,
                     next_touch_due_at=now - timedelta(minutes=5)),
        CRMContact(organization_id=org.id, first_name="Twin", last_name="Kpi",
                   stage="pre_need", lead_id=lead.id),
    ])
    db.commit()
    return lead


@pytest.fixture()
def world(db_session):
    now = _now()
    platform = Platform(name="Evo", slug="evo-kpi")
    db_session.add(platform)
    db_session.commit()
    org = Organization(name="Kpi Org", slug="kpi-org", plan="starter",
                       is_active=True, platform_id=platform.id,
                       created_at=now - timedelta(days=120))
    db_session.add(org)
    db_session.commit()

    advisor = _user(db_session, org.id, "advisor", "Kpi Advisor")
    admin = _user(db_session, org.id, "org_admin", "Kpi Admin")

    executive = _user(db_session, None, "advisor", "Kpi Executive")
    db_session.add_all([
        Membership(user_id=executive.id, scope_type=SCOPE_PLATFORM,
                   scope_id=platform.id, role=ROLE_BRAND_EXECUTIVE, is_active=True),
        Membership(user_id=executive.id, scope_type=SCOPE_CUSTOMER_ORG,
                   scope_id=org.id, role=ROLE_BRAND_EXECUTIVE, is_active=True),
    ])
    db_session.commit()

    real = _lead_with_history(db_session, org, advisor, is_test=False, idx=1, now=now)
    test = _lead_with_history(db_session, org, advisor, is_test=True, idx=2, now=now)
    # A CRM contact with no linked lead is a real contact and must still count.
    db_session.add(CRMContact(organization_id=org.id, first_name="Walk",
                              last_name="In", stage="inquiry"))
    db_session.commit()
    return {"org": org, "advisor": advisor, "admin": admin,
            "executive": executive, "real": real, "test": test, "now": now}


# ── /leads/* overview aggregates ─────────────────────────────────────────────

def test_engagement_breakdown_counts_only_the_real_lead(client, db_session, world):
    r = client.get("/leads/engagement-breakdown", headers=_h(db_session, world["advisor"]))
    assert r.status_code == 200, r.text
    assert r.json()["hot"] == 1


def test_status_funnel_counts_only_the_real_lead(client, db_session, world):
    r = client.get("/leads/status-funnel", headers=_h(db_session, world["advisor"]))
    assert r.status_code == 200, r.text
    booked = next(s for s in r.json() if s["status"] == "booked")
    assert booked["count"] == 1


def test_sparklines_count_only_the_real_lead(client, db_session, world):
    r = client.get("/leads/sparklines", headers=_h(db_session, world["advisor"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert sum(body["leads_imported"]) == 1
    assert sum(body["bookings"]) == 1


def test_daily_briefing_performance_counts_exclude_test_leads(client, db_session, world):
    r = client.get("/leads/daily-briefing", headers=_h(db_session, world["advisor"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["leads_imported_last_24h"] == 1
    assert body["bookings_last_7_days"] == 1
    assert body["certified_appointments_waiting"] == 1
    # Work queues deliberately match the inbox / cadence queue, which still
    # show test records. Changing these is a decision, not a cleanup.
    assert body["replies_needing_attention"] == 2
    assert body["cadence_touches_due_today"] == 2


# ── /admin/dashboard* ────────────────────────────────────────────────────────

def test_admin_master_dashboard_excludes_test_leads(client, db_session, world):
    r = client.get("/admin/dashboard", headers=_h(db_session, world["admin"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_leads"] == 1
    row = next(a for a in body["advisors"] if a["advisor_id"] == world["advisor"].id)
    assert row["leads_owned"] == 1
    assert row["messages_sent"] == 1
    assert row["hot_replies"] == 1


def test_admin_funnel_excludes_test_leads(client, db_session, world):
    r = client.get("/admin/dashboard/funnel", headers=_h(db_session, world["admin"]))
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("total_leads", "sent", "replied", "hot_interested", "booked", "sold"):
        assert body[key] == 1, key


def test_admin_quality_metrics_exclude_test_leads(client, db_session, world):
    r = client.get("/admin/dashboard/metrics", headers=_h(db_session, world["admin"]))
    assert r.status_code == 200, r.text
    totals = r.json()["totals"]
    for key in ("leads_owned", "messages_sent", "replies", "hot_replies", "booked_leads"):
        assert totals[key] == 1, key


def test_admin_revenue_excludes_test_leads(client, db_session, world):
    r = client.get("/admin/dashboard/revenue", headers=_h(db_session, world["admin"]))
    assert r.status_code == 200, r.text
    assert r.json()["total_sales"] == 1


def test_team_activity_ignores_actions_on_test_leads(client, db_session, world):
    r = client.get("/admin/dashboard/team-activity", headers=_h(db_session, world["admin"]))
    assert r.status_code == 200, r.text
    row = next(a for a in r.json()["advisors"] if a["advisor_id"] == world["advisor"].id)
    real_msg = db_session.query(Message).filter(Message.lead_id == world["real"].id).one()
    real_outcome = db_session.query(LeadOutcome).filter(
        LeadOutcome.lead_id == world["real"].id).one()
    expected = max(real_msg.sent_at, real_outcome.created_at)
    assert row["last_action_at"].startswith(expected.isoformat()[:19])


# ── /executive/* ─────────────────────────────────────────────────────────────

def test_executive_observation_overview_excludes_test_leads(client, db_session, world):
    r = client.get("/executive/organizations/%s/observe/overview" % world["org"].id,
                   headers=_h(db_session, world["executive"]))
    assert r.status_code == 200, r.text
    body = r.json()
    s = body["lead_summary"]
    for key in ("total", "arrangements", "hot_replies", "leads_imported_last_24h",
                "bookings_last_7_days", "certified_appointments_waiting",
                "cadence_touches_due_today"):
        assert s[key] == 1, key
    booked = next(f for f in body["funnel"] if f["status"] == "booked")
    assert booked["count"] == 1
    assert [l["id"] for l in body["leads_needing_action"]] == []  # both booked


def test_executive_customer_health_excludes_test_leads(client, db_session, world):
    r = client.get("/executive/customer-health", headers=_h(db_session, world["executive"]))
    assert r.status_code == 200, r.text
    row = next(o for o in r.json()["organizations"] if o["id"] == world["org"].id)
    assert row["total_leads"] == 1
    assert row["booked_count"] == 1


# ── /reports/crm-summary ─────────────────────────────────────────────────────

def test_crm_summary_excludes_contacts_linked_to_test_leads(client, db_session, world):
    r = client.get("/reports/crm-summary", headers=_h(db_session, world["admin"]))
    assert r.status_code == 200, r.text
    body = r.json()
    # The real lead's contact + the unlinked walk-in; NOT the test lead's.
    assert body["total_contacts"] == 2
    stages = {row["stage"]: row["count"] for row in body["stage_counts"]}
    assert stages == {"pre_need": 1, "inquiry": 1}
