"""Dependency-free regression tests for scripts/relay/relay_state.py (python3 -m unittest)."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "relay"))
import relay_state as rs  # noqa: E402

T0 = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)
_n = [0]


def at(m):
    return (T0 + timedelta(minutes=m)).isoformat()


def cm(login, body, m):
    _n[0] += 1
    return {"id": _n[0], "user": {"login": login}, "author_association": "OWNER",
            "created_at": at(m), "body": body}


def directive(rid, m, parent="-", branch="platform-dev"):
    return cm("MikeSimmonsAI", "[RELAY:DIRECTIVE]\nrelay_run_id: %s\nparent_run_id: %s\nPROJECT: P-%s\nBRANCH: %s"
              % (rid, parent, rid, branch), m)


def ack(rid, m, parent="-", branch="platform-dev"):
    return cm("github-actions[bot]", "[RELAY:ACK] relay_run_id: %s\nparent_run_id: %s\nproject: P-%s\nbranch: %s"
              % (rid, parent, rid, branch), m)


def status(rid, st, m, extra="", branch="platform-dev"):
    return cm("claude[bot]", "[RELAY:CLAUDE_STATUS]\nrelay_run_id: %s\nSTATUS: %s\nPROJECT: P-%s\nBRANCH: %s\n%s"
              % (rid, st, rid, branch, extra), m)


def build(comments, runs=None, now_min=10):
    return rs.build(comments, runs, T0 + timedelta(minutes=now_min))


class RelayStateTests(unittest.TestCase):
    def test_no_active_worker_is_idle(self):
        out = build([directive("a", 0), ack("a", 1), status("a", "COMPLETED", 5)])
        self.assertEqual(out["worker"]["state"], "idle")
        self.assertIn("no active", out["worker"]["display"].lower())

    def test_directive_without_ack_is_queued_not_working(self):
        w = build([directive("a", 0)])["worker"]
        self.assertEqual((w["state"], w["stage"]), ("queued", "Queued"))
        self.assertNotEqual(w["display"], "Working")

    def test_terminal_never_working(self):
        for st in ("BLOCKED", "COMPLETED", "APPROVAL_REQUIRED"):
            out = build([directive("a", 0), ack("a", 1), status("a", "WORKING", 2), status("a", st, 5)])
            self.assertEqual(out["worker"]["state"], "idle", st)
            self.assertEqual(out["history"][0]["status"], st)

    def test_working_status_after_terminal_ignored(self):
        out = build([directive("a", 0), ack("a", 1), status("a", "BLOCKED", 3, "BLOCKERS: x"),
                     status("a", "WORKING", 4)])
        self.assertEqual(out["worker"]["state"], "idle")

    def test_newest_active_wins_over_older_stale(self):
        out = build([directive("old", 0), ack("old", 1), directive("new", 5), ack("new", 6)], now_min=8)
        self.assertEqual(out["worker"]["relay_run_id"], "new")
        self.assertEqual([c["relay_run_id"] for c in out["queued_behind"]], ["old"])

    def test_superseded_child_directive(self):
        cs = [directive("p", 0), ack("p", 1), status("p", "BLOCKED", 4, "BLOCKERS: waiting"),
              directive("c", 6, parent="p"), ack("c", 7, parent="p")]
        out = build(cs, now_min=9)
        self.assertEqual(out["worker"]["relay_run_id"], "c")
        self.assertEqual(out["history"][0]["relay_run_id"], "p")
        self.assertEqual(out["history"][0]["blocked_reason"], "waiting")

    def test_parent_without_terminal_is_superseded_not_working(self):
        cs = [directive("p", 0), ack("p", 1), directive("c", 6, parent="p"), ack("c", 7, parent="p")]
        out = build(cs, now_min=9)
        self.assertEqual(out["worker"]["relay_run_id"], "c")
        self.assertEqual(out["history"][0]["state"], "superseded")

    def test_stale_hung_threshold(self):
        cs = [directive("a", 0), ack("a", 1), status("a", "WORKING", 2)]
        self.assertEqual(build(cs, now_min=30)["worker"]["health"], "ok")          # 29 min
        w = build(cs, now_min=32)["worker"]                                         # 31 min
        self.assertEqual((w["health"], w["display"]), ("STALE/HUNG", "STALE/HUNG"))

    def test_actions_terminal_without_relay_terminal_is_mismatch(self):
        runs = [{"relay_run_id": "a", "id": 99, "status": "completed", "conclusion": "failure"}]
        out = build([directive("a", 0), ack("a", 1), status("a", "WORKING", 2)], runs)
        self.assertEqual(out["worker"]["state"], "idle")
        self.assertEqual(out["history"][0]["display"], "RELAY_STATE_MISMATCH")

    def test_forged_status_and_ack_ignored(self):
        forged = cm("random-user", "[RELAY:ACK] relay_run_id: x\nbranch: platform-dev", 1)
        out = build([directive("x", 0), forged])
        self.assertEqual(out["worker"]["state"], "queued")
        bad = status("a", "COMPLETED", 3)
        bad["user"]["login"] = "random-user"
        out = build([directive("a", 0), ack("a", 1), bad])
        self.assertEqual(out["worker"]["state"], "active")

    def test_identity_stage_and_checkpoint(self):
        runs = [{"relay_run_id": "a", "id": 4242, "status": "in_progress", "head_sha": "abcdef1234567",
                 "updated_at": at(8)}]
        w = build([directive("a", 0), ack("a", 1), status("a", "WORKING", 5, "COMMITS: c7bab62 test")], runs)["worker"]
        self.assertEqual((w["actions_run_id"], w["branch"], w["stage"]), (4242, "platform-dev", "Commit/Push"))
        self.assertEqual((w["checkpoint_sha"], w["checkpoint_age_min"], w["elapsed_min"]), ("c7bab62", 2, 9))
        self.assertNotIn("percent", w)
        self.assertNotIn("eta", w)

    def test_completed_today_excludes_superseded_blocked(self):
        cs = [directive("a", 0), ack("a", 1), status("a", "COMPLETED", 3, "COMPLETED: did it"),
              directive("b", 4), ack("b", 5), status("b", "BLOCKED", 6),
              directive("c", 7), ack("c", 8)]
        cs.append(directive("d", 9, parent="c"))
        cs.append(ack("d", 10, parent="c"))
        out = build(cs, now_min=12)
        self.assertEqual(out["completed_today"], 1)
        self.assertEqual(out["worker"]["relay_run_id"], "d")

    def test_suggested_next_from_newest_lineage(self):
        cs = [directive("a", 0), ack("a", 1), status("a", "COMPLETED", 2, "NEXT RECOMMENDED ACTION: old thing"),
              directive("b", 4), ack("b", 5), status("b", "COMPLETED", 6, "NEXT RECOMMENDED ACTION: new thing")]
        self.assertEqual(build(cs, now_min=8)["suggested_next"], "new thing")


if __name__ == "__main__":
    unittest.main()
