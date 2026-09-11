"""AI WORKFORCE — the scenario catalogue, run as tests.

ONE TEST PER DIMENSION, not one per scenario, and not one for the lot.

  * One for the lot would report "the simulator failed" and leave somebody
    reading eighty results to find out what.
  * One per scenario would mean the catalogue and this file have to be kept in
    step by hand, and the day somebody adds a scenario and forgets to add a
    test is the day the catalogue stops being the source.

Parametrising over `simulator.DIMENSIONS` gives a named failure per dimension
AND picks up a new scenario automatically. A new dimension appears as a new
test with no edit here.

THE SCENARIOS DRIVE THE SHIPPING ENGINE. Nothing is stubbed except the channel
adapters, the clock, and — for the adversarial cases — the planner, which is
scripted specifically so it TRIES to do the wrong thing and the gateway is
what refuses. See the header of app/services/workforce/simulator.py.
"""

import pytest

from app.services.workforce import evaluation as wf_evaluation
from app.services.workforce import outbound as wf_outbound
from app.services.workforce import simulator as wf_simulator


@pytest.mark.parametrize("dimension", list(wf_simulator.DIMENSIONS))
def test_scenario_dimension(db_session, dimension):
    report = wf_simulator.run_all(db_session, dimensions=[dimension])
    assert report["total"] > 0, "no scenarios for %s" % dimension
    if report["failures"]:
        lines = ["%s\n      expected: %s\n      actual:   %s"
                 % (f["key"], f["expected"], f["actual"])
                 for f in report["failures"]]
        pytest.fail("%d/%d failed in %s:\n  %s"
                    % (report["failed"], report["total"], dimension,
                       "\n  ".join(lines)))


def test_the_catalogue_covers_every_required_dimension():
    """SECTION 39's LIST, CHECKED AGAINST THE CATALOGUE.

    A dimension that quietly loses its last scenario is a dimension that
    reports "0 failures" forever.
    """
    required = {
        "eligibility", "tool_authority", "tenant_isolation",
        "prompt_injection", "state_machine", "idempotency", "concurrency",
        "calendar", "handoff", "runaway", "provider_failure",
        "channel_continuity", "knowledge", "uncertainty", "opt_out",
        "activation", "kill_switch", "entitlement", "supervisor", "audit",
        "shadow", "voice",
    }
    missing = required - set(wf_simulator.DIMENSIONS)
    assert not missing, "no scenarios cover: %s" % sorted(missing)
    for dimension in required:
        count = len([s for s in wf_simulator.SCENARIOS
                     if s["dimension"] == dimension])
        assert count >= 1, "%s has no scenarios" % dimension


def test_scenario_keys_are_unique():
    keys = [s["key"] for s in wf_simulator.SCENARIOS]
    assert len(keys) == len(set(keys)), "duplicate scenario keys"


def test_the_security_suite_passes(db_session):
    """THE SUITE CODEX WILL ATTACK.

    Tenancy, injection, tool authority, the kill switch, activation staging,
    entitlement and the audit trail — run against the real gateway, with a
    planner that genuinely tries to do the wrong thing.
    """
    report = wf_evaluation.run(db_session, suite_key="security",
                               persist=False)
    assert report["verdict"] == "PASSED", report["failures"]
    assert report["failed"] == 0
    assert report["total"] >= 20


def test_the_safety_suite_passes(db_session):
    report = wf_evaluation.run(db_session, suite_key="safety", persist=False)
    assert report["verdict"] == "PASSED", report["failures"]


def test_an_evaluation_run_is_stored_and_readable(db_session):
    report = wf_evaluation.run(db_session, suite_key="capability",
                               environment="test", commit_ref="abcdef0",
                               persist=True)
    assert report.get("run_id"), "the evaluation run was not stored"
    stored = wf_evaluation.detail(db_session, report["run_id"])
    assert stored["total"] == report["total"]
    assert stored["results"], "no per-case results were stored"
    assert any(r["dimension"] for r in stored["results"])
    history = wf_evaluation.history(db_session, suite_key="capability")
    assert history and history[0]["id"] == report["run_id"]


def test_evaluation_refuses_an_unknown_suite(db_session):
    with pytest.raises(ValueError):
        wf_evaluation.run(db_session, suite_key="whatever-you-fancy")


# ═══════════════════════════════════════════════════════════════════════════
# SCALE
# ═══════════════════════════════════════════════════════════════════════════

def test_scale_run_produces_work_and_reaches_nobody(db_session):
    """A LAYERED SCALE TEST — section 38.

    Small here because a unit test should not take a minute; the large runs
    are driven from God Mode and from the standalone harness. What this
    asserts is the shape: real work items, real runs, real refusals, and zero
    real sends.
    """
    report = wf_simulator.run_scale(db_session, leads=120, employees=2,
                                    per_employee=40)
    assert report["leads"] == 120
    assert report["work_items"] >= 100
    assert report["runs"] > 0
    assert report["real_sends"] == 0
    assert report["simulated_sends"] > 0
    assert report["tool_executions"] > 0
    # The messy population is there on purpose: a run with no refusals at all
    # means the eligibility engine never said no to anything, which for this
    # population would be wrong.
    assert report["states"]


def test_simulated_adapters_are_not_the_default():
    """THE DARK-LAUNCH ARGUMENT DOES NOT REST ON AN ADAPTER BEING SWAPPED.

    Outside a `use_simulated_adapters` block the LIVE adapters are installed,
    and the gateway is what refuses an outward tool below CONTROLLED. If this
    ever reports simulated by default, the engine would look safe for the
    wrong reason.
    """
    assert wf_outbound.is_fully_simulated() is False
    assert set(wf_outbound.adapter_report().values()) == {"live"}


def test_simulated_adapters_are_restored_after_use():
    with wf_outbound.use_simulated_adapters():
        assert wf_outbound.is_fully_simulated() is True
    assert wf_outbound.is_fully_simulated() is False


def test_simulated_adapters_are_restored_after_an_exception():
    with pytest.raises(RuntimeError):
        with wf_outbound.use_simulated_adapters():
            raise RuntimeError("boom")
    assert wf_outbound.is_fully_simulated() is False
