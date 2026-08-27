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
    db.commit()
    db.close()


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
