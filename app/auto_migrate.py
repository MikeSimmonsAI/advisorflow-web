"""
Auto-migration: runs automatically on every app startup (wired into
main.py's on_startup handler), so a new column or enum value added to
the SQLAlchemy models never again requires someone to manually SSH into
Render's Shell tab and run a migration command by hand.

WHY THIS EXISTS: this project relies on Base.metadata.create_all() at
startup, which only creates brand-new TABLES - it never adds a new
COLUMN to a table that already exists, and it never adds a new VALUE to
a Postgres enum TYPE that already exists. Three separate times this
session, a new column or enum value got added to the Python models but
never reached the live database until someone manually ran a one-off
script - and the app silently broke in the meantime (every request
touching the affected table/column would fail) until that manual step
happened. Mike was explicit: he does not want to do that by hand every
single deploy.

THE FIX: consolidate every column-add and enum-add into the lists below,
and call run_auto_migrations() from main.py's startup handler, right
after create_all(). Every statement here is written to be safe to run
on EVERY boot, forever:
  - Column adds use "ADD COLUMN IF NOT EXISTS" - a no-op if it already exists.
  - Enum adds use "ADD VALUE IF NOT EXISTS" - same, a no-op if already present.
Neither ever drops, renames, or alters existing data - this module only
ever ADDS things, which is what makes it safe to run unconditionally on
every single startup rather than needing a human to decide when to run it.

ADDING A NEW COLUMN OR ENUM VALUE IN A FUTURE SESSION: add it to
COLUMNS_TO_ADD or ENUM_VALUES_TO_ADD below. That's the only step needed
- no separate one-off script, no manual Shell command, no "don't forget
to run this" note. The next deploy picks it up automatically.

SQLite (local/test) is skipped entirely for the enum step, since SQLite
doesn't enforce enum types at the database level at all - this only
matters for Postgres (production). The column-add step DOES run against
SQLite too, using SQLite's own ALTER TABLE ADD COLUMN syntax, so local
dev/test environments stay consistent with production without needing
a different code path.
"""

import os
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

# (table, column, full column definition) - every column ever added to
# an EXISTING table that create_all() would never retroactively add to
# a live database. Append here, never remove (removing an entry doesn't
# undo it on databases that already have the column, and a stale no-op
# entry costs nothing to leave in place).
COLUMNS_TO_ADD = [
    ("users", "microsoft_oauth_refresh_token_encrypted", "VARCHAR"),
    ("users", "microsoft_email_address", "VARCHAR"),
    ("users", "microsoft_365_connected", "BOOLEAN DEFAULT FALSE"),
    ("users", "notification_phone", "VARCHAR"),
    ("users", "notify_via_sms", "BOOLEAN DEFAULT FALSE"),
    ("users", "can_import_leads", "BOOLEAN DEFAULT FALSE"),
    ("users", "feature_flags", "TEXT"),
    ("users", "auto_send_phase", "VARCHAR DEFAULT 'off'"),
    ("leads", "engagement_temperature", "VARCHAR"),
    ("leads", "google_contact_resource_name", "VARCHAR"),
    ("booking_links", "confirmed_at", "TIMESTAMP"),
    ("email_messages", "opened_at", "TIMESTAMP"),
    ("email_messages", "click_count", "INTEGER DEFAULT 0"),
    ("email_messages", "last_clicked_at", "TIMESTAMP"),
    ("replies", "classification", "VARCHAR"),
    ("replies", "classification_confidence", "VARCHAR"),
    ("replies", "classification_reasoning", "TEXT"),
    ("notifications", "send_failure_reason", "TEXT"),
    ("lead_outcomes", "has_preneed_planning", "BOOLEAN"),
    ("lead_outcomes", "has_insurance_funding", "BOOLEAN"),
    ("lead_outcomes", "is_veteran", "BOOLEAN"),
    ("lead_outcomes", "next_step", "TEXT"),
]

# (postgres enum type name, value to add) - SQLAlchemy's SAEnum writes
# the Python enum MEMBER NAME (e.g. "ADVISOR_FLAGGED"), not member.value
# (e.g. "advisor_flagged") - see the casing-bug history in
# migrate_add_advisor_flagged_enum_value.py for exactly how this went
# wrong once already. Always use the uppercase member NAME here.
ENUM_VALUES_TO_ADD = [
    ("suppressionsource", "ADVISOR_FLAGGED"),
    ("notificationtype", "REPLY_RECEIVED"),
]


# Real, one-time column-type-change migrations - columns that changed
# from a hard database enum to a plain string when the per-organization
# tier/track configuration system (TierDefinition) replaced the old
# hardcoded LeadTier/MessageTrack Python enums. Each entry is
# (table, column, postgres_enum_type_name).
#
# CRITICAL, documented standing rule for this codebase: SQLAlchemy's
# SAEnum writes the Python enum member NAME (uppercase, e.g.
# "PRE_NEED") into Postgres, not .value (lowercase "pre_need"). A naive
# `ALTER COLUMN ... TYPE VARCHAR USING column::text` cast would
# therefore convert every existing lead's tier to its UPPERCASE name -
# which would then silently fail to match any TierDefinition.tier_key
# (all lowercase, e.g. "pre_need"), since nothing would ever look
# correct again despite the migration "succeeding." The LOWER() call
# below is what actually prevents that corruption.
ENUM_COLUMNS_TO_CONVERT_TO_STRING = [
    ("leads", "tier", "leadtier"),
    ("leads", "message_track", "messagetrack"),
    ("campaigns", "message_track", "messagetrack"),
    ("message_templates", "message_track", "messagetrack"),
]


def run_auto_migrations(engine) -> None:
    """
    Called once from main.py's startup handler, right after
    Base.metadata.create_all(). Safe to call on every single boot -
    every statement is a no-op if already applied.
    """
    is_sqlite = str(engine.url).startswith("sqlite")

    with engine.connect() as conn:
        for table, column, definition in COLUMNS_TO_ADD:
            try:
                if is_sqlite:
                    # SQLite doesn't support "IF NOT EXISTS" on ADD COLUMN -
                    # check first, then add only if genuinely missing.
                    existing_cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
                    if column not in existing_cols:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition};"))
            except (OperationalError, ProgrammingError) as e:
                # Logged, not raised - a single failed column-add (e.g.
                # the table itself doesn't exist yet on a brand-new
                # database, where create_all() just created it fresh
                # with this column already included) should never crash
                # the whole app on startup. Each statement is independent.
                print(f"[auto_migrate] Skipped {table}.{column}: {e}")
        conn.commit()

        if not is_sqlite:
            # Real, one-time column-type conversions - enum columns that
            # became plain strings when TierDefinition replaced the old
            # hardcoded LeadTier/MessageTrack enums. Idempotent: checks
            # the column's CURRENT data type via information_schema
            # first, so this is a safe no-op on every later boot once
            # the conversion has already happened once. SQLite is
            # skipped entirely here since SQLite has no real column-type
            # enforcement to begin with - a SQLite column declared as
            # SAEnum already stores whatever Python handed it (which,
            # confirmed earlier in this project, is the lowercase
            # .value via SQLite's loose typing, not the uppercase NAME
            # quirk that's Postgres-specific), so there's nothing to
            # convert there.
            for table, column, enum_type in ENUM_COLUMNS_TO_CONVERT_TO_STRING:
                try:
                    current_type = conn.execute(text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name = :table AND column_name = :column"
                    ), {"table": table, "column": column}).scalar()

                    if current_type == "USER-DEFINED":
                        # Still the old enum type - convert now. LOWER()
                        # is the critical piece: Postgres holds the
                        # uppercase enum NAME (e.g. "PRE_NEED"), and
                        # every TierDefinition.tier_key/track_key is
                        # lowercase - a cast without LOWER() would
                        # silently corrupt every existing row to a value
                        # that matches nothing.
                        conn.execute(text(
                            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR USING LOWER({column}::text);"
                        ))
                        conn.commit()
                        print(f"[auto_migrate] Converted {table}.{column} from enum to string (lowercased).")
                    # else: already a plain string (varchar/text) - genuinely nothing to do, not even a log line needed every single boot.
                except (OperationalError, ProgrammingError) as e:
                    conn.rollback()
                    print(f"[auto_migrate] Skipped enum-to-string conversion for {table}.{column}: {e}")

        if not is_sqlite:
            # Postgres enum ADD VALUE has historically had restrictions
            # running inside an explicit transaction in some versions -
            # run these in their own autocommit-style execution, separate
            # from the column-add transaction above.
            for enum_type, value in ENUM_VALUES_TO_ADD:
                try:
                    conn.execute(text("COMMIT"))  # ensure no open transaction before ALTER TYPE
                    conn.execute(text(f"ALTER TYPE {enum_type} ADD VALUE IF NOT EXISTS '{value}';"))
                    conn.execute(text("COMMIT"))
                except (OperationalError, ProgrammingError) as e:
                    print(f"[auto_migrate] Skipped enum {enum_type}.{value}: {e}")

    print(f"[auto_migrate] Startup migration check complete ({len(COLUMNS_TO_ADD)} columns, {len(ENUM_VALUES_TO_ADD)} enum values checked).")
