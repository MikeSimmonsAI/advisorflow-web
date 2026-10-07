"""Dependency-free contract: one capacity decision + safe tenant-override writes.

Executes the production pure module (capacity_rules.py); synthetic data only.
"""
import importlib.util
import os
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_s = importlib.util.spec_from_file_location(
    "capacity_rules", os.path.join(_ROOT, "app", "services", "capacity_rules.py"))
C = importlib.util.module_from_spec(_s)
_s.loader.exec_module(C)

DIMS = ("max_leads", "max_users", "max_locations", "sms_monthly_allowance")


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


class Decide(unittest.TestCase):
    def test_below_at_above(self):
        self.assertTrue(C.decide(10, 8, 1)["allowed"])
        self.assertTrue(C.decide(10, 9, 1)["allowed"])      # lands exactly at limit
        d = C.decide(10, 10, 1, C.SOURCE_CATALOGUE, "starter")
        self.assertFalse(d["allowed"])
        self.assertIn("10 of 10", d["reason"])
        self.assertIn("starter", d["reason"])
        self.assertFalse(C.decide(10, 14, 1)["allowed"])    # already above

    def test_unlimited(self):
        d = C.decide(None, 99999)
        self.assertTrue(d["allowed"] and d["unlimited"])
        self.assertEqual(d["source"], C.SOURCE_UNLIMITED)

    def test_override_source_reported(self):
        d = C.decide(5, 5, 1, C.SOURCE_OVERRIDE, "enterprise")
        self.assertEqual(d["source"], C.SOURCE_OVERRIDE)
        self.assertIn("explicit tenant override", d["reason"])
        self.assertEqual(C.decide(None, 1, 1, C.SOURCE_OVERRIDE)["source"],
                         C.SOURCE_OVERRIDE)

    def test_purchased_noted(self):
        self.assertIn("incl. 3 purchased", C.decide(8, 8, 1, bought=3)["reason"])

    def test_malformed_plan_values(self):
        for bad in (None, "", "abc", 0, -4, True, [], {}):
            self.assertIsNone(C.normalize_ceiling(bad), bad)
        self.assertEqual(C.normalize_ceiling("25"), 25)


class Validate(unittest.TestCase):
    def v(self, data, unl=None):
        return C.validate_snapshot_ceilings(data, unl, DIMS)

    def test_zero_ceiling_refused_not_silently_unlimited(self):
        self.assertTrue(self.v({"max_leads": 0}))
        self.assertFalse(self.v({"sms_monthly_allowance": 0}))  # 0 allowance is real

    def test_negative_and_garbage(self):
        self.assertTrue(self.v({"max_users": -1}))
        self.assertTrue(self.v({"max_users": "x"}))

    def test_conflicting_unlimited(self):
        self.assertTrue(self.v({"max_leads": 50}, ["max_leads"]))
        self.assertFalse(self.v({}, ["max_leads"]))

    def test_valid(self):
        self.assertEqual(self.v({"max_leads": 5000, "max_users": 3}), [])


class Wiring(unittest.TestCase):
    """Static proof the production paths use the shared rules (no DB here)."""

    def test_write_path_validates_before_snapshot_and_stays_gated(self):
        s = _src("app/routers/god_billing_router.py")
        self.assertLess(s.index("validate_snapshot_ceilings"),
                        s.index("snap = CustomerEntitlementSnapshot("))
        self.assertIn("customer.entitlements_recorded", s)   # audit retained
        i = s.index("def set_customer_entitlements")
        self.assertIn("Depends(require_god)", s[i:i + 400])  # platform-only

    def test_hold_and_refusal_share_check(self):
        self.assertIn('check.get("reason")', _src("app/services/lead_capacity.py"))
        self.assertIn("capacity_rules.decide(", _src("app/services/plan_limits.py"))

    def test_no_billing_calls_added(self):
        self.assertNotIn("stripe", _src("app/services/capacity_rules.py").lower())


if __name__ == "__main__":
    unittest.main()
