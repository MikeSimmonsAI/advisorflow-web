"""The T6 queue worker exists, is dark, and is not wired into the deploy."""

import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOB = os.path.join(ROOT, "app", "jobs", "run_ai_workforce_worker.py")


def _load():
    spec = importlib.util.spec_from_file_location("wf_worker_job", JOB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_worker_does_nothing_unless_operations_are_enabled(monkeypatch):
    monkeypatch.delenv("AI_OPERATIONS_ENABLED", raising=False)
    assert _load().main() == 0


def test_worker_does_nothing_when_a_kill_switch_is_engaged(monkeypatch):
    monkeypatch.setenv("AI_OPERATIONS_ENABLED", "1")
    monkeypatch.setenv("AI_WORKFORCE_KILL", "1")
    assert _load().main() == 0


def test_worker_is_not_wired_into_render_or_main():
    for rel in ("render.yaml", os.path.join("app", "main.py")):
        text = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert "run_ai_workforce_worker" not in text


def test_a_pass_with_no_employees_claims_nothing(db_session):
    out = _load().run_pass(db_session)
    assert out == {"organizations": 0, "employees": 0, "claimed": 0,
                   "ran": 0, "errors": 0, "leases_recovered": 0}


# ═══════════════════════════════════════════════════════════════════════════
# M1 - A SYNTHETIC LEAD QUALIFIER, DRIVEN BY THE REAL T6 WORKER PASS
#
# Nothing below calls runtime.run_employee or the simulator directly: the work
# is claimed and run by `run_pass`, the same function the worker's main() calls.
# Everything is a synthetic brand, org and lead on reserved fictional contacts,
# and every send is a simulated adapter, so nobody real can be reached.
# ═══════════════════════════════════════════════════════════════════════════

import itertools  # noqa: E402
import json  # noqa: E402

from app.models.models import Lead, Organization, Platform, User  # noqa: E402
from app.models.workforce_models import (AIEmployeeRun,  # noqa: E402
                                         AIToolExecution, AIWorkItem,
                                         AIWorkItemEvent)
from app.services.auth_service import hash_password  # noqa: E402
from app.services.workforce import constants as C  # noqa: E402
from app.services.workforce import outbound as wf_outbound  # noqa: E402
from app.services.workforce import profiles as wf_profiles  # noqa: E402
from app.services.workforce import queue as wf_queue  # noqa: E402
from app.services.workforce import service as wf_service  # noqa: E402

_SEQ = itertools.count(700000)


def _qualifier(db, *, activate=True):
    """Brand offering -> customer hire -> simulation. Returns (org, emp, lead)."""
    n = next(_SEQ)
    brand = Platform(name="M1 Brand %d" % n, slug="m1-brand-%d" % n,
                     is_active=True)
    db.add(brand)
    db.flush()
    org = Organization(name="M1 Synthetic Co %d" % n, slug="m1-org-%d" % n,
                       platform_id=brand.id, plan="standard", is_demo=True,
                       enabled_features=json.dumps(
                           ["leads", "sms", "email", "booking", "calendar",
                            "crm"]))
    db.add(org)
    db.flush()
    admin = User(organization_id=org.id,
                 email="m1admin%d@example.invalid" % n,
                 password_hash=hash_password("AdminPass123!"),
                 full_name="M1 Admin", role="org_admin",
                 must_change_password=False)
    db.add(admin)
    db.flush()
    lead = Lead(organization_id=org.id, first_name="Sam", last_name="Synthetic",
                phone=wf_profiles.safe_phone(n),
                email=wf_profiles.safe_email("sam", "synthetic", n),
                status="new", source="synthetic_m1", sms_consent=True,
                allow_sms=True, allow_email=True, is_test=False)
    db.add(lead)
    db.flush()
    wf_service.sync_templates(db)
    wf_service.set_offering(db, platform_id=brand.id,
                            template_key="lead_qualifier", enabled=True)
    emp = wf_service.hire(db, organization_id=org.id,
                          template_key="lead_qualifier", actor=admin)
    if activate:
        wf_profiles.enable_simulation_scopes(
            db, platform_id=brand.id, organization_id=org.id,
            reason="m1 test", raise_platform=True)
        wf_service.activate(db, emp, C.SIMULATION, actor=admin, reason="m1")
    db.commit()
    return org, emp, lead


def _enqueue(db, emp, lead):
    out = wf_queue.enqueue(db, emp, [lead.id], subject_type="lead")
    db.commit()
    return out


def _items(db, emp):
    return (db.query(AIWorkItem).filter(AIWorkItem.employee_id == emp.id)
            .all())


def test_the_worker_pass_claims_and_runs_a_queued_lead(db_session):
    org, emp, lead = _qualifier(db_session)
    assert _enqueue(db_session, emp, lead)["created"] == 1
    assert [i.state for i in _items(db_session, emp)] == [C.ASSIGNED]

    with wf_outbound.use_simulated_adapters():
        out = _load().run_pass(db_session)
    db_session.commit()

    # The worker, not a test helper, claimed it and ran it.
    assert out == {"organizations": 1, "employees": 1, "claimed": 1,
                   "ran": 1, "errors": 0, "leases_recovered": 0}
    item = _items(db_session, emp)[0]
    assert item.state != C.ASSIGNED, "the item was never worked"

    # Durable run record, traceable to the item.
    runs = (db_session.query(AIEmployeeRun)
            .filter(AIEmployeeRun.employee_id == emp.id).all())
    assert len(runs) == 1
    assert runs[0].work_item_id == item.id
    assert runs[0].status == "completed"

    # The state machine recorded the path the worker took.
    states = [e.to_state for e in
              db_session.query(AIWorkItemEvent)
              .filter(AIWorkItemEvent.work_item_id == item.id)
              .order_by(AIWorkItemEvent.created_at).all()]
    assert states[0] == C.ASSIGNED
    assert C.ELIGIBILITY_PENDING in states
    assert states[-1] == item.state

    # At least one tool call in the ledger, tied to the run.
    tools = (db_session.query(AIToolExecution)
             .filter(AIToolExecution.run_id == runs[0].id).all())
    assert tools, "the run left no tool ledger"


def test_re_enqueueing_does_not_duplicate_or_redo_work(db_session):
    org, emp, lead = _qualifier(db_session)
    _enqueue(db_session, emp, lead)
    with wf_outbound.use_simulated_adapters():
        _load().run_pass(db_session)
        db_session.commit()
        first_state = _items(db_session, emp)[0].state
        runs_before = db_session.query(AIEmployeeRun).count()
        again = _enqueue(db_session, emp, lead)
        _load().run_pass(db_session)
        db_session.commit()
    assert again["created"] == 0 and again["skipped_existing"] == 1
    assert len(_items(db_session, emp)) == 1
    if first_state in C.TERMINAL_STATES:
        assert db_session.query(AIEmployeeRun).count() == runs_before


def test_an_employee_that_is_off_claims_nothing(db_session):
    org, emp, lead = _qualifier(db_session, activate=False)
    _enqueue(db_session, emp, lead)
    out = _load().run_pass(db_session)
    db_session.commit()
    assert out["claimed"] == 0 and out["ran"] == 0
    assert [i.state for i in _items(db_session, emp)] == [C.ASSIGNED]
    assert db_session.query(AIEmployeeRun).count() == 0


def test_a_paused_employee_is_skipped_and_then_resumes(db_session):
    org, emp, lead = _qualifier(db_session)
    _enqueue(db_session, emp, lead)
    wf_service.pause(db_session, emp, reason="owner pressed pause")
    db_session.commit()
    with wf_outbound.use_simulated_adapters():
        paused = _load().run_pass(db_session)
        db_session.commit()
        assert paused["employees"] == 0 and paused["claimed"] == 0
        assert db_session.query(AIEmployeeRun).count() == 0
        wf_service.resume(db_session, emp)
        db_session.commit()
        resumed = _load().run_pass(db_session)
        db_session.commit()
    assert resumed["claimed"] == 1 and resumed["ran"] == 1


def test_a_retired_employee_is_never_picked_up(db_session):
    org, emp, lead = _qualifier(db_session)
    _enqueue(db_session, emp, lead)
    wf_service.disable(db_session, emp, reason="retired")
    db_session.commit()
    out = _load().run_pass(db_session)
    assert out["employees"] == 0 and out["claimed"] == 0
    assert db_session.query(AIEmployeeRun).count() == 0


def test_the_kill_switch_stops_a_worker_pass(db_session, monkeypatch):
    org, emp, lead = _qualifier(db_session)
    _enqueue(db_session, emp, lead)
    monkeypatch.setenv("AI_WORKFORCE_KILL", "1")
    out = _load().run_pass(db_session)
    db_session.commit()
    assert out["claimed"] == 0 and out["ran"] == 0
    assert db_session.query(AIEmployeeRun).count() == 0
    assert [i.state for i in _items(db_session, emp)] == [C.ASSIGNED]


def test_one_tenant_failing_does_not_stop_another(db_session, monkeypatch):
    bad_org, bad_emp, bad_lead = _qualifier(db_session)
    good_org, good_emp, good_lead = _qualifier(db_session)
    _enqueue(db_session, bad_emp, bad_lead)
    _enqueue(db_session, good_emp, good_lead)

    from app.services.workforce import runtime
    real = runtime.run_organization

    def flaky(db, org_id, **kw):
        if org_id == bad_org.id:
            raise RuntimeError("boom")
        return real(db, org_id, **kw)

    monkeypatch.setattr(runtime, "run_organization", flaky)
    with wf_outbound.use_simulated_adapters():
        out = _load().run_pass(db_session)
        db_session.commit()
    assert out["errors"] == 1
    assert out["claimed"] == 1 and out["ran"] == 1
    assert [i.state for i in _items(db_session, bad_emp)] == [C.ASSIGNED]
    assert _items(db_session, good_emp)[0].state != C.ASSIGNED
