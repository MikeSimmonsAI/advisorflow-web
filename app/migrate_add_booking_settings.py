"""
One-time migration: adds booking/scheduling settings columns to the users table.

These columns let each advisor configure their own appointment availability
(working hours, duration, buffer time, max bookings per day, timezone, and
a custom confirmation message shown to leads after they book).

USAGE: run once against the live database via Render's Shell tab:
    python -m app.migrate_add_booking_settings
"""

import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")

COLUMNS_TO_ADD = [
    ("users", "appt_duration_minutes", "INTEGER DEFAULT 30"),
    ("users", "buffer_minutes", "INTEGER DEFAULT 0"),
    ("users", "max_bookings_per_day", "INTEGER DEFAULT 8"),
    ("users", "available_start_time", "VARCHAR(8) DEFAULT '09:00'"),
    ("users", "available_end_time", "VARCHAR(8) DEFAULT '17:00'"),
    ("users", "available_days", "VARCHAR(20) DEFAULT '0,1,2,3,4'"),
    ("users", "booking_timezone", "VARCHAR(60) DEFAULT 'America/Chicago'"),
    ("users", "booking_confirmation_message", "TEXT"),
]


def run_migration():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set. Run this in the Render Shell.")
        return

    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        for table, column, definition in COLUMNS_TO_ADD:
            sql = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition};"
            print(f"Running: {sql}")
            conn.execute(text(sql))
        conn.commit()

    print("\nMigration complete — booking settings columns added.")


if __name__ == "__main__":
    run_migration()
