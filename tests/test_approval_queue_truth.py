"""Stdlib tests for the manager approval queue decision (no DB, no network)."""
import os
import re
import shutil
import subprocess
import unittest
from decimal import Decimal

from app.services import approval_queue_truth as t

EDIT = ("draft", "internal_review", "ready")
ROOT = os.path.join(os.path.dirname(__file__), "..")


def src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def req(i="r1", brand="b1", status="pending", at="2026-10-01T10:00:00", **kw):
    r = {"id": i, "brand_sales_org_id": brand, "opportunity_id": "o1", "proposal_id": "p1",
         "kind": None, "status": status, "requested_by": "rep1", "requested_by_name": "Rae Rep",
         "requested_at": at, "base_amount": Decimal("1000.10"),
         "current_adjustment": Decimal("0.00"), "requested_adjustment": Decimal("-100.05"),
         "reason": "Customer asked", "decided_by": None,
         "proposal": {"exists": True, "sales_status": "draft",
                      "base_amount": Decimal("1000.10"), "adjustment": Decimal("0.00")},
         "opportunity": {"exists": True, "status": "open", "company_name": "Acme", "stage": "proposal"}}
    r.update(kw)
    return r


def custom(**kw):
    base = dict(kind="custom_deal", proposal_id=None, base_amount=None, current_adjustment=None,
                requested_adjustment=None, requested_unit_price=Decimal("19.99"),
                requested_min_units=3, requested_term_months=12,
                floor_breach_detail='{"summary": "Below floor", "breaches": [{"x": 1}], "ceilings": {"a": 1}}',
                proposal={"exists": False})
    base.update(kw)
    return req(**base)


def q(rows, mgr=True, brand="b1"):
    return t.decide_queue(brand_sales_org_id=brand, requests=rows, viewer_is_manager=mgr,
                          editable_statuses=EDIT)


class Scope(unittest.TestCase):
    def test_other_brand_rows_dropped_and_not_counted(self):
        out = q([req("a"), req("other-brand-req", brand="b2")])
        self.assertEqual([i["id"] for i in out["pending"]], ["a"])
        self.assertEqual(out["pending_count"], 1)
        self.assertNotIn("other-brand-req", repr(out))

    def test_non_manager_sees_nothing_and_counts_are_none(self):
        out = q([req("a")], mgr=False)
        self.assertFalse(out["authorized"])
        self.assertEqual(out["pending"], [])
        self.assertIsNone(out["pending_count"])

    def test_missing_brand_refused(self):
        with self.assertRaises(t.QueueInputError):
            q([req()], brand="")


class Ordering(unittest.TestCase):
    def test_oldest_first_then_id_and_repeatable(self):
        rows = [req("c", at="2026-10-02T00:00:00"), req("b", at="2026-10-01T00:00:00"),
                req("a", at="2026-10-01T00:00:00")]
        ids = [i["id"] for i in q(rows)["pending"]]
        self.assertEqual(ids, ["a", "b", "c"])
        self.assertEqual(ids, [i["id"] for i in q(list(reversed(rows)))["pending"]])


class Separation(unittest.TestCase):
    def test_resolved_withdrawn_stale_never_pending(self):
        rows = [req("p")] + [req(s, status=s, decided_at="2026-10-03") for s in
                             ("approved", "denied", "withdrawn", "stale")]
        out = q(rows)
        self.assertEqual([i["id"] for i in out["pending"]], ["p"])
        self.assertEqual(out["pending_count"], 1)
        self.assertEqual(len(out["history"]), 4)
        self.assertTrue(all(not h["actionable"] for h in out["history"]))

    def test_blockers_precise_and_counted(self):
        rows = [req("lock", proposal={"exists": True, "sales_status": "sent", "base_amount": 1, "adjustment": 0}),
                req("gone", proposal={"exists": False}),
                req("nodeal", opportunity={"exists": False}),
                req("closed", opportunity={"exists": True, "status": "won"}),
                req("neg", requested_adjustment=Decimal("-2000")),
                req("inc", requested_adjustment=None),
                req("ok")]
        out = q(rows)
        by = {i["id"]: i for i in out["pending"]}
        self.assertEqual(by["lock"]["blocker"], t.B_PROPOSAL_LOCKED)
        self.assertEqual(by["gone"]["blocker"], t.B_PROPOSAL_MISSING)
        self.assertEqual(by["nodeal"]["blocker"], t.B_DEAL_MISSING)
        self.assertEqual(by["closed"]["blocker"], t.B_DEAL_CLOSED)
        self.assertEqual(by["neg"]["blocker"], t.B_NEGATIVE_TOTAL)
        self.assertEqual(by["inc"]["blocker"], t.B_FACTS_INCOMPLETE)
        self.assertTrue(by["ok"]["actionable"])
        self.assertEqual((out["actionable_count"], out["blocked_count"]), (1, 6))
        self.assertTrue(all(i["blocker_text"] for i in out["pending"] if i["blocker"]))


class Cents(unittest.TestCase):
    def test_exact_cents_no_float_drift(self):
        self.assertEqual(t.to_cents(Decimal("0.10")) + t.to_cents(Decimal("0.20")), 30)
        m = q([req()])["pending"][0]["money"]
        self.assertEqual(m["requested_total"], {"cents": 90005, "display": "900.05"})
        self.assertEqual(m["current_total"]["display"], "1,000.10")

    def test_unavailable_is_none_not_zero(self):
        for bad in (None, "", "abc", "NaN", "Infinity", "1.005", True, 10 ** 12):
            self.assertIsNone(t.to_cents(bad), bad)
        m = q([req(requested_adjustment=None)])["pending"][0]["money"]
        self.assertIsNone(m["requested_total"])
        self.assertIsNone(m["requested_adjustment"])

    def test_custom_deal_monthly_cents(self):
        it = q([custom()])["pending"][0]
        self.assertEqual(it["money"]["requested_monthly"]["cents"], 1999 * 3)
        self.assertTrue(it["actionable"])
        self.assertTrue(it["policy"]["available"])
        self.assertEqual(it["policy"]["summary"], "Below floor")

    def test_missing_policy_stated(self):
        self.assertFalse(q([custom(floor_breach_detail=None)])["pending"][0]["policy"]["available"])
        self.assertFalse(q([custom(floor_breach_detail="{bad")])["pending"][0]["policy"]["available"])
        self.assertFalse(q([req()])["pending"][0]["policy"]["available"])

    def test_custom_incomplete_blocked(self):
        self.assertEqual(q([custom(requested_min_units=None)])["pending"][0]["blocker"],
                         t.B_FACTS_INCOMPLETE)


class Version(unittest.TestCase):
    def test_changes_when_live_facts_change(self):
        a = t.version_token(req())
        b = t.version_token(req(proposal={"exists": True, "sales_status": "sent",
                                          "base_amount": Decimal("1000.10"), "adjustment": Decimal("0")}))
        self.assertNotEqual(a, b)
        self.assertEqual(a, t.version_token(req()))


def plan(r, approve=True, version="auto", actor="m1", mgr=True, brand="b1"):
    v = t.version_token(r) if version == "auto" and r else version
    return t.plan_decision(request=r, brand_sales_org_id=brand, actor_id=actor, actor_is_manager=mgr,
                           approve=approve, expected_version=v, editable_statuses=EDIT)


class Mutation(unittest.TestCase):
    def test_apply_when_current(self):
        self.assertEqual(plan(req())["outcome"], t.O_APPLY)
        self.assertEqual(plan(req(), approve=False)["outcome"], t.O_APPLY)

    def test_seller_and_cross_brand_are_404(self):
        self.assertEqual(plan(req(), mgr=False)["outcome"], t.O_NOT_FOUND)
        self.assertEqual(plan(req(brand="b2"))["outcome"], t.O_NOT_FOUND)
        self.assertEqual(plan(None)["outcome"], t.O_NOT_FOUND)

    def test_missing_or_stale_version(self):
        self.assertEqual(plan(req(), version=None)["outcome"], t.O_REFUSED)
        self.assertEqual(plan(req(), version="v1.aaaa.bbbb")["outcome"], t.O_CONFLICT)

    def test_state_changed_since_load_is_conflict(self):
        old = t.version_token(req())
        locked = req(proposal={"exists": True, "sales_status": "sent", "base_amount": 1, "adjustment": 0})
        self.assertEqual(plan(locked, version=old)["outcome"], t.O_CONFLICT)

    def test_approve_blocked_is_refused_but_deny_allowed(self):
        locked = req(proposal={"exists": True, "sales_status": "sent", "base_amount": 1, "adjustment": 0})
        p = plan(locked)
        self.assertEqual((p["outcome"], p["blocker"]), (t.O_REFUSED, t.B_PROPOSAL_LOCKED))
        self.assertEqual(plan(locked, approve=False)["outcome"], t.O_APPLY)

    def test_replay_same_manager_same_answer_only(self):
        pending = req()
        v = t.version_token(pending)
        done = req(status="approved", decided_by="m1")
        self.assertEqual(plan(done, version=v)["outcome"], t.O_REPLAY)
        self.assertEqual(plan(done, version=v, actor="m2")["outcome"], t.O_CONFLICT)
        self.assertEqual(plan(done, approve=False, version=v)["outcome"], t.O_CONFLICT)
        self.assertEqual(plan(req(status="withdrawn"), version=v)["outcome"], t.O_CONFLICT)
        self.assertEqual(plan(req(status="stale", decided_by="m1"), version=v)["outcome"], t.O_CONFLICT)


class Wiring(unittest.TestCase):
    def test_router_gates_and_row_lock_and_rollback(self):
        s = src("app/routers/sales_manager_router.py")
        for route in ('@router.get("/approvals/queue")', '@router.post("/approvals/{request_id}/decision")'):
            seg = s[s.index(route):s.index(route) + 400]
            self.assertIn("Depends(require_sales_manager)", seg)
        body = s[s.index("def decide_approval_versioned"):s.index("# PIPELINE FINANCIAL PROJECTION")]
        self.assertIn("lock=True", body)
        self.assertIn("db.rollback()", body)
        self.assertLess(body.index("plan_decision"), body.index("_appr.decide("))
        queue = s[s.index("def approvals_queue"):s.index("def decide_approval_versioned")]
        self.assertNotIn("commit", queue)
        self.assertNotIn("sweep_stale", queue)

    def test_gather_is_read_only_and_brand_filtered(self):
        g = src("app/services/approval_queue_gather.py")
        self.assertNotRegex(g, r"\.(add|commit|delete|flush)\(")
        self.assertIn("brand_sales_org_id == brand_sales_org_id", g)

    def test_no_send_charge_provider_anywhere(self):
        for rel in ("app/services/approval_queue_truth.py", "app/services/approval_queue_gather.py",
                    "frontend/src/pages/sales/ApprovalQueue.jsx", "frontend/src/utils/approvalQueue.js"):
            code = re.sub(r"/\*.*?\*/|#.*", "", src(rel), flags=re.S)
            self.assertNotRegex(code.lower(), r"stripe|twilio|sendgrid|smtp|charge\(|/send|finalize|payout")

    def test_page_nav_route_and_guards(self):
        page = src("frontend/src/pages/sales/ApprovalQueue.jsx")
        self.assertIn("inflight.current", page)
        self.assertIn("decisionBody(it", page)
        self.assertIn("if (m.reload) await load()", page)
        self.assertIn("Loading approvals", page)
        self.assertIn("Nothing is waiting on you", page)
        self.assertNotRegex(page, r"parseFloat|Number\(|toFixed")
        shell = src("frontend/src/pages/sales/SalesShell.jsx")
        mgr = shell[shell.index("const MANAGER_NAV"):shell.index("export default function SalesShell")]
        i = mgr.index("/sales/approvals")
        self.assertIn("view_team_pipeline", mgr[i:i + 160])
        self.assertNotIn("/sales/approvals", shell[:shell.index("const MANAGER_NAV")])
        self.assertIn('path="/sales/approvals"', src("frontend/src/App.jsx"))

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_node_helper_suite(self):
        r = subprocess.run(["node", "--test", "frontend/tests/approvalQueue.test.mjs"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"# fail 0")


if __name__ == "__main__":
    unittest.main()
