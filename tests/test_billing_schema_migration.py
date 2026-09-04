"""An existing database created BEFORE the Organization billing columns
existed must boot and self-upgrade.

THE DEFECT THIS FILE PINS DOWN

`models.Organization` declares stripe_customer_id, stripe_subscription_id,
stripe_plan_interval and billing_status. `Base.metadata.create_all()` gave them
free to every database created after they were written, and never adds a column
to a table that already exists - so a database created before them never got
them, and every ORM query against Organization (the workspace membership
backfill is simply the first one after startup) failed with

    psycopg2.errors.UndefinedColumn:
    column organizations.stripe_customer_id does not exist

on EVERY boot, not just the first. They were never in COLUMNS_TO_ADD, so the
one mechanism that exists to fix exactly this could never fix it.

Two separate things are asserted here, because two separate things were wrong:

  1. the four columns are now in the real migration list, and a stale database
     genuinely gains them by running the repository's real migration path
  2. a failing entry ELSEWHERE in COLUMNS_TO_ADD no longer discards the batch

(2) is what makes (1) hold on a real database. The old loop ran the whole list
in one transaction with no rollback in its handler, so on Postgres the first
failure aborted the transaction and the closing COMMIT was executed as a
ROLLBACK - throwing away every column that had already succeeded. A database
with pre-existing drift is precisely the database that needs the upgrade, so
appending to the list without fixing that would not have survived a real boot.
"""

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auto_migrate
from app.models.models import Base, Organization
import app.models.registry  # noqa: F401  (every model on one Base, as main.py does)


BILLING_COLUMNS = (
    "stripe_customer_id",
    "stripe_subscription_id",
    "stripe_plan_interval",
    "billing_status",
)


def _legacy_engine():
    """A database that looks like one created before billing existed.

    create_all() builds the current schema, then the four billing columns are
    dropped back off `organizations` - which is the state a long-lived
    production or staging database is actually in.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        for column in BILLING_COLUMNS:
            conn.execute(text(f"ALTER TABLE organizations DROP COLUMN {column}"))
        conn.commit()
    return engine


def _columns(engine, table="organizations"):
    return {c["name"] for c in inspect(engine).get_columns(table)}


# ═════════════════════════════════════════════════════════════════════════════
# 1. The list itself
# ═════════════════════════════════════════════════════════════════════════════

def test_billing_columns_are_declared_in_the_migration_mechanism():
    """create_all() cannot add these, so COLUMNS_TO_ADD has to."""
    declared = {(t, c) for t, c, _ in auto_migrate.COLUMNS_TO_ADD}
    for column in BILLING_COLUMNS:
        assert ("organizations", column) in declared, (
            f"organizations.{column} is on the Organization model but not in "
            f"COLUMNS_TO_ADD - a pre-existing database can never gain it.")


def test_billing_columns_are_declared_nullable_with_no_default():
    """Adding a column must not write a value onto anybody's existing row."""
    for table, column, definition in auto_migrate.COLUMNS_TO_ADD:
        if table == "organizations" and column in BILLING_COLUMNS:
            assert definition == "VARCHAR", (
                f"organizations.{column} is defined as {definition!r}; a DEFAULT "
                f"or NOT NULL here would back-fill a billing state onto every "
                f"existing customer row.")


def test_model_and_migration_agree_on_the_billing_columns():
    """The model is the source of truth; the migration must cover all of it."""
    model_columns = {c.name for c in Organization.__table__.columns}
    for column in BILLING_COLUMNS:
        assert column in model_columns


# ═════════════════════════════════════════════════════════════════════════════
# 2. The real migration path against a stale database
# ═════════════════════════════════════════════════════════════════════════════

def test_stale_database_is_missing_the_columns_before_migration():
    """The fixture has to actually reproduce the defect, or nothing below means
    anything."""
    engine = _legacy_engine()
    columns = _columns(engine)
    for column in BILLING_COLUMNS:
        assert column not in columns


def test_orm_query_fails_before_migration():
    """This is the reported symptom, reproduced."""
    engine = _legacy_engine()
    session = sessionmaker(bind=engine)()
    with pytest.raises(Exception) as exc:
        session.query(Organization).first()
    assert "stripe_customer_id" in str(exc.value)
    session.close()


def test_real_migration_path_adds_the_columns():
    engine = _legacy_engine()
    auto_migrate.run_auto_migrations(engine)
    columns = _columns(engine)
    for column in BILLING_COLUMNS:
        assert column in columns, f"organizations.{column} still missing"


def test_orm_query_succeeds_after_migration():
    """The workspace membership backfill's query is an Organization query. Once
    the schema is valid it no longer raises UndefinedColumn."""
    engine = _legacy_engine()
    auto_migrate.run_auto_migrations(engine)
    session = sessionmaker(bind=engine)()
    assert session.query(Organization).all() == []
    org = Organization(name="Test Org", slug="test-org", plan="standard")
    session.add(org)
    session.commit()
    fetched = session.query(Organization).filter(Organization.slug == "test-org").one()
    assert fetched.stripe_customer_id is None
    assert fetched.stripe_subscription_id is None
    assert fetched.stripe_plan_interval is None
    assert fetched.billing_status is None
    session.close()


def test_workspace_backfill_runs_after_migration():
    """The exact call main.py makes at step 2a, on a migrated stale database."""
    from app.services import workspace_access
    engine = _legacy_engine()
    auto_migrate.run_auto_migrations(engine)
    session = sessionmaker(bind=engine)()
    report = workspace_access.backfill_from_legacy_column(session)
    assert isinstance(report, dict)
    session.close()


def test_migration_is_idempotent_on_second_run():
    engine = _legacy_engine()
    auto_migrate.run_auto_migrations(engine)
    first = _columns(engine)
    auto_migrate.run_auto_migrations(engine)
    second = _columns(engine)
    assert first == second
    for column in BILLING_COLUMNS:
        assert column in second


def test_migration_does_not_modify_existing_rows():
    """Schema addition only. An organization that existed before the migration
    keeps every value it had, and gains NULL - not a billing state."""
    engine = _legacy_engine()
    # Inserted with raw SQL on purpose: the ORM names the billing columns in
    # every INSERT, so on a pre-migration database only raw SQL can create the
    # legacy row this test needs.
    org_id = "org-existing-before-billing"
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO organizations (id, name, slug, plan, is_active) "
            "VALUES (:id, :name, :slug, :plan, 1)"),
            {"id": org_id, "name": "Existing Customer",
             "slug": "existing", "plan": "standard"})
        conn.commit()

    auto_migrate.run_auto_migrations(engine)

    session = sessionmaker(bind=engine)()
    after = session.query(Organization).filter(Organization.id == org_id).one()
    assert (after.name, after.slug, after.plan) == ("Existing Customer", "existing", "standard")
    assert after.is_active is True
    assert after.billing_status is None
    assert after.stripe_customer_id is None
    session.close()


# ═════════════════════════════════════════════════════════════════════════════
# 3. A failure earlier in the list must not discard the rest
#
# Postgres-specific behaviour, so the connection is a stub that reproduces it
# exactly: once a statement raises, every later statement raises too until
# someone rolls back, and COMMIT on an aborted transaction discards the work.
# ═════════════════════════════════════════════════════════════════════════════

class _AbortingConnection:
    """Postgres transaction semantics, in miniature."""

    def __init__(self, bad_tables=()):
        self.bad_tables = set(bad_tables)
        self.aborted = False
        self.pending = []
        self.committed = []

    def execute(self, statement):
        sql = str(statement)
        if self.aborted:
            raise ProgrammingError(
                sql, {},
                Exception("current transaction is aborted, commands ignored "
                          "until end of transaction block"))
        table = sql.split("ALTER TABLE ")[1].split(" ")[0]
        if table in self.bad_tables:
            self.aborted = True
            raise ProgrammingError(sql, {}, Exception(f'relation "{table}" does not exist'))
        self.pending.append(sql)
        return self

    def fetchall(self):
        return []

    def commit(self):
        if self.aborted:
            # Postgres executes COMMIT on an aborted transaction as ROLLBACK.
            self.pending = []
            return
        self.committed.extend(self.pending)
        self.pending = []

    def rollback(self):
        self.aborted = False
        self.pending = []


def test_failure_earlier_in_the_list_does_not_discard_later_columns():
    conn = _AbortingConnection(bad_tables={"missing_table"})
    columns = [
        ("organizations", "before_col", "VARCHAR"),
        ("missing_table", "any_col", "VARCHAR"),        # aborts the transaction
        ("organizations", "stripe_customer_id", "VARCHAR"),
        ("organizations", "billing_status", "VARCHAR"),
    ]
    auto_migrate._apply_column_adds(conn, columns, is_sqlite=False)

    committed = " ".join(conn.committed)
    assert "before_col" in committed, "a column committed before the failure was discarded"
    assert "stripe_customer_id" in committed, "the failure discarded a later column"
    assert "billing_status" in committed, "the failure discarded a later column"
    assert "missing_table" not in committed


def test_apply_column_adds_never_raises_on_a_bad_entry():
    conn = _AbortingConnection(bad_tables={"missing_table"})
    auto_migrate._apply_column_adds(
        conn, [("missing_table", "x", "VARCHAR")], is_sqlite=False)
    assert conn.committed == []


def test_every_billing_column_survives_a_poisoning_entry():
    """The real list, with drift injected ahead of the billing entries."""
    conn = _AbortingConnection(bad_tables={"drifted_table"})
    columns = ([("drifted_table", "gone", "VARCHAR")] +
               [c for c in auto_migrate.COLUMNS_TO_ADD
                if c[0] == "organizations" and c[1] in BILLING_COLUMNS])
    auto_migrate._apply_column_adds(conn, columns, is_sqlite=False)
    committed = " ".join(conn.committed)
    for column in BILLING_COLUMNS:
        assert column in committed
