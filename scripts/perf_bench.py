"""MEASURE FIRST. Query counts and wall time per route, against a real dataset.

The mission's rule is "do not optimize blindly", so this exists before any fix
does. It builds a fixture whose size you choose, hits each route through the
real app, and counts every SQL statement SQLAlchemy actually emits.

HOW THE COUNTING WORKS. A `before_cursor_execute` event listener on the engine
increments a counter per statement. That counts what the database is asked to
do, not what the ORM intended - lazy loads, duplicate identical selects and
cascade-triggered statements all show up, which is exactly the point.

WHY IT ALSO SNAPSHOTS THE RESPONSE. Making a route faster while quietly changing
what it returns is not an optimisation, it is a bug with a stopwatch. Every run
records a normalised digest of the response body so a later run can prove the
output is byte-identical. `--save` writes a baseline; `--compare` re-runs and
diffs counts, timings AND digests.

Usage:
    python scripts/perf_bench.py --save baseline.json          # measure & store
    python scripts/perf_bench.py --compare baseline.json       # after the fix
    python scripts/perf_bench.py --scale 200                   # bigger fixture
"""
import argparse
import hashlib
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="perfbench_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from sqlalchemy import event                                        # noqa: E402
from fastapi.testclient import TestClient                           # noqa: E402
from app.main import app                                            # noqa: E402
from app.deps import SessionLocal, engine                           # noqa: E402
from app.models.models import (                                     # noqa: E402
    Base, Platform, Organization, User, Lead, Message, Reply,
)
from app.models.sales_models import (                               # noqa: E402
    BrandSalesOrg, Membership, BrandPackage, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.implementation_models import (                      # noqa: E402
    Implementation, ImplementationMilestone,
)
from app.models.sales_models import OpportunityEvent                # noqa: E402
from app.models.scheduling_models import (                          # noqa: E402
    MeetingType, SalesAppointment, AppointmentParticipant,
    APPT_SCHEDULED, CONF_PENDING, CONF_CONFIRMED,
    SLOT_OPPORTUNITY_OWNER, SLOT_SALES_MANAGER,
)
from app.models.meeting_models import AppointmentMeeting            # noqa: E402
from app.services import availability as _av                        # noqa: E402
from app.services.auth_service import hash_password                 # noqa: E402

PW = "PerfBench!2026"

# ── the counter ─────────────────────────────────────────────────────────────
_stats = {"n": 0, "sql": []}


@event.listens_for(engine, "before_cursor_execute")
def _count(conn, cursor, statement, parameters, context, executemany):
    _stats["n"] += 1
    if len(_stats["sql"]) < 4000:
        _stats["sql"].append(statement.split("\n")[0][:110])


class Counted:
    """Count statements emitted inside the block."""

    def __enter__(self):
        _stats["n"] = 0
        _stats["sql"] = []
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self.t0) * 1000.0
        self.queries = _stats["n"]
        self.sql = list(_stats["sql"])


def digest(obj):
    """Order-insensitive digest of a response body.

    Sorts dict keys and, for lists of dicts carrying an 'id', sorts by id — so a
    change in ORDER of equal content does not read as a behaviour change, but a
    change in CONTENT does.
    """
    def norm(o):
        if isinstance(o, dict):
            return {k: norm(o[k]) for k in sorted(o)}
        if isinstance(o, list):
            items = [norm(x) for x in o]
            if items and all(isinstance(x, dict) and "id" in x for x in items):
                items.sort(key=lambda x: str(x.get("id")))
            return items
        return o
    return hashlib.sha256(
        json.dumps(norm(obj), sort_keys=True, default=str).encode()).hexdigest()[:16]


def top_repeats(sql, k=5):
    counts = {}
    for s in sql:
        counts[s] = counts.get(s, 0) + 1
    return sorted(counts.items(), key=lambda x: -x[1])[:k]


# ── fixture ─────────────────────────────────────────────────────────────────

def build(scale):
    """scale = implementations/customers; advisors and appointments scale with it."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    now = datetime.utcnow()

    db.add(Platform(id="plt", name="EvoSys Pro", slug="evo-perf"))
    db.add(Platform(id="plt2", name="BookaBoost", slug="bab-perf"))
    db.flush()
    db.add(BrandSalesOrg(id="bso", platform_id="plt", name="EvoSys Pro Sales",
                         slug="evo-sales-perf", timezone="America/Chicago"))
    db.add(BrandSalesOrg(id="bso2", platform_id="plt2", name="BookaBoost Sales",
                         slug="bab-sales-perf", timezone="America/Chicago"))
    db.add(BrandPackage(id="pkg", platform_id="plt", key="growth", name="Growth",
                        price=2495, currency="USD"))
    db.flush()

    def mk(uid, email, name, role, org=None):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=now - timedelta(days=1)))

    mk("u-god", "god@perf.test", "Owner", "god_admin")
    mk("u-mgr", "mgr@perf.test", "Sales Manager", "advisor")
    mk("u-rep", "rep@perf.test", "Sales Rep", "advisor")
    db.flush()
    db.add(Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso", role=ROLE_SALES_MANAGER, is_active=True))
    db.add(Membership(user_id="u-rep", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso", role=ROLE_SALES_REP, is_active=True))
    db.flush()

    # Customers + implementations + an advisor cohort per customer.
    for i in range(scale):
        oid = "org-%03d" % i
        db.add(Organization(id=oid, name="Customer %03d" % i, slug="cust-%03d" % i,
                            platform_id="plt" if i % 2 == 0 else "plt2", is_active=True))
        db.flush()
        mk("adm-%03d" % i, "adm%03d@perf.test" % i, "Admin %03d" % i, "org_admin", org=oid)
        for a in range(3):
            aid = "adv-%03d-%d" % (i, a)
            mk(aid, "adv%03d%d@perf.test" % (i, a), "Advisor %03d-%d" % (i, a),
               "advisor", org=oid)
        db.flush()
        opp = Opportunity(id="opp-%03d" % i, brand_sales_org_id="bso",
                          owner_user_id="u-rep", company_name="Customer %03d" % i,
                          status="won", stage="won", created_at=now)
        db.add(opp)
        db.flush()
        db.add(Implementation(id="impl-%03d" % i, opportunity_id=opp.id,
                              organization_id=oid, platform_id="plt",
                              brand_sales_org_id="bso", package_id="pkg",
                              sold_by_user_id="u-rep", owner_user_id="u-mgr",
                              status="configuration", created_at=now))
        db.flush()
        for m in range(4):
            db.add(ImplementationMilestone(
                id="ms-%03d-%d" % (i, m), implementation_id="impl-%03d" % i,
                key="k%d" % m, label="Milestone %d" % m,
                status="done" if m < 2 else "pending", position=m))
        # A few leads + messages per customer so counts are not trivially zero.
        for L in range(5):
            lid = "lead-%03d-%d" % (i, L)
            db.add(Lead(id=lid, organization_id=oid, first_name="L%d" % L,
                        last_name="C%03d" % i, phone="+1555%07d" % (i * 10 + L),
                        status="new", assigned_to_id="adv-%03d-%d" % (i, L % 3),
                        created_at=now))
            db.flush()
            db.add(Message(id="msg-%03d-%d" % (i, L), lead_id=lid,
                           sender_id="adv-%03d-%d" % (i, L % 3), body="hi"))
            if L % 2 == 0:
                db.add(Reply(id="rep-%03d-%d" % (i, L), lead_id=lid, body="ok",
                             received_at=now))
        # Opportunities for the pipeline board.
        for o in range(3):
            db.add(Opportunity(id="o-%03d-%d" % (i, o), brand_sales_org_id="bso",
                               owner_user_id="u-rep" if o % 2 else "u-mgr",
                               company_name="Prospect %03d-%d" % (i, o),
                               status="open", stage="discovery", created_at=now))
    db.flush()

    _timeline(db, scale, now)
    _appointments(db, scale, now)

    db.commit()
    db.close()


# ── the part the first baseline was missing ─────────────────────────────────
# my_day's cost is almost entirely appointment-driven: kind() runs a MeetingType
# query per appointment and is invoked twice over todays, _appt_brief costs three
# queries each and is called over SEVEN overlapping lists, and recent_activity
# resolves an actor name per row. A fixture with no SalesAppointment and no
# OpportunityEvent rows measures none of that - it measures an empty calendar
# and reports a number that looks healthy. These two builders exist so the
# benchmark exercises the shapes the optimisation is supposed to remove.

def _timeline(db, scale, now):
    """Activity events with DISTINCT actors, so _user_name cannot be memoised
    away by accident and the 15-row N+1 is real."""
    actors = ["u-rep", "u-mgr"] + ["adv-%03d-%d" % (i, i % 3) for i in range(scale)]
    n = 0
    for i in range(scale):
        for o in range(3):
            for e in range(3):
                n += 1
                db.add(OpportunityEvent(
                    id="ev-%03d-%d-%d" % (i, o, e),
                    opportunity_id="o-%03d-%d" % (i, o),
                    event_type=("created", "stage_changed", "note")[e],
                    summary="Event %d" % e,
                    actor_user_id=actors[n % len(actors)],
                    occurred_at=now - timedelta(hours=n)))
    db.flush()


def _appointments(db, scale, now):
    """A real sales calendar for the rep and for the brand.

    Placed with the SAME local-day maths my_day uses, so 'today' means today to
    the route rather than to UTC - otherwise the todays_appointments list comes
    back empty on either side of the timezone offset and the benchmark quietly
    measures nothing again.
    """
    tz = "America/Chicago"
    types = [
        ("discovery",       "Discovery Call",     30, False),
        ("discovery_demo",  "Discovery + Demo",   60, True),
        ("demo",            "Product Demo",       45, True),
        ("closing_call",    "Closing Call",       30, True),
        ("internal_review", "Pipeline Review",    30, False),
    ]
    for n, (key, label, mins, video) in enumerate(types):
        db.add(MeetingType(id="mt-" + key, brand_sales_org_id="bso", key=key,
                           name=label, duration_minutes=mins,
                           required_slots="opportunity_owner,sales_manager",
                           is_internal=(key == "internal_review"),
                           requires_video=video, sort_order=n))
    db.flush()

    today_local = _av.utc_to_local(now, tz).date()
    seq = {"n": 0}

    def appt(start, mins, mt_key, owner, opp_id, participants,
             confirmed=True, video=True):
        seq["n"] += 1
        aid = "appt-%04d" % seq["n"]
        db.add(SalesAppointment(
            id=aid, brand_sales_org_id="bso", opportunity_id=opp_id,
            meeting_type_id="mt-" + mt_key,
            title="%s - %s" % (mt_key.replace("_", " ").title(), opp_id),
            starts_at=start, ends_at=start + timedelta(minutes=mins),
            timezone=tz, status=APPT_SCHEDULED,
            prospect_name="Prospect %s" % opp_id,
            prospect_company="Company %s" % opp_id,
            prospect_email="p%s@perf.test" % seq["n"],
            confirmation_status=CONF_CONFIRMED if confirmed else CONF_PENDING,
            created_by=owner, created_at=now - timedelta(days=2)))
        db.flush()
        for uid, slot, required in participants:
            db.add(AppointmentParticipant(
                id="ap-%04d-%s" % (seq["n"], uid[-6:]), appointment_id=aid,
                user_id=uid, role_slot=slot, is_required=required,
                busy_start_at=start - timedelta(minutes=10),
                busy_end_at=start + timedelta(minutes=mins + 10)))
        if video:
            db.add(AppointmentMeeting(
                id="am-%04d" % seq["n"], appointment_id=aid,
                brand_sales_org_id="bso", provider="zoom",
                provider_meeting_id="z%09d" % seq["n"],
                join_url="https://zoom.example/j/%09d" % seq["n"],
                status="created"))
        db.flush()
        return aid

    def rep_opp(i):
        # o-XXX-1 is owned by u-rep (the loop above alternates on o % 2).
        return "o-%03d-1" % (i % max(scale, 1))

    both = [("u-rep", SLOT_OPPORTUNITY_OWNER, True),
            ("u-mgr", SLOT_SALES_MANAGER, False)]
    mgr_only = [("u-mgr", SLOT_OPPORTUNITY_OWNER, True)]

    # THE REP'S TODAY - six meetings at 9,10,11,13,14,15 local.
    keys = ["discovery", "demo", "discovery_demo", "closing_call",
            "discovery", "demo"]
    for n, hour in enumerate((9, 10, 11, 13, 14, 15)):
        appt(_av.local_to_utc(today_local, hour * 60, tz), 30, keys[n],
             "u-rep", rep_opp(n), both, confirmed=(n % 3 != 0), video=(n % 2 == 0))

    # THE REP'S NEXT TWO WEEKS - two a day, so `upcoming` reaches its 25 cap.
    for d in range(1, 16):
        day = today_local + timedelta(days=d)
        for n, hour in enumerate((10, 14)):
            k = keys[(d + n) % len(keys)]
            appt(_av.local_to_utc(day, hour * 60, tz), 45, k, "u-rep",
                 rep_opp(d * 2 + n), both,
                 confirmed=((d + n) % 4 != 0), video=((d + n) % 2 == 0))

    # HISTORY - a month behind, so the date filters have rows to exclude.
    for d in range(1, 31):
        day = today_local - timedelta(days=d)
        appt(_av.local_to_utc(day, 11 * 60, tz), 30, keys[d % len(keys)],
             "u-rep", rep_opp(d), both, confirmed=True, video=(d % 3 == 0))

    # THE REST OF THE BRAND - the rep is on none of these, the manager sees all
    # of them. Without these, manager scope and rep scope return the same set
    # and _visible_sales_appointments is never actually exercised.
    for i in range(scale):
        for n, off in enumerate((0, 3)):
            day = today_local + timedelta(days=off)
            appt(_av.local_to_utc(day, (9 + (i % 8)) * 60 + n * 30, tz), 30,
                 keys[(i + n) % len(keys)], "u-mgr", "o-%03d-0" % i, mgr_only,
                 confirmed=(i % 2 == 0), video=(i % 4 == 0))
    db.flush()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


ROUTES = [
    ("GET", "/sales/my-day", "rep", None),
    ("GET", "/sales/opportunities", "mgr", None),
    ("GET", "/sales/implementations", "mgr", None),
    ("GET", "/god/ops/implementations", "god", None),
    ("GET", "/god/ops/sales-operations", "god", None),
    ("GET", "/god/ops/customer-organizations", "god", None),
    ("GET", "/god/ops/won-queue", "god", None),
    ("GET", "/admin/dashboard", "god", None),
    ("GET", "/admin/dashboard/metrics", "god", None),
    ("GET", "/god/customers", "god", None),
    ("GET", "/god/platform/overview", "god", None),
]


def run(scale, repeats):
    build(scale)
    out = {"scale": scale, "routes": {}}
    with TestClient(app) as c:
        who = {"god": token(c, "god@perf.test"), "mgr": token(c, "mgr@perf.test"),
               "rep": token(c, "rep@perf.test")}
        for method, path, actor, body in ROUTES:
            h = who[actor]
            c.request(method, path, headers=h)          # warm caches/imports
            times, qs, dg, sql = [], None, None, []
            for _ in range(repeats):
                with Counted() as m:
                    r = c.request(method, path, headers=h)
                times.append(m.ms)
                qs = m.queries
                sql = m.sql
                try:
                    dg = digest(r.json())
                except Exception:
                    dg = "non-json:%s" % r.status_code
            out["routes"][path] = {
                "status": r.status_code, "queries": qs,
                "ms_median": round(statistics.median(times), 1),
                "ms_min": round(min(times), 1),
                "digest": dg,
                "top_repeated_sql": [{"count": n, "sql": s} for s, n in top_repeats(sql)],
            }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=25)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--save")
    ap.add_argument("--compare")
    a = ap.parse_args()

    res = run(a.scale, a.repeats)

    print("=" * 92)
    print("PERF BENCH  scale=%d customers  (%d advisors, %d impls, %d leads)"
          % (a.scale, a.scale * 3, a.scale, a.scale * 5))
    print("=" * 92)
    print("%-42s %8s %10s  %s" % ("ROUTE", "QUERIES", "ms(med)", "digest"))
    print("-" * 92)
    for p, d in res["routes"].items():
        print("%-42s %8d %10.1f  %s%s" % (p, d["queries"], d["ms_median"], d["digest"],
                                          "" if d["status"] == 200 else "  <%s>" % d["status"]))

    if a.compare and os.path.exists(a.compare):
        base = json.load(open(a.compare))
        print("\n" + "=" * 92)
        print("COMPARED WITH %s (scale %s)" % (a.compare, base.get("scale")))
        print("=" * 92)
        print("%-42s %19s %19s  %s" % ("ROUTE", "QUERIES", "ms(med)", "RESULT"))
        print("-" * 92)
        regressions = 0
        for p, d in res["routes"].items():
            b = base["routes"].get(p)
            if not b:
                continue
            same = b["digest"] == d["digest"]
            dq = d["queries"] - b["queries"]
            dm = d["ms_median"] - b["ms_median"]
            if not same or dq > 0:
                regressions += 1
            print("%-42s %8d -> %-8d %8.1f -> %-8.1f  %s" % (
                p, b["queries"], d["queries"], b["ms_median"], d["ms_median"],
                "SAME OUTPUT" if same else "!! OUTPUT CHANGED"))
        print("\n%d route(s) regressed or changed output." % regressions)

    if a.save:
        json.dump(res, open(a.save, "w"), indent=1)
        print("\nsaved -> %s" % a.save)

    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
