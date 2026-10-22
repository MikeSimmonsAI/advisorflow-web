"""M1 ACCEPTANCE - a Lead Qualifier, from the brand's offer to real results.

Every step below goes through the SHIPPING services a customer's clicks call:

    brand offers the job + terms + catalogue item  (T6 offering, T8 commerce)
    customer is entitled                            (T2 purchase, read by T8)
    customer picks the job                          (t8 lifecycle.select)
    customer answers setup                          (t8 lifecycle.configure)
    readiness from real configuration               (t8 readiness)
    RUN TEST -> practice lead -> REAL T6 worker     (test_mode.run_test ->
                                                     run_ai_workforce_worker)
    START WORKING in test mode                      (test_mode.start)
    background worker picks up queued work          (worker main())
    results read back from persisted rows           (T9 command overview)
    pause / resume / stop / retire

No step calls the simulator, runtime.execute or a test-only shortcut to make a
result appear. Nothing can reach a real person: practice records are on the
reserved fictional block, the stage is SIMULATION, and the adapters are
simulated.
"""

import importlib.util
import itertools
import json
import os
from datetime import datetime, timedelta

import pytest

from app.models.models import Lead, Organization, Platform, User
from app.models.workforce_models import (AIEmployee, AIEmployeeRun, AIHandoff,
                                         AIToolExecution, AIWorkItem)
from app.services.ai_deployment import commerce as t8_commerce
from app.services.ai_deployment import constants as D
from app.services.ai_deployment import deprovision as t8_deprovision
from app.services.ai_deployment import lifecycle as t8_lifecycle
from app.services.ai_deployment import readiness as t8_readiness
from app.services.ai_deployment import simulation as t8_sim
from app.services.ai_deployment import test_mode
from app.services.auth_service import hash_password
from app.services.workforce import activation as wf_activation
from app.services.workforce import constants as WC
from app.services.workforce import practice
from app.services.workforce import queue as wf_queue
from app.services.workforce import registry as wf_registry
from app.services.workforce import service as wf_service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB = os.path.join(ROOT, "app", "jobs", "run_ai_workforce_worker.py")
_SEQ = itertools.count(1)



def _next_weekday_1500_utc() -> datetime:
    """A moment AFTER now, on a weekday, at 15:00 UTC (09:00-10:00 Chicago).

    It has to be in the future: work queued during the test is due "now", and
    a pass pretending to be in the past would correctly find nothing due.
    """
    d = datetime.utcnow().replace(hour=15, minute=0, second=0, microsecond=0)
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


# Inside the configured working hours (08:00-18:00 America/Chicago, Mon-Fri).
NOON_ISH = _next_weekday_1500_utc()


def _worker():
    spec = importlib.util.spec_from_file_location("wf_worker_m1", JOB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Account:
    """One isolated synthetic brand + REAL (non-demo) customer account."""

    def __init__(self, db, *, template_key="lead_qualifier", allow_test=True,
                 demo=False, entitled=True):
        n = next(_SEQ)
        self.db = db
        wf_service.sync_templates(db)
        self.brand = Platform(name="M1 Brand %d" % n, slug="m1tm-brand-%d" % n,
                              is_active=True)
        db.add(self.brand)
        db.flush()
        self.org = Organization(
            name="M1 Customer %d" % n, slug="m1tm-org-%d" % n,
            platform_id=self.brand.id, plan="standard", is_demo=demo,
            enabled_features=json.dumps(t8_sim.FEATURES))
        db.add(self.org)
        db.flush()
        self.admin = User(organization_id=self.org.id,
                          email="owner%d@example.invalid" % n,
                          password_hash=hash_password("OwnerPass123!"),
                          full_name="Pat Owner", role="org_admin",
                          must_change_password=False, is_active=True)
        db.add(self.admin)
        db.flush()

        # THE BRAND OFFERS IT (what an authorised admin does in AI Deployment)
        wf_service.set_offering(db, platform_id=self.brand.id,
                                template_key=template_key, enabled=True)
        tpl = wf_registry.template(template_key)
        item = t8_sim._catalogue_item(
            db, self.brand, key="m1-%s-%d" % (template_key, n),
            name=tpl.name, entitlement_key=tpl.entitlement_key)
        t8_sim._set_terms(db, self.brand.id, template_key, item.key)
        if entitled:
            t8_sim._purchase(db, self.org, item)
        if allow_test:
            # THE PLATFORM ALLOWS TEST MODE for this brand and account. The
            # platform row is raised to SIMULATION only (reaches nobody).
            for scope, sid in ((wf_activation.SCOPE_PLATFORM, ""),
                               (wf_activation.SCOPE_BRAND, self.brand.id),
                               (wf_activation.SCOPE_CUSTOMER, self.org.id)):
                wf_activation.set_state(db, scope, sid, WC.SIMULATION,
                                        reason="M1: allow test mode")
        self.template_key = template_key
        db.commit()

    def hire_and_set_up(self, *, handoff=True):
        db = self.db
        dep = t8_lifecycle.select(db, org=self.org,
                                  template_key=self.template_key,
                                  actor=self.admin,
                                  provisioning_key="m1-%s" % self.org.id)
        answers = {
            "goal": "Find out which new leads are interested and ready.",
            "audience": {"statuses": ["new"]},
            "channels": ["sms", "email"],
            "hours": {"days": [0, 1, 2, 3, 4], "start": "08:00",
                      "end": "18:00"},
            "working_hours": {"days": [0, 1, 2, 3, 4], "start": "08:00",
                              "end": "18:00"},
            "timezone": "America/Chicago",
            "good_lead": ["Wants a quote soon", "Is the decision maker"],
        }
        if handoff:
            answers.update({"handoff_to": self.admin.id,
                            "handoff_user_id": self.admin.id,
                            "booking_owner": self.admin.id})
        t8_lifecycle.configure(db, dep, answers, actor=self.admin)
        t8_readiness.refresh(db, dep)
        db.commit()
        return dep

    def employee(self, dep):
        return self.db.get(AIEmployee, dep.employee_id)


def _worker_heartbeat(db, monkeypatch):
    """Run the worker's real main() once so it records a successful pass."""
    from sqlalchemy.orm import sessionmaker
    import app.deps as deps
    bind = db.get_bind()
    monkeypatch.setattr(deps, "SessionLocal", sessionmaker(bind=bind))
    monkeypatch.setenv("AI_OPERATIONS_ENABLED", "1")
    monkeypatch.delenv("AI_WORKFORCE_KILL", raising=False)
    assert _worker().main() == 0


# ── 1. THE WHOLE JOURNEY ─────────────────────────────────────────────────────

def test_lead_qualifier_run_test_passes_through_the_real_worker(db_session):
    acct = Account(db_session)
    dep = acct.hire_and_set_up()
    emp = acct.employee(dep)
    # No texting number is set up on this account: live texting is not ready,
    # and that must NOT stop a practice run - nothing is sent in test mode.
    assert dep.state in (D.READY, D.VALIDATION_REQUIRED) and emp is not None

    diag = test_mode.diagnose(db_session, dep, action="test")
    assert diag["ok"], diag["blockers"]
    assert diag["live_messages"] is False
    assert "sms_sender" in [c["code"] for c in diag["needed_later_for_live"]]

    report = test_mode.run_test(db_session, dep, actor=acct.admin,
                                now=NOON_ISH)
    db_session.commit()

    assert report["verdict"] == "PASS", report["problems"]
    assert report["headline"].startswith("PASS")
    assert report["ended_state"] == WC.QUALIFIED
    # The worker claimed it - twice: first contact, then the pretend reply.
    assert [p["claimed"] for p in report["worker_passes"]] == [1, 1]

    item = db_session.get(AIWorkItem, report["work_item_id"])
    assert item.state == WC.QUALIFIED and item.employee_id == emp.id

    runs = db_session.query(AIEmployeeRun).filter(
        AIEmployeeRun.work_item_id == item.id).all()
    assert len(runs) == 2 and all(r.status == "completed" for r in runs)
    assert sorted(report["run_ids"]) == sorted(r.id for r in runs)

    tools = db_session.query(AIToolExecution).filter(
        AIToolExecution.work_item_id == item.id).all()
    keys = [t.tool_key for t in tools]
    assert "conversation.send_sms" in keys or \
        "conversation.send_email" in keys
    assert "employee.mark_qualified" in keys
    sends = [t for t in tools if t.tool_key.startswith("conversation.send")]
    assert sends and all(t.simulated for t in sends), \
        "a practice send was not simulated"

    handoffs = db_session.query(AIHandoff).filter(
        AIHandoff.work_item_id == item.id).all()
    assert len(handoffs) == 1
    assert handoffs[0].assigned_to_user_id == acct.admin.id

    # The employee went back to OFF after the test: a test does not leave
    # anything running by itself.
    db_session.refresh(emp)
    assert emp.activation_state == WC.OFF
    assert test_mode.status(db_session, dep) == test_mode.S_READY_TO_TEST


def test_the_practice_lead_never_appears_in_the_customer_crm(db_session):
    acct = Account(db_session)
    dep = acct.hire_and_set_up()
    report = test_mode.run_test(db_session, dep, actor=acct.admin,
                                now=NOON_ISH)
    lead = db_session.get(Lead, report["practice_lead"]["id"])
    assert lead.is_test is True
    assert lead.source == practice.PRACTICE_SOURCE
    assert practice.is_fictional_contact(lead)
    from app.services import test_records
    visible = test_records.exclude_test_records(
        db_session.query(Lead).filter(
            Lead.organization_id == acct.org.id)).all()
    assert lead.id not in [l.id for l in visible]


def test_results_are_read_back_from_persisted_records(db_session):
    from app.services.workforce_intelligence import command as t9_command
    from app.services.workforce_intelligence import scope as t9_scope
    acct = Account(db_session)
    dep = acct.hire_and_set_up()
    report = test_mode.run_test(db_session, dep, actor=acct.admin,
                                now=NOON_ISH)
    db_session.commit()
    scope = t9_scope.for_system([acct.org.id])
    ov = t9_command.overview(db_session, scope, now=NOON_ISH,
                             include_scorecards=False)
    work = ov["workforce"]
    text = json.dumps(work)
    assert WC.QUALIFIED in json.dumps(
        __import__("app.services.workforce_intelligence.collect",
                   fromlist=["x"]).work_counts_by_state(db_session, scope))
    assert report["work_item_id"]
    assert text  # rendered without error from real rows


# ── 2. NEEDS ATTENTION IS HONEST ─────────────────────────────────────────────

def test_test_mode_not_allowed_is_explained_and_runs_nothing(db_session):
    acct = Account(db_session, allow_test=False)
    dep = acct.hire_and_set_up()
    report = test_mode.run_test(db_session, dep, actor=acct.admin,
                                now=NOON_ISH)
    assert report["verdict"] == "NEEDS_ATTENTION" and report["ran"] is False
    codes = [p["code"] for p in report["problems"]]
    assert "test_mode_not_allowed" in codes
    p = [p for p in report["problems"]
         if p["code"] == "test_mode_not_allowed"][0]
    assert p["who"] == "admin" and p["what"] and p["why"] and p["next"]
    assert db_session.query(AIWorkItem).count() == 0
    assert db_session.query(AIEmployeeRun).count() == 0


def _lapse_plan(db, acct):
    """The add-on is removed from the customer's plan after they hired."""
    from app.models.purchase_models import CatalogPurchase, PurchaseStatus
    for row in db.query(CatalogPurchase).filter(
            CatalogPurchase.organization_id == acct.org.id).all():
        row.status = PurchaseStatus.CANCELED
    db.flush()


def test_not_on_plan_is_explained(db_session):
    acct = Account(db_session)
    dep = acct.hire_and_set_up()
    _lapse_plan(db_session, acct)
    diag = test_mode.diagnose(db_session, dep, action="test")
    assert not diag["ok"]
    blocker = [b for b in diag["blockers"] if b["code"] == "not_on_plan"]
    assert blocker and blocker[0]["who"] == "admin"


def test_customer_words_carry_no_internal_jargon(db_session):
    acct = Account(db_session, allow_test=False)
    dep = acct.hire_and_set_up(handoff=False)
    _lapse_plan(db_session, acct)
    diag = test_mode.diagnose(db_session, dep, action="start")
    assert len(diag["blockers"]) >= 3     # plan, handoff, test mode, worker
    words = json.dumps([{k: b[k] for k in ("what", "why", "next")}
                        for b in diag["blockers"]]).lower()
    for banned in ("t6", "t7", "t8", "t9", "activation_state", "entitlement",
                   "simulation", "scope", "prompt", "model", "api",
                   "read model", "god"):
        assert banned not in words, banned


# ── 3. SAFETY BOUNDARIES ─────────────────────────────────────────────────────

def test_test_mode_refuses_a_real_lead_in_a_real_account(db_session):
    acct = Account(db_session)
    dep = acct.hire_and_set_up()
    emp = acct.employee(dep)
    real = Lead(organization_id=acct.org.id, first_name="Real",
                last_name="Person", phone="+12145550123",
                email="real.person@example.invalid", status="new",
                source="import", allow_sms=True, allow_email=True,
                sms_consent=True, is_test=False)
    db_session.add(real)
    wf_service.activate(db_session, emp, WC.SIMULATION, actor=acct.admin)
    wf_queue.enqueue(db_session, emp, [real.id])
    db_session.commit()
    from app.services.workforce import outbound
    with outbound.use_simulated_adapters():
        _worker().run_pass(db_session, now=NOON_ISH)
    db_session.commit()
    item = db_session.query(AIWorkItem).filter(
        AIWorkItem.subject_id == real.id).one()
    assert item.state not in (WC.WORKING, WC.WAITING_FOR_RESPONSE,
                              WC.QUALIFIED)
    assert "test_mode_practice_only" in (item.eligibility_reasons or "")
    assert db_session.query(AIToolExecution).filter(
        AIToolExecution.work_item_id == item.id,
        AIToolExecution.tool_key.like("conversation.send%")).count() == 0


def test_a_practice_record_is_excluded_once_the_stage_could_reach_people(
        db_session):
    acct = Account(db_session)
    dep = acct.hire_and_set_up()
    emp = acct.employee(dep)
    lead = practice.create_practice_lead(db_session,
                                         organization_id=acct.org.id)
    for scope, sid in ((wf_activation.SCOPE_PLATFORM, ""),
                       (wf_activation.SCOPE_BRAND, acct.brand.id),
                       (wf_activation.SCOPE_CUSTOMER, acct.org.id)):
        wf_activation.set_state(db_session, scope, sid, WC.CONTROLLED,
                                reason="M1: prove the exception is narrow")
    wf_service.activate(db_session, emp, WC.CONTROLLED, actor=acct.admin)
    from app.services.workforce import eligibility
    verdict = eligibility.evaluate(db_session, employee=emp, lead=lead,
                                   channel="sms", enforce_hours=False,
                                   enforce_frequency=False)
    assert verdict.result == WC.DENY
    assert "internal_record" in json.dumps(verdict.reasons)


def test_one_customers_test_never_touches_another_customer(db_session):
    a = Account(db_session)
    b = Account(db_session)
    dep_a = a.hire_and_set_up()
    dep_b = b.hire_and_set_up()
    emp_b = b.employee(dep_b)
    b_lead = practice.create_practice_lead(db_session,
                                           organization_id=b.org.id)
    wf_queue.enqueue(db_session, emp_b, [b_lead.id])
    db_session.commit()
    report = test_mode.run_test(db_session, dep_a, actor=a.admin,
                                now=NOON_ISH)
    assert report["verdict"] == "PASS"
    b_item = db_session.query(AIWorkItem).filter(
        AIWorkItem.employee_id == emp_b.id).one()
    assert b_item.state == WC.ASSIGNED
    assert db_session.query(AIEmployeeRun).filter(
        AIEmployeeRun.organization_id == b.org.id).count() == 0
    # And the report carries only A's records.
    for run_id in report["run_ids"]:
        assert db_session.get(AIEmployeeRun, run_id).organization_id == \
            a.org.id


def test_a_customer_cannot_reach_another_tenants_lead_through_the_queue(
        db_session):
    a = Account(db_session)
    b = Account(db_session)
    dep_a = a.hire_and_set_up()
    emp_a = a.employee(dep_a)
    foreign = practice.create_practice_lead(db_session,
                                            organization_id=b.org.id)
    out = wf_queue.enqueue(db_session, emp_a, [foreign.id])
    assert out["created"] == 0


# ── 4. START WORKING, THE BACKGROUND WORKER, AND THE STOP CONTROLS ──────────

def test_start_is_refused_until_the_worker_is_really_running(db_session,
                                                              monkeypatch):
    acct = Account(db_session)
    dep = acct.hire_and_set_up()
    refused = test_mode.start(db_session, dep, actor=acct.admin)
    assert refused["started"] is False
    assert "worker_not_running" in [b["code"] for b in refused["blockers"]]
    assert acct.employee(dep).activation_state == WC.OFF

    _worker_heartbeat(db_session, monkeypatch)
    health = test_mode.worker_health(db_session)
    assert health["running"] and health["last_status"] == "success"
    started = test_mode.start(db_session, dep, actor=acct.admin)
    assert started["started"] is True, started["blockers"]
    assert test_mode.status(db_session, dep) == test_mode.S_TESTING


def test_working_pause_resume_stop_retire(db_session, monkeypatch):
    from app.services.workforce import outbound
    acct = Account(db_session)
    dep = acct.hire_and_set_up()
    emp = acct.employee(dep)
    _worker_heartbeat(db_session, monkeypatch)
    assert test_mode.start(db_session, dep, actor=acct.admin)["started"]

    lead = practice.create_practice_lead(db_session,
                                         organization_id=acct.org.id)
    wf_queue.enqueue(db_session, emp, [lead.id], job_key=emp.job_role)
    db_session.commit()
    w = _worker()

    # WORKING: the scheduled worker pass picks it up.
    with outbound.use_simulated_adapters():
        first = w.run_pass(db_session, now=NOON_ISH)
    db_session.commit()
    assert first["claimed"] == 1
    item = db_session.query(AIWorkItem).filter(
        AIWorkItem.subject_id == lead.id).one()
    assert item.state == WC.WAITING_FOR_RESPONSE

    # PAUSE: nothing is claimed, even when the item is due.
    assert test_mode.pause(db_session, dep, actor=acct.admin)["status"] == \
        test_mode.S_PAUSED
    item.next_action_at = NOON_ISH
    db_session.commit()
    with outbound.use_simulated_adapters():
        paused = w.run_pass(db_session, now=NOON_ISH + timedelta(hours=1))
    assert paused["claimed"] == 0

    # RESUME: work continues.
    assert test_mode.resume(db_session, dep, actor=acct.admin)["status"] == \
        test_mode.S_TESTING
    db_session.commit()
    with outbound.use_simulated_adapters():
        resumed = w.run_pass(db_session, now=NOON_ISH + timedelta(hours=2))
    db_session.commit()
    assert resumed["claimed"] == 1

    # STOP: the employee is off, its queue is parked, nothing is claimed.
    lead2 = practice.create_practice_lead(db_session,
                                          organization_id=acct.org.id)
    wf_queue.enqueue(db_session, emp, [lead2.id], job_key=emp.job_role)
    assert test_mode.stop(db_session, dep, actor=acct.admin)["status"] == \
        test_mode.S_STOPPED
    db_session.commit()
    with outbound.use_simulated_adapters():
        stopped = w.run_pass(db_session, now=NOON_ISH + timedelta(hours=3))
    assert stopped["claimed"] == 0
    db_session.refresh(emp)
    assert emp.activation_state == WC.OFF

    # START again works, and picks up the parked item.
    assert test_mode.start(db_session, dep, actor=acct.admin,
                           now=datetime.utcnow())["started"]

    # RETIRE: permanent; history preserved; never picked up again.
    t8_deprovision.retire(db_session, dep, actor=acct.admin,
                          reason="M1: retire")
    db_session.commit()
    assert test_mode.status(db_session, dep) == test_mode.S_RETIRED
    with outbound.use_simulated_adapters():
        after = w.run_pass(db_session, now=NOON_ISH + timedelta(hours=4))
    assert after["claimed"] == 0
    assert db_session.query(AIEmployeeRun).filter(
        AIEmployeeRun.employee_id == emp.id).count() >= 2
    assert test_mode.diagnose(db_session, dep, action="test")["blockers"][0][
        "code"] == "retired"


def test_a_crashed_workers_lease_is_recovered_by_the_next_pass(db_session):
    from app.services.workforce import outbound
    acct = Account(db_session)
    dep = acct.hire_and_set_up()
    emp = acct.employee(dep)
    wf_service.activate(db_session, emp, WC.SIMULATION, actor=acct.admin)
    lead = practice.create_practice_lead(db_session,
                                         organization_id=acct.org.id)
    wf_queue.enqueue(db_session, emp, [lead.id], job_key=emp.job_role)
    # A worker claims it and dies without releasing.
    claimed = wf_queue.claim(db_session, emp, limit=1, now=NOON_ISH)
    assert len(claimed) == 1
    db_session.commit()
    w = _worker()
    with outbound.use_simulated_adapters():
        during = w.run_pass(db_session, organization_id=acct.org.id,
                            now=NOON_ISH + timedelta(seconds=30))
    assert during["claimed"] == 0, "a live lease was stolen"
    with outbound.use_simulated_adapters():
        later = w.run_pass(db_session, now=NOON_ISH + timedelta(hours=2))
    db_session.commit()
    assert later["leases_recovered"] == 1
    assert later["claimed"] == 1 and later["ran"] == 1


def test_kill_switch_stops_test_mode_and_says_so(db_session, monkeypatch):
    acct = Account(db_session)
    dep = acct.hire_and_set_up()
    monkeypatch.setenv("AI_WORKFORCE_KILL", "1")
    report = test_mode.run_test(db_session, dep, actor=acct.admin,
                                now=NOON_ISH)
    assert report["verdict"] == "NEEDS_ATTENTION"
    assert "safety_stop" in [p["code"] for p in report["problems"]]
    assert db_session.query(AIEmployeeRun).count() == 0


# ── 5. M3: APPOINTMENT SETTER, THE SAME PATH ─────────────────────────────────

def _setter(db):
    acct = Account(db, template_key="appointment_setter")
    dep = acct.hire_and_set_up()
    return acct, dep


def test_appointment_setter_books_a_practice_appointment_on_the_real_worker(
        db_session, monkeypatch):
    from app.models.models import BookingLink
    from app.services import tenant_scheduling
    from app.services.workforce import booking as wf_booking

    def no_calendar(*a, **k):
        raise AssertionError("a practice booking reached a real calendar")

    def no_case_file(*a, **k):
        raise AssertionError("a practice booking created a case file")

    monkeypatch.setattr(tenant_scheduling, "_push_to_calendar", no_calendar)
    monkeypatch.setattr(tenant_scheduling, "_make_case_file", no_case_file)

    acct, dep = _setter(db_session)
    report = test_mode.run_test(db_session, dep, actor=acct.admin,
                                now=NOON_ISH)
    db_session.commit()
    assert report["verdict"] == "PASS", report["problems"]
    assert report["ended_state"] == WC.APPOINTMENT_BOOKED
    assert [p["claimed"] for p in report["worker_passes"]] == [1, 1]
    tools = [t["tool"] for t in report["what_it_tried"]]
    assert tools.index("calendar.get_availability") < \
        tools.index("appointment.book")
    appt = report["practice_appointment"]
    assert appt and appt["practice"] is True
    assert appt["on_a_real_calendar"] is False

    rows = db_session.query(BookingLink).filter(
        BookingLink.lead_id == report["practice_lead"]["id"]).all()
    assert len(rows) == 1
    assert rows[0].status == wf_booking.PRACTICE_STATUS
    # Nothing selecting real appointments sees it.
    assert db_session.query(BookingLink).filter(
        BookingLink.status.in_(("booked", "confirmed"))).count() == 0


def test_a_practice_booking_does_not_take_a_real_slot(db_session):
    from app.services.workforce import booking as wf_booking
    acct, dep = _setter(db_session)
    report = test_mode.run_test(db_session, dep, actor=acct.admin,
                                now=NOON_ISH)
    assert report["verdict"] == "PASS"
    taken = report["practice_appointment"]["starts_at"]
    emp = acct.employee(dep)
    real = Lead(organization_id=acct.org.id, first_name="Real",
                last_name="Caller", phone="+12145550188",
                email="real.caller@example.invalid", status="new",
                source="import", allow_sms=True, allow_email=True,
                sms_consent=True, is_test=False)
    db_session.add(real)
    db_session.flush()
    slots = wf_booking.available_slots(db_session, employee=emp, lead=real,
                                       advisor=acct.admin, days_ahead=7,
                                       now=NOON_ISH)
    assert slots["availability_status"] == "ok"
    assert taken in [s["starts_at"] for s in slots["slots"]], \
        "the practice appointment removed a real opening"


def test_no_open_times_is_needs_attention_with_the_fix(db_session,
                                                     monkeypatch):
    from app.services import tenant_scheduling
    acct, dep = _setter(db_session)
    # The calendar answers, and has nothing open.
    monkeypatch.setattr(tenant_scheduling, "availability",
                        lambda *a, **k: {"availability_status": "ok",
                                         "slots": [], "reason": None})
    report = test_mode.run_test(db_session, dep, actor=acct.admin,
                                now=NOON_ISH)
    assert report["verdict"] == "NEEDS_ATTENTION"
    assert report["ran"] is True
    p = report["problems"][0]
    assert p["code"] == "no_open_times", report["problems"]
    assert "Availability" in p["next"]


def test_both_verified_jobs_are_offered_and_the_rest_are_not(db_session):
    acct = Account(db_session)
    for key in ("appointment_setter", "follow_up_specialist"):
        wf_service.set_offering(db_session, platform_id=acct.brand.id,
                                template_key=key, enabled=True)
        tpl = wf_registry.template(key)
        item = t8_sim._catalogue_item(db_session, acct.brand,
                                      key="m3-%s" % key, name=tpl.name,
                                      entitlement_key=tpl.entitlement_key)
        t8_sim._set_terms(db_session, acct.brand.id, key, item.key)
        t8_sim._purchase(db_session, acct.org, item)
    db_session.commit()
    cards = {c["key"]: c for c in test_mode.job_cards(db_session,
                                                      acct.org.id)}
    assert cards["lead_qualifier"]["can_set_up"]
    assert cards["appointment_setter"]["can_set_up"]
    assert cards["follow_up_specialist"]["can_set_up"]
    assert cards["follow_up_specialist"]["proven"] is True
    assert cards["customer_care_agent"]["availability"] == "coming_soon"


def test_every_registered_job_has_a_card_with_an_honest_label(db_session):
    acct = Account(db_session)
    cards = test_mode.job_cards(db_session, acct.org.id)
    assert sorted(c["key"] for c in cards) == sorted(
        wf_registry.ALL_TEMPLATE_KEYS)
    assert len(cards) == 11
    for c in cards:
        assert c["title"] and c["does"] and c["availability_label"]
        if not c["proven"]:
            assert c["availability"] == "coming_soon"
            assert not c["can_set_up"]
            assert c["availability_label"] == "Not yet proven"
        else:
            assert c["key"] in test_mode.VERIFIED_JOBS


def test_reports_never_show_an_internal_action_name(db_session):
    for key in ("lead_qualifier", "appointment_setter",
                "follow_up_specialist"):
        acct = Account(db_session, template_key=key)
        dep = acct.hire_and_set_up()
        report = test_mode.run_test(db_session, dep, actor=acct.admin,
                                    now=NOON_ISH)
        assert report["verdict"] == "PASS", (key, report["problems"])
        for t in report["what_it_tried"]:
            assert t["action"] != t["tool"] and "." not in t["action"], t
        for s in report["steps"]:
            assert "_" not in s["step"], s
        assert report["pretend_reply"]


# ── 6. FOLLOW-UP SPECIALIST, THE SAME PATH ───────────────────────────────────

def test_follow_up_specialist_end_to_end_on_the_real_worker(db_session,
                                                           monkeypatch):
    from app.services import tenant_scheduling
    from app.services.workforce_intelligence import collect as t9_collect
    from app.services.workforce_intelligence import scope as t9_scope

    def no_calendar(*a, **k):
        raise AssertionError("a practice follow-up reached a real calendar")

    monkeypatch.setattr(tenant_scheduling, "_push_to_calendar", no_calendar)

    # T8 offering/hire/config, through the shipping services.
    acct = Account(db_session, template_key="follow_up_specialist")
    dep = acct.hire_and_set_up()
    emp = acct.employee(dep)
    assert emp.job_role == "follow_up_specialist"

    report = test_mode.run_test(db_session, dep, actor=acct.admin,
                                now=NOON_ISH)
    db_session.commit()
    assert report["verdict"] == "PASS", report["problems"]
    assert report["ended_state"] in (WC.QUALIFIED, WC.APPOINTMENT_BOOKED,
                                     WC.HUMAN_HANDOFF)
    # T6 queue + worker: two real passes, the second woken by the reply.
    assert [p["claimed"] for p in report["worker_passes"]] == [1, 1]
    assert report["pretend_reply_sent"] is True
    assert "went quiet" in report["pretend_reply"]

    # Persisted tool executions: outbound was practice-only, none refused.
    rows = (db_session.query(AIToolExecution)
            .filter(AIToolExecution.work_item_id == report["work_item_id"])
            .all())
    assert rows and all(r.decision == "allowed" for r in rows)
    sends = [r for r in rows if r.tool_key.startswith("conversation.send_")]
    assert sends and all(r.simulated for r in sends)
    assert not report["refused"]

    # Persisted outcome + T9 evidence read from real rows.
    item = db_session.query(AIWorkItem).get(report["work_item_id"])
    assert item.state == report["ended_state"]
    scope = t9_scope.for_system([acct.org.id])
    counts = t9_collect.work_counts_by_state(db_session, scope)
    assert report["ended_state"] in json.dumps(counts)

    # No real person was reachable: the lead is a practice record.
    lead = db_session.query(Lead).get(report["practice_lead"]["id"])
    assert lead.is_test is True
