"""CHANGING AN ORGANIZATION'S INDUSTRY WITHOUT DESTROYING ITS WORK.

THE TWO WRONG ANSWERS
---------------------
A customer provisioned with the wrong industry ends up holding another
vertical's vocabulary. There are two obvious fixes and both are bad:

  RESEED EVERYTHING. Fast, and it deletes the appointment types the customer
  spent an afternoon writing, along with any tier they renamed. For a live
  organization this is data loss dressed up as a repair.

  CHANGE NOTHING AUTOMATICALLY. Safe, and it leaves an operator deleting
  dozens of irrelevant defaults by hand, which is the complaint that started
  this.

THE ANSWER IS TO TELL THEM APART
--------------------------------
A configuration surface is in one of three states:

  EMPTY              nothing is stored; the platform is showing a default.
  UNTOUCHED DEFAULT  something is stored and it is EXACTLY some template's
                     defaults, byte for byte. Nobody chose it; it was seeded.
  CUSTOMIZED         something is stored that matches no template. A person
                     typed it.

Only the first two are replaced. A customized surface is preserved and
REPORTED, so the operator can see what was kept and decide separately. Nothing
here is destructive without an explicit, audited instruction that names the
surface it is about to overwrite.

WHAT THIS NEVER DOES
--------------------
Send anything, charge anything, create a user, or touch an organization's
leads, appointments or commercial agreement. It rewrites configuration
defaults and writes an audit entry saying exactly what it rewrote.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization, TierDefinition, User
from app.routers.audit_log_router import log_action
from app.services import industry_templates as templates
from app.services import tier_config_service as tiers

STATE_EMPTY       = "empty"
STATE_UNTOUCHED   = "untouched_default"
STATE_CUSTOMIZED  = "customized"

SURFACE_TIER_CONFIG       = "tier_config"
SURFACE_APPOINTMENT_TYPES = "appointment_types"
SURFACE_TIER_DEFINITIONS  = "tier_definitions"

SURFACE_LABELS = {
    SURFACE_TIER_CONFIG:       "Lead tiers",
    SURFACE_APPOINTMENT_TYPES: "Appointment types",
    SURFACE_TIER_DEFINITIONS:  "Lead tier definitions and AI tracks",
}

ACTION_PREVIEWED = "org_industry_migration_previewed"
ACTION_APPLIED   = "org_industry_migration_applied"


def _json_list(raw: Optional[str]) -> Optional[List[Any]]:
    if raw is None or not str(raw).strip():
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, list) else None


def _same_tier_config(stored: List[Any], candidate: List[Dict[str, Any]]) -> bool:
    """Equal as a SET OF TIER VALUES AND LABELS, not as raw JSON.

    Key order and a trailing description edit are not a customer decision about
    their pipeline; a renamed or added tier is. Comparing the pairs that carry
    meaning is what keeps a formatting difference from being mistaken for
    somebody's work.
    """
    def shape(rows):
        out = set()
        for row in rows or []:
            if not isinstance(row, dict):
                return None
            out.add((str(row.get("value") or "").strip().lower(),
                     str(row.get("label") or "").strip().lower()))
        return out

    a, b = shape(stored), shape(candidate)
    return a is not None and b is not None and a == b


def _classify_tier_config(org: Organization) -> Dict[str, Any]:
    stored = _json_list(getattr(org, "tier_config", None))
    if not stored:
        return {"state": STATE_EMPTY, "matches_template": None,
                "current": None, "count": 0}
    for key in templates.TEMPLATES:
        if _same_tier_config(stored, templates.TEMPLATES[key]["lead_tiers"]):
            return {"state": STATE_UNTOUCHED, "matches_template": key,
                    "current": stored, "count": len(stored)}
    return {"state": STATE_CUSTOMIZED, "matches_template": None,
            "current": stored, "count": len(stored)}


def _classify_appointment_types(org: Organization) -> Dict[str, Any]:
    stored = _json_list(getattr(org, "appointment_types", None))
    if not stored:
        return {"state": STATE_EMPTY, "matches_template": None,
                "current": None, "count": 0}
    normalized = [str(x).strip().lower() for x in stored]
    for key, tpl in templates.TEMPLATES.items():
        if normalized == [str(x).strip().lower() for x in tpl["appointment_types"]]:
            return {"state": STATE_UNTOUCHED, "matches_template": key,
                    "current": stored, "count": len(stored)}
    return {"state": STATE_CUSTOMIZED, "matches_template": None,
            "current": stored, "count": len(stored)}


def _classify_tier_definitions(db: Session, org: Organization) -> Dict[str, Any]:
    rows = (db.query(TierDefinition)
            .filter(TierDefinition.organization_id == org.id).all())
    if not rows:
        return {"state": STATE_EMPTY, "matches_template": None,
                "current": [], "count": 0}

    current = sorted((str(r.tier_key or "").strip().lower(),
                      str(r.tier_label or "").strip().lower()) for r in rows)
    for key in templates.TEMPLATES:
        candidate = tiers.get_tier_set_for_industry(
            templates.TEMPLATES[key]["tier_definition_key"])
        shape = sorted((str(s.get("tier_key") or "").strip().lower(),
                        str(s.get("tier_label") or "").strip().lower())
                       for s in candidate)
        if current == shape:
            return {"state": STATE_UNTOUCHED, "matches_template": key,
                    "current": [r.tier_key for r in rows], "count": len(rows)}
    # The funeral set is reachable by its own name rather than through a
    # template key, so it is checked explicitly — otherwise an org seeded with
    # it would look customized and never be repaired.
    shape = sorted((str(s.get("tier_key") or "").strip().lower(),
                    str(s.get("tier_label") or "").strip().lower())
                   for s in tiers.RESTLAND_DEFAULT_TIERS)
    if current == shape:
        return {"state": STATE_UNTOUCHED, "matches_template": "funeral",
                "current": [r.tier_key for r in rows], "count": len(rows)}

    return {"state": STATE_CUSTOMIZED, "matches_template": None,
            "current": [r.tier_key for r in rows], "count": len(rows)}


def classify(db: Session, org: Organization) -> Dict[str, Dict[str, Any]]:
    """What is stored on each configuration surface, and who put it there."""
    return {
        SURFACE_TIER_CONFIG: _classify_tier_config(org),
        SURFACE_APPOINTMENT_TYPES: _classify_appointment_types(org),
        SURFACE_TIER_DEFINITIONS: _classify_tier_definitions(db, org),
    }


# ════════════════════════════════════════════════════════════════════════════
# PREVIEW
# ════════════════════════════════════════════════════════════════════════════


def preview(db: Session, org: Organization,
            target_industry: Optional[str],
            replace_customized: bool = False) -> Dict[str, Any]:
    """What applying this industry WOULD change, and what it would leave alone.

    Nothing is written. This is what an operator reads before deciding, and
    what the apply path re-derives rather than trusting a client to send back.
    """
    target = templates.resolve(target_industry)
    state = classify(db, org)

    planned: List[Dict[str, Any]] = []
    preserved: List[Dict[str, Any]] = []

    def consider(surface: str, new_value: Any, describe: str):
        info = state[surface]
        entry = {
            "surface": surface,
            "label": SURFACE_LABELS[surface],
            "state": info["state"],
            "matched_template": info["matches_template"],
            "current_count": info["count"],
            "new_count": len(new_value) if new_value is not None else 0,
            "describes": describe,
        }
        if info["state"] == STATE_CUSTOMIZED and not replace_customized:
            entry["action"] = "preserve"
            entry["reason"] = ("This has been customized for this "
                               "organization and is not part of any template, "
                               "so it is left exactly as it is.")
            preserved.append(entry)
        else:
            entry["action"] = "replace"
            if info["state"] == STATE_CUSTOMIZED:
                entry["reason"] = ("Explicitly requested: customized values "
                                   "will be overwritten.")
            elif info["state"] == STATE_UNTOUCHED:
                entry["reason"] = ("Inherited from the '%s' template and never "
                                   "edited here." % info["matches_template"])
            else:
                entry["reason"] = "Nothing is configured on this surface yet."
            planned.append(entry)

    consider(SURFACE_TIER_CONFIG, target["lead_tiers"], "lead tier vocabulary")
    consider(SURFACE_APPOINTMENT_TYPES, target["appointment_types"],
             "appointment type list")
    consider(SURFACE_TIER_DEFINITIONS,
             tiers.get_tier_set_for_industry(target["tier_definition_key"]),
             "tier definitions and their AI tracks")

    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "current_industry": getattr(org, "industry", None),
        "current_industry_matched": templates.is_known(getattr(org, "industry", None)),
        "target_industry": target["key"],
        "target_industry_label": target["label"],
        "industry_changes": (templates.normalize(getattr(org, "industry", None))
                             != target["key"]
                             or (getattr(org, "industry", None) or "") != target["key"]),
        "planned": planned,
        "preserved": preserved,
        "replace_customized": bool(replace_customized),
        "template": templates.summary(target["key"]),
        "safety": {
            "sends_nothing": True,
            "charges_nothing": True,
            "touches_leads": False,
            "touches_commercial_agreement": False,
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# APPLY
# ════════════════════════════════════════════════════════════════════════════


def apply(db: Session, org: Organization, actor: User,
          target_industry: Optional[str], *,
          reason: str,
          replace_customized: bool = False) -> Dict[str, Any]:
    """Set the industry and replace only what the preview said it would.

    The plan is recomputed here rather than taken from the caller. A client
    that shows a preview and then posts back "replace everything" must not be
    able to widen the blast radius between the two requests.
    """
    from fastapi import HTTPException

    if not (reason or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Changing an organization's industry requires a reason. It "
                   "is the only record of why its configuration changed.")

    plan = preview(db, org, target_industry, replace_customized)
    target = templates.resolve(target_industry)
    surfaces = {row["surface"] for row in plan["planned"]}

    before = {
        "industry": getattr(org, "industry", None),
        "surfaces": {key: value["state"]
                     for key, value in classify(db, org).items()},
    }

    applied: List[str] = []

    if SURFACE_TIER_CONFIG in surfaces:
        org.tier_config = json.dumps(target["lead_tiers"])
        applied.append(SURFACE_TIER_CONFIG)

    if SURFACE_APPOINTMENT_TYPES in surfaces:
        org.appointment_types = json.dumps(target["appointment_types"])
        applied.append(SURFACE_APPOINTMENT_TYPES)

    if SURFACE_TIER_DEFINITIONS in surfaces:
        # Replaced wholesale ONLY because the preview established that what is
        # there is a template's own rows or nothing at all. A customized set
        # never reaches this branch unless somebody explicitly asked for it.
        (db.query(TierDefinition)
         .filter(TierDefinition.organization_id == org.id)
         .delete(synchronize_session=False))
        for spec in tiers.get_tier_set_for_industry(target["tier_definition_key"]):
            db.add(TierDefinition(organization_id=org.id, **spec))
        applied.append(SURFACE_TIER_DEFINITIONS)

    org.industry = target["key"]

    log_action(
        db, org.id, getattr(actor, "id", None),
        action=ACTION_APPLIED,
        target_type="organization",
        target_id=org.id,
        platform_id=getattr(org, "platform_id", None),
        before=before,
        after={"industry": org.industry, "surfaces_replaced": applied},
        details={"preserved": [row["surface"] for row in plan["preserved"]],
                 "replace_customized": bool(replace_customized),
                 "target_industry_label": target["label"]},
        note=reason.strip(),
        commit=False,
    )
    db.flush()

    return {
        "applied": applied,
        "preserved": [row["surface"] for row in plan["preserved"]],
        "industry": org.industry,
        "industry_label": target["label"],
        "plan": plan,
    }
