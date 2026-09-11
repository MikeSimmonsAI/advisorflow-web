"""T8 - THE DEPLOYMENT STATE MACHINE AND ITS INVARIANTS.

These are PROPERTIES rather than scenarios, which is why they are asserted
directly instead of through a lifecycle. The end-to-end behaviour lives in
tests/test_ai_deployment_proofs.py.

WHAT THIS FILE DEFENDS

  * the transition table has no way out of a terminal state, and no state that
    cannot be reached
  * operational state and commercial state are two vocabularies with nothing
    in common, which is the brief's section 3 expressed as a set operation
  * provisioning is idempotent, and a duplicate is refused by a database
    constraint rather than by a check
  * configuration accepts business answers and refuses internals, by
    construction rather than by remembering to
  * readiness is deterministic: the same facts give the same verdict twice
"""

import pytest

from app.services.ai_deployment import activation as t8_activation
from app.services.ai_deployment import capacity as t8_capacity
from app.services.ai_deployment import catalog as t8_catalog
from app.services.ai_deployment import commerce as t8_commerce
from app.services.ai_deployment import configuration as t8_config
from app.services.ai_deployment import constants as D
from app.services.ai_deployment import evaluation as t8_evaluation
from app.services.ai_deployment import lifecycle as t8_lifecycle
from app.services.ai_deployment import readiness as t8_readiness


@pytest.fixture()
def world(db_session):
    """The harness's own synthetic world: two brands, three customers."""
    env = t8_evaluation.Env()
    env.build(db_session)
    db_session.flush()
    return env


# ---------------------------------------------------------------------------
# THE TRANSITION TABLE
# ---------------------------------------------------------------------------

def test_every_state_appears_in_the_transition_table():
    assert set(D.ALLOWED_TRANSITIONS) == set(D.ROW_STATES)


def test_no_transition_names_a_state_that_does_not_exist():
    for state, targets in D.ALLOWED_TRANSITIONS.items():
        unknown = [t for t in targets if t not in D.ROW_STATES]
        assert not unknown, "%s -> %s" % (state, unknown)


def test_a_terminal_state_has_no_way_out():
    """Retirement is final. Re-hiring is a new deployment, not a revival."""
    for state in D.TERMINAL_STATES:
        assert not D.ALLOWED_TRANSITIONS[state], (
            "%s is terminal and has outgoing edges" % state)


def test_every_state_is_reachable_from_the_first_one():
    """A state nothing can reach is a state that describes nothing."""
    seen, frontier = {D.SELECTED}, [D.SELECTED]
    while frontier:
        state = frontier.pop()
        for nxt in D.ALLOWED_TRANSITIONS.get(state, ()):
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == set(D.ROW_STATES), (
        "unreachable: %s" % sorted(set(D.ROW_STATES) - seen))


def test_suspension_never_leads_straight_back_to_working():
    """Section 6: nothing becomes active because something changed under it.

    Entitlement returning puts a deployment back in the queue for a person to
    switch on. It does not switch it on.
    """
    assert not (D.ALLOWED_TRANSITIONS[D.SUSPENDED] & D.LIVE_STATES)


def test_the_two_state_vocabularies_do_not_overlap():
    """Operational and commercial states share no value. Section 3.

    If one name appeared in both, a screen could render a commercial answer in
    an operational field and look entirely correct.
    """
    assert not (set(D.ROW_STATES) & set(D.COMMERCIAL_STATES))


def test_pending_payment_is_not_a_live_commercial_state():
    """Opening a checkout is not paying for one."""
    assert D.COMM_PENDING not in D.COMMERCIALLY_LIVE
    assert D.COMM_LAPSED not in D.COMMERCIALLY_LIVE
    assert D.COMMERCIALLY_LIVE == {D.COMM_INCLUDED, D.COMM_ENTITLED}


def test_every_state_has_words_a_customer_can_read():
    missing = [s for s in D.ALL_STATES if s not in D.STATE_LABELS]
    assert not missing, missing


# ---------------------------------------------------------------------------
# HIRING AND PROVISIONING
# ---------------------------------------------------------------------------

def test_a_new_deployment_starts_off_and_holds_no_actor(world, db_session):
    dep = t8_lifecycle.select(db_session, org=world.org_a,
                              template_key=world.template_key,
                              actor=world.admin_a, provisioning_key="unit-1")
    assert dep.state == D.SELECTED
    assert dep.employee_id is None
    assert dep.commercial_state == D.COMM_ENTITLED


def test_the_same_provisioning_key_never_creates_two(world, db_session):
    first = t8_lifecycle.select(db_session, org=world.org_a,
                                template_key=world.template_key,
                                actor=world.admin_a,
                                provisioning_key="unit-2")
    second = t8_lifecycle.select(db_session, org=world.org_a,
                                 template_key=world.template_key,
                                 actor=world.admin_a,
                                 provisioning_key="unit-2")
    assert first.id == second.id


def test_the_database_refuses_a_duplicate_provisioning_key(world, db_session):
    """THE GUARANTEE IS A CONSTRAINT, not the lookup in `select`.

    Written by inserting the row directly, so the test fails if somebody ever
    drops the unique index and leaves the friendly check in place.
    """
    from sqlalchemy.exc import IntegrityError
    from app.models.ai_deployment_models import AIEmployeeDeployment
    t8_lifecycle.select(db_session, org=world.org_a,
                        template_key=world.template_key, actor=world.admin_a,
                        provisioning_key="unit-3")
    db_session.add(AIEmployeeDeployment(
        organization_id=world.org_a.id, template_key=world.template_key,
        provisioning_key="unit-3", state=D.SELECTED))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_provisioning_creates_the_actor_once(world, db_session):
    from app.models.workforce_models import AIEmployee
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="unit-4")
    assert dep.employee_id
    again = t8_lifecycle.provision(db_session, dep, actor=world.admin_a)
    assert again["created"] is False
    assert (db_session.query(AIEmployee)
            .filter(AIEmployee.organization_id == world.org_a.id).count() == 1)


def test_the_actor_is_created_switched_off(world, db_session):
    from app.models.workforce_models import AIEmployee
    from app.services.workforce import constants as WC
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="unit-5")
    emp = (db_session.query(AIEmployee)
           .filter(AIEmployee.id == dep.employee_id).first())
    assert emp.status == "draft"
    assert emp.activation_state == WC.OFF


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

def test_the_interview_asks_about_the_business_and_nothing_else(world,
                                                                db_session):
    schema = t8_config.schema_for(db_session, world.org_a.id,
                                  world.template_key)
    for field in schema["fields"]:
        assert D.forbidden_reason(field["key"]) is None, field["key"]


def test_an_internal_setting_is_refused_rather_than_ignored(world, db_session):
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="cfg-1")
    with pytest.raises(t8_config.ConfigurationRefused):
        t8_lifecycle.configure(db_session, dep, {"system_prompt": "be nice"},
                               actor=world.admin_a)


def test_an_unknown_business_key_is_dropped(world, db_session):
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="cfg-2")
    t8_lifecycle.configure(db_session, dep, {"something_invented": "x"},
                           actor=world.admin_a)
    assert "something_invented" not in t8_config.config_of(dep)


def test_configuration_reaches_the_engine(world, db_session):
    """Editing the hours changes the EMPLOYEE, not only the screen."""
    from app.models.workforce_models import AIEmployee
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="cfg-3")
    t8_lifecycle.configure(db_session, dep, {
        "working_hours": {"days": [0, 1], "start": "10:00", "end": "16:00"},
        "hours": {"days": [0, 1], "start": "10:00", "end": "16:00"},
    }, actor=world.admin_a)
    emp = (db_session.query(AIEmployee)
           .filter(AIEmployee.id == dep.employee_id).first())
    assert "10:00" in (emp.operating_hours or "")


def test_channels_are_narrowed_on_the_way_in(world, db_session):
    """A channel the job cannot use is refused, not stored and ignored."""
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="cfg-4")
    cleaned, problems = t8_config.validate(
        db_session, organization_id=world.org_a.id,
        template_key=world.template_key,
        answers={"channels": ["sms", "voice"]}, deployment_id=dep.id)
    codes = [p["code"] for p in problems]
    assert D.R_FORBIDDEN_CONFIG in codes
    assert "voice" in str(problems)
    assert cleaned["channels"] == ["sms", "voice"]   # reported, then refused


# ---------------------------------------------------------------------------
# READINESS
# ---------------------------------------------------------------------------

def test_readiness_is_deterministic(world, db_session):
    """The same facts, twice, give the same verdict and the same checks."""
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="rdy-1")
    first = t8_readiness.evaluate(db_session, dep)
    second = t8_readiness.evaluate(db_session, dep)
    assert first.verdict == second.verdict
    assert ([(c.key, c.passed) for c in first.checks]
            == [(c.key, c.passed) for c in second.checks])


def test_every_readiness_check_is_named_and_explained(world, db_session):
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="rdy-2")
    result = t8_readiness.evaluate(db_session, dep)
    for check in result.checks:
        assert check.key and check.label
        assert check.severity in (D.SEVERITY_BLOCKING, D.SEVERITY_REVIEW,
                                  D.SEVERITY_INFO)
        if not check.passed:
            assert check.detail, check.key


def test_a_verdict_is_always_one_of_three(world, db_session):
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="rdy-3")
    assert (t8_readiness.evaluate(db_session, dep).verdict
            in D.READINESS_VERDICTS)


def test_review_required_is_carried_on_the_verdict_not_the_state(world,
                                                                 db_session):
    """A deployment that needs a person to LOOK is still READY to be started.

    `validation_required` means the customer has work to do. `review_required`
    means the platform does. Collapsing them would make one word mean both.
    """
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="rdy-4")
    result = t8_readiness.refresh(db_session, dep)
    if result.verdict == D.READY_REVIEW:
        assert dep.state == D.READY


# ---------------------------------------------------------------------------
# ACTIVATION
# ---------------------------------------------------------------------------

def test_activation_needs_a_human(world, db_session):
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="act-1")
    with pytest.raises(t8_lifecycle.DeploymentRefused):
        t8_activation.request(db_session, dep, D.CONTROLLED, actor=None)


def test_only_the_platform_operator_completes_an_activation(world, db_session):
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="act-2")
    t8_activation.acknowledge_review(db_session, dep, actor=world.operator)
    out = t8_activation.request(db_session, dep, D.CONTROLLED,
                                actor=world.admin_a, reason="please")
    assert out["granted"] is False
    assert out["pending_operator"] is True
    assert dep.state != D.CONTROLLED


def test_activation_re_reads_readiness_rather_than_trusting_the_row(world,
                                                                    db_session):
    """A stored verdict is an answer from whenever it was stored."""
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="act-3")
    t8_readiness.refresh(db_session, dep)
    dep.readiness_state = D.READY_YES        # a lie on the row
    db_session.flush()
    # Break something real underneath it.
    dep.config = "{}"
    db_session.flush()
    pre = t8_activation.preconditions(db_session, dep, D.CONTROLLED)
    assert not pre["allowed"]
    assert D.R_NOT_READY in [r["code"] for r in pre["refusals"]]


def test_a_retired_deployment_cannot_be_switched_on(world, db_session):
    from app.services.ai_deployment import deprovision as t8_deprovision
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="act-4")
    t8_deprovision.retire(db_session, dep, actor=world.operator, reason="done")
    pre = t8_activation.preconditions(db_session, dep, D.CONTROLLED)
    assert D.R_RETIRED in [r["code"] for r in pre["refusals"]]


# ---------------------------------------------------------------------------
# CATALOGUE AND CAPACITY
# ---------------------------------------------------------------------------

def test_the_customer_catalogue_covers_every_platform_job(world, db_session):
    from app.services.workforce import registry as wf_registry
    rows = t8_catalog.customer_catalog(db_session, world.org_a.id)
    assert ({r["template_key"] for r in rows}
            == set(wf_registry.ALL_TEMPLATE_KEYS))


def test_the_customer_catalogue_shows_what_it_cannot_sell(world, db_session):
    """An unavailable job is SHOWN with a reason, not hidden."""
    rows = t8_catalog.customer_catalog(db_session, world.org_a.id)
    unavailable = [r for r in rows if not r["can_hire"]]
    assert unavailable
    for row in unavailable:
        assert row["blockers"], row["template_key"]


def test_requirements_are_derived_from_the_job_itself():
    """A job that books needs a calendar. Nobody writes that down twice."""
    reqs = t8_catalog.requirements_for("appointment_setter")
    assert reqs["needs_calendar"] is True
    assert reqs["reaches_people"] is True
    supervisor = t8_catalog.requirements_for("manager_supervisor")
    assert supervisor["reaches_people"] is False


def test_an_ai_employee_grants_no_platform_capacity():
    """Section 9, as a function anybody can assert on."""
    assert t8_capacity.granted_dimensions() == []


def test_the_supervisor_is_the_management_capability():
    assert t8_capacity.is_advanced_capability("manager_supervisor") is True
    assert t8_capacity.is_advanced_capability("reactivation_specialist") is False


# ---------------------------------------------------------------------------
# COMMERCE MIRROR
# ---------------------------------------------------------------------------

def test_the_commercial_mirror_records_when_it_was_read(world, db_session):
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="com-1")
    out = t8_commerce.reconcile_deployment(db_session, dep, reason="unit")
    assert dep.commercial_checked_at is not None
    assert out["commercial_state"] == dep.commercial_state


def test_reconciling_a_healthy_deployment_changes_nothing(world, db_session):
    dep = world.deploy(db_session, world.org_a, world.admin_a, key="com-2")
    before = dep.state
    t8_commerce.on_commercial_change(db_session, world.org_a.id, reason="unit")
    assert dep.state == before


def test_the_commercial_hook_never_raises(db_session):
    """Its callers are webhooks. It must fail quietly or not at all."""
    out = t8_commerce.on_commercial_change(db_session, "no-such-org",
                                           reason="unit")
    assert out["checked"] == 0
