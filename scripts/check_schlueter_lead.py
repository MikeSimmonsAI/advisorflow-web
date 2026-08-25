# scripts/check_schlueter_lead.py -- READ ONLY.
# The EvoSys Pro Sales Manager's personal email exists as a pre-need funeral
# LEAD inside a customer org. Establish whether he is at risk of receiving
# automated funeral outreach.
import os, sys

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL not set.")
import psycopg2
import psycopg2.extras

EMAIL = "michaelpschlueter@gmail.com"

u = os.environ["DATABASE_URL"]
if "sslmode=" not in u:
    u += ("&" if "?" in u else "?") + "sslmode=require"
conn = psycopg2.connect(u)
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

cur.execute("""SELECT l.id, l.first_name, l.last_name, l.email, l.phone, l.tier, l.status,
                      l.contact_channel, l.created_at, l.last_messaged_at, o.name AS org
               FROM leads l LEFT JOIN organizations o ON o.id = l.organization_id
               WHERE lower(l.email) = lower(%s)""", (EMAIL,))
leads = cur.fetchall()
print("=== lead rows for %s ===" % EMAIL)
for r in leads:
    print("  id=%s  %s %s" % (r["id"], r["first_name"], r["last_name"]))
    print("     org=%s  tier=%s  status=%s  channel=%s" % (r["org"], r["tier"], r["status"], r["contact_channel"]))
    print("     phone=%s  created=%s  last_messaged=%s" % (r["phone"], r["created_at"], r["last_messaged_at"]))

for r in leads:
    lid = r["id"]
    print("\n  --- outreach exposure for lead %s ---" % lid)
    for label, sql in [
        ("messages sent (SMS)", "SELECT count(*) FROM messages WHERE lead_id=%s"),
        ("replies received",    "SELECT count(*) FROM replies WHERE lead_id=%s"),
        ("cadence state rows",  "SELECT count(*) FROM cadence_states WHERE lead_id=%s"),
        ("pipeline convos",     "SELECT count(*) FROM pipeline_conversations WHERE lead_id=%s"),
        ("booking links",       "SELECT count(*) FROM booking_links WHERE lead_id=%s"),
    ]:
        try:
            cur.execute(sql, (lid,))
            print("     %-22s %s" % (label, cur.fetchone()[0]))
        except Exception as e:
            print("     %-22s ERROR %s" % (label, str(e).split("\n")[0][:60]))

    try:
        cur.execute("""SELECT active, current_step, next_send_at FROM cadence_states
                       WHERE lead_id=%s""", (lid,))
        for c in cur.fetchall():
            print("     CADENCE active=%s step=%s next_send_at=%s"
                  % (c["active"], c["current_step"], c["next_send_at"]))
    except Exception:
        pass

conn.close()
print("\nREAD-ONLY: nothing modified.")
