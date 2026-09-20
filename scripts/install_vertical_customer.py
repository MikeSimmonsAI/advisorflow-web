#!/usr/bin/env python
"""Create ONE customer organization, through the door that already owns it.

WHAT THIS IS FOR. `configure_customer_workspace.py` next door sets a
customer's industry, features, branding and screens — and refuses to create
the organization, for the reason it states: "a second door into tenant
creation is how two of the same customer appear." That is right, and it left
a gap. Creating a customer in production meant either the god UI, which is a
person clicking through four screens with no record of what they typed, or a
one-off snippet pasted into a production shell, which is the same thing with
fewer witnesses.

So this is not a second door. It is the SAME door — `customer_provisioning.
create_customer` and `implementation_service.start_for_organization`, exactly
what POST /god/customers calls, in exactly that order and in one transaction —
with the arguments written down, a duplicate check in front of it, and a dry
run by default.

NO CUSTOMER IS NAMED IN HERE. Name, slug and industry are arguments. A
vertical's own configuration is data in `industry_templates`, so a cleaning
company created by this script opens a workspace with its own screens,
vocabulary and pipeline board without this file knowing that vertical exists.

WHAT IT WILL NOT DO:

  * It will not create a second organization for a customer that looks like it
    already exists. The check is deliberately wide — slug, exact name, and a
    substring match on the distinctive words of the name — and it REFUSES
    rather than warning. An accidental duplicate tenant is not a thing you
    notice quickly: both halves look right, and the leads land in whichever
    one somebody logged into.
  * It will not create or invite a user. Nobody gets an account because an
    organization was created, and no invitation is sent from here.
  * It will not send anything. No email, no SMS, no notification.
  * It will not configure the workspace. Run
    `configure_customer_workspace.py` after this, which is where features,
    branding and screens live.

USAGE

    python scripts/install_vertical_customer.py                            \
        --name "Acme Facility Services" --slug acme-facility-services      \
        --industry cleaning --plan trial --timezone America/Chicago        \
        --platform-slug evosyspro                                          \
        [--actor-email someone@example.com] [--apply]

Nothing is written without --apply. Run it without, read what it says it will
do, then run it again with.

WHERE THIS RUNS, AND WHY IT IS NOT DEPLOYED CODE
------------------------------------------------
The backend web service never imports this file and no route reaches it. It is
operator tooling: a person runs it, reads what it says, and runs it again with
--apply. So it is deliberately OUTSIDE the runtime deployment — `scripts/**`
is in every Python service's `ignoredPaths` in render.yaml, and that is
correct. A change here must not rebuild and restart four live services for a
tool nobody is executing at that moment.

The consequence, stated plainly because it is surprising the first time: a
commit that touches only `scripts/**` does not reach the server. The copy on
the instance is whatever the last app-triggered build carried. To run the
current version against production, either let it ride along with the next
backend deploy, or use Render's "Manual Deploy → Deploy latest commit", which
ignores build filters. Do not edit the build filter to make this file appear
on a server that never needs to execute it.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import or_                              # noqa: E402

from app.deps import SessionLocal                       # noqa: E402
from app.models.models import Organization, Platform, User  # noqa: E402
from app.services import customer_provisioning as cp    # noqa: E402
from app.services import implementation_service as impl_svc  # noqa: E402
from app.services import industry_templates             # noqa: E402
from app.services import workspace_views                # noqa: E402

# Words too common to be evidence of anything. "Commercial Cleaning Partners"
# and "Commercial Roofing Services" share a word; that is not a duplicate.
_NOISE = {
    "the", "and", "of", "for", "inc", "llc", "ltd", "co", "company", "corp",
    "corporation", "group", "holdings", "services", "service", "solutions",
    "systems", "partners", "enterprises", "commercial", "residential",
    "national", "american", "global", "professional",
}


def _distinctive(name):
    """The words in a name worth matching on, longest first."""
    words = [w.strip(".,&()").lower() for w in (name or "").split()]
    return sorted((w for w in words if len(w) > 3 and w not in _NOISE),
                  key=len, reverse=True)


def _looks_like_duplicate(db, name, slug):
    """Every organization that might already BE this customer.

    Wide on purpose. A false positive costs somebody thirty seconds of
    reading; a false negative costs a customer two tenants, half their leads
    in each, and no obvious symptom.
    """
    clauses = [Organization.slug == slug,
               Organization.name.ilike(name.strip())]
    for word in _distinctive(name)[:3]:
        clauses.append(Organization.name.ilike("%%%s%%" % word))
        clauses.append(Organization.slug.ilike("%%%s%%" % word))
    return db.query(Organization).filter(or_(*clauses)).all()


def _actor(db, email):
    """Who the audit trail records as having done this.

    An explicit --actor-email is preferred, because "which human asked for
    this customer to exist" is a real question a year later. Falling back to
    any god_admin is honest about what it is and is printed as such.
    """
    if email:
        user = db.query(User).filter(User.email.ilike(email.strip())).first()
        if user is None:
            raise SystemExit("No user with email %r." % email)
        return user, "named on the command line"
    user = (db.query(User).filter(User.role == "god_admin")
            .order_by(User.created_at.asc()).first())
    if user is None:
        raise SystemExit(
            "No god_admin user exists to attribute this to. Pass "
            "--actor-email.")
    return user, "fallback: the earliest god_admin, no actor was named"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--slug", help="Defaults to a slug derived from the name.")
    parser.add_argument("--industry", required=True,
                        help="Industry key or label; normalized through "
                             "industry_templates.")
    parser.add_argument("--plan", default="trial")
    parser.add_argument("--timezone", default="America/Chicago")
    parser.add_argument("--platform-slug",
                        help="The brand this customer belongs to.")
    parser.add_argument("--platform-id",
                        help="The brand's id, if you have it instead.")
    parser.add_argument("--actor-email",
                        help="Whose name goes on the audit trail.")
    parser.add_argument("--force-despite-similar", action="store_true",
                        help="Create anyway when an existing organization "
                             "looks like this one. Read the list first.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write. Without this, nothing changes.")
    args = parser.parse_args()

    if not args.platform_slug and not args.platform_id:
        raise SystemExit(
            "A brand is required: pass --platform-slug or --platform-id. A "
            "customer with no platform sits outside every scoping decision in "
            "the system.")

    db = SessionLocal()
    try:
        if args.platform_id:
            platform = (db.query(Platform)
                        .filter(Platform.id == args.platform_id).first())
        else:
            platform = (db.query(Platform)
                        .filter(Platform.slug == args.platform_slug).first())
        if platform is None:
            known = ", ".join(sorted(p.slug or p.id
                                     for p in db.query(Platform).all()))
            raise SystemExit("No such brand. Known brands: %s" % (known or "(none)"))

        slug = args.slug or cp.unique_slug(db, args.name)
        industry = industry_templates.normalize(args.industry)
        if not industry_templates.is_known(args.industry):
            print("  ! %r is not a known industry; it normalizes to %r."
                  % (args.industry, industry))

        actor, why = _actor(db, args.actor_email)

        print("Create customer")
        print("  name        : %s" % args.name)
        print("  slug        : %s" % slug)
        print("  brand       : %s (%s)" % (platform.name, platform.id))
        print("  industry    : %s  [%s]"
              % (industry, industry_templates.resolve(industry)["label"]))
        print("  plan        : %s" % args.plan)
        print("  timezone    : %s" % args.timezone)
        print("  actor       : %s <%s>  (%s)"
              % (actor.full_name or "(no name)", actor.email, why))
        screens = industry_templates.workspace_views(industry)
        print("  screens it inherits: %s"
              % (", ".join(v["label"] for v in workspace_views._parse(screens))
                 or "(none — its industry supplies no workflow screens)"))
        print("  features    : none. A new customer starts with an explicit "
              "empty allow-list, not NULL.")
        print("")

        similar = _looks_like_duplicate(db, args.name, slug)
        if similar:
            print("AN ORGANIZATION LIKE THIS ALREADY EXISTS:")
            for o in similar:
                print("  - %s | %s | slug=%s | industry=%s | active=%s"
                      % (o.id, o.name, o.slug, o.industry, o.is_active))
            if not args.force_despite_similar:
                raise SystemExit(
                    "\nRefusing to create a second organization. If one of "
                    "those IS this customer, configure it instead. If none of "
                    "them is, re-run with --force-despite-similar.")
            print("  ! --force-despite-similar was passed. Creating anyway.\n")
        else:
            print("No existing organization matches this name or slug.")

        if not args.apply:
            print("\nDRY RUN. Nothing was written. Re-run with --apply.")
            return

        # ONE TRANSACTION, THE SAME TWO CALLS POST /god/customers MAKES.
        # `create_customer` deliberately does not commit, so the organization
        # and its launch checklist are written together or not at all — a
        # customer that exists without one cannot be onboarded, and the gap is
        # invisible precisely because the customer is.
        org, _loc = cp.create_customer(
            db, actor, name=args.name, platform_id=platform.id, slug=slug,
            industry=industry, plan=args.plan, timezone=args.timezone)
        started = impl_svc.start_for_organization(
            db, org, actor,
            reason="Created through install_vertical_customer.py", commit=False)
        db.commit()
        db.refresh(org)

        print("\nCreated.")
        print("  organization id : %s" % org.id)
        print("  slug            : %s" % org.slug)
        print("  industry        : %s" % org.industry)
        print("  implementation  : %s (created=%s)"
              % (started["implementation"].id, started["created"]))
        print("  users invited   : none")
        print("  messages sent   : none")
        print("\nNext: configure_customer_workspace.py --org-id %s" % org.id)
    finally:
        db.close()


if __name__ == "__main__":
    main()
