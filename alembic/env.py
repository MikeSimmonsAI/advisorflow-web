from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os
import sys

# Make sure the app package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.models import Base
# EVERY model module, on that same Base, BEFORE target_metadata is read.
#
# autogenerate diffs the live database against Base.metadata, and metadata
# only contains tables whose module has been imported. Importing models.py
# alone gave a PARTIAL picture, so autogenerate saw tables and columns that
# exist in the database but not in its idea of the schema - and proposed
# DROPPING them. alembic/versions/02907fcdb80c_initial_schema.py contains
# exactly that shape of damage (op.drop_column on leads, lead_outcomes and
# others), which is what a partial metadata produces.
#
# app/models/registry.py is the same single registry app/main.py and
# tests/conftest.py import. There is no second list to keep in step.
import app.models.registry  # noqa: F401,E402  (imported for side effects)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url():
    """Pull DATABASE_URL from environment — never hardcode credentials."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    # SQLAlchemy requires postgresql:// not postgres:// (Render uses the old form)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_url()
    connectable = engine_from_config(
        {"sqlalchemy.url": url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
