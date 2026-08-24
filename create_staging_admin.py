"""
create_staging_admin.py — seed god_admin on the staging PostgreSQL database.

Usage:
    set DATABASE_URL=postgres://user:pass@host/dbname
    python create_staging_admin.py

Or inline:
    DATABASE_URL="postgres://..." python create_staging_admin.py
"""

import os, uuid
import psycopg2
import bcrypt

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    print("ERROR: set DATABASE_URL env var to your staging External Database URL from Render.")
    raise SystemExit(1)

# Fix Render's postgres:// → postgresql:// if needed (psycopg2 needs postgresql://)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

import psycopg2
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

EMAIL = "mike@simmonsstrong.com"
PASSWORD = "AdvisorFlow2024!"
ORG_NAME = "AdvisorFlow Platform"
ORG_SLUG = "advisorflow-platform"

# Check if org already exists
cur.execute("SELECT id FROM organizations WHERE slug = %s", (ORG_SLUG,))
row = cur.fetchone()
if row:
    org_id = row[0]
    print(f"Org already exists: {org_id}")
else:
    org_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO organizations (id, name, slug, plan, is_active) VALUES (%s, %s, %s, %s, %s)",
        (org_id, ORG_NAME, ORG_SLUG, "god", True),
    )
    print(f"Created org: {org_id}")

# Check if user already exists
cur.execute("SELECT id, role FROM users WHERE email = %s", (EMAIL,))
user_row = cur.fetchone()
if user_row:
    uid, role = user_row
    print(f"User already exists (id={uid}, role={role}) — upgrading to god_admin")
    pw = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
    cur.execute(
        "UPDATE users SET role='god_admin', must_change_password=FALSE, password_hash=%s WHERE id=%s",
        (pw, uid),
    )
else:
    uid = str(uuid.uuid4())
    pw = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
    cur.execute(
        """INSERT INTO users
           (id, organization_id, email, full_name, password_hash, role, is_active,
            must_change_password, failed_login_attempts)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (uid, org_id, EMAIL, "Mike Simmons", pw, "god_admin", True, False, 0),
    )
    print(f"Created user: {uid}")

conn.commit()
cur.execute("SELECT email, role FROM users WHERE email = %s", (EMAIL,))
print("Verified:", cur.fetchone())
conn.close()
print(f"\nDone — login at staging with:")
print(f"  Email:    {EMAIL}")
print(f"  Password: {PASSWORD}")
