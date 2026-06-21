"""
seed_registry_from_sent_log.py
Imports FINAL_sent_log.csv (from rebuild_sent_log.py) into the web app's
ContactRegistry table, so everyone the OLD desktop/ADB pipeline already
texted is respected by the NEW web app's dedup engine - prevents
re-contacting the ~1,150 protected numbers from the old system.

IMPORTANT LIMITATION: ContactRegistry's dedup key is phone + last_name,
but FINAL_sent_log only has phone numbers (no last name, since it's
reconstructed from the phone's raw SMS outbox which doesn't carry contact
metadata). This script registers each phone number against a placeholder
"__unknown__" last name bucket so it still blocks future re-sends to that
EXACT phone number, but it can't enforce the full phone+lastname dedup key
match for these historical entries until/unless they're cross-referenced
against the actual CRM lead data to recover last names.

If you have the original CRM export(s) those sends came from, a better
path is re-importing those Excel files through the normal
/leads/upload/confirm flow instead of this script - that captures full
name+phone+tier and properly populates the dedup registry with accurate
last names. Use this script only as a stopgap for numbers you can't trace
back to source data.

USAGE:
    python seed_registry_from_sent_log.py --csv FINAL_sent_log.csv --org-slug restland
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.deps import SessionLocal
from app.models.models import Organization, ContactRegistry
from app.services.dedup_service import normalize_phone, normalize_last_name, PLACEHOLDER_LAST_NAME


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Path to FINAL_sent_log.csv")
    parser.add_argument("--org-slug", default="restland")
    args = parser.parse_args()

    db = SessionLocal()
    org = db.query(Organization).filter(Organization.slug == args.org_slug).first()
    if not org:
        print(f"Organization with slug '{args.org_slug}' not found. Run app/seed.py first.")
        sys.exit(1)

    added, skipped = 0, 0
    with open(args.csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            phone_norm = normalize_phone(row.get("phone_normalized") or row.get("phone_raw", ""))
            if not phone_norm:
                skipped += 1
                continue

            existing = db.query(ContactRegistry).filter(
                ContactRegistry.organization_id == org.id,
                ContactRegistry.normalized_phone == phone_norm,
                ContactRegistry.normalized_last_name == PLACEHOLDER_LAST_NAME,
            ).first()
            if existing:
                skipped += 1
                continue

            entry = ContactRegistry(
                organization_id=org.id,
                normalized_phone=phone_norm,
                normalized_last_name=PLACEHOLDER_LAST_NAME,
            )
            db.add(entry)
            added += 1

    db.commit()
    db.close()
    print(f"Added {added} historical phone numbers to the dedup registry, skipped {skipped} duplicates/blank rows.")
    print()
    print("NOTE: these are registered under a placeholder last name bucket, so they")
    print("only block re-sends to that exact phone number, not the full phone+lastname")
    print("dedup match used for new imports. See script docstring for details.")


if __name__ == "__main__":
    main()
