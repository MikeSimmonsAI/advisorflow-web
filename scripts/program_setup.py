#!/usr/bin/env python
"""Configure a customer as a LOCATION OUTREACH PROGRAM (built for SCI). Dry run by default.

    python scripts/program_setup.py --org-id <id> --source SCI_Filtered_551_Leads.csv \
        --name "Service Corporation International" \
        --primary-contact "Kerry Allan" --primary-title "Head of Sales" \
        --logo SCI_Logo.png [--stage] [--apply]

    # or create the organization first (owner account required, no invite sent):
    python scripts/program_setup.py --create --platform-slug evosyspro --actor-email <owner> ...

WHAT IT DOES (with --apply):
    1. (--create) creates the organization through customer_provisioning,
       industry "funeral", nothing switched on that it does not name.
    2. Program row: primary contact NAME and title only. No phone, no email,
       no user account - those are added when supplied (no invite is sent).
    3. One Location + LocationProfile per distinct "Location Friendly Name"
       in the source, plus "Unassigned / Location Review". Only the name comes
       from the source; addresses, sites and managers are NOT invented.
    4. Campaign families, all INACTIVE.
    5. --logo: stores the logo as the program's active logo asset.
    6. Source file: dry run always; --stage writes staging rows (551 in, 551
       kept). Neither creates leads, enrols anyone or sends anything.
    7. --promote: staged rows -> live contacts/leads, one per contact master,
       every source Lead ID kept on it; sms_consent False; no enrolment, no
       sends. Rows still held in a review queue are promoted but every send
       path refuses them until the review is cleared.

Without --apply it prints what it would do and rolls everything back.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.deps import SessionLocal                                 # noqa: E402
import app.models.registry  # noqa: E402,F401
from app.models.models import Organization, Platform, User        # noqa: E402
from app.services.programs import importer, setup                 # noqa: E402

DEFAULT_FEATURES = ["leads", "reports", "users", "availability", "branding_settings",
                    "audit_log", "imports", "email", "sms"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--org-id")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--platform-slug", default="evosyspro")
    ap.add_argument("--actor-email", help="owner account the changes are attributed to")
    ap.add_argument("--name", default="Service Corporation International")
    ap.add_argument("--primary-contact", default="Kerry Allan")
    ap.add_argument("--primary-title", default="Head of Sales")
    ap.add_argument("--hero-subtitle", default="Family Service Lead & Communication Center")
    ap.add_argument("--source", required=True)
    ap.add_argument("--logo")
    ap.add_argument("--stage", action="store_true", help="write staging rows (still no leads)")
    ap.add_argument("--promote", action="store_true",
                    help="with --apply: turn staged rows into live contacts/leads (one per contact "
                         "master, no consent inferred, nothing enrolled or sent)")
    ap.add_argument("--mgmt-name", help="management alert recipient name")
    ap.add_argument("--mgmt-sms", help="management alert phone (10-digit US or +E.164)")
    ap.add_argument("--mgmt-email", help="management alert email")
    ap.add_argument("--staff-alerts", choices=("on", "off"),
                    help="real SMS/email staff alerts for HOT replies (in-app is always on)")
    ap.add_argument("--hold-reviews", action="store_true",
                    help="with --stage: put every record still in a review state ON HOLD")
    ap.add_argument("--no-aliases", action="store_true", help="do not assign location email addresses")
    ap.add_argument("--mail-folder", help='Outlook folder processed replies are filed under, e.g. "Inbox/Customers Folder/SCI"')
    ap.add_argument("--manager-email", action="append", default=[],
                    help="EXISTING login to give the workspace 'manager' role (repeatable). "
                         "Never creates an account; refuses an unknown email.")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)

    content = open(a.source, "rb").read()
    rows = importer.parse_csv(content)
    names = sorted({(r.get(importer.COL["location"]) or "").strip() for r in rows} - {""})

    db = SessionLocal()
    try:
        actor = None
        if a.actor_email:
            actor = db.query(User).filter(User.email == a.actor_email.strip().lower()).first()
        if actor is None:
            actor = db.query(User).filter(User.role == "god_admin", User.is_active.is_(True)).first()
        if actor is None:
            sys.exit("No owner account found to attribute the changes to (--actor-email).")

        if a.create:
            from app.services import customer_provisioning as cp
            plat = db.query(Platform).filter(Platform.slug == a.platform_slug).first()
            if plat is None:
                sys.exit("Platform %r not found." % a.platform_slug)
            existing = db.query(Organization).filter(Organization.name == a.name).first()
            if existing is not None:
                org = existing
                print("organization exists:", org.id)
            else:
                org, _ = cp.create_customer(db, actor, name=a.name, platform_id=plat.id,
                                            industry="funeral", timezone="America/Chicago")
                org.enabled_features = json.dumps(DEFAULT_FEATURES)
                org.brand_name = a.name
                print("organization created:", org.id)
        else:
            org = db.query(Organization).filter(Organization.id == a.org_id).first()
            if org is None:
                sys.exit("Organization %r not found (pass --org-id or --create)." % a.org_id)

        prog = setup.ensure_program(db, org, name=a.name, primary_contact_name=a.primary_contact,
                                    primary_contact_title=a.primary_title, hero_title=a.name,
                                    hero_subtitle=a.hero_subtitle)
        profiles = setup.ensure_locations(db, org, actor, names)
        fams = setup.ensure_campaign_families(db, org)
        if a.logo:
            data = open(a.logo, "rb").read()
            logo = setup.store_asset(db, org, kind="logo", title="%s logo" % a.name, data=data,
                                     content_type="image/png", filename=os.path.basename(a.logo),
                                     activate=True, uploaded_by=actor.id)
            prog.logo_asset_id = logo.id
        if a.mgmt_sms or a.mgmt_email:
            from app.routers.program_router import _clean_email, _clean_phone
            m = {"name": a.mgmt_name or "Management", "role": "management",
                 "phone": _clean_phone(a.mgmt_sms, "--mgmt-sms"), "email": _clean_email(a.mgmt_email, "--mgmt-email")}
            others = [x for x in json.loads(prog.management_recipients or "[]")
                      if isinstance(x, dict) and (x.get("phone"), x.get("email")) != (m["phone"], m["email"])]
            prog.management_recipients = json.dumps(others + [m])
            print("management recipient:", m["name"], m["phone"] or "-", m["email"] or "-")
        if a.staff_alerts:
            prog.staff_sms_alerts_enabled = a.staff_alerts == "on"
            print("staff alerts:", a.staff_alerts)
        for em in a.manager_email:
            from app.services.workspace_access import grant_workspace_membership
            u = db.query(User).filter(User.email == em.strip().lower(), User.is_active.is_(True)).all()
            if len(u) != 1:
                sys.exit("--manager-email %s: %s - no account was created." % (
                    em, "no active login with that email" if not u else "more than one login matches"))
            grant_workspace_membership(db, u[0].id, org.id, role="manager", granted_by=actor.id, commit=False)
            print("manager access (workspace role 'manager', this workspace only):", u[0].email)
        if a.mail_folder:
            prog.mailbox_folder_path = a.mail_folder.strip().strip("/")
            print("Outlook filing under:", prog.mailbox_folder_path)
        db.flush()
        if not a.no_aliases:
            from app.services.programs import aliases
            try:
                table = aliases.assign(db, prog)
                print("location addresses: %d on %s (used only once they receive mail)"
                      % (len(table), aliases.alias_domain(db, prog)))
                for name, addr in sorted(table.items()):
                    print("   %-42s %s" % (name, addr))
            except ValueError as exc:
                print("location addresses NOT assigned:", exc)
        print("program:", prog.id, "| locations:", sum(1 for p in profiles.values() if not p.is_review_bucket),
              "+ review bucket | campaign families:", len(fams), "(all inactive)")

        if not a.apply:
            # Pure analysis against the uncommitted setup, then roll it all back.
            res = importer.analyze(rows, profiles, fams)
            print(json.dumps(res["summary"], indent=1, ensure_ascii=False))
            db.rollback()
            print("\nDRY RUN - nothing was written. Re-run with --apply.")
            return
        db.commit()
        res = importer.stage(db, org, content, filename=os.path.basename(a.source),
                             dry_run=not a.stage, actor_id=actor.id)
        print(json.dumps(res["summary"], indent=1, ensure_ascii=False))
        if a.hold_reviews and a.stage:
            from app.services.programs import holds
            h = holds.hold_open_reviews(db, org.id, actor.id)
            db.commit()
            print("held:", json.dumps({k: v for k, v in h.items() if k != "source_lead_ids"}))
        from app.services.programs import promote
        try:
            pr = promote.promote(db, org, actor, apply=a.promote)
        except Exception as exc:                             # noqa: BLE001 - e.g. plan limit (402)
            db.rollback()
            sys.exit("Promotion refused, nothing promoted: %s" % getattr(exc, "detail", exc))
        db.commit()
        print("promotion%s:" % ("" if a.promote else " (plan only - add --promote)"),
              json.dumps(pr, ensure_ascii=False))
        print("\nAPPLIED. Organization id:", org.id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
