"""Issue, list and revoke integration service keys.

    python scripts/integration_key.py list
    python scripts/integration_key.py issue --name "Taffiny voice" --brand bso-evo \
        --advisor <user_id> [--rate 60] [--apply]
    python scripts/integration_key.py revoke --prefix evsk_AbCdEf --apply

THE SECRET IS PRINTED ONCE, HERE, TO THIS TERMINAL, AND NOWHERE ELSE. It is not
written to the database (only a SHA-256 hash is), not logged, not emailed, not
texted. If it is lost, revoke the key and issue another — there is no recovery
path by design, because a recoverable secret is a stored secret.

DRY RUN BY DEFAULT. Nothing is written without --apply, matching the discipline
in seed_evosyspro_sales.py. Read the plan, then run it again with --apply.
"""

import argparse
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
from app.models.integration_models import (                        # noqa: E402
    IntegrationCredential, IntegrationRequestLog, INTEGRATION_RETELL,
)
from app.services.integration_auth import generate_key             # noqa: E402


def _fmt(c: IntegrationCredential) -> str:
    state = "active"
    if c.revoked_at is not None:
        state = "REVOKED %s" % c.revoked_at.strftime("%Y-%m-%d")
    elif not c.is_active:
        state = "inactive"
    return "  %-14s  %-28s %-16s %-9s last used %s" % (
        c.key_prefix, (c.name or "")[:28], c.brand_sales_org_id, state,
        c.last_used_at.strftime("%Y-%m-%d %H:%M") if c.last_used_at else "never")


def cmd_list(db, args):
    rows = (db.query(IntegrationCredential)
            .order_by(IntegrationCredential.created_at.desc()).all())
    if not rows:
        print("No integration credentials exist.")
        return
    print("\n  %-14s  %-28s %-16s %-9s %s" % ("PREFIX", "NAME", "BRAND", "STATE", ""))
    for c in rows:
        print(_fmt(c))
        used = (db.query(IntegrationRequestLog)
                .filter(IntegrationRequestLog.credential_id == c.id).count())
        print("                  %d requests recorded" % used)
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
        brand_sales_org_id=org.id,
        default_advisor_user_id=advisor.id if advisor else None,
        allowed_advisor_ids=allow or None,
        rate_limit_per_minute=args.rate,
        is_active=True, created_at=datetime.utcnow(), note=args.note)
    db.add(cred)
    db.commit()

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
        {"list": cmd_list, "issue": cmd_issue, "revoke": cmd_revoke}[args.cmd](db, args)
    finally:
        db.close()


if __name__ == "__main__":
    main()
