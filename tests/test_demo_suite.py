"""THE DEMO SUITE. What this file defends, in order of how badly.

  1. A DEMO CANNOT REACH A REAL CUSTOMER. Every action resolves its target
     inside the demonstration environment by seed key, and every write asserts
     the tenant is flagged as a demonstration first. Two independent failures
     would be needed for a demo action to land on a real record.

  2. A DEMO CANNOT REACH A REAL PROVIDER. The outbound SMS and email paths
     refuse a demonstration organisation outright, on the server. That is the
     guard that stops a stranger's phone ringing during a sales meeting.

  3. DEMO ACCESS IS NOT GOD ACCESS, AND NOT CUSTOMER ACCESS. It is a
     platform-scoped capability that grants exactly one thing.

  4. THE BUTTONS DO SOMETHING. Every action in the dispatch table changes real
     rows, and the panels are computed from those rows rather than from
     constants — so the demonstration cannot disagree with itself mid-meeting.

  5. RESET IS PROVABLE. Rebuilding restores canonical state and removes what a
     presenter did, without touching anything outside the demo tenants.
"""

import itertools

import pytest

from app.models.demo_suite_models import DemoActionEvent, DemoEnvironment
from app.models.models import (BookingLink, Lead, Message, Organization,
                               Platform, User)
from app.models.sales_models import (SCOPE_BRAND_SALES_ORG, BrandSalesOrg,
                                     Membership, Opportunity)
from app.services import capabilities, demo_guard
from app.services import demo_environment as denv
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _user(db, role="advisor", org_id=None, name="Person"):
    u = User(organization_id=org_id, email="u%d@example.com" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def brands(db_session):
    evo = Platform(name="EvoSys Pro", slug="evo-%d" % next(_SEQ))
    boost = Platform(name="BookaBoost", slug="boost-%d" % next(_SEQ))
    db_session.add_all([evo, boost])
    db_session.commit()
    god = _user(db_session, role="god_admin", name="Owner")
    return dict(evo=evo, boost=boost, god=god)


@pytest.fixture()
def built(db_session, brands):
    """One brand with a built environment, and a real customer beside it."""
    real = Organization(name="Restland", slug="restland-%d" % next(_SEQ),
                        plan="standard", platform_id=brands["evo"].id)
    db_session.add(real)
    db_session.commit()
    denv.build(db_session, brands["evo"].id, actor=brands["god"])
    env = denv.get_environment(db_session, brands["evo"].id)
    return dict(env=env, real=real, **brands)


def _presenter(db, platform, god, admin=False):
    p = _user(db, role="advisor", name="Presenter")
    keys = ["demo_suite"] + (["demo_admin"] if admin else [])
    capabilities.set_platform_grants(db, p, platform.id, platform.name, god,
                                     keys, commit=True)
    return p


# ═════════════════════════════════════════════════════════════════════════════
# 1. Building the environment
# ═════════════════════════════════════════════════════════════════════════════

def test_build_creates_two_flagged_tenants(db_session, brands):
    out = denv.build(db_session, brands["evo"].id, actor=brands["god"])
    org = db_session.query(Organization).filter(
        Organization.id == out["organization_id"]).first()
    bso = db_session.query(BrandSalesOrg).filter(
        BrandSalesOrg.id == out["brand_sales_org_id"]).first()
    assert org.is_demo is True
    assert bso.is_demo is True
    # Under the REAL platform, so the demo wears the brand's own configuration.
    assert org.platform_id == brands["evo"].id
    assert bso.platform_id == brands["evo"].id
    # And with no provider credentials of any kind.
    assert not org.org_twilio_account_sid
    assert not org.resend_api_key


def test_seed_is_believable_not_placeholder(db_session, brands):
    denv.build(db_session, brands["evo"].id, actor=brands["god"])
    env = denv.get_environment(db_session, brands["evo"].id)
    leads = db_session.query(Lead).filter(
        Lead.organization_id == env.organization_id).all()
    assert len(leads) >= 10
    names = {"%s %s" % (l.first_name, l.last_name) for l in leads}
    joined = " ".join(names).lower()
    for placeholder in ("test", "lorem", "foo", "bar", "example user",
                        "abc company"):
        assert placeholder not in joined
    # Every contact detail is fictional by construction.
    assert all((l.phone or "").startswith("+1555010") for l in leads)
    assert all("@example.com" in (l.email or "") for l in leads)
    # The world is ALIVE: conversations, appointments and a pipeline exist.
    assert db_session.query(Message).count() >= 8
    assert db_session.query(BookingLink).count() >= 2
    assert db_session.query(Opportunity).filter(
        Opportunity.brand_sales_org_id == env.brand_sales_org_id).count() >= 5


def test_rebuild_is_idempotent(db_session, brands):
    a = denv.build(db_session, brands["evo"].id, actor=brands["god"])
    b = denv.build(db_session, brands["evo"].id, actor=brands["god"])
    # The tenants are REUSED, so ids referenced elsewhere survive a rebuild.
    assert a["organization_id"] == b["organization_id"]
    assert a["brand_sales_org_id"] == b["brand_sales_org_id"]
    assert a["workspace"]["leads"] == b["workspace"]["leads"]
    env = denv.get_environment(db_session, brands["evo"].id)
    assert db_session.query(Lead).filter(
        Lead.organization_id == env.organization_id).count() \
        == a["workspace"]["leads"]


def test_two_brands_get_separate_worlds(db_session, brands):
    denv.build(db_session, brands["evo"].id, actor=brands["god"])
    denv.build(db_session, brands["boost"].id, actor=brands["god"])
    e1 = denv.get_environment(db_session, brands["evo"].id)
    e2 = denv.get_environment(db_session, brands["boost"].id)
    assert e1.organization_id != e2.organization_id
    assert e1.brand_sales_org_id != e2.brand_sales_org_id


# ═════════════════════════════════════════════════════════════════════════════
# 2. Entitlement
# ═════════════════════════════════════════════════════════════════════════════

def test_without_the_grant_the_demo_does_not_open(client, db_session, built):
    nobody = _user(db_session, role="advisor")
    r = client.get("/demo-suite/%s/world" % built["evo"].id,
                   headers=_h(db_session, nobody))
    assert r.status_code == 403


def test_a_grant_on_one_brand_does_not_open_another(client, db_session, brands):
    denv.build(db_session, brands["evo"].id, actor=brands["god"])
    denv.build(db_session, brands["boost"].id, actor=brands["god"])
    p = _presenter(db_session, brands["evo"], brands["god"])
    assert client.get("/demo-suite/%s/world" % brands["evo"].id,
                      headers=_h(db_session, p)).status_code == 200
    assert client.get("/demo-suite/%s/world" % brands["boost"].id,
                      headers=_h(db_session, p)).status_code == 403


def test_org_admin_role_alone_grants_no_demo_access(client, db_session, built):
    """A role is never an entitlement. Demo access comes from a grant."""
    admin = _user(db_session, role="org_admin", org_id=built["real"].id)
    r = client.get("/demo-suite/%s/world" % built["evo"].id,
                   headers=_h(db_session, admin))
    assert r.status_code == 403


def test_demo_access_grants_nothing_on_the_control_plane(client, db_session,
                                                         built):
    p = _presenter(db_session, built["evo"], built["god"], admin=True)
    for path in ("/god/stats", "/god/orgs", "/god/users",
                 "/god/demo-suite/environments"):
        assert client.get(path, headers=_h(db_session, p)).status_code == 403


def test_presenting_is_not_rebuilding(client, db_session, built):
    """A presenter must not be able to destroy a colleague's live demo."""
    p = _presenter(db_session, built["evo"], built["god"], admin=False)
    r = client.post("/demo-suite/%s/rebuild" % built["evo"].id,
                    headers=_h(db_session, p))
    assert r.status_code == 403
    admin = _presenter(db_session, built["evo"], built["god"], admin=True)
    assert client.post("/demo-suite/%s/rebuild" % built["evo"].id,
                       headers=_h(db_session, admin)).status_code == 200


def test_the_owner_presents_every_brand_without_a_grant(client, db_session,
                                                        built):
    r = client.get("/demo-suite/%s/world" % built["evo"].id,
                   headers=_h(db_session, built["god"]))
    assert r.status_code == 200


def test_my_demo_access_reports_brands_and_readiness(client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"])
    body = client.get("/demo-suite/me", headers=_h(db_session, p)).json()
    assert [b["platform_name"] for b in body["brands"]] == ["EvoSys Pro"]
    assert body["brands"][0]["environment_ready"] is True
    assert body["brands"][0]["may_admin"] is False


# ═════════════════════════════════════════════════════════════════════════════
# 3. The world loads, and it is computed rather than constant
# ═════════════════════════════════════════════════════════════════════════════

def test_every_panel_loads(client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"])
    body = client.get("/demo-suite/%s/world" % built["evo"].id,
                      headers=_h(db_session, p)).json()
    panels = body["panels"]
    for key in ("priority", "leads", "conversation", "calendar", "pipeline",
                "team", "revenue", "customers", "launch"):
        assert key in panels, key
    assert panels["priority"]["queue"]
    assert panels["pipeline"]["columns"]
    assert panels["team"]["people"]
    assert panels["revenue"]["open_pipeline"] > 0
    # The weights behind a weighted projection are STATED, not buried.
    assert panels["revenue"]["weights"]


def test_the_compliance_boundary_is_visible_and_excluded(client, db_session,
                                                         built):
    p = _presenter(db_session, built["evo"], built["god"])
    panels = client.get("/demo-suite/%s/world" % built["evo"].id,
                        headers=_h(db_session, p)).json()["panels"]
    blocked = [l for l in panels["leads"]["leads"] if l["blocked"]]
    assert blocked, "the demo must be able to show a do-not-contact record"
    excluded = panels["priority"]["excluded"]
    assert any("do-not-contact" in e["reason"].lower() for e in excluded)
    # ...and it is never in the workable queue, whatever its other signals.
    assert all(not q.get("blocked") for q in panels["priority"]["queue"])
    assert blocked[0]["name"] not in [q["name"] for q in
                                      panels["priority"]["queue"]]


# ═════════════════════════════════════════════════════════════════════════════
# 4. The buttons do something
# ═════════════════════════════════════════════════════════════════════════════

def _act(client, db, user, platform, action, **params):
    return client.post("/demo-suite/%s/action" % platform.id,
                       json={"action": action, "params": params},
                       headers=_h(db, user))


def test_move_stage_actually_moves_the_deal(client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"])
    r = _act(client, db_session, p, built["evo"], "move_stage",
             target="cordova", to_stage="closing")
    assert r.status_code == 200, r.text
    db_session.expire_all()
    opp = db_session.query(Opportunity).filter(
        Opportunity.id == denv.canonical_id(built["env"], "opp", "cordova")
    ).first()
    assert opp.stage == "closing"
    assert "narration" in r.json()
    # The board the presenter is looking at agrees, in the same response.
    board = r.json()["panels"]["pipeline"]
    closing = [c for c in board["columns"] if c["stage"] == "closing"][0]
    assert any(d["company"] == "Cordova Dental Partners"
               for d in closing["deals"])


def test_complete_task_clears_the_next_action_and_writes_history(
        client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"])
    r = _act(client, db_session, p, built["evo"], "complete_task",
             target="halverson")
    assert r.status_code == 200
    db_session.expire_all()
    opp = db_session.query(Opportunity).filter(
        Opportunity.id == denv.canonical_id(built["env"], "opp", "halverson")
    ).first()
    assert opp.next_action is None
    from app.models.sales_models import OpportunityEvent
    assert (db_session.query(OpportunityEvent)
            .filter(OpportunityEvent.opportunity_id == opp.id,
                    OpportunityEvent.event_type == "next_action_completed")
            .count()) == 1


def test_book_appointment_creates_a_real_appointment(client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"])
    before = db_session.query(BookingLink).count()
    r = _act(client, db_session, p, built["evo"], "book_appointment",
             target="terrence")
    assert r.status_code == 200
    assert db_session.query(BookingLink).count() == before + 1
    # A believable time: a weekday, in working hours.
    from datetime import datetime
    when = datetime.fromisoformat(r.json()["when"])
    assert when.weekday() < 5 and 8 <= when.hour <= 17


def test_send_sms_writes_a_simulated_message_and_calls_no_provider(
        client, db_session, built, monkeypatch):
    p = _presenter(db_session, built["evo"], built["god"])

    def _explode(*a, **k):                      # pragma: no cover - must not run
        raise AssertionError("the Demo Suite reached the real send path")
    monkeypatch.setattr("app.services.sms_service.send_sms", _explode)

    r = _act(client, db_session, p, built["evo"], "send_sms", target="terrence")
    assert r.status_code == 200
    assert r.json()["simulated"] is True
    lead_id = denv.canonical_id(built["env"], "lead", "terrence")
    msgs = db_session.query(Message).filter(Message.lead_id == lead_id).all()
    assert msgs
    # The simulation is DECLARED in the column the carrier's own id occupies.
    assert all((m.twilio_sid or "").startswith("SIMULATED-DEMO") for m in msgs)


def test_qualify_writes_the_decision_onto_the_record(client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"])
    r = _act(client, db_session, p, built["evo"], "qualify_all")
    assert r.status_code == 200
    body = r.json()
    assert body["evaluated"] >= 10
    # Exclusions are REPORTED, not hidden — that is the trust-builder.
    assert body["excluded"]
    db_session.expire_all()
    lead = db_session.query(Lead).filter(
        Lead.id == denv.canonical_id(built["env"], "lead", "terrence")).first()
    assert lead.ai_lead_quality_note


def test_the_ai_gate_is_real(client, db_session, built):
    """A draft is not a message. Approving it is what makes it one."""
    p = _presenter(db_session, built["evo"], built["god"])
    lead_id = denv.canonical_id(built["env"], "lead", "terrence")
    before = db_session.query(Message).filter(Message.lead_id == lead_id).count()

    r = _act(client, db_session, p, built["evo"], "ai_follow_up",
             target="terrence")
    assert r.status_code == 200
    assert r.json()["requires_approval"] is True
    assert db_session.query(Message).filter(
        Message.lead_id == lead_id).count() == before      # nothing was sent

    r2 = _act(client, db_session, p, built["evo"], "approve_draft",
              target="terrence")
    assert r2.status_code == 200
    assert db_session.query(Message).filter(
        Message.lead_id == lead_id).count() == before + 1


def test_approving_with_no_draft_is_refused(client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"])
    r = _act(client, db_session, p, built["evo"], "approve_draft",
             target="gloria")
    assert r.status_code == 409


def test_an_unknown_action_is_refused_not_ignored(client, db_session, built):
    """A control that posts an unknown action and gets a 200 is a dead button
    with a network request attached."""
    p = _presenter(db_session, built["evo"], built["god"])
    r = _act(client, db_session, p, built["evo"], "delete_everything")
    assert r.status_code == 400
    assert "Unknown demo action" in r.json()["detail"]


# ═════════════════════════════════════════════════════════════════════════════
# 5. The boundary
# ═════════════════════════════════════════════════════════════════════════════

def test_a_demo_action_cannot_be_pointed_at_a_real_record(client, db_session,
                                                          built):
    """Targets are seed keys resolved inside the environment, so a real id is
    not addressable at all."""
    real_lead = Lead(organization_id=built["real"].id, first_name="Real",
                     last_name="Family", phone="+12145550000")
    db_session.add(real_lead)
    db_session.commit()
    p = _presenter(db_session, built["evo"], built["god"])
    r = _act(client, db_session, p, built["evo"], "send_sms",
             target=real_lead.id)
    assert r.status_code == 404
    assert db_session.query(Message).filter(
        Message.lead_id == real_lead.id).count() == 0


def test_the_send_path_refuses_a_demonstration_organisation(db_session, built):
    """The server-side guard, independent of the Demo Suite entirely."""
    from app.services import sms_service
    env = built["env"]
    lead = db_session.query(Lead).filter(
        Lead.id == denv.canonical_id(env, "lead", "gloria")).first()
    advisor = db_session.query(User).filter(User.id == lead.assigned_to_id).first()
    with pytest.raises(demo_guard.DemoBoundaryViolation):
        sms_service.send_sms(db_session, advisor, lead, "hello")


def test_a_real_organisation_is_not_blocked_by_the_demo_guard(db_session,
                                                              built):
    """The guard must refuse demo tenants and nothing else."""
    from app.services import sms_service
    real_lead = Lead(organization_id=built["real"].id, first_name="Real",
                     last_name="Family", phone="+12145550000")
    db_session.add(real_lead)
    db_session.commit()
    # No DemoBoundaryViolation — it fails later, on the real DNC/credential
    # path, which is the correct place for a real lead to fail in a test.
    try:
        sms_service._demo_send_guard(db_session, real_lead, "SMS")
    except demo_guard.DemoBoundaryViolation:      # pragma: no cover
        pytest.fail("the demo guard blocked a real organisation")


def test_demo_tenants_are_not_in_the_executive_portfolio(db_session, built):
    """A demonstration workspace must never be counted as a customer."""
    from app.services import executive_authority
    ids = executive_authority.authorized_org_ids(
        db_session, built["god"], built["evo"].id)
    assert built["real"].id in ids
    assert built["env"].organization_id not in ids


def test_demo_tenants_are_not_in_the_customer_list(client, db_session, built):
    body = client.get("/god/orgs", headers=_h(db_session, built["god"])).json()
    names = [o["name"] for o in body["orgs"]]
    assert "Restland" in names
    assert denv.DEMO_WORKSPACE_NAME not in names
    # ...and are reachable for the one screen that wants them.
    body2 = client.get("/god/orgs?include_demo=true",
                       headers=_h(db_session, built["god"])).json()
    assert denv.DEMO_WORKSPACE_NAME in [o["name"] for o in body2["orgs"]]


# ═════════════════════════════════════════════════════════════════════════════
# 6. Guided scenarios, help and the coach
# ═════════════════════════════════════════════════════════════════════════════

def test_the_scenario_catalogue_is_grounded(client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"])
    body = client.get("/demo-suite/%s/scenarios" % built["evo"].id,
                      headers=_h(db_session, p)).json()
    keys = {s["key"] for s in body["scenarios"]}
    assert {"lead_to_appointment", "reactivation", "manager_day",
            "executive_view", "implementation_launch"} <= keys
    assert all(s["practised"] is False for s in body["scenarios"])


def test_every_step_carries_what_to_say_and_why(client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"])
    body = client.get("/demo-suite/%s/scenarios/lead_to_appointment"
                      % built["evo"].id, headers=_h(db_session, p)).json()
    assert body["scenario"]["opening"] and body["scenario"]["closing"]
    for step in body["steps"]:
        for field in ("what_we_show", "where_to_click", "what_happens",
                      "why_it_matters", "prospect_should_notice",
                      "presenter_says"):
            assert step[field], (step["key"], field)


def test_every_named_action_in_every_scenario_actually_exists(client,
                                                              db_session,
                                                              built):
    """A step that says something will happen must be able to make it happen."""
    from app.services import demo_actions, demo_content
    for scenario in demo_content.SCENARIOS:
        for step in scenario["steps"]:
            action = step.get("action")
            if not action:
                continue
            assert action["action"] in demo_actions._DISPATCH, \
                (scenario["key"], step["key"], action["action"])
            assert step["panel"] in demo_content.PANELS


def test_every_help_key_referenced_by_a_step_exists(client, db_session, built):
    from app.services import demo_content
    for scenario in demo_content.SCENARIOS:
        for step in scenario["steps"]:
            if step.get("help_key"):
                assert demo_content.help_topic(step["help_key"]) is not None, \
                    (scenario["key"], step["key"], step["help_key"])


def test_help_answers_the_same_five_questions_every_time(client, db_session,
                                                         built):
    p = _presenter(db_session, built["evo"], built["god"])
    topics = client.get("/demo-suite/help",
                        headers=_h(db_session, p)).json()["topics"]
    assert len(topics) >= 10
    for t in topics:
        for field in ("what_it_is", "what_it_does", "why_customer_cares",
                      "what_happens_next", "presenter_says"):
            assert t[field], (t["key"], field)
    one = client.get("/demo-suite/help/ai_prioritisation",
                     headers=_h(db_session, p))
    assert one.status_code == 200
    assert client.get("/demo-suite/help/nope",
                      headers=_h(db_session, p)).status_code == 404


def test_running_a_step_advances_the_session(client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"])
    r = client.post("/demo-suite/%s/action" % built["evo"].id,
                    json={"action": "qualify_lead",
                          "params": {"target": "terrence"},
                          "scenario": "lead_to_appointment",
                          "step": "qualify"},
                    headers=_h(db_session, p))
    assert r.status_code == 200
    assert "qualify" in r.json()["session"]["completed"]
    assert r.json()["session"]["status"] == "running"


def test_completing_every_step_completes_the_scenario(client, db_session,
                                                      built):
    from app.services import demo_content
    p = _presenter(db_session, built["evo"], built["god"])
    scenario = demo_content.get_scenario("lead_to_appointment")
    for step in scenario["steps"]:
        r = client.post("/demo-suite/%s/scenarios/lead_to_appointment/step"
                        % built["evo"].id, json={"step": step["key"]},
                        headers=_h(db_session, p))
        assert r.status_code == 200
    assert r.json()["session"]["status"] == "complete"


def test_two_presenters_keep_their_own_place(client, db_session, built):
    a = _presenter(db_session, built["evo"], built["god"])
    b = _presenter(db_session, built["evo"], built["god"])
    client.post("/demo-suite/%s/scenarios/lead_to_appointment/step"
                % built["evo"].id, json={"step": "open_priority"},
                headers=_h(db_session, a))
    body_b = client.get("/demo-suite/%s/scenarios/lead_to_appointment"
                        % built["evo"].id, headers=_h(db_session, b)).json()
    assert body_b["session"]["completed"] == []


def test_resetting_a_session_touches_nobody_else(client, db_session, built):
    a = _presenter(db_session, built["evo"], built["god"])
    b = _presenter(db_session, built["evo"], built["god"])
    for u in (a, b):
        client.post("/demo-suite/%s/scenarios/lead_to_appointment/step"
                    % built["evo"].id, json={"step": "open_priority"},
                    headers=_h(db_session, u))
    client.post("/demo-suite/%s/scenarios/lead_to_appointment/reset"
                % built["evo"].id, headers=_h(db_session, a))
    body_b = client.get("/demo-suite/%s/scenarios/lead_to_appointment"
                        % built["evo"].id, headers=_h(db_session, b)).json()
    assert body_b["session"]["completed"] == ["open_priority"]


# ═════════════════════════════════════════════════════════════════════════════
# 7. Reset restores canonical state
# ═════════════════════════════════════════════════════════════════════════════

def test_rebuild_restores_canonical_state(client, db_session, built):
    p = _presenter(db_session, built["evo"], built["god"], admin=True)
    _act(client, db_session, p, built["evo"], "move_stage", target="cordova",
         to_stage="closing")
    _act(client, db_session, p, built["evo"], "send_sms", target="terrence")
    _act(client, db_session, p, built["evo"], "book_appointment",
         target="terrence")

    r = client.post("/demo-suite/%s/rebuild" % built["evo"].id,
                    headers=_h(db_session, p))
    assert r.status_code == 200
    db_session.expire_all()
    env = denv.get_environment(db_session, built["evo"].id)
    opp = db_session.query(Opportunity).filter(
        Opportunity.id == denv.canonical_id(env, "opp", "cordova")).first()
    assert opp.stage == "discovery"                      # back to canonical
    lead = db_session.query(Lead).filter(
        Lead.id == denv.canonical_id(env, "lead", "terrence")).first()
    assert lead.status == "new"
    assert lead.last_contact_date is None
    assert db_session.query(BookingLink).filter(
        BookingLink.lead_id == lead.id).count() == 0


def test_a_rebuild_does_not_erase_who_presented(client, db_session, built):
    """The trail outlives the world it describes.

    A rebuild happens weekly and the event log is the only answer to "who
    demonstrated what, to whom, on Tuesday" — which is precisely the question
    somebody asks after a presentation went sideways, by which time the world
    has been rebuilt. Deleting the trail with the seed would make that
    unanswerable by design.
    """
    p = _presenter(db_session, built["evo"], built["god"], admin=True)
    _act(client, db_session, p, built["evo"], "send_sms", target="terrence")
    before = db_session.query(DemoActionEvent).filter(
        DemoActionEvent.action == "send_sms").count()
    assert before >= 1

    client.post("/demo-suite/%s/rebuild" % built["evo"].id,
                headers=_h(db_session, p))
    db_session.expire_all()
    assert db_session.query(DemoActionEvent).filter(
        DemoActionEvent.action == "send_sms").count() >= before
    # Presenter progress DOES reset — a step counter pointing at a world that
    # no longer exists is worse than an empty one.
    from app.models.demo_suite_models import DemoSession
    assert db_session.query(DemoSession).filter(
        DemoSession.environment_id == built["env"].id).count() == 0


def test_reset_never_touches_a_real_tenant(client, db_session, built):
    real_lead = Lead(organization_id=built["real"].id, first_name="Real",
                     last_name="Family", phone="+12145550000")
    db_session.add(real_lead)
    db_session.commit()
    denv.reset(db_session, built["env"], actor=built["god"])
    db_session.expire_all()
    assert db_session.query(Lead).filter(Lead.id == real_lead.id).first() \
        is not None
    assert db_session.query(Organization).filter(
        Organization.id == built["real"].id).first() is not None


def test_the_demo_keeps_its_own_trail_not_the_audit_log(client, db_session,
                                                        built):
    from app.models.models import AuditLogEntry
    p = _presenter(db_session, built["evo"], built["god"])
    before = db_session.query(AuditLogEntry).count()
    _act(client, db_session, p, built["evo"], "move_stage", target="cordova",
         to_stage="closing")
    assert db_session.query(DemoActionEvent).count() >= 1
    # Real audit numbers must never have to filter demo noise out of themselves.
    assert db_session.query(AuditLogEntry).count() == before
    events = client.get("/god/demo-suite/events",
                        headers=_h(db_session, built["god"])).json()
    assert any(e["action"] == "move_stage" for e in events["events"])


def test_an_unbuilt_brand_says_so_rather_than_looking_broken(client,
                                                            db_session,
                                                            brands):
    p = _presenter(db_session, brands["boost"], brands["god"])
    r = client.get("/demo-suite/%s/world" % brands["boost"].id,
                   headers=_h(db_session, p))
    assert r.status_code == 409
    assert "has not been built" in r.json()["detail"]


def test_god_environments_overview_is_readable(client, db_session, built):
    body = client.get("/god/demo-suite/environments",
                      headers=_h(db_session, built["god"])).json()
    evo = [e for e in body["environments"]
           if e["platform_id"] == built["evo"].id][0]
    assert evo["ready"] is True
    assert evo["counts"]["leads"] >= 10
    assert evo["workspace_name"] == denv.DEMO_WORKSPACE_NAME
    boost = [e for e in body["environments"]
             if e["platform_id"] == built["boost"].id][0]
    assert boost["exists"] is False and boost["ready"] is False
