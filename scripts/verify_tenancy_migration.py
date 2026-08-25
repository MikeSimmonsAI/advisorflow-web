# scripts/verify_tenancy_migration.py -- READ ONLY.
# Confirms the nullable-organization_id migration and the is_test column
# actually landed in the target database.
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

fail = []


def col(table, column):
    cur.execute("""SELECT is_nullable, data_type FROM information_schema.columns
                   WHERE table_name=%s AND column_name=%s""", (table, column))
    return cur.fetchone()

print("=== users.organization_id ===")
r = col("users", "organization_id")
if not r:
    print("  ** COLUMN MISSING **"); fail.append("users.organization_id")
else:
    ok = r[0] == "YES"
    print("  is_nullable = %s   %s" % (r[0], "OK" if ok else "** STILL NOT NULL **"))
    if not ok:
        fail.append("users.organization_id still NOT NULL")

print("\n=== leads test-record columns ===")
for c in ("is_test", "test_note"):
    r = col("leads", c)
    print("  leads.%-10s %s" % (c, ("present (%s)" % r[1]) if r else "** MISSING **"))
    if not r:
        fail.append("leads." + c)

print("\n=== existing data integrity ===")
cur.execute("SELECT count(*) FROM users")
total = cur.fetchone()[0]
cur.execute("SELECT count(*) FROM users WHERE organization_id IS NULL")
nulls = cur.fetchone()[0]
print("  users total ................ %d" % total)
print("  users with NULL org ........ %d  (expected 0 until sales staff are created)" % nulls)

cur.execute("SELECT count(*) FROM leads WHERE is_test IS TRUE")
print("  leads flagged is_test ...... %d" % cur.fetchone()[0])
cur.execute("SELECT count(*) FROM leads")
print("  leads total ................ %d  (must be unchanged)" % cur.fetchone()[0])

conn.close()
print()
if fail:
    sys.exit("MIGRATION VERIFY FAILED: " + ", ".join(fail))
print("TENANCY MIGRATION VERIFIED IN PRODUCTION")
