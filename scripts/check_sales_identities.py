# scripts/check_sales_identities.py -- READ ONLY.
# Do the EvoSys Pro sales-team people already exist as users? As leads?
# Run BEFORE seeding memberships so we never create a duplicate identity.
import os, sys

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL not set.")
import psycopg2
import psycopg2.extras

PEOPLE = [
    ("Michael Schlueter", "michaelpschlueter@gmail.com", "sales_manager"),
    ("Blake Rehani",      "blakerehani@gmail.com",       "sales_rep"),
    ("Mike Simmons",      "mike@simmonsstrong.com",      "owner/god"),
]

u = os.environ["DATABASE_URL"]
if "sslmode=" not in u:
    u += ("&" if "?" in u else "?") + "sslmode=require"
conn = psycopg2.connect(u)
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

print("=== Does a USER row already exist? ===")
for name, email, intended in PEOPLE:
    cur.execute("""SELECT id, email, full_name, role, is_active, organization_id, last_login_at
                   FROM users WHERE lower(email) = lower(%s)""", (email,))
    r = cur.fetchone()
    if r:
        print("  %-28s USER EXISTS  role=%-12s active=%s  last_login=%s"
              % (email, r["role"], r["is_active"], r["last_login_at"] or "never"))
        print("      %-24s id=%s  org=%s" % ("", r["id"], r["organization_id"]))
    else:
        print("  %-28s no user row  (intended: %s)" % (email, intended))

print("\n=== Do they appear as LEADS? (would be a different domain entirely) ===")
for name, email, _ in PEOPLE:
    cur.execute("""SELECT l.id, l.first_name, l.last_name, l.tier, l.status, o.name AS org
                   FROM leads l LEFT JOIN organizations o ON o.id = l.organization_id
                   WHERE lower(l.email) = lower(%s)""", (email,))
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print("  %-28s LEAD in '%s'  tier=%s status=%s"
                  % (email, r["org"], r["tier"], r["status"]))
    else:
        print("  %-28s not a lead" % email)

print("\n=== EvoSys Pro platform ===")
cur.execute("SELECT id, name, slug, domain, support_email FROM platforms WHERE slug = 'evosyspro'")
p = cur.fetchone()
print("  %s" % (dict(p) if p else "** evosyspro platform NOT FOUND **"))

print("\n=== Existing brand sales orgs / memberships (should be empty) ===")
for t in ("brand_sales_orgs", "memberships", "brand_packages"):
    cur.execute("SELECT count(*) FROM %s" % t)
    print("  %-20s %d row(s)" % (t, cur.fetchone()[0]))

conn.close()
print("\nREAD-ONLY: nothing was created or modified.")
