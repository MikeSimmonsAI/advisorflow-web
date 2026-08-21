# BookaBoost — Backup, Staging & Alembic Migration Guide
*Written Aug 21 2026 — execute in order, do not skip steps*

---

## PHASE 1 — Backup Production First

### Step 1 · Check Your Render Postgres Plan

1. Go to [dashboard.render.com](https://dashboard.render.com)
2. In the left sidebar, click your PostgreSQL database (not the backend service — the actual DB)
3. Look at the plan shown at the top — it will say **Free**, **Starter**, **Standard**, etc.

**If it says Free:**
- Free tier has zero automatic backups
- Click **Upgrade** → choose **Starter ($7/mo)**
- Starter gives you: daily automatic snapshots + 7 days of point-in-time recovery
- Do this before anything else

**If it says Starter or higher:** You have daily backups. Confirm the "Last backup" timestamp is recent.

---

### Step 2 · Manual pg_dump Backup (Do This Right Now)

This is YOUR copy, independent of Render. A full export of every row in the database.

**From the Render Shell on your backend service:**

1. Go to advisorflow-backend → **Shell** in the left sidebar
2. Run this command (replace with your actual DATABASE_URL from Environment):

```bash
pg_dump $DATABASE_URL > /tmp/bookaboost_backup_aug21_2026.sql
```

3. Verify it's not empty:
```bash
wc -l /tmp/bookaboost_backup_aug21_2026.sql
```
Should show thousands of lines — not zero.

4. Download it to your computer. In the Render shell, you can't directly download files, so instead pipe it to stdout and copy the output, or use this alternative: run pg_dump locally if you have psql installed, pointing at the Render DB URL.

**Alternative — run from your local terminal if you have psql:**
```bash
pg_dump "postgresql://advisorflow:PASSWORD@HOST/DBNAME" > bookaboost_backup_aug21_2026.sql
```
(Get the full connection string from Render → your DB → Connection → External Database URL)

**Store this file somewhere safe:** Google Drive, Dropbox, local hard drive. This is your insurance policy.

---

## PHASE 2 — Create the Staging Environment

The goal: a complete second copy of the system that is 100% isolated from production. You break things in staging. Production stays clean.

### Step 3 · Create the Staging Branch on GitHub

```bash
git checkout main
git pull
git checkout -b staging
git push origin staging
```

That's it. `staging` is now a branch that starts identical to `main`.

**Workflow going forward:**
- All new feature work → commit to `staging` branch
- Test everything on staging
- When it's solid → `git merge staging main` → push to `main` → production deploys

---

### Step 4 · Create a Staging Database on Render

1. Render Dashboard → **New** → **PostgreSQL**
2. Name it: `advisorflow-db-staging`
3. Plan: **Free** is fine for staging (no real data, so no backup needed)
4. Region: same as production (Oregon / US West)
5. Click **Create Database**
6. Copy the **Internal Database URL** — you'll need it in the next step

---

### Step 5 · Create the Staging Backend Service on Render

1. Render Dashboard → **New** → **Web Service**
2. Connect to the same GitHub repo (`MikeSimmonsAI/advisorflow-web`)
3. Settings:
   - **Name:** `advisorflow-backend-staging`
   - **Branch:** `staging` ← critical, not main
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Plan:** Free (staging doesn't need uptime guarantees)
4. Click **Create Web Service**

**Then set Environment Variables** (same as production but with staging-specific values):

| Key | Value |
|---|---|
| `DATABASE_URL` | paste the staging DB Internal URL from Step 4 |
| `JWT_SECRET` | generate a new random string (different from prod) |
| `OPENAI_API_KEY` | same as prod |
| `TWILIO_ACCOUNT_SID` | same as prod |
| `TWILIO_AUTH_TOKEN` | same as prod |
| `TWILIO_PHONE_NUMBER` | same as prod (staging will use same Twilio number — that's fine) |
| `RESEND_API_KEY` | same as prod |
| `SUPER_ADMIN_EMAIL` | your email |
| `GOD_ADMIN_EMAIL` | `mike@simmonsstrong.com` |
| `ENVIRONMENT` | `staging` |

5. Deploy — it will pull the staging branch, install deps, and start. The startup migrations in `main.py` will create all tables fresh on the new staging DB automatically.

---

### Step 6 · Verify Staging Works

1. Visit your staging URL (Render gives it a `.onrender.com` subdomain)
2. Hit `/health` — should return `{"status": "ok"}`
3. Create a test user, log in, create a test lead — confirm the full flow works
4. This is now your sandbox. Break things here freely.

---

## PHASE 3 — Install Alembic (Start on Staging)

We validate Alembic works on staging before touching production.

### Step 7 · Install Alembic

```bash
pip install alembic
pip freeze | grep alembic >> requirements.txt
```

### Step 8 · Initialize Alembic

From the root of the project:

```bash
alembic init alembic
```

This creates:
```
alembic/
    env.py
    versions/        ← migration files live here
alembic.ini          ← config file
```

### Step 9 · Configure alembic/env.py

Replace the generated `env.py` with this:

```python
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def get_url():
    return os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))

def run_migrations_offline():
    url = get_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    url = get_url()
    connectable = engine_from_config(
        {"sqlalchemy.url": url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### Step 10 · Generate the Baseline Migration

This inspects the current schema and creates a migration file representing it:

```bash
DATABASE_URL="your_staging_db_url" alembic revision --autogenerate -m "initial_schema"
```

A file appears in `alembic/versions/` — open it and glance at the `upgrade()` function. It should list all your tables: `leads`, `users`, `organizations`, `messages`, `replies`, etc.

### Step 11 · Stamp the Staging DB

This tells Alembic "the database already looks like the initial migration — don't run it":

```bash
DATABASE_URL="your_staging_db_url" alembic stamp head
```

### Step 12 · Test That Alembic Works

Create a small test migration — add a throwaway column:

```bash
DATABASE_URL="your_staging_db_url" alembic revision -m "test_migration"
```

Edit the generated file:
```python
def upgrade():
    op.add_column('leads', sa.Column('alembic_test', sa.String(), nullable=True))

def downgrade():
    op.drop_column('leads', 'alembic_test')
```

Run it:
```bash
DATABASE_URL="your_staging_db_url" alembic upgrade head
```

Check the staging DB — `alembic_test` column should exist on the leads table.

Roll it back:
```bash
DATABASE_URL="your_staging_db_url" alembic downgrade -1
```

Column gone. Alembic works. Delete that test migration file.

---

## PHASE 4 — Update the Deploy Command

### Step 13 · Update Render Start Command (Staging First)

In Render → `advisorflow-backend-staging` → Settings → Start Command, change to:

```
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Deploy staging. Watch the logs — you should see Alembic run at startup, confirm it's at `head`, then Uvicorn start.

### Step 14 · Remove Manual ALTER TABLE Blocks from main.py

Once Alembic is running cleanly on staging, remove the `_index_migrations` block and any other `ALTER TABLE` / `ADD COLUMN` blocks from `main.py`. Alembic owns the schema from here. The performance indexes should be moved into a proper Alembic migration file.

---

## PHASE 5 — Promote to Production

Only after staging is running clean for at least one full deploy cycle.

### Step 15 · Stamp the Production DB

```bash
DATABASE_URL="your_PROD_db_url" alembic stamp head
```

This tells production's DB that it's already at the baseline — do not re-run the initial migration.

### Step 16 · Update Production Render Start Command

Render → `advisorflow-backend` → Settings → Start Command:

```
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### Step 17 · Merge Staging → Main → Deploy

```bash
git checkout main
git merge staging
git push origin main
```

Render auto-deploys. Alembic runs at startup, confirms prod is at `head`, Uvicorn starts. Done.

---

## Going Forward — The New Workflow

**Every schema change from here on:**

1. On the `staging` branch:
```bash
alembic revision --autogenerate -m "add_booking_settings_columns"
# review the generated file, make sure it looks right
alembic upgrade head  # test on staging
```

2. Commit the migration file:
```bash
git add alembic/versions/
git commit -m "migration: add booking settings columns"
```

3. Push to staging → staging deploys, migration runs automatically on staging DB
4. Test the feature on staging
5. Merge to main → production deploys, migration runs automatically on prod DB

**If something breaks:** `alembic downgrade -1` rolls back the last migration instantly.

---

## Summary

| What | Where | Purpose |
|---|---|---|
| Render daily snapshots | Render Postgres (Starter+) | Auto backup every 24hrs |
| pg_dump export | Your Google Drive / local | Manual full backup you control |
| `staging` branch | GitHub | Where all new code gets built |
| `advisorflow-backend-staging` | Render | Isolated sandbox — break things here |
| `advisorflow-db-staging` | Render | Staging data only, totally separate from prod |
| Alembic | In the codebase | Schema versioning with rollback |

Production never gets touched until staging proves it works.
