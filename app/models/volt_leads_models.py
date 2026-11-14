"""VOLT consumer leads (DEV-4) - the tables for the consumer eligibility funnel
and the agent dashboard live here, separate from the recruiting tables in
volt_models.py.

Registered in app/models/registry.py, so `python -m app.migrate` (pre-deploy)
and app start-up create any NEW table defined here with create_all. Rules for
this file (enforced by review and by the relay's file scope):
  - new tables only, every table name starting with `volt_lead`;
  - no new columns on existing tables (that needs auto_migrate, outside scope);
  - no Alembic revisions (production does not run Alembic; see app/migrate.py).
Empty until DEV-4 adds the models.
"""
from app.models.models import Base  # noqa: F401  (models defined here subclass Base)
