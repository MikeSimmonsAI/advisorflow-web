#!/usr/bin/env python
"""Provision the Wholesale pilot organization: Building Equity Investments LLC.

IDEMPOTENT. Run it twice and the second run changes nothing: the organization
is found by slug (then by exact name) and reused; settings, kill switches and
pilot controls are only DEFAULTED when their row does not exist yet, so an
administrator's later choices are never overwritten.

WHAT IT DOES (through the same doors the god UI uses):
  * creates the organization with customer_provisioning.create_customer on the
    EvoSys Pro platform (industry: wholesale real estate), attributed to a
    named platform administrator (--actor-email, else the earliest god_admin);
  * applies the Wholesale blueprint's feature keys
    (org_blueprints.blueprint_feature_keys("wholesale_real_estate"));
  * creates Wholesale settings with EVERY automation OFF (no auto enrich /
    stage / qualify / analysis / buyer match, seller SMS program off, inquiry
    email off, paid enrichment caps 0);
  * creates the EvoSense kill switches with SMS, email and voice outreach
    PAUSED, and a draft pilot-control row (250 records, $0 skip-trace budget).

WHAT IT WILL NOT DO:
  * It creates NO user. Derrick Davis's email and credentials are not known
    and are not invented: the owner invites him through the normal invite flow
    once his real email is confirmed.
  * It sends nothing and calls no provider.
  * It writes nothing without --apply.

USAGE (do NOT point this at production without reading the dry run):
    python scripts/seed_building_equity.py [--actor-email owner@example.com] [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ORG_NAME = "Building Equity Investments LLC"
ORG_SLUG = "building-equity-investments"
INDUSTRY = "wholesale real estate"
PLATFORM_SLUG = "evosyspro"
PILOT_USER_NAME = "Derrick Davis"        # named for the instructions only - no account is created

SETTINGS_AUTOMATION_OFF = {
    "auto_enrich_on_import": False,
    "auto_stage_on_enrichment": False,
    "auto_qualify_on_reply": False,
    "auto_analysis_on_qualified": False,
    "auto_match_on_contract": False,
    "enrichment_auto": False,
    "sms_program_enabled": False,
    "inquiry_email_enabled": False,
    "cold_seller_email_confirmed": False,
    "enrichment_daily_cap": 0,
    "enrichment_monthly_cap": 0,
}
CONTROLS_PAUSED = {"paused_sms": True, "paused_email": True, "paused_voice": True}


def _actor(db, email):
    from app.models.models import User
    if email:
        u = db.query(User).filter(User.email == email.strip().lower()).first()
        if u is None or u.role not in ("god_admin", "super_admin"):
            raise SystemExit("--actor-email must name an existing platform administrator.")
        return u
    u = (db.query(User).filter(User.role == "god_admin")
         .order_by(User.created_at.asc()).first())
    if u is None:
        raise SystemExit("No god_admin exists to attribute this to. Pass --actor-email.")
    return u


def seed(db, *, actor_email=None, apply=False, out=print):
    """Returns a report dict. Commits only when apply=True."""
    from app.models.models import Organization, Platform
    from app.models.evosense_models import EvoSenseControl
    from app.models.wholesale_models import WholesaleSettings
    from app.models.wholesale_ops_models import WholesalePilotControl
    from app.routers.audit_log_router import log_action
    from app.services import customer_provisioning as cp
    from app.services import entitlements, org_blueprints

    report = {"organization": None, "created": [], "unchanged": [], "user_created": False}
    platform = db.query(Platform).filter(Platform.slug == PLATFORM_SLUG).first()
    if platform is None:
        raise SystemExit("Platform '%s' not found - this script never creates a brand." % PLATFORM_SLUG)
    actor = _actor(db, actor_email)

    org = (db.query(Organization).filter(Organization.slug == ORG_SLUG).first()
           or db.query(Organization).filter(Organization.name == ORG_NAME).first())
    if org is None:
        org, _ = cp.create_customer(db, actor, name=ORG_NAME, platform_id=platform.id,
                                    slug=ORG_SLUG, industry=INDUSTRY, plan="trial",
                                    timezone="America/Chicago")
        report["created"].append("organization")
    else:
        if org.platform_id != platform.id:
            raise SystemExit("An organization named %r exists on another brand - refusing to touch it."
                             % org.name)
        report["unchanged"].append("organization")
    report["organization"] = {"id": org.id, "name": org.name, "slug": org.slug,
                              "industry": org.industry}

    # Blueprint features: added to whatever is enabled, never removed.
    want = org_blueprints.blueprint_feature_keys("wholesale_real_estate")
    # The ORGANIZATION layer - the stored allow-list this script writes. (Not
    # enabled_for(): that adds the brand/platform layers, which are not ours.)
    current = entitlements.legacy_enabled_for(org)
    current = list(current) if current is not None else None
    if current is None:
        report["unchanged"].append("features (legacy all-features org)")
    else:
        merged = entitlements.normalize_keys(sorted(set(current) | set(want)))
        if set(merged) != set(current):
            org.enabled_features = json.dumps(merged)
            log_action(db, org.id, actor.id, action="customer.features_set",
                       target_type="organization", target_id=org.id,
                       platform_id=org.platform_id, before={"enabled_features": current},
                       after={"enabled_features": merged},
                       note="seed_building_equity: wholesale blueprint", commit=False)
            report["created"].append("features:%s" % ",".join(sorted(set(merged) - set(current))))
        else:
            report["unchanged"].append("features")
    report["features"] = sorted(want)

    if db.query(WholesaleSettings).filter(WholesaleSettings.organization_id == org.id).first() is None:
        db.add(WholesaleSettings(organization_id=org.id, **SETTINGS_AUTOMATION_OFF))
        report["created"].append("wholesale_settings (automation OFF)")
    else:
        report["unchanged"].append("wholesale_settings")

    if db.query(EvoSenseControl).filter(EvoSenseControl.organization_id == org.id).first() is None:
        db.add(EvoSenseControl(organization_id=org.id, **CONTROLS_PAUSED))
        report["created"].append("evosense_controls (sms/email/voice paused)")
    else:
        report["unchanged"].append("evosense_controls")

    if db.query(WholesalePilotControl).filter(WholesalePilotControl.organization_id == org.id).first() is None:
        db.add(WholesalePilotControl(organization_id=org.id, status="draft", max_records=250,
                                     skip_trace_budget_cents=0, outreach_daily_limit=0,
                                     updated_by_id=actor.id))
        report["created"].append("pilot_controls (draft, 250 cap, $0 budget)")
    else:
        report["unchanged"].append("pilot_controls")

    db.flush()
    if apply:
        db.commit()
    else:
        db.rollback()
    out(json.dumps(report, indent=2))
    out("")
    out("NEXT STEP - %s's account is NOT created by this script." % PILOT_USER_NAME)
    out("  1. Confirm his real email address with him directly.")
    out("  2. Invite him to %s through the platform's user-invite flow (role: org_admin)." % ORG_NAME)
    out("  3. He sets his own password from the invitation. No password is ever chosen for him.")
    out("  Outreach stays paused (SMS/email/voice) until an administrator turns it on.")
    if not apply:
        out("\nDRY RUN - nothing was written. Re-run with --apply.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--actor-email", default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from app.deps import SessionLocal
    import app.models.registry  # noqa: F401
    db = SessionLocal()
    try:
        seed(db, actor_email=args.actor_email, apply=args.apply)
    finally:
        db.close()


if __name__ == "__main__":
    main()
