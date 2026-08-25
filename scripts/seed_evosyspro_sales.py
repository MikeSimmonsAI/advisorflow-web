"""
EvoSys Pro brand-sales production seed. APPROVED Aug 25 2026.

Creates the EvoSys Pro internal sales organization, its package catalog, the
sales team, and their memberships — then flags the team's existing QA lead
records as test records so they can never receive production outreach.

    DRY RUN (default):  python scripts/seed_evosyspro_sales.py
    APPLY:              python scripts/seed_evosyspro_sales.py --apply

IDEMPOTENT. Every step checks for an existing record first. Re-running changes
nothing and re-prints the current state. It never creates a second Mike Simmons,
never re-creates an existing membership, and never re-flags an already-flagged
lead.

RULES THIS SCRIPT ENFORCES
  · Sales users get organization_id = NULL. They are not customer tenants.
  · The brand sales org is NOT an `organizations` row.
  · Packages are the SALES catalog, independent of the Stripe billing plans.
  · A lead is flagged as test ONLY on a verified email or exact phone-digit
    match. Never on a similar name.
  · Temporary passwords are printed ONCE to the operator. Nothing is emailed
    or texted.
"""
import os
import sys
import re
from datetime import date

APPLY = "--apply" in sys.argv
TODAY = date.today().isoformat()

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL not set in this shell.")
os.environ.setdefault("JWT_SECRET", "seed" + "0" * 60)
os.environ.setdefault("SECRET_KEY", "seed" + "0" * 60)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func

from app.deps import SessionLocal
from app.models.models import User, Organization, Platform, Lead
from app.models.sales_models import (
    BrandSalesOrg, BrandPackage, Membership,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password
from app.routers.god_router import _temp_password

PLATFORM_SLUG = "evosyspro"
SALES_ORG_SLUG = "evosyspro-sales"
SALES_ORG_NAME = "EvoSys Pro Sales"

# Real people. Supplied by Mike, never invented.
TEAM = [
    {"email": "michaelpschlueter@gmail.com", "name": "Michael Schlueter",
     "phone": "540-392-7776", "role": ROLE_SALES_MANAGER, "create": True},
    {"email": "blakerehani@gmail.com", "name": "Blake Rehani",
     "phone": "480-550-1982", "role": ROLE_SALES_REP, "create": True},
    # Already exists as god_admin — attach a membership, never duplicate.
    {"email": "mike@simmonsstrong.com", "name": "Mike Simmons",
     "phone": "843-532-8405", "role": ROLE_SALES_MANAGER, "create": False},
]

# The SALES catalog. Deliberately NOT the Stripe plans ($497/$997/$1,997).
# billing_plan_key stays NULL — connecting a sold package to billing is a
# separate, explicit decision.
PACKAGES = [
    {"key": "starter",      "name": "Starter",              "price": 1497.00, "order": 1},
    {"key": "growth",       "name": "Growth",               "price": 2495.00, "order": 2},
    {"key": "professional", "name": "Professional",         "price": 4995.00, "order": 3},
    {"key": "multi_tenant", "name": "Multi-Tenant / Custom", "price": None,
     "order": 4, "custom": True},
]

created = {"users": [], "memberships": [], "packages": [], "leads_flagged": []}
reused = []
temp_passwords = {}    # email -> temp password. Printed ONCE at the end, never stored.
package_ids = {}       # key   -> brand_packages.id
user_ids = {}          # email -> users.id
membership_ids = {}    # email -> memberships.id

# users.role for a brand-sales user.
#
# DELIBERATE: 'advisor' is the LOWEST-privilege value in the existing role
# ladder and every privileged check in the codebase is an allowlist that
# excludes it (`role in ("org_admin","super_admin","god_admin")`). Paired with
# organization_id = NULL it yields a user who can reach no customer tenant data
# at all. Their real capability comes from `memberships`, not from here.
#
# Do NOT "upgrade" this to org_admin/super_admin to make the sales UI easier —
# that would hand a salesperson a customer tenant. And do not use 'viewer':
# it is grantable but recognised by no guard, so it would SKIP the
# "you can only edit your own leads" ownership checks in leads_router.
#
# Note this is the one place that deliberately diverges from god_create_user's
# TENANT_ROLES guard, which refuses 'advisor' without an org_id. That guard is
# right for an operator clicking a button (guessing an org is always wrong).
# This is a reviewed, approved seed doing it on purpose, with NULL asserted
# rather than defaulted.
SALES_BASE_ROLE = "advisor"


def digits(s):
    return re.sub(r"\D", "", s or "")


def phone_matches(a, b):
    """Compare on trailing 10 digits so 5403927776 == 15403927776."""
    da, db_ = digits(a), digits(b)
    return bool(da) and bool(db_) and da[-10:] == db_[-10:]


def log(action, detail):
    print("  %-8s %s" % (("[APPLY]" if APPLY else "[DRY]"), "%-14s %s" % (action, detail)))


db = SessionLocal()
try:
    print("MODE: %s\n" % ("APPLY — changes WILL be committed" if APPLY
                          else "DRY RUN — nothing will be written"))

    # ── 1. Platform ────────────────────────────────────────────────────────
    platform = db.query(Platform).filter(Platform.slug == PLATFORM_SLUG).first()
    if not platform:
        sys.exit("Platform '%s' not found. Aborting." % PLATFORM_SLUG)
    print("=== 1. Platform ===")
    print("  %s (%s)" % (platform.name, platform.id))

    # ── 2. Brand sales org ─────────────────────────────────────────────────
    print("\n=== 2. Brand sales organization ===")
    sales_org = db.query(BrandSalesOrg).filter(BrandSalesOrg.slug == SALES_ORG_SLUG).first()
    if sales_org:
        reused.append("brand sales org %s" % sales_org.id)
        print("  exists: %s (%s)" % (sales_org.name, sales_org.id))
    else:
        sales_org = BrandSalesOrg(platform_id=platform.id, name=SALES_ORG_NAME,
                                  slug=SALES_ORG_SLUG, timezone="America/Chicago")
        if APPLY:
            db.add(sales_org); db.flush()
        log("CREATE", "%s under platform %s" % (SALES_ORG_NAME, platform.slug))

    # Guard: it must never be a customer organizations row.
    clash = db.query(Organization).filter(Organization.slug == SALES_ORG_SLUG).first()
    if clash:
        sys.exit("ABORT: a customer organization already uses slug '%s'." % SALES_ORG_SLUG)
    print("  verified: not present in customer `organizations` table")

    # ── 3. Package catalog ─────────────────────────────────────────────────
    print("\n=== 3. EvoSys Pro sales package catalog ===")
    for p in PACKAGES:
        existing = (db.query(BrandPackage)
                    .filter(BrandPackage.platform_id == platform.id,
                            BrandPackage.key == p["key"]).first())
        price_label = ("$%s" % format(p["price"], ",.2f")) if p["price"] else "custom pricing"
        if existing:
            reused.append("package %s" % p["key"])
            package_ids[p["key"]] = existing.id
            print("  exists: %-22s %-16s %s" % (p["name"], price_label, existing.id))
        else:
            pkg = BrandPackage(
                platform_id=platform.id, key=p["key"], name=p["name"],
                price=p["price"], currency="USD", billing_period="monthly",
                is_custom=bool(p.get("custom")), sort_order=p["order"],
                billing_plan_key=None,   # NEVER inferred from the Stripe catalog
            )
            if APPLY:
                db.add(pkg); db.flush()
                created["packages"].append((p["name"], pkg.id))
                package_ids[p["key"]] = pkg.id
            log("CREATE", "%-22s %s" % (p["name"], price_label))
    print("  billing_plan_key left NULL on all — sales catalog stays independent of Stripe")

    # ── 4. Sales team users ────────────────────────────────────────────────
    # organization_id is NULL on purpose. See SALES_BASE_ROLE above.
    print("\n=== 4. Sales team users ===")
    for m in TEAM:
        email = m["email"].strip().lower()
        existing = db.query(User).filter(User.email == email).first()

        if existing:
            user_ids[email] = existing.id
            reused.append("user %s" % email)
            print("  exists: %-30s %-38s role=%-11s org=%s"
                  % (email, existing.id, existing.role,
                     existing.organization_id or "NULL"))
            if not m["create"] and existing.organization_id:
                print("          note: retains customer org %s — left untouched."
                      % existing.organization_id)
            continue

        if not m["create"]:
            sys.exit("ABORT: %s was expected to already exist and does not. "
                     "Refusing to create it — verify the address before re-running."
                     % email)

        temp_pw = _temp_password()
        u = User(
            organization_id=None,          # positive assertion: no customer tenancy
            email=email,
            full_name=m["name"],
            password_hash=hash_password(temp_pw),
            role=SALES_BASE_ROLE,
            must_change_password=True,     # forced by get_current_user until changed
            is_active=True,
        )
        if APPLY:
            db.add(u); db.flush()
            user_ids[email] = u.id
            created["users"].append((m["name"], email, u.id))
            temp_passwords[email] = temp_pw
        else:
            # Never generate or display a credential during a dry run.
            temp_pw = None
        log("CREATE USER", "%-30s %-18s role=%s org=NULL"
            % (email, m["name"], SALES_BASE_ROLE))

    # Identity guard — Mike's explicit instruction. These are two different people.
    mike_id = user_ids.get("mike@simmonsstrong.com")
    mps_id = user_ids.get("michaelpschlueter@gmail.com")
    if mike_id and mps_id and mike_id == mps_id:
        sys.exit("ABORT: Mike Simmons and Michael Schlueter resolved to the same "
                 "user row. Identities must never be merged.")
    print("  verified: Mike Simmons and Michael Schlueter are distinct user rows")

    # ── 5. Brand-sales memberships ─────────────────────────────────────────
    # Additive. users.role is untouched; sales capability lives here.
    print("\n=== 5. Brand-sales memberships ===")
    if not APPLY and not getattr(sales_org, "id", None):
        print("  (dry run: brand sales org not yet persisted — membership scope_id"
              " will be its new id)")
    for m in TEAM:
        email = m["email"].strip().lower()
        uid = user_ids.get(email)
        if not uid:
            # Dry run only: the user row does not exist yet, so there is no
            # user_id to bind a membership to. It WILL be granted on --apply.
            log("GRANT", "%-30s %-14s in %s  (pending user creation)"
                % (email, m["role"], SALES_ORG_SLUG))
            continue

        existing = (db.query(Membership)
                    .filter(Membership.user_id == uid,
                            Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                            Membership.scope_id == sales_org.id,
                            Membership.role == m["role"]).first()) if sales_org.id else None
        if existing:
            membership_ids[email] = existing.id
            reused.append("membership %s/%s" % (email, m["role"]))
            print("  exists: %-30s %-14s %s" % (email, m["role"], existing.id))
            continue

        mem = Membership(
            user_id=uid, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=sales_org.id,
            role=m["role"], is_active=True,
            granted_by=mike_id,   # audit trail: who granted this
        )
        if APPLY:
            db.add(mem); db.flush()
            membership_ids[email] = mem.id
            created["memberships"].append((email, m["role"], mem.id))
        log("GRANT", "%-30s %-14s in %s" % (email, m["role"], SALES_ORG_SLUG))

    # ── 6. Flag internal QA leads as test records ──────────────────────────
    # RULE (Mike, explicit): match on a VERIFIED email address or an exact
    # trailing-10-digit phone match ONLY. A similar name is never a match.
    print("\n=== 6. Internal QA lead records ===")
    print("  matching on verified email or exact phone digits only — never on name")
    for m in TEAM:
        email = m["email"].strip().lower()
        last10 = digits(m["phone"])[-10:]

        # Cheap candidate pull, then verify in Python. LIKE on the trailing 10
        # digits catches both '5403927776' and '15403927776' storage forms.
        candidates = (db.query(Lead)
                      .filter((func.lower(Lead.email) == email)
                              | Lead.phone.like("%" + last10)
                              | Lead.phone_raw.like("%" + last10))
                      .all())

        confirmed = []
        for lead in candidates:
            why = []
            if (lead.email or "").strip().lower() == email:
                why.append("email")
            if phone_matches(lead.phone, m["phone"]) or phone_matches(lead.phone_raw, m["phone"]):
                why.append("phone")
            if why:                       # never reached without email or phone
                confirmed.append((lead, "+".join(why)))

        if not confirmed:
            print("  %-30s no matching lead record" % m["name"])
            continue

        for lead, why in confirmed:
            org = db.query(Organization).filter(
                Organization.id == lead.organization_id).first()
            org_name = org.name if org else lead.organization_id
            label = "%s / %s %s / %s" % (lead.id, lead.first_name or "",
                                         lead.last_name or "", org_name)
            if lead.is_test:
                reused.append("lead %s already flagged" % lead.id)
                print("  already test: %s  (match: %s)" % (label, why))
                continue
            if APPLY:
                lead.is_test = True
                lead.test_note = (
                    "Internal QA record — %s, EvoSys Pro sales team. Flagged by "
                    "scripts/seed_evosyspro_sales.py on %s (match: %s). "
                    "Excluded from all production outreach."
                    % (m["name"], TODAY, why)
                )
                created["leads_flagged"].append((m["name"], lead.id, org_name, why))
            log("FLAG TEST", "%s  (match: %s)" % (label, why))

    # ── 7. Commit ──────────────────────────────────────────────────────────
    if APPLY:
        db.commit()
        print("\nCOMMITTED.")
    else:
        db.rollback()
        print("\nROLLED BACK — dry run. Re-run with --apply to write.")

    # ── 8. Report ──────────────────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("SEED REPORT")
    print("=" * 74)
    print("brand sales org : %s  (%s)" % (sales_org.name, sales_org.id or "<new>"))
    print("platform        : %s  (%s)" % (platform.name, platform.id))

    print("\npackages:")
    for p in PACKAGES:
        print("  %-22s %s" % (p["name"], package_ids.get(p["key"], "<new>")))

    print("\nusers:")
    for m in TEAM:
        email = m["email"].strip().lower()
        print("  %-30s %-38s %s" % (email, user_ids.get(email, "<new>"), m["role"]))

    print("\nmemberships:")
    for m in TEAM:
        email = m["email"].strip().lower()
        print("  %-30s %-14s %s" % (email, m["role"],
                                    membership_ids.get(email, "<new>")))

    print("\ntest records flagged this run: %d" % len(created["leads_flagged"]))
    for name, lid, org_name, why in created["leads_flagged"]:
        print("  %-20s %s  %s  (match: %s)" % (name, lid, org_name, why))

    if reused:
        print("\nalready present (unchanged): %d" % len(reused))
        for r in reused:
            print("  %s" % r)

    # Credentials last, so they are the final thing on screen and easy to
    # transcribe. Printed ONCE. Nothing is emailed or texted (Mike's rule).
    if temp_passwords:
        print("\n" + "!" * 74)
        print("TEMPORARY PASSWORDS — shown ONCE. Not stored, not emailed, not texted.")
        print("Deliver these to each person directly. They are forced to change the")
        print("password at first login (must_change_password = True).")
        print("!" * 74)
        for m in TEAM:
            email = m["email"].strip().lower()
            if email in temp_passwords:
                print("  %-20s %-30s %s" % (m["name"], email, temp_passwords[email]))
        print("!" * 74)

except Exception:
    db.rollback()
    raise
finally:
    db.close()
