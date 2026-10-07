"""Dependency-free static tests for the AI Workforce truth/recovery slice.

Run: python3 -m unittest tests.test_ai_workforce_truth_recovery
The pure helpers are exercised by tests/workforce_truth.test.mjs (node --test).
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


PAGE = _read("frontend", "src", "pages", "AIWorkforce.jsx")
EMP = _read("frontend", "src", "pages", "AIWorkforceEmployee.jsx")
ROUTER = _read("app", "routers", "workforce_router.py")
HELPERS = _read("frontend", "src", "utils", "workforceTruth.js")


class StatusLineTruth(unittest.TestCase):
    def test_permission_is_not_called_working(self):
        self.assertNotIn("AI employees are working", ROUTER)
        self.assertIn("switched on", ROUTER)


class ActionSafety(unittest.TestCase):
    def test_page_uses_synchronous_guard_for_every_mutation(self):
        self.assertIn("createActionGuard", PAGE)
        self.assertIn("const lock = `hire:${templateKey}`", PAGE)
        for lock in ("lock", "'save'", "`act:${path}`"):
            self.assertIn("guard.tryAcquire(%s)" % lock, PAGE)
        self.assertEqual(PAGE.count("guard.release("), 3)

    def test_employee_page_has_ref_lock_and_releases_it(self):
        self.assertIn("busyRef.current = true", EMP)
        self.assertIn("busyRef.current = false", EMP)

    def test_resume_message_reads_real_field(self):
        self.assertNotIn("${res.state}", EMP)
        self.assertIn("res.result.state", EMP)


class StaleAndRecovery(unittest.TestCase):
    def test_stale_responses_are_dropped(self):
        self.assertIn("loadSeq.isCurrent(token)", PAGE)
        self.assertIn("openSeq.isCurrent(token)", PAGE)
        self.assertIn("token !== loadSeq.current", EMP)

    def test_failed_refresh_keeps_last_good_and_flags_stale(self):
        self.assertIn("applyRefresh(lastGood.current, outcome)", PAGE)
        self.assertIn("may be out of date", PAGE)
        self.assertIn("may be out of date", EMP)

    def test_accessible_alert_status_and_retry(self):
        self.assertIn('role="alert"', PAGE)
        self.assertIn('role="status"', PAGE)
        self.assertIn('role="alert"', EMP)
        self.assertRegex(PAGE, r"Try again")
        self.assertIn("autoFocus", PAGE)

    def test_support_code_only_when_supplied(self):
        self.assertIn("code ?", PAGE)
        self.assertIn("support_code", HELPERS)


class HelperContract(unittest.TestCase):
    def test_helpers_are_pure_and_import_free(self):
        self.assertIsNone(re.search(r"^import ", HELPERS, re.M))
        for name in ("selectActiveRun", "createSequencer", "createActionGuard",
                     "applyRefresh", "lifecycleLabel", "evidenceLabel", "redact"):
            self.assertIn("export function %s" % name, HELPERS)

    def test_unknown_is_default_never_idle_or_working(self):
        self.assertIn("|| 'Unknown'", HELPERS)
        self.assertNotRegex(HELPERS, r"idle:\s*'")


class TenantScope(unittest.TestCase):
    def test_every_workforce_read_is_org_filtered(self):
        # Every route resolves the org server-side; none take org id as an arg.
        self.assertNotRegex(ROUTER, r"def \w+\([^)]*organization_id\s*:")
        self.assertGreaterEqual(ROUTER.count("_org_id(user, db, request)"), 10)
        self.assertIn("AIEmployee.organization_id == org_id", ROUTER)
        self.assertIn("AIWorkItem.organization_id == org_id", ROUTER)
        self.assertIn("Lead.organization_id == org_id", ROUTER)

    def test_cross_tenant_and_missing_employee_are_indistinguishable(self):
        self.assertEqual(ROUTER.count('"No such AI employee."'), 1)


if __name__ == "__main__":
    unittest.main()
