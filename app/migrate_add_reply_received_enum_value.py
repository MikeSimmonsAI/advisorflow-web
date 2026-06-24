"""
One-time manual migration: adds the new 'reply_received' value to the
notificationtype Postgres enum type.

Same issue as app/migrate_add_advisor_flagged_enum_value.py: NotificationType
is declared with SAEnum() in models.py, which creates a native Postgres
enum TYPE. Adding a new Python enum value (NotificationType.REPLY_RECEIVED)
does not retroactively add it to an already-existing Postgres enum type -
without this migration, the new "every reply" alert (notify_reply in
notification_service.py) would fail in production the first time a
non-hot reply came in, with something like:
    invalid input value for enum notificationtype: "reply_received"
despite working fine locally against SQLite, which doesn't enforce enum
types at the database level at all.

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
        sql = "ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'reply_received';"
        print(f"Running: {sql}")
        conn.execute(text(sql))

    print("\nMigration complete. 'reply_received' is now a valid notificationtype value (or already was).")


if __name__ == "__main__":
    run_migration()
