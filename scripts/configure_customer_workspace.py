#!/usr/bin/env python
"""Configure a customer's workspace as DATA. No customer is named in here.

WHAT THIS IS FOR. A vertical arrives - energy retail, commercial cleaning -
and the work of making the platform look like that customer's own system is
four settings and a list of screens. Doing it by hand through four different
screens is how one of them gets missed; doing it in a migration is how a
customer's configuration ends up in engine code. So: one script, arguments,
dry run by default.

WHAT IT WILL NOT DO:

  * It will not create an organization. `customer_provisioning` owns that, and
    a second door into tenant creation is how two of the same customer appear.
  * It will not invent a brand. The organization's `platform_id` decides what
    it inherits, and this script never sets it.
  * It will not overwrite a customer's own edits unless told to. Industry
    migration already classifies each surface as empty / untouched-default /
    customized and leaves customized alone; this refuses to replace a
    non-empty `workspace_views` without --replace-views for the same reason.
  * It sends nothing. No invitation, no notification, no email.

USAGE

    python scripts/configure_customer_workspace.py --org-id <id>            \
        --industry energy                                                  \
        --views config/workspace-views/<file>.json                         \
        --features leads,reports,users,master_dashboard,branding_settings  \
        --brand-name "..." --primary-color "#176bff"                       \
        [--replace-views] [--apply]

Nothing is written without --apply. Run it without, read what it says it will
do, then run it again with.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.deps import SessionLocal                      # noqa: E402
from app.models.models import Organization, Platform   # noqa: E402
from app.services import entitlements                  # noqa: E402
from app.services import industry_templates            # noqa: E402
from app.services import workspace_views               # noqa: E402
from app.services.tier_config_service import (          # noqa: E402
    seed_default_tier_definitions)


def _load_views(path):
    """The screens, validated by the same parser the product uses.

    Validated HERE rather than at render time on purpose: a view the server
    would silently drop should fail this script loudly, while somebody is
    still looking at it.
    """
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list):
        raise SystemExit("%s must contain a JSON list of views." % path)
    parsed = workspace_views._parse(raw)
    if len(parsed) != len(raw):
        kept = {v["key"] for v in parsed}
        dropped = [v.get("key") or "(no key)" for v in raw
                   if isinstance(v, dict) and v.get("key") not in kept]
        raise SystemExit(
            "These views would be dropped by the server and are refused "
            "here: %s" % ", ".join(str(d) for d in dropped))
    return raw, parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org-id", required=True)
    parser.add_argument("--industry", help="Industry key or label; normalized "
                                           "through industry_templates.")
    parser.add_argument("--views", help="Path to a JSON list of workspace views.")
    parser.add_argument("--replace-views", action="store_true",
                        help="Replace views this customer already has.")
    parser.add_argument("--features", help="Comma-separated enabled_features "
                                           "allow-list.")
    parser.add_argument("--brand-name")
    parser.add_argument("--brand-logo-url")
    parser.add_argument("--primary-color")
    parser.add_argument("--accent-color")
    parser.add_argument("--support-email")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write. Without this, nothing changes.")
    args = parser.parse_args()

    views_raw = views_parsed = None
    if args.views:
        views_raw, views_parsed = _load_views(args.views)

    features = None
    if args.features:
        requested = [k.strip() for k in args.features.split(",") if k.strip()]
        unknown = [k for k in requested if k not in entitlements.FEATURES]
        if unknown:
            raise SystemExit(
                "Unknown feature key(s): %s\nKnown keys: %s"
                % (", ".join(unknown), ", ".join(entitlements.ALL_FEATURE_KEYS)))
        features = entitlements.normalize_keys(requested)

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.id == args.org_id).first()
        if org is None:
            raise SystemExit("No organization with id %r." % args.org_id)

        platform = (db.query(Platform).filter(Platform.id == org.platform_id).first()
                    if org.platform_id else None)

        print("Organization : %s (%s)" % (org.name, org.id))
        print("Brand        : %s" % (platform.name if platform else
                                     "(none — it will inherit nothing)"))
        print("")

        changes = []

        if args.industry:
            resolved = industry_templates.normalize(args.industry)
            if not industry_templates.is_known(args.industry):
                print("  ! %r is not a known industry; it normalizes to %r."
                      % (args.industry, resolved))
            if (org.industry or "") != resolved:
                changes.append(("industry", org.industry, resolved))
                if args.apply:
                    org.industry = resolved
            else:
                print("  = industry already %r" % resolved)

        if features is not None:
            current = org.enabled_features
            after = json.dumps(features)
            if (current or "") != after:
                changes.append(("enabled_features", current, after))
                if args.apply:
                    org.enabled_features = after
            else:
                print("  = enabled_features already as requested")

        if views_raw is not None:
            existing = (org.workspace_views or "").strip()
            if existing and existing not in ("null",) and not args.replace_views:
                raise SystemExit(
                    "This customer already has workspace views configured. "
                    "Pass --replace-views if you mean to replace them.")
            after = json.dumps(views_raw)
            if (org.workspace_views or "") != after:
                changes.append(("workspace_views",
                                "%d view(s)" % len(workspace_views._parse(existing)),
                                ", ".join(v["key"] for v in views_parsed)))
                if args.apply:
                    org.workspace_views = after
            else:
                print("  = workspace_views already as requested")

        for attr, value in (("brand_name", args.brand_name),
                            ("brand_logo_url", args.brand_logo_url),
                            ("brand_color_primary", args.primary_color),
                            ("brand_color_accent", args.accent_color),
                            ("support_email", args.support_email)):
            if value is None:
                continue
            if (getattr(org, attr) or "") != value:
                changes.append((attr, getattr(org, attr), value))
                if args.apply:
                    setattr(org, attr, value)

        if not changes:
            print("Nothing to change.")
            return

        for name, before, after in changes:
            print("  %s %-20s %s -> %s"
                  % ("~" if args.apply else "?", name,
                     (str(before)[:40] if before else "(unset)"),
                     str(after)[:60]))

        if not args.apply:
            print("\nDRY RUN. Nothing was written. Re-run with --apply.")
            return

        db.commit()

        # AFTER the industry is committed, not before: the tier set is chosen
        # from the organization's stored industry, and seeding against the old
        # one is how a customer ends up with another vertical's AI tracks.
        if args.industry:
            seeded = seed_default_tier_definitions(
                db, org.id, industry=org.industry)
            db.commit()
            print("  ~ tier definitions   %s"
                  % ("seeded from the %s set" % org.industry if seeded
                     else "already present, left alone"))

        print("\nApplied.")
        print("Screens now in this workspace's rail: %s"
              % (", ".join(v["label"] for v in workspace_views.for_organization(org))
                 or "(none)"))
    finally:
        db.close()


if __name__ == "__main__":
    main()
