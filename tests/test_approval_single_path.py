"""Single-path closure for manager approvals (stdlib; no DB, no network).

One read (`/approvals/queue`), one write (`/approvals/{id}/decision`). The
legacy unversioned `/decide` is retired and no GET writes.
"""
import os
import re
import unittest
from decimal import Decimal

from app.services import approval_queue_truth as t

ROOT = os.path.join(os.path.dirname(__file__), "..")
EDIT = ("draft", "internal_review", "ready")
WRITES = r"\.(add|flush|commit|delete|merge)\(|sweep_stale|\.status\s*=(?!=)|\.decided_at\s*=(?!=)"


def src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def code(rel):
    # Strip whole-line comments only; inline "#"/"//" can sit inside strings.
    text = src(rel)
    if rel.endswith((".js", ".jsx", ".ts")):
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^[ \t]*(//|#)[^\n]*$", "", text, flags=re.M)


def seg(text, start, end):
    return text[text.index(start):text.index(end, text.index(start) + 1)]


def req(i="r1", brand="b1", status="pending", decided_by=None):
    return {"id": i, "brand_sales_org_id": brand, "opportunity_id": "o1", "proposal_id": "p1",
            "kind": None, "status": status, "requested_by": "rep1", "requested_by_name": "Rae",
            "requested_at": "2026-10-01T10:00:00", "base_amount": Decimal("1000.10"),
            "current_adjustment": Decimal("0.00"), "requested_adjustment": Decimal("-100.05"),
            "reason": "x", "decided_by": decided_by,
            "proposal": {"exists": True, "sales_status": "draft",
                         "base_amount": Decimal("1000.10"), "adjustment": Decimal("0.00")},
            "opportunity": {"exists": True, "status": "open", "company_name": "Acme", "stage": "p"}}


def plan(r, version=None, actor="m1", mgr=True, approve=True, brand="b1"):
    return t.plan_decision(request=r, brand_sales_org_id=brand, actor_id=actor,
                           actor_is_manager=mgr, approve=approve,
                           expected_version=version, editable_statuses=EDIT)


class GetRoutesAreReadOnly(unittest.TestCase):
    def test_router_get_routes_do_no_write(self):
        s = code("app/routers/sales_manager_router.py")
        for name, nxt in (("def manager_overview", "def manager_rep_detail"),
                          ("def manager_rep_detail", "def manager_approvals"),
                          ("def manager_approvals", "def decide_approval("),
                          ("def approvals_queue", "def decide_approval_versioned"),
                          ("def pipeline_projection", "return result")):
            self.assertNotRegex(seg(s, name, nxt), WRITES, name)

    def test_overview_service_has_no_sweep_or_write(self):
        s = code("app/services/manager_workspace.py")
        self.assertNotIn("sweep_stale", s)
        self.assertNotIn("pending_for_brand", s)
        self.assertNotRegex(s, r"\bdb\.(add|flush|commit|delete|merge)\(")

    def test_no_route_calls_sweep_stale(self):
        for rel in ("app/routers/sales_manager_router.py", "app/services/manager_workspace.py"):
            self.assertNotIn("sweep_stale", code(rel))


class LegacyDecideRetired(unittest.TestCase):
    def test_legacy_decide_refuses_and_never_decides(self):
        s = code("app/routers/sales_manager_router.py")
        body = seg(s, "def decide_approval(", "def approvals_queue")
        self.assertIn("status_code=410", body)
        self.assertIn("require_sales_manager", body)
        self.assertNotIn("_appr.decide", body)
        self.assertNotIn("get_db", body)           # no session, so no possible write
        self.assertNotRegex(body, WRITES)

    def test_only_versioned_endpoint_calls_decide(self):
        s = code("app/routers/sales_manager_router.py")
        self.assertEqual(len(re.findall(r"_appr\.decide\(", s)), 1)
        ver = seg(s, "def decide_approval_versioned", "def pipeline_projection")
        self.assertIn("_appr.decide(", ver)
        self.assertIn("lock=True", ver)
        self.assertIn("plan_decision", ver)

    def test_no_ui_or_mobile_caller_of_legacy_decide(self):
        for rel in ("frontend/src/pages/sales/ManagerCommand.jsx",
                    "frontend/src/pages/sales/ApprovalQueue.jsx", "mobile/src/api/endpoints.ts"):
            self.assertNotRegex(src(rel), r"/sales/manager/approvals/[^`'\"]*/decide[`'\"]", rel)
        self.assertNotRegex(src("mobile/src/api/endpoints.ts"), r"manager/approvals\$\{qs|manager/approvals`")


class EveryCallerSendsVersion(unittest.TestCase):
    def test_pages_post_decision_via_decisionBody(self):
        for rel in ("frontend/src/pages/sales/ManagerCommand.jsx",
                    "frontend/src/pages/sales/ApprovalQueue.jsx"):
            s = src(rel)
            posts = re.findall(r"api\.post\(([^)]*)", s)
            self.assertEqual(len(posts), 1, rel)
            self.assertIn("/decision`", posts[0])
            self.assertIn("decisionBody(it", s)
            self.assertIn("inflight.current", s)
            self.assertNotRegex(s, r"parseFloat|toFixed")

    def test_manager_command_reloads_on_409_404_and_no_false_success(self):
        s = src("frontend/src/pages/sales/ManagerCommand.jsx")
        fn = seg(s, "async function decide(", "async function openRepDetail")
        self.assertIn("errorMessage(e)", fn)
        self.assertIn("if (m.reload) await load()", fn)
        self.assertLess(fn.index("await api.post"), fn.index("setNote(res.replay"))   # note only after success
        self.assertNotIn("setNote(", fn[fn.index("} catch"):])

    def test_decision_body_always_carries_expected_version(self):
        self.assertIn("expected_version: item.version", src("frontend/src/utils/approvalQueue.js"))
        self.assertIn("expected_version", src("app/routers/sales_manager_router.py"))

    def test_missing_version_refused(self):
        self.assertEqual(plan(req(), version=None)["outcome"], t.O_REFUSED)


class OnePendingCount(unittest.TestCase):
    def test_overview_uses_same_queue_decision(self):
        s = code("app/services/manager_workspace.py")
        self.assertIn("approval_queue_truth", s)
        self.assertIn("decide_queue(", s)
        r = code("app/routers/sales_manager_router.py")
        self.assertIn("decide_queue(", seg(r, "def approvals_queue", "def decide_approval_versioned"))
        # legacy GET is an alias of the queue, not a second query
        self.assertIn("approvals_queue(", seg(r, "def manager_approvals", "def decide_approval("))

    def test_count_and_order_come_from_one_function(self):
        rows = [req("b", brand="b1"), req("a", brand="b1"), req("z", brand="b2")]
        rows[0]["requested_at"] = "2026-10-02T00:00:00"
        out = t.decide_queue(brand_sales_org_id="b1", requests=rows, viewer_is_manager=True,
                             editable_statuses=EDIT)
        self.assertEqual([i["id"] for i in out["pending"]], ["a", "b"])
        self.assertEqual(out["pending_count"], len(out["pending"]))

    def test_team_proposals_reads_new_shape(self):
        s = src("frontend/src/pages/sales/TeamProposals.jsx")
        self.assertNotIn("status_label}</Chip>\n              <span>{r.requested_by_name", s)
        self.assertIn("usd(r.money?.requested_adjustment)", s)


class Decisions(unittest.TestCase):
    def test_stale_version_is_409_and_cross_brand_or_seller_hidden(self):
        v = t.version_token(req())
        self.assertEqual(plan(req(), version=v)["outcome"], t.O_APPLY)
        changed = req(); changed["requested_adjustment"] = Decimal("-1.00")
        self.assertEqual(plan(changed, version=v)["outcome"], t.O_CONFLICT)
        self.assertEqual(plan(req(brand="b2"), version=v)["outcome"], t.O_NOT_FOUND)
        self.assertEqual(plan(req(), version=v, mgr=False)["outcome"], t.O_NOT_FOUND)
        self.assertEqual(plan(None, version=v)["outcome"], t.O_NOT_FOUND)

    def test_same_manager_repeat_is_replay_no_write(self):
        v = t.version_token(req())
        done = req(status="approved", decided_by="m1")
        self.assertEqual(plan(done, version=v)["outcome"], t.O_REPLAY)
        s = code("app/routers/sales_manager_router.py")
        ver = seg(s, "def decide_approval_versioned", "def pipeline_projection")
        replay = seg(ver, "O_REPLAY", "res = _appr.decide")
        self.assertNotRegex(replay, r"commit|\.add\(")

    def test_refusal_paths_rollback(self):
        s = code("app/routers/sales_manager_router.py")
        ver = seg(s, "def decide_approval_versioned", "def pipeline_projection")
        self.assertGreaterEqual(ver.count("db.rollback()"), 4)


class NoOutboundAction(unittest.TestCase):
    def test_no_send_charge_provider(self):
        for rel in ("app/routers/sales_manager_router.py", "app/services/manager_workspace.py",
                    "frontend/src/pages/sales/ManagerCommand.jsx",
                    "frontend/src/pages/sales/ApprovalQueue.jsx"):
            c = code(rel).lower()
            # manager_workspace/ManagerCommand are broader screens; scan only approval parts
            if rel.endswith("ManagerCommand.jsx"):
                c = seg(c, "async function decide(", "async function openrepdetail")
            self.assertNotRegex(c, r"stripe|twilio|sendgrid|smtp|charge\(|/send|finalize",
                                rel)


if __name__ == "__main__":
    unittest.main()
