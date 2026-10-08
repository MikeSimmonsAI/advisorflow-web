"""GET /sales/calendar/view carries NO event history; /calendar/events is the only feed.

Dependency-free (no fastapi/DB). Source facts about the router and every
consumer in the repo, plus the real Python decision for the guarantees the
feed keeps (scope, bounds, DST, dedupe, partial). Synthetic data only.
"""
import os
import unittest
from datetime import date, datetime

from test_shared_calendar_events import MGR, SELLER, SELLER2, appt, opp, sce, src
from test_shared_calendar_single_feed import feed_for

R = "app/routers/sales_scheduling_router.py"
TC = "frontend/src/pages/sales/TeamCalendar.jsx"
RETIRED = ("appointments", "agenda_today", "attention", "upcoming",
           "locations", "total", "truncated", "limit")
ROOT = os.path.join(os.path.dirname(__file__), "..")
EVENTS_END = '@router.get("/calendar/sync-status")'


def _body(text, start, end):
    return text[text.index(start):text.index(end)]


class ViewHasNoEventHistory(unittest.TestCase):
    def setUp(self):
        full = _body(src(R), "def calendar_view(", '@router.get("/calendar/events")')
        # Code only: the docstring names the retired keys on purpose.
        head, _doc, rest = full.split('"""', 2)
        self.view = head + rest

    def test_no_appointment_query_or_normalisation(self):
        for dead in ("_visible_appointments", "_capped_rows", "_appt_out",
                     "APPT_CANCELLED", "CONF_PENDING", "agenda", "upcoming",
                     "include_cancelled", "meeting_type_ids", "location"):
            self.assertNotIn(dead, self.view, dead)
        # The only appointment table use left is the roster "in a meeting" status.
        self.assertEqual(self.view.count("SalesAppointment"), 4)   # the one join
        self.assertIn("AppointmentParticipant.is_blocking", self.view)

    def test_response_has_no_retired_keys(self):
        ret = self.view[self.view.rindex("    return {"):]
        for k in RETIRED:
            self.assertNotIn('"%s":' % k, ret, k)
        for keep in ("people", "meeting_types", "range", "sync_status",
                     "external_visibility", "external_included"):
            self.assertIn('"%s"' % keep, ret, keep)

    def test_no_outbound_action_in_view(self):
        for forbidden in ("send_", "requests.", "httpx", "apinvite.", "twilio", "db.add("):
            self.assertNotIn(forbidden, self.view, forbidden)

    def test_events_route_is_the_sole_history_source(self):
        ev = _body(src(R), "def calendar_events(", EVENTS_END)
        for needed in ("appointments", "activities"):
            self.assertIn('status["%s"]' % needed, ev)
        self.assertIn("_appt_out(db, a, user)", ev)
        self.assertIn("date_to is before date_from", ev)


class NoConsumerReadsRetiredKeys(unittest.TestCase):
    def test_only_the_web_roster_loader_calls_view(self):
        skip = {"node_modules", ".git", "dist", "Claude outputs", "handoff", "docs",
                "audit", "review_artifacts", "tests"}
        hits = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in skip]
            for f in files:
                if not f.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".py", ".ps1")):
                    continue
                path = os.path.join(base, f)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        t = fh.read()
                except OSError:
                    continue
                if "/calendar/view" in t and not path.endswith("sales_scheduling_router.py"):
                    hits.append(os.path.relpath(path, ROOT).replace(os.sep, "/"))
        self.assertEqual(hits, [TC])

    def test_web_view_request_asks_only_for_roster_params(self):
        t = src(TC)
        body = t[t.index("const load = useCallback"):t.index("useEffect(() => { load() }")]
        for dead in ("meeting_type_ids", "include_cancelled", "location"):
            self.assertNotIn(dead, body, dead)
        for k in RETIRED:
            for pre in ("data?.", "data.", "r.", "r?."):
                self.assertNotIn(pre + k, t, pre + k)

    def test_mobile_never_calls_view(self):
        for rel in ("mobile/app/(sales)/calendar.tsx", "mobile/src/api/endpoints.ts"):
            self.assertNotIn("calendar/view", src(rel), rel)


class MutationsStillReloadTheFeed(unittest.TestCase):
    def test_after_change_reloads_roster_and_feed(self):
        t = src(TC)
        self.assertIn("await Promise.all([load(), loadFeed()])", t)
        self.assertIn("onChanged={afterChange}", t)
        self.assertGreaterEqual(t.count("afterChange('Booked: '"), 2)

    def test_mutation_authorization_unchanged(self):
        r = src(R)
        for route in ('@router.post("/appointments", status_code=201)',
                      '@router.post("/appointments/{appt_id}/cancel")',
                      '@router.post("/appointments/{appt_id}/outcome")'):
            sig = r[r.index(route):][:400]
            self.assertIn("user: User = Depends(require_sales_member)", sig, route)


class FeedGuaranteesRemain(unittest.TestCase):
    def setUp(self):
        self.cands = [
            sce.appointment_event(appt("a1"), opp("o1", SELLER), "S", [SELLER]),
            sce.appointment_event(appt("a2"), opp("o2", SELLER2), "S2", [SELLER2]),
            sce.appointment_event(appt("a3", brand="other"),
                                  opp("o3", SELLER, brand="other"), "S", [SELLER]),
        ]
        self.d1, self.d2 = date(2026, 10, 5), date(2026, 10, 11)

    def test_seller_manager_and_brand_scope(self):
        s = feed_for(self.cands, self.d1, self.d2, viewer=SELLER, manager=False)
        m = feed_for(self.cands, self.d1, self.d2)
        self.assertEqual([e["source_id"] for e in s["events"]], ["a1"])
        self.assertEqual({e["source_id"] for e in m["events"]}, {"a1", "a2"})

    def test_dedupe_partial_and_date_bounds(self):
        out = feed_for([self.cands[0], self.cands[0]], self.d1, self.d2)
        self.assertEqual(len(out["events"]), 1)
        down = feed_for(self.cands[:1], self.d1, self.d2,
                        status={"appointments": {"status": "unavailable", "count": 0}})
        self.assertEqual(down["unavailable_sources"], ["appointments"])
        outside = feed_for(self.cands, date(2026, 12, 1), date(2026, 12, 7))
        self.assertEqual(outside["events"], [])

    def test_dst_days_are_not_24_hours(self):
        s, e = sce.range_bounds(date(2026, 11, 1), date(2026, 11, 1), "America/Chicago")
        self.assertEqual((e - s).total_seconds(), 25 * 3600)
        s, e = sce.range_bounds(date(2026, 3, 8), date(2026, 3, 8), "America/Chicago")
        self.assertEqual((e - s).total_seconds(), 23 * 3600)

    def test_stale_guard_is_still_wired(self):
        t = src(TC)
        self.assertIn("feedGuard.current.isCurrent(token)", t)
        self.assertIn("setFeed(null)", t)
        self.assertIn("const reqId = useRef(0)", t)
        self.assertIn("reqId.current !== mine", t)


if __name__ == "__main__":
    unittest.main()
