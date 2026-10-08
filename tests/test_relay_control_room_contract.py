"""Dependency-free (stdlib unittest) Control Room contract + behaviour tests.

Run: python3 -m unittest tests.test_relay_control_room_contract
Needs no pytest/FastAPI/node/network. Router and UI are checked statically; the
service is executed for real with GitHub stubbed.
"""
import ast
import os
import re
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.services import relay_control_room as rcr  # stdlib-only import chain

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SECRET = "ghp_SUPERSECRETTOKENVALUE123"
T0 = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(minutes=5)
_n = [0]


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def _cm(login, body, m):
    _n[0] += 1
    return {"id": _n[0], "user": {"login": login}, "author_association": "OWNER",
            "created_at": (T0 + timedelta(minutes=m)).isoformat(), "body": body}


def _directive(rid, parent="-"):
    return _cm("MikeSimmonsAI", "[RELAY:DIRECTIVE]\nrelay_run_id: %s\nparent_run_id: %s\nPROJECT: P\nBRANCH: platform-dev" % (rid, parent), 0)


def _ack(rid, parent="-"):
    return _cm("github-actions[bot]", "[RELAY:ACK] relay_run_id: %s\nparent_run_id: %s\nproject: P\nbranch: platform-dev" % (rid, parent), 1)


def _terminal(rid, status):
    return _cm("github-actions[bot]", "[RELAY:CLAUDE_STATUS]\nrelay_run_id: %s\nSTATUS: %s\nPROJECT: P\nBRANCH: platform-dev\nNEXT RECOMMENDED ACTION: x" % (rid, status), 3)


class RouterContract(unittest.TestCase):
    def setUp(self):
        self.src = _read("app", "routers", "god_relay_router.py")
        self.tree = ast.parse(self.src)

    def _routes(self):
        out = []
        for n in ast.walk(self.tree):
            if isinstance(n, ast.FunctionDef):
                for d in n.decorator_list:
                    if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                            and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"):
                        out.append((d.func.attr, n))
        return out

    def test_get_only(self):
        # The staging router also carries Give Direction (POST /direction, /package*),
        # which writes only a MIKE_INPUT audit comment. The worker view is GET-only.
        methods = {fn.name: m for m, fn in self._routes()}
        self.assertEqual(methods["relay_worker"], "get")
        body = self.src[self.src.index("def relay_worker"):]
        body = body.split("\n@router")[0]
        for bad in ("post", "put", "patch", "delete", "_gh(", "urlopen"):
            self.assertNotIn(bad, body)

    def test_god_protected_on_every_route(self):
        for _, fn in self._routes():
            defaults = [ast.unparse(d) for d in fn.args.defaults]
            self.assertTrue(any("Depends(require_god)" in d for d in defaults), fn.name)
        self.assertIn("from app.deps import require_god", self.src)

    def test_require_god_is_god_admin_only(self):
        deps = _read("app", "deps.py")
        m = re.search(r"def require_god\(.*?\n(?=\ndef )", deps, re.S)
        self.assertIn('user.role != "god_admin"', m.group(0))

    def test_prefix_and_registration(self):
        self.assertIn('prefix="/god/relay"', self.src)
        main = _read("app", "main.py")
        self.assertIn("from app.routers.god_relay_router import router as god_relay_router", main)
        self.assertIn("app.include_router(god_relay_router)", main)

    def test_no_cache_headers_complete(self):
        self.assertIn("rcr.NO_CACHE_HEADERS", self.src)
        h = {k.lower(): v for k, v in rcr.NO_CACHE_HEADERS.items()}
        self.assertIn("no-store", h["cache-control"])
        self.assertIn("no-cache", h["cache-control"])
        self.assertEqual(h["pragma"], "no-cache")
        self.assertEqual(h["expires"], "0")

    def test_headers_set_for_success_and_failure(self):
        # Failure is a normal return value, so the header loop runs for both.
        body = self.src[self.src.index("def relay_worker"):]
        body = body.split("\n@router")[0]   # up to the next route, if any
        self.assertLess(body.index("NO_CACHE_HEADERS"), body.index("return rcr.get_state()"))
        self.assertNotIn("raise", body)

    def test_service_uses_only_get(self):
        src = _read("app", "services", "relay_control_room.py")
        self.assertIn('method="GET"', src)
        for bad in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "data="):
            self.assertNotIn(bad, src)


class ServiceBehaviour(unittest.TestCase):
    def test_working_has_no_percent_or_eta(self):
        s = rcr.get_state(lambda: [_directive("a"), _ack("a")], lambda: [], NOW)
        self.assertEqual(s["worker"]["display"], "Working")
        self.assertIsNone(s["worker"]["actions_run_id"])
        dumped = str(s).lower()
        self.assertNotIn("percent", dumped)
        self.assertNotIn("eta", s)
        self.assertNotIn("eta", s["worker"])

    def test_failure_returns_unavailable_never_working(self):
        def boom():
            raise rcr.RelayUnavailable("down")
        for exc in (boom, lambda: 1 / 0):
            s = rcr.get_state(exc, lambda: [], NOW)
            self.assertFalse(s["available"])
            self.assertEqual(s["worker"]["state"], "unavailable")
            self.assertNotEqual(s["worker"]["display"], "Working")
            self.assertEqual(s["queued_behind"], [])
            self.assertEqual(s["history"], [])

    def test_unexpected_error_text_not_returned(self):
        def boom():
            raise ValueError("leaked %s" % SECRET)
        self.assertNotIn(SECRET, repr(rcr.get_state(boom, lambda: [], NOW)))

    def test_http_failure_never_leaks_token(self):
        env = {"RELAY_READ_TOKEN": SECRET, "RELAY_REPOSITORY": "o/r"}
        errs = (urllib.error.URLError("auth failed Bearer %s" % SECRET),
                RuntimeError("Authorization: Bearer %s" % SECRET),
                urllib.error.HTTPError("https://x", 401, "bad token " + SECRET, {}, None))
        for err in errs:
            with mock.patch.dict(os.environ, env), mock.patch.object(urllib.request, "urlopen", side_effect=err):
                s = rcr.get_state(now=NOW)
            self.assertFalse(s["available"])
            self.assertNotIn(SECRET, repr(s))
            self.assertNotEqual(s["worker"]["display"], "Working")

    def test_missing_credentials_unavailable(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            s = rcr.get_state(now=NOW)
        self.assertFalse(s["available"])
        self.assertIn("not configured", s["reason"])

    def test_outbound_request_is_get(self):
        seen = {}

        class R:
            def read(self):
                return b"[]"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake(req, timeout=None):
            seen["m"] = req.get_method()
            return R()
        with mock.patch.dict(os.environ, {"RELAY_READ_TOKEN": SECRET, "RELAY_REPOSITORY": "o/r"}), \
                mock.patch.object(urllib.request, "urlopen", fake):
            rcr._get("/x")
        self.assertEqual(seen["m"], "GET")

    def test_terminal_states_never_working(self):
        for st in ("COMPLETED", "BLOCKED", "APPROVAL_REQUIRED"):
            cms = [_directive("a"), _ack("a"), _terminal("a", st)]
            s = rcr.get_state(lambda cms=cms: cms, lambda: [], NOW)
            self.assertNotEqual(s["worker"].get("display"), "Working", st)
            self.assertNotEqual(s["worker"].get("state"), "active", st)

    def test_superseded_parent_never_working(self):
        cms = [_directive("a"), _ack("a"), _directive("b", "a"), _ack("b", "a")]
        s = rcr.get_state(lambda: cms, lambda: [], NOW)
        self.assertEqual(s["worker"]["relay_run_id"], "b")
        a = [h for h in s["history"] if h["relay_run_id"] == "a"]
        self.assertEqual(a[0]["display"], "SUPERSEDED")
        self.assertEqual(a[0]["superseded_by"], "b")

    def test_finished_actions_without_terminal_is_not_working(self):
        runs = [{"id": 5, "relay_run_id": "a", "status": "completed", "conclusion": "success",
                 "head_sha": None, "updated_at": NOW.isoformat()}]
        s = rcr.get_state(lambda: [_directive("a"), _ack("a")], lambda: runs, NOW)
        self.assertNotEqual(s["worker"]["display"], "Working")

    def test_null_and_unmapped_run_ids_do_not_crash(self):
        for runs in (None, [], [{"id": None, "head_sha": None}], [{"id": 1, "head_sha": None, "status": None}]):
            s = rcr.get_state(lambda: [_directive("a"), _ack("a")], lambda r=runs: r, NOW)
            self.assertTrue(s["available"])
            self.assertIsNone(s["worker"]["actions_run_id"])

    def test_runs_failure_degrades_to_null_run_id(self):
        def bad():
            raise rcr.RelayUnavailable("x")
        s = rcr.get_state(lambda: [_directive("a"), _ack("a")], bad, NOW)
        self.assertTrue(s["available"])
        self.assertIsNone(s["worker"]["actions_run_id"])

    def test_poll_constant(self):
        self.assertEqual(rcr.POLL_SECONDS, 20)
        self.assertEqual(rcr.unavailable("x")["poll_seconds"], 20)


class UiContract(unittest.TestCase):
    def setUp(self):
        self.page = _read("frontend", "src", "pages", "god", "GodRelayControlRoom.jsx")
        self.util = _read("frontend", "src", "utils", "relayControlRoom.js")

    def test_authoritative_endpoint_cache_busted(self):
        self.assertIn("/god/relay/worker?_=", self.util)
        self.assertIn("_seq += 1", self.util)
        # every request (poll and manual) goes through the refresher: new URL + no-store, never reused
        self.assertIn("fetchState(stateUrl(), { cache: 'no-store' })", self.util)
        self.assertIn("api.get(url, opts)", self.page)
        self.assertNotRegex(self.page, r"fetch\(|axios|github\.com")

    def test_polls_every_20_seconds(self):
        self.assertIn("export const POLL_MS = 20000", self.util)
        self.assertIn("setInterval(() => refresh(false), POLL_MS)", self.page)
        self.assertIn("clearInterval(poll)", self.page)

    def test_manual_check_is_never_disabled_by_a_pending_request(self):
        self.assertNotIn("disabled={loading}", self.page)
        self.assertIn("refresh(true)", self.page)

    def test_fetch_failure_clears_active_state(self):
        # createRefresher routes every failure/timeout to onError(failedState(...))
        self.assertIn("onError(failedState(", self.util)
        self.assertIn("onError: (s) => { setState(s)", self.page)
        fs = re.search(r"export function failedState.*?\n\}", self.util, re.S).group(0)
        for tok in ("available: false", "state: 'unavailable'", "queued_behind: []", "history: []"):
            self.assertIn(tok, fs)
        self.assertIn("state || failedState(", self.page)

    def test_working_requires_available_active_working(self):
        iw = re.search(r"export function isWorking.*?\n\}", self.util, re.S).group(0)
        for tok in ("state.available", "'active'", "'Working'"):
            self.assertIn(tok, iw)
        self.assertNotIn("'Working'", self.page)
        self.assertIn("isWorking(s)", self.page)

    def test_no_percentage_or_eta_presented(self):
        for src in (self.page, self.util):
            code = re.sub(r"/\*.*?\*/|//[^\n]*", "", src, flags=re.S)  # comments may say "no ETA"
            low = code.lower()
            self.assertNotRegex(low, r"\beta\b")
            self.assertNotIn("% complete", low)
            self.assertNotRegex(low, r"<meter|progressbar|<progress")
        self.assertIsNone(re.search(r"\d+\s*%", self.util))

    def test_unmapped_run_id_and_null_checkpoint_honest(self):
        self.assertIn("'not mapped'", self.util)
        self.assertIn("actionsRunText(worker)", self.page)
        self.assertIn("worker.checkpoint_sha", self.page)
        self.assertIn("'none yet'", self.page)

    def test_integration_wiring(self):
        # Two lines share this panel: SCI staging (sci-program) embeds it in
        # /god/control-room (ControlRoom.jsx); platform-dev mounts it at /god/relay.
        app = _read("frontend", "src", "App.jsx")
        cr_path = os.path.join(ROOT, "frontend", "src", "pages", "god", "ControlRoom.jsx")
        cr = _read("frontend", "src", "pages", "god", "ControlRoom.jsx") if os.path.exists(cr_path) else ""
        if "<GodRelayControlRoom onManualRefresh=" in cr:
            self.assertIn("import('./pages/god/ControlRoom')", app)
            self.assertRegex(app, r'path="/god/control-room"\s+element=\{<GodRoute><GodModeLayout><ControlRoom />')
        else:
            self.assertIn("import('./pages/god/GodRelayControlRoom')", app)
            self.assertRegex(app, r'path="/god/relay"\s+element=\{<GodRoute><GodModeLayout><GodRelayControlRoom />')
        self.assertIn("<GodLaunchBoard", _read("frontend", "src", "pages", "god", "GodRelayControlRoom.jsx"))
        util = _read("frontend", "src", "utils", "relayControlRoom.js")
        self.assertIn("/god/relay/worker", util)
        api_dir = os.path.join(ROOT, "frontend", "src", "api")
        self.assertTrue(any(f.startswith("client.") for f in os.listdir(api_dir)))

if __name__ == "__main__":
    unittest.main()
