"""Stdlib tests for the Control Room launch board. Run: python3 -m unittest tests.test_launch_board"""
import ast
import os
import tempfile
import unittest

from app.services import launch_board as lb

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class Board(unittest.TestCase):
    def setUp(self):
        self.b = {"projects": [], "next_id": 1}

    def _p(self, name="A"):
        return lb.add_project(self.b, name, actor="mike")

    def test_new_project_is_unapproved_backlog_and_auto_priority(self):
        a, c = self._p("A"), self._p("B")
        self.assertEqual((a["lane"], a["approved"]), ("backlog", False))
        self.assertEqual((a["priority"], c["priority"]), (1, 2))

    def test_duplicate_and_blank_names_refused(self):
        self._p("A")
        for n in ("a", " ", ""):
            with self.assertRaises(lb.BoardError):
                lb.add_project(self.b, n)

    def test_cannot_activate_unapproved_and_unapprove_demotes(self):
        p = self._p()
        with self.assertRaises(lb.BoardError):
            lb.set_lane(self.b, p["id"], "active")
        lb.approve(self.b, p["id"], "mike")
        lb.set_lane(self.b, p["id"], "active")
        lb.revoke_approval(self.b, p["id"])
        self.assertEqual(p["lane"], "backlog")

    def test_queue_only_approved_non_archived_in_priority_order(self):
        self._p("A")
        c, d = self._p("B"), self._p("C")
        lb.approve(self.b, c["id"], "mike")
        lb.approve(self.b, d["id"], "mike")
        lb.set_priority(self.b, d["id"], 1)
        lb.set_priority(self.b, c["id"], 2)
        self.assertEqual([q["name"] for q in lb.view(self.b)["queue"]], ["C", "B"])
        lb.set_lane(self.b, d["id"], "archived")
        self.assertEqual([q["name"] for q in lb.view(self.b)["queue"]], ["B"])

    def test_approval_needs_named_approver(self):
        p = self._p()
        with self.assertRaises(lb.BoardError):
            lb.approve(self.b, p["id"], " ")

    def test_archive_preserves_history(self):
        p = self._p()
        lb.set_lane(self.b, p["id"], "archived", "mike")
        v = lb.view(self.b)
        self.assertEqual(v["summary"]["archived"], 1)
        self.assertEqual([h["action"] for h in v["lanes"]["archived"][0]["history"]], ["created", "lane"])

    def test_priority_validation(self):
        p = self._p()
        for bad in (0, -1, True, "1", None):
            with self.assertRaises(lb.BoardError):
                lb.set_priority(self.b, p["id"], bad)

    def test_evidence_requires_ref_and_valid_values(self):
        p = self._p()
        with self.assertRaises(lb.BoardError):
            lb.record_evidence(self.b, p["id"], "test", "verified", "")
        with self.assertRaises(lb.BoardError):
            lb.record_evidence(self.b, p["id"], "percent", "verified", "x")
        with self.assertRaises(lb.BoardError):
            lb.record_evidence(self.b, p["id"], "test", "99%", "x")

    def test_task_completion_is_not_product_completion(self):
        p = self._p()
        lb.complete_task(self.b, p["id"], "slice 1")
        r = lb.view(self.b)["lanes"]["backlog"][0]
        self.assertEqual((r["tasks_completed"], r["product_state"], r["product_complete"]),
                         (1, "in_progress", False))
        self.assertEqual(r["last_completed"]["summary"], "slice 1")

    def test_product_complete_needs_all_evidence_and_human_mark(self):
        p = self._p()
        for k in lb.EVIDENCE_KINDS[:3]:
            lb.record_evidence(self.b, p["id"], k, "verified", "ref")
        with self.assertRaises(lb.BoardError):
            lb.mark_product_complete(self.b, p["id"], True)
        lb.record_evidence(self.b, p["id"], "verification", "claimed", "ref")
        with self.assertRaises(lb.BoardError):
            lb.mark_product_complete(self.b, p["id"], True)
        lb.record_evidence(self.b, p["id"], "verification", "verified", "ref")
        self.assertNotEqual(lb.product_state(p), "complete")  # not yet marked by a human
        lb.mark_product_complete(self.b, p["id"], True)
        self.assertEqual(lb.product_state(p), "complete")
        lb.record_evidence(self.b, p["id"], "test", "none")   # evidence withdrawn
        self.assertNotEqual(lb.product_state(p), "complete")

    def test_working_status_separate_and_never_guessed(self):
        p = self._p("A")
        lb.complete_task(self.b, p["id"], "done")
        r = lb.view(self.b)["lanes"]["backlog"][0]
        self.assertEqual(r["working_status"], "no live evidence")
        r = lb.view(self.b, {"A": "Working"})["lanes"]["backlog"][0]
        self.assertEqual((r["working_status"], r["last_completed"]["summary"]), ("Working", "done"))

    def test_no_percentage_fields(self):
        self._p()
        self.assertNotIn("percent", repr(lb.view(self.b)).lower())

    def test_persistence_and_refusal_saves_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "b.json")
            pid = lb.mutate(lambda b: lb.add_project(b, "A")["id"], path)

            def bad(b):
                lb.set_priority(b, pid, 5)
                lb.set_lane(b, pid, "active")   # refused: unapproved
            with self.assertRaises(lb.BoardError):
                lb.mutate(bad, path)
            p = lb.load(path)["projects"][0]
            self.assertEqual((p["priority"], p["lane"]), (1, "backlog"))

    def test_corrupt_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "b.json")
            with open(path, "w") as f:
                f.write("{not json")
            with self.assertRaises(lb.BoardError):
                lb.load(path)
            with self.assertRaises(lb.BoardError):
                lb.mutate(lambda b: lb.add_project(b, "A"), path)
            with open(path) as f:
                self.assertEqual(f.read(), "{not json")


class RouterContract(unittest.TestCase):
    def test_every_route_god_guarded_and_wired(self):
        with open(os.path.join(ROOT, "app", "routers", "god_launch_board_router.py")) as f:
            src = f.read()
        routes = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)
                  and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                          and isinstance(d.func.value, ast.Name) and d.func.value.id == "router"
                          for d in n.decorator_list)]
        self.assertEqual(len(routes), 3)
        for fn in routes:
            self.assertIn("require_god", ast.dump(fn.args), fn.name)
        with open(os.path.join(ROOT, "app", "main.py")) as f:
            self.assertIn("god_launch_board_router", f.read())


if __name__ == "__main__":
    unittest.main()
