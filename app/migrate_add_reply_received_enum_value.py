"""
One-time manual migration: adds the new 'REPLY_RECEIVED' value to the
notificationtype Postgres enum type.

Same issue as app/migrate_add_advisor_flagged_enum_value.py: NotificationType
is declared with SAEnum() in models.py, which creates a native Postgres
enum TYPE. Adding a new Python enum value (NotificationType.REPLY_RECEIVED)
does not retroactively add it to an already-existing Postgres enum type -
without this migration, the new "every reply" alert (notify_reply in
notification_service.py) fails in production the first time a non-hot
reply comes in, with something like:
    invalid input value for enum notificationtype: "REPLY_RECEIVED"
despite working fine locally against SQLite, which doesn't enforce enum
types at the database level at all.

CASING FIX: matches the real bug found and fixed live in
migrate_add_advisor_flagged_enum_value.py - SQLAlchemy's SAEnum writes
the Python enum MEMBER NAME to Postgres by default, not member.value.
NotificationType.REPLY_RECEIVED.name is "REPLY_RECEIVED" (uppercase);
.value is "reply_received" (lowercase). This script adds the uppercase
NAME, since that's what the running code actually sends - the original
draft of this script (written alongside the advisor_flagged one, before
the casing bug was caught) would have had the identical problem if run
as originally written.

USAGE: run this once, manually, against the live database - e.g. via
Render's Shell tab on advisorflow-backend:
    python -m app.migrate_add_reply_received_enum_value

Idempotent via "ADD VALUE IF NOT EXISTS" - safe to run more than once.
"""

import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")


def run_migration():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set. Run this in an environment with the real database connection configured.")
        return

    if DATABASE_URL.startswith("sqlite"):
        print("DATABASE_URL is SQLite - enum types aren't enforced at the DB level here, nothing to migrate. Skipping.")
        return

    engine = create_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        sql = "ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'REPLY_RECEIVED';"
        print(f"Running: {sql}")
        conn.execute(text(sql))

    print("\nMigration complete. 'REPLY_RECEIVED' is now a valid notificationtype value (or already was).")


if __name__ == "__main__":
    run_migration()
