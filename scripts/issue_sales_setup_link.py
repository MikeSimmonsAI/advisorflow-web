"""Issue a one-time brand-sales access link from the Render shell.

WHY THIS EXISTS. The God Mode UI is the normal way to do this (Sales Operations
-> brand -> Sales team -> Generate setup link). This script is for the very
first link, when nobody who could click that button can currently sign in, and
for any later operator run where a browser session is not available.

IT IS THE SAME CODE PATH. It calls `staff_activation.issue()` - the same
function the endpoint calls - after re-running every guard the endpoint runs:
the actor must hold god or sales-manager authority for that brand, and the
target must already hold an ACTIVE membership in that brand. This grants no
access of its own; it only unlocks access that already exists.

IT PRINTS ONE URL, ONCE. No password is created, changed, printed or emailed.
The token is not stored, not audited and not recoverable - a lost link is
replaced by generating another, which revokes the lost one.

Usage, in the Render shell for advisorflow-backend:

    python scripts/issue_sales_setup_link.py \
        --actor-email <god admin email> \
        --email <target email> \
        --brand "EvoSys Pro" \
        --purpose reset \
        --base-url https://app.evosyspro.live \
        --confirm

Without --confirm it reports what it WOULD do and issues nothing.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from app.deps import SessionLocal                                   # noqa: E402
from app.models.models import User                                  # noqa: E402
from app.models.sales_models import (                               # noqa: E402
    Membership, BrandSalesOrg, SCOPE_BRAND_SALES_ORG, BRAND_SALES_ROLES,
)
from app.services import staff_activation as staff_access           # noqa: E402


def require_production_db():
    """Refuse to mint a link against a dev database.

    Same refusal as inspect_sales_users.py, for the same reason: the local .env
    points at a near-empty dev SQLite file. Issuing there would succeed and
    print a URL that works against nothing, and it would be indistinguishable
    on screen from a real production link.

    This is a function rather than module-level code so `prepare()` below can be
    exercised by the smoke suite against a SQLite fixture. `main()` - the only
    way this file is ever run - calls it before opening a session, and
    smoke_staff_activation.py asserts that it does.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL is not set. Run this in the Render shell.")
    if url.startswith("sqlite"):
        raise SystemExit(
            "DATABASE_URL points at SQLite, so this is a local dev database.\n"
            "Run this in the Render shell for advisorflow-backend. A link minted\n"
            "against a dev database is indistinguishable from a real one.")


def prepare(db, actor_email, target_email, brand):
    """Resolve the three rows and re-run BOTH endpoint guards.

    Returns `(actor, target, bso, membership)`. Raises SystemExit with the
    reason if anything does not hold. Nothing is written here.
    """
    actor = db.query(User).filter(User.email == actor_email).first()
    if actor is None:
        raise SystemExit("Actor not found: %s" % actor_email)

    target = db.query(User).filter(User.email == target_email).first()
    if target is None:
        raise SystemExit("Target user not found: %s" % target_email)

    bso = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == brand).first()
    if bso is None:
        matches = (db.query(BrandSalesOrg)
                     .filter(BrandSalesOrg.name.ilike("%%%s%%" % brand))
                     .all())
        if len(matches) != 1:
            raise SystemExit(
                "Brand %r matched %d sales organisations. Pass an exact id.\n%s"
                % (brand, len(matches),
                   "\n".join("  %s  %s" % (b.id, b.name) for b in matches)))
        bso = matches[0]

    # Guard 1 - the endpoint's authority check, unchanged.
    staff_access.assert_can_manage_sales_access(actor, bso.id, db)

    # Guard 2 - the endpoint's membership check, unchanged. This is what stops
    # the script being a back door: it unlocks existing access, never grants it.
    holds = (db.query(Membership)
               .filter(Membership.user_id == target.id,
                       Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                       Membership.scope_id == bso.id,
                       Membership.role.in_(BRAND_SALES_ROLES),
                       Membership.is_active.is_(True))
               .first())
    if holds is None:
        raise SystemExit(
            "%s has no ACTIVE sales membership in %s. An access link unlocks "
            "existing access; it does not grant it. Nothing was written."
            % (target.email, bso.name))

    return actor, target, bso, holds


def main():
    require_production_db()
    ap = argparse.ArgumentParser()
    ap.add_argument("--actor-email", required=True,
                    help="Who is issuing this. Must hold god or sales-manager "
                         "authority for the brand.")
    ap.add_argument("--email", required=True, help="The person receiving the link.")
    ap.add_argument("--brand", required=True,
                    help="Brand sales org id, or an exact/partial name match.")
    ap.add_argument("--purpose", default="setup", choices=["setup", "reset"])
    ap.add_argument("--base-url", default="", help="e.g. https://app.evosyspro.live")
    ap.add_argument("--ttl-hours", type=int, default=72)
    ap.add_argument("--confirm", action="store_true",
                    help="Actually issue. Without it, nothing is written.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        actor, target, bso, holds = prepare(
            db, args.actor_email, args.email, args.brand)

        state = staff_access.access_state(db, target)

        print("")
        print("  brand         %s  (%s)" % (bso.name, bso.id))
        print("  actor         %s" % actor.email)
        print("  target        %s  <%s>" % (target.full_name, target.email))
        print("  user id       %s" % target.id)
        print("  organization  %s" % (target.organization_id or "NULL (brand-sales)"))
        print("  sales role    %s" % holds.role)
        # A password hash existing means nothing on its own - both people this
        # was built for had one and still could not get in, because
        # must_change_password blocks every authenticated route and clearing it
        # needs the password they do not have.
        print("  has password  %s" % state["has_password"])
        print("  must change   %s" % state["must_change_password"])
        print("  has signed in %s" % state["has_signed_in"])
        print("  locked out    %s" % state["locked_out"])
        prev = state["link"]
        print("  existing link %s" % (
            "none" if not prev else
            "%s (%s), usable=%s - issuing REVOKES it"
            % (prev["id"], prev["status"], prev["is_usable"])))
        print("  purpose       %s" % args.purpose)
        print("")

        if not args.confirm:
            print("  DRY RUN - nothing written. Re-run with --confirm to issue.")
            return

        row, raw = staff_access.issue(
            db, target, actor,
            brand_sales_org_id=bso.id,
            purpose=args.purpose,
            ttl_hours=args.ttl_hours)

        print("  ONE-TIME LINK (shown once, not recoverable):")
        print("")
        print("    %s" % staff_access.activation_url(args.base_url, raw))
        print("")
        print("  expires %s UTC   activation id %s" % (row.expires_at, row.id))
        print("  No password was created, changed or printed.")
        print("")
    finally:
        db.close()


if __name__ == "__main__":
    main()
