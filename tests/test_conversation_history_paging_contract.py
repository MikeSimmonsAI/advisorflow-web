"""Dependency-free contract: mixed SMS/email history survives merge, channel
filtering and `before` paging with no dropped or repeated boundary events.
Executes the production helpers (history_paging); source-wires API and UI."""
import importlib.util
import os
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load(rel, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


HP = _load("app/services/history_paging.py", "history_paging_under_test")
RT = _load("app/services/reply_timeline.py", "reply_timeline_hp")
T0 = datetime(2026, 10, 1, 12, 0, 0)


class FakeCol:
    key = "ts"

    def __lt__(self, v):
        return ("lt", v)

    def __eq__(self, v):
        return ("eq", v)

    def desc(self):
        return "desc"


class FakeQuery:
    def __init__(self, rows, conds=(), n=None):
        self.rows, self.conds, self.n = rows, conds, n

    def filter(self, cond):
        return FakeQuery(self.rows, self.conds + (cond,), self.n)

    def order_by(self, _):
        return self

    def limit(self, n):
        return FakeQuery(self.rows, self.conds, n)

    def all(self):
        out = self.rows
        for op, v in self.conds:
            out = [r for r in out if (r.ts < v if op == "lt" else r.ts == v)]
        out = sorted(out, key=lambda r: r.ts, reverse=True)
        return out[: self.n] if self.n else out


def src(prefix, n, step_s, same=0):
    """n rows; the first `same` rows share one timestamp."""
    return [SimpleNamespace(
        id="%s%d" % (prefix, i),
        ts=T0 - timedelta(seconds=0 if i < same else (i - same + 1) * step_s))
        for i in range(n)]


SOURCES = {
    ("outbound", "sms"): src("m", 12, 60, same=4),   # ids collide across tables
    ("outbound", "email"): src("m", 7, 90),
    ("inbound", "sms"): src("m", 5, 120, same=2),
    ("inbound", "email"): src("m", 6, 75),
}
TOTAL = sum(len(v) for v in SOURCES.values())


def fetch_all(limit, before=None):
    events = []
    for (kind, ch), rows in SOURCES.items():
        for r in HP.fetch_with_ties(FakeQuery(rows), FakeCol(), limit + 1, before,
                                    lambda x: x.ts):
            events.append({"kind": kind, "channel": ch, "id": r.id, "timestamp": r.ts})
    return events


def walk(limit):
    seen, before = [], None
    for _ in range(100):
        p = HP.paginate(fetch_all(limit, before), limit)
        seen += p["events"]
        if not p["has_more"]:
            return seen
        before = p["next_before"]
    raise AssertionError("cursor did not terminate")


class Paging(unittest.TestCase):
    def test_all_four_classes_no_drop_no_repeat_at_every_page_size(self):
        for limit in (1, 2, 3, 5, 7, 11, 50):
            seen = walk(limit)
            uids = [HP.event_uid(e) for e in seen]
            self.assertEqual(len(uids), len(set(uids)), limit)
            self.assertEqual(len(uids), TOTAL, limit)
            self.assertEqual({(e["kind"], e["channel"]) for e in seen},
                             set(SOURCES))
            ts = [e["timestamp"] for e in seen]
            self.assertEqual(ts, sorted(ts, reverse=True))

    def test_naive_truncate_then_strict_cursor_loses_tie_rows(self):
        # The pre-fix rule: cut at `limit`, next page strictly older.
        ordered = HP.sort_newest_first(fetch_all(2))
        self.assertEqual(ordered[1]["timestamp"], ordered[2]["timestamp"])
        lost = [e for e in ordered[2:] if e["timestamp"] == ordered[1]["timestamp"]]
        self.assertTrue(lost)
        page = HP.paginate(fetch_all(2), 2)["events"]
        self.assertGreater(len(page), 2)  # fixed: tie group kept whole

    def test_uids_do_not_collide_across_tables(self):
        ids = [dict(kind=k, channel=c, id="m0") for k, c in SOURCES]
        self.assertEqual(len({HP.event_uid(x) for x in ids}), 4)

    def test_order_deterministic_for_equal_timestamps_undated_last(self):
        evs = [dict(kind="inbound", channel="sms", id="z", timestamp=T0),
               dict(kind="outbound", channel="email", id="a", timestamp=T0),
               dict(kind="outbound", channel="sms", id="n", timestamp=None),
               dict(kind="outbound", channel="sms", id="b", timestamp=T0)]
        self.assertEqual(HP.sort_newest_first(evs),
                         HP.sort_newest_first(list(reversed(evs))))
        self.assertIsNone(HP.sort_newest_first(evs)[-1]["timestamp"])

    def test_source_limit_does_not_split_ties(self):
        rows = src("x", 10, 60, same=6)
        got = HP.fetch_with_ties(FakeQuery(rows), FakeCol(), 3, None, lambda x: x.ts)
        self.assertEqual(len(got), 6)

    def test_api_wiring_and_tenant_scope(self):
        h = open(os.path.join(ROOT, "app/services/communication_history.py")).read()
        self.assertIn("reply_q.filter(is_email if", h)
        self.assertLess(h.index("reply_q.filter(is_email"), h.index("_page(reply_q"))
        self.assertIn("history_paging.paginate(events, limit)", h)
        self.assertEqual(RT.reply_channel(SimpleNamespace(source=" Email ")), "email")
        s = open(os.path.join(ROOT, "app/routers/leads_detail_router.py")).read()
        self.assertEqual(s.count("lead_scope.load_lead_in_scope(db, current_user, lead_id)"), 2)
        self.assertIn("fetch_with_ties(query, column, page_size, before", s)
        self.assertIn('"id": m.id,', s)
        self.assertIn('"id": e.id,', s)


class Frontend(unittest.TestCase):
    def setUp(self):
        self.src = open(os.path.join(ROOT, "frontend/src/pages/LeadDetail.jsx")).read()

    def test_key_includes_type_and_older_pages_append(self):
        self.assertIn("`id|${e.type || ''}|${e.channel || ''}|${e.id}`", self.src)
        self.assertIn("setOlderEvents((prev) => mergeTimelineEvents(prev, page?.events || []))", self.src)

    @unittest.skipUnless(shutil.which("node"), "node unavailable")
    def test_merge_keeps_prior_pages_and_same_id_across_tables(self):
        a = self.src.index("function timelineEventKey")
        b = self.src.index("// The cursor for the page OLDER")
        js = self.src[a:b] + "\n" + """
const p1=[{id:'m0',type:'outbound',channel:'sms',timestamp:'2026-10-01T12:00:00Z'},
          {id:'m0',type:'outbound',channel:'email',timestamp:'2026-10-01T12:00:00Z'}]
const p2=[{id:'m0',type:'inbound',channel:'sms',timestamp:'2026-10-01T11:00:00Z'},
          {id:'m0',type:'outbound',channel:'sms',timestamp:'2026-10-01T12:00:00Z'}]
const out=mergeTimelineEvents(p1,p2)
if(out.length!==3||out[0].type!=='inbound') throw new Error(JSON.stringify(out))
console.log('ok')
"""
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "ok", r.stderr)


if __name__ == "__main__":
    unittest.main()
