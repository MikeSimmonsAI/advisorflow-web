"""T9 - THE SYNTHETIC PROOFS, THE SCALE TEST, AND THE DARK LAUNCH.

THREE THINGS THIS FILE ESTABLISHES THAT NO UNIT TEST CAN.

  THE LIFECYCLE. A workforce that has actually done things - booked, failed,
  been refused, handed over, gone quiet - shows up on the management surface
  as activity, outcomes, exceptions, review items, a scorecard, a finding, a
  cost picture and an executive summary, and a manager can act on one of them.
  Three lifecycles, because a Reactivation Specialist and a Full-Lifecycle
  Energy employee are different jobs and a proof that only covered one would
  be a proof about one job.

  SCALE. Section 23's requirement is not "it is fast" - it is that the query
  count does not grow with the number of employees. That is testable directly,
  by counting statements, and a timing assertion would be flaky on a laptop
  and meaningless on a server. So the assertion is on QUERY COUNT, measured
  with a cursor hook.

  DARKNESS. T9 deploys on top of three layers that are switched off, and
  deploying it must not switch any of them on. The last section asserts the
  switches are where T6 and T7 left them, before and after everything above.
"""

import itertools
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.models.ai_operations_models import AICommunication
from app.models.workforce_models import AIToolExecution
from app.services.ai_operations import flags as t7_flags
from app.services.workforce import activation as t6_activation
from app.services.workforce import constants as W
from app.services.workforce_intelligence import command as t9_command
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence import executive as t9_executive
from app.services.workforce_intelligence import proof as t9_proof
from app.services.workforce_intelligence import scope as t9_scope


# One counter for the whole module, so two scale worlds in one test do not
# collide on a slug or an email.
_SCALE_SEQ = itertools.count(1)


class _QueryCounter:
    """Counts statements against one session. The N+1 detector.

    A TIMING ASSERTION WOULD BE THE WRONG TEST. It passes on a fast machine
    with an N+1 in it and fails on a slow one without. The property section 23
    actually asks for is that the number of statements does not grow with the
    number of employees, and that is exactly what this measures.
    """

    def __init__(self, session):
        self.session = session
        self.count = 0
        self._bind = session.get_bind()

    def _on_execute(self, *args, **kwargs):
        self.count += 1

    def __enter__(self):
        event.listen(self._bind, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(self._bind, "before_cursor_execute", self._on_execute)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# THE THREE LIFECYCLES
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("key", ["reactivation", "energy_residential",
                                 "energy_b2b"])
def test_the_management_lifecycle_proof_passes(db_session, key):
    """Every step, for one job, end to end."""
    report = t9_proof.run(db_session, which=key)
    db_session.commit()
    scenario = report["scenarios"][0]
    failed = [s for s in scenario["steps"] if not s["passed"]]
    assert not failed, "\n".join("%s: %s" % (s["step"], s["detail"])
                                 for s in failed)
    assert scenario["passed"] is True


def test_the_proofs_produce_no_real_outreach(db_session):
    report = t9_proof.run(db_session, which="reactivation")
    db_session.commit()
    assert report["real_outreach"] == 0
    assert db_session.query(AICommunication).filter(
        AICommunication.simulated.is_(False)).count() == 0
    assert db_session.query(AIToolExecution).filter(
        AIToolExecution.simulated.is_(False),
        AIToolExecution.decision == "allowed").count() == 0


def test_every_proof_step_is_named_and_explained(db_session):
    report = t9_proof.run(db_session, which="reactivation")
    db_session.commit()
    for scenario in report["scenarios"]:
        for step in scenario["steps"]:
            assert step["step"]
            assert step["detail"], step["step"]


def test_an_unknown_proof_is_refused(db_session):
    with pytest.raises(ValueError):
        t9_proof.run(db_session, which="not_a_lifecycle")


def test_the_proof_history_is_built_through_the_engines_own_contracts():
    """The enrichment may not write a state or a counter by hand.

    A proof that set `state` directly would keep passing after the engine's
    state machine changed, against a shape nothing produces any more.
    """
    import inspect
    source = inspect.getsource(t9_proof._enrich)
    assert "wf_queue.enqueue" in source
    assert "wf_queue.advance_to" in source
    assert "wf_performance.bump" in source
    assert "item.state =" not in source
    assert "AIWorkItem(" not in source


# ═══════════════════════════════════════════════════════════════════════════
# SCALE
# ═══════════════════════════════════════════════════════════════════════════


def _scale_world(db_session, *, employees, leads_per_employee):
    """A workspace with a realistic population, built in bulk.

    THE SETUP IS DELIBERATELY NOT THROUGH THE ENGINE'S CONTRACTS. What is
    under test here is the READ path's query count, and enqueueing twelve
    thousand records one contract call at a time would take minutes and prove
    nothing about it. The lifecycle proofs above are where the contracts are
    exercised.
    """
    import itertools
    import json

    from app.models.ai_deployment_models import AIEmployeeDeployment
    from app.models.models import Lead, Organization, Platform, User
    from app.models.workforce_models import (AIEmployee, AIPerformanceEntry,
                                             AIWorkItem)
    from app.services.auth_service import hash_password
    from app.services.workforce import service as wf_service

    seq = _SCALE_SEQ
    brand = db_session.query(Platform).filter(
        Platform.slug == "t9-scale").first()
    if brand is None:
        brand = Platform(name="Scale Brand", slug="t9-scale")
        db_session.add(brand)
        db_session.flush()
    org = Organization(name="Scale Org", slug="t9-scale-%d" % next(seq),
                       plan="standard", platform_id=brand.id,
                       enabled_features=json.dumps(["sms", "email", "leads"]))
    db_session.add(org)
    db_session.flush()
    advisor = User(organization_id=org.id,
                   email="scale-%d@example.invalid" % next(seq),
                   password_hash=hash_password("TestPass123!"),
                   full_name="Scale Advisor", role="advisor",
                   must_change_password=False)
    db_session.add(advisor)
    db_session.flush()
    wf_service.sync_templates(db_session)

    made = []
    for i in range(employees):
        emp = wf_service.hire(db_session, organization_id=org.id,
                              template_key="reactivation_specialist",
                              name="Employee %d" % i)
        db_session.add(AIEmployeeDeployment(
            organization_id=org.id, platform_id=brand.id,
            template_key="reactivation_specialist", employee_id=emp.id,
            display_name=emp.name, state="active",
            commercial_state="entitled", readiness_state="ready",
            provisioning_key="scale-%d-%d" % (i, next(seq))))
        made.append(emp)
    db_session.flush()

    today = datetime.utcnow().strftime("%Y-%m-%d")
    bulk_leads, bulk_items, bulk_metrics = [], [], []
    for emp_index, emp in enumerate(made):
        for j in range(leads_per_employee):
            lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                        first_name="Scale", last_name="L%d-%d" % (emp_index, j),
                        phone="1555%07d" % next(seq),
                        email="s%d@example.invalid" % next(seq))
            bulk_leads.append(lead)
    db_session.bulk_save_objects(bulk_leads, return_defaults=True)
    db_session.flush()

    ids = [row[0] for row in db_session.query(Lead.id).filter(
        Lead.organization_id == org.id).all()]
    per = max(1, len(ids) // max(1, len(made)))
    for emp_index, emp in enumerate(made):
        chunk = ids[emp_index * per:(emp_index + 1) * per]
        for subject_id in chunk:
            bulk_items.append(AIWorkItem(
                organization_id=org.id, employee_id=emp.id,
                job_key="reactivation", subject_type="lead",
                subject_id=subject_id, state=W.ELIGIBLE,
                next_action_at=datetime.utcnow() - timedelta(hours=4)))
        for metric in ("messages_sent", "responses", "appointments",
                       "records_assigned", "records_eligible"):
            bulk_metrics.append(AIPerformanceEntry(
                organization_id=org.id, employee_id=emp.id,
                metric_date=today, metric_key=metric, value=len(chunk)))
    db_session.bulk_save_objects(bulk_items)
    db_session.bulk_save_objects(bulk_metrics)
    db_session.commit()

    scope = t9_scope.Scope(
        scope_type=C.SCOPE_ORGANIZATION, scope_id=org.id, actor_user_id=None,
        organization_ids=(org.id,), platform_ids=(brand.id,))
    return {"org": org, "scope": scope, "employees": made,
            "work_items": len(bulk_items)}


def test_the_query_count_does_not_grow_with_the_number_of_employees(
        db_session):
    """THE N+1 ASSERTION. Not a timing test - a statement count.

    Five employees and twenty produce the same shape of page. If the read path
    asked a question per employee, the second number would be roughly four
    times the first. The allowance below is generous on purpose: what is being
    refused is GROWTH WITH N, not a fixed handful of extra statements.
    """
    small = _scale_world(db_session, employees=4, leads_per_employee=150)
    with _QueryCounter(db_session) as counter:
        t9_command.overview(db_session, small["scope"],
                            include_scorecards=False)
    few = counter.count

    large = _scale_world(db_session, employees=16, leads_per_employee=150)
    with _QueryCounter(db_session) as counter:
        t9_command.overview(db_session, large["scope"],
                            include_scorecards=False)
    many = counter.count

    assert few > 0 and many > 0
    # Four times the employees must not mean anything like four times the
    # queries. A little growth is fine - a per-employee query is not.
    assert many < few * 1.5, ("query count grew with employee count: "
                              "%d -> %d" % (few, many))


def test_a_realistic_population_is_answered_in_a_bounded_number_of_queries(
        db_session):
    world = _scale_world(db_session, employees=8, leads_per_employee=250)
    assert world["work_items"] >= 1900
    started = time.time()
    with _QueryCounter(db_session) as counter:
        payload = t9_command.overview(db_session, world["scope"],
                                      include_scorecards=False)
    elapsed = time.time() - started
    assert payload["workforce"]["employees"] == 8
    # A COUNT RATHER THAN A CLOCK. The ceiling is a sanity bound on the shape
    # of the read path, not a performance target - a machine being slow must
    # not fail this.
    assert counter.count < 120, counter.count
    assert elapsed < 30


def test_the_scorecard_page_is_also_bounded(db_session):
    from app.services.workforce_intelligence import scorecards as t9_scorecards
    world = _scale_world(db_session, employees=12, leads_per_employee=100)
    with _QueryCounter(db_session) as counter:
        cards = t9_scorecards.build(db_session, world["scope"])
    assert len(cards["cards"]) == 12
    assert counter.count < 120, counter.count


def test_the_executive_contract_is_bounded_at_scale(db_session):
    world = _scale_world(db_session, employees=10, leads_per_employee=150)
    with _QueryCounter(db_session) as counter:
        snap = t9_executive.snapshot(db_session, world["scope"])
    assert snap["contract_version"] == t9_executive.CONTRACT_VERSION
    assert counter.count < 250, counter.count


# ═══════════════════════════════════════════════════════════════════════════
# THE DARK LAUNCH
# ═══════════════════════════════════════════════════════════════════════════


def test_t9_does_not_switch_anything_on(db_session):
    """Before and after everything above, the three brakes are still on."""
    before = t7_flags.state()
    t9_proof.run(db_session, which="reactivation")
    db_session.commit()
    after = t7_flags.state()
    assert before == after
    assert after["operations_enabled"] is False
    assert after["live_send_enabled"] is False
    assert after["live_voice_enabled"] is False


def test_the_platform_activation_row_is_off_and_t9_leaves_it_there(db_session):
    row = t6_activation.ensure_platform_row(db_session)
    db_session.commit()
    assert row.state == W.OFF
    world = _scale_world(db_session, employees=2, leads_per_employee=20)
    t9_command.refresh(db_session, world["scope"])
    db_session.commit()
    db_session.refresh(row)
    assert row.state == W.OFF
    assert row.kill_switch is False


def test_the_command_centre_reports_the_switches_without_touching_them(
        db_session):
    state = t9_command.platform_state(db_session)
    assert state["operations"]["operations_enabled"] is False
    assert "cannot start the workforce" in state["note"]
    assert t7_flags.state()["operations_enabled"] is False
