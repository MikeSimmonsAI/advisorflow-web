"""THE PROVIDER CAPABILITY REGISTRY - what each provider can actually supply,
per capability, per tenant.

A provider being configured does not mean every capability is operational.
For each (provider, capability) the registry reports, never merged:

    PLATFORM STATE  configured   credentials present / product purchased
                    blocked      a public source refused the platform
    TENANT STATE    enabled      this workspace switched it on
                    reachable    the last call reached it
                    healthy      this CAPABILITY last succeeded more recently
                                 than it failed (per-capability health)
                    operational  may be called for this capability right now

and one state word: OPERATIONAL | UNVERIFIED | DEGRADED | NOT ENABLED |
BLOCKED | NOT CONFIGURED | MANUAL ONLY | SANDBOX. "Connected" is never shown
merely because credentials exist: UNVERIFIED means configured and enabled but
never proven to work.

The capability vocabulary below is the product's. Each maps onto the engine's
existing capability constants (evosense/common.py) - there is one registry of
providers (evosense/providers.py PROVIDERS), not two.
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.services.evosense import common as C
from app.services.evosense import providers as PV

# product capability -> engine capabilities that supply it
CAPABILITY_MAP: Dict[str, List[str]] = {
    "OWNER_IDENTITY": [C.OWNERSHIP, C.ENTITY_RESOLUTION],
    "PHONE": [C.CONTACT_ENRICHMENT],
    "EMAIL": [C.CONTACT_ENRICHMENT],
    "LINE_TYPE": [C.PHONE_VALIDATION, C.CONTACT_ENRICHMENT],
    "PHONE_VALIDATION": [C.PHONE_VALIDATION],
    "EMAIL_VALIDATION": [C.EMAIL_VALIDATION],
    "PROPERTY_FACTS": [C.ASSESSOR, C.PARCEL, C.PROPERTY_SEARCH],
    "TAX": [C.TAX],
    "DEED": ["DEED"],
    "LIEN": ["LIEN"],
    "MORTGAGE": ["MORTGAGE"],
    "FORECLOSURE": [C.FORECLOSURE],
    "CODE_VIOLATION": [C.CODE_VIOLATION],
    "SOLD_COMPS": [C.COMPS],
    "LISTING_HISTORY": [C.LISTING],
    "VALUATION": [C.VALUATION],
    "IMAGERY": ["IMAGERY"],
    "GEOCODING": [C.GEOCODING],
}
LABELS = {k: k.replace("_", " ").title() for k in CAPABILITY_MAP}
LABELS.update({"SOLD_COMPS": "Sold comparable sales", "VALUATION": "Valuation (AVM - never ARV)",
               "LINE_TYPE": "Phone line type"})

S_OPERATIONAL, S_UNVERIFIED, S_DEGRADED = "OPERATIONAL", "UNVERIFIED", "DEGRADED"
S_NOT_ENABLED, S_BLOCKED, S_NOT_CONFIGURED = "NOT ENABLED", "BLOCKED", "NOT CONFIGURED"
S_MANUAL, S_SANDBOX = "MANUAL ONLY", "SANDBOX"
S_EVALUATION = "EVALUATION ONLY"
S_GATED = "TRUTH GATE NOT MET"


def _existing_cfg(db, org_id, key):
    from app.models.evosense_models import EvoSenseProviderConfig
    return (db.query(EvoSenseProviderConfig)
            .filter(EvoSenseProviderConfig.organization_id == org_id,
                    EvoSenseProviderConfig.provider_key == key).first())


def provider_capability(db, org_id: str, p, engine_caps: List[str], cfg=None) -> Dict[str, Any]:
    """The state of provider `p` for the capability served by `engine_caps`.
    Reads only - never creates a config row."""
    cfg = cfg if cfg is not None else _existing_cfg(db, org_id, p.key)
    kind = p.connector_kind
    configured = kind != C.INTERFACE_ONLY and (not p.required_env or p.is_configured())
    platform_block = PV.platform_blocked(db, p.key, cfg) if cfg is not None else PV.platform_blocked(db, p.key)
    enabled = kind in (C.MANUAL, C.IMPORT) or bool(getattr(cfg, "enabled", False))
    canon = PV.canonical(p, cfg, blocked=platform_block) if cfg is not None else None
    health = C.jload(getattr(cfg, "capability_health", None), {}) or {}
    cap_rows = [health[c] for c in engine_caps if c in health]
    last_ok = max((r.get("last_success_at") or "" for r in cap_rows), default="")
    last_bad = max((r.get("last_failure_at") or "" for r in cap_rows), default="")
    failures = max((int(r.get("failures") or 0) for r in cap_rows), default=0)
    if cap_rows:
        healthy = bool(last_ok) and last_ok > last_bad
        health_basis = "capability"
    elif health:
        # This provider tracks health per capability and has never succeeded
        # at THIS one: another capability's success proves nothing here.
        healthy, health_basis = False, "capability"
    else:
        # No call has been recorded per capability yet (tracking began with
        # this registry): fall back to the provider's own last success /
        # failure, and SAY that is the basis rather than showing "never".
        healthy = bool(canon and canon.get("healthy"))
        health_basis = "provider" if (getattr(cfg, "last_success_at", None)
                                      or getattr(cfg, "last_failure_at", None)) else None
        ok_at, bad_at = getattr(cfg, "last_success_at", None), getattr(cfg, "last_failure_at", None)
        last_ok = ok_at.isoformat() + "Z" if ok_at else ""
        last_bad = bad_at.isoformat() + "Z" if bad_at else ""
    reachable = (bool(last_ok) or bool(canon and canon.get("reachable"))) if (cap_rows or canon) else None

    if kind in (C.MANUAL, C.IMPORT):
        # Nothing is called: reachability and health do not apply.
        reachable = healthy = None
        state = S_MANUAL
    elif platform_block or (canon and canon.get("blocked")):
        state = S_BLOCKED
    elif not configured:
        state = S_NOT_CONFIGURED
    elif not enabled:
        state = S_NOT_ENABLED
    elif failures >= PV.DEGRADE_AFTER or (canon and canon.get("failed")):
        state = S_DEGRADED
    elif kind == C.SANDBOX:
        state = S_SANDBOX
    elif healthy:
        state = S_OPERATIONAL
    else:
        state = S_UNVERIFIED
    evaluation_only = bool(getattr(p, "evaluation_only", False))
    gated = False
    if C.COMPS in engine_caps and kind == C.REAL and state in (S_OPERATIONAL, S_UNVERIFIED):
        from app.services.evosense import truth_gate as TG
        gated = not TG.passes(db, org_id, p.key)
    if evaluation_only and state in (S_OPERATIONAL, S_UNVERIFIED):
        state = S_EVALUATION
    elif gated:
        state = S_GATED
    operational = (not evaluation_only and not gated and state in (S_OPERATIONAL, S_UNVERIFIED, S_SANDBOX)
                   and bool(canon and canon.get("routable")))
    why = {S_MANUAL: "Entered by a person or imported from a file",
           S_BLOCKED: "Blocked: %s" % ((canon or {}).get("why") or "the source refused the platform"),
           S_NOT_CONFIGURED: ("Not purchased - interface only" if kind == C.INTERFACE_ONLY
                              else "Credentials not configured on the platform"),
           S_NOT_ENABLED: "Not enabled for this workspace",
           S_DEGRADED: "Failing for this capability (%s consecutive failures)%s" % (
               failures, ": %s" % cap_rows[-1].get("reason") if cap_rows and cap_rows[-1].get("reason") else ""),
           S_SANDBOX: "Synthetic sandbox adapter - software testing only, never real data",
           S_OPERATIONAL: ("Last call for this capability succeeded" if health_basis == "capability"
                           else "Last call to this provider succeeded (not yet recorded per capability)"),
           S_UNVERIFIED: "Configured and enabled, never proven to work - not 'connected'",
           S_GATED: "DFW sold-comps truth gate not met - what the price is, its origin, coverage, "
                    "freshness and our storage / display rights must all be established first",
           S_EVALUATION: "Provider evaluation only - not approved for production; nothing in "
                         "discovery, enrichment or outreach can call it"}[state]
    if evaluation_only and state in (S_NOT_CONFIGURED, S_NOT_ENABLED):
        why = "Adapter built for evaluation; %s" % why[0].lower() + why[1:]
    return {"provider": p.key, "label": p.label, "connector_kind": kind, "state": state, "why": why,
            "platform": {"configured": configured, "blocked": bool(platform_block)},
            "tenant": {"enabled": enabled, "reachable": reachable, "healthy": healthy,
                       "failures": failures, "last_success_at": last_ok or None,
                       "last_failure_at": last_bad or None, "health_basis": health_basis},
            "configured": configured, "enabled": enabled, "reachable": reachable,
            "healthy": healthy, "operational": operational, "blocked": state == S_BLOCKED,
            "synthetic": kind == C.SANDBOX, "evaluation_only": evaluation_only,
            "cost_cents": {c: p.costs.get(c) for c in engine_caps if p.costs.get(c) is not None}}


def matrix(db, org_id: str) -> List[Dict[str, Any]]:
    """Every product capability: its providers, each with its own state, and
    an overall answer that never claims more than the best REAL provider."""
    out = []
    for cap, engine_caps in CAPABILITY_MAP.items():
        rows = []
        for key, p in PV.PROVIDERS.items():
            if set(engine_caps) & set(p.capabilities or ()):
                rows.append(provider_capability(db, org_id, p, engine_caps))
        real_ok = [r for r in rows if r["operational"] and not r["synthetic"]
                   and not r.get("evaluation_only") and r["connector_kind"] == C.REAL]
        if real_ok:
            overall = S_OPERATIONAL if any(r["state"] == S_OPERATIONAL for r in real_ok) else S_UNVERIFIED
        elif any(r["state"] == S_DEGRADED for r in rows
                 if r["connector_kind"] == C.REAL and not r.get("evaluation_only")):
            overall = S_DEGRADED
        elif any(r["state"] == S_BLOCKED for r in rows):
            overall = S_BLOCKED
        elif any(r["state"] == S_SANDBOX and r["operational"] for r in rows):
            overall = S_SANDBOX
        elif any(r["state"] == S_MANUAL for r in rows):
            overall = S_MANUAL
        else:
            overall = S_NOT_CONFIGURED
        out.append({"capability": cap, "label": LABELS[cap], "state": overall,
                    "engine_capabilities": engine_caps,
                    "real_operational": [r["provider"] for r in real_ok],
                    "providers": rows,
                    "note": {S_OPERATIONAL: "A real provider is working for this capability.",
                             S_UNVERIFIED: "A real provider is configured but not yet proven.",
                             S_DEGRADED: "The real provider is failing for this capability.",
                             S_BLOCKED: "The source refused the platform.",
                             S_SANDBOX: "Only a synthetic sandbox adapter - no real data.",
                             S_MANUAL: "Only manual entry or file import.",
                             S_NOT_CONFIGURED: "No provider can supply this yet."}[overall]})
    return out


def is_operational(db, org_id: str, capability: str, *, real_only: bool = True) -> bool:
    for row in matrix(db, org_id):
        if row["capability"] == capability:
            return bool(row["real_operational"]) if real_only else row["state"] in (
                S_OPERATIONAL, S_UNVERIFIED, S_SANDBOX)
    return False
