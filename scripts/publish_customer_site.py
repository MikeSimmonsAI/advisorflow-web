#!/usr/bin/env python
"""Publish a customer's public page from a file in this repo.

The page lives in `public-sites/<slug>/index.html` so it is versioned, reviewed
and diffable like everything else; the `customer_sites` row is what serves it,
so publishing does not need a deploy and a copy change does not rebuild five
Python services.

    python scripts/publish_customer_site.py --org-id <id> --slug <slug> \
        --file public-sites/<slug>/index.html [--consent-file <path>] [--apply]

Dry run by default. Republishing keeps the address: the URL is on the
customer's stationery and a publish that moved it would break every link they
have handed out.

CONSENT WORDING IS A FILE, NOT A FLAG. If the page's form asks for messaging
permission, the sentence it shows has to be stored on the row verbatim - it is
what gets written to every lead as evidence, and it is the thing a carrier
dispute is actually argued from. Pass the same wording the page displays.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.deps import SessionLocal                       # noqa: E402
from app.models.models import Organization, Platform    # noqa: E402
from app.services import customer_sites                 # noqa: E402


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _sanity(html, slug):
    """The three things a live customer page must not get wrong.

    None of these is a security control - the publish route is god-only and
    the markup is written by staff. They are the mistakes that are easy to
    make when a page starts life as a prototype, and expensive to notice after
    it is live.
    """
    problems = []
    if "/site/" not in html:
        problems.append("the page never references /site/ - its form will not "
                        "post anywhere")
    for marker in ("demo123", "Demo Mode", "Sample Data Only",
                   "no real authentication", "sample account data"):
        if marker.lower() in html.lower():
            problems.append("the page still carries prototype text: %r" % marker)
    if re.search(r"\d+(\.\d+)?\s*¢\s*/?\s*kWh", html):
        problems.append("the page states a per-kWh price; a live customer page "
                        "may not quote a rate the platform cannot prove")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org-id", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--title")
    parser.add_argument("--consent-file",
                        help="A text file holding the consent wording the page "
                             "displays, verbatim.")
    parser.add_argument("--consent-version")
    parser.add_argument("--owner-user-id",
                        help="Assign every new enquiry from this page to one "
                             "person. Omit to leave them unassigned.")
    parser.add_argument("--force", action="store_true",
                        help="Publish despite the sanity warnings.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    html = _read(args.file)
    consent_text = _read(args.consent_file).strip() if args.consent_file else None

    problem = customer_sites.slug_error(args.slug)
    if problem:
        raise SystemExit(problem)

    warnings = _sanity(html, args.slug)
    for warning in warnings:
        print("  ! %s" % warning)
    if warnings and not args.force:
        raise SystemExit("Refusing to publish. Fix these, or pass --force if "
                         "they are deliberate.")

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == args.org_id).first()
        if org is None:
            raise SystemExit("No organization with id %r." % args.org_id)
        platform = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                    if org.platform_id else None)
        # NOT `app_base_url`. That is the host the React app is served from,
        # and its catch-all route answers every unknown path with the SPA — so
        # a customer site address built from it returns 200 and renders "That
        # page doesn't exist", which this script then printed as the address to
        # hand the customer. See customer_sites.site_base_url.
        base = customer_sites.site_base_url(platform)

        existing = customer_sites.resolve(db, args.slug)
        print("Organization : %s (%s)" % (org.name, org.id))
        print("Slug         : %s" % args.slug)
        print("Source       : %s (%d bytes)"
              % (args.file, len(html.encode("utf-8"))))
        print("Consent      : %s"
              % ("%d characters, stored verbatim" % len(consent_text)
                 if consent_text else "none - the page must not ask for it"))
        print("Action       : %s"
              % ("REPLACE the page already at this address"
                 if existing is not None else "CREATE a new page"))
        if existing is not None and existing.organization_id != org.id:
            raise SystemExit("That address belongs to another customer.")
        url = customer_sites.public_url(base, args.slug)
        print("Public URL   : %s"
              % (url or "NONE — this brand has no `sites_base_url` set, so "
                        "there is no address to hand the customer. The page "
                        "will still publish and still be served at "
                        "/site/%s on the backend." % args.slug))

        if not args.apply:
            print("\nDRY RUN. Nothing was written. Re-run with --apply.")
            return

        site = customer_sites.publish(
            db, organization=org, slug=args.slug, html=html,
            title=args.title or org.name,
            consent_text=consent_text,
            consent_version=args.consent_version,
            default_owner_user_id=args.owner_user_id)
        print("\nPublished. %d views and %d enquiries recorded so far."
              % (site.view_count or 0, site.inquiry_count or 0))
    finally:
        db.close()


if __name__ == "__main__":
    main()
