"""Dependency-free contract: dual-role (sales + customer workspace) access.

Executes the production landing/guard helpers under node and asserts the
backend/frontend wiring as source facts. Synthetic only; no DB, no network.
"""
import os
import subprocess
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8-sig") as f:
        return f.read()


class Behaviour(unittest.TestCase):
    def test_four_identities_execute(self):
        script = os.path.join(_ROOT, "tests", "frontend", "homeDestination.test.mjs")
        try:
            p = subprocess.run(["node", script], capture_output=True, text=True,
                               cwd=_ROOT, timeout=120)
        except (FileNotFoundError, OSError):
            self.skipTest("node unavailable")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))


class Wiring(unittest.TestCase):
    def test_home_redirect_uses_pure_decision(self):
        s = _src("frontend/src/App.jsx")
        self.assertIn("decideHomeDestination(ctx, user)", s)
        self.assertNotIn("ctx.workspace_count === 0 &&", s)

    def test_back_office_context_requires_a_sales_role(self):
        s = _src("app/services/workspace_access.py")
        i = s.index("for m in platform_memberships(user, db):")
        self.assertIn("m.role not in BRAND_SALES_ROLES", s[i:i + 500])

    def test_workspace_entry_stays_membership_scoped(self):
        s = _src("app/services/workspace_access.py")
        self.assertIn("def assert_workspace_membership", s)
        self.assertIn("if has_workspace(user, db, requested):", s)
        self.assertIn("Membership.scope_type == SCOPE_CUSTOMER_ORG", s)

    def test_switcher_derives_from_server_list(self):
        s = _src("frontend/src/components/ContextSwitcher.jsx")
        self.assertIn("contexts.workspace_contexts", s)
        self.assertIn("contexts.has_back_office", s)


if __name__ == "__main__":
    unittest.main()
