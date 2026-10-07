"""Dependency-free contract: Universal Importer alias + resume integrity.

Executes the production pure helpers (fields.py, workflow.py); synthetic data
only, no DB, no network, no real import.
"""
import importlib.util
import os
import sys
import types
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8-sig") as f:
        return f.read()


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, rel))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# Load the production modules without importing the app package (which needs
# sqlalchemy/fastapi): stub only the empty package shells.
for _pkg in ("app", "app.services", "app.services.intake"):
    sys.modules.setdefault(_pkg, types.ModuleType(_pkg))
N = _load("app.services.intake.normalize", "app/services/intake/normalize.py")
sys.modules["app.services.intake"].normalize = N
F = _load("app.services.intake.fields", "app/services/intake/fields.py")
WF = _load("app.services.intake.workflow", "app/services/intake/workflow.py")


class Alias(unittest.TestCase):
    def test_last_activity_date_variants(self):
        for h in ("Last Activity Date", "last activity date", "LAST  ACTIVITY  DATE",
                  "last_activity_date", "Last-Activity-Date", "LastActivityDate"):
            s = F.suggest(h)
            self.assertEqual((s["kind"], s["target"]), ("standard", "last_activity_date"), h)

    def test_no_broad_fuzzy_matching(self):
        for h in ("Open Activity Date", "Next Activity Date", "Last Modified Date",
                  "Date of Last Activity", "Last Activity Date (UTC)", "Created Date"):
            self.assertNotEqual(F.suggest(h)["target"], "last_activity_date", h)


class Confirmation(unittest.TestCase):
    H = ["Email", "Type", "Last Activity Date"]

    def test_changed_mapping_clears_confirmation(self):
        old = F.clean_mapping(self.H, {"Type": {"kind": "standard", "target": "classification"}})
        new = F.clean_mapping(self.H, {"Type": {"kind": "standard", "target": "classification"},
                                       "Last Activity Date": {"kind": "standard",
                                                              "target": "last_activity_date"}})
        cfg = {"column": "Type", "confirmed_at": "2026-10-07T00:00:00"}
        out = F.drop_stale_confirmation(old, new, cfg)
        self.assertNotIn("confirmed_at", out)
        self.assertEqual(out["column"], "Type")
        self.assertIn("confirmed_at", cfg)  # input not mutated

    def test_same_mapping_keeps_confirmation(self):
        m = F.clean_mapping(self.H, {})
        cfg = {"confirmed_at": "x"}
        self.assertIs(F.drop_stale_confirmation(dict(m), m, cfg), cfg)

    def test_stale_schema_keys_rejected(self):
        m = F.clean_mapping(["A"], {"A": {"kind": "ignore"},
                                    "Other File Col": {"kind": "standard", "target": "email"}})
        self.assertEqual(list(m), ["A"])


class Resume(unittest.TestCase):
    def test_steps(self):
        w = WF.workflow
        self.assertEqual(w("mapping", True, True)["step"], 4)
        self.assertEqual(w("mapping", True, False)["step"], 2)  # after confirmation cleared
        self.assertEqual(w("mapping", False, False)["step"], 2)
        self.assertEqual(w("ready_for_review", True, True)["step"], 5)
        self.assertEqual(w("ready_for_review", True, False)["step"], 3)
        self.assertEqual(w("staged", True, True)["step"], 6)
        self.assertEqual(w("committed", True, True)["step"], 7)

    def test_mapping_change_does_not_resume_at_classify(self):
        old = F.clean_mapping(["Type"], {"Type": {"kind": "standard", "target": "classification"}})
        new = F.clean_mapping(["Type"], {"Type": {"kind": "source", "target": "type"}})
        cfg = F.drop_stale_confirmation(old, new, {"confirmed_at": "x"})
        step = WF.workflow("mapping", True, bool(cfg.get("confirmed_at")))["step"]
        self.assertEqual(step, 2)


class Wiring(unittest.TestCase):
    def test_engine_uses_helpers(self):
        s = _src("app/services/intake/engine.py")
        self.assertIn("F.clean_mapping(headers, mapping)", s)
        self.assertIn("F.drop_stale_confirmation(old_mapping, clean, cfg)", s)

    def test_router_uses_pure_workflow_and_gate(self):
        s = _src("app/routers/intake_router.py")
        self.assertIn("_workflow = WF.workflow", s)
        self.assertIn("The field mapping or classification changed since the last analysis", s)
        self.assertIn("ImportBatch.organization_id == ctx.org_id", s)
        i = s.index("def save_mapping")
        self.assertIn("_batch_or_404(db, ctx, batch_id)", s[i:i + 400])
        self.assertIn("b.status = ImportBatchStatus.MAPPING", s)


if __name__ == "__main__":
    unittest.main()
