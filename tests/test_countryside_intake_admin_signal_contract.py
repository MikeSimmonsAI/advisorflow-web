"""Dependency-free contract: a captured website demo lead tells its own
workspace's org_admins in-app, replay-safe, DB-only. Synthetic only."""
import importlib.util
import os
import unittest
from types import SimpleNamespace as NS

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location(
    "intake_signal_under_test", os.path.join(_ROOT, "app", "services", "intake_admin_signal.py"))
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


LEAD = NS(id="L1", organization_id="orgA", first_name="Pat", last_name="Roe")


class Plan(unittest.TestCase):
    def test_one_row_per_active_admin_of_own_org(self):
        admins = [NS(id="a1", organization_id="orgA"),
                  NS(id="a2", organization_id="orgA", is_active=None),
                  NS(id="off", organization_id="orgA", is_active=False),
                  NS(id="x", organization_id="orgB")]
        plan = S.signal_plan(LEAD, admins, lambda u, m: False, "sub-1")
        self.assertEqual([p["user_id"] for p in plan], ["a1", "a2"])
        self.assertTrue(all(p["lead_id"] == "L1" for p in plan))
        self.assertIn("Pat Roe", plan[0]["message"])
        self.assertIn("[ref sub-1]", plan[0]["message"])

    def test_replay_writes_nothing(self):
        told = set()
        a = [NS(id="a1", organization_id="orgA")]
        first = S.signal_plan(LEAD, a, lambda u, m: (u, m) in told, "s")
        told.update((p["user_id"], p["message"]) for p in first)
        again = S.signal_plan(LEAD, a, lambda u, m: (u, m) in told, "s")
        self.assertEqual((len(first), len(again)), (1, 0))

    def test_duplicate_admin_rows_collapse(self):
        a = NS(id="a1", organization_id="orgA")
        self.assertEqual(len(S.signal_plan(LEAD, [a, a], lambda u, m: False)), 1)

    def test_no_org_or_no_lead_or_no_admins(self):
        self.assertEqual(S.signal_plan(None, [], lambda u, m: False), [])
        self.assertEqual(S.signal_plan(NS(id="L", organization_id=None), [NS(id="a")],
                                       lambda u, m: False), [])
        self.assertEqual(S.signal_plan(LEAD, [], lambda u, m: False), [])

    def test_notify_never_raises(self):
        self.assertEqual(S.notify_workspace_admins(object(), LEAD, "s"), 0)


class Wiring(unittest.TestCase):
    def test_route_signals_after_capture_before_brand_notice(self):
        s = _src("app/routers/site_intake_router.py")
        cap = s.index("result = pc.capture(")
        sig = s.index("notify_workspace_admins(db, _lead, submission_id)")
        brand = s.index("notify_demo_request(db, platform=platform")
        self.assertTrue(cap < sig < brand)

    def test_in_app_only_and_tenant_scoped(self):
        s = _src("app/services/intake_admin_signal.py")
        code = s.split('"""', 2)[2].lower()
        for banned in ("send_email", "web_push", "twilio", "notification_service",
                       "god_admin"):
            self.assertNotIn(banned, code)
        self.assertIn("workspace_org_admins(db, lead.organization_id)", s)

    def test_ui_opens_lead_from_lead_id(self):
        s = _src("frontend/src/components/NotificationBell.jsx")
        self.assertIn("navigate(`/leads/${n.lead_id}`)", s)


if __name__ == "__main__":
    unittest.main()
