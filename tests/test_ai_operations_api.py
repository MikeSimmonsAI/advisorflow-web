"""THE OPERATIONAL SURFACE — two routers, two authorities, one engine.

WHAT THESE TESTS PIN DOWN.

  * `/ai-operations` answers for the CALLER'S OWN organization and takes no
    organization id anywhere, so the tenant boundary is a property of the
    signatures rather than of a filter each route has to remember. The tests
    check that property by putting a second customer's conversation in the
    database and asserting it never appears.

  * Another tenant's conversation is 404 and not 403, deliberately: telling
    a caller that a conversation exists somewhere else is itself a
    disclosure.

  * `/god/ai-operations` extends God Mode through the same `require_god`
    dependency every other platform-control route uses, so a non-god caller
    is refused with 403 — asserted as what the application actually does
    rather than as what a convention elsewhere might suggest.

  * There is NO route here that makes an AI employee send anything, which is
    why nothing below tries to.
"""

from datetime import datetime

import pytest

from app.models.models import (Lead, LeadStatus, LeadTier, Organization, User)
from app.services.ai_operations import (channels, constants as C, continuity,
                                        contracts, orchestrator, profiles)
from app.services.auth_service import create_access_token, hash_password


READ_ROUTES = [
    "/ai-operations/overview",
    "/ai-operations/threads",
    "/ai-operations/communications",
    "/ai-operations/scheduled",
    "/ai-operations/handoffs",
    "/ai-operations/blocked",
    "/ai-operations/activity",
    "/ai-operations/budget",
    "/ai-operations/stop-reasons",
    "/ai-operations/employees/some-employee/status",
]

GOD_READ_ROUTES = [
    "/god/ai-operations/state",
    "/god/ai-operations/unrouted",
    "/god/ai-operations/profiles",
]


def business_hours():
    return datetime.utcnow().replace(hour=15, minute=0, second=0,
                                     microsecond=0)


@pytest.fixture()
def ops_enabled(monkeypatch):
    """AI Operations ON for this test, and never live.

    Set deliberately even on the read routes: `overview` renders the
    dark-launch state, so a test that left the layer off would be asserting
    against a different payload than the one an operator sees.
    """
    monkeypatch.setenv("AI_OPERATIONS_ENABLED", "1")
    monkeypatch.delenv("AI_OPERATIONS_LIVE_SEND", raising=False)
    monkeypatch.delenv("AI_OPERATIONS_KILL", raising=False)
    monkeypatch.delenv("AI_WORKFORCE_KILL", raising=False)
    yield
    channels.reset_adapters()
    profiles.forget_declared()


def declare(org, **overrides):
    kwargs = {
        "employee_id": "api-employee",
        "organization_id": org.id,
        "name": "API Test Employee",
        "tool_keys": set(profiles.REACTIVATION_TOOLS),
        "channels": {C.CHANNEL_SMS, C.CHANNEL_EMAIL},
        "activation_state": "simulation",
        "status": "active",
        "timezone": "America/Chicago",
    }
    kwargs.update(overrides)
    return contracts.declare_employee_context(**kwargs)


def god_headers(db_session):
    """A platform owner. Role is the whole authority; no tenant is needed."""
    god = User(organization_id=None, email="god@advisorflow.test",
               password_hash=hash_password("GodPass123!"),
               full_name="Platform Owner", role="god_admin",
               must_change_password=False)
    db_session.add(god)
    db_session.commit()
    return {"Authorization": "Bearer %s" % create_access_token(god,
                                                               db_session)}


def other_tenant_thread(db_session):
    """A whole second customer, with a conversation of their own."""
    org = Organization(name="Somebody Else's Home", slug="other-org-t7-api",
                       plan="standard")
    db_session.add(org)
    db_session.commit()
    lead = Lead(organization_id=org.id, first_name="Not", last_name="Yours",
                phone="19725550199", email="not.yours@example.com",
                tier=LeadTier.PRE_NEED, status=LeadStatus.NEW)
    db_session.add(lead)
    db_session.commit()
    thread = continuity.open_thread(
        db_session, declare(org, employee_id="other-org-employee"),
        subject_type="lead", subject_id=lead.id,
        objective="Somebody else's objective")
    db_session.commit()
    return org, lead, thread


def own_thread(db_session, org, lead):
    """A conversation for the caller's own organization, with a real send."""
    ctx = declare(org)
    result = orchestrator.send_message(db_session, ctx, subject_id=lead.id,
                                       objective="Our own objective",
                                       body="First touch.",
                                       now=business_hours())
    assert result.ok is True, result.gate.denial_code
    db_session.commit()
    return result.gate.thread


# ═══════════════════════════════════════════════════════════════════════════
# AUTHENTICATION AND TENANCY
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("path", READ_ROUTES)
def test_every_read_route_requires_authentication(ops_enabled, client, path):
    """No token, no operational data. Not an empty list — a refusal."""
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", GOD_READ_ROUTES)
def test_every_god_read_route_requires_authentication(ops_enabled, client,
                                                      path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", READ_ROUTES)
def test_every_read_route_answers_for_an_authenticated_tenant_user(
        ops_enabled, client, auth_headers, path):
    assert client.get(path, headers=auth_headers).status_code == 200


def test_reads_return_the_callers_own_organization_only(
        ops_enabled, client, db_session, sample_org, sample_lead,
        auth_headers):
    """A second customer's conversation is in the database and never appears.

    The routes take no organization parameter at all, so there is no argument
    anywhere on this surface that could widen what a caller sees. This is the
    test that the property holds in practice and not only in the signatures.
    """
    mine = own_thread(db_session, sample_org, sample_lead)
    _other_org, _other_lead, theirs = other_tenant_thread(db_session)

    listed = client.get("/ai-operations/threads", headers=auth_headers)
    assert listed.status_code == 200
    thread_ids = [row["thread_id"] for row in listed.json()["threads"]]
    assert mine.id in thread_ids
    assert theirs.id not in thread_ids

    comms = client.get("/ai-operations/communications", headers=auth_headers)
    assert comms.status_code == 200
    assert all(row["thread_id"] == mine.id
               for row in comms.json()["communications"])

    overview = client.get("/ai-operations/overview", headers=auth_headers)
    assert overview.json()["organization_id"] == sample_org.id
    assert overview.json()["open_threads"] == 1


def test_overview_has_the_shape_the_console_reads(ops_enabled, client,
                                                  db_session, sample_org,
                                                  sample_lead, auth_headers):
    """Counts first, then the state behind each count.

    `dark_launch` is asserted explicitly: "why is nothing sending" is a
    question with a factual answer, and this payload is where an operator
    reads it.
    """
    own_thread(db_session, sample_org, sample_lead)

    body = client.get("/ai-operations/overview", headers=auth_headers).json()
    for key in ("organization_id", "generated_at", "dark_launch", "providers",
                "workforce_contracts", "open_threads", "threads_by_state",
                "threads_by_group", "handoffs_waiting", "scheduled_pending",
                "unrouted_inbound_24h", "denials_24h", "budget"):
        assert key in body, key

    assert body["dark_launch"]["operations_enabled"] is True
    assert body["dark_launch"]["live_send_enabled"] is False
    assert body["dark_launch"]["live_voice_enabled"] is False
    assert body["dark_launch"]["kill_engaged"] is False
    assert body["providers"]["live_send_enabled"] is False
    # Every group in the console's vocabulary is present, so a group with no
    # conversations renders as zero rather than as a missing key.
    for key, _label, _states in C.COMM_GROUPS:
        assert key in body["threads_by_group"]
    assert body["threads_by_group"]["waiting"] == 1
    assert body["budget"]["organization"]["sms"] == 1


def test_another_organizations_thread_is_404_and_not_403(
        ops_enabled, client, db_session, sample_org, sample_lead,
        auth_headers):
    """404, DELIBERATELY. 403 would confirm the conversation exists.

    Every route that takes a thread id is checked, including the mutations:
    a boundary that held on the read and leaked on the stop would be no
    boundary at all.
    """
    own_thread(db_session, sample_org, sample_lead)
    _org, _lead, theirs = other_tenant_thread(db_session)

    assert client.get("/ai-operations/threads/%s" % theirs.id,
                      headers=auth_headers).status_code == 404
    for action in ("takeover", "release", "stop"):
        response = client.post("/ai-operations/threads/%s/%s"
                               % (theirs.id, action), json={},
                               headers=auth_headers)
        assert response.status_code == 404, action

    db_session.refresh(theirs)
    assert theirs.human_owner_user_id is None
    assert theirs.stop_reason is None
    assert theirs.status == "open"


# ═══════════════════════════════════════════════════════════════════════════
# THE CONTROLS
# ═══════════════════════════════════════════════════════════════════════════

def test_thread_detail_returns_the_conversation_its_history_and_activity(
        ops_enabled, client, db_session, sample_org, sample_lead,
        auth_headers):
    mine = own_thread(db_session, sample_org, sample_lead)

    body = client.get("/ai-operations/threads/%s" % mine.id,
                      headers=auth_headers).json()
    assert body["thread"]["thread_id"] == mine.id
    assert body["thread"]["state"] == C.WAITING_FOR_RESPONSE
    assert len(body["communications"]) == 1
    assert body["communications"][0]["channel"] == C.CHANNEL_SMS
    assert body["activity"], "an allowed operation must leave an audit trail"


def test_takeover_then_release_hands_the_conversation_back(
        ops_enabled, client, db_session, sample_org, sample_lead,
        sample_advisor, auth_headers):
    """A person takes it, the AI stops; they hand it back, and by DEFAULT the
    AI still does not resume.

    That default is the point of the release test: somebody who took a
    conversation over and finished with it has not thereby re-authorized
    automated outreach to that family.
    """
    mine = own_thread(db_session, sample_org, sample_lead)

    taken = client.post("/ai-operations/threads/%s/takeover" % mine.id,
                        json={"reason_code": "human_requested",
                              "note": "I will call them."},
                        headers=auth_headers)
    assert taken.status_code == 200
    assert taken.json()["ok"] is True
    assert taken.json()["ownership_id"]
    assert taken.json()["thread"]["human_owner_user_id"] == sample_advisor.id
    assert taken.json()["thread"]["state"] == C.HUMAN_OWNED

    released = client.post("/ai-operations/threads/%s/release" % mine.id,
                           json={}, headers=auth_headers)
    assert released.status_code == 200
    assert released.json()["ok"] is True
    thread = released.json()["thread"]
    assert thread["human_owner_user_id"] is None
    assert thread["state"] == C.COMPLETED
    assert thread["stop_reason"] == C.STOP_HUMAN_TAKEOVER


def test_release_with_an_explicit_yes_returns_the_work_to_the_employee(
        ops_enabled, client, db_session, sample_org, sample_lead,
        auth_headers):
    mine = own_thread(db_session, sample_org, sample_lead)
    client.post("/ai-operations/threads/%s/takeover" % mine.id, json={},
                headers=auth_headers)

    released = client.post("/ai-operations/threads/%s/release" % mine.id,
                           json={"ai_may_resume": True},
                           headers=auth_headers)
    assert released.status_code == 200
    thread = released.json()["thread"]
    assert thread["human_owner_user_id"] is None
    assert thread["state"] == C.QUEUED
    assert thread["stop_reason"] is None


def test_stopping_one_thread_records_the_reason_and_is_idempotent(
        ops_enabled, client, db_session, sample_org, sample_lead,
        auth_headers):
    """STOPPING IS MONOTONIC: the FIRST reason is the true one.

    A second stop is a no-op that keeps the original reason, so a tidy-up
    job cannot overwrite the fact that a person asked.
    """
    mine = own_thread(db_session, sample_org, sample_lead)

    first = client.post("/ai-operations/threads/%s/stop" % mine.id,
                        json={"reason": C.STOP_CONTACT_REQUESTED},
                        headers=auth_headers)
    assert first.status_code == 200
    assert first.json()["ok"] is True
    assert first.json()["thread"]["stop_reason"] == C.STOP_CONTACT_REQUESTED
    assert first.json()["thread"]["state"] == C.STOPPED

    second = client.post("/ai-operations/threads/%s/stop" % mine.id,
                         json={"reason": C.STOP_OBJECTIVE_COMPLETE},
                         headers=auth_headers)
    assert second.status_code == 200
    assert second.json()["ok"] is False          # nothing left to stop
    assert second.json()["thread"]["stop_reason"] == C.STOP_CONTACT_REQUESTED


def test_stop_all_stops_this_customers_conversations_and_nobody_elses(
        ops_enabled, client, db_session, sample_org, sample_lead,
        auth_headers):
    """Available to any user who can reach this surface, on purpose.

    The person who can see the AI working is the person who should be able
    to stop it; requiring an admin would mean whoever is watching it go
    wrong has to go and find one.
    """
    mine = own_thread(db_session, sample_org, sample_lead)
    _org, _lead, theirs = other_tenant_thread(db_session)

    response = client.post("/ai-operations/stop-all",
                           json={"reason": C.STOP_SUPERVISOR},
                           headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["threads_stopped"] == 1

    db_session.refresh(mine)
    db_session.refresh(theirs)
    assert mine.stop_reason == C.STOP_SUPERVISOR
    assert mine.status == "closed"
    assert theirs.stop_reason is None
    assert theirs.status == "open"


def test_pausing_an_employees_work_cancels_only_this_tenants_actions(
        ops_enabled, client, db_session, sample_org, sample_lead,
        auth_headers):
    """Cancels the queued ACTIONS, which is this layer's own state — not the
    employee's own pause switch, which belongs to the workforce engine's
    employee row. Two owners for one flag is how a pause stops meaning
    anything."""
    from datetime import timedelta

    from app.models.ai_operations_models import AIScheduledAction
    from app.services.ai_operations import followup

    ctx = declare(sample_org)
    sent = orchestrator.send_message(db_session, ctx,
                                     subject_id=sample_lead.id,
                                     body="First touch.",
                                     now=business_hours())
    followup.schedule(db_session, ctx, thread=sent.gate.thread,
                      operation=C.OP_SEND_MESSAGE,
                      when=business_hours() + timedelta(days=2),
                      channel=C.CHANNEL_SMS,
                      payload={"body": "Later."}, reason="cadence")
    db_session.commit()

    response = client.post("/ai-operations/employees/%s/pause-work"
                           % ctx.employee_id, json={"reason": "operator"},
                           headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["cancelled_actions"] == 1
    assert (db_session.query(AIScheduledAction)
            .filter(AIScheduledAction.status == "pending").count()) == 0


# ═══════════════════════════════════════════════════════════════════════════
# GOD MODE
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("path", GOD_READ_ROUTES)
def test_god_routes_are_refused_for_a_tenant_user(ops_enabled, client,
                                                  auth_headers, path):
    """`require_god` is the same dependency every other platform-control
    route uses, and it answers 403. Asserted as what the application
    actually does rather than as what a convention elsewhere suggests."""
    assert client.get(path, headers=auth_headers).status_code == 403


@pytest.mark.parametrize("path", GOD_READ_ROUTES)
def test_god_routes_are_refused_for_an_org_admin(ops_enabled, client,
                                                 admin_auth_headers, path):
    """Being the most senior person INSIDE a customer is not platform
    authority. This is the escalation the gate exists to refuse."""
    assert client.get(path, headers=admin_auth_headers).status_code == 403


@pytest.mark.parametrize("path", GOD_READ_ROUTES)
def test_god_routes_answer_for_a_god_user(ops_enabled, client, db_session,
                                          path):
    assert client.get(path, headers=god_headers(db_session)).status_code == 200


def test_god_state_reports_the_dark_launch_and_the_operation_authority_map(
        ops_enabled, client, db_session):
    """Why nothing is sending, in one factual answer — plus the mapping that
    makes "no operation invents its own permission" checkable rather than
    asserted."""
    body = client.get("/god/ai-operations/state",
                      headers=god_headers(db_session)).json()

    assert body["dark_launch"]["operations_enabled"] is True
    assert body["dark_launch"]["live_send_enabled"] is False
    assert body["dark_launch"]["live_voice_enabled"] is False
    assert sorted(body["operations"]) == sorted(C.ALL_OPERATIONS)
    # EVERY operation maps to a workforce tool key. An operation with no
    # mapping would be unauthorized by construction, which is the safe
    # direction — but it would also be dead, and that is worth knowing.
    assert all(body["operation_authority"][op] for op in C.ALL_OPERATIONS)


def test_simulate_without_the_synthetic_data_acknowledgement_is_400(
        ops_enabled, client, db_session):
    """Creating synthetic organizations in a deployment is a deliberate act,
    not something a misdirected click should do."""
    headers = god_headers(db_session)

    refused = client.post("/god/ai-operations/simulate", json={},
                          headers=headers)
    assert refused.status_code == 400
    assert "confirm_synthetic_data" in refused.json()["detail"]

    also_refused = client.post("/god/ai-operations/simulate",
                               json={"confirm_synthetic_data": False,
                                     "profile": "reactivation"},
                               headers=headers)
    assert also_refused.status_code == 400

    # Nothing was created by either refusal.
    assert (db_session.query(Organization)
            .filter(Organization.slug.like("%s%%" % profiles.SYNTHETIC_PREFIX))
            .count()) == 0


def test_simulate_with_the_acknowledgement_runs_one_scenario(
        ops_enabled, client, db_session):
    """The proof runs through the real engine, and everything it did is
    marked simulated. `dark_launch` comes back with it so the reader can see
    that nothing could have left the process."""
    response = client.post(
        "/god/ai-operations/simulate",
        json={"confirm_synthetic_data": True, "profile": "reactivation",
              "scenario": "opt_out"},
        headers=god_headers(db_session))

    assert response.status_code == 200
    body = response.json()
    assert body["dark_launch"]["live_send_enabled"] is False
    assert body["profile"]["organization_name"].endswith(
        profiles.SYNTHETIC_MARKER)
    assert len(body["reports"]) == 1
    report = body["reports"][0]
    assert report["thread"]["stop_reason"] == C.STOP_OPT_OUT
    assert report["thread"]["state"] == C.STOPPED
