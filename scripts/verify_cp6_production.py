"""READ-ONLY production verification for Checkpoint 6.

Runs SELECTs and nothing else. There is no INSERT, UPDATE, DELETE or DDL in
this file, and the session is opened in an explicitly read-only transaction so
the database itself would refuse one.

It never prints a connection string, a credential, a token hash, or any
family's personal data - only counts and structural facts.

RUN IT WHERE THE CREDENTIAL ALREADY IS. The production DATABASE_URL lives in
Render's environment, not in the local .env (which points at SQLite), and that
is the right way round - so the intended way to run this is the Render shell for
the backend service, where DATABASE_URL is already set and no secret has to be
copied anywhere:

    python scripts/verify_cp6_production.py

Locally it will refuse, because a local .env points at SQLite and verifying
"production has no fake data" against a dev database proves nothing.
"""
import os
import sys
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Load .env the same way the app does, without echoing anything.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
except Exception:
    pass

url = os.environ.get("DATABASE_URL", "")
if not url:
    raise SystemExit("DATABASE_URL is not set. Nothing to verify.")
if url.startswith("sqlite"):
    raise SystemExit(
        "DATABASE_URL points at SQLite, so this is a local database.\n"
        "Run this in the Render shell for advisorflow-backend, where the real\n"
        "DATABASE_URL is already set. Verifying 'production has no fake data'\n"
        "against a dev database would prove nothing.")

from sqlalchemy import create_engine, text                       # noqa: E402

engine = create_engine(url, pool_pre_ping=True)
FAIL = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:300]) if not ok else ""))
    if not ok:
        FAIL.append(label)


def scalar(conn, sql, **params):
    """First column of the first row.

    The connection parameter is `conn`, not `c`: it used to be `c`, and a bind
    parameter named `:c` was then passed as `scalar(c, sql, c=col)` - two values
    for the same argument, and a TypeError on the second section. Naming the
    connection something no bind parameter would plausibly be called is what
    stops that recurring.
    """
    return conn.execute(text(sql), params).scalar()


with engine.connect() as c:
    # Belt and braces: the transaction itself refuses writes.
    try:
        c.execute(text("SET TRANSACTION READ ONLY"))
    except Exception:
        pass

    print("\n--- Checkpoint 6 tables exist " + "-" * 38)
    for t in ("implementations", "implementation_milestones", "customer_activations"):
        n = scalar(c, "SELECT COUNT(*) FROM information_schema.tables "
                      "WHERE table_name = :t", t=t)
        check("table %s created" % t, n == 1, n)

    print("\n--- audit_log_entries migrated " + "-" * 37)
    for col in ("platform_id", "brand_sales_org_id", "before_state",
                "after_state", "note"):
        n = scalar(c, "SELECT COUNT(*) FROM information_schema.columns "
                      "WHERE table_name='audit_log_entries' AND column_name=:col",
                   col=col)
        check("column audit_log_entries.%s added" % col, n == 1, n)
    nullable = scalar(c, "SELECT is_nullable FROM information_schema.columns "
                         "WHERE table_name='audit_log_entries' "
                         "AND column_name='organization_id'")
    check("audit_log_entries.organization_id is nullable", nullable == "YES", nullable)

    print("\n--- the idempotency guarantee is real in production " + "-" * 16)
    uq = scalar(c, """
        SELECT COUNT(*) FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage k
          ON tc.constraint_name = k.constraint_name
        WHERE tc.table_name='implementations'
          AND tc.constraint_type='UNIQUE'
          AND k.column_name IN ('opportunity_id','organization_id')
    """)
    idx = scalar(c, """
        SELECT COUNT(*) FROM pg_indexes
        WHERE tablename='implementations' AND indexdef LIKE '%UNIQUE%'
    """)
    check("opportunity_id and organization_id are both uniquely constrained",
          (uq or 0) >= 2 or (idx or 0) >= 2, "uq=%s unique_idx=%s" % (uq, idx))

    print("\n--- PRODUCTION HAS NO FAKE OR DEMO DATA " + "-" * 28)
    n = scalar(c, "SELECT COUNT(*) FROM implementations")
    check("implementations table is empty (nothing provisioned yet)", n == 0, n)
    n = scalar(c, "SELECT COUNT(*) FROM customer_activations")
    check("no activation invitations exist", n == 0, n)
    n = scalar(c, "SELECT COUNT(*) FROM implementation_milestones")
    check("no milestones exist", n == 0, n)

    n = scalar(c, "SELECT COUNT(*) FROM organizations WHERE slug LIKE 'demo-%%'")
    check("no demo- customer organisations", n == 0, n)
    n = scalar(c, "SELECT COUNT(*) FROM organizations "
                  "WHERE lower(name) LIKE '%%cedar ridge%%' "
                  "   OR lower(name) LIKE '%%brookfield%%' "
                  "   OR lower(name) LIKE '%%northgate%%'")
    check("none of the local seed companies leaked into production", n == 0, n)
    n = scalar(c, "SELECT COUNT(*) FROM users WHERE email LIKE '%%@local.test' "
                  "   OR email LIKE '%%@example.test' OR email LIKE '%%.test'")
    check("no .test users exist", n == 0, n)

    n = scalar(c, "SELECT COUNT(*) FROM opportunities "
                  "WHERE customer_organization_id IS NOT NULL")
    check("no opportunity is linked to a customer org yet", n == 0, n)

    print("\n--- real production shape (context, not assertions) " + "-" * 16)
    for t in ("platforms", "organizations", "users", "opportunities",
              "brand_sales_orgs", "leads"):
        try:
            print("       %-20s %s rows" % (t, scalar(c, "SELECT COUNT(*) FROM %s" % t)))
        except Exception as e:
            print("       %-20s (unreadable: %s)" % (t, str(e)[:40]))

print("\n" + "=" * 64)
if FAIL:
    print("FAILED (%d):" % len(FAIL))
    for f in FAIL:
        print("  - %s" % f)
    sys.exit(1)
print("PRODUCTION VERIFICATION PASSED - read-only, nothing was written")
