import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "relay"))
from active_stall_watchdog import stale_runs

NOW = datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc)
def comment(i, marker, rid, minutes, branch="platform-dev", status="", actor="github-actions[bot]"):
    body = f"{marker}\nrelay_run_id: {rid}\nbranch: {branch}\n"
    if status:
        body += f"STATUS: {status}\n"
    return {"id": i, "body": body, "created_at": (NOW-timedelta(minutes=minutes)).isoformat(), "user": {"login": actor}}

class StallTests(unittest.TestCase):
    def test_stalled_ack(self):
        self.assertEqual(len(stale_runs([comment(1, "[RELAY:ACK]", "a", 30)], NOW)), 1)
    def test_recent_ack(self):
        self.assertEqual(stale_runs([comment(1, "[RELAY:ACK]", "a", 5)], NOW), [])
    def test_recent_heartbeat(self):
        cs = [comment(1, "[RELAY:ACK]", "a", 30), comment(2, "[RELAY:CLAUDE_STATUS]", "a", 5, status="WORKING")]
        self.assertEqual(stale_runs(cs, NOW), [])
    def test_terminal(self):
        cs = [comment(1, "[RELAY:ACK]", "a", 30), comment(2, "[RELAY:CLAUDE_STATUS]", "a", 25, status="COMPLETED")]
        self.assertEqual(stale_runs(cs, NOW), [])
    def test_forged_ack(self):
        self.assertEqual(stale_runs([comment(1, "[RELAY:ACK]", "a", 30, actor="attacker")], NOW), [])

if __name__ == "__main__":
    unittest.main()
