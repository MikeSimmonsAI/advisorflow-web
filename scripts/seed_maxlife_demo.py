"""Seed the DEMO "Max Life Demo Agency" workspace for Max Life Command.

A thin wrapper: the seed itself lives in app/services/agency/demo_seed.py
(seed_maxlife_demo), which is also what the God-mode endpoint
POST /god/demo/maxlife calls - use that endpoint for production, where there is
no shell or database access.

IDEMPOTENT and DEMO-labelled (see the service module). Sends nothing.
REFUSES to run when APP_ENV/ENVIRONMENT says production unless --i-know-this-is-not-prod
is passed. Usage:
    python scripts/seed_maxlife_demo.py [--database-url sqlite:///demo.db]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url")
    ap.add_argument("--i-know-this-is-not-prod", action="store_true")
    args = ap.parse_args()
    env = (os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT") or "").lower()
    if env in ("prod", "production") and not args.i_know_this_is_not_prod:
        sys.exit("Refusing to seed demo data into a production environment.")
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    from app.deps import SessionLocal, engine
    import app.models.registry  # noqa: F401
    import app.models.agency_models  # noqa: F401
    from app.models.models import Base
    from app.services.agency.demo_seed import DemoSeedError, seed_maxlife_demo

    Base.metadata.create_all(bind=engine, tables=[t for n, t in Base.metadata.tables.items()
                                                  if n.startswith("agency_")])
    password = os.environ.get("MAXLIFE_DEMO_PASSWORD")  # never hard-coded
    db = SessionLocal()
    try:
        rep = seed_maxlife_demo(db, password=password)
    except DemoSeedError as e:
        db.rollback()
        sys.exit("Refused: %s" % e)
    finally:
        db.close()
    c = rep["counts"]
    print("Seeded %s (org %s): %d agents, %d prospects (%d unassigned: %s). Added this run: %s. "
          "All rows DEMO; nothing sent."
          % (rep["organization_name"], rep["organization_id"], c["agents"], c["prospects"],
             len(rep["unassigned"]), ", ".join(rep["unassigned"]) or "none",
             sum(rep["added"].values())))
    if not password:
        print("Demo users got random passwords (set MAXLIFE_DEMO_PASSWORD to choose one).")


if __name__ == "__main__":
    main()
