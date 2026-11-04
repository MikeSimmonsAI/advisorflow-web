#!/usr/bin/env python
"""Move an existing single-location customer onto the program shell (Issue #507).

Dry run by default: prints the preflight and baseline counts, writes nothing.

    python scripts/single_location_program_cutover.py --org-id <id>                 # preflight only
    python scripts/single_location_program_cutover.py --org-id <id> --apply \
        --hero-title "Restland" --contact-name "Mike Simmons"
    python scripts/single_location_program_cutover.py --org-id <id> --reconcile
    python scripts/single_location_program_cutover.py --org-id <id> --rollback <run_id>

Sends nothing and switches nothing on. See
app/services/programs/single_location_cutover.py for exactly what is written.
Take a database backup first (docs/restland/CUTOVER.md).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-id", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--reconcile", action="store_true")
    ap.add_argument("--rollback", metavar="RUN_ID")
    ap.add_argument("--hero-title")
    ap.add_argument("--contact-name")
    ap.add_argument("--actor-id")
    a = ap.parse_args()
    from app.deps import SessionLocal
    from app.models.models import Organization
    from app.services.programs import single_location_cutover as C
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == a.org_id).first()
        if a.rollback:
            out = C.rollback(db, org, a.rollback)
        elif a.apply:
            out = C.apply(db, org, actor_id=a.actor_id, hero_title=a.hero_title, contact_name=a.contact_name)
        elif a.reconcile:
            out = C.reconcile(db, org)
        else:
            out = C.preflight(db, org)
        print(json.dumps(out, indent=2, default=str))
        return 0
    except C.CutoverRefused as e:
        db.rollback()
        print("REFUSED: %s" % e)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
