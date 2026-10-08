"""Dependency-free contract: shared calendar ordering, truncation, mobile state.

Executes the production JS helpers under node (skips only if node is missing)
and asserts the backend/frontend wiring as source facts. Synthetic only.
"""
import os
import subprocess
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8-sig") as f:
        return f.read()


class Behaviour(unittest.TestCase):
    def test_helpers_execute(self):
        script = os.path.join(_ROOT, "tests", "frontend", "calendarState.test.mjs")
        try:
            p = subprocess.run(["node", script], capture_output=True, text=True,
                               cwd=_ROOT, timeout=120)
        except (FileNotFoundError, OSError):
            self.skipTest("node unavailable")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))


class Wiring(unittest.TestCase):
    def setUp(self):
        self.r = _src("app/routers/sales_scheduling_router.py")

    def test_every_calendar_query_uses_total_order_and_cap_probe(self):
        self.assertNotIn(".order_by(SalesAppointment.starts_at.asc()).limit(500)", self.r)
        self.assertEqual(self.r.count("= _capped_rows(q)"), 2)
        self.assertIn("SalesAppointment.starts_at.asc(), SalesAppointment.id.asc()", self.r)
        self.assertIn("limit(CALENDAR_EVENT_CAP + 1)", self.r)

    def test_truncation_reported_by_both_endpoints(self):
        self.assertEqual(self.r.count('"truncated": truncated'), 2)

    def test_scope_checks_unchanged(self):
        self.assertIn("Only a sales manager can view the team schedule.", self.r)
        self.assertIn("_visible_appointments(db, user, org)", self.r)

    def test_ui_wiring(self):
        s = _src("frontend/src/pages/sales/TeamCalendar.jsx")
        self.assertIn("initialViewState(", s)
        self.assertIn("saveViewState(view, anchor)", s)
        self.assertIn("data?.truncated", s)
        self.assertIn("sort(compareAppts)", s)


if __name__ == "__main__":
    unittest.main()
