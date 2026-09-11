"""AI WORKFORCE — the API surface, and who may reach it.

THE THREE QUESTIONS THIS FILE ANSWERS:

  1. Do the routes exist and work? A router built and never registered is the
     defect `test_god_navigation.py` was written for, and it is invisible to
     every test that exercises what is there.
  2. Can the wrong person reach them? God routes are god-only. Customer writes
     are admin-only. Observation mode cannot mutate.
  3. Can a customer see another customer's team? No route here accepts an
     organization id, so the test is that the customer surface answers only
     for the caller's own workspace.

NOTHING IN THIS FILE SWITCHES ANYTHING ON. Every employee created here stays
at `off`, which is also the assertion in `test_a_new_employee_is_created_off`.
"""

import itertools
import json

import pytest

from app.models.models import Organization, Platform, User
from app.models.workforce_models import AIEmployee
from app.services.auth_service import create_access_token, hash_password
from app.services.workforce import constants as C
from app.services.workforce import service as wf_service

_SEQ = itertools.count(700000)


# ── fixtures ────────────────────────────────────────────────────────────────

def _headers(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _god(db):
    u = User(organization_id=None,
             email="wfgod%d@example.invalid" % next(_SEQ),
             password_hash=hash_password("GodPass123!"), full_name="God",
             role="god_admin", must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _brand(db, slug_hint="wfroutes"):
    p = Platform(name="Workforce Routes Brand",
                 slug="%s-%d" % (slug_hint, next(_SEQ)), is_active=True)
    db.add(p)
    db.commit()
    return p


def _customer(db, brand):
    org = Organization(name="Workforce Routes Customer",
                       slug="wfroutes-org-%d" % next(_SEQ),
                       platform_id=brand.id, plan="standard",
                       enabled_features=json.dumps(
                           ["leads", "sms", "email", "booking", "calendar",
                            "crm", "compliance", "ai_assist"]))
    db.add(org)
    db.commit()
    return org


def _user(db, org, role="advisor"):
    u = User(organization_id=org.id,
             email="wfuser%d@example.invalid" % next(_SEQ),
             password_hash=hash_password("UserPass123!"),
             full_name="Workforce User", role=role,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _offer(db, brand, template_key="reactivation_specialist"):
    wf_service.sync_templates(db)
    wf_service.set_offering(db, platform_id=brand.id,
                            template_key=template_key, enabled=True)
    db.commit()


def _hire(db, org, actor, template_key="reactivation_specialist"):
    emp = wf_service.hire(db, organization_id=org.id,
                          template_key=template_key, actor=actor,
                          operating_hours={"days": [0, 1, 2, 3, 4],
                                           "start": "09:00", "end": "17:00"})
    db.commit()
    return emp


# ═══════════════════════════════════════════════════════════════════════════
# THE ROUTES EXIST
# ═══════════════════════════════════════════════════════════════════════════

def test_the_workforce_routers_are_registered():
    """A router that is built and never included answers 404 forever."""
    from app.main import app
    paths = {r.path for r in app.routes}
    for expected in ("/workforce/team", "/workforce/catalogue",
                     "/workforce/queue", "/workforce/handoffs",
                     "/workforce/performance",
                     "/god/workforce/overview", "/god/workforce/tools",
                     "/god/workforce/activation",
                     "/god/workforce/templates"):
        assert expected in paths, "%s is not registered" % expected


def test_customer_team_answers_for_the_callers_own_workspace(
        client, db_session, sample_org, sample_advisor):
    r = client.get("/workforce/team", headers=_headers(db_session,
                                                       sample_advisor))
    assert r.status_code == 200
    body = r.json()
    assert body["organization_id"] == sample_org.id
    # DARK BY DEFAULT, and the screen says so in words.
    assert body["activation"]["state"] == C.OFF
    assert not body["activation"]["may_execute"]
    assert "switched off" in body["status_line"]


def test_the_customer_surface_takes_no_organization_id():
    """A route that cannot name another customer cannot leak one."""
    from app.routers import workforce_router
    for route in workforce_router.router.routes:
        params = set(getattr(route, "param_convertors", {}) or {})
        assert "organization_id" not in params, (
            "%s accepts an organization id" % route.path)
        assert "org_id" not in params


def test_catalogue_shows_unavailable_jobs_honestly(client, db_session,
                                                   sample_org,
                                                   sample_advisor):
    from app.services.workforce import registry
    wf_service.sync_templates(db_session)
    db_session.commit()
    r = client.get("/workforce/catalogue",
                   headers=_headers(db_session, sample_advisor))
    assert r.status_code == 200
    rows = r.json()["available"]
    assert rows, "the catalogue was empty"
    keys = {row["template_key"] for row in rows}
    assert "reactivation_specialist" in keys
    # Nothing is purchasable yet, so nothing is available — and each row says
    # why rather than simply being missing.
    assert all(not row["available"] for row in rows)
    assert all(row["entitlement"]["detail"] for row in rows)
    assert set(keys) <= set(registry.ALL_TEMPLATE_KEYS)


# ═══════════════════════════════════════════════════════════════════════════
# AUTHORIZATION
# ═══════════════════════════════════════════════════════════════════════════

def test_god_workforce_refuses_a_tenant_admin(client, db_session, sample_org):
    admin = _user(db_session, sample_org, role="org_admin")
    r = client.get("/god/workforce/overview",
                   headers=_headers(db_session, admin))
    assert r.status_code == 403


def test_god_workforce_refuses_a_super_admin(client, db_session, sample_org):
    """super_admin is a PLATFORM operator and still not root."""
    su = _user(db_session, sample_org, role="super_admin")
    r = client.get("/god/workforce/activation",
                   headers=_headers(db_session, su))
    assert r.status_code == 403


def test_god_workforce_refuses_an_anonymous_caller(client):
    assert client.get("/god/workforce/overview").status_code == 401


def test_god_can_read_the_overview(client, db_session):
    god = _god(db_session)
    r = client.get("/god/workforce/overview", headers=_headers(db_session, god))
    assert r.status_code == 200
    body = r.json()
    assert body["dark_launch"]["platform_stage"] in (C.OFF, C.SIMULATION)
    assert body["dark_launch"]["live_voice_enabled"] is False


def test_hiring_requires_an_admin(client, db_session, sample_org,
                                  sample_advisor):
    r = client.post("/workforce/team",
                    json={"template_key": "reactivation_specialist"},
                    headers=_headers(db_session, sample_advisor))
    assert r.status_code == 403


def test_hiring_without_entitlement_is_402(client, db_session, sample_org):
    admin = _user(db_session, sample_org, role="org_admin")
    wf_service.sync_templates(db_session)
    db_session.commit()
    r = client.post("/workforce/team",
                    json={"template_key": "reactivation_specialist"},
                    headers=_headers(db_session, admin))
    assert r.status_code == 402


def test_hiring_an_unknown_job_is_404(client, db_session, sample_org):
    admin = _user(db_session, sample_org, role="org_admin")
    r = client.post("/workforce/team",
                    json={"template_key": "chief_vibes_officer"},
                    headers=_headers(db_session, admin))
    assert r.status_code == 404


def test_god_may_hire_past_the_entitlement_gate(client, db_session):
    """The owner operating inside a customer is not the customer.

    Same reasoning as `require_feature`: an operator must be able to configure
    something the customer has not bought yet, which is how it gets set up
    before it is sold.
    """
    god = _god(db_session)
    brand = _brand(db_session)
    org = _customer(db_session, brand)
    _offer(db_session, brand)
    headers = dict(_headers(db_session, god))
    headers["X-Org-Override"] = org.id
    r = client.post("/workforce/team",
                    json={"template_key": "reactivation_specialist",
                          "name": "Reactivation"},
                    headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "draft"


# ═══════════════════════════════════════════════════════════════════════════
# THE EMPLOYEE LIFECYCLE THROUGH THE API
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def hired(db_session):
    brand = _brand(db_session)
    org = _customer(db_session, brand)
    admin = _user(db_session, org, role="org_admin")
    _offer(db_session, brand)
    emp = _hire(db_session, org, admin)
    return {"brand": brand, "org": org, "admin": admin, "employee": emp}


def test_a_new_employee_is_created_off(hired):
    assert hired["employee"].status == "draft"
    assert hired["employee"].activation_state == C.OFF


def test_employee_detail_speaks_business_language(client, db_session, hired):
    r = client.get("/workforce/employees/%s" % hired["employee"].id,
                   headers=_headers(db_session, hired["admin"]))
    assert r.status_code == 200
    body = r.json()
    assert body["job_title"]
    assert body["what_it_does"]
    assert isinstance(body["can_do"], list) and body["can_do"]
    assert all("label" in item for item in body["can_do"])
    # NO DEVELOPER INTERNALS ON THE CUSTOMER SURFACE — section 51.
    flat = json.dumps(body).lower()
    for forbidden in ("system_prompt", "temperature", "agent graph",
                      "token budget", "vector namespace"):
        assert forbidden not in flat


def test_another_customer_cannot_read_the_employee(client, db_session, hired,
                                                   sample_org):
    stranger = _user(db_session, sample_org, role="org_admin")
    r = client.get("/workforce/employees/%s" % hired["employee"].id,
                   headers=_headers(db_session, stranger))
    # 404, not 403 — the same answer as an id that does not exist, so this
    # cannot be used to find out which ids are real.
    assert r.status_code == 404


def test_a_customer_may_not_switch_itself_live(client, db_session, hired):
    r = client.post("/workforce/employees/%s/activation" % hired["employee"].id,
                    json={"state": C.ACTIVE},
                    headers=_headers(db_session, hired["admin"]))
    assert r.status_code == 403
    assert "AdvisorFlow" in r.json()["detail"]


def test_a_customer_may_switch_to_simulation(client, db_session, hired):
    r = client.post("/workforce/employees/%s/activation" % hired["employee"].id,
                    json={"state": C.SIMULATION, "reason": "trying it out"},
                    headers=_headers(db_session, hired["admin"]))
    assert r.status_code == 200
    # The employee's own stage moved; the EFFECTIVE stage is still off,
    # because the platform scope is off and the minimum wins.
    assert r.json()["activation"]["state"] == C.OFF


def test_pause_and_resume(client, db_session, hired):
    emp_id = hired["employee"].id
    headers = _headers(db_session, hired["admin"])
    r = client.post("/workforce/employees/%s/pause" % emp_id,
                    json={"reason": "not today"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["paused"] is True
    r = client.post("/workforce/employees/%s/resume" % emp_id, headers=headers)
    assert r.status_code == 200
    assert r.json()["paused"] is False


def test_configuration_is_business_shaped(client, db_session, hired):
    r = client.patch("/workforce/employees/%s" % hired["employee"].id,
                     json={"name": "Renamed",
                           "config": {"goal": "book consultations"},
                           "operating_hours": {"days": [0, 1], "start": "10:00",
                                               "end": "16:00"},
                           "daily_work_cap": 25},
                     headers=_headers(db_session, hired["admin"]))
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Renamed"
    assert body["daily_work_cap"] == 25
    assert body["operating_hours"]["start"] == "10:00"


def test_assigning_records_is_scoped_to_what_the_admin_can_see(
        client, db_session, hired, sample_lead):
    """A lead from another tenant is not in the authorized set at all."""
    r = client.post("/workforce/employees/%s/assign" % hired["employee"].id,
                    json={"lead_ids": [sample_lead.id]},
                    headers=_headers(db_session, hired["admin"]))
    # `assert_leads_in_scope` refuses the WHOLE batch rather than silently
    # dropping the ids the caller may not touch.
    assert r.status_code in (403, 404)


def test_queue_and_performance_read_cleanly(client, db_session, hired):
    headers = _headers(db_session, hired["admin"])
    r = client.get("/workforce/queue", headers=headers)
    assert r.status_code == 200
    assert {g["key"] for g in r.json()["groups"]} == {
        key for key, _label, _states in C.QUEUE_GROUPS}
    r = client.get("/workforce/performance", headers=headers)
    assert r.status_code == 200
    body = r.json()["report"]
    # NO INVENTED MONEY — section 27.
    assert body["revenue"] is None
    assert "does not attribute revenue" in body["revenue_note"]


def test_handoffs_list_is_empty_and_valid(client, db_session, hired):
    r = client.get("/workforce/handoffs",
                   headers=_headers(db_session, hired["admin"]))
    assert r.status_code == 200
    assert r.json()["handoffs"] == []
