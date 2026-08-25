# scripts/god_snapshot.py -- READ ONLY.
# Dumps a real data snapshot for the God Mode Command Center as JSON, so design
# work happens against actual numbers instead of invented placeholders.
# Writes to scripts/god_snapshot.json
import os, sys, json, datetime, decimal

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL not set.")
import psycopg2
import psycopg2.extras

u = os.environ["DATABASE_URL"]
if "sslmode=" not in u:
    u += ("&" if "?" in u else "?") + "sslmode=require"
conn = psycopg2.connect(u)
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

out = {}


def q(key, sql, one=False):
    try:
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
        out[key] = (rows[0] if rows else None) if one else rows
    except Exception as e:
        out[key] = {"error": str(e).split("\n")[0][:160]}


q("totals", """
    SELECT
      (SELECT count(*) FROM organizations)                                    AS orgs,
      (SELECT count(*) FROM organizations WHERE is_active)                    AS orgs_active,
      (SELECT count(*) FROM users)                                            AS users,
      (SELECT count(*) FROM users WHERE is_active)                            AS users_active,
      (SELECT count(*) FROM leads)                                            AS leads,
      (SELECT count(*) FROM leads WHERE created_at >= now()-interval '30 days')  AS leads_30d,
      (SELECT count(*) FROM leads WHERE created_at >= now()-interval '7 days')   AS leads_7d,
      (SELECT count(*) FROM messages)                                         AS sms_total,
      (SELECT count(*) FROM messages WHERE sent_at >= now()-interval '7 days') AS sms_7d,
      (SELECT count(*) FROM replies)                                          AS replies_total,
      (SELECT count(*) FROM replies WHERE received_at >= now()-interval '7 days') AS replies_7d,
      (SELECT count(*) FROM email_messages)                                   AS emails_total,
      (SELECT count(*) FROM booking_links)                                    AS booking_links,
      (SELECT count(*) FROM booking_links WHERE status='booked')              AS booked,
      (SELECT count(*) FROM leads WHERE status='dnc')                         AS dnc
""", one=True)

q("platforms", """
    SELECT p.name, p.slug, p.domain,
           count(DISTINCT o.id) AS orgs,
           COALESCE(sum((SELECT count(*) FROM leads l WHERE l.organization_id=o.id)),0) AS leads,
           COALESCE(sum((SELECT count(*) FROM users u WHERE u.organization_id=o.id)),0) AS users
    FROM platforms p LEFT JOIN organizations o ON o.platform_id=p.id
    GROUP BY p.id,p.name,p.slug,p.domain ORDER BY leads DESC;
""")

q("orgs", """
    SELECT o.id, o.name, o.slug, o.is_active, COALESCE(o.plan,'-') AS plan,
           COALESCE(p.slug,'unassigned') AS platform,
           (SELECT count(*) FROM users u WHERE u.organization_id=o.id) AS users,
           (SELECT count(*) FROM leads l WHERE l.organization_id=o.id) AS leads,
           (SELECT count(*) FROM leads l WHERE l.organization_id=o.id
              AND l.created_at >= now()-interval '30 days') AS leads_30d,
           (SELECT count(*) FROM messages m JOIN leads l2 ON l2.id=m.lead_id
              WHERE l2.organization_id=o.id) AS sms,
           (SELECT count(*) FROM replies r JOIN leads l3 ON l3.id=r.lead_id
              WHERE l3.organization_id=o.id) AS replies,
           (SELECT max(l4.created_at) FROM leads l4 WHERE l4.organization_id=o.id) AS last_lead_at,
           (SELECT max(m2.sent_at) FROM messages m2 JOIN leads l5 ON l5.id=m2.lead_id
              WHERE l5.organization_id=o.id) AS last_sms_at
    FROM organizations o LEFT JOIN platforms p ON p.id=o.platform_id
    ORDER BY leads DESC;
""")

# 14 day lead + sms trend, for sparklines that mean something
q("trend_leads_14d", """
    SELECT to_char(d::date,'YYYY-MM-DD') AS day,
           (SELECT count(*) FROM leads l WHERE l.created_at::date = d::date) AS n
    FROM generate_series(now()-interval '13 days', now(), interval '1 day') d ORDER BY day;
""")
q("trend_sms_14d", """
    SELECT to_char(d::date,'YYYY-MM-DD') AS day,
           (SELECT count(*) FROM messages m WHERE m.sent_at::date = d::date) AS n
    FROM generate_series(now()-interval '13 days', now(), interval '1 day') d ORDER BY day;
""")

q("lead_tiers", "SELECT COALESCE(tier,'(none)') AS tier, count(*) AS n FROM leads GROUP BY tier ORDER BY n DESC;")
q("lead_status", "SELECT COALESCE(status,'(none)') AS status, count(*) AS n FROM leads GROUP BY status ORDER BY n DESC LIMIT 12;")

q("recent_activity", """
    SELECT * FROM (
      SELECT 'lead' AS kind, l.created_at AS at, l.first_name||' '||COALESCE(l.last_name,'') AS who,
             o.name AS org, COALESCE(l.source,'-') AS detail
        FROM leads l JOIN organizations o ON o.id=l.organization_id
       WHERE l.created_at IS NOT NULL
      UNION ALL
      SELECT 'reply', r.received_at, COALESCE(l.first_name,'')||' '||COALESCE(l.last_name,''),
             o.name, left(COALESCE(r.body,''),60)
        FROM replies r JOIN leads l ON l.id=r.lead_id JOIN organizations o ON o.id=l.organization_id
       WHERE r.received_at IS NOT NULL
      UNION ALL
      SELECT 'sms', m.sent_at, COALESCE(l.first_name,'')||' '||COALESCE(l.last_name,''),
             o.name, left(COALESCE(m.body,''),60)
        FROM messages m JOIN leads l ON l.id=m.lead_id JOIN organizations o ON o.id=l.organization_id
       WHERE m.sent_at IS NOT NULL
    ) x ORDER BY at DESC LIMIT 25;
""")

q("users_by_role", "SELECT role, count(*) AS n FROM users GROUP BY role ORDER BY n DESC;")
q("recent_logins", """
    SELECT email, role, last_login_at FROM users
    WHERE last_login_at IS NOT NULL ORDER BY last_login_at DESC LIMIT 10;
""")


def enc(o):
    if isinstance(o, (datetime.datetime, datetime.date)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    return str(o)


path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "god_snapshot.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2, default=enc)

print("wrote %s" % path)
for k, v in out.items():
    n = len(v) if isinstance(v, list) else 1
    print("  %-20s %s" % (k, ("%d rows" % n) if isinstance(v, list) else "ok"))
conn.close()
