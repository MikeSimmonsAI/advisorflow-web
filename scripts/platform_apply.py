# scripts/platform_apply.py
# Assigns platform_id to super_admin accounts so platform scoping can be turned
# on in the API without collapsing them to a single org.
#
# ORDER MATTERS. deps.get_platform_org_ids() falls through to "own org only"
# when a super_admin has platform_id NULL. So the DATA must be seeded BEFORE
# the code is tightened, never after.
#
# Derived from the Aug 25 2026 audit:
#   organizations - all 6 client orgs already have platform_id. Nothing to do.
#   org-god-platform - intentionally stays unassigned (it is the god org).
#   god_admin - intentionally stays NULL (god is never platform-scoped).
#   super_admin x2 - both belong to EvoSys Pro orgs, both currently NULL.
#
# Assignment is per-email and explicit, not a batch UPDATE, so the blast radius
# is visible in the diff. Re-running is safe: it only touches NULL rows.
#
#   DRY RUN (default):  python scripts/platform_apply.py
#   APPLY:              python scripts/platform_apply.py --apply
import os, sys

APPLY = "--apply" in sys.argv

ASSIGNMENTS = [
    # (email, platform_id, why)
    ("simmonsmj242@gmail.com", "plt-evosyspro",
     "super_admin on Greenland Cemetery (slug 'restland'), an EvoSys Pro org"),
    ("user@gmail.com", "plt-evosyspro",
     "super_admin on WUPA (slug 'evosyspro'), an EvoSys Pro org"),
]

url = os.environ.get("DATABASE_URL", "")
if not url:
    sys.exit("DATABASE_URL not set in this shell.")

import psycopg2
import psycopg2.extras

if "sslmode=" not in url:
    url += ("&" if "?" in url else "?") + "sslmode=require"

conn = psycopg2.connect(url)
conn.autocommit = False
cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

print("MODE: %s\n" % ("APPLY - changes will be committed" if APPLY else "DRY RUN - nothing will be written"))

planned = 0
for email, platform_id, why in ASSIGNMENTS:
    cur.execute("SELECT email, role, platform_id FROM users WHERE email = %s;", (email,))
    row = cur.fetchone()
    if not row:
        print("  SKIP  %-26s account not found" % email)
        continue
    if row["role"] != "super_admin":
        print("  SKIP  %-26s role is %s, not super_admin" % (email, row["role"]))
        continue
    if row["platform_id"]:
        print("  SKIP  %-26s already assigned to %s" % (email, row["platform_id"]))
        continue
    print("  SET   %-26s -> %s" % (email, platform_id))
    print("        reason: %s" % why)
    planned += 1
    if APPLY:
        # Guarded: only ever writes a NULL row, and only for a super_admin.
        cur.execute("""
            UPDATE users SET platform_id = %s
            WHERE email = %s AND role = 'super_admin' AND platform_id IS NULL;
        """, (platform_id, email))
        print("        rows updated: %d" % cur.rowcount)

if APPLY:
    conn.commit()
    print("\nCOMMITTED %d change(s)." % planned)
else:
    conn.rollback()
    print("\nDRY RUN complete - %d change(s) would be made. Re-run with --apply." % planned)

print("\n=== post-state: admin accounts ===")
cur.execute("""
    SELECT u.email, u.role, COALESCE(u.platform_id,'(null)') AS user_platform,
           COALESCE(p.slug,'-') AS org_platform_slug
    FROM users u
    LEFT JOIN organizations o ON o.id = u.organization_id
    LEFT JOIN platforms p     ON p.id = o.platform_id
    WHERE u.role IN ('super_admin','god_admin')
    ORDER BY u.role, u.email;
""")
for r in cur.fetchall():
    print("  %-26s %-12s user_platform=%-16s org=%s" % (
        r["email"], r["role"], r["user_platform"], r["org_platform_slug"]))
print("\n  (god_admin SHOULD stay (null) - god is never platform-scoped)")

conn.close()
