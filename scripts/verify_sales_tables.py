# scripts/verify_sales_tables.py -- READ ONLY.
# Confirms the Phase 1 sales tables actually exist in the target database.
# Reads DATABASE_URL from the environment; never prints it.
import os, sys

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL not set.")
import psycopg2

u = os.environ["DATABASE_URL"]
if "sslmode=" not in u:
    u += ("&" if "?" in u else "?") + "sslmode=require"

conn = psycopg2.connect(u)
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
tables = {r[0] for r in cur.fetchall()}

EXPECTED = ("memberships", "brand_sales_orgs", "brand_packages",
            "opportunities", "discovery_records", "opportunity_events")

missing = []
print("=== Phase 1 sales tables ===")
for t in EXPECTED:
    ok = t in tables
    print("  %-24s %s" % (t, "CREATED" if ok else "** MISSING **"))
    if not ok:
        missing.append(t)

print("\n=== regression: existing tables intact ===")
for t in ("users", "organizations", "platforms", "leads", "booking_links", "messages"):
    print("  %-24s %s" % (t, "ok" if t in tables else "** MISSING **"))

print("\n  total tables in database: %d" % len(tables))

# Nothing should have been written to the new tables yet - no fake seed data.
for t in EXPECTED:
    if t in tables:
        cur.execute("SELECT count(*) FROM %s" % t)
        n = cur.fetchone()[0]
        if n:
            print("  NOTE: %s already holds %d row(s)" % (t, n))

conn.close()
if missing:
    sys.exit("MISSING TABLES: " + ", ".join(missing))
print("\nALL PHASE 1 TABLES PRESENT")
