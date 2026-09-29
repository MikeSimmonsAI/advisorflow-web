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
    # A PLATFORM MODULE, NOT A BRAND'S FEATURE. The wholesale engine is shared
    # code gated by this one key, so EvoSys Pro, BookaBoost and any future
    # white-label brand enable it per customer organization rather than forking
    # a copy. It REQUIRES `leads` (see REQUIRES below) because a property owner
    # IS a Lead in this platform — that is what gives the module DNC, consent,
    # suppression and message history without a second implementation of any of
    # them. An organization with this key and no `leads` is the incoherent
    # configuration the WUPA note below records, in a new shape.
    "wholesale_real_estate": "Wholesale real estate acquisition and disposition",

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
    "wholesale_real_estate": ("leads",),
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


def _session_of(org: Optional[Organization]) -> Optional[Session]:
    if org is None:
        return None
    try:
        from sqlalchemy.orm import object_session
        return object_session(org)
    except Exception:  # noqa: BLE001
        return None


def enabled_for(org: Optional[Organization]) -> Optional[List[str]]:
    """The org's EFFECTIVE feature list, or None meaning 'everything'.

    HIERARCHICAL SINCE 2026-09-28. The stored allow-list is now the ORGANIZATION
    layer of a chain (platform -> brand -> organization; see
    app/services/entitlement_resolver.py). When no platform/brand/org override
    row touches this organization the answer is exactly the legacy one - the
    same value, the same type, None still meaning "everything". Only when an
    override exists is the list computed by the resolver, which is how a
    brand-level disable (e.g. BookaBoost switching off wholesale_real_estate)
    reaches every organization of that brand without editing their allow-lists.
    """
    legacy = legacy_enabled_for(org)
    db = _session_of(org)
    if db is None:
        return legacy
    from app.services import entitlement_resolver as er
    ov = er.load_overrides(db, org, include_narrow=False)
    if not ov.any_org_level():
        return legacy
    return er.effective_org_list(db, org, ov)


def legacy_enabled_for(org: Optional[Organization]) -> Optional[List[str]]:
    """The org's stored allow-list, or None meaning 'everything' (legacy orgs).

    This is the ORGANIZATION layer only. Enforcement goes through
    `enabled_for` / `org_has_feature`, which add the platform and brand layers.
    """
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
    db = _session_of(org)
    ov = None
    if db is not None:
        from app.services import entitlement_resolver as er
        ov = er.load_overrides(db, org, include_narrow=False)
    return _org_has_feature_with(db, org, key, ov)


def _org_has_feature_with(db: Optional[Session], org: Optional[Organization], key: str,
                          ov) -> bool:
    """Organization-level answer given ALREADY-LOADED override rows (or None)."""
    if ov is not None and ov.any_org_level() and key in FEATURES:
        from app.services import entitlement_resolver as er
        return er.org_enabled(db, org, key, ov)
    allowed = legacy_enabled_for(org)
    if allowed is None:
        return True
    return key in allowed


def _role_in(db: Session, org: Organization, user: User) -> Optional[str]:
    """Role IN `org`: membership role; users.role only for the home org (or an
    executive observation of `org`); None in another org the person holds no
    membership in. One rule, shared with workspace_location."""
    from app.services.workspace_location import workspace_role_in
    return workspace_role_in(db, user, org.id)


def _has_person_rows(ov) -> bool:
    return ov is not None and any(r.scope in ("role", "user") for r in ov.rows)


def _has_workspace_rows(ov) -> bool:
    return ov is not None and any(r.scope == "workspace" for r in ov.rows)


def _workspace_allowed_with(db: Session, org: Organization, key: str, ov,
                            location_ids=(), cache=None) -> bool:
    """The WORKSPACE (location) narrowing layer, given already-loaded rows.

    ENFORCED SINCE 2026-09-28 (it was stored but decorative before). The
    location ids come from `workspace_location.resolve`: the selected location,
    the one location a person is assigned to, or - for a person assigned to
    several with none selected - ALL of them, and the feature must then be
    allowed in EVERY one (most restrictive). No ids = organization level.
    """
    if org is None or key not in FEATURES or not location_ids or not _has_workspace_rows(ov):
        return True
    from app.services import entitlement_resolver as er
    c = cache if cache is not None else er.enforcement_cache()
    for wid in location_ids:
        res = er.evaluate(db, key, org=org, level="workspace", workspace_id=wid,
                          overrides=ov, check_setup=False, _cache=c)
        if not res["enabled"]:
            return False
    return True


def workspace_has_feature(db: Session, org: Optional[Organization], key: str,
                          location_ids=()) -> bool:
    """Organization AND workspace (location) layers for `key`. For callers that
    hold an org and resolved location ids but no user."""
    if org is None:
        return False
    from app.services import entitlement_resolver as er
    ov = er.load_overrides(db, org)
    return (_org_has_feature_with(db, org, key, ov)
            and _workspace_allowed_with(db, org, key, ov, tuple(location_ids or ())))


def feature_allowed_for_request(db: Session, org: Optional[Organization], user: User,
                                key: str, request: Optional[Request] = None) -> bool:
    """The full non-raising answer require_feature gives, for code that must
    DECIDE rather than refuse (e.g. whether to include a module in a payload).

    platform -> brand -> organization -> workspace (location resolved from the
    request, never widened by an invalid selection) -> role -> user.
    god_admin is not narrowed, matching require_feature.
    """
    if getattr(user, "role", None) == "god_admin":
        return True
    if org is None or key not in FEATURES:
        return org is not None
    from app.services import entitlement_resolver as er
    from app.services import workspace_location as wl
    ov = er.load_overrides(db, org)
    if not _org_has_feature_with(db, org, key, ov):
        return False
    role = _role_in(db, org, user)
    ctx = wl.resolve(db, user, org.id, request, role=role, strict=False)
    return (_workspace_allowed_with(db, org, key, ov, ctx.location_ids)
            and _user_allowed_with(db, org, user, key, ov, role))


def _user_allowed_with(db: Session, org: Organization, user: User, key: str, ov,
                       role: Optional[str] = None, cache=None) -> bool:
    """Narrowing layers for one person, given already-loaded rows."""
    if org is None or key not in FEATURES or not _has_person_rows(ov):
        return True
    from app.services import entitlement_resolver as er
    if role is None:
        role = _role_in(db, org, user)
    res = er.evaluate(db, key, org=org, level="user", role=role,
                      user_id=getattr(user, "id", None), overrides=ov,
                      check_setup=False,
                      _cache=cache if cache is not None else er.enforcement_cache())
    return bool(res["enabled"])


def user_has_feature(db: Session, org: Optional[Organization], user: User,
                     key: str, request: Optional[Request] = None) -> bool:
    """The NARROWING layers (workspace/role/user overrides) for one person.

    Only ever removes access. `require_feature` does not call this - it loads
    the override rows ONCE and uses the `_with` helpers - but it is kept as a
    convenience for callers holding just an org and a user. The WORKSPACE
    (location) layer is resolved from `request` (or the ambient request) the
    same way require_feature does, except that an invalid selection falls back
    to the implicit rule instead of raising (never wider than a valid one).
    """
    if org is None or key not in FEATURES:
        return True
    if getattr(user, "role", None) == "god_admin":
        return True
    from app.services import entitlement_resolver as er
    ov = er.load_overrides(db, org)
    role = None
    if _has_workspace_rows(ov):
        from app.services import workspace_location as wl
        if request is None:
            from app.services.lead_scope import _ambient_request
            request = _ambient_request()
        role = _role_in(db, org, user)
        ctx = wl.resolve(db, user, org.id, request, role=role, strict=False)
        if not _workspace_allowed_with(db, org, key, ov, ctx.location_ids):
            return False
    return _user_allowed_with(db, org, user, key, ov, role)


def nav_features(db: Session, org: Optional[Organization], user: Optional[User],
                 legacy_list: Optional[List[str]],
                 location_ids=()) -> Optional[List[str]]:
    """What GET /branding/org tells the customer shell it may draw.

    `legacy_list` is what that endpoint computed before hierarchy existed; it is
    returned UNCHANGED when no override row touches this organization. With
    overrides, the organization's effective list is used and then narrowed by
    any role/user override for this person - the same evaluation
    `require_feature` enforces, so the sidebar cannot offer what the server
    will refuse. One override query and at most one role lookup per call.

    `location_ids` is the request's resolved WORKSPACE (location) context
    (app/services/workspace_location.py). A workspace override that switches a
    feature off removes it here exactly as require_feature refuses it.
    """
    if org is None or db is None:
        return legacy_list
    from app.services import entitlement_resolver as er
    ov = er.load_overrides(db, org)
    if not ov.rows:
        return legacy_list
    base = er.effective_org_list(db, org, ov) if ov.any_org_level() else legacy_list
    location_ids = tuple(location_ids or ())
    narrow_ws = bool(location_ids) and _has_workspace_rows(ov)
    narrow_person = user is not None and _has_person_rows(ov)
    if not narrow_ws and not narrow_person:
        return base
    role = _role_in(db, org, user) if narrow_person else None
    cache = er.enforcement_cache()
    keys = list(ALL_FEATURE_KEYS) if base is None else list(base)
    return [k for k in keys
            if k not in FEATURES
            or ((not narrow_ws or _workspace_allowed_with(db, org, k, ov, location_ids, cache))
                and (not narrow_person
                     or _user_allowed_with(db, org, user, k, ov, role, cache)))]


def set_features(db: Session, org: Organization, actor: User,
                 keys: Optional[List[str]]) -> List[str]:
    """Replace an organization's allow-list. None restores the legacy 'all'."""
    from app.routers.audit_log_router import log_action

    # THE STORED ALLOW-LIST, never the resolved list. Writing the resolved
    # list back would bake a brand disable (or an org override) into the
    # allow-list, and resetting that override would then change nothing.
    before = legacy_enabled_for(org)
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
    """The STORED allow-list, as it always was - plus, only when a platform /
    brand / org override touches this organization, an `effective` block.

    Every field outside `effective` describes `organizations.enabled_features`
    and is what editors (PUT /god/customers/{id}/features, the Control Center
    switches) read and write back. The resolved answer lives ONLY under
    `effective` (and `available[].effective_enabled`), so it can never be
    round-tripped into the allow-list.
    """
    allowed = legacy_enabled_for(org)
    plan = getattr(org, "plan", None) if org is not None else None
    preset = plan_features(plan)
    report = _stored_report(allowed, plan, preset)
    db = _session_of(org)
    if db is not None:
        from app.services import entitlement_resolver as er
        ov = er.load_overrides(db, org, include_narrow=False)
        if ov.any_org_level():
            eff = er.effective_org_list(db, org, ov)
            eff_set = set(eff)
            for item in report["available"]:
                item["effective_enabled"] = item["key"] in eff_set
            report["effective"] = {
                "overrides_active": True,
                "enabled": eff,
                "enabled_count": len(eff),
                "differs_from_allow_list": sorted(
                    k for k in ALL_FEATURE_KEYS
                    if (k in eff_set) != (allowed is None or k in allowed)),
                "note": "Platform/brand/organization overrides apply. `enabled` above is "
                        "the stored allow-list; this block is what is enforced.",
            }
    return report


def _stored_report(allowed, plan, preset) -> Dict:
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

    def _dep(request: Request = None,
             user: User = Depends(get_current_user),
             db: Session = Depends(get_db)) -> User:
        # FastAPI injects `request` by its annotation. A direct call (the
        # access diagnostic calls this with user/db only) reads the ambient
        # request instead - the diagnostic publishes its synthetic one there.
        if request is None:
            from app.services.lead_scope import _ambient_request
            request = _ambient_request()
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
        # ONE override query for the whole decision (platform + brand + org +
        # rows scoped inside the org); both checks below reuse it.
        ov = None
        if org is not None:
            from app.services import entitlement_resolver as _er
            ov = _er.load_overrides(db, org)
        if not _org_has_feature_with(db, org, key, ov):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="This organization is not enabled for '%s' (%s). An operator can "
                       "enable it in the customer's Features settings."
                       % (key, FEATURES[key]))
        # WORKSPACE (LOCATION) LAYER — enforced, no longer decorative.
        #
        # The location comes from X-Workspace-Location, validated against this
        # organization and the caller's UserLocation assignments (an invalid
        # selection is refused with 403 inside resolve()). With no header, a
        # person assigned to one location is evaluated there and a person
        # assigned to several is evaluated against ALL of them, so leaving the
        # header off can never step around a workspace that switched the
        # feature off. Refused with the SAME status as the organization-level
        # refusal above (402, "not enabled"): to the customer both mean the
        # module is not switched on where they are working.
        # See app/services/workspace_location.py.
        # Cost: nothing extra unless a location was selected or this
        # organization has workspace rows at all.
        from app.services import workspace_location as _wl
        role = None
        loc_ids = ()
        if _wl.requested_location(request) or _has_workspace_rows(ov):
            role = _role_in(db, org, user)
            loc_ctx = _wl.resolve(db, user, org_id, request, role=role, strict=True)
            loc_ids = loc_ctx.location_ids
        else:
            loc_ctx = None
        if not _workspace_allowed_with(db, org, key, ov, loc_ids):
            where = (loc_ctx.selected.name if loc_ctx is not None and loc_ctx.selected is not None
                     else "one of your assigned locations")
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="'%s' (%s) is switched off for this workspace location (%s). An "
                       "operator can change it in Feature Entitlements."
                       % (key, FEATURES[key], where))
        # Role/user overrides can only NARROW (entitlement_resolver).
        if not _user_allowed_with(db, org, user, key, ov, role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="'%s' (%s) is switched off for your role or account in this "
                       "organization." % (key, FEATURES[key]))

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
