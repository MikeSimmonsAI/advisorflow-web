"""Issue #21 regression: Control Room refresh, truthful UP NEXT, blockers.

Run: python3 -m unittest tests.test_relay_control_room_refresh
Executes frontend/src/utils/relayControlRoom.js under node (skipped if node is absent).
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
UTIL = os.path.abspath(os.path.join(ROOT, "frontend", "src", "utils", "relayControlRoom.js"))
NODE = shutil.which("node")

PRELUDE = """
import { pathToFileURL } from 'node:url'
const m = await import(pathToFileURL(process.argv[2]).href)
const good = (gen, extra = {}) => ({ available: true, generated_at: gen,
  worker: { state: 'idle', display: 'Idle' }, queued_behind: [], history: [], completed_today: 0,
  suggested_next: '', ...extra })
function harness(fetchImpl) {
  const log = { states: [], errors: [], loading: [] }
  const r = m.createRefresher({ fetchState: fetchImpl, timeoutMs: 50,
    onLoading: (v) => log.loading.push(v), onState: (s) => log.states.push(s), onError: (s) => log.errors.push(s) })
  return { r, log }
}
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


class Refresh(unittest.TestCase):
    def test_success_updates_state_once(self):
        o = run_js("""
const { r, log } = harness(async () => good('2026-10-07T23:00:00Z'))
await r(true)
out.states = log.states.length; out.errors = log.errors.length; out.loading = log.loading""")
        self.assertEqual((o["states"], o["errors"], o["loading"]), (1, 0, [True, False]))

    def test_http_and_network_failure_are_errors_not_success(self):
        o = run_js("""
const a = harness(async () => { const e = new Error('Request failed'); e.status = 500; throw e })
await a.r(true)
const b = harness(async () => { throw new Error('Unable to reach the server.') })
await b.r(true)
out.s = a.log.states.length + b.log.states.length
out.e = [a.log.errors[0].available, b.log.errors[0].available]
out.reason = b.log.errors[0].reason""")
        self.assertEqual(o["s"], 0)
        self.assertEqual(o["e"], [False, False])
        self.assertIn("Unable to reach", o["reason"])

    def test_garbage_200_is_failure(self):
        o = run_js("""
const res = []
for (const bad of ['<html>proxy</html>', null, [], {}, { available: true }, { available: true, worker: {} }]) {
  const h = harness(async () => bad); await h.r(true)
  res.push([h.log.states.length, h.log.errors.length])
}
out.res = res""")
        self.assertEqual(o["res"], [[0, 1]] * 6)

    def test_server_unavailable_payload_is_error(self):
        o = run_js("""
const h = harness(async () => ({ available: false, reason: 'GitHub read failed' })); await h.r(true)
out.s = h.log.states.length; out.reason = h.log.errors[0].reason""")
        self.assertEqual(o["s"], 0)
        self.assertEqual(o["reason"], "GitHub read failed")

    def test_timeout_is_failure(self):
        o = run_js("""
const h = harness(() => new Promise(() => {})); await h.r(true)
out.e = h.log.errors.length; out.reason = h.log.errors[0].reason; out.loading = h.log.loading""")
        self.assertEqual(o["e"], 1)
        self.assertIn("Timed out", o["reason"])
        self.assertEqual(o["loading"], [True, False])

    def test_stale_older_reply_never_overwrites_newer(self):
        o = run_js("""
const resolvers = []
const h = harness(() => new Promise(res => resolvers.push(res)))
const first = h.r(false); const second = h.r(true)
resolvers[1](good('2026-10-07T23:10:00Z', { completed_today: 2 }))
await second
resolvers[0](good('2026-10-07T23:00:00Z', { completed_today: 1 }))
await first
out.states = h.log.states.map(s => s.completed_today); out.errors = h.log.errors.length""")
        self.assertEqual(o["states"], [2])
        self.assertEqual(o["errors"], 0)

    def test_older_generated_at_from_newest_request_is_rejected(self):
        o = run_js("""
const seq = [good('2026-10-07T23:10:00Z'), good('2026-10-07T23:00:00Z')]
const h = harness(async () => seq.shift())
await h.r(true); await h.r(true)
out.states = h.log.states.length; out.reason = h.log.errors[0] && h.log.errors[0].reason""")
        self.assertEqual(o["states"], 1)
        self.assertIn("older", o["reason"])

    def test_concurrent_clicks_each_make_a_new_request_and_settle_loading(self):
        o = run_js("""
const urls = []; const resolvers = []
const h = harness((url) => { urls.push(url); return new Promise(res => resolvers.push(res)) })
const a = h.r(true), b = h.r(true), c = h.r(true)
resolvers[2](good('2026-10-07T23:10:00Z')); resolvers[0](good('2026-10-07T23:09:00Z')); resolvers[1](good('2026-10-07T23:09:30Z'))
await Promise.all([a, b, c])
out.unique = new Set(urls).size; out.states = h.log.states.length; out.loading = h.log.loading.slice(-1)""")
        self.assertEqual((o["unique"], o["states"], o["loading"]), (3, 1, [False]))

    def test_stale_check_flag_uses_last_success_only(self):
        o = run_js("""
out.never = m.isCheckStale(null, 1000); out.fresh = m.isCheckStale(1000, 1000 + m.POLL_MS)
out.old = m.isCheckStale(1000, 1000 + m.STALE_AFTER_MS + 1)""")
        self.assertEqual((o["never"], o["fresh"], o["old"]), (True, False, True))


class Queue(unittest.TestCase):
    def test_empty_queue_is_empty_not_invented(self):
        o = run_js("""
out.empty = m.upNext(good('2026-10-07T23:00:00Z'))
out.failed = m.upNext(m.failedState('x'))
out.active_only = m.upNext(good('t', { worker: { state: 'active', display: 'Working', relay_run_id: 'r1' } }))""")
        self.assertEqual((o["empty"], o["failed"], o["active_only"]), ([], [], []))

    def test_only_queued_cards_listed_with_fields(self):
        o = run_js("""
const q = { state: 'queued', relay_run_id: 'r2', project: 'P', display: 'Queued', last_update_at: '2026-10-07T23:00:00Z', health: 'ok' }
const stale = { ...q, relay_run_id: 'r3', display: 'Queued - STALE', health: 'STALE' }
const s = good('t', { worker: { state: 'active', display: 'Working', relay_run_id: 'r1' },
  queued_behind: [q, stale, { state: 'active', relay_run_id: 'r9' }],
  suggested_next: 'do a thing' })
out.items = m.upNext(s); out.rec = m.recommendation(s)""")
        ids = [i["relay_run_id"] for i in o["items"]]
        self.assertEqual(ids, ["r2", "r3"])
        self.assertEqual(o["items"][0]["start"], "auto-start")
        self.assertIn("unassigned", o["items"][0]["owner"])
        self.assertTrue(o["items"][1]["stale"])
        self.assertEqual(o["rec"], "do a thing")

    def test_recommendation_not_in_queue_and_hidden_when_unavailable(self):
        o = run_js("""
out.q = m.upNext(good('t', { suggested_next: 'x' })); out.rec = m.recommendation(m.failedState('x'))""")
        self.assertEqual((o["q"], o["rec"]), ([], ""))

    def test_blockers_actionable(self):
        o = run_js("""
const s = good('t', { history: [
  { state: 'terminal', result: 'BLOCKED', relay_run_id: 'a', blocked_reason: 'need repo' },
  { state: 'terminal', result: 'APPROVAL_REQUIRED', relay_run_id: 'b', blocked_reason: '' },
  { state: 'terminal', result: 'COMPLETED', relay_run_id: 'c' },
  { state: 'mismatch', relay_run_id: 'd', health: 'Actions run is failure' }],
  worker: { state: 'active', display: 'STALE/HUNG', health: 'STALE/HUNG', health_detail: 'past lease', relay_run_id: 'w' } })
out.b = m.blockers(s); out.none = m.blockers(m.failedState('x'))""")
        self.assertEqual([b["relay_run_id"] for b in o["b"]], ["w", "a", "b", "d"])
        self.assertEqual(o["b"][2]["reason"], "no reason reported")
        self.assertEqual(o["none"], [])


class PageWiring(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "frontend", "src", "pages", "god", "GodRelayControlRoom.jsx"), encoding="utf-8") as f:
            self.page = f.read()

    def test_suggested_next_replaced_and_failure_not_called_success(self):
        self.assertNotIn("Suggested next", self.page)
        self.assertIn("UP NEXT", self.page)
        self.assertIn("last attempt FAILED", self.page)
        self.assertIn("setLastFailed(Date.now())", self.page)
        # a failed fetch must not advance the successful-check time
        err = self.page.split("onError:")[1].split("\n")[0]
        self.assertNotIn("setLastOk", err)


if __name__ == "__main__":
    unittest.main()
