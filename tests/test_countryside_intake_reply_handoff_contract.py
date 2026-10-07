"""Dependency-free contract: website intake -> unassigned lead -> hot SMS reply
notifies the workspace's own org_admins (it notified nobody). Synthetic only."""
import importlib.util
import os
import unittest
from types import SimpleNamespace as NS

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location(
    "reply_handoff_under_test", os.path.join(_ROOT, "app", "services", "reply_handoff.py"))
RH = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RH)


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


class Recipients(unittest.TestCase):
    def test_assigned_advisor_only(self):
        adv = NS(id="adv")
        called = []
        out = RH.reply_recipients(adv, lambda: called.append(1) or [NS(id="adm")])
        self.assertEqual(out, [adv])
        self.assertEqual(called, [])  # admins never even loaded

    def test_unassigned_intake_lead_falls_back_to_org_admins(self):
        a1, a2 = NS(id="a1", is_active=True), NS(id="a2", is_active=None)
        gone = NS(id="a3", is_active=False)
        out = RH.reply_recipients(None, lambda: [a1, gone, a2])
        self.assertEqual([u.id for u in out], ["a1", "a2"])

    def test_no_admins_is_empty_not_error(self):
        self.assertEqual(RH.reply_recipients(None, lambda: []), [])

    def test_no_org_loads_nothing(self):
        self.assertEqual(RH.workspace_org_admins(object(), None), [])


class SourceWiring(unittest.TestCase):
    def test_sms_webhook_uses_fallback(self):
        s = _src("app/routers/sms_router.py")
        self.assertNotIn("if is_hot and lead.assigned_to:", s)
        self.assertIn("reply_recipients(", s)
        self.assertIn("workspace_org_admins(db, lead.organization_id)", s)

    def test_admin_query_is_tenant_scoped_and_not_god(self):
        s = _src("app/services/reply_handoff.py")
        self.assertIn("User.organization_id == organization_id", s)
        self.assertIn('User.role == "org_admin"', s)
        self.assertNotIn("god_admin", s)

    def test_intake_creates_unassigned_lead_in_destination_org(self):
        s = _src("app/services/public_capture.py")
        self.assertNotIn("assigned_to_id=", s)
        self.assertIn("organization_id=org.id", s)


if __name__ == "__main__":
    unittest.main()
