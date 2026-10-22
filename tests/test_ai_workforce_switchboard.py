"""The God Mode switchboard: one click turns safe test mode on for real brands.

Proves the production gap is closed: a brand with NOTHING configured (no
offering, no terms, no purchase, every scope off - exactly what production
had) becomes usable by a customer through the switchboard alone, and the
customer's Run test passes on the real worker.
"""

import json
import os
from datetime import datetime, timedelta

import pytest

from app.models.models import Organization, Platform, User
from app.services.ai_deployment import lifecycle as t8_lifecycle
from app.services.ai_deployment import switchboard, test_mode
from app.services.auth_service import create_access_token, hash_password
from app.services.workforce import activation as wf_activation
from app.services.workforce import constants as WC


def _world(db, *, plans=("trial", "professional")):
    brand = Platform(name="Northwind Test Brand", slug="northwind-sb", is_active=True)
    db.add(brand)
    db.flush()
    orgs = []
    for i, plan in enumerate(plans):
        o = Organization(name="Account %d" % i, slug="sb-acct-%d" % i,
                         platform_id=brand.id, plan=plan, is_demo=False,
                         enabled_features=json.dumps(
                             ["leads", "sms", "email", "booking", "calendar",
                              "crm"]))
        db.add(o)
        db.flush()
        orgs.append(o)
    admin = User(organization_id=orgs[0].id, email="owner@sb.invalid",
                 password_hash=hash_password("OwnerPass123!"),
                 full_name="Owner", role="org_admin",
                 must_change_password=False, is_active=True)
    god = User(organization_id=orgs[0].id, email="god@sb.invalid",
               password_hash=hash_password("GodPass123!"),
               full_name="Operator", role="god_admin",
               must_change_password=False, is_active=True)
    db.add_all([admin, god])
    db.commit()
    return brand, orgs, admin, god


def _next_weekday_1500():
    d = datetime.utcnow().replace(hour=15, minute=0, second=0, microsecond=0)
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def test_switchboard_alone_makes_a_bare_brand_usable(db_session):
    brand, orgs, admin, god = _world(db_session)
    before = switchboard.status(db_session)
    acct = before["brands"][0]["accounts"][0]
    assert acct["test_mode"] is False and acct["summary"] == "Off."

    out = switchboard.enable(db_session, platform_id=brand.id,
                             actor_user_id=god.id)
    db_session.commit()
    assert out["changed"] and len(out["accounts"]) == 2

    after = switchboard.status(db_session)
    for a in after["brands"][0]["accounts"]:
        assert a["test_mode"] is True
        assert all(j["on_plan"] for j in a["jobs"].values()), a["jobs"]
        assert a["summary"].startswith("Ready")
    cards = {c["key"]: c for c in test_mode.job_cards(db_session, orgs[0].id)}
    assert cards["lead_qualifier"]["can_set_up"]
    assert cards["appointment_setter"]["can_set_up"]

    # The customer journey, on nothing but the switchboard's configuration.
    dep = t8_lifecycle.select(db_session, org=orgs[0],
                              template_key="lead_qualifier", actor=admin,
                              provisioning_key="sb-1")
    every = {"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "18:00"}
    t8_lifecycle.configure(db_session, dep, {
        "goal": "Find ready leads.", "audience": {"statuses": ["new"]},
        "channels": ["sms", "email"], "hours": every, "working_hours": every,
        "timezone": "America/Chicago", "good_lead": ["Wants a quote"],
        "handoff_to": admin.id, "handoff_user_id": admin.id}, actor=admin)
    db_session.commit()
    report = test_mode.run_test(db_session, dep, actor=admin,
                                now=_next_weekday_1500())
    assert report["verdict"] == "PASS", report["problems"]


def test_enable_is_idempotent_and_never_lowers_or_clears_a_kill(db_session):
    brand, orgs, admin, god = _world(db_session)
    wf_activation.set_state(db_session, wf_activation.SCOPE_CUSTOMER,
                            orgs[1].id, WC.CONTROLLED, reason="operator")
    wf_activation.set_kill_switch(db_session, wf_activation.SCOPE_CUSTOMER,
                                  orgs[0].id, True, reason="incident")
    switchboard.enable(db_session, platform_id=brand.id)
    db_session.commit()
    again = switchboard.enable(db_session, platform_id=brand.id)
    db_session.commit()
    assert again["changed"] is False
    assert wf_activation.scope_report(
        db_session, wf_activation.SCOPE_CUSTOMER,
        orgs[1].id)["state"] == WC.CONTROLLED
    killed = wf_activation.scope_report(db_session,
                                        wf_activation.SCOPE_CUSTOMER,
                                        orgs[0].id)
    assert killed["kill_switch"] is True
    st = switchboard.status(db_session)["brands"][0]["accounts"]
    assert [a for a in st if a["id"] == orgs[0].id][0]["test_mode"] is False


def test_one_account_only_and_turning_it_off(db_session):
    brand, orgs, admin, god = _world(db_session)
    switchboard.enable(db_session, platform_id=brand.id,
                       organization_ids=[orgs[0].id])
    db_session.commit()
    st = {a["id"]: a for a in
          switchboard.status(db_session)["brands"][0]["accounts"]}
    assert st[orgs[0].id]["test_mode"] and not st[orgs[1].id]["test_mode"]
    switchboard.disable(db_session, platform_id=brand.id,
                        organization_ids=[orgs[0].id])
    db_session.commit()
    st = {a["id"]: a for a in
          switchboard.status(db_session)["brands"][0]["accounts"]}
    assert not st[orgs[0].id]["test_mode"]


def test_an_account_from_another_brand_is_refused(db_session):
    brand, orgs, admin, god = _world(db_session)
    other = Platform(name="Other", slug="other-sb", is_active=True)
    db_session.add(other)
    db_session.flush()
    with pytest.raises(ValueError):
        switchboard.enable(db_session, platform_id=other.id,
                           organization_ids=[orgs[0].id])


def test_startup_brand_list_applies_by_slug(db_session):
    brand, orgs, admin, god = _world(db_session)
    entries = "northwind-sb, no-such-brand".split(",")
    out = switchboard.apply_brands(db_session, entries)
    assert out == [{"brand": brand.name, "accounts": 2, "changed": True}]
    assert switchboard.apply_brands(db_session, entries)[0]["changed"] is False


def test_god_routes_brand_names_and_permissions(client, db_session):
    brand, orgs, admin, god = _world(db_session)
    g = {"Authorization": "Bearer %s" % create_access_token(god, db_session)}
    a = {"Authorization": "Bearer %s" % create_access_token(admin, db_session)}
    assert client.get("/god/ai-workforce/switchboard",
                      headers=a).status_code in (401, 403)
    # The brand NAME works now (production 404'd on names).
    r = client.get("/god/ai-workforce/brands/Northwind Test Brand/catalog",
                   headers=g)
    assert r.status_code == 200, r.text
    assert [b["id"] for b in client.get("/god/ai-workforce/brands",
                                        headers=g).json()["brands"]] \
        == [brand.id]
    r = client.post("/god/ai-workforce/switchboard/enable", headers=g,
                    json={"platform_id": "northwind-sb"})
    assert r.status_code == 200, r.text
    assert r.json()["status"]["brands"][0]["accounts_ready"] == 2
    r = client.post("/god/ai-workforce/switchboard/run-worker-now", headers=g)
    assert r.status_code == 200, r.text
    assert "summary" in r.json()


def test_control_page_reads_plainly_and_the_stop_really_stops(client,
                                                              db_session):
    import importlib.util
    from app.services.workforce import practice
    from app.services.workforce import queue as wf_queue
    from app.services.workforce import service as wf_service
    from app.models.workforce_models import AIEmployee
    brand, orgs, admin, god = _world(db_session)
    g = {"Authorization": "Bearer %s" % create_access_token(god, db_session)}
    a = {"Authorization": "Bearer %s" % create_access_token(admin, db_session)}
    assert client.get("/god/ai-workforce/control",
                      headers=a).status_code in (401, 403)

    off = client.get("/god/ai-workforce/control", headers=g).json()
    assert off["headline"].startswith("Off.")
    assert off["real_messages"] is False

    switchboard.enable(db_session, platform_id=brand.id)
    db_session.commit()
    on = client.get("/god/ai-workforce/control", headers=g).json()
    assert on["headline"].startswith("Working in safe test mode for 2 of 2")
    assert any("background worker" in p for p in on["problems"])

    # A queued practice lead, then the stop.
    dep = t8_lifecycle.select(db_session, org=orgs[0],
                              template_key="lead_qualifier", actor=admin,
                              provisioning_key="sb-stop")
    every = {"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59"}
    t8_lifecycle.configure(db_session, dep, {
        "goal": "x", "audience": {"statuses": ["new"]},
        "channels": ["sms", "email"], "hours": every, "working_hours": every,
        "timezone": "America/Chicago", "good_lead": ["y"],
        "handoff_to": admin.id, "handoff_user_id": admin.id}, actor=admin)
    emp = db_session.get(AIEmployee, dep.employee_id)
    wf_service.activate(db_session, emp, WC.SIMULATION, actor=admin)
    lead = practice.create_practice_lead(db_session,
                                         organization_id=orgs[0].id)
    wf_queue.enqueue(db_session, emp, [lead.id], job_key=emp.job_role)
    db_session.commit()

    r = client.post("/god/ai-workforce/control/emergency-stop", headers=g,
                    json={"on": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["control"]["stopped"] is True
    assert body["control"]["headline"].startswith("Stopped.")
    assert body["result"]["work_parked"] == 1

    spec = importlib.util.spec_from_file_location(
        "wfw", os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "app", "jobs",
            "run_ai_workforce_worker.py"))
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    assert w.run_once(db_session, per_employee=5)["claimed"] == 0

    r = client.post("/god/ai-workforce/control/emergency-stop", headers=g,
                    json={"on": False})
    assert r.json()["control"]["stopped"] is False
    db_session.expire_all()
    assert w.run_once(db_session, per_employee=5)["claimed"] == 1
    recent = client.get("/god/ai-workforce/control",
                        headers=g).json()["recent"]
    assert recent and recent[0]["account"] == orgs[0].name
    assert all("." not in x["what"] for x in recent)
