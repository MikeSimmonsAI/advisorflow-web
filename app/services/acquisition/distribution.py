"""Master Lead Intelligence -> tenant Acquisition assignment.

This is NOT an importer. The canonical /intake system owns file parsing,
mapping, staging and commit (#470). The Lead Intelligence platform prospect
pool already exists. This module lets God explicitly assign a researched,
qualified platform-owned business to ONE cleaning tenant using the existing
Acquisition engine. No import batches, sends, calls or subscriptions result.

A prospect from a CUSTOMER tenant's CRM is never accepted as a source here.
EvoSys Sales opportunities (prospective cleaning company CUSTOMERS) are a
separate domain and are never considered for distribution.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionOpportunity
from app.models.lead_intel_models import LeadIntelProspect
from app.models.models import Organization, User
from app.services import lead_routing as LR
from app.services.acquisition import common as C, engine as E, geo as G
from app.services.acquisition import harvey_adapted as H
from app.services import qualification as Q


def assess(db: Session, prospect: LeadIntelProspect, org: Organization, segment: str) -> Dict[str, Any]:
    """Preview a manual allocation. No changes, messages or inferred consent."""
    reasons: List[str] = []
    profile = E.get_profile(db, org.id)
    if not C.acquisition_identity(org) or profile is None:
        reasons.append("destination_not_enabled_for_cleaning_acquisition")
    if segment not in C.SEGMENT_KEYS:
        reasons.append("unknown_segment")
    elif profile is not None and segment not in C.jload(profile.segments, []):
        reasons.append("segment_not_served_by_client")

    if prospect.stage != "qualified":
        reasons.append("master_prospect_not_qualified")
    if prospect.bucket not in (Q.READY, Q.REVIEW):
        reasons.append("master_prospect_not_eligible_for_assignment")
    refuse = LR.routable_reason(prospect)
    if refuse and refuse not in ("excluded",):
        reasons.append("master_" + refuse)

    territory, note = G.status(prospect.state)
    if territory != G.ELIGIBLE:
        reasons.append("location_" + territory)
    if profile is not None:
        cities = {x.casefold() for x in C.jload(profile.service_area, []) if isinstance(x, str)}
        zips = {str(x).strip() for x in C.jload(getattr(profile, "service_zips", None), [])}
        city_ok = bool(prospect.city and prospect.city.casefold() in cities)
        zip_ok = bool(prospect.zip_code and prospect.zip_code in zips)
        if not (city_ok or zip_ok):
            reasons.append("outside_client_service_area")
        states = {str(x).upper() for x in C.jload(getattr(profile, "service_states", None), [])}
        if states and (G.normalize_state(prospect.state) not in states):
            reasons.append("outside_client_service_states")
        exclusions = C.jload(profile.exclusions, [])
        domain = H.normalize_domain(prospect.website or "") if prospect.website else ""
        if (domain and H.do_not_contact_domain_match(domain, exclusions)) or (
            prospect.email and H.do_not_contact_match(prospect.email, exclusions)
        ):
            reasons.append("client_never_contact_exclusion")

    if LR.suppression_reason(db, org.id, prospect.phone_e164 or prospect.phone_raw, prospect.email):
        reasons.append("client_suppression")
    key = H.dedupe_key(prospect.name or "", H.normalize_domain(prospect.website or ""),
                       prospect.phone_e164 or prospect.phone_raw or "")
    if not key:
        reasons.append("missing_business_identity")
    elif db.query(AcquisitionOpportunity.id).filter(
        AcquisitionOpportunity.organization_id == org.id, AcquisitionOpportunity.dedupe_key == key
    ).first():
        reasons.append("already_in_client_pipeline")
    if not (prospect.name or "").strip():
        reasons.append("missing_business_name")
    return {
        "prospect_id": prospect.id,
        "destination_org_id": org.id,
        "destination_name": org.name,
        "segment": segment,
        "eligible": not reasons,
        "refused_because": list(dict.fromkeys(reasons)),
        "geography": territory,
        "geography_note": note,
        "delivery": "email_only",
        "action": "assign_to_client_pipeline_only",
        "note": "Assignment does not verify contact details, send anything, or start a pilot.",
    }


def assign_one(db: Session, prospect_id: str, org: Organization, actor: User, segment: str) -> Dict[str, Any]:
    """Exclusive, owner-controlled handoff. Locks platform pool row until the
    canonical tenant engine commits the tenant lead and allocation together.

    Retry after crash sees routed_org_id and refuses rather than creating a
    second lead. There is no transfer of other tenants' private customer data.
    """
    p = db.query(LeadIntelProspect).filter(LeadIntelProspect.id == prospect_id).with_for_update().first()
    if p is None:
        return {"prospect_id": prospect_id, "status": "refused", "reasons": ["not_found"]}
    decision = assess(db, p, org, segment)
    if not decision["eligible"]:
        return {"prospect_id": prospect_id, "status": "refused", "reasons": decision["refused_because"]}
    p.routed_org_id = org.id
    p.routed_at = datetime.utcnow()
    p.routed_by_id = actor.id
    # This source ID ties the tenant's opportunity to the original master row
    # without creating another intake system or misusing routed_batch_id.
    payload = {
        "business_name": p.name,
        "segment": segment,
        "website": p.website or "",
        "phone": p.phone_e164 or p.phone_raw or "",
        "email": p.email or "",
        "street_address": p.address or "",
        "city": p.city or "",
        "state": p.state or "",
        "zip_code": p.zip_code or "",
        "source": "master_pool:" + str(p.id),
        "is_practice": False,
    }
    try:
        opp = E.add_prospect(db, org, actor, payload)
        # E.add_prospect commits the tenant Lead/Opportunity AND the previously
        # set master routing fields in this same database transaction.
    except Exception:
        db.rollback()
        raise
    return {
        "prospect_id": p.id,
        "destination_org_id": org.id,
        "opportunity_id": opp.id,
        "lead_id": opp.lead_id,
        "status": "assigned",
        "outreach": "not_started",
        "pilot_started": False,
    }


def assign_many(db: Session, ids: List[str], org: Organization, actor: User, segment: str) -> Dict[str, Any]:
    """A small explicit operator handoff, NOT an automatic matching worker."""
    results: List[Dict[str, Any]] = []
    for prospect_id in dict.fromkeys(ids):
        results.append(assign_one(db, prospect_id, org, actor, segment))
    return {
        "destination_org_id": org.id,
        "assigned": sum(x["status"] == "assigned" for x in results),
        "refused": sum(x["status"] == "refused" for x in results),
        "results": results,
        "sent": 0,
        "channel": "email_only",
    }
