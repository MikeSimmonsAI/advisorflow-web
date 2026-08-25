# scripts/platform_audit.py  --  READ ONLY. Runs no UPDATE, INSERT, or DELETE.
# Reads DATABASE_URL from the environment (never prints it).
# Reports how orgs and super_admins map to platforms, so the platform-scope
# migration can be applied in the correct order.
import os, sys

url = os.environ.get("DATABASE_URL", "")
if not url:
    sys.exit("DATABASE_URL not set in this shell.")

import psycopg2
import psycopg2.extras

# Render external URLs need SSL.
if "sslmode=" not in url:
    url += ("&" if "?" in url else "?") + "sslmode=require"

conn = psycopg2.connect(url)
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)


def show(title, sql):
    print("\n=== %s ===" % title)
    cur.execute(sql)
    rows = cur.fetchall()
    if not rows:
        print("  (none)")
        return rows
    cols = [d[0] for d in cur.description]
    w = {c: max(len(c), max((len(str(r[c])) for r in rows), default=0)) for c in cols}
    print("  " + " | ".join(c.ljust(w[c]) for c in cols))
    print("  " + "-+-".join("-" * w[c] for c in cols))
    for r in rows:
        print("  " + " | ".join(str(r[c]).ljust(w[c]) for c in cols))
    return rows


show("PLATFORMS", "SELECT id, name, slug, domain, is_active FROM platforms ORDER BY id;")

show("ORGANIZATIONS", """
    SELECT o.id, o.name, o.slug,
           COALESCE(o.platform_id,'** UNASSIGNED **') AS platform_id,
           (SELECT count(*) FROM users u WHERE u.organization_id = o.id) AS users
    FROM organizations o
    ORDER BY o.platform_id NULLS FIRST, o.name;
""")

show("ADMIN ACCOUNTS", """
    SELECT u.email, u.role,
           COALESCE(u.organization_id,'-')             AS org_id,
           COALESCE(u.platform_id,'** UNASSIGNED **')  AS user_platform,
           COALESCE(o.platform_id,'-')                 AS org_platform,
           COALESCE(p.slug,'-')                        AS org_platform_slug
    FROM users u
    LEFT JOIN organizations o ON o.id = u.organization_id
    LEFT JOIN platforms p     ON p.id = o.platform_id
    WHERE u.role IN ('super_admin','god_admin')
    ORDER BY u.role, u.email;
""")

show("SUMMARY", """
    SELECT
      (SELECT count(*) FROM organizations WHERE platform_id IS NULL) AS orgs_unassigned,
      (SELECT count(*) FROM organizations WHERE platform_id IS NOT NULL) AS orgs_assigned,
      (SELECT count(*) FROM users WHERE role='super_admin' AND platform_id IS NULL) AS supers_unassigned,
      (SELECT count(*) FROM users WHERE role='super_admin' AND platform_id IS NOT NULL) AS supers_assigned,
      (SELECT count(*) FROM users WHERE role='god_admin') AS god_admins;
""")

# Columns the ORM declares but auto_migrate.py never adds - suspected missing.
print("\n=== SUSPECTED MISSING COLUMNS (booking_links) ===")
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name='booking_links';
""")
have = {r[0] for r in cur.fetchall()}
for c in ("confirmation_sent", "reminder_24hr_sent", "reminder_1hr_sent",
          "review_request_sent_at", "organization_id", "user_id"):
    print("  %-24s %s" % (c, "present" if c in have else "** MISSING **"))

print("\n=== sms consent columns on leads ===")
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='leads';")
lead_cols = {r[0] for r in cur.fetchall()}
for c in ("sms_consent", "sms_consent_timestamp", "sms_consent_ip", "sms_consent_text"):
    print("  %-24s %s" % (c, "present" if c in lead_cols else "** MISSING **"))

conn.close()
print("\nAUDIT COMPLETE - nothing was modified.")
