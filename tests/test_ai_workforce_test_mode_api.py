"""The customer's test-mode buttons, over HTTP, as the screen calls them.

Hire -> set up -> look at the page -> Run test -> PASS, all through
`/ai-workforce/...` with a real customer admin's token. Plus the refusals that
matter on the wire: an advisor cannot press the buttons, another account's
admin gets 404 (not 403 - a 403 would confirm the id exists), and no route
takes an organization id.
"""

import json
from datetime import datetime

import pytest

from app.services.workforce import activation as wf_activation
from app.services.workforce import constants as WC


@pytest.fixture()
def lq_offered(db_session, sample_org):
    from app.models.models import Platform
    from app.services.ai_deployment import simulation as sim
    from app.services.workforce import registry as wf_registry
    from app.services.workforce import service as wf_service
    brand = Platform(name="TM API Brand", slug="tm-api-brand")
    db_session.add(brand)
    db_session.flush()
    sample_org.platform_id = brand.id
    sample_org.enabled_features = json.dumps(sim.FEATURES)
    wf_service.sync_templates(db_session)
    wf_service.set_offering(db_session, platform_id=brand.id,
                            template_key="lead_qualifier", enabled=True)
    tpl = wf_registry.template("lead_qualifier")
    item = sim._catalogue_item(db_session, brand, key="tm-api-lq",
                               name=tpl.name,
                               entitlement_key=tpl.entitlement_key)
    sim._set_terms(db_session, brand.id, "lead_qualifier", item.key)
    sim._purchase(db_session, sample_org, item)
    for scope, sid in ((wf_activation.SCOPE_PLATFORM, ""),
                       (wf_activation.SCOPE_BRAND, brand.id),
                       (wf_activation.SCOPE_CUSTOMER, sample_org.id)):
        wf_activation.set_state(db_session, scope, sid, WC.SIMULATION,
                                reason="test mode allowed for this test")
    db_session.commit()
    return brand


def _hire_and_set_up(client, headers, db_session, sample_org):
    from app.models.models import User
    admin = (db_session.query(User)
             .filter(User.organization_id == sample_org.id,
                     User.role == "org_admin").first())
    r = client.post("/ai-workforce/deployments", headers=headers,
                    json={"template_key": "lead_qualifier",
                          "provisioning_key": "tm-api"})
    assert r.status_code == 201, r.text
    dep_id = r.json()["id"]
    every_day = {"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00",
                 "end": "23:59"}
    r = client.patch("/ai-workforce/deployments/%s/configuration" % dep_id,
                     headers=headers, json={"configuration": {
                         "goal": "Find out which new leads are ready.",
                         "audience": {"statuses": ["new"]},
                         "channels": ["sms", "email"],
                         "hours": every_day, "working_hours": every_day,
                         "timezone": "America/Chicago",
                         "good_lead": ["Wants a quote soon"],
                         "handoff_to": admin.id,
                         "handoff_user_id": admin.id}})
    assert r.status_code == 200, r.text
    return dep_id


def test_run_test_over_http_passes(client, admin_auth_headers, db_session,
                                   sample_org, lq_offered):
    dep_id = _hire_and_set_up(client, admin_auth_headers, db_session,
                              sample_org)
    page = client.get("/ai-workforce/deployments/%s/test-mode" % dep_id,
                      headers=admin_auth_headers)
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["can_run_test"] is True, body["test_blockers"]
    assert body["live_messages"] is False
    assert body["board"]["has_any_work"] is False

    r = client.post("/ai-workforce/deployments/%s/test-mode/run" % dep_id,
                    headers=admin_auth_headers)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["verdict"] == "PASS", report["problems"]
    assert report["run_ids"]

    after = client.get("/ai-workforce/deployments/%s/test-mode" % dep_id,
                       headers=admin_auth_headers).json()
    assert after["last_test"]["verdict"] == "PASS"
    assert after["board"]["has_any_work"] is True
    handoffs = [g for g in after["board"]["groups"] if g["key"] == "handoffs"]
    assert handoffs[0]["count"] == 1
    assert after["board"]["items"][0]["practice"] is True


def test_start_without_a_running_worker_is_refused_with_a_reason(
        client, admin_auth_headers, db_session, sample_org, lq_offered):
    dep_id = _hire_and_set_up(client, admin_auth_headers, db_session,
                              sample_org)
    r = client.post("/ai-workforce/deployments/%s/test-mode/start" % dep_id,
                    headers=admin_auth_headers)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["started"] is False
    assert "worker_not_running" in [b["code"] for b in result["blockers"]]


def test_an_advisor_cannot_press_the_buttons(client, admin_auth_headers,
                                             auth_headers, db_session,
                                             sample_org, lq_offered):
    dep_id = _hire_and_set_up(client, admin_auth_headers, db_session,
                              sample_org)
    for action in ("run", "start", "pause", "resume", "stop"):
        r = client.post("/ai-workforce/deployments/%s/test-mode/%s"
                        % (dep_id, action), headers=auth_headers)
        assert r.status_code in (401, 403), action
    # Looking is allowed.
    assert client.get("/ai-workforce/deployments/%s/test-mode" % dep_id,
                      headers=auth_headers).status_code == 200


def test_another_accounts_admin_gets_404(client, admin_auth_headers,
                                         db_session, sample_org, lq_offered):
    from app.models.models import Organization, User
    from app.services.auth_service import create_access_token, hash_password
    dep_id = _hire_and_set_up(client, admin_auth_headers, db_session,
                              sample_org)
    other = Organization(name="Other Co", slug="tm-other-co",
                         plan="standard")
    db_session.add(other)
    db_session.flush()
    stranger = User(organization_id=other.id, email="boss@other.invalid",
                    password_hash=hash_password("OtherPass123!"),
                    full_name="Other Boss", role="org_admin",
                    must_change_password=False)
    db_session.add(stranger)
    db_session.commit()
    h = {"Authorization": "Bearer %s"
                          % create_access_token(stranger, db_session)}
    assert client.get("/ai-workforce/deployments/%s/test-mode" % dep_id,
                      headers=h).status_code == 404
    for action in ("run", "start", "stop"):
        assert client.post("/ai-workforce/deployments/%s/test-mode/%s"
                           % (dep_id, action), headers=h).status_code == 404


def test_no_test_mode_route_takes_an_organization_id():
    from app.routers import ai_deployment_router
    for route in ai_deployment_router.router.routes:
        if "test-mode" not in route.path:
            continue
        assert "organization" not in route.path
        names = {p.name for p in route.dependant.query_params}
        assert not any("org" in n for n in names), route.path


def test_home_is_one_honest_payload(client, admin_auth_headers, auth_headers,
                                    db_session, sample_org, lq_offered):
    r = client.get("/ai-workforce/home", headers=admin_auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    cards = {c["key"]: c for c in body["jobs"]}
    assert list(cards) == ["lead_qualifier", "appointment_setter",
                           "follow_up_specialist", "customer_care_agent"]
    assert cards["lead_qualifier"]["availability"] == "available"
    assert cards["lead_qualifier"]["can_set_up"] is True
    # Not verified end to end yet -> labelled truthfully, not offered as working.
    for key in ("appointment_setter", "follow_up_specialist",
                "customer_care_agent"):
        assert cards[key]["can_set_up"] is False
        assert cards[key]["availability"] in ("coming_soon", "not_offered",
                                              "not_on_plan")
    assert body["workers"] == [] and body["can_manage"] is True
    rate = body["results"]["real"]["ready_rate"]
    assert rate["shown"] is False and rate["value"] is None
    assert "at least 5" in rate["how"]

    dep_id = _hire_and_set_up(client, admin_auth_headers, db_session,
                              sample_org)
    client.post("/ai-workforce/deployments/%s/test-mode/run" % dep_id,
                headers=admin_auth_headers)
    body = client.get("/ai-workforce/home", headers=admin_auth_headers).json()
    assert len(body["workers"]) == 1
    assert body["results"]["practice"]["qualified"] == 1
    assert body["results"]["real"]["finished"] == 0
    kinds = [n["kind"] for n in body["needs_you"]]
    assert "handoff" in kinds
    handoff = [n for n in body["needs_you"] if n["kind"] == "handoff"][0]
    assert handoff["practice"] is True and handoff["what"] and handoff["next"]
    assert [c for c in body["jobs"] if c["key"] == "lead_qualifier"][0][
        "availability"] == "set_up"

    # An advisor can see it, and is told they cannot manage it.
    adv = client.get("/ai-workforce/home", headers=auth_headers)
    assert adv.status_code == 200 and adv.json()["can_manage"] is False


def test_admin_health_reports_the_queue_worker_truthfully(client, db_session,
                                                           sample_org,
                                                           monkeypatch):
    from app.models.job_models import JobRun
    from app.models.models import User
    from app.services.auth_service import create_access_token, hash_password
    god = User(organization_id=sample_org.id, email="god@tm.invalid",
               password_hash=hash_password("GodPass123!"),
               full_name="Operator", role="god_admin",
               must_change_password=False)
    db_session.add(god)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(god, db_session)}
    body = client.get("/god/workforce/health", headers=h).json()
    assert body["queue_worker"]["running"] is False
    assert body["queue_worker"]["last_run_at"] is None
    db_session.add(JobRun(job_name="ai_workforce_worker",
                          started_at=datetime.utcnow(), status="success",
                          metrics={"claimed": 0}))
    db_session.commit()
    body = client.get("/god/workforce/health", headers=h).json()
    assert body["queue_worker"]["running"] is True
