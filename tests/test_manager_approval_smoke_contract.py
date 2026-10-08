"""Executable contract for the manager approval smoke harness (stdlib; no DB/network).

Proves scripts/smoke_manager_workspace.py reads the one versioned queue, decides
only via /decision with expected_version, treats 409/404 as failure, and has no
retired-endpoint, GET-mutation, outbound, provider or payment path.
"""
import ast
import importlib.util
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def load(rel):
    path = os.path.join(ROOT, rel)
    spec = importlib.util.spec_from_file_location(os.path.basename(rel)[:-3], path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


K = load("scripts/manager_approval_smoke_contract.py")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


SMOKE = read("scripts/smoke_manager_workspace.py")
HELPER = read("scripts/manager_approval_smoke_contract.py")


def item(i="r1", version="v1.aaa.bbb"):
    return {"id": i, "kind": "proposal_adjustment", "status": "pending",
            "version": version, "actionable": True, "blocker": None,
            "money": {"requested_adjustment": {"cents": -30000, "display": "-300.00"},
                      "requested_total": {"cents": 469500, "display": "4,695.00"},
                      "live_total": None}}


def queue(items=None, brand="b1", **kw):
    items = [item()] if items is None else items
    q = {"brand_sales_org_id": brand, "authorized": True, "pending": items, "history": [],
         "pending_count": len(items), "actionable_count": len(items), "blocked_count": 0}
    q.update(kw)
    return q


class SmokeSource(unittest.TestCase):
    def test_compiles(self):
        ast.parse(SMOKE)
        ast.parse(HELPER)

    def test_never_references_retired_decide(self):
        for text in (SMOKE, HELPER):
            self.assertNotRegex(text, r"/decide(?!\w)|approvals\"\s*,|manager/approvals\"")

    def test_uses_queue_and_versioned_decision(self):
        self.assertIn("K.QUEUE_PATH", SMOKE)
        self.assertIn("K.decision_request", SMOKE)
        self.assertIn("expected_version", SMOKE)
        self.assertEqual(K.QUEUE_PATH, "/sales/manager/approvals/queue")
        self.assertEqual(K.DECISION_PATH % "x", "/sales/manager/approvals/x/decision")

    def test_every_post_to_approvals_goes_through_helper_path(self):
        for m in re.finditer(r"c\.post\(([^,]+),", SMOKE):
            arg = m.group(1)
            if "approvals" in arg or "dpath" in arg or "DECISION" in arg:
                self.assertRegex(arg, r"dpath|DECISION_PATH", arg)

    def test_stale_and_exact_cents_are_asserted(self):
        self.assertIn("status_code == 409", SMOKE)
        self.assertIn('.get("cents")', SMOKE)

    def test_no_mutation_through_get(self):
        for m in re.finditer(r"c\.get\(([^)]*)\)", SMOKE):
            self.assertNotRegex(m.group(1), r"decision|decide", m.group(1))
        self.assertNotRegex(HELPER, r"requests|urllib|http\.client|socket|subprocess")

    def test_no_outbound_provider_or_payment_path(self):
        pat = r"(?i)\b(stripe|twilio|sendgrid|smtplib|requests\.|httpx\.|urlopen|zoom_client|charge\()"
        self.assertNotRegex(HELPER, pat)
        self.assertNotRegex(SMOKE, pat)
        self.assertIn("sqlite:///", SMOKE)          # temp SQLite only
        self.assertNotRegex(SMOKE, r"postgres(ql)?://")


class ReadQueueFailsClosed(unittest.TestCase):
    def test_good_queue(self):
        self.assertEqual(len(K.read_queue(queue(), "b1")), 1)

    def test_missing_key_unexpected_shape(self):
        q = queue()
        del q["blocked_count"]
        with self.assertRaises(K.ContractError):
            K.read_queue(q, "b1")
        with self.assertRaises(K.ContractError):
            K.read_queue([], "b1")

    def test_unavailable_counts(self):
        for k in ("pending_count", "actionable_count", "blocked_count"):
            with self.assertRaises(K.ContractError):
                K.read_queue(queue(**{k: None}), "b1")

    def test_count_mismatch(self):
        with self.assertRaises(K.ContractError):
            K.read_queue(queue(pending_count=2, actionable_count=2), "b1")

    def test_cross_brand(self):
        with self.assertRaises(K.ContractError):
            K.read_queue(queue(brand="other"), "b1")

    def test_unauthorized(self):
        with self.assertRaises(K.ContractError):
            K.read_queue(queue(authorized=False), "b1")

    def test_missing_or_bad_version(self):
        for v in (None, "", "stale"):
            with self.assertRaises(K.ContractError):
                K.read_queue(queue([item(version=v)]), "b1")
        it = item()
        del it["version"]
        with self.assertRaises(K.ContractError):
            K.read_queue(queue([it]), "b1")

    def test_non_pending_row(self):
        it = item()
        it["status"] = "approved"
        with self.assertRaises(K.ContractError):
            K.read_queue(queue([it]), "b1")

    def test_float_or_wrong_display_cents(self):
        it = item()
        it["money"]["requested_adjustment"]["cents"] = -300.0
        with self.assertRaises(K.ContractError):
            K.read_queue(queue([it]), "b1")
        it = item()
        it["money"]["requested_total"]["display"] = "4695.00"
        with self.assertRaises(K.ContractError):
            K.read_queue(queue([it]), "b1")

    def test_find_item_must_be_exactly_one(self):
        with self.assertRaises(K.ContractError):
            K.find_item([item()], "nope")
        with self.assertRaises(K.ContractError):
            K.find_item([item(), item()], "r1")


class DecisionRequestAndResponse(unittest.TestCase):
    def test_supplies_expected_version_from_row(self):
        path, body = K.decision_request(item(version="v1.x.y"), True, "ok")
        self.assertEqual(path, "/sales/manager/approvals/r1/decision")
        self.assertEqual(body, {"approve": True, "expected_version": "v1.x.y", "note": "ok"})

    def test_refuses_without_version(self):
        it = item()
        it["version"] = ""
        with self.assertRaises(K.ContractError):
            K.decision_request(it, True)

    def test_409_is_stale_not_success(self):
        with self.assertRaisesRegex(K.ContractError, "409 stale"):
            K.read_decision(409, {"detail": "changed"}, True)

    def test_404_and_400_are_not_success(self):
        for code in (400, 403, 404, 410, 500):
            with self.assertRaises(K.ContractError):
                K.read_decision(code, {"ok": True}, True)

    def test_false_success(self):
        for body in (None, {}, {"ok": False}, {"ok": "yes", "status": "approved"}):
            with self.assertRaises(K.ContractError):
                K.read_decision(200, body, True)
        with self.assertRaises(K.ContractError):          # status contradicts the answer
            K.read_decision(200, {"ok": True, "replay": False, "applied": True,
                                  "status": "denied"}, True)

    def test_true_success_and_replay(self):
        self.assertEqual(K.read_decision(
            200, {"ok": True, "replay": False, "applied": True, "status": "approved"}, True),
            "applied")
        self.assertEqual(K.read_decision(
            200, {"ok": True, "replay": True, "status": "denied"}, False), "replay")


if __name__ == "__main__":
    unittest.main()
