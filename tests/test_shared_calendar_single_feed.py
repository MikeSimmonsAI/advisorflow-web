"""One authoritative calendar read: every visible view uses /sales/calendar/events.

Dependency-free. Source facts for the wiring, the real Python decision for
timezone/DST/scope/dedupe, and the production JS helpers under node (skips only
if node is missing) for grid-vs-agenda agreement. Synthetic data only.
"""
import json
import os
import re
import subprocess
import unittest
from datetime import date, datetime

from test_shared_calendar_events import (  # noqa: E402
    BRAND, SELLER, SELLER2, MGR, appt, opp, sce, src,
)

_ROOT = os.path.join(os.path.dirname(__file__), "..")
TC = "frontend/src/pages/sales/TeamCalendar.jsx"
OK = {"appointments": {"status": "ok", "count": 0}}


def feed_for(cands, d1, d2, tz="America/Chicago", viewer=MGR, manager=True,
             status=OK, **kw):
    s, e = sce.range_bounds(d1, d2, tz)
    out = sce.decide(viewer_id=viewer, brand_id=BRAND, is_manager=manager,
                     candidates=cands, source_status=status,
                     start_utc=s, end_utc=e, **kw)
    for ev in out["events"]:
        if ev["type"] == "appointment":   # the router attaches the card detail
            ev["appointment"] = {"id": ev["source_id"], "title": ev["title"],
                                 "participants": [], "outcome_state": {}}
    return out


def bridge(feed, **extra):
    script = os.path.join(_ROOT, "tests", "frontend", "feedBridge.mjs")
    payload = json.dumps(dict(feed=feed, **extra), default=str)
    try:
        p = subprocess.run(["node", script], input=payload, capture_output=True,
                           text=True, cwd=_ROOT, timeout=120)
    except (FileNotFoundError, OSError):
        raise unittest.SkipTest("node unavailable")
    assert p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    return json.loads(p.stdout)


class EveryViewReadsTheFeed(unittest.TestCase):
    def setUp(self):
        self.t = src(TC)

    def test_only_feed_endpoint_supplies_appointments(self):
        self.assertIn("/sales/calendar/events", self.t)
        self.assertIn("appointmentsFromFeed(feed)", self.t)
        for dead in ("data?.appointments", "data.appointments", "data?.agenda_today",
                     "data?.attention", "data?.upcoming", "data?.locations",
                     "data?.truncated", "meeting_type_ids", "'location'"):
            self.assertNotIn(dead, self.t, dead)

    def test_one_events_request_site_and_it_is_unfiltered(self):
        self.assertEqual(self.t.count("/sales/calendar/events?"), 1)
        self.assertIn("eventQuery({ from, to })", self.t)

    def test_every_grid_gets_the_same_derived_rows(self):
        for view in ("<TimeGrid days={days} appts={appts}",
                     "<MonthGrid anchor={anchor} appts={appts}",
                     "<AgendaList appts={appts}", "appts={appts}\n"):
            self.assertIn(view, self.t, view)
        # grids are gated on the feed, not on the legacy view payload
        for g in ("{feed && (view === 'day' || view === 'week')",
                  "{feed && view === 'month'", "{feed && view === 'agenda'"):
            self.assertIn(g, self.t, g)

    def test_state_and_stale_guards(self):
        self.assertIn("feedGuard.current.isCurrent(token)", self.t)
        self.assertIn("setFeed(null)", self.t)            # error never leaves old range
        self.assertIn("<ErrorBar error={feedError} onRetry={loadFeed} />", self.t)
        self.assertIn("Promise.all([load(), loadFeed()])", self.t)  # after a change
        self.assertIn("feed?.truncated", self.t)

    def test_legacy_view_cannot_feed_the_grid(self):
        # the legacy loader no longer carries appointment filters
        body = self.t[self.t.index("const load = useCallback"):
                      self.t.index("useEffect(() => { load() }")]
        self.assertNotIn("meeting_type_ids", body)
        self.assertNotIn("setAppts", self.t)
        r = src("app/routers/sales_scheduling_router.py")
        self.assertIn("APPOINTMENT READS ARE RETIRED HERE FOR THE UI", r)

    def test_mobile_agenda_reads_only_the_feed(self):
        m = src("mobile/app/(sales)/calendar.tsx")
        self.assertIn("calendarEvents(", m)
        self.assertNotIn("scheduling.appointments(", m)
        self.assertIn("local_date", m)
        self.assertIn("unavailable_sources", m)

    def test_router_attaches_read_only_detail_and_clock(self):
        r = src("app/routers/sales_scheduling_router.py")
        body = r[r.index("def calendar_events("):r.index('@router.get("/calendar/sync-status")')]
        self.assertIn('ev["appointment"] = _appt_out(db, a, user)', body)
        self.assertIn('"today_local"', body)
        self.assertIn('"now_utc"', body)

    def test_no_outbound_action_added(self):
        r = src("app/routers/sales_scheduling_router.py")
        body = r[r.index("def calendar_events("):r.index('@router.get("/calendar/sync-status")')]
        for forbidden in ("db.add(", "db.commit(", "send_", "requests.", "httpx",
                          "apinvite.", "apsync.", "twilio"):
            self.assertNotIn(forbidden, body, forbidden)
        helpers = src("frontend/src/pages/sales/sharedEvents.js")
        for forbidden in ("api.post", "fetch(", "XMLHttpRequest", "sendBeacon"):
            self.assertNotIn(forbidden, helpers, forbidden)
        m = src("mobile/app/(sales)/calendar.tsx")
        for forbidden in (".post(", "scheduling.create", "scheduling.cancel", "scheduling.confirm"):
            self.assertNotIn(forbidden, m, forbidden)


class RangeTimezoneAndPlacement(unittest.TestCase):
    def test_month_navigation_requests_bounded_range(self):
        # a six-week month grid is within the server cap; 63+ days is not asked for
        self.assertLessEqual(42, sce.MAX_RANGE_DAYS)
        s, e = sce.range_bounds(date(2026, 9, 28), date(2026, 11, 8), "America/Chicago")
        self.assertEqual((e - s).days, 42)  # 41 days + DST fall-back day handled below

    def test_dst_days_are_not_24_hours(self):
        s, e = sce.range_bounds(date(2026, 11, 1), date(2026, 11, 1), "America/Chicago")
        self.assertEqual((e - s).total_seconds(), 25 * 3600)       # fall back
        s, e = sce.range_bounds(date(2026, 3, 8), date(2026, 3, 8), "America/Chicago")
        self.assertEqual((e - s).total_seconds(), 23 * 3600)       # spring forward

    def test_dst_boundary_event_lands_in_range_and_correct_local_day(self):
        o = opp("o1", SELLER)
        # 2026-11-01 06:30Z = 01:30 CST (second pass) -> still Nov 1 local
        a = sce.appointment_event(appt("a1", starts=datetime(2026, 11, 1, 6, 30),
                                       ends=datetime(2026, 11, 1, 7, 30)), o, "S", [SELLER])
        out = feed_for([a], date(2026, 11, 1), date(2026, 11, 1))
        self.assertEqual([e["local_date"] for e in out["events"]], ["2026-11-01"])
        none = feed_for([a], date(2026, 10, 31), date(2026, 10, 31))
        self.assertEqual(none["events"], [])

    def test_cross_midnight_event_appears_once_in_range(self):
        o = opp("o1", SELLER)
        a = sce.appointment_event(appt("a1", starts=datetime(2026, 11, 1, 4, 30),
                                       ends=datetime(2026, 11, 1, 6, 30)), o, "S", [SELLER])
        out = feed_for([a], date(2026, 10, 25), date(2026, 11, 7))
        self.assertEqual(len(out["events"]), 1)
        self.assertEqual(out["events"][0]["local_date"], "2026-10-31")
        self.assertEqual(out["events"][0]["local_end_date"], "2026-11-01")

    def test_month_boundary_neighbouring_ranges_never_double_or_drop(self):
        o = opp("o1", SELLER)
        # CDT: Nov 1 00:00 local = 05:00Z
        before = sce.appointment_event(appt("b", starts=datetime(2026, 11, 1, 4, 30),
                                           ends=datetime(2026, 11, 1, 4, 50)), o, "S", [SELLER])
        after = sce.appointment_event(appt("c", starts=datetime(2026, 11, 1, 5, 10),
                                          ends=datetime(2026, 11, 1, 5, 30)), o, "S", [SELLER])
        oct_ = feed_for([before, after], date(2026, 10, 1), date(2026, 10, 31))
        nov = feed_for([before, after], date(2026, 11, 1), date(2026, 11, 30))
        self.assertEqual([e["source_id"] for e in oct_["events"]], ["b"])
        self.assertEqual([e["source_id"] for e in nov["events"]], ["c"])

    def test_event_spanning_the_boundary_is_once_per_range_on_its_start_day(self):
        o = opp("o1", SELLER)
        span = sce.appointment_event(appt("s", starts=datetime(2026, 11, 1, 4, 59),
                                          ends=datetime(2026, 11, 1, 5, 20)), o, "S", [SELLER])
        for d1, d2 in ((date(2026, 10, 1), date(2026, 10, 31)),
                       (date(2026, 11, 1), date(2026, 11, 30))):
            out = feed_for([span], d1, d2)
            self.assertEqual(len(out["events"]), 1)
            self.assertEqual(out["events"][0]["local_date"], "2026-10-31")

    def test_cancelled_and_completed_keep_buckets(self):
        o = opp("o1", SELLER)
        c = [sce.appointment_event(appt("c1", status="cancelled"), o, "S", [SELLER]),
             sce.appointment_event(appt("c2", status="completed"), o, "S", [SELLER])]
        out = feed_for(c, date(2026, 10, 5), date(2026, 10, 11))
        self.assertEqual({e["id"]: e["bucket"] for e in out["events"]},
                         {"appointment:c1": "cancelled", "appointment:c2": "completed"})


class ScopeAndPartial(unittest.TestCase):
    def setUp(self):
        self.cands = [
            sce.appointment_event(appt("a1"), opp("o1", SELLER), "S", [SELLER]),
            sce.appointment_event(appt("a2"), opp("o2", SELLER2), "S2", [SELLER2]),
            sce.appointment_event(appt("a3", brand="other"), opp("o3", SELLER, brand="other"),
                                  "S", [SELLER]),
        ]

    def test_seller_and_manager_scope_apply_to_one_feed(self):
        s = feed_for(self.cands, date(2026, 10, 5), date(2026, 10, 11),
                     viewer=SELLER, manager=False)
        m = feed_for(self.cands, date(2026, 10, 5), date(2026, 10, 11))
        self.assertEqual([e["source_id"] for e in s["events"]], ["a1"])
        self.assertEqual({e["source_id"] for e in m["events"]}, {"a1", "a2"})  # no cross-brand

    def test_facets_survive_filters(self):
        full = feed_for(self.cands, date(2026, 10, 5), date(2026, 10, 11))
        flt = feed_for(self.cands, date(2026, 10, 5), date(2026, 10, 11),
                       owner_ids={SELLER}, types={"appointment"})
        self.assertEqual(full["facets"]["owners"], flt["facets"]["owners"])
        self.assertEqual(len(flt["events"]), 1)

    def test_partial_source_flags_appointments_missing(self):
        out = feed_for(self.cands[:1], date(2026, 10, 5), date(2026, 10, 11),
                       status={"appointments": {"status": "unavailable", "count": 0},
                               "tasks": {"status": "ok", "count": 0}})
        self.assertTrue(out["partial"])
        self.assertEqual(out["unavailable_sources"], ["appointments"])

    def test_duplicate_source_rows_render_once(self):
        dup = [self.cands[0], self.cands[0]]
        out = feed_for(dup, date(2026, 10, 5), date(2026, 10, 11))
        self.assertEqual(len(out["events"]), 1)
        self.assertEqual(out["suppressed_duplicates"][0]["reason"], "same_source_row")


class GridAgendaAgreement(unittest.TestCase):
    def test_grid_and_agenda_share_ids_status_buckets(self):
        o = opp("o1", SELLER)
        cands = [
            sce.appointment_event(appt("a1"), o, "S", [SELLER]),
            sce.appointment_event(appt("a2", status="cancelled",
                                       starts=datetime(2026, 10, 7, 15, 0),
                                       ends=datetime(2026, 10, 7, 16, 0)), o, "S", [SELLER]),
            sce.appointment_event(appt("a3", status="completed",
                                       starts=datetime(2026, 10, 8, 3, 30),
                                       ends=datetime(2026, 10, 8, 4, 30)), o, "S", [SELLER]),
            sce.task_event(opp("o9", SELLER, next_action="Call",
                               next_action_due_at=datetime(2026, 10, 9, 15, 0)),
                           "America/Chicago", "S"),
        ]
        feed = feed_for(cands, date(2026, 10, 5), date(2026, 10, 11))
        got = bridge(feed, includeCancelled=True, todayLocal="2026-10-06",
                     nowUtc="2026-10-06T12:00:00Z")
        grid_ids = [g["event_id"] for g in got["grid"]]
        appt_events = [e for e in feed["events"] if e["type"] == "appointment"]
        self.assertEqual(grid_ids, [e["id"] for e in appt_events])
        by_id = {e["id"]: e for e in appt_events}
        for g in got["grid"]:
            e = by_id[g["event_id"]]
            self.assertEqual((g["bucket"], g["status"], g["local_date"]),
                             (e["bucket"], e["status"], e["local_date"]))
        # the agenda groups the same ids under the same local dates
        agenda = {d: ids for d, ids in got["agendaDays"]}
        for day, ids in got["days"].items():
            self.assertTrue(set(ids) <= set(agenda[day]), day)
        # 03:30Z on Oct 8 is 22:30 CDT Oct 7 - the same day in both views
        self.assertEqual(by_id["appointment:a3"]["local_date"], "2026-10-07")
        self.assertIn("appointment:a3", got["days"]["2026-10-07"])
        # default grid hides cancelled; the agenda feed still lists the id once
        default = bridge(feed, todayLocal="2026-10-06", nowUtc="2026-10-06T12:00:00Z")
        self.assertNotIn("appointment:a2", [g["event_id"] for g in default["grid"]])
        self.assertEqual(sum(ids.count("appointment:a2") for _, ids in default["agendaDays"]), 1)
        self.assertEqual(got["today"], ["appointment:a1"])


class NodeSuite(unittest.TestCase):
    def test_single_feed_helpers_execute(self):
        script = os.path.join(_ROOT, "tests", "frontend", "singleFeed.test.mjs")
        try:
            p = subprocess.run(["node", script], capture_output=True, text=True,
                               cwd=_ROOT, timeout=120)
        except (FileNotFoundError, OSError):
            self.skipTest("node unavailable")
        self.assertEqual(p.returncode, 0, (p.stdout or "") + (p.stderr or ""))


if __name__ == "__main__":
    unittest.main()
