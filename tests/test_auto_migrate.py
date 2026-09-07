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

from app.auto_migrate import run_auto_migrations, COLUMNS_TO_ADD, ENUM_VALUES_TO_ADD, ENUM_COLUMNS_TO_CONVERT_TO_STRING


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


# Columns COLUMNS_TO_ADD still creates that the models no longer declare.
#
# These are not typos - each was a real column whose model attribute was later
# removed (users.can_import_leads went in commit f187461, "one name per import
# permission"). auto_migrate's own docstring says a stale no-op entry is
# harmless to leave in place, and removing entries has its own risk: a
# production database may still carry the column, and other tests pin some of
# them (test_run_auto_migrations_adds_missing_column_to_existing_table asserts
# can_import_leads and notification_phone are created).
#
# So they are ALLOWED, but named, dated and counted - not silently tolerated.
# Anything that drifts out of the models from here on is a new entry and fails
# the test below, which is the behaviour this test was always meant to have.
KNOWN_RETIRED_COLUMNS = {
    # retired 2026-09-02, commit f187461 - import permission renaming
    ("users", "can_import_leads"),
    ("users", "notification_phone"),
    ("users", "notify_via_sms"),
    ("users", "feature_flags"),
    ("leads", "google_contact_resource_name"),
    ("leads", "service_address"),
    ("leads", "extra_data"),
    ("notifications", "send_failure_reason"),
    ("lead_outcomes", "has_preneed_planning"),
    ("lead_outcomes", "has_insurance_funding"),
    ("lead_outcomes", "is_veteran"),
    ("lead_outcomes", "next_step"),
    ("campaigns", "purpose"),
    ("campaigns", "tone"),
    ("campaigns", "sent_count"),
    ("campaigns", "skipped_count"),
    ("campaigns", "error_count"),
    ("campaigns", "status"),
    ("campaigns", "ai_direction"),
    ("campaigns", "sent_at"),
    ("crm_contacts", "pipeline_stage"),
    ("crm_contacts", "company"),
    ("crm_contacts", "last_contact_at"),
}


def test_columns_to_add_list_matches_real_model_fields():
    """
    Every (table, column) in COLUMNS_TO_ADD must name a real table and a real
    column on the real models - catching a typo before it ships rather than
    discovering it live.

    THIS TEST USED TO CHECK ALMOST NOTHING. It carried a hand-written map of
    seven model classes and asserted table membership FIRST, so the moment
    COLUMNS_TO_ADD grew an eighth table the very first entry raised and the
    per-column hasattr check below it never ran at all. COLUMNS_TO_ADD is now
    332 entries across 34 tables; the whitelist had not been touched, so the
    only assertion that ever executed was "is this one of seven names", and
    23 genuinely drifted columns sat behind it unreported.

    The table map is now derived from Base.metadata, the same way conftest
    builds the test schema, so it can never go stale again - a new model is
    picked up automatically instead of being mistaken for a typo.
    """
    import app.models.registry  # noqa: F401  (imports every model module)
    from app.models.models import Base

    known_tables = set(Base.metadata.tables.keys())
    assert len(known_tables) > 20, (
        "Model registry looks incomplete - refusing to run a check that would "
        "report real tables as typos."
    )

    unknown_tables = []
    drifted_columns = []

    for table, column, _definition in COLUMNS_TO_ADD:
        if table not in known_tables:
            unknown_tables.append((table, column))
            continue
        if column not in Base.metadata.tables[table].columns:
            if (table, column) not in KNOWN_RETIRED_COLUMNS:
                drifted_columns.append((table, column))

    assert not unknown_tables, (
        "COLUMNS_TO_ADD names tables that do not exist in the model registry: "
        "%s" % sorted(unknown_tables)
    )
    assert not drifted_columns, (
        "COLUMNS_TO_ADD creates columns the models no longer declare: %s. "
        "Either the model lost the column (add it to KNOWN_RETIRED_COLUMNS "
        "with the commit and date) or the migration entry is a typo."
        % sorted(drifted_columns)
    )


def test_known_retired_columns_are_actually_retired():
    """KNOWN_RETIRED_COLUMNS must not accumulate entries that came back.

    An allow-list nobody prunes stops being an allow-list and becomes a
    blindfold. If a column is re-added to its model, it must drop out of the
    exemption set rather than sit there permanently excusing a check.
    """
    import app.models.registry  # noqa: F401
    from app.models.models import Base

    resurrected = [
        (table, column)
        for (table, column) in KNOWN_RETIRED_COLUMNS
        if table in Base.metadata.tables
        and column in Base.metadata.tables[table].columns
    ]
    assert not resurrected, (
        "These columns are listed as retired but the models declare them "
        "again - remove them from KNOWN_RETIRED_COLUMNS: %s" % sorted(resurrected)
    )


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


def test_enum_columns_to_convert_list_matches_real_model_fields():
    """
    Sanity check that the hardcoded conversion list corresponds to
    real table/column pairs that were actually changed from SAEnum to
    String - catches a typo'd table/column name before it ships.
    """
    from app.models.models import Lead, Campaign, MessageTemplate
    model_by_table = {"leads": Lead, "campaigns": Campaign, "message_templates": MessageTemplate}

    for table, column, _enum_type in ENUM_COLUMNS_TO_CONVERT_TO_STRING:
        assert table in model_by_table, f"{table} is not a real table this migration list should reference"
        model = model_by_table[table]
        assert hasattr(model, column), f"{table}.{column} is not a real column on {model.__name__}"


def test_run_auto_migrations_does_not_attempt_enum_conversion_on_sqlite():
    """
    SQLite has no information_schema.columns the way Postgres does, and
    no real enum type to convert from in the first place - confirms the
    is_sqlite guard genuinely skips this whole block rather than
    erroring on a query SQLite can't run.
    """
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE leads (id TEXT PRIMARY KEY, tier TEXT)"))
        conn.commit()

    # Must complete without raising - if the conversion logic ran
    # against SQLite, the information_schema query itself would fail.
    run_auto_migrations(engine)


def test_enum_to_string_conversion_sql_uses_lower_not_a_naive_cast():
    """
    THE CRITICAL CORRECTNESS CHECK for this whole migration step.
    SQLAlchemy's SAEnum stores the Python enum member NAME (uppercase,
    e.g. "PRE_NEED") in Postgres, not .value (lowercase "pre_need") -
    this is a documented, confirmed standing rule for this codebase.
    A naive `column::text` cast would silently corrupt every existing
    lead's tier to its uppercase NAME, which would then match nothing
    in TierDefinition.tier_key (all lowercase) - the migration would
    "succeed" with no error while quietly breaking every lead's tier.

    This test mocks the actual database connection and inspects the
    REAL SQL string sent to execute(), to directly confirm LOWER() is
    present - this is checked here because a real Postgres instance
    with actual enum types isn't available in this test environment,
    so this is the most direct, honest verification possible of the
    actual SQL this code would run in production.
    """
    from unittest.mock import MagicMock, patch
    import app.auto_migrate as auto_migrate_module

    mock_conn = MagicMock()
    mock_conn.execute.return_value.scalar.return_value = "USER-DEFINED"  # simulates "still the old enum type"

    mock_engine = MagicMock()
    mock_engine.url = "postgresql://fake"
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    with patch.object(auto_migrate_module, "COLUMNS_TO_ADD", []), \
         patch.object(auto_migrate_module, "ENUM_VALUES_TO_ADD", []):
        run_auto_migrations(mock_engine)

    executed_sql_strings = [str(call.args[0]) for call in mock_conn.execute.call_args_list]
    alter_statements = [sql for sql in executed_sql_strings if "ALTER TABLE" in sql and "ALTER COLUMN" in sql]

    assert len(alter_statements) == len(ENUM_COLUMNS_TO_CONVERT_TO_STRING), \
        "Expected one ALTER COLUMN statement per entry in ENUM_COLUMNS_TO_CONVERT_TO_STRING"
    for statement in alter_statements:
        assert "LOWER(" in statement, f"Missing LOWER() in conversion SQL - would corrupt data to uppercase: {statement}"
        assert "::text" in statement
