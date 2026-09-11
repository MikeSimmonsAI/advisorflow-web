"""THE AI EMPLOYEE CATALOGUE - three views of one library.

THERE IS NO SECOND REGISTRY IN THIS FILE. The job library is
`app/services/workforce/registry.py`, in code, where T6 put it and for the
reason recorded there: a registry stored only in a table is a registry a typo
can silently empty. This module ASSEMBLES views of that library for three
audiences and adds nothing to it.

    PLATFORM   what AI employees can exist at all, what each one needs to be
               able to work, and how deeply it has been proven. God Mode's
               view.
    BRAND      which of those a white-label brand offers, under what name, on
               which channels, and under what commercial arrangement. The
               brand's view, and the one place a brand's own wording reaches a
               customer.
    CUSTOMER   what this business could hire, whether they can, and honestly
               why not. The customer's view.

UNAVAILABLE JOBS ARE SHOWN, NOT HIDDEN. T6 made that decision for its own
catalogue and it is right for the same reason here: a customer who cannot see
that an AI Appointment Setter exists cannot ask for one. What they get is the
job, what it does, and a truthful `available` flag with the reason beside it.

NO PRICE IS ASSEMBLED HERE EITHER. The customer view names the catalogue item
and who may sell it; the amount comes from T2's own customer-facing payload,
which already withholds internal notes and Stripe identifiers.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization
from app.services.ai_deployment import capacity, commerce
from app.services.ai_deployment import constants as D

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WHAT A JOB NEEDS IN ORDER TO WORK
# ---------------------------------------------------------------------------
#
# DERIVED FROM THE TEMPLATE, NEVER WRITTEN DOWN TWICE. A job that holds
# `appointment.book` needs a calendar; a job with an SMS channel needs the
# `sms` feature. Listing those by hand per template is how the eleventh job
# ends up claiming to need nothing.

def requirements_for(template_key: str) -> Dict[str, Any]:
    """The business preconditions this job has, read off its own tool list."""
    from app.services.workforce import registry as wf_registry
    spec = wf_registry.template(template_key)
    if spec is None:
        return {"channels": [], "features": [], "needs_calendar": False,
                "needs_handoff": True, "reaches_people": False}

    features: List[str] = []
    needs_calendar = False
    reaches = False
    for key in spec.tool_keys:
        tool = wf_registry.tool(key)
        if tool is None:
            continue
        if tool.required_feature and tool.required_feature not in features:
            features.append(tool.required_feature)
        if tool.reaches_outside:
            reaches = True
        if key.startswith("appointment.") or key == "calendar.get_availability":
            needs_calendar = True
    # A channel the job declares needs the matching customer feature, whether
    # or not a tool happened to name it.
    for channel in spec.channels:
        if channel not in features:
            features.append(channel)
    return {
        "channels": list(spec.channels),
        "features": sorted(features),
        "needs_calendar": needs_calendar,
        # EVERY job needs somewhere to hand work to a person. A job that can
        # never escalate is a job that gets stuck holding a conversation it
        # should not be in.
        "needs_handoff": True,
        "reaches_people": reaches,
    }


# ---------------------------------------------------------------------------
# PLATFORM VIEW
# ---------------------------------------------------------------------------

def platform_catalog(db: Optional[Session] = None) -> List[Dict[str, Any]]:
    """Every job the platform can run, with what it needs and how proven it is.

    `depth` is carried through from T6 unchanged. It is honesty rather than
    configuration - "implemented" means the job has been driven end to end
    through a simulator and a harness, "architected" means the engine supports
    it and nobody has proven it to that standard yet - and God Mode shows it so
    nobody demonstrates a job that has never been exercised.
    """
    from app.services.workforce import registry as wf_registry
    out = []
    for key in wf_registry.ALL_TEMPLATE_KEYS:
        spec = wf_registry.template(key)
        out.append({
            "template_key": key,
            "name": spec.name,
            "job_role": spec.job_role,
            "summary": spec.summary,
            "description": spec.description,
            "channels": list(spec.channels),
            "depth": spec.depth,
            "entitlement_key": spec.entitlement_key,
            "required_feature": spec.required_feature,
            "requirements": requirements_for(key),
            "advanced_capability": capacity.is_advanced_capability(key),
            "config_questions": list(spec.questions),
        })
    return out


# ---------------------------------------------------------------------------
# BRAND VIEW
# ---------------------------------------------------------------------------

def brand_catalog(db: Session, platform_id: str) -> List[Dict[str, Any]]:
    """What this brand offers, and whether each offering is coherent.

    `blockers` is the point of this shape, the same way it is on T2's own
    `admin_out`: an offering that looks configured and cannot actually be
    acquired is the failure an operator cannot see from the columns alone, so
    it is computed and named rather than left to be discovered by a customer.
    """
    from app.models.workforce_models import AIBrandOffering, AIEmployeeTemplate
    from app.services.workforce import registry as wf_registry

    offerings = {}
    rows = (db.query(AIBrandOffering, AIEmployeeTemplate)
            .join(AIEmployeeTemplate,
                  AIBrandOffering.template_id == AIEmployeeTemplate.id)
            .filter(AIBrandOffering.platform_id == platform_id).all())
    for offering, tpl in rows:
        offerings[tpl.key] = offering

    out = []
    for key in wf_registry.ALL_TEMPLATE_KEYS:
        spec = wf_registry.template(key)
        offering = offerings.get(key)
        terms = commerce.terms_for(db, platform_id, key)
        item = None
        blockers: List[str] = []

        if offering is None:
            blockers.append("This brand has not added this job to its "
                            "offering yet.")
        elif not offering.is_enabled:
            blockers.append("The offering exists and is switched off.")

        if terms is None:
            blockers.append("No commercial terms are configured, so nobody "
                            "can acquire it.")
        else:
            if not terms.is_available:
                blockers.append("Commercial terms exist and are marked "
                                "unavailable.")
            mode = terms.commercial_mode or D.MODE_ADDON
            included = commerce.json_list(terms.included_plan_keys, []) or []
            if mode == D.MODE_INCLUDED and not included:
                blockers.append("Marked as included with a package and no "
                                "package is named.")
            if mode in (D.MODE_ADDON, D.MODE_QUOTED, D.MODE_CAPACITY):
                if not terms.catalog_item_key:
                    blockers.append("No catalogue item key is set, so there "
                                    "is nothing to sell.")
                else:
                    item = commerce.catalog_item(db, platform_id,
                                                 terms.catalog_item_key)
                    if item is None:
                        blockers.append(
                            "Catalogue item %r does not exist in this brand's "
                            "catalogue." % terms.catalog_item_key)
                    else:
                        from app.services import brand_catalog as t2_catalog
                        if not t2_catalog.is_sellable(item):
                            blockers.append(
                                "Catalogue item %r is not currently sellable."
                                % item.key)
            if capacity.is_advanced_capability(key) and not (
                    commerce.json_list(terms.eligible_plan_keys, []) or []):
                blockers.append(
                    "This is a management capability and no eligible package "
                    "has been named, so no customer may hold it.")

        out.append({
            "template_key": key,
            "platform_name": spec.name,
            "job_role": spec.job_role,
            "summary": spec.summary,
            "depth": spec.depth,
            "platform_channels": list(spec.channels),
            "advanced_capability": capacity.is_advanced_capability(key),
            "requirements": requirements_for(key),
            "offered": offering is not None,
            "enabled": bool(offering is not None and offering.is_enabled),
            "display_name": (getattr(offering, "display_name", None)
                             or spec.name),
            "description": (getattr(offering, "description", None)
                            or spec.summary),
            "offering_channels": (
                commerce.json_list(getattr(offering, "allowed_channels", None))
                or list(spec.channels)),
            "terms": terms_out(terms),
            "catalog_item": ({"key": item.key, "name": item.name,
                              "self_service": bool(item.self_service),
                              "seller_assisted": bool(item.seller_assisted),
                              "pricing_mode": item.pricing_mode}
                             if item is not None else None),
            "acquirable": not blockers,
            "blockers": blockers,
        })
    return out


def terms_out(terms) -> Optional[Dict[str, Any]]:
    if terms is None:
        return None
    return {
        "commercial_mode": terms.commercial_mode,
        "commercial_mode_label": D.MODE_LABELS.get(terms.commercial_mode,
                                                   terms.commercial_mode),
        "catalog_item_key": terms.catalog_item_key,
        "included_plan_keys": commerce.json_list(terms.included_plan_keys, [])
        or [],
        "eligible_plan_keys": commerce.json_list(terms.eligible_plan_keys, [])
        or [],
        "max_per_customer": terms.max_per_customer,
        "requires_controlled_first": bool(terms.requires_controlled_first),
        "allowed_channels": commerce.json_list(terms.allowed_channels, []) or [],
        "is_available": bool(terms.is_available),
        "notes": terms.notes,
        "updated_at": (terms.updated_at.isoformat() if terms.updated_at
                       else None),
    }


# ---------------------------------------------------------------------------
# CUSTOMER VIEW
# ---------------------------------------------------------------------------

def customer_catalog(db: Session, organization_id: str) -> List[Dict[str, Any]]:
    """What this business could hire, and honestly whether they can.

    THE JOB'S OWN NAME NEVER LEAKS PAST THE BRAND'S. `display_name` is what a
    customer reads; the platform template name appears nowhere in this payload,
    because the customer's relationship is with the brand.
    """
    from app.models.ai_deployment_models import AIEmployeeDeployment
    from app.services.workforce import registry as wf_registry

    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        return []

    held: Dict[str, List[Any]] = {}
    for row in (db.query(AIEmployeeDeployment)
                .filter(AIEmployeeDeployment.organization_id == organization_id)
                .all()):
        held.setdefault(row.template_key, []).append(row)

    out = []
    for key in wf_registry.ALL_TEMPLATE_KEYS:
        spec = wf_registry.template(key)
        offer = commerce.resolve_offer(db, org, key)
        cap = capacity.may_hire(db, org, key, terms=offer.terms)
        mine = [r for r in held.get(key, []) if r.state != D.RETIRED]

        blockers = list(offer.blockers)
        if not cap.allowed and cap.reason:
            blockers.append(cap.reason)

        # AVAILABLE means "you could start hiring this one right now", which is
        # a narrower claim than "you are entitled to it": a customer who is
        # entitled and already at their limit is entitled and cannot hire.
        can_hire = bool(offer.state in (D.COMM_INCLUDED, D.COMM_ENTITLED)
                        and cap.allowed)

        out.append({
            "template_key": key,
            "name": (getattr(offer.offering, "display_name", None)
                     or spec.name),
            "description": (getattr(offer.offering, "description", None)
                            or spec.summary),
            "job_role": spec.job_role,
            "channels": (commerce.json_list(
                getattr(offer.offering, "allowed_channels", None))
                or list(spec.channels)),
            "requirements": requirements_for(key),
            "state": D.AVAILABLE if not mine else mine[0].state,
            "can_hire": can_hire,
            "held": len(mine),
            "deployments": [{"id": r.id, "state": r.state,
                             "state_label": D.STATE_LABELS.get(r.state,
                                                               r.state),
                             "name": r.display_name}
                            for r in mine],
            "commerce": offer.as_dict(),
            "capacity": cap.as_dict(),
            "blockers": blockers,
        })
    return out


def customer_catalog_entry(db: Session, organization_id: str,
                           template_key: str) -> Optional[Dict[str, Any]]:
    """One row of the customer catalogue. Same answer, one job."""
    for row in customer_catalog(db, organization_id):
        if row["template_key"] == template_key:
            return row
    return None
