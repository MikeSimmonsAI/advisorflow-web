# scripts/god_data_check.py -- READ ONLY.
# How much real data exists for a God Mode dashboard to actually show?
# Answers "is the Command Center empty because it is unbuilt, or because
# there is nothing to put in it?"
import os, sys
if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL not set.")
import psycopg2
u = os.environ["DATABASE_URL"]
if "sslmode=" not in u:
    u += ("&" if "?" in u else "?") + "sslmode=require"
c = psycopg2.connect(u)
c.set_session(readonly=True, autocommit=True)
k = c.cursor()


def one(label, sql):
    try:
        k.execute(sql)
        r = k.fetchone()
        print("  %-38s %s" % (label, r[0] if r else 0))
    except Exception as e:
        print("  %-38s ERROR: %s" % (label, str(e).split(chr(10))[0][:70]))


print("=== VOLUME ===")
one("organizations", "SELECT count(*) FROM organizations;")
one("users", "SELECT count(*) FROM users;")
one("leads", "SELECT count(*) FROM leads;")
one("leads created last 30d", "SELECT count(*) FROM leads WHERE created_at >= now() - interval '30 days';")
one("messages (SMS)", "SELECT count(*) FROM messages;")
one("replies (inbound)", "SELECT count(*) FROM replies;")
one("email_messages", "SELECT count(*) FROM email_messages;")
one("booking_links", "SELECT count(*) FROM booking_links;")
one("booking_links booked", "SELECT count(*) FROM booking_links WHERE status='booked';")
one("pipeline_conversations", "SELECT count(*) FROM pipeline_conversations;")
one("platform_events", "SELECT count(*) FROM platform_events;")

print("\n=== PER-ORG ACTIVITY (what a tenant table would show) ===")
try:
    k.execute("""
        SELECT o.name,
               (SELECT count(*) FROM users u WHERE u.organization_id=o.id) AS users,
               (SELECT count(*) FROM leads l WHERE l.organization_id=o.id) AS leads,
               (SELECT count(*) FROM messages m JOIN leads l2 ON l2.id=m.lead_id
                 WHERE l2.organization_id=o.id) AS sms,
               (SELECT max(l3.created_at) FROM leads l3 WHERE l3.organization_id=o.id) AS last_lead
        FROM organizations o ORDER BY leads DESC;
    """)
    print("  %-38s %6s %7s %7s  %s" % ("org", "users", "leads", "sms", "last lead"))
    for r in k.fetchall():
        print("  %-38s %6s %7s %7s  %s" % (r[0][:38], r[1], r[2], r[3], r[4] or "-"))
except Exception as e:
    print("  ERROR: %s" % e)

print("\n=== IS THERE AN AUDIT TRAIL TO SHOW? ===")
k.execute("""SELECT table_name FROM information_schema.tables
             WHERE table_schema='public' AND table_name LIKE '%audit%' OR table_name LIKE '%event%';""")
t = [r[0] for r in k.fetchall()]
print("  audit/event tables: %s" % (t or "NONE"))
c.close()
