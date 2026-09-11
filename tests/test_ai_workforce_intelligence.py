"""T9 - THE MANAGEMENT LAYER'S OWN INVARIANTS.

WHAT THIS FILE DEFENDS. Not "the dashboard renders" - that is not what T9 is
for. These are the properties that make the numbers on it worth acting on:

  * UNKNOWN IS NEVER ZERO. A rate with no denominator, a revenue figure, a
    provider cost, an appointment-completion count - each is absent and says
    so, because a zero on a management screen reads as failure and gets acted
    on.
  * A CANCELLED OUTCOME IS NOT A SUCCESS, a failed message is not a
    conversation, and a duplicate event does not count twice.
  * THE ATTENTION QUEUE DEDUPLICATES TO ROOTS. One provider outage is one
    item, not forty.
  * RATES ARE RECOMPUTED WHEN ROLLED UP, never averaged.
  * UNLIKE JOBS ARE NEVER COMPARED and no composite score is a mystery.
  * EVERY MANAGEMENT ACTION IS DELEGATED and says which system performed it.
  * FACT AND INTERPRETATION STAY IN DIFFERENT FIELDS.

The adversarial and tenant-isolation properties live in
tests/test_ai_workforce_intelligence_security.py; the end-to-end lifecycles in
tests/test_ai_workforce_intelligence_proof.py.
"""

import itertools
import json
from datetime import datetime, timedelta

import pytest

from app.models.ai_deployment_models import AIEmployeeDeployment
from app.models.ai_operations_models import (AICommunication,
                                             AIConversationThread)
from app.models.models import BookingLink, Lead, Organization, Platform, User
from app.models.workforce_models import AIEmployee, AIWorkItem
from app.services.ai_deployment import constants as D
from app.services.ai_operations import constants as O
from app.services.auth_service import hash_password
from app.services.workforce import constants as W
from app.services.workforce import handoff as wf_handoff
from app.services.workforce import performance as wf_performance
from app.services.workforce import queue as wf_queue
from app.services.workforce import service as wf_service
from app.services.workforce_intelligence import actions as t9_actions
from app.services.workforce_intelligence import attention as t9_attention
from app.services.workforce_intelligence import coaching as t9_coaching
from app.services.workforce_intelligence import command as t9_command
from app.services.workforce_intelligence import constants as C
from app.services.workforce_intelligence import cost as t9_cost
from app.services.workforce_intelligence import executive as t9_executive
from app.services.workforce_intelligence import findings as t9_findings
from app.services.workforce_intelligence import metrics as t9_metrics
from app.services.workforce_intelligence import quality as t9_quality
from app.services.workforce_intelligence import readmodel as t9_readmodel
from app.services.workforce_intelligence import reconciliation as t9_rec
from app.services.workforce_intelligence import review as t9_review
from app.services.workforce_intelligence import scope as t9_scope
from app.services.workforce_intelligence import scorecards as t9_scorecards
from app.services.workforce_intelligence import stalled as t9_stalled
from app.services.workforce_intelligence.stalled import Signal

_SEQ = itertools.count(700000)


# ═══════════════════════════════════════════════════════════════════════════
# A WORLD
# ═══════════════════════════════════════════════════════════════════════════


def _platform(db, slug="t9-brand"):
    row = db.query(Platform).filter(Platform.slug == slug).first()
    if row is None:
        row = Platform(name="T9 Test Brand", slug=slug)
        db.add(row)
        db.flush()
    return row


def _org(db, platform, slug):
    row = Organization(name="T9 %s" % slug, slug=slug, plan="standard",
                       platform_id=platform.id,
                       enabled_features=json.dumps(["sms", "email", "leads",
                                                    "booking"]))
    db.add(row)
    db.flush()
    return row


def _advisor(db, org):
    row = User(organization_id=org.id,
               email="t9-%d@example.invalid" % next(_SEQ),
               password_hash=hash_password("TestPass123!"),
               full_name="T9 Advisor", role="advisor",
               must_change_password=False)
    db.add(row)
    db.flush()
    return row


def _lead(db, org, advisor, n):
    row = Lead(organization_id=org.id, assigned_to_id=advisor.id,
               first_name="Lead", last_name="Number%d" % n,
               phone="1555%07d" % next(_SEQ),
               email="lead%d@example.invalid" % next(_SEQ))
    db.add(row)
    db.flush()
    return row


def _employee(db, org, template_key="reactivation_specialist",
              name="Reactivation Specialist"):
    wf_service.sync_templates(db)
    return wf_service.hire(db, organization_id=org.id,
                           template_key=template_key, name=name)


def _deployment(db, org, employee, *, state=D.ACTIVE,
                commercial=D.COMM_ENTITLED, readiness=D.READY_YES):
    row = AIEmployeeDeployment(
        organization_id=org.id, platform_id=org.platform_id,
        template_key="reactivation_specialist", employee_id=employee.id,
        display_name=employee.name, state=state, commercial_state=commercial,
        readiness_state=readiness,
        provisioning_key="t9-test-%d" % next(_SEQ))
    db.add(row)
    db.flush()
    return row


def _thread(db, org, employee, lead, *, state=O.WAITING_FOR_RESPONSE,
            **kw):
    row = AIConversationThread(
        organization_id=org.id, platform_id=org.platform_id,
        employee_id=employee.id, subject_type="lead", subject_id=lead.id,
        state=state, status=kw.pop("status", "open"), **kw)
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def world(db_session):
    """One brand, one customer, one employee, twelve contacts."""
    platform = _platform(db_session)
    org = _org(db_session, platform, "t9-primary-%d" % next(_SEQ))
    advisor = _advisor(db_session, org)
    employee = _employee(db_session, org)
    deployment = _deployment(db_session, org, employee)
    leads = [_lead(db_session, org, advisor, i) for i in range(12)]
    db_session.commit()
    return {"platform": platform, "org": org, "advisor": advisor,
            "employee": employee, "deployment": deployment, "leads": leads}


def _scope(world):
    """A BACKGROUND scope - read-only by construction.

    `for_system` is what a cron pass gets, and it is marked read-only
    deliberately: a background aggregation must never be able to pause a
    customer's employee. Tests that exercise management actions use
    `_manager_scope` below, which is what a signed-in person gets.
    """
    return t9_scope.for_system([world["org"].id],
                               platform_id=world["platform"].id)


def _manager_scope(world):
    return t9_scope.Scope(
        scope_type=C.SCOPE_ORGANIZATION, scope_id=world["org"].id,
        actor_user_id=None, organization_ids=(world["org"].id,),
        platform_ids=(world["platform"].id,), read_only=False,
        label="T9 test workspace")


# ═══════════════════════════════════════════════════════════════════════════
# UNKNOWN IS NOT ZERO
# ═══════════════════════════════════════════════════════════════════════════


def test_a_rate_with_no_denominator_is_unknown_not_zero():
    """0% reads as failure. Absent reads as nothing to say yet."""
    value = t9_metrics.rate("r", "Response rate", 0, 0, calculation="x")
    assert value.value is None
    assert value.evidence == C.EV_UNKNOWN
    assert "not zero" in (value.note or "")


def test_a_rate_below_the_minimum_denominator_is_unknown():
    """Two events and one of them is not a 50% rate."""
    value = t9_metrics.rate("r", "Response rate", 1, 2, calculation="x",
                            minimum=10)
    assert value.value is None
    assert "Too few events" in (value.note or "")


def test_revenue_is_always_unknown_and_says_why():
    value = t9_metrics.revenue_value()
    assert value.value is None
    assert value.evidence == C.EV_UNKNOWN
    assert "does not attribute revenue" in value.note


def test_appointment_completion_is_unknown_because_attendance_is_not_recorded(
        db_session, world):
    scope = _scope(world)
    facts = t9_metrics.appointment_facts(
        db_session, scope, datetime.utcnow() - timedelta(days=30))
    completed = facts["appointments_completed"]
    assert completed.value is None
    assert completed.evidence == C.EV_UNKNOWN


def test_provider_cost_is_unknown_and_the_estimate_is_labelled(db_session,
                                                               world):
    report = t9_cost.report(db_session, _scope(world))
    provider = report["usage"]["provider_cost"]
    assert provider["value"] is None
    assert provider["label"] == "unknown"
    assert report["usage"]["estimated_cost"]["label"] == "estimated"
    assert "not provider invoices" in report["usage"]["note"]


def test_totals_keep_unknown_unknown_when_nothing_has_happened():
    totals = t9_metrics.totals({}, minimum=10)
    assert totals["values"]["revenue"]["value"] is None
    assert totals["values"]["appointments_completed"]["value"] is None


# ═══════════════════════════════════════════════════════════════════════════
# COUNTING THINGS THAT DID NOT HAPPEN
# ═══════════════════════════════════════════════════════════════════════════


def test_a_cancelled_booking_is_not_a_standing_appointment(db_session, world):
    """The booking happened; it does not still stand. Both are facts."""
    org, employee, leads = world["org"], world["employee"], world["leads"]
    booking = BookingLink(lead_id=leads[0].id, user_id=world["advisor"].id,
                          status="cancelled")
    db_session.add(booking)
    db_session.flush()
    _thread(db_session, org, employee, leads[0],
            state=O.APPOINTMENT_BOOKED, appointment_ref=booking.id)
    db_session.commit()

    facts = t9_metrics.appointment_facts(
        db_session, _scope(world), datetime.utcnow() - timedelta(days=30))
    assert facts["appointments_cancelled"].value == 1
    assert facts["appointments_standing"].value == 0


def test_a_failed_message_is_not_counted_as_a_delivered_one(db_session,
                                                            world):
    org, employee = world["org"], world["employee"]
    thread = _thread(db_session, org, employee, world["leads"][0])
    for outcome in (O.P_DELIVERED, O.P_FAILED, O.P_TIMEOUT, O.P_REJECTED):
        db_session.add(AICommunication(
            organization_id=org.id, thread_id=thread.id,
            employee_id=employee.id,
            subject_id=world["leads"][0].id, direction=O.OUTBOUND,
            channel=O.CHANNEL_SMS, state=O.SENT, provider_outcome=outcome,
            simulated=True))
    db_session.commit()

    per_employee = t9_metrics.employee_metrics(db_session, _scope(world))
    values = per_employee[employee.id]["values"]
    assert values["messages_delivered"]["value"] == 1
    assert values["provider_failures"]["value"] == 3


def test_rolling_up_recomputes_rates_rather_than_averaging_them():
    """The mean of two rates is not the rate. A small employee must not move
    the headline."""
    per_employee = {
        "a": {"values": {"responses": {"value": 1, "label": "Responses"},
                         "messages_delivered": {"value": 2,
                                                "label": "Delivered"}}},
        "b": {"values": {"responses": {"value": 10, "label": "Responses"},
                         "messages_delivered": {"value": 1000,
                                                "label": "Delivered"}}},
    }
    totals = t9_metrics.totals(per_employee, minimum=1)
    rate = totals["values"]["response_rate"]
    assert rate["numerator"] == 11
    assert rate["denominator"] == 1002
    # The naive average of 0.5 and 0.01 is 0.255. The real rate is ~0.011.
    assert rate["value"] < 0.05


def test_the_ledger_window_is_half_open_so_a_day_is_never_counted_twice(
        db_session, world):
    """Two adjacent periods must not share a day, or growth is arithmetic."""
    employee = world["employee"]
    now = datetime.utcnow()
    wf_performance.bump(db_session, employee, "appointments", now=now)
    db_session.commit()

    from app.services.workforce_intelligence import collect
    scope = _scope(world)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    inside = collect.performance_rollup_window(db_session, scope, today,
                                               tomorrow)
    boundary = collect.performance_rollup_window(db_session, scope, today,
                                                 today)
    assert inside[employee.id]["appointments"] == 1
    assert boundary == {}


# ═══════════════════════════════════════════════════════════════════════════
# THE ATTENTION QUEUE
# ═══════════════════════════════════════════════════════════════════════════


def _more_leads(db_session, world, n):
    """Enough contacts to cross a detection threshold honestly."""
    existing = len(world["leads"])
    for i in range(existing, n):
        world["leads"].append(_lead(db_session, world["org"],
                                    world["advisor"], i))
    db_session.commit()
    return world["leads"]


def _stalled_world(db_session, world, *, count=6, hours=6):
    """Records that were due and did not move, through T6's own queue."""
    employee = world["employee"]
    if count > len(world["leads"]):
        _more_leads(db_session, world, count)
    wf_queue.enqueue(db_session, employee,
                     [lead.id for lead in world["leads"][:count]])
    items = (db_session.query(AIWorkItem)
             .filter(AIWorkItem.employee_id == employee.id).all())
    for item in items:
        wf_queue.advance_to(db_session, item, W.ELIGIBLE, reason="test")
        wf_queue.schedule_next(db_session, item, 0,
                               now=datetime.utcnow() - timedelta(hours=hours))
    db_session.commit()
    return items


def test_stalled_work_is_detected_and_grouped_per_employee(db_session, world):
    _stalled_world(db_session, world)
    signals = t9_stalled.detect(db_session, _scope(world))
    stuck = [s for s in signals if s.kind == C.A_OBJECTIVE_STUCK]
    assert len(stuck) == 1, "six stalled records must be one item, not six"
    assert stuck[0].count == 6


def test_a_provider_outage_swallows_the_symptoms_it_caused():
    """One root, one item. Forty symptoms become a number on it."""
    root = Signal(kind=C.A_PROVIDER_FAILURE, organization_id="org",
                  employee_id="emp", dedup_key="provider:emp:sms:twilio",
                  title="SMS is failing", why="Twenty failures.", count=20)
    symptoms = [
        Signal(kind=C.A_OBJECTIVE_STUCK, organization_id="org",
               employee_id="emp", dedup_key="stuck:work:emp",
               title="43 records have not moved", why="", count=43),
        Signal(kind=C.A_REPEATED_FAILURE, organization_id="org",
               employee_id="emp", dedup_key="repeat-fail:emp",
               title="6 failing", why="", count=6),
    ]
    kept = t9_attention.deduplicate([root] + symptoms)
    assert len(kept) == 1
    assert kept[0].kind == C.A_PROVIDER_FAILURE
    assert kept[0].count == 20 + 43 + 6
    assert kept[0].evidence["rolled_up"]["records"] == 49


def test_a_root_never_suppresses_another_root():
    """Two genuine problems must not hide each other."""
    a = Signal(kind=C.A_PROVIDER_FAILURE, organization_id="org",
               employee_id="emp", dedup_key="a", title="", why="")
    b = Signal(kind=C.A_EMPLOYEE_SUSPENDED, organization_id="org",
               employee_id="emp", dedup_key="b", title="", why="")
    kept = t9_attention.deduplicate([a, b])
    assert {s.kind for s in kept} == {C.A_PROVIDER_FAILURE,
                                      C.A_EMPLOYEE_SUSPENDED}


def test_symptoms_for_a_different_employee_are_not_suppressed():
    """A provider failing for one employee says nothing about another."""
    root = Signal(kind=C.A_PROVIDER_FAILURE, organization_id="org",
                  employee_id="emp-a", dedup_key="a", title="", why="")
    other = Signal(kind=C.A_OBJECTIVE_STUCK, organization_id="org",
                   employee_id="emp-b", dedup_key="b", title="", why="")
    kept = t9_attention.deduplicate([root, other])
    assert len(kept) == 2


def test_severity_always_outranks_age_in_the_queue_order():
    now = datetime.utcnow()
    critical_new = t9_attention._priority(C.SEV_CRITICAL, now, now)
    normal_ancient = t9_attention._priority(
        C.SEV_NORMAL, now - timedelta(days=400), now)
    assert critical_new < normal_ancient


def test_every_queue_item_carries_what_why_source_and_an_action(db_session,
                                                                world):
    _stalled_world(db_session, world)
    scope = _scope(world)
    t9_attention.refresh(db_session, scope)
    db_session.commit()
    queue = t9_attention.queue(db_session, scope)
    assert queue["items"], "the queue must not be empty"
    for item in queue["items"]:
        assert item["what"]
        assert item["why"]
        assert item["recommended_action"]
        assert item["source"]["table"]
        assert item["age_seconds"] >= 0
        assert item["evidence_class"] == C.EV_FACT


def test_an_item_whose_condition_clears_is_cleared_not_resolved(db_session,
                                                                world):
    """"Somebody fixed it" and "it stopped being true" are different facts."""
    items = _stalled_world(db_session, world)
    scope = _scope(world)
    t9_attention.refresh(db_session, scope)
    db_session.commit()
    assert t9_attention.queue(db_session, scope)["total"] > 0

    for item in items:
        wf_queue.schedule_next(db_session, item, 600)
    db_session.commit()

    result = t9_attention.refresh(db_session, scope)
    db_session.commit()
    assert result["cleared"] >= 1
    assert t9_attention.queue(db_session, scope)["total"] == 0


def test_first_seen_survives_a_refresh_so_ages_are_trustworthy(db_session,
                                                               world):
    _stalled_world(db_session, world)
    scope = _scope(world)
    t9_attention.refresh(db_session, scope)
    db_session.commit()
    first = t9_attention.queue(db_session, scope)["items"][0]["first_seen_at"]
    t9_attention.refresh(db_session, scope)
    db_session.commit()
    second = t9_attention.queue(db_session, scope)["items"][0]["first_seen_at"]
    assert first == second


def test_an_acknowledged_item_stays_acknowledged_across_a_refresh(db_session,
                                                                  world):
    _stalled_world(db_session, world)
    scope = _scope(world)
    t9_attention.refresh(db_session, scope)
    db_session.commit()
    item_id = t9_attention.queue(db_session, scope)["items"][0]["id"]

    class _User:
        id = None
    t9_attention.acknowledge(db_session, scope, item_id, user=_User())
    db_session.commit()
    t9_attention.refresh(db_session, scope)
    db_session.commit()

    states = {i["id"]: i["state"]
              for i in t9_attention.queue(db_session, scope)["items"]}
    assert states[item_id] == C.ATTENTION_STATE_ACK


# ═══════════════════════════════════════════════════════════════════════════
# SCORECARDS AND BENCHMARKS
# ═══════════════════════════════════════════════════════════════════════════


def test_unlike_jobs_are_never_placed_on_one_scale():
    """A receptionist and a reactivation specialist do not share a scale."""
    cards = [{"job_role": "receptionist", "name": "R", "trends": {}},
             {"job_role": "reactivation", "name": "X", "trends": {}},
             {"job_role": "reactivation", "name": "Y", "trends": {}},
             {"job_role": "reactivation", "name": "Z", "trends": {}}]
    out = t9_scorecards.benchmark(cards)
    assert set(out) == {"receptionist", "reactivation"}
    assert "all" not in out
    assert out["receptionist"]["published"] is False


def test_a_benchmark_group_too_small_to_be_meaningful_publishes_nothing():
    """A median over two employees is one employee wearing a statistic."""
    cards = [{"job_role": "reactivation", "name": "A", "trends": {}},
             {"job_role": "reactivation", "name": "B", "trends": {}}]
    out = t9_scorecards.benchmark(cards, minimum_group=3)
    assert out["reactivation"]["published"] is False
    assert "at least 3" in out["reactivation"]["why"]


def test_benchmarking_cannot_reach_another_tenant_by_construction():
    """`benchmark` takes no database session, so it cannot query at all."""
    import inspect
    params = inspect.signature(t9_scorecards.benchmark).parameters
    assert "db" not in params and "session" not in params


def test_a_new_employee_has_no_baseline_rather_than_a_baseline_of_zero(
        db_session, world):
    cards = t9_scorecards.build(db_session, _scope(world))
    card = next(c for c in cards["cards"]
                if c["employee_id"] == world["employee"].id)
    trend = card["trends"]["appointments"]
    assert trend["off_baseline"] is False
    assert trend.get("baseline_note") or trend.get("baseline_daily") == 0.0


def test_a_period_comparison_shows_both_numbers_not_only_a_percentage(
        db_session, world):
    employee = world["employee"]
    now = datetime.utcnow()
    for _ in range(4):
        wf_performance.bump(db_session, employee, "appointments", now=now)
    db_session.commit()
    cards = t9_scorecards.build(db_session, _scope(world), now=now)
    trend = cards["cards"][0]["trends"]["appointments"]
    assert trend["current"] == 4
    assert trend["previous"] == 0
    assert trend["change_fraction"] is None
    assert "unknown, not infinite" in trend["note"]


def test_effort_per_outcome_is_reported_and_cost_per_outcome_is_not(
        db_session, world):
    """Effort needs no price. Cost per outcome would need one we do not have."""
    employee = world["employee"]
    now = datetime.utcnow()
    for _ in range(20):
        wf_performance.bump(db_session, employee, "messages_sent", now=now)
    for _ in range(2):
        wf_performance.bump(db_session, employee, "appointments", now=now)
    db_session.commit()
    cards = t9_scorecards.build(db_session, _scope(world), now=now)
    efficiency = cards["cards"][0]["efficiency"]
    assert efficiency["messages_per_outcome"] == 10.0
    assert efficiency["cost_per_outcome"] is None
    assert "unknown rather than zero" in efficiency["cost_per_outcome_note"]


# ═══════════════════════════════════════════════════════════════════════════
# QUALITY
# ═══════════════════════════════════════════════════════════════════════════


def test_quality_never_reads_a_message_body():
    """Graded on what happened, not on how it sounded."""
    import inspect
    source = inspect.getsource(t9_quality)
    assert "body_preview" not in source.replace(
        "_COMMUNICATION_FIELDS", "")
    assert "sentiment" not in source.lower()


def test_an_unmeasurable_dimension_is_unmeasured_not_perfect(db_session,
                                                             world):
    graded = t9_quality.evaluate(db_session, _scope(world))
    slot = graded["employees"][world["employee"].id]
    grounding = slot["dimensions"][C.Q_FACTUAL_GROUNDING]
    assert grounding["measured"] is False
    assert grounding["rate"] is None
    assert grounding["status"] == t9_quality.UNMEASURED


def test_an_unmeasured_dimension_contributes_no_weight_to_the_score(db_session,
                                                                    world):
    graded = t9_quality.evaluate(db_session, _scope(world))
    score = graded["employees"][world["employee"].id]["score"]
    excluded = [c for c in score["components"] if not c["included"]]
    assert excluded, "an employee with no history has unmeasured dimensions"
    assert all(c["why_excluded"] for c in excluded)


def test_the_composite_score_publishes_every_weight(db_session, world):
    """No mystery AI score: the arithmetic must be reproducible by hand."""
    graded = t9_quality.evaluate(db_session, _scope(world))
    score = graded["employees"][world["employee"].id]["score"]
    assert set(c["dimension"] for c in score["components"]) == set(
        C.QUALITY_DIMENSIONS)
    assert all(c["weight"] for c in score["components"])
    assert graded["weights"] == t9_quality.WEIGHTS


def test_a_message_sent_after_a_stop_is_a_named_exception(db_session, world):
    """Compliance is a list of rows, not a percentage."""
    org, employee, leads = world["org"], world["employee"], world["leads"]
    stopped_at = datetime.utcnow() - timedelta(hours=2)
    thread = _thread(db_session, org, employee, leads[0], state=O.STOPPED,
                     stopped_at=stopped_at, stop_reason=O.STOP_OPT_OUT)
    db_session.add(AICommunication(
        organization_id=org.id, thread_id=thread.id, employee_id=employee.id,
        subject_id=leads[0].id, direction=O.OUTBOUND, channel=O.CHANNEL_SMS,
        state=O.SENT, simulated=True,
        created_at=stopped_at + timedelta(minutes=30)))
    db_session.commit()

    graded = t9_quality.evaluate(db_session, _scope(world))
    dim = graded["employees"][employee.id]["dimensions"][C.Q_STOP_COMPLIANCE]
    assert dim["violations"], "the message after the stop must be named"
    assert dim["critical"] is True
    assert graded["exceptions"]["stop_condition"]


def test_the_platform_evaluation_is_quoted_not_merged(db_session, world):
    graded = t9_quality.evaluate(db_session, _scope(world))
    suite = graded["platform_evaluation"]
    assert suite["available"] is False
    assert "Unknown, not passing" in suite["note"] or "not available" in \
        suite["note"]


# ═══════════════════════════════════════════════════════════════════════════
# FINDINGS - fact and interpretation stay apart
# ═══════════════════════════════════════════════════════════════════════════


def test_a_finding_keeps_fact_metric_interpretation_and_unknown_apart(
        db_session, world):
    _stalled_world(db_session, world, count=30)
    scope = _scope(world)
    t9_findings.refresh(db_session, scope)
    db_session.commit()
    found = t9_findings.listing(db_session, scope)["findings"]
    assert found, "a queue of twelve waiting records must produce a finding"
    first = found[0]
    assert first["fact"]["class"] == C.EV_FACT
    assert first["metric"]["class"] == C.EV_METRIC
    assert first["interpretation"]["class"] == C.EV_INTERPRETATION
    assert first["unknown"]["class"] == C.EV_UNKNOWN
    assert first["recommendation"]["class"] == C.EV_RECOMMENDATION
    assert first["fact"]["items"], "a finding with no facts is an opinion"


def test_every_fact_on_a_finding_names_the_table_it_came_from(db_session,
                                                              world):
    _stalled_world(db_session, world, count=30)
    scope = _scope(world)
    t9_findings.refresh(db_session, scope)
    db_session.commit()
    for finding in t9_findings.listing(db_session, scope)["findings"]:
        for fact in finding["fact"]["items"]:
            assert fact.get("source_table"), fact


def test_every_metric_on_a_finding_carries_its_calculation(db_session, world):
    _stalled_world(db_session, world, count=30)
    scope = _scope(world)
    t9_findings.refresh(db_session, scope)
    db_session.commit()
    for finding in t9_findings.listing(db_session, scope)["findings"]:
        for metric in finding["metric"]["items"]:
            assert metric.get("calculation"), metric


def test_findings_are_deterministic_for_the_same_inputs(db_session, world):
    """Assembled text, not generated: the same rows produce the same sentence."""
    _stalled_world(db_session, world, count=30)
    scope = _scope(world)
    now = datetime.utcnow()
    first = [f.headline for f in t9_findings.generate(db_session, scope,
                                                      now=now)]
    second = [f.headline for f in t9_findings.generate(db_session, scope,
                                                       now=now)]
    assert first == second


# ═══════════════════════════════════════════════════════════════════════════
# RECONCILIATION - surfaced, never repaired
# ═══════════════════════════════════════════════════════════════════════════


def test_a_live_deployment_with_the_engine_off_is_surfaced(db_session, world):
    """Both sides quoted, and the system that owns the fix named."""
    world["employee"].activation_state = W.OFF
    db_session.commit()
    found = t9_rec.check(db_session, _scope(world))
    codes = {f.check_key for f in found}
    assert C.RC_DEPLOYMENT_ACTIVE_ENGINE_OFF in codes
    row = next(f for f in found
               if f.check_key == C.RC_DEPLOYMENT_ACTIVE_ENGINE_OFF)
    assert row.left_value == D.ACTIVE
    assert row.right_value == W.OFF
    assert row.remediation_owner == "t8_deployment_lifecycle"


def test_reconciliation_never_repairs_what_it_finds(db_session, world):
    """The contradiction must still be there after the check has run."""
    world["employee"].activation_state = W.OFF
    db_session.commit()
    t9_rec.refresh(db_session, _scope(world))
    db_session.commit()
    db_session.refresh(world["employee"])
    db_session.refresh(world["deployment"])
    assert world["employee"].activation_state == W.OFF
    assert world["deployment"].state == D.ACTIVE


def test_a_live_deployment_without_an_entitlement_is_surfaced(db_session,
                                                              world):
    world["deployment"].commercial_state = D.COMM_LAPSED
    db_session.commit()
    codes = {f.check_key for f in t9_rec.check(db_session, _scope(world))}
    assert C.RC_EMPLOYEE_ACTIVE_NO_ENTITLEMENT in codes


def test_a_conversation_claiming_a_cancelled_appointment_is_surfaced(
        db_session, world):
    org, employee, leads = world["org"], world["employee"], world["leads"]
    booking = BookingLink(lead_id=leads[0].id, user_id=world["advisor"].id,
                          status="cancelled")
    db_session.add(booking)
    db_session.flush()
    _thread(db_session, org, employee, leads[0],
            state=O.APPOINTMENT_BOOKED, appointment_ref=booking.id)
    db_session.commit()
    codes = {f.check_key for f in t9_rec.check(db_session, _scope(world))}
    assert C.RC_SUCCESS_AFTER_CANCEL in codes


def test_no_reconciliation_check_names_t9_as_the_remediation_owner():
    """Naming T9 would be the first step towards T9 fixing it."""
    assert all("t9" not in owner
               for owner in C.REMEDIATION_OWNER.values())
    assert set(C.REMEDIATION_OWNER) == set(C.RECONCILIATION_CHECKS)


# ═══════════════════════════════════════════════════════════════════════════
# HUMAN REVIEW
# ═══════════════════════════════════════════════════════════════════════════


def _review_world(db_session, world):
    employee = world["employee"]
    wf_queue.enqueue(db_session, employee,
                     [lead.id for lead in world["leads"][:3]])
    items = (db_session.query(AIWorkItem)
             .filter(AIWorkItem.employee_id == employee.id).all())
    for item in items:
        wf_queue.advance_to(db_session, item, W.NEEDS_REVIEW,
                            reason="ambiguous reply")
    db_session.commit()
    return items


def test_the_review_queue_gathers_all_four_sources(db_session, world):
    items = _review_world(db_session, world)
    wf_handoff.create(db_session, employee=world["employee"],
                      lead=world["leads"][5], work_item=None,
                      reason_code="human_requested", summary="Asked for a person")
    db_session.commit()
    queue = t9_review.queue(db_session, _scope(world))
    kinds = {i["source_kind"] for i in queue["items"]}
    assert C.REVIEW_SOURCE_WORK_ITEM in kinds
    assert C.REVIEW_SOURCE_HANDOFF in kinds
    assert queue["total"] >= len(items) + 1


def test_a_review_payload_never_contains_model_reasoning(db_session, world):
    items = _review_world(db_session, world)
    detail = t9_review.detail(db_session, _scope(world),
                              C.REVIEW_SOURCE_WORK_ITEM, items[0].id)
    assert detail is not None
    assert detail["model_reasoning"] is None
    blob = json.dumps(detail).lower()
    for forbidden in C.REVIEW_FORBIDDEN_FIELDS:
        if forbidden == "reasoning":
            # "model_reasoning" is the NAMED ABSENCE and is allowed to appear.
            continue
        assert forbidden not in blob, forbidden


def test_a_review_payload_carries_what_a_reviewer_needs(db_session, world):
    items = _review_world(db_session, world)
    detail = t9_review.detail(db_session, _scope(world),
                              C.REVIEW_SOURCE_WORK_ITEM, items[0].id)
    assert detail["employee"]["name"]
    assert detail["why_review_is_required"]
    assert detail["allowed_actions"]
    assert "timeline" in detail and "communications" in detail


def test_a_decision_outside_the_allow_list_is_refused(db_session, world):
    items = _review_world(db_session, world)

    class _User:
        id = None
    with pytest.raises(ValueError):
        t9_review.decide(db_session, _scope(world),
                         source_kind=C.REVIEW_SOURCE_WORK_ITEM,
                         source_id=items[0].id, decision="pause_the_platform",
                         user=_User())


def test_a_decision_is_recorded_and_changes_nothing_by_itself(db_session,
                                                              world):
    items = _review_world(db_session, world)
    before = items[0].state

    class _User:
        id = None
    result = t9_review.decide(db_session, _scope(world),
                              source_kind=C.REVIEW_SOURCE_WORK_ITEM,
                              source_id=items[0].id,
                              decision=C.RD_PAUSE_REQUESTED, user=_User())
    db_session.commit()
    db_session.refresh(items[0])
    assert result["recorded"] is True
    assert items[0].state == before, "recording a decision must not act"
    assert "does not change" in result["note"]


def test_decisions_are_append_only(db_session, world):
    items = _review_world(db_session, world)

    class _User:
        id = None
    scope = _scope(world)
    for decision in (C.RD_APPROVED, C.RD_REJECTED):
        t9_review.decide(db_session, scope,
                         source_kind=C.REVIEW_SOURCE_WORK_ITEM,
                         source_id=items[0].id, decision=decision,
                         user=_User())
    db_session.commit()
    detail = t9_review.detail(db_session, scope, C.REVIEW_SOURCE_WORK_ITEM,
                              items[0].id)
    assert len(detail["previous_decisions"]) == 2


# ═══════════════════════════════════════════════════════════════════════════
# MANAGEMENT ACTIONS - delegated, never performed here
# ═══════════════════════════════════════════════════════════════════════════


def test_every_declared_action_has_a_handler_and_a_named_authority():
    assert set(t9_actions._HANDLERS) == set(C.MANAGEMENT_ACTIONS)
    for action in C.MANAGEMENT_ACTIONS:
        assert C.ACTION_AUTHORITY[action]


def test_no_action_in_the_vocabulary_changes_authority_or_commerce():
    """The whole of section 9's prohibition list, asserted as absence."""
    forbidden = ("grant", "authority", "consent", "enable", "voice",
                 "live_send", "billing", "entitlement", "readiness",
                 "activate", "god")
    for action in C.MANAGEMENT_ACTIONS:
        for word in forbidden:
            assert word not in action, (action, word)


def test_an_unknown_action_is_refused_rather_than_handled_generically(
        db_session, world):
    class _User:
        id = None
        role = "org_admin"
    with pytest.raises(t9_actions.ActionRefused) as exc:
        t9_actions.perform(db_session, _scope(world),
                           action="delete_everything", user=_User())
    assert exc.value.code == C.R_UNKNOWN_ACTION


def test_a_performed_action_records_the_system_that_performed_it(db_session,
                                                                 world):
    _stalled_world(db_session, world)
    scope = _scope(world)
    t9_attention.refresh(db_session, scope)
    db_session.commit()
    item_id = t9_attention.queue(db_session, scope)["items"][0]["id"]

    class _User:
        id = None
        role = "org_admin"
    result = t9_actions.perform(db_session, scope,
                                action=C.M_ACKNOWLEDGE_EXCEPTION,
                                user=_User(), target_id=item_id)
    db_session.commit()
    assert result["performed_by"] == C.ACTION_AUTHORITY[
        C.M_ACKNOWLEDGE_EXCEPTION]
    history = t9_actions.history(db_session, scope)
    assert history[0]["performed_by_system"]


def test_a_read_only_observer_cannot_take_an_action_that_touches_another_system(
        db_session, world):
    scope = t9_scope.Scope(
        scope_type=C.SCOPE_ORGANIZATION, scope_id=world["org"].id,
        actor_user_id=None, read_only=True,
        organization_ids=(world["org"].id,))

    class _User:
        id = None
        role = "org_admin"
    with pytest.raises(t9_actions.ActionRefused) as exc:
        t9_actions.perform(db_session, scope, action=C.M_PAUSE_EMPLOYEE,
                           user=_User(),
                           target_id=world["deployment"].id)
    assert exc.value.code == C.R_OBSERVATION_MODE


def test_a_background_pass_scope_can_never_pause_an_employee(db_session,
                                                             world):
    """A cron must not be able to take a customer's workforce offline."""
    class _Cron:
        id = None
        role = "org_admin"
    with pytest.raises(t9_actions.ActionRefused) as exc:
        t9_actions.perform(db_session, _scope(world),
                           action=C.M_PAUSE_EMPLOYEE, user=_Cron(),
                           target_id=world["deployment"].id)
    assert exc.value.code == C.R_OBSERVATION_MODE


def test_pausing_goes_through_the_deployment_lifecycle(db_session, world):
    scope = _manager_scope(world)

    class _User:
        id = None
        role = "org_admin"
    result = t9_actions.perform(db_session, scope,
                                action=C.M_PAUSE_EMPLOYEE, user=_User(),
                                target_id=world["deployment"].id,
                                reason="test")
    db_session.commit()
    db_session.refresh(world["deployment"])
    assert result["performed_by"] == "ai_deployment.lifecycle.pause"
    assert world["deployment"].state == D.PAUSED


# ═══════════════════════════════════════════════════════════════════════════
# COACHING - recommends, never acts
# ═══════════════════════════════════════════════════════════════════════════


def test_no_recommendation_can_carry_a_forbidden_consequence():
    for consequence in t9_coaching.FORBIDDEN_CONSEQUENCES:
        with pytest.raises(ValueError):
            t9_coaching._check(consequence)


def test_every_recommendation_says_it_was_not_applied(db_session, world):
    _stalled_world(db_session, world, count=30)
    scope = _scope(world)
    t9_findings.refresh(db_session, scope)
    db_session.commit()
    out = t9_coaching.recommendations(db_session, scope)
    assert out["recommendations"]
    for rec in out["recommendations"]:
        assert rec["applied_automatically"] is False
        assert rec["class"] == C.EV_RECOMMENDATION
        assert rec["consequence"] in C.QUALITY_ALLOWED_CONSEQUENCES


# ═══════════════════════════════════════════════════════════════════════════
# FRESHNESS
# ═══════════════════════════════════════════════════════════════════════════


def test_a_view_never_computed_says_so_rather_than_showing_nothing(db_session,
                                                                   world):
    state = t9_readmodel.freshness(None, None)
    assert state["state"] == C.FRESH_ABSENT
    assert "missing, not empty" in state["statement"]


def test_an_old_payload_with_no_newer_events_is_recent_not_stale(db_session,
                                                                 world):
    """A quiet workspace must not be labelled out of date every two minutes."""
    scope = _scope(world)
    now = datetime.utcnow()
    t9_readmodel.materialise(db_session, scope, C.V_COMMAND_CENTER,
                             lambda: {"items": []},
                             now=now - timedelta(minutes=5))
    db_session.commit()
    result = t9_readmodel.read(db_session, scope, C.V_COMMAND_CENTER, now=now)
    assert result["freshness"]["state"] == C.FRESH_RECENT
    assert "still correct" in result["freshness"]["statement"]


def test_a_failed_computation_keeps_the_last_good_payload(db_session, world):
    scope = _scope(world)
    t9_readmodel.materialise(db_session, scope, C.V_COMMAND_CENTER,
                             lambda: {"items": ["good"]})
    db_session.commit()

    def _boom():
        raise RuntimeError("aggregation failed")

    result = t9_readmodel.materialise(db_session, scope, C.V_COMMAND_CENTER,
                                      _boom)
    db_session.commit()
    assert result["data"] == {"items": ["good"]}
    assert result["error"]
    assert result["freshness"]["state"] == C.FRESH_STALE


def test_a_read_model_key_always_includes_the_scope(world):
    scope = _scope(world)
    key = scope.cache_key(C.V_COMMAND_CENTER, "30d")
    assert key[0] == C.SCOPE_ORGANIZATION
    assert key[1] == world["org"].id
    assert len(key) == 4


# ═══════════════════════════════════════════════════════════════════════════
# THE COMMAND CENTRE AND THE EXECUTIVE CONTRACT
# ═══════════════════════════════════════════════════════════════════════════


def test_needs_attention_comes_first_in_the_payload(db_session, world):
    """The ordering is the argument, so it is asserted rather than styled."""
    data = t9_command.overview(db_session, _scope(world),
                               include_scorecards=False)
    keys = list(data)
    assert keys.index("attention") < keys.index("outcomes")
    assert keys.index("attention") < keys.index("costs")
    assert data["headline"]


def test_the_command_centre_quotes_the_dark_switches_and_changes_none(
        db_session, world):
    state = t9_command.platform_state(db_session)
    ops = state["operations"]
    assert ops["operations_enabled"] is False
    assert ops["live_send_enabled"] is False
    assert ops["live_voice_enabled"] is False
    assert "cannot start the workforce" in state["note"]


def test_the_executive_contract_carries_every_documented_key(db_session,
                                                             world):
    snap = t9_executive.snapshot(db_session, _scope(world))
    missing = [k for k in t9_executive.CONTRACT_KEYS if k not in snap]
    assert not missing, missing
    assert snap["contract_version"] == t9_executive.CONTRACT_VERSION


def test_every_executive_value_carries_its_class(db_session, world):
    snap = t9_executive.snapshot(db_session, _scope(world))
    for block in ("outcomes", "appointments", "handoffs"):
        for key, entry in snap[block].items():
            assert entry.get("class") in C.EVIDENCE_CLASSES, (block, key)


def test_the_executive_contract_never_publishes_a_workforce_quality_score(
        db_session, world):
    snap = t9_executive.snapshot(db_session, _scope(world))
    score = snap["quality"]["workforce_score"]
    assert score["value"] is None
    assert score["class"] == C.EV_UNKNOWN
    assert "not on one scale" in score["note"]


def test_the_executive_contract_reports_revenue_and_provider_cost_as_unknown(
        db_session, world):
    snap = t9_executive.snapshot(db_session, _scope(world))
    assert snap["outcomes"]["revenue"]["class"] == C.EV_UNKNOWN
    assert snap["cost"]["provider_cost_usd"]["class"] == C.EV_UNKNOWN
    assert snap["appointments"]["completed"]["class"] == C.EV_UNKNOWN


def test_the_published_contract_description_matches_the_snapshot(db_session,
                                                                 world):
    described = t9_executive.describe_contract()
    snap = t9_executive.snapshot(db_session, _scope(world))
    assert set(described["keys"]) == set(t9_executive.CONTRACT_KEYS)
    assert all(k in snap for k in described["keys"])


# ═══════════════════════════════════════════════════════════════════════════
# T9 WATCHING ITSELF
# ═══════════════════════════════════════════════════════════════════════════


def test_a_failed_pass_is_recorded_rather_than_swallowed(db_session, world):
    from app.services.workforce_intelligence import observability as t9_obs
    scope = _scope(world)
    with pytest.raises(RuntimeError):
        with t9_obs.pass_run(db_session, scope, C.P_ATTENTION):
            raise RuntimeError("deliberate")
    db_session.commit()
    health = t9_obs.health(db_session, scope)
    assert C.P_ATTENTION in health["passes_with_failures"]
    assert health["healthy"] is False
    assert "not necessarily quiet" in health["statement"]


def test_a_refresh_records_a_run_for_every_pass(db_session, world):
    from app.services.workforce_intelligence import observability as t9_obs
    scope = _scope(world)
    t9_command.refresh(db_session, scope)
    db_session.commit()
    health = t9_obs.health(db_session, scope)
    for pass_key in (C.P_ATTENTION, C.P_FINDINGS, C.P_RECONCILE):
        assert pass_key in health["passes"]
