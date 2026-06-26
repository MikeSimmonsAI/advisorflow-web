"""
One-time manual migration: adds columns that were added to the SQLAlchemy
models across recent sessions but never actually applied to the live
production database, since this project relies on
Base.metadata.create_all() at startup - which only creates NEW tables,
and never adds new COLUMNS to a table that already exists.

REAL BUG THIS FIXES: confirmed via Render's live logs - every request
touching the users table was failing with
"column users.microsoft_oauth_refresh_token_encrypted does not exist",
because the Microsoft 365 integration added new columns to the User
model, but the live database's users table was created months ago and
was never told about them.

This script is intentionally idempotent - every ALTER TABLE uses
"IF NOT EXISTS" (or an equivalent existence check), so running it
multiple times, or running it after some columns already exist, is
always safe and never errors.

USAGE: run this once, manually, against the live database - e.g. via
Render's Shell tab on advisorflow-backend:
    python -m app.migrate_add_missing_columns
"""

import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")

# (table, column, full column definition) - every column added to an
# EXISTING table across tonight's session that create_all() would never
# retroactively add to a live database.
COLUMNS_TO_ADD = [
    ("users", "microsoft_oauth_refresh_token_encrypted", "VARCHAR"),
    ("users", "microsoft_email_address", "VARCHAR"),
    ("users", "microsoft_365_connected", "BOOLEAN DEFAULT FALSE"),
    ("users", "notification_phone", "VARCHAR"),
    ("users", "notify_via_sms", "BOOLEAN DEFAULT FALSE"),
    ("users", "can_import_leads", "BOOLEAN DEFAULT FALSE"),
    ("leads", "engagement_temperature", "VARCHAR"),
    ("replies", "classification", "VARCHAR"),
    ("replies", "classification_confidence", "VARCHAR"),
    ("replies", "classification_reasoning", "TEXT"),
    ("notifications", "send_failure_reason", "TEXT"),
    ("lead_outcomes", "has_preneed_planning", "BOOLEAN"),
    ("lead_outcomes", "has_insurance_funding", "BOOLEAN"),
    ("lead_outcomes", "is_veteran", "BOOLEAN"),
    ("lead_outcomes", "next_step", "TEXT"),
]


def run_migration():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set. This must be run in an environment with the real database connection configured.")
        return

    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        for table, column, definition in COLUMNS_TO_ADD:
            # PostgreSQL supports "ADD COLUMN IF NOT EXISTS" directly,
            # which makes this whole script safely re-runnable.
            sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition};"
            print(f"Running: {sql}")
            conn.execute(text(sql))
        conn.commit()

    print("\nMigration complete. All missing columns have been added (or already existed).")


if __name__ == "__main__":
    run_migration()
