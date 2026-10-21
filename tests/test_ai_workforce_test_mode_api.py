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
