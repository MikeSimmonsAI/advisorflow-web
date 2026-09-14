"""FEATURE ENTITLEMENT — enforced on the server, where it counts.

`Organization.enabled_features` already existed: a JSON allow-list, written by
`PATCH /org-settings/features`, read by exactly two places — the org settings
payload and the org list. Nothing in the backend consulted it before doing work.
Enforcement lived in `Layout.jsx` and `Overview.jsx`, which decide whether to
draw a nav item.

That is not entitlement, it is decoration. main.py already says so about a
different feature, in a comment that turned out to apply here too:

    "Hiding the nav item is not access control - the Lead Scraper already
     taught us that."

So this module adds the missing half. `require_feature("campaigns")` is a real
dependency that returns 402 when a customer is not entitled to the thing they
just asked for, whatever the browser chose to render.

THE TWO-KEY RULE. The mission's wording is "Organization feature entitlement and
user permission remain separate. Access requires BOTH." Those are genuinely
different questions - "did this customer buy campaigns" and "is this particular
advisor allowed to send one" - and collapsing them is how a customer who paid
for a feature finds every one of their staff able to use it. This file answers
only the first. Role guards (`require_admin` and friends) answer the second, and
both must pass.

NULL IS NOT EMPTY. An organization whose `enabled_features` is NULL predates
entitlement and keeps everything, exactly as the column's own comment says.
Customers created by `customer_provisioning.create_customer` start with `[]` -
an explicit empty allow-list - so new customers start with nothing switched on
rather than everything. Reading NULL as "deny" would have switched off every
existing customer the moment this shipped.
"""

import json
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import Organization, User

# The registry. A feature that is not here cannot be granted, which stops the
# allow-list quietly filling with typos that grant nothing and are never noticed.
FEATURES: Dict[str, str] = {
    # ── Operational features: what the customer's staff DO all day ──────────
    "leads":        "Lead management and the leads list",
    "campaigns":    "Bulk campaigns and campaign builder",
    "cadences":     "Automated multi-touch cadences",
    "sms":          "Outbound SMS",
    "email":        "Outbound email",
    "voice":        "Outbound and inbound voice / AI calling",
    "crm":          "Native CRM contacts and pipeline",
    "crm_connectors": "CRM connectors (GoHighLevel, HubSpot)",
    "booking":      "Public booking pages and appointment scheduling",
    "calendar":     "Calendar connections and availability",
    "availability": "Advisor availability management",
    "reports":      "Reporting and analytics",
    "imports":      "CSV and bulk data import",
    "ai_assist":    "AI drafting, classification and suggestions",
    "compliance":   "Suppression lists and DNC handling",
    "case_files":   "Case files / family file review",

    # ── Admin features: sellable, and still only FEATURES ───────────────────
    #
    # THE SIDEBAR HAS GATED ON THESE SEVEN FOR A LONG TIME AND THE SERVER HAD
    # NEVER HEARD OF THEM. `Layout.jsx` asked `isFeatureEnabled('master_dashboard')`
    # while this registry held fourteen entirely different keys, and
    # `OrgManager.jsx` carried a third list again, complete with a plan -> feature
    # map that priced them. Anything written through the un-normalized
    # `PATCH /org-settings/features` therefore landed in `enabled_features` as a
    # key nothing recognised, and the God Features screen - which renders from
    # THIS dict - reported it as unknown. That was the warning.
    #
    # They are registered rather than deleted because OrgManager's PLAN_FEATURES
    # shows they are part of the commercial model: trial includes the master
    # dashboard and users, growth adds lead cleanup. They are real things
    # customers buy. Registering them is what makes one vocabulary out of three.
    "users":            "User management for the organization",
    "master_dashboard": "Team performance dashboard",
    "branding_settings": "Organization branding and settings",
    "audit_log":        "Audit log",
    "tier_config":      "Lead tier configuration",
    "lead_cleanup":     "Lead cleanup and duplicate merging",

    # `a2p_10dlc` IS DELIBERATELY ABSENT, and it is the eighth key the sidebar
    # used to ask for. A2P brand and campaign registration is not something a
    # customer USES, it is infrastructure somebody ADMINISTERS - and an A2P
    # brand binds permanently to the Twilio account that registers it. It moved
    # to CAPABILITIES in app/services/capabilities.py, behind both delegation
    # gates. Putting it back here would make enabling a feature hand over the
    # power to re-register the customer's carrier identity, which is the exact
    # collapse the two-gate model exists to prevent.
}

ALL_FEATURE_KEYS = tuple(sorted(FEATURES))


# ── WHAT A MODULE CANNOT WORK WITHOUT ───────────────────────────────────────
#
# FOUND LIVE, AND IT COST TWO CUSTOMERS THEIR LEAD BOOK. WUPA had `campaigns`,
# `lead_cleanup`, `tier_config` and `crm` enabled, 4,000 leads in the database,
# 778 of them assigned to one advisor — and `leads` switched OFF. Fiber Cartel
# was in the same state with 237. Every one of those modules exists to do
# something TO leads: the campaign builder filters a lead list, cleanup merges
# duplicate leads, tier config classifies leads. An allow-list that sells them
# without `leads` is not a narrower product, it is an incoherent one.
#
# This did no visible harm while `leads` was ungated. The moment entitlements
# were actually enforced, those organizations lost the module the other four
# were operating on, and their advisors got a dashboard of refusals.
#
# THIS MAP DOES NOT GRANT ANYTHING. Nothing reads it to widen an allow-list at
# request time — a dependency that silently switched a module on would make the
# stored configuration a lie, and an operator would never see it. It is here so
# the God console can SAY that a configuration is incoherent, and so
# `plan_features()` below cannot mint another one.
REQUIRES = {
    "campaigns":      ("leads",),
    "cadences":       ("leads",),
    "lead_cleanup":   ("leads",),
    "tier_config":    ("leads",),
    "imports":        ("leads",),
    "crm_connectors": ("crm",),
    "case_files":     ("leads",),
}


def dependency_gaps(keys: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Modules in this allow-list whose prerequisite is missing from it.

    Returns [] for a legacy-open organization (None), which is entitled to
    everything and therefore cannot be missing a prerequisite.
    """
    if keys is None:
        return []
    have = set(keys)
    gaps = []
    for key in sorted(have):
        for needed in REQUIRES.get(key, ()):
            if needed not in have:
                gaps.append({
                    "feature": key,
                    "feature_label": FEATURES.get(key, key),
                    "requires": needed,
                    "requires_label": FEATURES.get(needed, needed),
                    "detail": "%s is enabled but %s is not. %s operates on "
                              "%s and cannot function without it."
                              % (FEATURES.get(key, key),
                                 FEATURES.get(needed, needed),
                                 FEATURES.get(key, key),
                                 FEATURES.get(needed, needed).lower()),
                })
    return gaps


# ── PLAN PRESETS ────────────────────────────────────────────────────────────
#
# THESE LIVED IN `OrgManager.jsx`, IN THE BROWSER. A React file decided what a
# customer was entitled to, which is how it drifted from this registry — the
# same drift the note above records for the feature keys themselves, repeated
# one level up. Presets are a server concern and now live beside the registry
# they draw from; the God console reads them from here.
#
# `leads` IS IN EVERY TIER, and its absence from all four is the defect this
# constant exists to have fixed. The rest of each tier is unchanged: this is
# not a repricing, it is the observation that a lead platform cannot sell a
# plan that does not include leads.
_CORE = ("leads",)

PLAN_FEATURES: Dict[str, Optional[List[str]]] = {
    "trial":        list(_CORE) + ["master_dashboard", "users", "reports", "availability",
                                   "tier_config", "branding_settings", "compliance", "audit_log"],
    "starter":      list(_CORE) + ["master_dashboard", "users", "reports", "availability",
                                   "tier_config", "branding_settings", "compliance", "audit_log",
                                   "campaigns"],
    "growth":       list(_CORE) + ["master_dashboard", "users", "reports", "availability",
                                   "tier_config", "branding_settings", "compliance", "audit_log",
                                   "campaigns", "lead_cleanup"],
    "professional": list(_CORE) + ["master_dashboard", "users", "reports", "availability",
                                   "tier_config", "branding_settings", "compliance", "audit_log",
                                   "campaigns", "lead_cleanup", "crm", "crm_connectors"],
    # null/None = every feature.
    "enterprise":   None,
    # Legacy alias, kept so organizations already on 'standard' still resolve.
    "standard":     list(_CORE) + ["master_dashboard", "users", "reports", "availability",
                                   "tier_config", "branding_settings", "compliance", "audit_log",
                                   "campaigns", "lead_cleanup"],
}


def plan_features(plan: Optional[str]) -> Optional[List[str]]:
    """The preset for a plan, or None for 'everything'. Unknown plan -> trial."""
    key = (plan or "trial").strip().lower()
    if key in PLAN_FEATURES:
        return PLAN_FEATURES[key]
    return PLAN_FEATURES["trial"]


def _assert_presets_are_coherent() -> None:
    """A preset that ships a dependency gap is the bug this module just fixed.

    Checked at import so it cannot be reintroduced quietly: the process refuses
    to start rather than let another organization be configured into the state
    WUPA was found in.
    """
    for plan, keys in PLAN_FEATURES.items():
        if keys is None:
            continue
        unknown = [k for k in keys if k not in FEATURES]
        if unknown:
            raise RuntimeError(
                "PLAN_FEATURES[%r] names unregistered feature(s): %s"
                % (plan, ", ".join(sorted(unknown))))
        gaps = dependency_gaps(keys)
        if gaps:
            raise RuntimeError(
                "PLAN_FEATURES[%r] is incoherent: %s"
                % (plan, "; ".join(g["detail"] for g in gaps)))


_assert_presets_are_coherent()


def normalize_keys(keys: Optional[List[str]]) -> List[str]:
    if keys is None:
        return []
    cleaned, unknown = [], []
    for k in keys:
        k = (k or "").strip().lower()
        if not k:
            continue
        if k not in FEATURES:
            unknown.append(k)
        elif k not in cleaned:
            cleaned.append(k)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail="Unknown feature key(s): %s. Valid keys: %s"
                   % (", ".join(sorted(set(unknown))), ", ".join(ALL_FEATURE_KEYS)))
    return sorted(cleaned)


def enabled_for(org: Optional[Organization]) -> Optional[List[str]]:
    """The org's allow-list, or None meaning 'everything' (legacy orgs)."""
    if org is None:
        return []
    raw = getattr(org, "enabled_features", None)
    if raw is None:
        return None            # legacy: all features
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        # A corrupt allow-list is not a licence to enable everything.
        return []
    return [k for k in val if isinstance(k, str)] if isinstance(val, list) else []


def org_has_feature(org: Optional[Organization], key: str) -> bool:
    allowed = enabled_for(org)
    if allowed is None:
        return True
    return key in allowed


def set_features(db: Session, org: Organization, actor: User,
                 keys: Optional[List[str]]) -> List[str]:
    """Replace an organization's allow-list. None restores the legacy 'all'."""
    from app.routers.audit_log_router import log_action

    before = enabled_for(org)
    if keys is None:
        org.enabled_features = None
        after = None
    else:
        after = normalize_keys(keys)
        org.enabled_features = json.dumps(after)
    db.flush()
    log_action(
        db, org.id, actor.id,
        action="customer.features_set", target_type="organization", target_id=org.id,
        platform_id=org.platform_id,
        before={"enabled_features": before}, after={"enabled_features": after},
        commit=False,
    )
    return after if after is not None else list(ALL_FEATURE_KEYS)


def feature_report(org: Optional[Organization]) -> Dict:
    allowed = enabled_for(org)
    plan = getattr(org, "plan", None) if org is not None else None
    preset = plan_features(plan)
    return {
        "mode": "all" if allowed is None else "allow_list",
        "enabled": list(ALL_FEATURE_KEYS) if allowed is None else allowed,
        "available": [{"key": k, "label": FEATURES[k],
                       "enabled": True if allowed is None else (k in allowed),
                       "requires": list(REQUIRES.get(k, ()))}
                      for k in ALL_FEATURE_KEYS],
        "enabled_count": len(ALL_FEATURE_KEYS) if allowed is None else len(allowed),

        # AN OPERATOR SURFACE, NOT A CUSTOMER ONE. Everything below says
        # "this configuration does not hold together" to the person who can
        # fix it. None of it is sent to a customer's workspace, and none of it
        # changes what that workspace is entitled to.
        "plan": plan,
        "plan_preset": preset,
        "below_plan": ([] if allowed is None or preset is None
                       else sorted(k for k in preset if k not in allowed)),
        "dependency_gaps": dependency_gaps(allowed),
    }


def require_feature(key: str):
    """Dependency factory: this route needs the customer to be entitled to `key`.

    god_admin passes — the owner operating inside a customer is not the customer
    and must be able to configure a feature that is currently switched off.
    Everyone else is checked against the stored allow-list, not against what
    their browser decided to render.
    """
    if key not in FEATURES:
        raise RuntimeError("require_feature(%r): not a registered feature key" % key)

    def _dep(user: User = Depends(get_current_user),
             db: Session = Depends(get_db)) -> User:
        if getattr(user, "role", None) == "god_admin":
            return user

        # WHICH CUSTOMER'S ENTITLEMENT — THE ONE SEAM, NOT A SECOND ONE.
        #
        # This read `users.organization_id` directly, which was the whole
        # answer while that column WAS customer tenancy. It is not any more:
        # a person can hold a customer_org membership and no column value at
        # all. `require_tenant_user` already lets exactly that person through
        # on the strength of a SELECTED workspace they hold a membership in,
        # so a feature gate judging them by the column alone let them onto the
        # route and then refused them the feature — the same request answered
        # two different ways by two different notions of "which tenant".
        #
        # `active_workspace_org_id` is that seam, and it is the same function
        # every lead query and `launch_router._caller_org_id` resolve through.
        # Its order is: a workspace the caller SELECTED and holds an active
        # membership in, then the legacy column. A selected id that is not
        # backed by a membership is discarded by `workspace_access`, so this
        # can never be widened by asserting a header — and single-context
        # customer users, who select nothing, resolve exactly as before.
        from app.services.lead_scope import active_workspace_org_id
        org_id = active_workspace_org_id(user, db)
        if org_id is None:
            # Brand-sales staff and other non-tenant identities. They have no
            # entitlement because they have no tenant; require_tenant_user is
            # the guard that explains that properly.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This is a customer workspace feature and your account has no "
                       "customer organization.")
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org_has_feature(org, key):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="This organization is not enabled for '%s' (%s). An operator can "
                       "enable it in the customer's Features settings."
                       % (key, FEATURES[key]))

        # ── Billing standing ──────────────────────────────────────────────
        # Checked AFTER the feature allow-list, so the message a customer gets
        # names the real reason. Returns None unless a brand has explicitly
        # configured a failed-payment policy - see billing_suspension_reason.
        suspended = billing_suspension_reason(db, org)
        if suspended:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=suspended)
        return user

    return _dep


def billing_suspension_reason(db: Session, org: Optional[Organization]) -> Optional[str]:
    """Should this organization's access be withdrawn over billing? Almost always None.

    ═══════════════════════════════════════════════════════════════════════
    THIS FUNCTION IS SHIPPED SWITCHED OFF, AND THAT IS THE POINT.
    ═══════════════════════════════════════════════════════════════════════

    Entitlements used to have no idea whether a customer was paying. A
    cancelled organization kept full access until a human remembered to edit
    its feature allow-list by hand. That gap is now closed in the sense that
    the wiring exists - but nothing is withdrawn from anybody until a brand
    explicitly configures a failed-payment policy.

    That restraint is deliberate. The obvious "fix" - suspend on past_due, with
    a sensible-looking grace period - would cut off a paying customer because a
    card expired on a Friday, on a schedule nobody chose. A default here is not
    a neutral technical decision; it is a business decision made by whoever
    typed the number.

    So: `billing_policy.past_due_behavior` returns configured=False for every
    brand that has not decided, and this function returns None for all of them.
    Turning it on is a configuration change in God Mode, not a code change.

    WHAT IT WILL NEVER DO, even once configured:
      - suspend an organization that has never had a subscription. A customer
        who was onboarded manually, migrated, or is mid-implementation has no
        billing status to be past due on, and reading "no subscription" as
        "not paying" would lock out exactly the customers being set up.
      - suspend on `canceled`. Cancellation is handled by customer lifecycle,
        deliberately and by a person; a subscription ending must not
        retroactively become an access decision made by a webhook.
    """
    if org is None:
        return None

    status_value = (getattr(org, "billing_status", None) or "").lower()
    if status_value not in ("past_due", "unpaid"):
        return None

    # Never suspend something that was never subscribed. A NULL customer id
    # means this organization has no billing relationship at all.
    if not getattr(org, "stripe_customer_id", None):
        return None

    from app.services import billing_policy
    behavior = billing_policy.past_due_behavior(
        db, getattr(org, "platform_id", None))

    if not behavior["configured"] or not behavior["suspends"]:
        # POLICY REQUIRED, or the brand decided not to suspend. Either way,
        # nothing is withdrawn.
        return None

    grace_days = behavior.get("grace_days")
    if grace_days:
        from datetime import datetime, timedelta
        # The grace window runs from the end of the period they last paid for,
        # not from "now" - otherwise every request would restart the clock and
        # the grace period would never expire.
        anchor = getattr(org, "billing_current_period_end", None)
        if anchor is None:
            # No period end recorded means we cannot say the grace has elapsed,
            # and "we are not sure" must not withdraw access.
            return None
        if datetime.utcnow() < anchor + timedelta(days=int(grace_days)):
            return None

    return ("This organization's subscription is past due. Access is limited "
            "until payment is updated in Billing & Plan.")
