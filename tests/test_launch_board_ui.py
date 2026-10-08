"""Issue #21 regression: launch board UI helpers + storage status.

Run: python3 -m unittest tests.test_launch_board_ui
Executes frontend/src/utils/launchBoard.js under node (skipped if node is absent);
checks the JSX wiring statically.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from app.services import launch_board as lb

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
UTIL = os.path.join(ROOT, "frontend", "src", "utils", "launchBoard.js")
NODE = shutil.which("node")

PRELUDE = """
import { pathToFileURL } from 'node:url'
const m = await import(pathToFileURL(process.argv[2]).href)
const out = {}
"""


def run_js(body):
    if not NODE:
        raise unittest.SkipTest("node not available")
    with tempfile.TemporaryDirectory() as d:
        script = os.path.join(d, "t.mjs")
        with open(script, "w") as f:
            f.write(PRELUDE + body + "\nconsole.log(JSON.stringify(out))\n")
        p = subprocess.run([NODE, script, UTIL], capture_output=True, text=True, timeout=60, cwd=d)
    if p.returncode != 0:
        raise AssertionError(p.stderr)
    return json.loads(p.stdout.strip().splitlines()[-1])


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Helpers(unittest.TestCase):
    def test_malformed_reply_is_failure(self):
        o = run_js("""
out.a = m.normalizeBoard(null).ok; out.b = m.normalizeBoard({}).ok
out.c = m.normalizeBoard({ lanes: { active: [], backlog: [] }, queue: [] }).ok
out.d = m.normalizeBoard({ lanes: { active: [], backlog: [], archived: [] }, queue: [] }).ok""")
        self.assertEqual((o["a"], o["b"], o["c"], o["d"]), (False, False, False, True))

    def test_gate_needs_verified_and_ref(self):
        o = run_js("""
const p = { evidence: { source: { state: 'verified', ref: 'abc' }, test: { state: 'verified', ref: null },
  deployment: { state: 'claimed', ref: 'x' }, verification: { state: 'none', ref: null } } }
const s = m.gatesSummary(p); out.met = s.met; out.missing = s.missing; out.all = s.allMet
out.empty = m.gatesSummary({}).met""")
        self.assertEqual((o["met"], o["all"], o["empty"]), (1, False, 0))
        self.assertEqual(o["missing"], ["test", "deployment", "verification"])

    def test_live_status_never_guessed(self):
        o = run_js("""
const st = { worker: { project: 'A', display: 'Working' } }
out.hit = m.liveStatusFor({ name: 'A' }, st, true)
out.other = m.liveStatusFor({ name: 'B' }, st, true)
out.notWorking = m.liveStatusFor({ name: 'A' }, st, false)
out.none = m.liveStatusFor({ name: 'A' }, null, true)
out.lines = m.statusLines({ working_status: 'no live evidence', last_completed: { at: 't', summary: 'did x' } })""")
        self.assertEqual(o["hit"], "Working")
        self.assertEqual((o["other"], o["notWorking"], o["none"]), ("no live evidence",) * 3)
        self.assertEqual(o["lines"]["working"], "no live evidence")
        self.assertIn("did x", o["lines"]["lastCompleted"])

    def test_actions_validate_input(self):
        o = run_js("""
out.p0 = m.buildAction('priority', { priority: '0' }); out.pf = m.buildAction('priority', { priority: '1.5' })
out.p2 = m.buildAction('priority', { priority: ' 2 ' })
out.evNoRef = m.buildAction('evidence', { kind: 'test', state: 'verified', ref: ' ' })
out.evNone = m.buildAction('evidence', { kind: 'test', state: 'none', ref: '' })
out.badKind = m.buildAction('evidence', { kind: 'x', state: 'none' })
out.lane = m.buildAction('lane', { lane: 'nope' }); out.task = m.buildAction('task', { summary: '  ' })
out.unknown = m.buildAction('delete', {})""")
        self.assertEqual((o["p0"], o["pf"], o["p2"]), (None, None, {"priority": 2}))
        self.assertIsNone(o["evNoRef"])
        self.assertEqual(o["evNone"]["state"], "none")
        for k in ("badKind", "lane", "task", "unknown"):
            self.assertIsNone(o[k], k)

    def test_unapproved_cannot_be_offered_active(self):
        o = run_js("""
out.un = m.laneActions({ lane: 'backlog', approved: false })
out.ap = m.laneActions({ lane: 'backlog', approved: true })
out.arch = m.laneActions({ lane: 'archived', approved: true })""")
        self.assertEqual(o["un"], ["archived"])
        self.assertEqual(o["ap"], ["active", "archived"])
        self.assertEqual(o["arch"], ["active", "backlog"])

    def test_product_text_separates_tasks_from_product(self):
        o = run_js("""
out.t = m.productText({ tasks_completed: 3, product_state: 'in_progress' })
out.d = m.productText({ tasks_completed: 3, product_state: 'complete' })""")
        self.assertIn("product not complete", o["t"])
        self.assertIn("Product complete", o["d"])

    def test_error_text_never_empty(self):
        o = run_js("""
out.a = m.errorText({ detail: 'nope' }); out.b = m.errorText(null); out.c = m.errorText({ message: 'boom' })""")
        self.assertEqual((o["a"], o["b"], o["c"]), ("nope", "Request failed", "boom"))


class Wiring(unittest.TestCase):
    def test_control_room_mounts_board_and_collapses_log(self):
        s = read("frontend", "src", "pages", "god", "GodRelayControlRoom.jsx")
        self.assertIn("<GodLaunchBoard", s)
        self.assertIn('<details style={card} data-testid="relay-technical-log"', s)

    def test_board_uses_god_api_and_shows_errors(self):
        s = read("frontend", "src", "pages", "god", "GodLaunchBoard.jsx")
        u = read("frontend", "src", "utils", "launchBoard.js")
        self.assertIn("/god/launch-board", u)
        self.assertIn("Not saved", s)
        # success message only after the post and reload succeeded (inside try, after awaits)
        self.assertLess(s.index("await api.post(`${BOARD_URL}/projects/"), s.index("Saved: "))
        self.assertIn("failedBoard(errorText(e))", s)


class Storage(unittest.TestCase):
    def test_unconfigured_path_warns(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LAUNCH_BOARD_PATH", None)
            s = lb.storage_status()
        self.assertFalse(s["durable"])
        self.assertIn("LAUNCH_BOARD_PATH", s["warning"])

    def test_configured_path_no_warning(self):
        with mock.patch.dict(os.environ, {"LAUNCH_BOARD_PATH": "/var/data/b.json"}):
            s = lb.storage_status()
        self.assertTrue(s["durable"])
        self.assertIsNone(s["warning"])


class WriteProtection(unittest.TestCase):
    """Every board write sends expected_version + a unique Idempotency-Key; 409 reloads."""

    def test_helpers(self):
        o = run_js("""
out.k1 = m.newRequestKey(1000, () => 0.5); out.k2 = m.newRequestKey(1000, () => 0.5)
out.v = m.withExpectedVersion({ lane: 'active' }, { id: 1, version: 3 })
out.nov = m.withExpectedVersion({ lane: 'active' }, { id: 1 })
out.none = m.withExpectedVersion(null, { version: 3 })
out.h = m.writeHeaders('abc')
out.s409 = m.isStale({ status: 409 }); out.s400 = m.isStale({ status: 400 }); out.snull = m.isStale(null)""")
        self.assertNotEqual(o["k1"], o["k2"])
        self.assertTrue(o["k1"].startswith("lb-") and len(o["k1"]) <= 120)
        self.assertEqual(o["v"], {"lane": "active", "expected_version": 3})
        self.assertEqual(o["nov"], {"lane": "active"})
        self.assertIsNone(o["none"])
        self.assertEqual(o["h"], {"headers": {"Idempotency-Key": "abc"}})
        self.assertEqual((o["s409"], o["s400"], o["snull"]), (True, False, False))

    def test_board_sends_version_and_key_and_reloads_on_409(self):
        s = read("frontend", "src", "pages", "god", "GodLaunchBoard.jsx")
        self.assertIn("withExpectedVersion(buildAction(action, input), project)", s)
        self.assertEqual(s.count("writeHeaders(newRequestKey())"), 2)   # every write: actions + add
        stale = s[s.index("if (isStale(e))"):]
        self.assertLess(stale.index("await reload()"), stale.index("Not saved"))

    def test_client_and_cors_carry_the_header(self):
        c = read("frontend", "src", "api", "client.js")
        self.assertIn("post: (path, body, opts = {})", c)
        from app.main import BROWSER_HEADERS
        self.assertIn("Idempotency-Key", BROWSER_HEADERS)


if __name__ == "__main__":
    unittest.main()
