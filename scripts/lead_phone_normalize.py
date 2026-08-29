"""Normalise ONE lead's stored phone number, using the app's own rule.

    python scripts/lead_phone_normalize.py --lead <lead_id>
    python scripts/lead_phone_normalize.py --lead <lead_id> --apply

WHY. Phone numbers reach this system from imports, forms and typing, and some
arrive without a country code. Everything downstream — suppression lookups, SMS
sending, voice dialling — compares against `normalize_phone`'s output, so a row
stored as `4695537417` silently fails to match the same person stored as
`14695537417`. This repairs one row using THE SAME function the rest of the
system normalises with, so the fix cannot drift from the comparison.

It changes `phone` and nothing else, on one named lead, dry-run by default.
It refuses if normalisation would not actually change anything, and refuses if
the digits would change beyond adding a country code — a repair that alters
which human is on the other end is not a repair.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not (os.environ.get("DATABASE_URL") or "").strip():
    print("DATABASE_URL is not set. Refusing to guess which database to touch.")
    sys.exit(2)

from app.deps import SessionLocal                                  # noqa: E402
from app.models.models import Lead, Organization                   # noqa: E402
from app.services.dedup_service import normalize_phone             # noqa: E402

OTHER = ("first_name", "last_name", "email", "organization_id", "status",
         "assigned_to_id")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lead", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == args.lead).first()
        if lead is None:
            print("No lead with id %r." % args.lead)
            sys.exit(1)

        org = (db.query(Organization)
               .filter(Organization.id == lead.organization_id).first())
        before = {k: getattr(lead, k, None) for k in OTHER}
        current = lead.phone or ""
        fixed = normalize_phone(current)

        print()
        print("  lead      : %s  (%s %s)" % (lead.id, lead.first_name,
                                             lead.last_name))
        print("  tenant    : %s" % (org.name if org else lead.organization_id))
        print("  status    : %s" % lead.status)
        print("  phone     : %r  ->  %r" % (current, fixed))
        print()

        if not fixed:
            print("  Normalisation produced nothing usable. Refusing.")
            sys.exit(1)
        if fixed == current:
            print("  Already normalised. Nothing to do.")
            print()
            return

        # The only change permitted is a country-code prefix. If the trailing
        # digits move, this is a different number and not a repair.
        digits_before = "".join(ch for ch in current if ch.isdigit())
        if not fixed.endswith(digits_before[-10:]):
            print("  REFUSING: the last 10 digits would change (%s -> %s)."
                  % (digits_before[-10:], fixed[-10:]))
            print("  That is a different person, not a formatting repair.")
            sys.exit(1)

        if not args.apply:
            print("  DRY RUN. Nothing written. Re-run with --apply.")
            print()
            return

        lead.phone = fixed
        db.commit()
        db.close()

        fresh = SessionLocal()
        try:
            again = fresh.query(Lead).filter(Lead.id == args.lead).first()
            after = {k: getattr(again, k, None) for k in OTHER}
            drifted = [k for k in OTHER if before[k] != after[k]]
            print("  AFTER (re-read): phone = %r" % again.phone)
            if drifted:
                print("  FAIL: other fields changed: %s" % ", ".join(drifted))
                sys.exit(1)
            if again.phone != fixed:
                print("  FAIL: the change did not persist.")
                sys.exit(1)
            print("  PASS: phone normalised; %d other fields unchanged."
                  % len(OTHER))
            print()
        finally:
            fresh.close()
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
