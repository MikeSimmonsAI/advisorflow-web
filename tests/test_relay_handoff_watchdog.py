import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "relay"))

import handoff_watchdog as hw  # noqa: E402


def test_obsolete_completed_run_is_not_resignalled():
    # relay-restart-20261008-0858: COMPLETED Oct 8, re-signalled 9x over 2 days.
    ok, reason = hw.decide_resignal(age=47 * 60, threshold=10, max_age=360, retries=9,
                                    max_retries=2, handled=False, newer_run=None)
    assert not ok and reason.startswith("expired")


def test_fresh_missed_handoff_resignals_once_then_caps():
    assert hw.decide_resignal(30, 10, 360, 0, 2, False, None)[0]
    assert hw.decide_resignal(30, 10, 360, 1, 2, False, None)[0]
    ok, reason = hw.decide_resignal(30, 10, 360, 2, 2, False, None)
    assert not ok and "cap" in reason


def test_too_young_and_handled_and_superseded():
    assert not hw.decide_resignal(5, 10, 360, 0, 2, False, None)[0]
    assert not hw.decide_resignal(30, 10, 360, 0, 2, True, None)[0]
    ok, reason = hw.decide_resignal(30, 10, 360, 0, 2, False, "sci-overnight-20261010")
    assert not ok and "superseded" in reason


def test_superseded_by_detects_newer_run():
    terminal = {"id": 10, "body": "[RELAY:CLAUDE_STATUS]\nrelay_run_id: old-run\nstatus: COMPLETED"}
    newer = {"id": 11, "body": "[RELAY:ACK]\nrelay_run_id: new-run\nbranch: sci-program"}
    same = {"id": 12, "body": "[RELAY:ACK]\nrelay_run_id: old-run"}
    assert hw.superseded_by([terminal, same], terminal, "old-run") is None
    assert hw.superseded_by([terminal, newer], terminal, "old-run") == "new-run"


def test_unreadable_history_never_spams(monkeypatch):
    def boom(_):
        raise RuntimeError("api down")
    monkeypatch.setattr(hw, "api", boom)
    assert hw.prior_resignals("x") >= 2
