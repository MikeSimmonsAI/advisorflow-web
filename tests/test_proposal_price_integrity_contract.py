"""Dependency-free contract: proposal price math + atomic apply_pricing.

Executes the production pure module (price_math.py) and drives the real
apply_pricing source with synthetic stand-in objects. No DB, no provider,
no network.
"""
import ast
import importlib.util
import os
import unittest
from datetime import datetime
from decimal import Decimal
from typing import Optional

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_s = importlib.util.spec_from_file_location(
    "price_math", os.path.join(_ROOT, "app", "services", "price_math.py"))
M = importlib.util.module_from_spec(_s)
_s.loader.exec_module(M)


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


class Math(unittest.TestCase):
    def test_exact_cents_half_up(self):
        self.assertEqual(M.parse_money(100.005), Decimal("100.01"))
        self.assertEqual(M.parse_money("0.1") + M.parse_money("0.2"), Decimal("0.30"))
        self.assertEqual(M.parse_money("1497"), Decimal("1497.00"))

    def test_non_finite_and_junk_refused(self):
        for bad in ("NaN", "Infinity", "-Infinity", float("nan"), float("inf"),
                    "abc", True, 10 ** 12, "", None):
            self.assertIsNone(M.parse_money(bad), bad)

    def test_not_priced_is_not_zero(self):
        self.assertIsNone(M.price_total(None, None)["total"])
        self.assertEqual(M.price_total(None, 0)["total"], Decimal("0.00"))

    def test_total_and_negative(self):
        self.assertEqual(M.price_total("1500", "-250.50")["total"], Decimal("1249.50"))
        r = M.price_total("100", "-100.01")
        self.assertFalse(r["ok"])
        self.assertIsNone(r["total"])
        self.assertTrue(M.price_total("100", "-100")["ok"])      # exactly zero allowed


def _load_service():
    """Extract the real apply_pricing source from proposal_service without
    importing the app (which needs SQLAlchemy etc.)."""
    tree = ast.parse(_src("app/services/proposal_service.py"))
    want = {"_dec", "apply_pricing", "_apply_pricing", "_PRICING_COLUMNS"}
    body = []
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in want:
            body.append(n)
        elif isinstance(n, ast.Assign) and any(
                getattr(t, "id", None) in want for t in n.targets):
            body.append(n)
    events = []
    ns = {
        "Decimal": Decimal, "parse_money": M.parse_money, "price_total": M.price_total,
        "datetime": datetime,
        "PROPOSAL_EDITABLE_STATUSES": ("draft",),
        "_event": lambda db, *a: events.append(a),
        "Optional": Optional, "Session": None, "Proposal": None,
        "BrandPackage": None, "BrandSalesOrg": None, "Opportunity": None,
    }
    mod = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    exec(compile(mod, "proposal_service_subset", "exec"), ns)
    ns["_events"] = events
    return ns


class Prop:
    def __init__(self):
        self.package_id = None
        self.base_amount = Decimal("100.00")
        self.adjustment = None
        self.final_amount = Decimal("100.00")
        self.currency = "USD"
        self.billing_option = None
        self.contract_term_months = None
        self.price_override_by = None
        self.price_override_at = None
        self.price_override_reason = None
        self.sales_status = "draft"
        self.opportunity_id = "opp-1"
        self.brand_sales_org_id = "b1"


class User:
    id = "u1"


class Atomic(unittest.TestCase):
    def setUp(self):
        self.ns = _load_service()
        self.ns["can_override_price"] = lambda db, u, org: True

    def test_negative_total_refusal_leaves_proposal_untouched(self):
        p = Prop()
        r = self.ns["apply_pricing"](None, p, User(), adjustment=-500, reason="x")
        self.assertFalse(r["ok"])
        self.assertIsNone(p.adjustment)
        self.assertEqual(p.final_amount, Decimal("100.00"))
        self.assertIsNone(p.price_override_by)
        self.assertEqual(self.ns["_events"], [])        # no phantom timeline event

    def test_nan_adjustment_refused_not_stored(self):
        p = Prop()
        r = self.ns["apply_pricing"](None, p, User(), adjustment=float("nan"), reason="x")
        self.assertFalse(r["ok"])
        self.assertIsNone(p.adjustment)

    def test_success_applies_cents_and_logs_once(self):
        p = Prop()
        r = self.ns["apply_pricing"](None, p, User(), adjustment="-10.005", reason="promo")
        self.assertTrue(r["ok"])
        self.assertEqual(p.adjustment, Decimal("-10.01"))
        self.assertEqual(p.final_amount, Decimal("89.99"))
        self.assertEqual(len(self.ns["_events"]), 1)

    def test_rep_refused_without_mutation(self):
        self.ns["can_override_price"] = lambda db, u, org: False
        p = Prop()
        r = self.ns["apply_pricing"](None, p, User(), adjustment=-5, reason="x")
        self.assertFalse(r["ok"])
        self.assertIn("manager", r["error"])
        self.assertIsNone(p.adjustment)

    def test_replay_same_adjustment_is_idempotent(self):
        p = Prop()
        self.ns["apply_pricing"](None, p, User(), adjustment=-10, reason="a")
        self.ns["apply_pricing"](None, p, User(), adjustment=-10, reason="a")
        self.assertEqual(p.final_amount, Decimal("90.00"))
        self.assertEqual(len(self.ns["_events"]), 1)


class Wiring(unittest.TestCase):
    def test_service_uses_shared_math(self):
        s = _src("app/services/proposal_service.py")
        self.assertIn("from app.services.price_math import parse_money, price_total", s)
        self.assertIn("price_total(prop.base_amount, prop.adjustment)", s)

    def test_callers_still_route_through_apply_pricing(self):
        self.assertIn("ps.apply_pricing(", _src("app/routers/sales_proposal_router.py"))
        self.assertIn("ps.apply_pricing(", _src("app/services/pricing_approvals.py"))


if __name__ == "__main__":
    unittest.main()
