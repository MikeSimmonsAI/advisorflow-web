import os, sys
from dotenv import load_dotenv
load_dotenv()
import psycopg2

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

cur.execute("SELECT EXISTS(SELECT FROM information_schema.tables WHERE table_name='job_runs')")
exists = cur.fetchone()[0]
print('table_exists:', exists)

if exists:
    cur.execute("SELECT job_name, status, COUNT(*) FROM job_runs GROUP BY job_name, status ORDER BY job_name, status")
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(r)
    else:
        print('no rows yet (table empty)')

    cur.execute("SELECT job_name, status, started_at, finished_at, error_message FROM job_runs ORDER BY started_at DESC LIMIT 10")
    recent = cur.fetchall()
    print()
    print('--- 10 most recent ---')
    for r in recent:
        print(r)

conn.close()
