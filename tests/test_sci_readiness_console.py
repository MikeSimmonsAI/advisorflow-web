"""SCI Controlled Test console: synthetic checks, verdict, no send surface.
Stdlib-only logic (no DB, no Twilio); also executed by scripts/sci_readiness_harness.py."""
from app.services.programs import readiness_check as rc


def test_all_ten_checks_pass_and_nothing_sent():
    out = rc.run_all()
    assert (out["pass_count"], out["fail_count"], out["failed_gate"], out["sent"]) == (10, 0, None, 0)


def test_failure_names_exact_gate(monkeypatch):
    def boom():
        raise AssertionError("injected")
    monkeypatch.setattr(rc, "CHECKS", [(k, l, boom if k == "optout_stop" else f, x) for k, l, f, x in rc.CHECKS])
    out = rc.run_all()
    assert out["fail_count"] == 1 and "STOP" in out["failed_gate"]
    assert rc.verdict(out)["status"] == "BLOCKED"


def test_verdict_never_ready_while_external_blockers_exist():
    assert rc.verdict(None)["status"] == "BLOCKED"
    assert rc.verdict(rc.run_all())["status"] == "CONDITIONAL"


def test_gate_groups_are_the_four_required():
    assert {g["group"] for g in rc.LAUNCH_GATES} == {"verified", "test_now", "external", "approval"}
