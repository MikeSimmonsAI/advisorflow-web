"""AI Workforce durable evidence + persisted optimistic version (source-only).

Stdlib only: no fastapi / sqlalchemy / database. Shaping and planning are
exercised for real; the migration is executed against stub `op`/`sa` modules
and a fake table; router/model/queue wiring is asserted from source. Nothing
here touches a database, so DB-level locking is NOT proven by this file.
"""
import importlib.util
import os
import re
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


RE = _load("run_evidence", "app", "services", "workforce", "run_evidence.py")
for n in ("app", "app.services", "app.services.workforce"):
    sys.modules.setdefault(n, types.ModuleType(n))
sys.modules["app.services.workforce"].run_evidence = RE
sys.modules["app.services.workforce.run_evidence"] = RE
L = _load("operator_ledger_de", "app", "services", "workforce", "operator_ledger.py")

NOW = datetime(2026, 10, 8, 6, 0, 0)
PHASES = ("source_complete", "tests_complete", "deployed", "live_verified")
FULL = dict(source_complete=True, tests_complete=True, deployed=True,
            live_verified=True, evidence_commit_sha="abc123",
            evidence_test_command="python -m unittest", evidence_test_result="passed",
            evidence_test_count=42, evidence_deploy_ref="rel-9",
            evidence_deploy_status="succeeded", evidence_live_ref="chk-1",
            evidence_live_status="verified", evidence_source="relay",
            evidence_recorded_at=NOW)


class Item:
    def __init__(self, id="w1", state="needs_review", org="orgA", **kw):
        self.id, self.state, self.organization_id = id, state, org
        self.employee_id, self.job_key = "e1", "appointment_setter"
        self.subject_type, self.subject_id = "lead", "L1"
        self.priority, self.attempts, self.state_reason = 100, 0, None
        self.updated_at = NOW
        self.created_at = NOW - timedelta(hours=1)
        self.claimed_at = self.terminal_at = self.outcome = None
        self.__dict__.update(kw)


def entry(item, org="orgA"):
    return L.shape_work_item(item, organization_id=org, now=NOW, is_admin=True)


class LegacyAndPartial(unittest.TestCase):
    def test_legacy_row_is_all_unavailable_not_false_or_zero(self):
        e = entry(Item())
        for p in PHASES:
            self.assertEqual(e["phases"][p]["status"], "unavailable", p)
        for k in ("commit", "tests", "deploy", "live"):
            self.assertEqual(e[k], {"status": "unavailable"}, k)
        self.assertFalse(e["done"])
        self.assertEqual(e["last_checkpoint"]["status"], "unavailable")

    def test_partial_population_keeps_missing_phases_unavailable(self):
        e = entry(Item(source_complete=True, evidence_commit_sha="abc123"))
        self.assertEqual(e["phases"]["source_complete"]["status"], "passed")
        self.assertEqual(e["phases"]["source_complete"]["commit"], "abc123")
        for p in PHASES[1:]:
            self.assertEqual(e["phases"][p]["status"], "unavailable", p)
        self.assertEqual(e["commit"]["sha"], "abc123")
        self.assertEqual(e["tests"], {"status": "unavailable"})
        self.assertFalse(e["done"])

    def test_non_bool_flags_are_not_evidence(self):
        e = entry(Item(source_complete=1, tests_complete="yes",
                       deployed="true", live_verified=0))
        for p in PHASES:
            self.assertEqual(e["phases"][p]["status"], "unavailable", p)
        self.assertFalse(e["done"])

    def test_zero_test_count_is_recorded_zero_not_missing(self):
        e = entry(Item(evidence_test_count=0))
        self.assertEqual(e["tests"]["count"], 0)
        self.assertEqual(e["tests"]["status"], "available")

    def test_checkpoint_from_evidence_column(self):
        e = entry(Item(evidence_checkpoint_summary="tests green on 9a2"))
        self.assertEqual(e["last_checkpoint"]["status"], "available")


class PhaseIndependenceAndDone(unittest.TestCase):
    def test_each_single_phase_never_implies_done_or_another_phase(self):
        for p in PHASES:
            e = entry(Item(**{p: True}))
            self.assertFalse(e["done"], p)
            others = [q for q in PHASES if q != p]
            for q in others:
                self.assertEqual(e["phases"][q]["status"], "unavailable", (p, q))

    def test_done_requires_all_four_passed(self):
        self.assertTrue(entry(Item(**FULL))["done"])
        for p in PHASES:
            kw = dict(FULL)
            kw[p] = None
            self.assertFalse(entry(Item(**kw))["done"], p)
            kw[p] = False
            e = entry(Item(**kw))
            self.assertFalse(e["done"], p)
            self.assertEqual(e["phases"][p]["status"], "failed")

    def test_completed_state_alone_is_not_done(self):
        e = entry(Item(state="appointment_booked"))
        self.assertEqual(e["state"], "completed")
        self.assertFalse(e["done"])

    def test_done_is_not_inferred_from_detail_columns(self):
        e = entry(Item(evidence_commit_sha="x", evidence_test_result="passed",
                       evidence_deploy_status="succeeded",
                       evidence_live_status="verified"))
        self.assertFalse(e["done"])


class VersionToken(unittest.TestCase):
    def test_persisted_version_is_monotonic_and_stable(self):
        self.assertEqual(L.version_token(Item(row_version=3)), "v3")
        self.assertEqual(L.version_token(Item(row_version=0)), "v0")
        # unrelated column churn does not change a persisted token
        a = L.version_token(Item(row_version=3))
        self.assertEqual(a, L.version_token(Item(row_version=3, updated_at=NOW + timedelta(hours=1))))
        self.assertNotEqual(a, L.version_token(Item(row_version=4)))

    def test_legacy_null_version_never_collides_with_persisted(self):
        t = L.version_token(Item(row_version=None))
        self.assertTrue(t.startswith("legacy-"))
        self.assertFalse(t.startswith("v"))

    def test_bool_is_not_a_version(self):
        self.assertTrue(L.version_token(Item(row_version=True)).startswith("legacy-"))


class ReviewPlanWithPersistedVersion(unittest.TestCase):
    def apply(self, item, expected, target="eligibility_pending"):
        """Mirror of the route: plan, then bump exactly once on a write."""
        plan = L.plan_review(current_state=item.state,
                             current_version=L.version_token(item),
                             target_state=target, expected_version=expected,
                             reachable=item.state == "needs_review")
        if plan["write"]:
            item.state = target
            item.row_version = (item.row_version or 0) + 1
        return plan["outcome"]

    def test_success_increments_once(self):
        it = Item(row_version=5)
        self.assertEqual(self.apply(it, "v5"), "apply")
        self.assertEqual(L.version_token(it), "v6")

    def test_stale_conflict_does_not_increment(self):
        it = Item(row_version=5)
        self.assertEqual(self.apply(it, "v4"), "conflict")
        self.assertEqual((it.state, it.row_version), ("needs_review", 5))

    def test_replay_is_noop(self):
        it = Item(row_version=5)
        self.apply(it, "v5")
        self.assertEqual(self.apply(it, "v6"), "illegal")  # current view, not in review
        self.assertEqual(self.apply(it, "v4"), "replay")   # double click, old view
        self.assertEqual(it.row_version, 6)

    def test_missing_version_is_rejected(self):
        it = Item(row_version=5)
        self.assertEqual(self.apply(it, None), "version_required")
        self.assertEqual(self.apply(it, ""), "version_required")
        self.assertEqual(it.row_version, 5)

    def test_legacy_row_first_write_moves_to_v1(self):
        it = Item(row_version=None)
        tok = L.version_token(it)
        self.assertEqual(self.apply(it, tok), "apply")
        self.assertEqual(L.version_token(it), "v1")


class TenantIsolation(unittest.TestCase):
    def test_foreign_org_rows_are_dropped(self):
        mine = entry(Item(id="a", **FULL), "orgA")
        theirs = entry(Item(id="b", **FULL), "orgB")
        out = L.build_ledger([mine, theirs], organization_id="orgA")
        self.assertEqual([r["id"] for r in out["items"]], ["a"])

    def test_evidence_columns_never_carry_client_org(self):
        e = entry(Item(**FULL), "orgA")
        self.assertEqual(e["scope"], {"organization_id": "orgA"})


class MigrationContract(unittest.TestCase):
    """Run upgrade()/downgrade() against stub alembic/sqlalchemy + fake table."""

    NAME = "a1c3e5b7d9f2_add_ai_work_item_evidence.py"

    def _module(self, table):
        calls = []

        class Col:
            def __init__(self, name, type_, nullable=True):
                self.name, self.type_, self.nullable = name, type_, nullable

        sa = types.ModuleType("sqlalchemy")
        for t in ("Integer", "Boolean", "String", "DateTime"):
            setattr(sa, t, lambda t=t: t)
        sa.Column = Col

        class Insp:
            def get_table_names(self):
                return list(table)

            def get_columns(self, name):
                return [{"name": c} for c in table[name]]
        sa.inspect = lambda bind: Insp()
        op = types.ModuleType("alembic.op")
        op.get_bind = lambda: object()

        def add_column(t, col):
            calls.append(("add", col.name, col.nullable))
            table[t].append(col.name)

        def drop_column(t, name):
            calls.append(("drop", name))
            table[t].remove(name)
        op.add_column, op.drop_column = add_column, drop_column
        alembic = types.ModuleType("alembic")
        alembic.op = op
        saved = {k: sys.modules.get(k) for k in ("alembic", "alembic.op", "sqlalchemy")}
        sys.modules.update({"alembic": alembic, "alembic.op": op, "sqlalchemy": sa})
        try:
            mod = _load("mig_de", "alembic", "versions", self.NAME)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        return mod, calls

    BASE = ["id", "organization_id", "state"]

    def test_upgrade_adds_nullable_columns_then_downgrade_restores(self):
        table = {"ai_work_items": list(self.BASE)}
        mod, calls = self._module(table)
        mod.upgrade()
        added = [c for c in calls if c[0] == "add"]
        self.assertTrue(all(c[2] is True for c in added), "all must be NULLABLE")
        names = {c[1] for c in added}
        for need in ("row_version",) + PHASES + (
                "evidence_commit_sha", "evidence_test_command", "evidence_test_result",
                "evidence_test_count", "evidence_deploy_ref", "evidence_deploy_status",
                "evidence_live_ref", "evidence_live_status",
                "evidence_checkpoint_summary", "evidence_source", "evidence_recorded_at"):
            self.assertIn(need, names)
        mod.downgrade()
        self.assertEqual(table["ai_work_items"], self.BASE)

    def test_upgrade_is_idempotent_and_skips_missing_table(self):
        table = {"ai_work_items": list(self.BASE)}
        mod, calls = self._module(table)
        mod.upgrade()
        n = len(calls)
        mod.upgrade()
        self.assertEqual(len(calls), n)
        empty = {}
        mod2, calls2 = self._module(empty)
        mod2.upgrade()
        mod2.downgrade()
        self.assertEqual(calls2, [])

    def test_chain_and_runtime_path(self):
        mod, _ = self._module({})
        self.assertEqual(mod.down_revision, "6c8e0a2d4f1b")
        heads = set()
        downs = set()
        for f in os.listdir(_p("alembic", "versions")):
            if f.endswith(".py"):
                t = _read("alembic", "versions", f)
                heads.add(re.search(r"^revision\s*(?::[^=]+)?=\s*['\"](\w+)", t, re.M).group(1))
                m = re.search(r"^down_revision\s*(?::[^=]+)?=\s*['\"](\w+)", t, re.M)
                if m:
                    downs.add(m.group(1))
        self.assertEqual(heads - downs, {"a1c3e5b7d9f2"}, "single alembic head")
        am = _read("app", "auto_migrate.py")
        for c in ("row_version", "live_verified", "evidence_recorded_at"):
            self.assertIn('("ai_work_items", "%s"' % c, am)


class SourceWiring(unittest.TestCase):
    R = _read("app", "routers", "workforce_router.py")
    M = _read("app", "models", "workforce_models.py")
    Q = _read("app", "services", "workforce", "queue.py")

    def review(self):
        return re.search(r"def review_work_item(.*?)class HandoffActionRequest",
                         self.R, re.S).group(1)

    def test_model_columns_are_nullable_and_match_migration(self):
        mig = _read("alembic", "versions", MigrationContract.NAME)
        cols = re.findall(r'\("(\w+)", sa\.', mig)
        self.assertEqual(len(cols), 16)
        for c in cols:
            m = re.search(r"^\s+%s = Column\((.*)\)" % c, self.M, re.M)
            self.assertIsNotNone(m, c)
            self.assertIn("nullable=True", m.group(1), c)
            self.assertNotIn("default", m.group(1), c)

    def test_every_transition_bumps_version(self):
        self.assertIn("item.row_version = (item.row_version or 0) + 1", self.Q)
        t = re.search(r"def transition\(.*?\n    _event\(", self.Q, re.S).group(0)
        self.assertLess(t.index("item.state = to_state"), t.index("row_version"))

    def test_review_locks_row_scopes_org_and_validates_before_write(self):
        r = self.review()
        self.assertIn(".with_for_update()", r)
        self.assertIn("AIWorkItem.organization_id == org_id", r)
        self.assertNotIn("payload.organization_id", r)
        self.assertNotIn("organization_id:", re.search(
            r"class ReviewRequest(.*?)_REVIEW_DECISIONS", self.R, re.S).group(1))
        self.assertLess(r.index("expected_version"), r.index("with_for_update"))
        self.assertLess(r.index("with_for_update"), r.index("plan_review"))
        self.assertLess(r.index("plan_review"), r.index("advance_to"))

    def test_review_releases_lock_on_every_refusal_and_rolls_back_failures(self):
        r = self.review()
        for outcome in ("version_required", "replay", "conflict", "illegal"):
            m = re.search(r'outcome"\] == "%s":\n(.*?)\n    (?:if|try)' % outcome, r, re.S)
            self.assertIsNotNone(m, outcome)
            self.assertIn("db.rollback()", m.group(1), outcome)
        self.assertEqual(r.count("db.commit()"), 1)
        self.assertGreaterEqual(r.count("db.rollback()"), 7)
        self.assertIn("except Exception:\n        db.rollback()\n        raise", r)

    def test_missing_version_is_400_and_stale_is_409(self):
        r = self.review()
        self.assertRegex(r, r"status_code=400,\s+detail=\"expected_version is required")
        self.assertEqual(len(re.findall(r"status_code=409", r)), 3)


if __name__ == "__main__":
    unittest.main()
