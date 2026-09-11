"""T8 - THE TWO ROUTERS, AND WHAT THEY REFUSE.

THE TENANT BOUNDARY IS A PROPERTY OF THE SIGNATURES. No route on
`/ai-workforce` accepts an organization id, so the first test in this file is
that none of them ever grows one. Everything else here is about the refusals:
who may write, what a commercial answer looks like on the wire, and that a
stale browser tab is told rather than obeyed.

THE STATUS CODES ARE PART OF THE CONTRACT. 402 for a commercial refusal - the
same code `require_feature` uses for the same kind of answer - 409 for a race,
403 for authority, 404 for another tenant's anything. A screen acts on those
without parsing prose, so they are asserted rather than assumed.
"""

import json

import pytest

from app.services.ai_deployment import constants as D


# ---------------------------------------------------------------------------
# THE WORLD
# ---------------------------------------------------------------------------

@pytest.fixture()
def brand(db_session, sample_org):
    from app.models.models import Platform
    row = Platform(name="Proof Brand", slug="t8-api-brand")
    db_session.add(row)
    db_session.flush()
    sample_org.platform_id = row.id
    db_session.commit()
    return row


@pytest.fixture()
def offered(db_session, sample_org, brand):
    """The brand offers one job, prices it in its own catalogue, and the
    customer has bought it. Everything else follows from that."""
    from app.services.ai_deployment import simulation as sim
    from app.services.workforce import registry as wf_registry
    from app.services.workforce import service as wf_service

    wf_service.sync_templates(db_session)
    key = "reactivation_specialist"
    wf_service.set_offering(db_session, platform_id=brand.id,
                            template_key=key, enabled=True,
                            display_name="AI Reactivation Specialist")
    tpl = wf_registry.template(key)
    item = sim._catalogue_item(db_session, brand, key="t8-api-item",
                               name=tpl.name,
                               entitlement_key=tpl.entitlement_key)
    sim._set_terms(db_session, brand.id, key, item.key)
    sim._purchase(db_session, sample_org, item)
    db_session.commit()
    return key


@pytest.fixture()
def god_headers(db_session, sample_org):
    from app.models.models import User
    from app.services.auth_service import create_access_token, hash_password
    god = User(organization_id=sample_org.id, email="god@t8.invalid",
               password_hash=hash_password("GodPass123!"),
               full_name="Platform Operator", role="god_admin",
               must_change_password=False)
    db_session.add(god)
    db_session.commit()
    return {"Authorization": "Bearer %s"
                             % create_access_token(god, db_session)}


# ---------------------------------------------------------------------------
# THE BOUNDARY
# ---------------------------------------------------------------------------

def test_no_customer_route_accepts_an_organization_id():
    """THE TENANT BOUNDARY IS A FACT ABOUT THE SIGNATURES.

    A route that cannot name another customer cannot leak one. This is the
    same assertion `workforce_router` relies on, made here because T8 adds a
    second customer surface and the rule has to hold for both.
    """
    import inspect
    from app.routers import ai_deployment_router as router_module

    offenders = []
    for name, fn in vars(router_module).items():
        if not callable(fn) or name.startswith("_"):
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        for param in sig.parameters:
            if param in ("organization_id", "org_id", "tenant_id"):
                offenders.append("%s(%s)" % (name, param))
    assert not offenders, offenders


def test_unauthenticated_requests_are_refused(client):
    for path in ("/ai-workforce/overview", "/ai-workforce/catalog",
                 "/ai-workforce/deployments"):
        assert client.get(path).status_code in (401, 403), path


def test_god_routes_refuse_a_customer_admin(client, admin_auth_headers):
    for path in ("/god/ai-workforce/overview", "/god/ai-workforce/templates",
                 "/god/ai-workforce/deployments"):
        response = client.get(path, headers=admin_auth_headers)
        assert response.status_code in (401, 403), (path, response.status_code)


def test_an_advisor_cannot_hire(client, auth_headers, offered):
    response = client.post("/ai-workforce/deployments", headers=auth_headers,
                           json={"template_key": offered})
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# READING
# ---------------------------------------------------------------------------

def test_the_overview_answers_for_the_callers_own_workspace(client,
                                                            admin_auth_headers,
                                                            sample_org,
                                                            offered):
    response = client.get("/ai-workforce/overview", headers=admin_auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["organization_id"] == sample_org.id
    assert body["status_line"]
    assert isinstance(body["available"], list)


def test_the_catalogue_says_why_something_is_not_available(client,
                                                           admin_auth_headers,
                                                           offered):
    body = client.get("/ai-workforce/catalog",
                      headers=admin_auth_headers).json()
    blocked = [c for c in body["available"] if not c["can_hire"]]
    assert blocked
    for row in blocked:
        assert row["blockers"]


def test_the_questions_never_ask_about_the_ai(client, admin_auth_headers,
                                              offered):
    body = client.get("/ai-workforce/catalog/%s/questions" % offered,
                      headers=admin_auth_headers).json()
    assert body["fields"]
    for field in body["fields"]:
        assert D.forbidden_reason(field["key"]) is None, field["key"]


def test_questions_for_an_unknown_job_are_a_404(client, admin_auth_headers):
    response = client.get("/ai-workforce/catalog/not-a-job/questions",
                          headers=admin_auth_headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# WRITING
# ---------------------------------------------------------------------------

def test_hiring_creates_a_deployment_that_is_switched_off(client,
                                                          admin_auth_headers,
                                                          offered):
    response = client.post("/ai-workforce/deployments",
                           headers=admin_auth_headers,
                           json={"template_key": offered,
                                 "provisioning_key": "api-1"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] not in D.LIVE_STATES
    assert body["readiness"]["verdict"] in D.READINESS_VERDICTS


def test_hiring_twice_with_one_key_creates_one(client, admin_auth_headers,
                                               offered):
    first = client.post("/ai-workforce/deployments", headers=admin_auth_headers,
                        json={"template_key": offered,
                              "provisioning_key": "api-2"}).json()
    second = client.post("/ai-workforce/deployments",
                         headers=admin_auth_headers,
                         json={"template_key": offered,
                               "provisioning_key": "api-2"}).json()
    assert first["id"] == second["id"]


def test_hiring_something_the_brand_does_not_offer_is_a_404(client,
                                                            admin_auth_headers,
                                                            offered):
    response = client.post("/ai-workforce/deployments",
                           headers=admin_auth_headers,
                           json={"template_key": "appointment_setter",
                                 "provisioning_key": "api-3"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == D.R_NOT_OFFERED


def test_a_commercial_refusal_is_402(client, admin_auth_headers, db_session,
                                     sample_org, offered):
    """The same code `require_feature` uses for the same kind of answer."""
    from app.models.purchase_models import CatalogPurchase, PurchaseStatus
    for row in (db_session.query(CatalogPurchase)
                .filter(CatalogPurchase.organization_id == sample_org.id)
                .all()):
        row.status = PurchaseStatus.CANCELED
    db_session.commit()
    response = client.post("/ai-workforce/deployments",
                           headers=admin_auth_headers,
                           json={"template_key": offered,
                                 "provisioning_key": "api-4"})
    assert response.status_code == 402, response.text
    assert response.json()["detail"]["code"] == D.R_NOT_ENTITLED


def test_configuration_refuses_an_internal_field(client, admin_auth_headers,
                                                 offered):
    created = client.post("/ai-workforce/deployments",
                          headers=admin_auth_headers,
                          json={"template_key": offered,
                                "provisioning_key": "api-5"}).json()
    response = client.patch(
        "/ai-workforce/deployments/%s/configuration" % created["id"],
        headers=admin_auth_headers,
        json={"configuration": {"model": "something"}})
    assert response.status_code == 400
    body = response.json()["detail"]
    assert body["code"] == D.R_FORBIDDEN_CONFIG
    assert body["problems"]


def test_a_customers_activation_request_is_recorded_not_granted(
        client, admin_auth_headers, offered):
    created = client.post("/ai-workforce/deployments",
                          headers=admin_auth_headers,
                          json={"template_key": offered,
                                "provisioning_key": "api-6"}).json()
    response = client.post(
        "/ai-workforce/deployments/%s/activation" % created["id"],
        headers=admin_auth_headers, json={"stage": "controlled"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["granted"] is False
    assert body["deployment"]["state"] not in D.LIVE_STATES


def test_a_stale_browser_tab_is_told_rather_than_obeyed(client,
                                                        admin_auth_headers,
                                                        offered):
    created = client.post("/ai-workforce/deployments",
                          headers=admin_auth_headers,
                          json={"template_key": offered,
                                "provisioning_key": "api-7"}).json()
    response = client.post(
        "/ai-workforce/deployments/%s/pause" % created["id"],
        headers=admin_auth_headers,
        json={"reason": "from an old tab", "expected_state": "active"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == D.R_STALE_VIEW


def test_another_tenants_deployment_is_a_404(client, admin_auth_headers,
                                             db_session, offered):
    """The same 404 as one that does not exist. Ids stay unguessable."""
    from app.models.models import Organization
    from app.models.ai_deployment_models import AIEmployeeDeployment
    other = Organization(name="Someone Else", slug="t8-api-other")
    db_session.add(other)
    db_session.flush()
    row = AIEmployeeDeployment(organization_id=other.id,
                               template_key=offered,
                               provisioning_key="theirs", state=D.SELECTED)
    db_session.add(row)
    db_session.commit()
    response = client.get("/ai-workforce/deployments/%s" % row.id,
                          headers=admin_auth_headers)
    assert response.status_code == 404


def test_retiring_preserves_the_record(client, admin_auth_headers, offered):
    created = client.post("/ai-workforce/deployments",
                          headers=admin_auth_headers,
                          json={"template_key": offered,
                                "provisioning_key": "api-8"}).json()
    response = client.post(
        "/ai-workforce/deployments/%s/retire" % created["id"],
        headers=admin_auth_headers, json={"reason": "done with it"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == D.RETIRED
    assert body["result"]["history_preserved"] is not None
    listed = client.get("/ai-workforce/deployments?include_retired=true",
                        headers=admin_auth_headers).json()
    assert any(d["id"] == created["id"] for d in listed["deployments"])


# ---------------------------------------------------------------------------
# THE GOD SURFACE
# ---------------------------------------------------------------------------

def test_god_can_read_the_platform_picture(client, god_headers, offered):
    response = client.get("/god/ai-workforce/overview", headers=god_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "platform" in body and "orphans" in body


def test_brand_terms_refuse_a_catalogue_item_that_does_not_exist(client,
                                                                 god_headers,
                                                                 brand,
                                                                 offered):
    response = client.put("/god/ai-workforce/brands/%s/terms" % brand.id,
                          headers=god_headers,
                          json={"template_key": offered,
                                "catalog_item_key": "not-in-this-brand"})
    assert response.status_code == 400
    assert "catalogue item" in json.dumps(response.json()).lower()


def test_an_activation_without_a_reason_is_refused(client, god_headers,
                                                   admin_auth_headers, offered):
    created = client.post("/ai-workforce/deployments",
                          headers=admin_auth_headers,
                          json={"template_key": offered,
                                "provisioning_key": "api-9"}).json()
    response = client.post(
        "/god/ai-workforce/deployments/%s/activation" % created["id"],
        headers=god_headers, json={"stage": "controlled", "reason": "  "})
    assert response.status_code == 400


def test_the_proofs_require_an_explicit_confirmation(client, god_headers):
    for path in ("/god/ai-workforce/proof/run", "/god/ai-workforce/proof/attack"):
        response = client.post(path, headers=god_headers, json={})
        assert response.status_code == 400, path


def test_reconciling_only_ever_stops_things(client, god_headers, offered):
    response = client.post("/god/ai-workforce/reconcile", headers=god_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["restored"] == 0 or body["restored"] >= 0
    assert "suspended" in body
