"""EvoSense strategies: WHAT to hunt for. (Campaigns decide HOW to work it.)

A strategy is validated, versioned on every edit, and never deleted - an
archived strategy still answers "which strategy found this property?".
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.models.evosense_models import EvoSenseStrategy
from app.services.evosense import common as C
from app.services.evosense.signals import CATALOG as SIGNALS

STATUSES = ("draft", "active", "paused", "archived")
PROPERTY_TYPES = ("single_family", "duplex", "triplex", "fourplex", "multifamily",
                  "condo", "townhouse", "mobile_home", "land", "commercial")
OCCUPANCY = ("vacant", "non_owner_occupied", "owner_occupied", "tenant", "any")
OWNER_GEO = ("any", "absentee", "out_of_state")

DEFAULT_OUTREACH = {
    # Automation only starts outreach when this is on AND the organization's
    # SMS switch is on AND the contact passes eligibility.
    "auto_outreach": False,
    "channels": ["sms"],
    "campaign": "platform_cadence",
    # Real (non-sandbox) cold SMS is refused unless the organization has
    # confirmed its own compliance configuration for this strategy.
    "cold_outreach_compliance_confirmed": False,
}
DEFAULT_NURTURE = {"allow_nurture": True, "default_days": 60}

LIST_FIELDS = ("markets", "states", "counties", "cities", "zips", "property_types",
               "occupancy_preferences", "required_signals", "preferred_signals",
               "excluded_signals")
INT_FIELDS = ("min_value", "max_value", "min_equity_pct", "min_ownership_years",
              "min_opportunity_score", "min_contact_confidence", "handoff_intent_threshold",
              "target_fee", "daily_budget_cents", "monthly_budget_cents",
              "max_cost_per_property_cents", "approval_over_cents",
              "pilot_max_properties", "pilot_max_spend_cents")
BOOL_FIELDS = ("pilot_mode", "pilot_allow_paid", "houses_only")
# A pilot is a CAP, never a target. The Wholesale pilot (Building Equity) runs
# its first controlled batch at 250-500 records; 500 is the most any pilot run
# may touch, whatever the strategy says.
PILOT_HARD_CAP = 500               # properties per pilot run, whatever the strategy says
PILOT_RECOMMENDED_MIN = 250       # first controlled batch: 250-500 (advisory, not enforced)
PILOT_DEFAULT_CAP = 50
TEXT_FIELDS = ("name", "description", "owner_geography")
EDITABLE = LIST_FIELDS + INT_FIELDS + TEXT_FIELDS + BOOL_FIELDS + (
    "provider_preferences", "outreach_policy", "nurture_policy", "enrichment_policy")


def _clean_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\n;]", value)]
    out = []
    for v in value:
        s = str(v).strip()
        if s and s not in out:
            out.append(s)
    return out


def validate(data: Dict[str, Any], partial: bool = False) -> Tuple[Dict[str, Any], List[str]]:
    """Return (clean values, problems). Problems are sentences for a person."""
    clean: Dict[str, Any] = {}
    problems: List[str] = []
    for f in LIST_FIELDS:
        if f in data:
            clean[f] = _clean_list(data[f])
    for f in INT_FIELDS:
        if f in data:
            v = data[f]
            if v in (None, ""):
                clean[f] = None
                continue
            try:
                clean[f] = int(round(float(v)))
            except (TypeError, ValueError):
                problems.append("%s must be a number." % f.replace("_", " "))
                continue
            if clean[f] < 0:
                problems.append("%s cannot be negative." % f.replace("_", " "))
    for f in BOOL_FIELDS:
        if f in data and data[f] is not None:
            clean[f] = data[f] is True or str(data[f]).strip().lower() in ("1", "true", "yes", "on")
    for f in TEXT_FIELDS:
        if f in data:
            clean[f] = (str(data[f]).strip() or None) if data[f] is not None else None
    for f in ("provider_preferences", "outreach_policy", "nurture_policy"):
        if f in data and data[f] is not None:
            if not isinstance(data[f], dict):
                problems.append("%s must be an object." % f.replace("_", " "))
            else:
                clean[f] = data[f]

    if "enrichment_policy" in data and data["enrichment_policy"] is not None:
        from app.services.evosense.enrichment import POLICIES
        pol = str(data["enrichment_policy"]).strip().lower()
        if pol not in POLICIES:
            problems.append("Enrichment policy must be one of: %s." % ", ".join(POLICIES))
        else:
            clean["enrichment_policy"] = pol
    if not partial and not clean.get("name"):
        problems.append("Give the strategy a name.")
    if "states" in clean:
        clean["states"] = [s.upper()[:2] for s in clean["states"]]
    if "property_types" in clean:
        bad = [t for t in clean["property_types"] if t not in PROPERTY_TYPES]
        if bad:
            problems.append("Unknown property type: %s." % ", ".join(bad))
    if "occupancy_preferences" in clean:
        bad = [t for t in clean["occupancy_preferences"] if t not in OCCUPANCY]
        if bad:
            problems.append("Unknown occupancy preference: %s." % ", ".join(bad))
    if clean.get("owner_geography") and clean["owner_geography"] not in OWNER_GEO:
        problems.append("Owner geography must be any, absentee or out_of_state.")
    for f in ("required_signals", "preferred_signals", "excluded_signals"):
        bad = [s for s in clean.get(f, []) if s not in SIGNALS]
        if bad:
            problems.append("Unknown signal in %s: %s." % (f.replace("_", " "), ", ".join(bad)))
    both = set(clean.get("required_signals", [])) & set(clean.get("excluded_signals", []))
    if both:
        problems.append("A signal cannot be both required and excluded: %s." % ", ".join(both))
    mn, mx = clean.get("min_value"), clean.get("max_value")
    if mn is not None and mx is not None and mn > mx:
        problems.append("Minimum value is above maximum value.")
    if clean.get("pilot_max_properties") is not None and clean["pilot_max_properties"] > PILOT_HARD_CAP:
        problems.append("A pilot is capped at %s properties per run." % PILOT_HARD_CAP)
    for f in ("min_opportunity_score", "min_contact_confidence", "handoff_intent_threshold",
              "min_equity_pct"):
        if clean.get(f) is not None and clean[f] > 100:
            problems.append("%s must be 0-100." % f.replace("_", " "))
    return clean, problems


def apply(strategy: EvoSenseStrategy, clean: Dict[str, Any]) -> None:
    if clean.get("pilot_mode"):
        # A pilot never works owners by itself, whatever else was sent.
        pol = dict(clean.get("outreach_policy") or C.jload(getattr(strategy, "outreach_policy", None), {}) or {})
        pol["auto_outreach"] = False
        clean = {**clean, "outreach_policy": pol}
    for k, v in clean.items():
        if k in LIST_FIELDS or k in ("provider_preferences", "outreach_policy",
                                     "nurture_policy"):
            setattr(strategy, k, C.jdump(v))
        else:
            setattr(strategy, k, v)


def get(db, org_id: str, strategy_id: str) -> EvoSenseStrategy:
    s = (db.query(EvoSenseStrategy)
         .filter(EvoSenseStrategy.id == strategy_id,
                 EvoSenseStrategy.organization_id == org_id).first())
    if s is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return s


def lst(strategy, field) -> List[str]:
    return C.jload(getattr(strategy, field, None), []) or []


def outreach_policy(strategy) -> Dict[str, Any]:
    return {**DEFAULT_OUTREACH, **(C.jload(strategy.outreach_policy, {}) or {})}


def nurture_policy(strategy) -> Dict[str, Any]:
    return {**DEFAULT_NURTURE, **(C.jload(strategy.nurture_policy, {}) or {})}


TRANSITIONS = {
    "activate": (("draft", "paused"), "active"),
    "pause": (("active",), "paused"),
    "resume": (("paused",), "active"),
    "archive": (("draft", "active", "paused"), "archived"),
}


def transition(strategy: EvoSenseStrategy, action: str) -> str:
    if action not in TRANSITIONS:
        raise HTTPException(status_code=400, detail="Unknown action")
    allowed, to = TRANSITIONS[action]
    if strategy.status not in allowed:
        raise HTTPException(status_code=409, detail="A %s strategy cannot be %sd." % (
            strategy.status, action.rstrip("e")))
    if to == "active":
        problems = activation_problems(strategy)
        if problems:
            raise HTTPException(status_code=409, detail=" ".join(problems))
        strategy.activated_at = strategy.activated_at or C.now()
    if to == "archived":
        strategy.archived_at = C.now()
    strategy.status = to
    return to


def activation_problems(strategy) -> List[str]:
    out = []
    if not (lst(strategy, "states") or lst(strategy, "counties") or lst(strategy, "cities")
            or lst(strategy, "zips") or lst(strategy, "markets")):
        out.append("Say where EvoSense should hunt (at least one state, county, city, ZIP or market).")
    if not lst(strategy, "property_types"):
        out.append("Choose at least one property type.")
    return out


def _money(v):
    if v is None:
        return None
    return "$%sK" % format(v / 1000.0, ",.0f") if v >= 1000 else "$%s" % v


def summary(strategy) -> str:
    """The sentence the operator reads before activating."""
    where = lst(strategy, "counties") or lst(strategy, "cities") or lst(strategy, "zips") \
        or lst(strategy, "markets") or lst(strategy, "states")
    kind = "counties" if lst(strategy, "counties") else ""
    where_s = (" and ".join([", ".join(where[:-1]), where[-1]]) if len(where) > 1
               else (where[0] if where else "(nowhere yet)"))
    types = [t.replace("_", "-") for t in lst(strategy, "property_types")] or ["any"]
    parts = ["EvoSense will search %s%s for %s properties" % (
        where_s, (" " + kind) if kind and not where_s.lower().endswith("county") else "",
        "/".join(types))]
    if strategy.min_value is not None or strategy.max_value is not None:
        if strategy.min_value is not None and strategy.max_value is not None:
            parts.append("valued between %s and %s" % (_money(strategy.min_value),
                                                       _money(strategy.max_value)))
        elif strategy.min_value is not None:
            parts.append("valued above %s" % _money(strategy.min_value))
        else:
            parts.append("valued below %s" % _money(strategy.max_value))
    if strategy.min_equity_pct:
        parts.append("with at least %s%% estimated equity" % strategy.min_equity_pct)
    s1 = " ".join(parts) + "."
    if getattr(strategy, "houses_only", False):
        s1 += " Houses only: vacant lots, land-only accounts and commercial property are skipped."
    pref = [SIGNALS[x]["label"].lower() for x in lst(strategy, "preferred_signals") if x in SIGNALS]
    s2 = ""
    if pref:
        s2 = " %s properties receive additional priority." % (
            (", ".join(pref[:-1]) + " and " + pref[-1]) if len(pref) > 1 else pref[0]).capitalize()
    req = [SIGNALS[x]["label"].lower() for x in lst(strategy, "required_signals") if x in SIGNALS]
    if req:
        s2 += " Every property must show: %s." % ", ".join(req)
    exc = [SIGNALS[x]["label"].lower() for x in lst(strategy, "excluded_signals") if x in SIGNALS]
    if exc:
        s2 += " Properties showing %s are skipped." % ", ".join(exc)
    budget = strategy.daily_budget_cents or 0
    s3 = (" Paid data acquisition is limited to %s/day." % C.money(budget).replace(".00", "")
          if budget else " EvoSense will not buy any paid data (daily budget $0).")
    if strategy.monthly_budget_cents:
        s3 += " Monthly ceiling %s." % C.money(strategy.monthly_budget_cents).replace(".00", "")
    s4 = " It hands an opportunity to you when seller intent reaches %s." % (
        strategy.handoff_intent_threshold)
    pol = outreach_policy(strategy)
    s5 = (" Eligible owners are worked automatically through the cadence engine."
          if pol.get("auto_outreach") else
          " Outreach starts only when you start it.")
    if is_pilot(strategy):
        s5 += (" PILOT / CONTROLLED: at most %s properties per run, %s, no outreach of any kind, "
               "and it only runs when a person starts it." % (
                   pilot_cap(strategy),
                   "paid data up to %s" % C.money(pilot_spend_cap(strategy)) if pilot_spend_cap(strategy)
                   else "no paid data"))
    return s1 + s2 + s3 + s4 + s5


# ── READY-MADE STARTER SEARCHES ─────────────────────────────────────────────
# One click creates a DRAFT the person reviews and activates; nothing here
# starts a hunt. Every starter: houses only, the DFW counties, $0 paid data,
# no automatic outreach, best scores first.

DFW_COUNTIES = ["Dallas", "Tarrant", "Collin", "Denton", "Ellis", "Kaufman", "Rockwall",
                "Parker", "Johnson"]
HOUSE_TYPES = ["single_family", "duplex", "triplex", "fourplex", "townhouse", "condo"]

STARTERS = {
    "behind_on_taxes": {
        "name": "Behind on Taxes",
        "description": "Houses with unpaid property taxes or a tax lawsuit.",
        "data": "Works today on free data: county tax-sale lists (9 DFW counties) and the Tarrant tax roll.",
        "free_today": True,
        "fields": {"preferred_signals": ["TAX_DELINQUENT", "TAX_SUIT", "LIEN"]},
    },
    "tired_landlord": {
        "name": "Tired Landlord",
        "description": "Rental houses held 10+ years by an owner who lives somewhere else.",
        "data": "Works today on free data: Dallas, Tarrant and Collin owner records.",
        "free_today": True,
        "fields": {"owner_geography": "absentee", "min_ownership_years": 10,
                   "occupancy_preferences": ["non_owner_occupied"],
                   "preferred_signals": ["ABSENTEE_OWNER", "OUT_OF_STATE_OWNER", "TIRED_LANDLORD",
                                         "CODE_COMPLAINT", "CODE_VIOLATION", "TAX_DELINQUENT"]},
    },
    "problem_property": {
        "name": "Problem Property",
        "description": "Houses with code complaints or a poor condition rating from the appraiser.",
        "data": "Works today on free data inside Dallas and Fort Worth (city code records, Dallas appraiser rating).",
        "free_today": True,
        "fields": {"counties": ["Dallas", "Tarrant"],
                   "preferred_signals": ["CODE_VIOLATION", "CODE_COMPLAINT", "CDU_POOR",
                                         "DISTRESSED_CONDITION", "VACANT"]},
    },
    "long_time_owner": {
        "name": "Long-Time Owner",
        "description": ("Houses owned 20+ years. Long ownership does NOT prove the house is paid off - "
                        "mortgage status stays unverified until a lender record or the owner says so."),
        "data": "Works today on free data: deed dates from Dallas, Tarrant and Collin owner records.",
        "free_today": True,
        "fields": {"min_ownership_years": 20,
                   "preferred_signals": ["FREE_AND_CLEAR", "ABSENTEE_OWNER", "TAX_DELINQUENT"]},
    },
    "pre_foreclosure": {
        "name": "Pre-Foreclosure",
        "description": "Houses with a posted foreclosure notice.",
        "data": ("Fed by foreclosure-notice lists you import (Properties > Import list, list type "
                 "Foreclosure notices) - county postings, or a PropStream export if you ever have one."),
        "free_today": False,
        "fields": {"preferred_signals": ["PRE_FORECLOSURE", "TAX_DELINQUENT", "VACANT"]},
    },
    "probate": {
        "name": "Probate / Inherited",
        "description": ("Houses in an estate or probate case. A matching last name alone is never treated "
                        "as an inheritance."),
        "data": "Fed by probate / estate lists you import (Properties > Import list), or a PropStream export.",
        "free_today": False,
        "fields": {"preferred_signals": ["PROBATE", "ESTATE", "VACANT", "TAX_DELINQUENT"]},
    },
}


def starter_values(key: str) -> Dict[str, Any]:
    st = STARTERS[key]
    base = {"name": st["name"], "description": st["description"], "states": ["TX"],
            "counties": list(DFW_COUNTIES), "property_types": list(HOUSE_TYPES), "houses_only": True,
            "min_opportunity_score": 45, "min_contact_confidence": 60, "handoff_intent_threshold": 70,
            "daily_budget_cents": 0, "owner_geography": "any",
            "outreach_policy": dict(DEFAULT_OUTREACH), "nurture_policy": dict(DEFAULT_NURTURE)}
    base.update(st["fields"])
    return base


def starters_payload() -> List[Dict[str, Any]]:
    return [{"key": k, "name": v["name"], "description": v["description"], "data": v["data"],
             "free_today": v["free_today"]} for k, v in STARTERS.items()]


def is_pilot(strategy) -> bool:
    return bool(getattr(strategy, "pilot_mode", False))


def pilot_cap(strategy) -> int:
    v = getattr(strategy, "pilot_max_properties", None) or PILOT_DEFAULT_CAP
    return max(1, min(int(v), PILOT_HARD_CAP))


def pilot_spend_cap(strategy) -> int:
    """Cents a pilot may spend in total. Paid data off = $0, whatever is set."""
    if not getattr(strategy, "pilot_allow_paid", False):
        return 0
    return max(0, int(getattr(strategy, "pilot_max_spend_cents", None) or 0))


def payload(strategy) -> Dict[str, Any]:
    out = {"id": strategy.id, "name": strategy.name, "description": strategy.description,
           "status": strategy.status, "version": strategy.version,
           "is_test": bool(strategy.is_test),
           "created_at": strategy.created_at.isoformat() + "Z" if strategy.created_at else None,
           "activated_at": strategy.activated_at.isoformat() + "Z" if strategy.activated_at else None,
           "last_hunt_at": strategy.last_hunt_at.isoformat() + "Z" if strategy.last_hunt_at else None,
           "owner_geography": strategy.owner_geography or "any",
           "outreach_policy": outreach_policy(strategy),
           "nurture_policy": nurture_policy(strategy),
           "provider_preferences": C.jload(strategy.provider_preferences, {}) or {},
           "enrichment_policy": getattr(strategy, "enrichment_policy", None) or "standard",
           "summary": summary(strategy)}
    for f in LIST_FIELDS:
        out[f] = lst(strategy, f)
    for f in INT_FIELDS:
        out[f] = getattr(strategy, f)
    for f in BOOL_FIELDS:
        out[f] = bool(getattr(strategy, f, False))
    out["pilot"] = {"on": is_pilot(strategy), "record_cap": pilot_cap(strategy) if is_pilot(strategy) else None,
                    "spend_cap_cents": pilot_spend_cap(strategy) if is_pilot(strategy) else None,
                    "hard_cap": PILOT_HARD_CAP}
    return out


def geography_match(strategy, prop) -> Tuple[bool, str]:
    """Deterministic: is this property inside the strategy's hunting ground?"""
    def norm(s):
        return re.sub(r"\s+county$", "", (s or "").strip().lower())
    checks = []
    zips = lst(strategy, "zips")
    if zips:
        checks.append((prop.zip_code or "")[:5] in [z[:5] for z in zips])
    counties = [norm(c) for c in lst(strategy, "counties")]
    if counties:
        checks.append(norm(prop.county) in counties)
    cities = [c.strip().lower() for c in lst(strategy, "cities")]
    if cities:
        checks.append((prop.city or "").strip().lower() in cities)
    states = lst(strategy, "states")
    if states and (prop.state or "").upper()[:2] not in states:
        return False, "outside the strategy's states"
    if checks and not any(checks):
        return False, "outside the strategy's counties / cities / ZIPs"
    return True, "inside the strategy's hunting ground"
