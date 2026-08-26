"""Proof that the tenant-bridge columns reach a database that ALREADY has the
integration tables.

WHY THIS EXISTS. `Base.metadata.create_all()` creates whole TABLES and never
adds a column to one that already exists. `integration_credentials` and
`integration_request_logs` were created by the deploy before this one, so on
production they exist WITHOUT the tenant columns. If the entries in
`auto_migrate.COLUMNS_TO_ADD` were wrong, nothing would fail at deploy time —
it would fail the first time somebody minted a tenant key, which is exactly
when nobody wants to find out.

So this builds the OLD schema by hand, runs the real migration over it, and
checks the columns appeared.

Temp SQLite. Never touches production.

    python scripts/smoke_integration_migration.py
"""
import os
import sys
import shutil
import tempfile

TMP = tempfile.mkdtemp(prefix="intgmig_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                        # noqa: E402
from app.deps import engine                                        # noqa: E402
from app.auto_migrate import (                                     # noqa: E402
    COLUMNS_TO_ADD, NULLABILITY_TO_RELAX, run_auto_migrations,
)

FAILURES = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:300]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


# The schema as it exists on production RIGHT NOW: the pre-tenant shape.
OLD_CREDENTIALS = """
CREATE TABLE integration_credentials (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    key_prefix VARCHAR NOT NULL UNIQUE,
    key_hash VARCHAR NOT NULL,
    brand_sales_org_id VARCHAR NOT NULL,
    default_advisor_user_id VARCHAR,
    allowed_advisor_ids TEXT,
    rate_limit_per_minute INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL,
    revoked_at TIMESTAMP,
    last_used_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    created_by VARCHAR,
    note TEXT
)
"""

OLD_LOGS = """
CREATE TABLE integration_request_logs (
    id VARCHAR PRIMARY KEY,
    credential_id VARCHAR,
    integration_name VARCHAR,
    key_prefix VARCHAR,
    action VARCHAR NOT NULL,
    brand_sales_org_id VARCHAR,
    advisor_user_id VARCHAR,
    external_ref VARCHAR,
    appointment_id VARCHAR,
    success BOOLEAN NOT NULL,
    status_code INTEGER,
    detail TEXT,
    occurred_at TIMESTAMP NOT NULL
)
"""

NEEDED = [
    ("integration_credentials", "organization_id"),
    ("integration_request_logs", "organization_id"),
    ("integration_request_logs", "booking_link_id"),
    ("integration_request_logs", "lead_id"),
]


def columns(conn, table):
    return {r[1] for r in conn.execute(text("PRAGMA table_info(%s)" % table))}


def main():
    print("\n[1] The pre-tenant schema, as production has it today")
    with engine.connect() as conn:
        conn.execute(text(OLD_CREDENTIALS))
        conn.execute(text(OLD_LOGS))
        conn.commit()
        before_c = columns(conn, "integration_credentials")
        before_l = columns(conn, "integration_request_logs")
    check("the old tables exist without the tenant columns",
          "organization_id" not in before_c and "booking_link_id" not in before_l,
          [sorted(before_c), sorted(before_l)])

    print("\n[2] Every new column is registered for migration")
    registered = {(t, c) for t, c, _ in COLUMNS_TO_ADD}
    for table, col in NEEDED:
        check("%s.%s is in COLUMNS_TO_ADD" % (table, col),
              (table, col) in registered)
    check("THE BRAND SCOPE IS REGISTERED FOR A NOT-NULL RELAX",
          ("integration_credentials", "brand_sales_org_id") in
          {(t, c) for t, c in NULLABILITY_TO_RELAX})

    print("\n[3] Running the real migration over the old schema")
    run_auto_migrations(engine)
    with engine.connect() as conn:
        after_c = columns(conn, "integration_credentials")
        after_l = columns(conn, "integration_request_logs")
    for table, col in NEEDED:
        have = after_c if table == "integration_credentials" else after_l
        check("%s.%s EXISTS AFTER MIGRATION" % (table, col), col in have,
              sorted(have))

    print("\n[4] It is safe to run again, as it will be on every boot")
    run_auto_migrations(engine)
    with engine.connect() as conn:
        again = columns(conn, "integration_credentials")
    check("a second run changes nothing and does not raise", again == after_c)

    print()
    if FAILURES:
        print("  %d FAILURE(S): %s" % (len(FAILURES), ", ".join(FAILURES[:6])))
        shutil.rmtree(TMP, ignore_errors=True)
        sys.exit(1)
    print("  ALL INTEGRATION MIGRATION CHECKS PASSED")
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
