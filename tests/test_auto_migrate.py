"""
Tests for app/auto_migrate.py - the permanent fix for "I don't want to
manually run a migration command every deploy." Wired into main.py's
startup handler, this runs automatically on every app boot and adds any
column/enum value that exists in the Python models but hasn't reached
the live database yet.

This replaced three separate one-off manual migration scripts
(migrate_add_missing_columns.py, migrate_add_advisor_flagged_enum_value.py,
migrate_add_reply_received_enum_value.py) after the second production
outage this session caused by a missing column that nobody remembered
to migrate by hand. These tests exist specifically so that gap can never
silently reopen.
"""

import os
from sqlalchemy import create_engine, text

from app.auto_migrate import run_auto_migrations, COLUMNS_TO_ADD, ENUM_VALUES_TO_ADD


def _fresh_sqlite_engine(tmp_path, name="test.db"):
    db_path = str(tmp_path / name)
    return create_engine(f"sqlite:///{db_path}")


def test_run_auto_migrations_adds_missing_column_to_existing_table(tmp_path):
    """
    The actual real-world scenario that broke production twice: a table
    that already exists but predates a new column added to the model.
    """
    engine = _fresh_sqlite_engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)"))
        conn.commit()
        cols_before = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
    assert "can_import_leads" not in cols_before
    assert "notification_phone" not in cols_before

    run_auto_migrations(engine)

    with engine.connect() as conn:
        cols_after = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
    assert "can_import_leads" in cols_after
    assert "notification_phone" in cols_after
    assert "microsoft_365_connected" in cols_after


def test_run_auto_migrations_is_safe_to_run_twice(tmp_path):
    """Idempotency - running it again after columns already exist must not error."""
    engine = _fresh_sqlite_engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)"))
        conn.commit()

    run_auto_migrations(engine)
    run_auto_migrations(engine)  # must not raise on the second call

    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
    assert "can_import_leads" in cols


def test_run_auto_migrations_does_not_crash_when_a_table_does_not_exist_yet(tmp_path):
    """
    A column-add targeting a table that doesn't exist (e.g. a brand-new
    database where create_all() hasn't run yet, or a typo in the table
    name) must be logged and skipped, never crash the whole startup.
    """
    engine = _fresh_sqlite_engine(tmp_path)
    # No tables created at all - every single column-add should fail
    # individually and be caught, not raise out of run_auto_migrations.
    run_auto_migrations(engine)  # must not raise


def test_run_auto_migrations_skips_enum_step_entirely_on_sqlite(tmp_path):
    """SQLite doesn't enforce enum types at the DB level - the enum-add step must be a clean no-op, not an error."""
    engine = _fresh_sqlite_engine(tmp_path)
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)"))
        conn.commit()

    # Should complete without attempting any ALTER TYPE statements at all.
    run_auto_migrations(engine)


def test_columns_to_add_list_matches_real_model_fields():
    """
    Sanity check that the hardcoded list actually corresponds to real
    columns on the real models - catches a typo'd table/column name
    before it ships, rather than discovering it live.
    """
    from app.models.models import User, Lead, Reply, Notification

    model_by_table = {
        "users": User, "leads": Lead, "replies": Reply, "notifications": Notification,
    }

    for table, column, _definition in COLUMNS_TO_ADD:
        assert table in model_by_table, f"Unknown table '{table}' in COLUMNS_TO_ADD"
        model = model_by_table[table]
        assert hasattr(model, column), f"{model.__name__} has no attribute '{column}' - check for a typo in COLUMNS_TO_ADD"


def test_enum_values_to_add_use_uppercase_member_names_not_lowercase_values():
    """
    Regression guardrail for the exact casing bug that caused a real
    production outage: every value in ENUM_VALUES_TO_ADD must be the
    Python enum MEMBER NAME (uppercase), not member.value (lowercase) -
    SQLAlchemy's SAEnum writes the name to the database by default.
    """
    from app.models.models import SuppressionSource, NotificationType

    enum_by_type_name = {
        "suppressionsource": SuppressionSource,
        "notificationtype": NotificationType,
    }

    for enum_type_name, value in ENUM_VALUES_TO_ADD:
        assert enum_type_name in enum_by_type_name, f"Unknown enum type '{enum_type_name}'"
        enum_class = enum_by_type_name[enum_type_name]
        member_names = {member.name for member in enum_class}
        assert value in member_names, (
            f"'{value}' is not a member NAME of {enum_class.__name__} - "
            f"if this is a member VALUE (lowercase), that's the exact casing bug that broke production once already."
        )
        # Extra-explicit check: the value must match the member's .name, not .value
        member = enum_class[value]
        assert member.name == value
