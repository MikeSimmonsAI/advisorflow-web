"""
One-time manual migration: adds the new 'advisor_flagged' value to the
suppressionsource Postgres enum type.

REAL ISSUE THIS PREVENTS: SuppressionSource is declared with SAEnum() in
models.py, which on Postgres creates a native enum TYPE at the database
level (unlike SQLite, where it's just stored as a string with no real
type enforcement). Adding a new value to the Python enum
(SuppressionSource.ADVISOR_FLAGGED) does NOT retroactively add that
value to an already-existing Postgres enum type - Base.metadata.create_all()
only creates new tables, it doesn't ALTER an existing enum type. Without
this migration, the new quick-DNC button (POST /leads/{lead_id}/mark-dnc)
would fail in production with something like:
    invalid input value for enum suppressionsource: "advisor_flagged"
the first time anyone actually clicked it, despite working fine locally
against SQLite.

USAGE: run this once, manually, against the live database - e.g. via
Render's Shell tab on advisorflow-backend:
    python -m app.migrate_add_advisor_flagged_enum_value

Idempotent: Postgres 9.1+ supports "ALTER TYPE ... ADD VALUE IF NOT
EXISTS", so running this multiple times is always safe.

NOTE: this only matters for Postgres (production). SQLite (local/test)
doesn't enforce enum types at the database level at all, so this script
is a no-op there - which is also why this exact failure mode is the kind
of thing that passes every local test and only breaks live.
"""

import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")


def run_migration():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set. This must be run in an environment with the real database connection configured.")
        return

    if DATABASE_URL.startswith("sqlite"):
        print("DATABASE_URL is SQLite - enum types aren't enforced at the DB level here, nothing to migrate. Skipping.")
        return

    engine = create_engine(DATABASE_URL, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        # ALTER TYPE ... ADD VALUE has historically had restrictions
        # running inside an explicit transaction block on some Postgres
        # versions - autocommit avoids that entirely rather than
        # depending on which Postgres version Render happens to be on.
        sql = "ALTER TYPE suppressionsource ADD VALUE IF NOT EXISTS 'advisor_flagged';"
        print(f"Running: {sql}")
        conn.execute(text(sql))

    print("\nMigration complete. 'advisor_flagged' is now a valid suppressionsource value (or already was).")


if __name__ == "__main__":
    run_migration()
