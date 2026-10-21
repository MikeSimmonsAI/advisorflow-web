# Local synthetic data only: give rep2@example.test the demo_suite grant so the
# phone "More" check can see Demo Suite offered to a permitted rep.
# Usage: python grant_demo.py <path to the local SQLite db>
import sqlite3, uuid, sys
db = sqlite3.connect(sys.argv[1])
uid = db.execute("select id from users where email='rep2@example.test'").fetchone()[0]
pid = db.execute("select platform_id from brand_sales_orgs limit 1").fetchone()[0]
db.execute("insert into user_capability_grants (id,user_id,scope_type,scope_id,capability,is_active) values (?,?,?,?,?,1)",
           (str(uuid.uuid4()), uid, 'platform', pid, 'demo_suite'))
db.commit(); print('granted', uid, pid)
