"""THE DIAGNOSTICS EXIST AND CAN BE REACHED.

A switch that cannot be inspected and a diagnostic nobody can call are both
just code. Each of these was written as a service function first - runnable
from a shell, which is not the same as being available - and each answers a
question with real families on the other side of it:

    cadence-backlog          what happens the moment cadence SMS is switched on
    cadence-activation-plan  a safe order to do it in
    outbound-switches        is anything on, anywhere, right now
    lifecycle-readiness      what the one-column status model is costing

All four are read-only and god-only. This file proves both, and proves they
change nothing.
"""

from datetime import datetime, timedelta

import pytest

from app.models.models import CadenceState, Lead, User
from app.services.auth_service import create_access_token, hash_password

ENDPOINTS = [
    "/god/maintenance/cadence-backlog",
    "/god/maintenance/cadence-activation-plan",
    "/god/maintenance/outbound-switches",
]


@pytest.fixture()
def god_headers(db_session):
    god = User(organization_id=None, email="god-diag@platform.test",
               password_hash=hash_password("GodPass123!"), full_name="God",
               role="god_admin", must_change_password=False)
    db_session.add(god); db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(god, db_session)}


def _lead_with_due_cadence(db_session, org, advisor):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name="Diag", last_name="Lead", phone="12145558001",
                phone_raw="12145558001", email="diag@example.com", status="sent")
    db_session.add(lead); db_session.commit()
    now = datetime.utcnow()
    db_session.add(CadenceState(lead_id=lead.id, status="active",
                                current_touch_number=4,
                                cadence_started_at=now - timedelta(days=20),
                                next_touch_due_at=now - timedelta(hours=1)))
    db_session.commit()
    return lead


@pytest.mark.parametrize("path", ENDPOINTS)
def test_a_tenant_admin_cannot_reach_it(path, client, admin_auth_headers):
    assert client.get(path, headers=admin_auth_headers).status_code in (401, 403)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_an_anonymous_caller_cannot_reach_it(path, client):
    assert client.get(path).status_code in (401, 403)


def test_the_lifecycle_endpoint_is_god_only(client, admin_auth_headers, sample_org):
    r = client.get("/god/maintenance/lifecycle-readiness?organization_id=%s"
                   % sample_org.id, headers=admin_auth_headers)
    assert r.status_code in (401, 403)


def test_the_backlog_scan_answers_and_changes_nothing(
        client, god_headers, db_session, sample_org, sample_advisor):
    lead = _lead_with_due_cadence(db_session, sample_org, sample_advisor)
    before = db_session.query(CadenceState).filter(
        CadenceState.lead_id == lead.id).one()
    snapshot = (before.current_touch_number, before.next_touch_due_at,
                before.status)

    r = client.get("/god/maintenance/cadence-backlog", headers=god_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["read_only"] is True
    assert body["active_enrollments"] >= 1
    assert body["due_now"] >= 1
    # The whole point of the diagnostic.
    assert body["touches"]["advanced_without_evidence"] == 4
    assert "headline" in body

    db_session.expire_all()
    after = db_session.query(CadenceState).filter(
        CadenceState.lead_id == lead.id).one()
    assert (after.current_touch_number, after.next_touch_due_at,
            after.status) == snapshot


def test_the_backlog_scan_reports_the_shipped_posture(client, god_headers):
    body = client.get("/god/maintenance/cadence-backlog",
                      headers=god_headers).json()
    assert body["sending_enabled"] is False, "cadence SMS is not off"


def test_the_activation_plan_does_not_execute(client, god_headers):
    body = client.get("/god/maintenance/cadence-activation-plan",
                      headers=god_headers).json()
    assert body["executed"] is False
    assert body["read_only"] is True
    assert body["recommended_sequence"]


def test_the_switch_report_shows_the_deployment_half(client, god_headers):
    """This was the gap: the per-customer half was already on the customer
    record, but 'is anything on' could not be answered without shell access to
    the host."""
    body = client.get("/god/maintenance/outbound-switches",
                      headers=god_headers).json()
    assert body["read_only"] is True
    assert set(body["email"]["deployment"]), "no email switches reported"
    assert all(v is False for v in body["email"]["deployment"].values()), \
        "an outbound email source is enabled"
    assert body["cadence_sms"]["deployment_enabled"] is False
    assert body["cadence_sms"]["variable"] == "CADENCE_SMS_SENDING"


def test_the_switch_report_takes_an_organization(client, god_headers, sample_org):
    body = client.get("/god/maintenance/outbound-switches?organization_id=%s"
                      % sample_org.id, headers=god_headers).json()
    assert body["organization_id"] == sample_org.id
    # With an organization, the report is per source and carries BOTH halves,
    # which is the only combination that can send.
    for source, row in body["email"].items():
        assert set(row) >= {"deployment_enabled", "organization_enabled",
                            "effective", "variable"}, source
        assert row["effective"] is False, "%s is live for this customer" % source


def test_an_unknown_organization_is_a_404_not_an_empty_report(client, god_headers):
    r = client.get("/god/maintenance/outbound-switches?organization_id=nope",
                   headers=god_headers)
    assert r.status_code == 404


def test_the_lifecycle_report_counts_conflicted_leads(
        client, god_headers, db_session, sample_org, sample_advisor):
    from app.models.models import BookingLink
    conflicted = Lead(organization_id=sample_org.id,
                      assigned_to_id=sample_advisor.id, first_name="Both",
                      last_name="States", phone="12145558002",
                      email="both@example.com", status="dnc")
    db_session.add(conflicted); db_session.commit()
    db_session.add(BookingLink(token="diag-1", lead_id=conflicted.id,
                               user_id=sample_advisor.id, status="booked",
                               booked_time=datetime.utcnow() + timedelta(days=2)))
    db_session.commit()

    body = client.get("/god/maintenance/lifecycle-readiness?organization_id=%s"
                      % sample_org.id, headers=god_headers).json()
    assert body["read_only"] is True
    assert body["cannot_be_expressed_in_one_column"] >= 1
    assert "appointment_state" in body["lost_by_dimension"]


def test_every_readiness_endpoint_is_a_get(client, god_headers):
    """A diagnostic that answers a POST invites somebody to think it does
    something."""
    for path in ENDPOINTS:
        assert client.post(path, headers=god_headers).status_code in (404, 405)
