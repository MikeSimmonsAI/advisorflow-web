"""Dependency-free tests: AI Workforce operator ledger.

Run: python3 -m unittest tests.test_ai_workforce_operator_ledger

Evidence level: BEHAVIOURAL for the pure service (loaded without sqlalchemy)
and for the JS helper (node --test via subprocess, skipped if node missing).
SOURCE-STATIC for router wiring, org scoping in SQL and component wiring.
NOT proven: real DB queries, HTTP tenant isolation, browser rendering, mobile.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import types
import unittest
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _p(*a):
    return os.path.join(ROOT, *a)


def _read(*a):
    with open(_p(*a), encoding="utf-8") as f:
        return f.read()


def _load(name, *path):
    spec = importlib.util.spec_from_file_location(name, _p(*path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Make `from app.services.workforce import run_evidence` resolve without
# importing the app package (which needs fastapi/sqlalchemy).
RE = _load("run_evidence", "app", "services", "workforce", "run_evidence.py")
for n in ("app", "app.services", "app.services.workforce"):
    sys.modules.setdefault(n, types.ModuleType(n))
sys.modules["app.services.workforce"].run_evidence = RE
sys.modules["app.services.workforce.run_evidence"] = RE
L = _load("operator_ledger", "app", "services", "workforce", "operator_ledger.py")

NOW = datetime(2026, 10, 8, 6, 0, 0)


class Item:
    def __init__(self, id="w1", state="needs_review", **kw):
        self.id, self.state = id, state
        self.employee_id = kw.get("employee_id", "e1")
        self.job_key = "appointment_setter"
        self.subject_type, self.subject_id = "lead", kw.get("subject_id", "L1")
        self.priority, self.attempts = 100, kw.get("attempts", 0)
        self.state_reason = kw.get("state_reason")
        self.updated_at = kw.get("updated_at", NOW)
        self.created_at = NOW - timedelta(hours=1)
        self.claimed_at = self.terminal_at = None
        self.outcome = None


class Run:
    def __init__(self, id="r1", status="completed", **kw):
        self.id, self.status = id, status
        self.employee_id = kw.get("employee_id", "e1")
        self.work_item_id = kw.get("work_item_id", "w1")
        self.started_at = kw.get("started_at", NOW - timedelta(minutes=5))
        self.ended_at = kw.get("ended_at", NOW)
        self.mode, self.trigger = "simulation", "manual"
        self.summary = kw.get("summary")
        self.error = kw.get("error")
        self.abort_reason = kw.get("abort_reason")
        self.objective = "Book a tour"


def entry(item=None, org="orgA", **kw):
    return L.shape_work_item(item or Item(), organization_id=org, now=NOW, **kw)


class StateMapping(unittest.TestCase):
    def test_every_machine_state_maps(self):
        consts = _read("app", "services", "workforce", "constants.py")
        all_states = re.search(r"ALL_STATES = \((.*?)\)", consts, re.S).group(1)
        names = re.findall(r"[A-Z_]+", all_states)
        values = {}
        for n in names:
            m = re.search(r'^%s = "([a-z_]+)"' % n, consts, re.M)
            values[n] = m.group(1)
        self.assertEqual(set(values.values()), set(L.WORK_ITEM_MAP))
        for s in values.values():
            self.assertIn(L.operator_state(s), L.OPERATOR_STATES)

    def test_unknown_not_queued(self):
        self.assertEqual(L.operator_state("zzz"), "unknown")
        self.assertEqual(L.operator_state(None), "unknown")
        e = entry(Item(state="zzz"))
        self.assertEqual(e["state"], "unknown")
        self.assertIn("Unrecognised", e["blocker"]["reason"])

    def test_directive_states(self):
        for src, want in [("working", "running"), ("assigned", "queued"),
                          ("needs_review", "review_required"), ("paused", "blocked"),
                          ("failed", "failed"), ("appointment_booked", "completed"),
                          ("do_not_contact", "cancelled")]:
            self.assertEqual(entry(Item(state=src))["state"], want)

    def test_run_states(self):
        for st, want in [("running", "running"), ("completed", "completed"),
                         ("failed", "failed"), ("paused", "blocked"),
                         ("skipped", "cancelled")]:
            r = L.shape_run_entry(Run(status=st, ended_at=None if st == "running" else NOW),
                                  organization_id="orgA", now=NOW)
            self.assertEqual(r["state"], want, st)
        r = L.shape_run_entry(Run(status="weird"), organization_id="orgA", now=NOW)
        self.assertEqual(r["state"], "unknown")


class EvidenceTruth(unittest.TestCase):
    def test_unavailable_not_zero(self):
        e = entry(Item(state="appointment_booked"))
        for k in ("commit", "tests", "deploy"):
            self.assertEqual(e[k], {"status": "unavailable"})
        self.assertEqual(e["last_checkpoint"]["status"], "unavailable")
        r = L.shape_run_entry(Run(), organization_id="orgA", now=NOW)
        self.assertEqual(r["attempts"], {"status": "unavailable"})

    def test_completed_is_not_done(self):
        e = entry(Item(state="appointment_booked"))
        self.assertEqual(e["state"], "completed")
        self.assertFalse(e["done"])
        self.assertEqual(set(e["phases"]), set(L.PHASES))

    def test_one_phase_not_done(self):
        ph = L.phases_unavailable("x")
        ph["source_complete"] = {"status": "passed"}
        self.assertFalse(L.done_claim(ph))
        for p in L.PHASES:
            ph[p] = {"status": "passed"}
        self.assertTrue(L.done_claim(ph))

    def test_checkpoint_from_run_and_children(self):
        e = L.shape_work_item(Item(), organization_id="orgA", now=NOW, runs=[
            Run("r1", summary="Reached family"),
            Run("r2", started_at=NOW - timedelta(minutes=30), ended_at=NOW - timedelta(minutes=29))])
        self.assertEqual(e["child_run_ids"], ["r1", "r2"])
        self.assertEqual(e["last_checkpoint"]["summary"], "Reached family")

    def test_run_links_parent_and_blocker(self):
        r = L.shape_run_entry(Run(status="failed", error="boom"), organization_id="orgA", now=NOW)
        self.assertEqual(r["parent_id"], "w1")
        self.assertEqual(r["blocker"]["reason"], "boom")

    def test_no_pii_in_source_ref_and_redaction(self):
        e = entry(Item(state="failed", state_reason="call jo@x.com token=abc123456789"))
        self.assertEqual(set(e["source_ref"]), {"type", "id"})
        self.assertNotIn("jo@x.com", e["blocker"]["reason"])
        self.assertNotIn("abc123456789", e["blocker"]["reason"])


class ScopeAndOrdering(unittest.TestCase):
    def test_cross_org_rows_dropped(self):
        rows = [entry(Item("a"), org="orgA"), entry(Item("b"), org="orgB")]
        out = L.build_ledger(rows, organization_id="orgA")
        self.assertEqual([r["id"] for r in out["items"]], ["a"])
        self.assertEqual(out["organization_id"], "orgA")
        self.assertEqual(sum(out["counts"].values()), 1)

    def test_deterministic_order_and_ties(self):
        rows = [entry(Item(i, updated_at=NOW)) for i in ("b", "a", "c")]
        rows.append(entry(Item("z", updated_at=NOW + timedelta(minutes=1))))
        a = [r["id"] for r in L.build_ledger(rows, organization_id="orgA")["items"]]
        b = [r["id"] for r in L.build_ledger(list(reversed(rows)), organization_id="orgA")["items"]]
        self.assertEqual(a, b)
        self.assertEqual(a[0], "z")

    def test_filter_limit_truncation_partial(self):
        rows = [entry(Item("i%d" % i, state="working" if i % 2 else "failed")) for i in range(6)]
        out = L.build_ledger(rows, organization_id="orgA", state="failed", limit=2,
                             source_errors=["runs"])
        self.assertEqual(len(out["items"]), 2)
        self.assertTrue(out["truncated"])
        self.assertTrue(out["partial"])
        self.assertEqual(out["counts"]["running"], 3)   # facets survive filter
        self.assertEqual(L.build_ledger([], organization_id="orgA")["partial"], False)


class Decisions(unittest.TestCase):
    def test_options_and_blockers(self):
        ok = entry(is_admin=True)["decisions"]
        self.assertTrue(ok["review"]["available"])
        for k in ("assign", "reprioritize", "cancel", "retry"):
            self.assertFalse(ok[k]["available"])
            self.assertTrue(ok[k]["blocker"])
        self.assertFalse(entry(is_admin=False)["decisions"]["review"]["available"])
        self.assertFalse(entry(is_admin=True, observation=True)["decisions"]["review"]["available"])
        self.assertFalse(entry(Item(state="working"), is_admin=True)["decisions"]["review"]["available"])

    def test_version_changes_with_state(self):
        a = L.version_token(Item(state="needs_review"))
        self.assertEqual(a, L.version_token(Item(state="needs_review")))
        self.assertNotEqual(a, L.version_token(Item(state="paused")))
        self.assertNotEqual(a, L.version_token(Item(updated_at=NOW + timedelta(seconds=1))))

    def test_plan_review(self):
        P = L.plan_review
        k = dict(current_version="v2", reachable=True)
        self.assertEqual(P(current_state="needs_review", target_state="eligibility_pending",
                           expected_version=None, **k)["outcome"], "version_required")
        self.assertEqual(P(current_state="needs_review", target_state="eligibility_pending",
                           expected_version="v2", **k)["outcome"], "apply")
        self.assertEqual(P(current_state="needs_review", target_state="eligibility_pending",
                           expected_version="v1", **k)["outcome"], "conflict")
        # double click: item already moved, caller's token is old -> replay, no write
        r = P(current_state="eligibility_pending", target_state="eligibility_pending",
              expected_version="v1", **k)
        self.assertEqual((r["outcome"], r["write"]), ("replay", False))
        # item never was in review and view is current -> not a false success
        self.assertEqual(P(current_state="eligibility_pending", target_state="eligibility_pending",
                           expected_version="v2", **k)["outcome"], "illegal")
        self.assertEqual(P(current_state="do_not_contact", target_state="eligibility_pending",
                           expected_version="v2", current_version="v2",
                           reachable=False)["outcome"], "illegal")
        for o in ("conflict", "illegal", "replay", "version_required"):
            pass


class RouterAndUiWiring(unittest.TestCase):
    R = _read("app", "routers", "workforce_router.py")

    def test_ledger_route_read_only_and_scoped(self):
        m = re.search(r'@router\.get\("/ledger"\)(.*?)@router\.get\("/performance"\)', self.R, re.S)
        self.assertIsNotNone(m)
        body = m.group(1)
        self.assertEqual(body.count("organization_id == org_id"), 3)
        self.assertNotIn("organization_id: str", body)
        self.assertNotIn("db.commit", body)
        self.assertNotIn("db.add", body)
        self.assertIn("source_errors", body)
        self.assertIn("is_manager_here", body)

    def test_review_route_versioned_and_rolls_back(self):
        m = re.search(r'def review_work_item(.*?)class HandoffActionRequest', self.R, re.S).group(1)
        self.assertIn("plan_review", m)
        self.assertIn("db.rollback()", m)
        self.assertIn("expected_version", self.R)
        self.assertLess(m.index("plan_review"), m.index("advance_to"))
        self.assertIn("require_admin", self.R)
        self.assertIn("require_not_observation", m + self.R)

    def test_queue_ordering_has_tiebreak(self):
        self.assertIn("AIWorkItem.updated_at.desc(), AIWorkItem.id.desc()", self.R)

    def test_no_outbound_or_destructive_in_new_code(self):
        svc = _read("app", "services", "workforce", "operator_ledger.py")
        ui = _read("frontend", "src", "components", "WorkforceLedger.jsx") + \
            _read("frontend", "src", "utils", "workforceLedger.js")
        for text in (svc, ui):
            for bad in ("send_sms", "send_email", "twilio", "subprocess", "os.system",
                        "stripe", "deploy(", "DELETE", "api.delete", "eval(", "db.delete"):
                self.assertNotIn(bad, text, bad)
        self.assertEqual(re.findall(r"api\.(\w+)\(", ui), ["get", "post"])
        self.assertIn("/workforce/queue/${entry.id}/review", ui)

    def test_ui_wiring(self):
        ui = _read("frontend", "src", "components", "WorkforceLedger.jsx")
        for needle in ("createSequencer", "isCurrent", "createActionGuard", "tryAcquire",
                       "applyRefresh", "partial", "truncated", "No work has been assigned",
                       "Loading work ledger", "role=\"alert\"", "reviewPayload", "reviewOutcome"):
            self.assertIn(needle, ui)
        self.assertIn("<WorkforceLedger />", _read("frontend", "src", "pages", "AITeam.jsx"))
        # AITeam is reachable by any tenant user (route has no requireAdmin);
        # the decision buttons are gated by the server-provided decisions.
        self.assertIn("canDecide", _read("frontend", "src", "utils", "workforceLedger.js"))

    def test_mobile_shell_has_no_workforce_client(self):
        # Honest scope marker: no mobile AI Workforce client exists to extend.
        hits = []
        for dp, dn, fn in os.walk(_p("mobile")):
            dn[:] = [d for d in dn if d not in ("node_modules", ".git", "build")]
            for f in fn:
                if f.endswith((".js", ".jsx", ".ts", ".tsx", ".dart")):
                    with open(os.path.join(dp, f), encoding="utf-8", errors="ignore") as h:
                        if "/workforce/" in h.read():
                            hits.append(f)
        self.assertEqual(hits, [])


class NodeHelper(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_node_suite(self):
        r = subprocess.run(["node", "--test", _p("tests", "workforce_ledger.test.mjs")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
