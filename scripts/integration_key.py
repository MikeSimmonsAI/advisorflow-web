"""Issue, list and revoke integration service keys.

    python scripts/integration_key.py list
    python scripts/integration_key.py brands
    python scripts/integration_key.py tenants
    python scripts/integration_key.py show --prefix evsk_AbCdEf12
    python scripts/integration_key.py issue --name "EvoSys sales voice" --brand bso-evo \
        --advisor <user_id> [--rate 60] [--apply]
    python scripts/integration_key.py issue-tenant --name "Taffiney" --org <org_id> \
        --advisor <user_id> [--rate 60] [--apply]
    python scripts/integration_key.py revoke --prefix evsk_AbCdEf12 --apply

VERIFY WITH `show`, NOT BY CALLING THE API. `show` takes the non-secret prefix
and reports everything /ping would — tenant, advisor, calendar, hours, recent
requests — with the secret nowhere in play. Putting a key on a command line to
test it is how a key ends up in shell history and in a pasted terminal buffer.

TWO SCOPES, NEVER BOTH. `issue` produces a BRAND key (brand_sales_orgs) for
sales scheduling. `issue-tenant` produces a TENANT key (organizations) for a
customer's own advisors. A key of one kind is refused by every route of the
other, so which command you run decides what the key can ever reach.

THE SECRET IS PRINTED ONCE, HERE, TO THIS TERMINAL, AND NOWHERE ELSE. It is not
written to the database (only a SHA-256 hash is), not logged, not emailed, not
texted. If it is lost, revoke the key and issue another — there is no recovery
path by design, because a recoverable secret is a stored secret.

DRY RUN BY DEFAULT. Nothing is written without --apply, matching the discipline
in seed_evosyspro_sales.py. Read the plan, then run it again with --apply.
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not (os.environ.get("DATABASE_URL") or "").strip():
    print("DATABASE_URL is not set. Refusing to guess which database to touch.")
    sys.exit(2)

from app.deps import SessionLocal                                  # noqa: E402
from app.models.models import User                                 # noqa: E402
from app.models.sales_models import (                              # noqa: E402
    BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG,
    ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.models import Organization                         # noqa: E402
from app.models.integration_models import (                        # noqa: E402
    IntegrationCredential, IntegrationRequestLog,
    INTEGRATION_RETELL, INTEGRATION_RETELL_TENANT,
)
from app.services.integration_auth import generate_key             # noqa: E402


def _fmt(c: IntegrationCredential) -> str:
    state = "active"
    if c.revoked_at is not None:
        state = "REVOKED %s" % c.revoked_at.strftime("%Y-%m-%d")
    elif not c.is_active:
        state = "inactive"
    scope = c.brand_sales_org_id or c.organization_id or "— UNSCOPED —"
    return "  %-14s  %-24s %-13s %-16s %-9s last used %s" % (
        c.key_prefix, (c.name or "")[:24], c.kind, scope, state,
        c.last_used_at.strftime("%Y-%m-%d %H:%M") if c.last_used_at else "never")


def cmd_list(db, args):
    rows = (db.query(IntegrationCredential)
            .order_by(IntegrationCredential.created_at.desc()).all())
    if not rows:
        print("No integration credentials exist.")
        return
    print("\n  %-14s  %-24s %-13s %-16s %-9s %s" % (
        "PREFIX", "NAME", "KIND", "SCOPE", "STATE", ""))
    for c in rows:
        print(_fmt(c))
        used = (db.query(IntegrationRequestLog)
                .filter(IntegrationRequestLog.credential_id == c.id).count())
        print("                  %d requests recorded" % used)
    print()


def cmd_brands(db, args):
    """Read-only. The two ids `issue` needs, without anyone writing SQL by hand
    against production to find them."""
    orgs = db.query(BrandSalesOrg).order_by(BrandSalesOrg.name).all()
    if not orgs:
        print("No brand sales orgs exist.")
        return
    for org in orgs:
        print("\n  BRAND  %s" % org.name)
        print("    --brand %s   (timezone %s)" % (org.id, org.timezone))
        members = (db.query(User, Membership)
                   .join(Membership, Membership.user_id == User.id)
                   .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                           Membership.scope_id == org.id,
                           Membership.role.in_((ROLE_SALES_MANAGER, ROLE_SALES_REP)),
                           Membership.is_active.is_(True))
                   .order_by(User.full_name).all())
        if not members:
            print("    (no active sales members)")
            continue
        for u, m in members:
            print("    --advisor %-38s %-22s %s" % (u.id, (u.full_name or "")[:22], m.role))
    print()


def cmd_show(db, args):
    """Everything `/ping` would report, WITHOUT touching the key.

    WHY THIS EXISTS. Verifying a new credential by calling the ping route means
    putting the secret on a command line — where it lands in shell history, in
    a terminal scrollback, and in whatever gets copied out of that window. The
    facts worth checking (right tenant? right advisor? is their calendar
    actually connected?) all live in the database next to the hash, so they can
    be read from here with nothing secret in play.

    Looked up by the NON-SECRET prefix, which is printed at issue time and is
    safe to paste anywhere.
    """
    cred = (db.query(IntegrationCredential)
            .filter(IntegrationCredential.key_prefix == args.prefix).first())
    if cred is None:
        print("No credential with prefix %r." % args.prefix)
        sys.exit(1)

    state = "ACTIVE"
    if cred.revoked_at is not None:
        state = "REVOKED %s" % cred.revoked_at.strftime("%Y-%m-%d %H:%M")
    elif not cred.is_active:
        state = "INACTIVE"

    print()
    print("  Integration : %s" % cred.name)
    print("  Prefix      : %s" % cred.key_prefix)
    print("  Kind        : %s" % cred.kind)
    print("  State       : %s" % state)

    try:
        scope = cred.scope_kind()
    except ValueError as e:
        print("  Scope       : BROKEN - %s" % e)
        print("  This key is refused by every route. Revoke and reissue.")
        print()
        return

    advisor = None
    if cred.default_advisor_user_id:
        advisor = (db.query(User)
                   .filter(User.id == cred.default_advisor_user_id).first())

    if scope == "tenant":
        org = (db.query(Organization)
               .filter(Organization.id == cred.organization_id).first())
        print("  Tenant      : %s (%s)" % (
            (org.brand_name or org.name) if org else "MISSING",
            cred.organization_id))
        if org is not None:
            print("  Location    : %s" % (org.org_address or "- not set -"))
            types = []
            raw = (org.appointment_types or "").strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        types = [str(x).strip() for x in parsed if str(x).strip()]
                except Exception:
                    pass
            print("  Appt types  : %s" % (", ".join(types) if types
                                          else "- none configured -"))
            if not org.is_active:
                print("  WARNING: this organization is not active. Every call fails closed.")
        # The advisor must belong to THIS tenant or the bridge refuses it.
        if advisor is not None and (advisor.organization_id or None) != cred.organization_id:
            print("  WARNING: the default advisor does not belong to this tenant.")
            print("  Every call would return 'Advisor not found'. Revoke and reissue.")
    else:
        print("  Brand       : %s" % cred.brand_sales_org_id)

    print("  Default     : %s" % (advisor.full_name if advisor else "- none -"))
    if advisor is not None:
        cal = "none"
        if getattr(advisor, "microsoft_oauth_refresh_token_encrypted", None):
            cal = "microsoft"
        elif getattr(advisor, "google_oauth_refresh_token_encrypted", None):
            cal = "google"
        print("  Calendar    : %s" % cal)
        print("  Timezone    : %s" % (advisor.booking_timezone or "- default -"))
        print("  Hours       : %s-%s on days %s" % (
            advisor.available_start_time or "09:00",
            advisor.available_end_time or "17:00",
            advisor.available_days or "0,1,2,3,4"))
        if not advisor.is_active:
            print("  WARNING: the default advisor is not active.")
        if cal == "none":
            print()
            print("  NOTE: no external calendar is connected for this advisor.")
            print("  Availability reflects AdvisorFlow bookings and blocks only.")

    print("  Allowlist   : %s" % (", ".join(cred.advisor_allowlist())
                                  or "any active member of this scope"))
    print("  Rate limit  : %d/min" % cred.rate_limit_per_minute)
    print("  Last used   : %s" % (
        cred.last_used_at.strftime("%Y-%m-%d %H:%M UTC") if cred.last_used_at
        else "never - Retell has not called yet"))

    logs = (db.query(IntegrationRequestLog)
            .filter(IntegrationRequestLog.credential_id == cred.id)
            .order_by(IntegrationRequestLog.occurred_at.desc())
            .limit(args.recent or 5).all())
    total = (db.query(IntegrationRequestLog)
             .filter(IntegrationRequestLog.credential_id == cred.id).count())
    print("  Requests    : %d recorded" % total)
    for r in logs:
        print("      %s  %-12s %-3s %s" % (
            r.occurred_at.strftime("%m-%d %H:%M"), r.action,
            "ok" if r.success else str(r.status_code or "err"),
            (r.detail or "")[:60]))
    print()


def cmd_tenants(db, args):
    """Read-only. Customer organizations and their advisors, for `issue-tenant`.

    Shows which calendar each advisor's availability would actually be read
    from, because a key pointed at an advisor with no connected calendar will
    happily return slots that only reflect what is already in AdvisorFlow.
    """
    orgs = (db.query(Organization)
            .filter(Organization.is_active.is_(True))
            .order_by(Organization.name).all())
    if not orgs:
        print("No active organizations exist.")
        return
    for org in orgs:
        print("\n  TENANT  %s" % (org.brand_name or org.name))
        print("    --org %s" % org.id)
        advisors = (db.query(User)
                    .filter(User.organization_id == org.id,
                            User.is_active.is_(True))
                    .order_by(User.full_name).all())
        if not advisors:
            print("    (no active users)")
            continue
        for u in advisors:
            cal = "none"
            if getattr(u, "microsoft_oauth_refresh_token_encrypted", None):
                cal = "microsoft"
            elif getattr(u, "google_oauth_refresh_token_encrypted", None):
                cal = "google"
            print("    --advisor %-38s %-22s calendar=%s" % (
                u.id, (u.full_name or "")[:22], cal))
    print()


def cmd_issue_tenant(db, args):
    """Issue a credential scoped to ONE customer organization."""
    org = db.query(Organization).filter(Organization.id == args.org).first()
    if org is None:
        print("No organization with id %r. Run `tenants` first." % args.org)
        sys.exit(1)

    advisor = None
    if args.advisor:
        advisor = db.query(User).filter(User.id == args.advisor).first()
        if advisor is None:
            print("No user with id %r." % args.advisor)
            sys.exit(1)
        if (advisor.organization_id or None) != org.id:
            # Refuse rather than issue a key whose default advisor the bridge
            # would reject at run time — a key that cannot work is worse than
            # no key, because it looks like one.
            print("%s does not belong to %s. Refusing." % (advisor.email, org.name))
            sys.exit(1)
        if not advisor.is_active:
            print("%s is not active. Refusing." % advisor.email)
            sys.exit(1)

    allow = ",".join([a.strip() for a in (args.allow or "").split(",") if a.strip()])

    cal = "none"
    if advisor is not None:
        if getattr(advisor, "microsoft_oauth_refresh_token_encrypted", None):
            cal = "microsoft"
        elif getattr(advisor, "google_oauth_refresh_token_encrypted", None):
            cal = "google"

    print()
    print("  Integration : %s" % args.name)
    print("  Kind        : %s" % INTEGRATION_RETELL_TENANT)
    print("  Tenant      : %s (%s)" % (org.brand_name or org.name, org.id))
    print("  Default     : %s" % (advisor.full_name if advisor else "— none —"))
    print("  Calendar    : %s" % cal)
    print("  Allowlist   : %s" % (allow or "any active user of this tenant"))
    print("  Rate limit  : %d/min" % args.rate)
    if cal == "none" and advisor is not None:
        print()
        print("  NOTE: this advisor has no external calendar connected. Availability")
        print("  will reflect their AdvisorFlow bookings and blocks only.")
    print()

    if not args.apply:
        print("  DRY RUN. Nothing written. Re-run with --apply to issue the key.")
        print()
        return

    full, prefix, hashed = generate_key()
    cred = IntegrationCredential(
        name=args.name, kind=INTEGRATION_RETELL_TENANT,
        key_prefix=prefix, key_hash=hashed,
        # Tenant-scoped: organization_id set, brand_sales_org_id left NULL.
        brand_sales_org_id=None,
        organization_id=org.id,
        default_advisor_user_id=advisor.id if advisor else None,
        allowed_advisor_ids=allow or None,
        rate_limit_per_minute=args.rate,
        is_active=True, created_at=datetime.utcnow(), note=args.note)
    db.add(cred)
    db.commit()

    _print_key(full, prefix)


def _print_key(full: str, prefix: str) -> None:
    print("  " + "=" * 66)
    print("  KEY ISSUED. This is the only time it will ever be shown.")
    print("  " + "=" * 66)
    print()
    print("      %s" % full)
    print()
    print("  Prefix (safe to share/log): %s" % prefix)
    print("  Send it as:  Authorization: Bearer <the key above>")
    print()
    print("  Store it in Retell's secret field. Do not paste it into chat,")
    print("  a ticket, a commit, or a document.")
    print()


def cmd_issue(db, args):
    org = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == args.brand).first()
    if org is None:
        print("No brand sales org with id %r. Run `list` on brands first." % args.brand)
        sys.exit(1)

    advisor = None
    if args.advisor:
        advisor = db.query(User).filter(User.id == args.advisor).first()
        if advisor is None:
            print("No user with id %r." % args.advisor)
            sys.exit(1)
        member = db.query(Membership).filter(
            Membership.user_id == advisor.id,
            Membership.scope_type == SCOPE_BRAND_SALES_ORG,
            Membership.scope_id == org.id,
            Membership.role.in_((ROLE_SALES_MANAGER, ROLE_SALES_REP)),
            Membership.is_active.is_(True)).first()
        if member is None:
            # Refuse rather than issue a key whose default advisor the bridge
            # would reject at run time — a key that cannot work is worse than
            # no key, because it looks like one.
            print("%s is not an active sales member of %s. Refusing." % (
                advisor.email, org.name))
            sys.exit(1)

    allow = ",".join([a.strip() for a in (args.allow or "").split(",") if a.strip()])

    print()
    print("  Integration : %s" % args.name)
    print("  Kind        : %s" % INTEGRATION_RETELL)
    print("  Brand       : %s (%s)" % (org.name, org.id))
    print("  Default     : %s" % (advisor.full_name if advisor else "— none —"))
    print("  Allowlist   : %s" % (allow or "any active member of this brand"))
    print("  Rate limit  : %d/min" % args.rate)
    print()

    if not args.apply:
        print("  DRY RUN. Nothing written. Re-run with --apply to issue the key.")
        print()
        return

    full, prefix, hashed = generate_key()
    cred = IntegrationCredential(
        name=args.name, kind=INTEGRATION_RETELL,
        key_prefix=prefix, key_hash=hashed,
        # Brand-scoped: brand_sales_org_id set, organization_id left NULL.
        brand_sales_org_id=org.id,
        organization_id=None,
        default_advisor_user_id=advisor.id if advisor else None,
        allowed_advisor_ids=allow or None,
        rate_limit_per_minute=args.rate,
        is_active=True, created_at=datetime.utcnow(), note=args.note)
    db.add(cred)
    db.commit()

    _print_key(full, prefix)


def cmd_revoke(db, args):
    cred = (db.query(IntegrationCredential)
            .filter(IntegrationCredential.key_prefix == args.prefix).first())
    if cred is None:
        print("No credential with prefix %r." % args.prefix)
        sys.exit(1)
    print("  Revoking: %s (%s)" % (cred.name, cred.key_prefix))
    if not args.apply:
        print("  DRY RUN. Nothing written. Re-run with --apply.")
        return
    cred.is_active = False
    cred.revoked_at = datetime.utcnow()
    db.commit()
    # The audit rows are deliberately kept: revoking a key must not erase what
    # it did.
    print("  Revoked. Every request with this key now fails closed (401).")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")
    sub.add_parser("brands")
    sub.add_parser("tenants")

    s = sub.add_parser("show")
    s.add_argument("--prefix", required=True,
                   help="The NON-SECRET prefix printed at issue time")
    s.add_argument("--recent", type=int, default=5,
                   help="How many recent requests to list")

    t = sub.add_parser("issue-tenant")
    t.add_argument("--name", required=True, help="Human identity, shown in audit rows")
    t.add_argument("--org", required=True, help="organizations.id — the funeral home")
    t.add_argument("--advisor", help="users.id — the default advisor for this key")
    t.add_argument("--allow", help="Comma-separated user ids this key may target")
    t.add_argument("--rate", type=int, default=60)
    t.add_argument("--note")
    t.add_argument("--apply", action="store_true")

    i = sub.add_parser("issue")
    i.add_argument("--name", required=True, help="Human identity, shown in audit rows")
    i.add_argument("--brand", required=True, help="brand_sales_orgs.id")
    i.add_argument("--advisor", help="users.id — the default advisor for this key")
    i.add_argument("--allow", help="Comma-separated user ids this key may target")
    i.add_argument("--rate", type=int, default=60)
    i.add_argument("--note")
    i.add_argument("--apply", action="store_true")

    r = sub.add_parser("revoke")
    r.add_argument("--prefix", required=True)
    r.add_argument("--apply", action="store_true")

    args = p.parse_args()
    db = SessionLocal()
    try:
        {"list": cmd_list, "brands": cmd_brands, "tenants": cmd_tenants,
         "show": cmd_show, "issue": cmd_issue,
         "issue-tenant": cmd_issue_tenant,
         "revoke": cmd_revoke}[args.cmd](db, args)
    finally:
        db.close()


if __name__ == "__main__":
    main()
