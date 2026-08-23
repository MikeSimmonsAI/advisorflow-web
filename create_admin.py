import sqlite3, uuid, bcrypt

conn = sqlite3.connect('advisorflow.db')
cur = conn.cursor()

# 1. Create a god-level org
org_id = str(uuid.uuid4())
cur.execute('''
    INSERT INTO organizations (id, name, slug, plan, is_active)
    VALUES (?, ?, ?, ?, ?)
''', (org_id, 'AdvisorFlow Platform', 'advisorflow-platform', 'god', 1))

# 2. Create god_admin user (all NOT NULL fields included)
uid = str(uuid.uuid4())
pw = bcrypt.hashpw(b'AdvisorFlow2024!', bcrypt.gensalt()).decode()
cur.execute('''
    INSERT INTO users (id, organization_id, email, full_name, password_hash, role, is_active,
                       must_change_password, failed_login_attempts)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
''', (uid, org_id, 'mike@simmonsstrong.com', 'Mike Simmons', pw, 'god_admin', 1, 0, 0))

conn.commit()
cur.execute('SELECT email, role FROM users')
print('Users:', cur.fetchall(), flush=True)
conn.close()
print('Done — login: mike@simmonsstrong.com / AdvisorFlow2024!', flush=True)
