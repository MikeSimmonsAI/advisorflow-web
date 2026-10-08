"""Dependency-free contract for compensation projection discoverability + page truth.

Runs the pure JS helper tests through node (no npm install/browser/network) and
asserts source wiring: nav/route/server access alignment, controls, legend,
no float math, no payout/provider actions.
"""
import os
import re
import shutil
import subprocess
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


PAGE = "frontend/src/pages/sales/CompensationProjection.jsx"
HELPER = "frontend/src/utils/compensationProjection.js"
SHELL = "frontend/src/pages/sales/SalesShell.jsx"


class HelperExecuted(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_node_helper_suite_passes(self):
        r = subprocess.run(["node", "--test", "frontend/tests/compensationProjection.test.mjs"],
                           cwd=_ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"# fail 0")
        self.assertRegex(r.stdout, r"# pass [1-9]")


class NavAndAccess(unittest.TestCase):
    def test_nav_entry_in_my_work_without_permission(self):
        shell = _src(SHELL)
        lines = [l for l in shell.splitlines() if "to: '/sales/compensation-projection'" in l]
        self.assertEqual(len(lines), 1)
        self.assertNotIn("permission", lines[0])
        nav_block = shell[shell.index("const NAV = ["):shell.index("const REP_ONLY_NAV")]
        self.assertIn("/sales/compensation-projection", nav_block)
        mgr = shell[shell.index("const MANAGER_NAV"):shell.index("export default function SalesShell")]
        self.assertNotIn("/sales/compensation-projection", mgr)

    def test_route_guard_matches_server_guard(self):
        app = _src("frontend/src/App.jsx")
        self.assertRegex(app, r'path="/sales/compensation-projection" element=\{<SalesRoute><CompensationProjection />')
        router = _src("app/routers/compensation_router.py")
        start = router.index('@router.get("/projection")')
        seg = router[start:start + 600]
        self.assertIn("Depends(require_sales_member)", seg)
        self.assertNotIn("require_sales_manager", seg)

    def test_stage_options_from_server_list_and_select_stays_mounted(self):
        page = _src(PAGE)
        self.assertIn("available_stages", page)
        self.assertNotIn("stageOptions(d.deals)", page)
        # the select precedes the `{d ? (` figures block, so it is never unmounted
        self.assertLess(page.index("<select"), page.index("{d ? ("))

    def test_client_never_sends_payee_or_assumes_management(self):
        page = _src(PAGE) + _src(HELPER)
        self.assertNotIn("payee_user_id", page)
        self.assertNotIn("view_team_pipeline", page)
        self.assertIn("brand_sales_org_id", page)  # optional request only
        self.assertIn("server still decides", page)


class PageTruth(unittest.TestCase):
    def test_legend_and_labels_rendered(self):
        page = _src(PAGE)
        self.assertIn("LEGEND.map", page)
        self.assertIn('aria-label="Legend"', page)
        h = _src(HELPER)
        for l in ("Earned", "On hold", "Payable", "Paid", "Pending approval",
                  "Unweighted forecast", "Weighted forecast", "Excluded / blockers"):
            self.assertIn("label: '%s'" % l, h)

    def test_error_and_empty_states_wired(self):
        page = _src(PAGE)
        self.assertIn("errorMessage(e)", page)
        self.assertIn('role="alert"', page)
        self.assertIn("setD(null)", page)
        self.assertIn("EMPTY_TEXT[state]", page)
        self.assertIn("Excluded — not counted as $0", page)

    def test_no_float_math_or_intl(self):
        for rel in (PAGE, HELPER):
            s = _src(rel)
            for bad in ("parseFloat", "toFixed", "Intl.", "Number(", "Math.round", "toLocaleString"):
                self.assertNotIn(bad, s, (rel, bad))
            self.assertIsNone(re.search(r"\.cents\s*[-+*/]", s), rel)
        # Only arithmetic permitted: displaying basis points as a percent.
        self.assertEqual(_src(PAGE).count("probability_bp / 100"), 1)

    def test_no_payout_or_provider_actions(self):
        # Header comment legitimately says "no payout/bank/tax controls".
        s = (_src(PAGE).split("*/", 1)[1] + _src(HELPER)).lower()
        for bad in ("<button", "api.post", "api.put", "api.patch", "api.delete", "stripe",
                    "payroll run", "send email"):
            self.assertNotIn(bad, s, bad)
        for word in ("ach", "bank", "form", "sms"):
            self.assertIsNone(re.search(r"\b%s\b" % word, s), word)

    def test_responsive_theme_structure(self):
        page = _src(PAGE)
        self.assertIn("repeat(auto-fit, minmax(190px, 1fr))", page)
        self.assertIn("overflowX: 'auto'", page)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", page))
        self.assertIn("var(--text-strong)", page)


if __name__ == "__main__":
    unittest.main()
