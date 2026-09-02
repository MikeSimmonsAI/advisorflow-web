"""
migrate_add_import_tables.py
-----------------------------
Creates the import_batches and import_staged_rows tables if they do not
already exist, and adds any columns that may be missing from a partial
previous run.

SAFE TO RUN MULTIPLE TIMES — all DDL is written as CREATE IF NOT EXISTS /
ADD COLUMN IF NOT EXISTS.

Run in Render shell:
    python -m app.migrate_add_import_tables

Or directly:
    python app/migrate_add_import_tables.py
"""

import os
import sys

from sqlalchemy import create_engine, text


DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable not set.", file=sys.stderr)
    sys.exit(1)


STEPS = [
