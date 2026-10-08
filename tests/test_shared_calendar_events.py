"""Dependency-free tests for the shared sales calendar event decision.

Pure service + source-wiring facts + the node helper test. Synthetic data only;
nothing here touches a database, provider or customer.
"""
import importlib.util
import os
import subprocess
import unittest
from datetime import date, datetime
from types import SimpleNamespace as NS

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load():
    path = os.path.join(_ROOT, "app", "services", "shared_calendar_events.py")
    spec = importlib.util.spec_from_file_location("sce_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sce = _load()


def src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8-sig") as f:
        return f.read()


BRAND, OTHER = "brand-1", "brand-2"
SELLER, SELLER2, MGR = "u-seller", "u-seller2", "u-mgr"


def opp(oid="o1", owner=SELLER, brand=BRAND, **kw):
    base = dict(id=oid, owner_user_id=owner, brand_sales_org_id=brand,
                company_name="Acme", timezone="America/Chicago", status="open",
                next_action=None, next_action_due_at=None, updated_at=None)
    base.update(kw)
    return NS(**base)


def appt(aid="a1", o=None, brand=BRAND, status="scheduled",
         starts=datetime(2026, 10, 6, 15, 0), ends=datetime(2026, 10, 6, 16, 0), **kw):
    base = dict(id=aid, brand_sales_org_id=brand, title="Demo", starts_at=starts,
                ends_at=ends, timezone="America/Chicago", status=status,
                created_at=None, meeting_type="Demo", location=None)
    base.update(kw)
    return NS(**base)


START, END = sce.range_bounds(date(2026, 10, 5), date(2026, 10, 11), "America/Chicago")
OK = {"appointments": {"status": "ok", "count": 0}}


def run(cands, viewer=SELLER, manager=False, brand=BRAND, status=OK, **kw):
    return sce.decide(viewer_id=viewer, brand_id=brand, is_manager=manager,
                      candidates=cands, source_status=status,
                      start_utc=START, end_utc=END, **kw)


class Scope(unittest.TestCase):
    def setUp(self):
        mine, theirs = opp("o1", SELLER), opp("o2", SELLER2)
        self.cands = [
            sce.appointment_event(appt("a1"), mine, "S1", [SELLER]),
            sce.appointment_event(appt("a2"), theirs, "S2", [SELLER2]),
            sce.task_event(opp("o3", SELLER2, next_action="Call",
                               next_action_due_at=datetime(2026, 10, 7, 15, 0)),
                           "America/Chicago", "S2"),
        ]

    def test_seller_sees_only_own(self):
        ids = [e["id"] for e in run(self.cands)["events"]]
        self.assertEqual(ids, ["appointment:a1"])

    def test_participant_sees_appointment_not_owned(self):
        c = [sce.appointment_event(appt("a2"), opp("o2", SELLER2), "S2", [SELLER2, SELLER])]
        self.assertEqual(len(run(c)["events"]), 1)

    def test_manager_sees_whole_brand(self):
        out = run(self.cands, viewer=MGR, manager=True)
        self.assertEqual(len(out["events"]), 3)

    def test_cross_brand_never_leaks_even_for_manager_or_god(self):
        c = [sce.appointment_event(appt("a9", brand=OTHER), opp("o9", brand=OTHER), "X", [])]
        self.assertEqual(run(c, viewer=MGR, manager=True)["total"], 0)

    def test_seller_other_brand_owned_row_dropped(self):
        c = [sce.appointment_event(appt("a9", brand=OTHER), opp("o9", SELLER, OTHER), "X", [SELLER])]
        self.assertEqual(run(c)["total"], 0)


class Contract(unittest.TestCase):
    def test_missing_facts_unavailable_not_invented(self):
        e = sce.appointment_event(appt(title=None), None, None, [])
        self.assertIsNone(e["owner"])
        self.assertIsNone(e["title"])
        self.assertIsNone(e["company"])
        for f in ("owner", "title", "company", "opportunity_id"):
            self.assertIn(f, e["unavailable"])

    def test_stable_ids_and_evidence(self):
        e = sce.appointment_event(appt("abc"), opp(), "S", [])
        self.assertEqual(e["id"], "appointment:abc")
        self.assertEqual(e["evidence"]["source_table"], "sales_appointments")
        self.assertEqual(e["evidence"]["source_id"], "abc")

    def test_no_prospect_contact_details_exposed(self):
        a = appt(prospect_email="p@example.test", prospect_phone="555")
        flat = repr(sce.appointment_event(a, opp(), "S", []))
        self.assertNotIn("p@example.test", flat)
        self.assertNotIn("555", flat)

    def test_timezone_and_utc_preserved(self):
        e = sce.appointment_event(appt(), opp(), "S", [])
        self.assertEqual(e["starts_at"], "2026-10-06T15:00:00Z")
        self.assertEqual(e["timezone"], "America/Chicago")
        self.assertEqual(e["starts_at_local"], "2026-10-06T10:00:00")

    def test_task_without_due_is_unscheduled_with_blocker(self):
        t = sce.task_event(opp(next_action="Follow up"), "America/Chicago")
        self.assertEqual(t["bucket"], sce.BUCKET_UNSCHEDULED)
        self.assertTrue(t["blockers"])
        self.assertIsNone(t["starts_at"])
        out = run([t])
        self.assertEqual([e["id"] for e in out["unscheduled"]], ["task:o1"])
        self.assertEqual(out["events"], [])

    def test_task_never_reports_completion(self):
        t = sce.task_event(opp(next_action="x", next_action_due_at=datetime(2026, 10, 7, 15)),
                           "America/Chicago")
        self.assertIn("completion", t["unavailable"])
        self.assertEqual(t["bucket"], sce.BUCKET_TASK)

    def test_task_timezone_falls_back_to_brand_and_says_so(self):
        t = sce.task_event(opp(timezone=None, next_action="x",
                               next_action_due_at=datetime(2026, 10, 7, 15)), "America/Denver")
        self.assertEqual((t["timezone"], t["timezone_source"]), ("America/Denver", "brand_default"))


class Boundaries(unittest.TestCase):
    def test_late_evening_local_event_keeps_local_date(self):
        # 2026-10-07 03:30Z is 22:30 on the 6th in Chicago (CDT).
        e = sce.appointment_event(appt(starts=datetime(2026, 10, 7, 3, 30),
                                       ends=datetime(2026, 10, 7, 4, 30)), opp(), "S", [])
        self.assertEqual(e["local_date"], "2026-10-06")

    def test_midnight_crossing_has_distinct_end_date(self):
        e = sce.appointment_event(appt(starts=datetime(2026, 10, 7, 4, 30),
                                       ends=datetime(2026, 10, 7, 6, 0)), opp(), "S", [])
        self.assertEqual((e["local_date"], e["local_end_date"]), ("2026-10-06", "2026-10-07"))

    def test_range_is_local_midnight_to_midnight_across_dst(self):
        s, e = sce.range_bounds(date(2026, 11, 1), date(2026, 11, 1), "America/Chicago")
        self.assertEqual((e - s).total_seconds(), 25 * 3600)  # fall-back day
        self.assertEqual(s, datetime(2026, 11, 1, 5, 0))

    def test_event_outside_range_excluded_edges_inclusive_start(self):
        before = sce.appointment_event(
            appt("b", starts=datetime(2026, 10, 5, 4, 0), ends=datetime(2026, 10, 5, 4, 59)),
            opp(), "S", [])
        at_start = sce.appointment_event(
            appt("s", starts=START, ends=datetime(2026, 10, 5, 6, 0)), opp(), "S", [])
        at_end = sce.appointment_event(
            appt("e", starts=END, ends=datetime(2026, 10, 12, 6, 0)), opp(), "S", [])
        ids = [x["id"] for x in run([before, at_start, at_end])["events"]]
        self.assertEqual(ids, ["appointment:s"])

    def test_all_day_derived_only_from_local_midnight_due(self):
        midnight = sce.task_event(opp(next_action="x",
                                      next_action_due_at=datetime(2026, 10, 7, 5, 0)), "America/Chicago")
        timed = sce.task_event(opp(next_action="x",
                                   next_action_due_at=datetime(2026, 10, 7, 15, 0)), "America/Chicago")
        self.assertTrue(midnight["all_day"])
        self.assertFalse(timed["all_day"])


class OrderingAndDedupe(unittest.TestCase):
    def test_total_deterministic_order_independent_of_input_order(self):
        same = datetime(2026, 10, 6, 15, 0)
        evs = [sce.appointment_event(appt("a%d" % i, starts=same), opp(), "S", [SELLER])
               for i in (3, 1, 2)]
        evs.append(sce.task_event(opp("o9", next_action="t", next_action_due_at=same),
                                  "America/Chicago"))
        a = [e["id"] for e in run(evs)["events"]]
        b = [e["id"] for e in run(list(reversed(evs)))["events"]]
        self.assertEqual(a, b)
        self.assertEqual(a, ["appointment:a1", "appointment:a2", "appointment:a3", "task:o9"])

    def test_same_source_row_twice_kept_once(self):
        e = sce.appointment_event(appt("a1"), opp(), "S", [SELLER])
        out = run([e, dict(e)])
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["suppressed_duplicates"][0]["reason"], "same_source_row")

    def test_task_at_appointment_instant_suppressed(self):
        o = opp(next_action="Demo", next_action_due_at=datetime(2026, 10, 6, 15, 0))
        out = run([sce.appointment_event(appt(), o, "S", [SELLER]),
                   sce.task_event(o, "America/Chicago")])
        self.assertEqual([e["id"] for e in out["events"]], ["appointment:a1"])
        self.assertEqual(out["suppressed_duplicates"][0]["reason"], "task_matches_appointment")

    def test_booking_activity_mirrors_appointment_but_other_activity_stays(self):
        o = opp()
        booked = sce.activity_event(
            NS(id="e1", event_type="appointment_booked", summary="Booked",
               occurred_at=datetime(2026, 10, 6, 14)), o, "America/Chicago")
        note = sce.activity_event(
            NS(id="e2", event_type="stage_changed", summary="Moved",
               occurred_at=datetime(2026, 10, 6, 14)), o, "America/Chicago")
        out = run([sce.appointment_event(appt(), o, "S", [SELLER]), booked, note])
        ids = {e["id"] for e in out["events"]}
        self.assertEqual(ids, {"appointment:a1", "activity:e2"})

    def test_cancelled_and_completed_separated_from_scheduled(self):
        o = opp()
        c = [sce.appointment_event(appt("c", status="cancelled"), o, "S", [SELLER]),
             sce.appointment_event(appt("d", status="completed"), o, "S", [SELLER]),
             sce.appointment_event(appt("s", status="scheduled"), o, "S", [SELLER])]
        buckets = {e["id"]: e["bucket"] for e in run(c)["events"]}
        self.assertEqual(buckets, {"appointment:c": "cancelled", "appointment:d": "completed",
                                   "appointment:s": "scheduled"})
        only = run(c, buckets={"scheduled"})["events"]
        self.assertEqual([e["id"] for e in only], ["appointment:s"])


class FiltersAndPartial(unittest.TestCase):
    def test_facets_survive_filters(self):
        o = opp()
        c = [sce.appointment_event(appt(), o, "S", [SELLER]),
             sce.task_event(opp("o2", next_action="t",
                                next_action_due_at=datetime(2026, 10, 7, 15)), "America/Chicago")]
        out = run(c, types={"task"})
        self.assertEqual([e["type"] for e in out["events"]], ["task"])
        self.assertEqual(out["facets"]["types"], ["appointment", "task"])

    def test_owner_filter(self):
        c = [sce.appointment_event(appt("a1"), opp("o1", SELLER), "S1", [SELLER]),
             sce.appointment_event(appt("a2"), opp("o2", SELLER2), "S2", [SELLER2])]
        out = run(c, viewer=MGR, manager=True, owner_ids={SELLER2})
        self.assertEqual([e["id"] for e in out["events"]], ["appointment:a2"])
        self.assertEqual(len(out["facets"]["owners"]), 2)

    def test_unavailable_source_is_partial_not_empty_success(self):
        st = {"appointments": {"status": "ok", "count": 0},
              "tasks": {"status": "unavailable", "count": 0, "detail": "OperationalError"}}
        out = run([], status=st)
        self.assertTrue(out["partial"])
        self.assertEqual(out["unavailable_sources"], ["tasks"])

    def test_truncation_flagged(self):
        o = opp()
        c = [sce.appointment_event(appt("a%03d" % i, starts=datetime(2026, 10, 6, 15, i % 59)),
                                   o, "S", [SELLER]) for i in range(7)]
        out = run(c, cap=5)
        self.assertTrue(out["truncated"])
        self.assertEqual(len(out["events"]), 5)

    def test_read_only_flag(self):
        self.assertTrue(run([])["read_only"])


class Wiring(unittest.TestCase):
    def test_route_is_get_only_and_scoped(self):
        r = src("app/routers/sales_scheduling_router.py")
        self.assertIn('@router.get("/calendar/events")', r)
        body = r[r.index('def calendar_events('):r.index('@router.get("/calendar/sync-status")')]
        for forbidden in ("db.add(", "db.commit(", "send_", "_push_", "extbusy.",
                          "apsync.", "apinvite.", "requests.", "httpx"):
            self.assertNotIn(forbidden, body, forbidden)
        self.assertIn("require_sales_member", body)
        self.assertIn("Opportunity.owner_user_id == user.id", body)
        self.assertIn("brand_sales_org_id == org.id", body)

    def test_service_has_no_outbound_dependency(self):
        s = src("app/services/shared_calendar_events.py")
        for forbidden in ("import requests", "httpx", "smtp", "twilio", "sqlalchemy", "fastapi"):
            self.assertNotIn(forbidden, s.lower())

    def test_web_and_mobile_use_same_endpoint(self):
        self.assertIn("/sales/calendar/events", src("frontend/src/pages/sales/TeamCalendar.jsx"))
        self.assertIn("/sales/calendar/events", src("mobile/src/api/endpoints.ts"))
        m = src("mobile/app/(sales)/calendar.tsx")
        self.assertIn("calendarEvents(", m)
        self.assertIn("unavailable_sources", m)

    def test_web_has_stale_guard_and_partial_state(self):
        t = src("frontend/src/pages/sales/TeamCalendar.jsx")
        self.assertIn("feedGuard.current.isCurrent(token)", t)
        self.assertIn("Partial data", t)
        self.assertIn("mergeOptions(", t)

    def test_no_outbound_actions_in_feed_ui(self):
        t = src("frontend/src/pages/sales/TeamCalendar.jsx")
        card = t[t.index('title="TASKS & ACTIVITY"'):t.index('title="NEEDS ATTENTION"')]
        for forbidden in ("api.post", "api.put", "api.delete", "Send", "Invite", "Remind"):
            self.assertNotIn(forbidden, card)


class NodeHelpers(unittest.TestCase):
    def test_helpers_execute(self):
        script = os.path.join(_ROOT, "tests", "frontend", "sharedEvents.test.mjs")
        try:
            p = subprocess.run(["node", script], capture_output=True, text=True,
                               cwd=_ROOT, timeout=120)
        except (FileNotFoundError, OSError):
            self.skipTest("node unavailable")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))


if __name__ == "__main__":
    unittest.main()
