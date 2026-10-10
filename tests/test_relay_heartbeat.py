"""Heartbeat / silence detection for the relay worker and the overnight desktop runner."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "relay"))
sys.path.insert(0, os.path.dirname(__file__))
import relay_state as rs  # noqa: E402
from test_relay_state import T0, ack, build, directive, status  # noqa: E402


class SilenceTests(unittest.TestCase):
    def test_silent_worker_inside_the_lease_reads_stale(self):
        w = build([directive("a", 0), ack("a", 1)], now_min=23)["worker"]
        self.assertEqual(w["health"], "STALE")
        self.assertIn("no progress update for 22 min", w["health_detail"])

    def test_progress_updates_keep_it_fresh(self):
        w = build([directive("a", 0), ack("a", 1), status("a", "WORKING", 15)], now_min=28)["worker"]
        self.assertEqual((w["display"], w["health"]), ("Working", "ok"))

    def test_lease_still_caps_a_run_that_keeps_reporting(self):
        w = build([directive("a", 0), ack("a", 1), status("a", "WORKING", 29)], now_min=35)["worker"]
        self.assertEqual(w["health"], "STALE/HUNG")

    def test_terminal_run_is_never_stale(self):
        out = build([directive("a", 0), ack("a", 1), status("a", "COMPLETED", 2)], now_min=90)
        self.assertEqual(out["worker"]["state"], "idle")


def hb(at_min, due_min, state="running"):
    return {"at": (T0 + timedelta(minutes=at_min)).isoformat(),
            "next_checkin_by": (T0 + timedelta(minutes=due_min)).isoformat(),
            "state": state, "project": "SCI", "task": "visual QA", "branch": "b", "commit": "abc1234",
            "done": "x", "tests": "y", "blocker": "none", "next": "z"}


class DesktopRunnerTests(unittest.TestCase):
    def now(self, m):
        return T0 + timedelta(minutes=m)

    def test_no_heartbeat_is_unknown_never_working(self):
        self.assertEqual(rs.desktop_runner(None, self.now(0))["state"], "unknown")

    def test_fresh_heartbeat_is_working(self):
        r = rs.desktop_runner(hb(0, 20), self.now(15))
        self.assertEqual((r["state"], r["since_heartbeat_min"], r["project"]), ("active", 15, "SCI"))

    def test_missed_checkin_past_grace_is_stale(self):
        r = rs.desktop_runner(hb(0, 20), self.now(31))
        self.assertEqual((r["state"], r["health"]), ("stale", "STALE"))
        self.assertIn("31 min ago", r["health_detail"])

    def test_within_grace_is_not_stale(self):
        self.assertEqual(rs.desktop_runner(hb(0, 20), self.now(29))["state"], "active")

    def test_reported_complete_or_blocked_is_not_stale(self):
        self.assertEqual(rs.desktop_runner(hb(0, 20, "complete"), self.now(500))["state"], "complete")
        self.assertEqual(rs.desktop_runner(hb(0, 20, "blocked"), self.now(500))["state"], "blocked")

    def test_simulated_stall_then_recovery(self):
        stalled = rs.desktop_runner(hb(0, 20), self.now(45))
        recovered = rs.desktop_runner(hb(44, 64), self.now(45))
        self.assertEqual((stalled["state"], recovered["state"]), ("stale", "active"))


class ControlRoomPayloadTests(unittest.TestCase):
    def test_unreadable_heartbeat_reads_unknown_and_keeps_relay_available(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from app.services import relay_control_room as rcr

        def boom():
            raise rcr.RelayUnavailable("nope")
        r = rcr.desktop_runner_state(fetch=boom, now=T0)
        self.assertEqual(r["state"], "unknown")
        ok = rcr.desktop_runner_state(fetch=lambda: hb(0, 20), now=T0 + timedelta(minutes=5))
        self.assertEqual(ok["state"], "active")


if __name__ == "__main__":
    unittest.main()
