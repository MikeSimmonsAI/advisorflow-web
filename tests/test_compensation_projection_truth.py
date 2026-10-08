"""Regression proof for the pure compensation projection decision.

Stdlib unittest (also collected by pytest). Imports only the pure module, so it
runs without SQLAlchemy / a database.
"""
import importlib.util
import os
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

_P = os.path.join(os.path.dirname(__file__), "..", "app", "services",
                  "compensation_projection_truth.py")
_spec = importlib.util.spec_from_file_location("cpt", _P)
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

NOW = datetime(2026, 10, 8, 12, 0, 0)
B = "brand-a"


def deal(oid, stage="proposal", owner="rep1", fixed="10000.00",
         payouts=None, **kw):
    d = {"opportunity_id": oid, "brand_sales_org_id": B, "owner_user_id": owner,
         "company_name": "Co " + oid, "stage": stage, "fixed_value": Decimal(fixed),
         "pricing_complete": True, "comp_status": "projected",
         "plan_configured": True, "plan_name": "Plan",
         "payouts": payouts if payouts is not None else [
             {"payee_user_id": owner, "payee_kind": "seller",
              "amount": Decimal("1000.00"), "rule_id": "r1", "basis": "pct_setup",
              "rate_percent": 10.0},
             {"payee_user_id": "mgr", "payee_kind": "override",
              "amount": Decimal("200.00"), "rule_id": "r2", "basis": "pct_setup",
              "rate_percent": 2.0}]}
    d.update(kw)
    return d


def entry(eid, state="earned", payable_at=None, payee="rep1", amount="500.00",
          **kw):
    e = {"entry_id": eid, "brand_sales_org_id": B, "opportunity_id": "o",
         "payee_user_id": payee, "amount": Decimal(amount), "state": state,
         "payable_at": payable_at, "collection_reference": "pay_1"}
    e.update(kw)
    return e


def run(deals=(), entries=(), probs=None, **kw):
    return T.decide(brand_sales_org_id=B, deals=list(deals),
                    entries=list(entries),
                    probabilities={"proposal": "50"} if probs is None else probs,
                    now=NOW, **kw)


class Cents(unittest.TestCase):
    def test_exact_cents(self):
        self.assertEqual(T.to_cents("0.10") + T.to_cents("0.20"), 30)
        self.assertEqual(T.to_cents(Decimal("1234.56")), 123456)
        self.assertEqual(T.format_cents(123456789), "1,234,567.89")
        self.assertEqual(T.format_cents(5), "0.05")

    def test_refusals(self):
        for bad in (float("nan"), float("inf"), 1.5, True, "abc", "-1",
                    "1.005", Decimal("NaN"), Decimal("Infinity"),
                    "999999999999999", None):
            with self.assertRaises(T.ProjectionInputError, msg=repr(bad)):
                T.to_cents(bad)

    def test_weigh_half_up(self):
        self.assertEqual(T.weigh(1001, 5000), 501)   # 500.5 -> 501
        self.assertEqual(T.weigh(1000, 3333), 333)
        self.assertEqual(T.weigh(1000, 10000), 1000)
        self.assertEqual(T.weigh(1000, 0), 0)

    def test_probability_validation(self):
        for bad in ("101", "-1", "50.005", "x", None, 12.5):
            with self.assertRaises(T.ProjectionInputError, msg=repr(bad)):
                T.to_basis_points(bad)
        self.assertEqual(T.to_basis_points("33.33"), 3333)


class Forecast(unittest.TestCase):
    def test_stage_weighting_and_totals(self):
        r = run([deal("o1"), deal("o2", stage="demo", fixed="2000.00")],
                probs={"proposal": "50", "demo": "25.5"})
        f = r["forecast"]
        self.assertEqual(f["gross_sales"]["cents"], 1200000)
        self.assertEqual(f["commission"]["cents"], 2 * 120000)
        self.assertEqual(f["weighted_gross_sales"]["cents"], 500000 + 51000)
        self.assertEqual(f["weighted_commission"]["cents"], 60000 + 30600)
        self.assertEqual(f["deal_count"], 2)
        self.assertEqual(r["earned"]["total"]["cents"], 0)

    def test_missing_probability_not_zero(self):
        r = run([deal("o1", stage="negotiation")])
        f = r["forecast"]
        self.assertEqual(f["gross_sales"]["cents"], 1000000)
        self.assertIsNone(f["weighted_gross_sales"]["cents"])
        self.assertIsNone(f["weighted_commission"]["cents"])
        self.assertEqual(f["weighted_missing_probability_count"], 1)
        self.assertTrue(r["blockers"])

    def test_forecast_never_labelled_earned(self):
        r = run([deal("o1")])
        self.assertEqual(r["earned"]["total"]["cents"], 0)
        self.assertEqual(r["deals"][0]["category"], "forecast")
        self.assertIn("not earned", r["deals"][0]["label"].lower())
        self.assertIn("not earned", r["disclaimer"].lower())

    def test_pending_approval_held_out_of_forecast(self):
        r = run([deal("o1", comp_status="pending_approval")])
        self.assertEqual(r["forecast"]["commission"]["cents"], 0)
        self.assertEqual(r["forecast"]["gross_sales"]["cents"], 0)
        self.assertEqual(r["pending"]["commission"]["cents"], 120000)
        self.assertEqual(r["pending"]["deal_count"], 1)
        self.assertEqual(r["deals"][0]["category"], "pending")

    def test_cap_respected_and_rounding_slack(self):
        capped = deal("o1", capped=True, cap_amount=Decimal("1000.00"),
                      payouts=[{"payee_user_id": "rep1", "payee_kind": "seller",
                                "amount": Decimal("833.34"), "rule_id": "a"},
                               {"payee_user_id": "mgr", "payee_kind": "override",
                                "amount": Decimal("166.67"), "rule_id": "b"}])
        r = run([capped])
        self.assertEqual(r["forecast"]["commission"]["cents"], 100001)
        self.assertTrue(r["deals"][0]["capped"])
        over = deal("o2", cap_amount=Decimal("100.00"))
        with self.assertRaises(T.ProjectionInputError):
            run([over])

    def test_exclusions_are_named_not_zero(self):
        r = run([
            deal("n1", comp_status="unconfigured", plan_configured=False,
                 payouts=[]),
            deal("n2", payouts=[]),
            deal("n3", pricing_complete=False),
            deal("n4", fixed_value=None),
        ])
        reasons = {x["opportunity_id"]: x["reason"] for x in r["excluded"]}
        self.assertEqual(reasons, {
            "n1": T.EX_NO_PLAN, "n2": T.EX_NO_RATE,
            "n3": T.EX_PRICING_INCOMPLETE, "n4": T.EX_NO_PRICING})
        self.assertEqual(r["forecast"]["deal_count"], 0)
        self.assertEqual(r["forecast"]["commission"]["cents"], 0)
        self.assertTrue(any("plan" in b.lower() for b in r["blockers"]))

    def test_stage_filter(self):
        r = run([deal("o1"), deal("o2", stage="demo")], stage="demo",
                probs={"demo": "10"})
        self.assertEqual([d["opportunity_id"] for d in r["deals"]], ["o2"])

    def test_replay_and_stable_ordering(self):
        ds = [deal("o3"), deal("o1", stage="demo"), deal("o2")]
        es = [entry("e2"), entry("e1", state="paid")]
        a = run(ds, es, probs={"proposal": "50", "demo": "10"})
        b = run(list(reversed(ds)), list(reversed(es)),
                probs={"proposal": "50", "demo": "10"})
        self.assertEqual(a, b)
        self.assertEqual([d["opportunity_id"] for d in a["deals"]],
                         ["o1", "o2", "o3"])   # by stage, then id
        self.assertEqual(a, run(ds, es, probs={"proposal": "50", "demo": "10"}))


class Earned(unittest.TestCase):
    def test_holdback_labels(self):
        r = run(entries=[
            entry("e1", payable_at=NOW + timedelta(days=3)),
            entry("e2", payable_at=NOW - timedelta(days=1)),
            entry("e3", state="payable"),
            entry("e4", state="paid", amount="100.00"),
            entry("e5", state="void", amount="999.00"),
            entry("e6", payable_at=None),
        ])
        e = r["earned"]
        self.assertEqual(e["on_hold"]["cents"], 100000)   # e1 + e6 (unknown => held)
        self.assertEqual(e["on_hold"]["count"], 2)
        self.assertEqual(e["payable"]["cents"], 100000)   # e2 + e3
        self.assertEqual(e["paid"]["cents"], 10000)
        self.assertEqual(e["total"]["cents"], 210000)     # void excluded
        self.assertEqual(r["forecast"]["commission"]["cents"], 0)

    def test_missing_payment_evidence_refused(self):
        with self.assertRaises(T.ProjectionInputError):
            run(entries=[entry("e1", collection_reference="")])

    def test_bad_state_and_duplicates(self):
        with self.assertRaises(T.ProjectionInputError):
            run(entries=[entry("e1", state="projected")])
        with self.assertRaises(T.ProjectionInputError):
            run(entries=[entry("e1"), entry("e1")])
        with self.assertRaises(T.ProjectionInputError):
            run(deals=[deal("o1"), deal("o1")])

    def test_negative_or_subcent_amounts_refused(self):
        with self.assertRaises(T.ProjectionInputError):
            run(entries=[entry("e1", amount="-5.00")])
        with self.assertRaises(T.ProjectionInputError):
            run(entries=[entry("e1", amount="1.005")])
        with self.assertRaises(T.ProjectionInputError):
            run(deals=[deal("o1", payouts=[{"payee_user_id": "rep1",
                                            "amount": Decimal("NaN")}])])


class Scope(unittest.TestCase):
    def test_seller_sees_only_own_share(self):
        ds = [deal("o1"), deal("o2", owner="rep2",
                               payouts=[{"payee_user_id": "rep2",
                                         "payee_kind": "seller",
                                         "amount": Decimal("300.00")}])]
        es = [entry("e1", payee="rep1"), entry("e2", payee="rep2"),
              entry("e3", payee="mgr")]
        r = run(ds, es, payee_user_id="rep1")
        self.assertEqual(r["scope"], "seller")
        self.assertEqual(r["forecast"]["commission"]["cents"], 100000)  # no override
        self.assertEqual(r["earned"]["total"]["cents"], 50000)
        self.assertEqual([d["opportunity_id"] for d in r["deals"]], ["o1"])
        mgr = run(ds, es)
        self.assertEqual(mgr["scope"], "management")
        self.assertEqual(mgr["forecast"]["commission"]["cents"], 150000)
        self.assertEqual(mgr["earned"]["total"]["cents"], 150000)

    def test_cross_tenant_rows_refused(self):
        with self.assertRaises(T.ProjectionInputError):
            run(deals=[deal("o1", brand_sales_org_id="brand-b")])
        with self.assertRaises(T.ProjectionInputError):
            run(entries=[entry("e1", brand_sales_org_id="brand-b")])

    def test_resolve_scope(self):
        rs = T.resolve_scope
        base = dict(user_id="u", requested_payee=None)
        self.assertEqual(rs(requested_brand=B, management_brand_ids=[B],
                            member_brand_ids=[B], **base)["scope"], "management")
        s = rs(requested_brand=B, management_brand_ids=[], member_brand_ids=[B],
               **base)
        self.assertEqual((s["scope"], s["payee_user_id"]), ("seller", "u"))
        with self.assertRaises(T.ScopeRefused) as c:   # other seller
            rs(requested_brand=B, management_brand_ids=[], member_brand_ids=[B],
               user_id="u", requested_payee="other")
        self.assertEqual(c.exception.status, 403)
        with self.assertRaises(T.ScopeRefused) as c:   # cross-tenant brand
            rs(requested_brand="brand-b", management_brand_ids=[B],
               member_brand_ids=[B], **base)
        self.assertEqual(c.exception.status, 403)
        with self.assertRaises(T.ScopeRefused) as c:   # nothing at all
            rs(requested_brand=None, management_brand_ids=[],
               member_brand_ids=[], **base)
        self.assertEqual(c.exception.status, 403)
        with self.assertRaises(T.ScopeRefused) as c:   # ambiguous
            rs(requested_brand=None, management_brand_ids=[B, "c"],
               member_brand_ids=[], **base)
        self.assertEqual(c.exception.status, 400)
        self.assertEqual(rs(requested_brand=None, management_brand_ids=[],
                            member_brand_ids=[B], **base)["brand_sales_org_id"], B)


class UiContract(unittest.TestCase):
    """Static checks on the JSX / router (not compiled or browser-verified)."""
    root = os.path.join(os.path.dirname(__file__), "..")

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_page_has_no_money_movement_controls(self):
        src = self._read("frontend", "src", "pages", "sales",
                         "CompensationProjection.jsx")
        code = src.split("*/", 1)[1].lower()
        for word in ("<button", "api.post", "api.patch", "api.put", "api.delete",
                     "stripe", "ach", "payroll", "tolocalestring", "intl.",
                     "parsefloat", "tofixed"):
            self.assertNotIn(word, code.replace("approve", ""), word)
        self.assertIn("var(--surface-card)", src)
        self.assertIn("not earned", src.lower())
        helper = self._read("frontend", "src", "utils", "compensationProjection.js")
        for code_ in ("403", "422", "400"):
            self.assertIn(code_, helper)

    def test_route_and_endpoint_wired_read_only(self):
        app = self._read("frontend", "src", "App.jsx")
        self.assertIn("/sales/compensation-projection", app)
        rt = self._read("app", "routers", "compensation_router.py")
        self.assertIn('@router.get("/projection")', rt)
        self.assertIn("resolve_scope", rt)
        gather = self._read("app", "services", "compensation_projection_gather.py")
        for w in (".add(", ".commit(", ".delete(", ".flush("):
            self.assertNotIn(w, gather)


class AvailableStages(unittest.TestCase):
    """The stage dropdown must not shrink when a stage is selected."""

    PROBS = {"proposal": "50", "discovery": "20", "negotiation": "70"}

    def _deals(self):
        rep2 = [{"payee_user_id": "rep2", "payee_kind": "seller",
                 "amount": Decimal("500.00"), "rule_id": "r1",
                 "basis": "pct_setup", "rate_percent": 5.0}]
        return [deal("o1", stage="proposal"), deal("o2", stage="discovery"),
                deal("o3", stage="negotiation", owner="rep2", payouts=rep2),
                deal("o4", stage="proposal")]

    def test_unfiltered_sorted_and_deduplicated(self):
        r = run(self._deals(), probs=self.PROBS)
        self.assertEqual(r["available_stages"],
                         ["discovery", "negotiation", "proposal"])

    def test_selection_does_not_shrink_options_and_switching(self):
        base = run(self._deals(), probs=self.PROBS)["available_stages"]
        for s in base:  # direct switch to any stage keeps the full list
            r = run(self._deals(), probs=self.PROBS, stage=s)
            self.assertEqual(r["available_stages"], base)
            self.assertEqual(r["filters"]["stage"], s)
            self.assertTrue(all(x["stage"] == s for x in r["deals"]))
        r = run(self._deals(), probs=self.PROBS, stage=None)  # All stages
        self.assertEqual(len(r["deals"]), 4)

    def test_seller_scope_only_offers_own_stages(self):
        r = run(self._deals(), probs=self.PROBS, payee_user_id="rep2")
        self.assertEqual(r["available_stages"], ["negotiation"])
        r = run(self._deals(), probs=self.PROBS, payee_user_id="rep1",
                stage="proposal")
        self.assertEqual(r["available_stages"], ["discovery", "proposal"])

    def test_excluded_deals_still_offer_their_stage(self):
        d = deal("o5", stage="stalled", pricing_complete=False)
        r = run([d, deal("o1")], probs=self.PROBS, stage="proposal")
        self.assertIn("stalled", r["available_stages"])

    def test_empty_and_no_match(self):
        self.assertEqual(run([])["available_stages"], [])
        r = run(self._deals(), probs=self.PROBS, stage="nope")
        self.assertEqual(r["deals"], [])
        self.assertEqual(r["available_stages"],
                         ["discovery", "negotiation", "proposal"])

    def test_no_cross_tenant_leak(self):
        other = deal("x1", stage="secret")
        other["brand_sales_org_id"] = "brand-b"
        with self.assertRaises(T.ProjectionInputError):
            run([deal("o1"), other])


if __name__ == "__main__":
    unittest.main()
