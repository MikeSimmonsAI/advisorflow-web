"""T8 - THE PROOFS, RUN AS TESTS RATHER THAN ON DEMAND.

The God routes can run both of these on a live deployment, which is useful and
is not a substitute for running them on every commit. A proof nobody runs is a
claim; a proof the suite runs is a property.

WHAT EACH FILE-LEVEL TEST ASSERTS

    the three lifecycles     catalogue -> entitlement -> hire -> configure ->
                             readiness -> controlled activation -> objective ->
                             operational action -> handoff -> pause -> resume ->
                             retire, with zero real outreach.
    the adversarial harness  every dimension, every case, all passing - and
                             the DIMENSIONS are asserted individually, because
                             a suite that quietly stopped running its isolation
                             cases would still report "all passed".

WHY THE PER-CASE ASSERTIONS EXIST TOO. `all_passed` is one boolean and a
refactor that dropped half the cases would not move it. Naming the expected
case count per dimension means deleting a case is a decision somebody has to
make in this file, with the number in front of them.
"""

import pytest

from app.services.ai_deployment import constants as D
from app.services.ai_deployment import evaluation as t8_evaluation
from app.services.ai_deployment import simulation as t8_simulation


# ---------------------------------------------------------------------------
# THE THREE SYNTHETIC LIFECYCLES
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def lifecycles(db_session):
    return t8_simulation.run(db_session, which="all")


def test_all_three_lifecycles_pass(lifecycles):
    failures = [
        "%s / %s: %s" % (s["label"], st["step"], st["detail"])
        for s in lifecycles["scenarios"] for st in s["steps"]
        if not st["passed"]
    ]
    assert not failures, "\n  ".join([""] + failures)
    assert lifecycles["all_passed"]


def test_every_named_lifecycle_actually_ran(lifecycles):
    """Reactivation, energy residential and energy B2B. All three, every run."""
    keys = {s["key"] for s in lifecycles["scenarios"]}
    assert keys == {"reactivation", "energy_residential", "energy_b2b"}


def test_each_lifecycle_covers_the_whole_sequence(lifecycles):
    """Section 15's sequence, step by step, in every scenario.

    Asserted by NAME rather than by count: a scenario that lost its
    deprovisioning step and gained two configuration steps would keep the same
    total and stop proving the thing that matters most.
    """
    required = {
        "catalogue_availability", "entitlement", "hire_is_idempotent",
        "configuration_saved", "readiness_deterministic",
        "customer_cannot_switch_on_live_operation", "review_requires_a_person",
        "controlled_activation", "t6_objective", "t7_operational_simulation",
        "handoff_outcome", "pause_stops_work", "resume_returns_it_to_work",
        "retire_preserves_history", "cancellation_stops_a_live_employee",
        "zero_real_outreach",
    }
    for scenario in lifecycles["scenarios"]:
        present = {s["step"] for s in scenario["steps"]}
        assert required <= present, (
            "%s is missing: %s" % (scenario["key"], sorted(required - present)))


def test_no_real_outreach_occurred(lifecycles):
    """The single claim the whole of T8 is not allowed to get wrong."""
    assert lifecycles["real_outreach"] == 0
    for scenario in lifecycles["scenarios"]:
        step = next(s for s in scenario["steps"]
                    if s["step"] == "zero_real_outreach")
        assert step["passed"], step["detail"]


def test_the_two_energy_segments_run_the_same_engine(lifecycles):
    """Residential and B2B differ in CONFIGURATION and in nothing else.

    Same template, same steps, same outcomes. If a branch on segment ever
    appeared anywhere in T6, T7 or T8, the two scenarios would start to differ
    in something other than the answers they were given.
    """
    res = next(s for s in lifecycles["scenarios"]
               if s["key"] == "energy_residential")
    b2b = next(s for s in lifecycles["scenarios"] if s["key"] == "energy_b2b")
    assert [s["step"] for s in res["steps"]] == [s["step"] for s in b2b["steps"]]
    assert ([s["passed"] for s in res["steps"]]
            == [s["passed"] for s in b2b["steps"]])


# ---------------------------------------------------------------------------
# THE ADVERSARIAL HARNESS
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def harness(db_session):
    return t8_evaluation.run(db_session)


def test_the_whole_harness_passes(harness):
    failures = ["%s / %s: expected %r, got %r"
                % (r["dimension"], r["case"], r["expected"], r["actual"])
                for r in harness["failures"]]
    assert not failures, "\n  ".join([""] + failures)
    assert harness["all_passed"]


# THE EXPECTED SHAPE OF THE SUITE. Deleting a case has to be a decision made
# here, with the number in front of whoever makes it.
EXPECTED_DIMENSIONS = {
    "isolation": 8,
    "commerce": 7,
    "package": 3,
    "races": 7,
    "injection": 5,
    "readiness": 6,
    "deprovision": 4,
    "dark_launch": 3,
}


@pytest.mark.parametrize("dimension,count", sorted(EXPECTED_DIMENSIONS.items()))
def test_each_dimension_ran_every_case_it_is_supposed_to(harness, dimension,
                                                         count):
    bucket = harness["by_dimension"].get(dimension)
    assert bucket is not None, "the %s dimension did not run at all" % dimension
    assert bucket["total"] == count, (
        "%s ran %d cases, not %d" % (dimension, bucket["total"], count))
    assert bucket["passed"] == count


def test_the_harness_describes_itself(harness):
    """Every case carries a sentence, because a screen renders this list."""
    described = t8_evaluation.describe()
    assert len(described) == harness["total"]
    empty = [d["case"] for d in described if not d["what"]]
    assert not empty, "cases with no description: %s" % empty


def test_the_harness_leaves_nothing_behind(db_session):
    """Every case runs in a savepoint. Nothing it created survives the run.

    Asserted against DEPLOYMENTS specifically: the harness's own world is built
    outside the savepoints and is expected to persist, but a deployment created
    inside a case must not.
    """
    from app.models.ai_deployment_models import AIEmployeeDeployment
    t8_evaluation.run(db_session)
    live = (db_session.query(AIEmployeeDeployment)
            .filter(AIEmployeeDeployment.state != D.RETIRED).count())
    assert live == 0, "%d deployment(s) survived the harness" % live
