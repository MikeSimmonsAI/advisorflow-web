"""HIERARCHICAL FEATURE ENTITLEMENT RESOLUTION.

`entitlements.py` owns the vocabulary (FEATURES, REQUIRES, PLAN_FEATURES) and
the customer's commercial allow-list (`organizations.enabled_features`). This
module adds the layers ABOVE and BELOW that allow-list, stored as explicit
override rows (app/models/entitlement_models.py), and answers two questions
with the same code:

    evaluate()  what is the effective state of feature K for this target?
    explain()   why? - the chain of decisions, layer by layer.

═══════════════════════════════════════════════════════════════════════════
PRECEDENCE — WHAT THE SPEC SAYS AND HOW IT IS IMPLEMENTED
═══════════════════════════════════════════════════════════════════════════

The spec's words (sections 10-12, 16, 22 and the approved mockups):

  * "Organizations inherit from platform/brand and receive controlled
    overrides."  Both approved Feature Entitlements mockups show a feature with
    Brand = OFF and Organization = Override ON resolving to ENABLED
    (Skip Tracing / Wholesale Real Estate).
  * "Workspaces inherit organization configuration and support NARROWER
    operational settings."
  * "Role/User access determines who can use an AVAILABLE capability."

So the rule is:

  1. OFFER (platform -> brand). Every registered feature is offered by default.
     An explicit platform row sets it for the whole deployment; an explicit
     brand row beats the platform row for that brand's organizations.
  2. ORGANIZATION. An explicit org override row beats platform and brand, in
     either direction (the mockup case). With no org row, the organization
     gets the feature when it is offered AND its commercial allow-list
     (`enabled_features`; NULL = everything) includes it. This is what closes
     the BookaBoost bypass: a brand-level disable now removes the module from
     every organization of that brand that has not been explicitly overridden,
     whatever its allow-list says.
  3. WORKSPACE / ROLE / USER may only NARROW. A "disabled" row there removes
     the feature; an "enabled" row can never grant what the organization does
     not have (it is reported as "capped by organization").
     All three are ENFORCED at request time. "Workspace" is a customer
     Location; the request's location is resolved by
     app/services/workspace_location.py (X-Workspace-Location, validated
     against UserLocation assignments; implicit when the person has exactly
     one location; most restrictive across all of them when they have several
     and select none) and applied by entitlements.require_feature and
     entitlements.nav_features (GET /branding/org).
  4. DEPENDENCIES (entitlements.REQUIRES). If a prerequisite is effectively
     disabled BY AN EXPLICIT OVERRIDE somewhere in its chain, the dependent
     feature is Blocked by Dependency and is refused. A prerequisite missing
     only from a legacy allow-list is reported (as dependency_gaps always has
     been) but does NOT change access - that is the pre-existing behaviour and
     changing it would switch modules off for customers like WUPA overnight.
  5. REQUIRES SETUP is a display state layered on an ENABLED feature, only when
     a real configuration signal says so (customer_readiness: stored Twilio
     credentials, connected calendars, locations with hours). It never changes
     access, and features with no truthful signal never claim it.

NO ROWS => THE OLD ANSWER. When no override row touches an organization, the
enforcement functions in entitlements.py return exactly what they returned
before this module existed (tests/test_entitlements_resolver.py proves it for
every feature and representative organizations).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, inspect as sa_inspect, or_
from sqlalchemy.orm import Session

from app.models.entitlement_models import (
    PLATFORM_SCOPE_ID, SCOPES, STATES, FeatureOverride, FeatureOverrideEvent,
)
from app.models.models import Organization, Platform
from app.utils.time_fmt import iso_utc  # S19: explicit-UTC timestamps

# ── Catalog metadata (presentation only; the registry is entitlements.FEATURES)
CATEGORIES: Dict[str, str] = {
    "leads": "Core Platform",
    "users": "Core Platform",
    "imports": "Core Platform",
    "compliance": "Core Platform",
    "audit_log": "Core Platform",
    "branding_settings": "Administration",
    "tier_config": "Lead Intelligence",
    "lead_cleanup": "Lead Intelligence",
    "sms": "Communications",
    "email": "Communications",
    "voice": "Communications",
    "campaigns": "Communications",
    "cadences": "Communications",
    "crm": "CRM & Contacts",
    "crm_connectors": "Integrations",
    "case_files": "CRM & Contacts",
    "booking": "Scheduling & Booking",
    "calendar": "Scheduling & Booking",
    "availability": "Scheduling & Booking",
    "reports": "Reporting & Analytics",
    "master_dashboard": "Reporting & Analytics",
    "ai_assist": "AI & Automation",
    "wholesale_real_estate": "Vertical",
}
DEFAULT_CATEGORY = "Other"

STATE_ENABLED = "enabled"
STATE_DISABLED = "disabled"
STATE_REQUIRES_SETUP = "requires_setup"
STATE_BLOCKED = "blocked_by_dependency"

# Features with a TRUTHFUL setup signal, and which readiness probe answers it.
# Anything not listed here never reports "requires setup".
SETUP_PROBES: Dict[str, str] = {
    "sms": "twilio",
    "voice": "twilio",
    "calendar": "calendar",
    "booking": "booking",
}


def category_of(key: str) -> str:
    return CATEGORIES.get(key, DEFAULT_CATEGORY)


# ── Loading override rows ───────────────────────────────────────────────────

_TABLE_PRESENT: Dict[int, bool] = {}


def _table_present(db: Session) -> bool:
    """feature_overrides exists? Checked once per engine.

    A missing table (a database create_all has not reached yet) must mean
    "no overrides", never a failed statement inside the caller's transaction.
    """
    try:
        bind = db.get_bind()
    except Exception:  # noqa: BLE001
        return False
    key = id(bind)
    # Only a POSITIVE answer is cached: a table created after startup (a later
    # create_all) is picked up on the next call instead of being ignored forever.
    if not _TABLE_PRESENT.get(key):
        # Inspect through the SESSION'S OWN connection. Inspecting the engine
        # checks out a connection and resets it on return; under StaticPool
        # (tests) that is the session's connection, and the reset rolled back
        # the caller's open transaction.
        try:
            present = sa_inspect(db.connection()).has_table(
                FeatureOverride.__tablename__)
        except Exception:  # noqa: BLE001
            return False
        if present:
            _TABLE_PRESENT[key] = True
        return bool(present)
    return True


def mark_table_present(db: Session) -> None:
    try:
        _TABLE_PRESENT[id(db.get_bind())] = True
    except Exception:  # noqa: BLE001
        pass


class Overrides:
    """All override rows relevant to one organization (or one brand), indexed."""

    def __init__(self, rows: Iterable[FeatureOverride]):
        self.rows = list(rows)
        self._idx: Dict[Tuple[str, str, str, str], FeatureOverride] = {}
        for r in self.rows:
            self._idx[(r.scope, r.scope_id, r.org_id or "", r.feature_key)] = r

    def get(self, scope: str, scope_id: Optional[str], key: str,
            org_id: str = "") -> Optional[FeatureOverride]:
        if scope_id is None:
            return None
        return self._idx.get((scope, scope_id, org_id or "", key))

    def __bool__(self) -> bool:
        return bool(self.rows)

    def any_org_level(self) -> bool:
        """Any row that can change an organization-level answer."""
        return any(r.scope in ("platform", "brand", "org") for r in self.rows)

    def any_narrowing(self) -> bool:
        return any(r.scope in ("role", "user", "workspace") for r in self.rows)


def load_overrides(db: Optional[Session], org: Optional[Organization] = None,
                   platform_id: Optional[str] = None,
                   include_narrow: bool = True) -> Overrides:
    """Rows for platform-global, the brand, the org, and (optionally) rows
    scoped inside the org (workspace/role/user). One query."""
    if db is None or not _table_present(db):
        return Overrides([])
    pid = platform_id if platform_id is not None else (
        getattr(org, "platform_id", None) if org is not None else None)
    clauses = [and_(FeatureOverride.scope == "platform",
                    FeatureOverride.scope_id == PLATFORM_SCOPE_ID)]
    if pid:
        clauses.append(and_(FeatureOverride.scope == "brand",
                            FeatureOverride.scope_id == pid))
    if org is not None and getattr(org, "id", None):
        clauses.append(and_(FeatureOverride.scope == "org",
                            FeatureOverride.scope_id == org.id))
        if include_narrow:
            clauses.append(FeatureOverride.org_id == org.id)
    rows = db.query(FeatureOverride).filter(or_(*clauses)).all()
    return Overrides(rows)


# ── Setup probes (real configuration only) ──────────────────────────────────

def _setup_status(db: Session, org: Organization, key: str,
                  cache: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    probe = SETUP_PROBES.get(key)
    if probe is None or org is None or db is None:
        return None
    if probe not in cache:
        try:
            from app.services import customer_readiness as cr
            fn = {"twilio": cr._twilio, "calendar": cr._calendar,
                  "booking": cr._booking}[probe]
            cache[probe] = fn(db, org)
        except Exception:  # noqa: BLE001
            cache[probe] = None
    res = cache[probe]
    if not res:
        return None
    configured = res.get("status") == "CONFIGURED"
    return {"probe": probe, "status": res.get("status"),
            "configured": configured, "reason": res.get("reason")}


# ── Evaluation ──────────────────────────────────────────────────────────────

def _row_info(row: Optional[FeatureOverride]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {"id": row.id, "state": row.state, "reason": row.reason,
            "actor_user_id": row.actor_user_id,
            "updated_at": iso_utc(row.updated_at),
            "created_at": iso_utc(row.created_at)}


def _brand_label(db: Optional[Session], platform_id: Optional[str],
                 cache: Dict[str, Any]) -> str:
    if not platform_id:
        return "Brand"
    if cache.get("__no_labels__"):
        # Enforcement paths never show the label; don't spend a query on it.
        return "Brand"
    ck = "brand:" + platform_id
    if ck not in cache:
        name = None
        if db is not None:
            p = db.query(Platform).filter(Platform.id == platform_id).first()
            name = p.name if p else None
        cache[ck] = "Brand (%s)" % name if name else "Brand"
    return cache[ck]


def evaluate(db: Optional[Session], key: str, *,
             org: Optional[Organization] = None,
             platform_id: Optional[str] = None,
             level: str = "org",
             workspace_id: Optional[str] = None,
             role: Optional[str] = None,
             user_id: Optional[str] = None,
             overrides: Optional[Overrides] = None,
             legacy_allowed: Any = "__unset__",
             check_setup: bool = True,
             _cache: Optional[Dict[str, Any]] = None,
             _visiting: Optional[set] = None) -> Dict[str, Any]:
    """Effective state of `key` at `level` (platform|brand|org|workspace|role|user).

    Returns a dict with: enabled (bool, what enforcement uses), state (display),
    layers (per-layer decision), inherited_from, decided_by_override,
    dependencies, setup.
    """
    from app.services import entitlements as ent

    if key not in ent.FEATURES:
        raise KeyError(key)
    cache = _cache if _cache is not None else {}
    visiting = set(_visiting or ())
    visiting.add(key)
    if org is not None and platform_id is None:
        platform_id = getattr(org, "platform_id", None)
    if overrides is None:
        overrides = load_overrides(db, org, platform_id)
    order = SCOPES
    depth = order.index(level) if level in order else order.index("org")

    layers: List[Dict[str, Any]] = []

    # 1. platform
    prow = overrides.get("platform", PLATFORM_SCOPE_ID, key)
    enabled = True if prow is None else prow.state == STATE_ENABLED
    source = "Platform" if prow is not None else "Platform default"
    decisive = prow
    layers.append({"layer": "platform", "label": "Platform",
                   "scope_id": PLATFORM_SCOPE_ID,
                   "override": _row_info(prow),
                   "state": prow.state if prow else "default_enabled",
                   "result": enabled})

    # 2. brand
    if depth >= 1:
        brow = overrides.get("brand", platform_id, key) if platform_id else None
        blabel = _brand_label(db, platform_id, cache)
        if brow is not None:
            enabled = brow.state == STATE_ENABLED
            source, decisive = blabel, brow
        layers.append({"layer": "brand", "label": blabel, "scope_id": platform_id,
                       "override": _row_info(brow),
                       "state": brow.state if brow else ("inherited" if platform_id else "no_brand"),
                       "result": enabled})

    # 3. organization
    if depth >= 2:
        org_id = getattr(org, "id", None)
        orow = overrides.get("org", org_id, key) if org_id else None
        if legacy_allowed == "__unset__":
            legacy_allowed = ent.legacy_enabled_for(org) if org is not None else None
        in_allow = True if legacy_allowed is None else (key in legacy_allowed)
        if orow is not None:
            enabled = orow.state == STATE_ENABLED
            source, decisive = "Organization (Override)", orow
            ostate = orow.state
        elif not in_allow:
            if enabled:
                source = "Organization plan / allow-list"
                decisive = None
            enabled = False
            ostate = "not_in_allow_list"
        else:
            ostate = "inherited"
        layers.append({"layer": "org", "label": "Organization", "scope_id": org_id,
                       "override": _row_info(orow), "state": ostate,
                       "allow_list": ("all" if legacy_allowed is None else "includes"
                                      if in_allow else "excludes"),
                       "result": enabled})

    # 4-6. narrowing layers
    org_id = getattr(org, "id", None) or ""
    narrow = (("workspace", workspace_id, "Workspace"),
              ("role", role, "Role"),
              ("user", user_id, "User"))
    applies = {"workspace": depth >= 3, "role": depth >= 4, "user": depth >= 5}
    for scope, sid, label in narrow:
        if not applies[scope] or sid is None:
            continue
        row = overrides.get(scope, sid, key, org_id)
        st = "inherited"
        if row is not None:
            if row.state == STATE_DISABLED:
                if enabled:
                    source, decisive = "%s (Override)" % label, row
                enabled = False
                st = STATE_DISABLED
            else:
                st = STATE_ENABLED if enabled else "capped_by_organization"
        layers.append({"layer": scope, "label": label, "scope_id": sid,
                       "override": _row_info(row), "state": st, "result": enabled})

    decided_by_override = decisive is not None

    # dependencies
    deps: List[Dict[str, Any]] = []
    blocked = False
    for req in ent.REQUIRES.get(key, ()):
        if req in visiting:
            continue
        sub = evaluate(db, req, org=org, platform_id=platform_id, level=level,
                       workspace_id=workspace_id, role=role, user_id=user_id,
                       overrides=overrides, legacy_allowed=legacy_allowed,
                       check_setup=False, _cache=cache, _visiting=visiting)
        met = bool(sub["enabled"])
        enforced = (not met) and sub["decided_by_override"]
        if enforced:
            blocked = True
        deps.append({"key": req, "label": ent.FEATURES.get(req, req), "met": met,
                     "state": sub["state"], "inherited_from": sub["inherited_from"],
                     "blocks_access": enforced})

    setup = None
    if enabled and not blocked and check_setup and depth >= 2 and org is not None:
        setup = _setup_status(db, org, key, cache)

    final_enabled = enabled and not blocked
    if blocked and enabled:
        state = STATE_BLOCKED
    elif not enabled:
        state = STATE_DISABLED
    elif setup is not None and not setup["configured"]:
        state = STATE_REQUIRES_SETUP
    else:
        state = STATE_ENABLED

    return {
        "key": key, "level": level, "enabled": final_enabled, "state": state,
        "inherited_from": source, "decided_by_override": decided_by_override,
        "decisive_override": _row_info(decisive),
        "layers": layers, "dependencies": deps,
        "dependencies_met": all(d["met"] for d in deps),
        "setup": setup,
    }


def enforcement_cache() -> Dict[str, Any]:
    """A cache for enforcement-only evaluations: no label queries."""
    return {"__no_labels__": True}


def org_enabled(db: Optional[Session], org: Organization, key: str,
                overrides: Optional[Overrides] = None,
                _cache: Optional[Dict[str, Any]] = None) -> bool:
    return evaluate(db, key, org=org, level="org", overrides=overrides,
                    check_setup=False,
                    _cache=_cache if _cache is not None else enforcement_cache())["enabled"]


def effective_org_list(db: Optional[Session], org: Organization,
                       overrides: Optional[Overrides] = None) -> List[str]:
    from app.services import entitlements as ent
    ov = overrides if overrides is not None else load_overrides(db, org)
    legacy = ent.legacy_enabled_for(org)
    cache: Dict[str, Any] = enforcement_cache()
    return [k for k in ent.ALL_FEATURE_KEYS
            if evaluate(db, k, org=org, level="org", overrides=ov,
                        legacy_allowed=legacy, check_setup=False,
                        _cache=cache)["enabled"]]


def explain(db: Session, key: str, *, org: Optional[Organization] = None,
            platform_id: Optional[str] = None, level: str = "org",
            workspace_id: Optional[str] = None, role: Optional[str] = None,
            user_id: Optional[str] = None) -> Dict[str, Any]:
    """WHY DOES THIS ORGANIZATION / USER HAVE THIS FEATURE — as sentences
    generated from the same evaluation enforcement uses."""
    from app.services import entitlements as ent
    res = evaluate(db, key, org=org, platform_id=platform_id, level=level,
                   workspace_id=workspace_id, role=role, user_id=user_id)
    steps = []
    for l in res["layers"]:
        ov = l.get("override")
        if l["layer"] == "platform":
            txt = ("Platform override: %s." % ov["state"] if ov
                   else "Platform default: every registered feature is offered.")
        elif l["layer"] == "brand":
            if l["state"] == "no_brand":
                txt = "Organization has no brand assigned; nothing to inherit from a brand."
            elif ov:
                txt = "%s override: %s." % (l["label"], ov["state"])
            else:
                txt = "%s: no override, inherits the platform decision." % l["label"]
        elif l["layer"] == "org":
            if ov:
                txt = "Organization override: %s (beats platform and brand)." % ov["state"]
            elif l["state"] == "not_in_allow_list":
                txt = "Organization's plan / allow-list does not include this feature."
            else:
                txt = ("No organization override; allow-list %s it, so the inherited "
                       "decision stands." % ("grants every feature to" if l.get("allow_list") == "all"
                                             else "includes"))
        else:
            if l["state"] == "capped_by_organization":
                txt = ("%s override says enabled, but narrower scopes cannot grant what "
                       "the organization does not have." % l["label"])
            elif ov:
                txt = "%s override: %s." % (l["label"], ov["state"])
            else:
                txt = "%s: no override, inherits." % l["label"]
        steps.append(dict(l, explanation=txt))
    for d in res["dependencies"]:
        if d["met"]:
            d["explanation"] = "Requires %s: met." % d["key"]
        elif d["blocks_access"]:
            d["explanation"] = ("Requires %s, which is disabled by an explicit override: "
                                "access is blocked." % d["key"])
        else:
            d["explanation"] = ("Requires %s, which is missing from the allow-list: reported "
                                "as a configuration gap, access is not changed." % d["key"])
    res["steps"] = steps
    res["feature_label"] = ent.FEATURES[key]
    res["summary"] = "%s is %s for this %s (decided by: %s)." % (
        ent.FEATURES[key], res["state"].replace("_", " "), level, res["inherited_from"])
    return res


# ── Writes ──────────────────────────────────────────────────────────────────

def _norm_scope(scope: str, scope_id: Optional[str], org_id: Optional[str]) -> Tuple[str, str, str]:
    scope = (scope or "").strip().lower()
    if scope not in SCOPES:
        raise ValueError("scope must be one of %s" % ", ".join(SCOPES))
    if scope == "platform":
        return scope, PLATFORM_SCOPE_ID, ""
    if not scope_id:
        raise ValueError("scope_id is required for scope %s" % scope)
    if scope in ("workspace", "role", "user"):
        if not org_id:
            raise ValueError("org_id is required for scope %s" % scope)
        return scope, scope_id, org_id
    return scope, scope_id, ""


def set_override(db: Session, *, scope: str, scope_id: Optional[str], feature_key: str,
                 state: str, actor_user_id: Optional[str], reason: Optional[str] = None,
                 org_id: Optional[str] = None) -> FeatureOverride:
    from datetime import datetime
    from app.services import entitlements as ent
    if feature_key not in ent.FEATURES:
        raise ValueError("Unknown feature key: %s" % feature_key)
    if state not in STATES:
        raise ValueError("state must be enabled or disabled")
    scope, sid, oid = _norm_scope(scope, scope_id, org_id)
    row = db.query(FeatureOverride).filter(
        FeatureOverride.scope == scope, FeatureOverride.scope_id == sid,
        FeatureOverride.org_id == oid, FeatureOverride.feature_key == feature_key).first()
    prev = row.state if row else None
    now = datetime.utcnow()
    if row is None:
        row = FeatureOverride(scope=scope, scope_id=sid, org_id=oid, feature_key=feature_key,
                              state=state, reason=reason, actor_user_id=actor_user_id,
                              created_at=now, updated_at=now)
        db.add(row)
    else:
        row.state, row.reason, row.actor_user_id, row.updated_at = state, reason, actor_user_id, now
    db.add(FeatureOverrideEvent(scope=scope, scope_id=sid, org_id=oid, feature_key=feature_key,
                                action="set", previous_state=prev, new_state=state,
                                reason=reason, actor_user_id=actor_user_id, at=now))
    db.flush()
    mark_table_present(db)
    return row


def reset_override(db: Session, *, scope: str, scope_id: Optional[str], feature_key: str,
                   actor_user_id: Optional[str], reason: Optional[str] = None,
                   org_id: Optional[str] = None) -> Optional[str]:
    """Delete the row (back to inherited). Returns the previous state, or None
    when there was nothing to reset (no event is written then)."""
    from datetime import datetime
    scope, sid, oid = _norm_scope(scope, scope_id, org_id)
    row = db.query(FeatureOverride).filter(
        FeatureOverride.scope == scope, FeatureOverride.scope_id == sid,
        FeatureOverride.org_id == oid, FeatureOverride.feature_key == feature_key).first()
    if row is None:
        return None
    prev = row.state
    db.delete(row)
    db.add(FeatureOverrideEvent(scope=scope, scope_id=sid, org_id=oid, feature_key=feature_key,
                                action="reset", previous_state=prev, new_state=None,
                                reason=reason, actor_user_id=actor_user_id,
                                at=datetime.utcnow()))
    db.flush()
    return prev
