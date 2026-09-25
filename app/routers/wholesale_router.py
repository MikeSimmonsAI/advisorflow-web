"""Wholesale Real Estate — acquisition side.

Properties, sellers, enrichment, the deal room, comps, analysis, offers,
approvals, documents, the pipeline and the Command Center.

EVERY ROUTE IN THIS FILE IS GATED THREE TIMES, AND EACH GATE ANSWERS A DIFFERENT
QUESTION:

    require_tenant_user                  is this caller inside a customer?
    require_feature("wholesale_real_estate")  has this customer bought the module?
    require_not_observation (writes)     is this a real actor, not an observer?

That is the pattern the rest of this platform already uses, and the comment in
`entitlements.py` says why the second one has to exist on the server: hiding a
nav item is not access control.

Disposition — buyers, buy boxes, matching, outreach — lives in
`wholesale_buyers_router.py`. Two files because this one would otherwise be
unreadable, one prefix because it is one module.
"""

import csv
import io
import json
import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Depends, File, HTTPException, Query, Request,
                     UploadFile)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import (get_db, require_not_observation, require_tenant_or_observer,
                      require_tenant_user)
from app.models.models import Lead, User
from app.models.wholesale_models import (
    ACTOR_AUTOMATION, ACTOR_USER, LOST_REASONS, OFFER_FROM_SELLER,
    OFFER_FROM_US, OFFER_STATUSES, TITLE_STATUSES, VALUE_MANUAL, VALUE_SOURCES,
    WholesaleApproval, WholesaleComp, WholesaleDeal, WholesaleDocument,
    WholesaleEnrichmentRequest, WholesaleEvent, WholesaleFile, WholesaleOffer,
    WholesaleProperty, WholesaleSellerProfile, WholesaleSettings,
)
from app.services import wholesale_analysis as analysis
from app.services import wholesale_enrichment as enrichment
from app.services import wholesale_esign as esign
from app.services import wholesale_ai
from app.services import wholesale_pipeline as pipeline
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

log = logging.getLogger(__name__)

FEATURE = "wholesale_real_estate"

router = APIRouter(prefix="/wholesale", tags=["wholesale"],
                   dependencies=[Depends(require_feature(FEATURE))])


# ── Serialization ───────────────────────────────────────────────────────────
#
# One function per shape, in one place. A route that builds its own dict is a
# route whose payload drifts from every other route's.

def _num(value: Any) -> Optional[float]:
    d = analysis.money(value)
    return None if d is None else float(d)


def _jsonl(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def property_json(p: WholesaleProperty) -> Dict[str, Any]:
    return {
        "id": p.id, "address": svc.address_line(p),
        "street_address": p.street_address, "unit": p.unit, "city": p.city,
        "state": p.state, "zip_code": p.zip_code, "county": p.county,
        "market": p.market, "parcel_apn": p.parcel_apn,
        "property_type": p.property_type,
        "bedrooms": _num(p.bedrooms), "bathrooms": _num(p.bathrooms),
        "square_feet": p.square_feet, "lot_size_sqft": p.lot_size_sqft,
        "year_built": p.year_built,
        "estimated_value": _num(p.estimated_value),
        "estimated_value_source": p.estimated_value_source,
        "mortgage_balance": _num(p.mortgage_balance),
        "mortgage_source": p.mortgage_source, "liens_note": p.liens_note,
        "ownership_type": p.ownership_type, "owner_name": p.owner_name,
        "owner_mailing_street": p.owner_mailing_street,
        "owner_mailing_city": p.owner_mailing_city,
        "owner_mailing_state": p.owner_mailing_state,
        "owner_mailing_zip": p.owner_mailing_zip,
        "occupancy_status": p.occupancy_status,
        "acquisition_source": p.acquisition_source, "source_detail": p.source_detail,
        "tags": _jsonl(p.tags), "notes": p.notes,
        "assigned_to_id": p.assigned_to_id,
        "is_test": bool(p.is_test), "test_note": p.test_note,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def seller_json(profile: Optional[WholesaleSellerProfile],
                lead: Optional[Lead]) -> Optional[Dict[str, Any]]:
    if profile is None:
        return None
    return {
        "id": profile.id, "lead_id": profile.lead_id,
        "first_name": getattr(lead, "first_name", None),
        "last_name": getattr(lead, "last_name", None),
        "phone": getattr(lead, "phone", None),
        "email": getattr(lead, "email", None),
        # THE COMPLIANCE FACTS, SURFACED. A screen that lets somebody start
        # outreach has to be able to show that this person said stop.
        "lead_status": getattr(lead, "status", None),
        "is_dnc": getattr(lead, "status", None) == "dnc",
        "allow_sms": getattr(lead, "allow_sms", None),
        "allow_email": getattr(lead, "allow_email", None),
        "allow_voice": getattr(lead, "allow_voice", None),
        "sms_consent": bool(getattr(lead, "sms_consent", False)),
        "owner_status": profile.owner_status,
        "preferred_contact_method": profile.preferred_contact_method,
        "is_available": profile.is_available,
        "considering_selling": profile.considering_selling,
        "asking_price": _num(profile.asking_price),
        "asking_price_source": profile.asking_price_source,
        "timeline": profile.timeline, "motivation": profile.motivation,
        "reason_for_selling": profile.reason_for_selling,
        "property_condition": profile.property_condition,
        "major_repairs": profile.major_repairs, "occupancy": profile.occupancy,
        "mortgage_note": profile.mortgage_note,
        "decision_makers": profile.decision_makers,
        "best_callback_time": profile.best_callback_time,
        "appointment_status": profile.appointment_status,
        "qualification_band": profile.qualification_band,
        "qualification_score": profile.qualification_score,
        "qualification_reasons": _jsonl(profile.qualification_reasons),
        "completeness": profile.completeness,
        "ai_summary": profile.ai_summary, "ai_intent": profile.ai_intent,
        "ai_last_run_at": (profile.ai_last_run_at.isoformat()
                           if profile.ai_last_run_at else None),
        "needs_human": bool(profile.needs_human),
        "needs_human_reason": profile.needs_human_reason,
    }


def payment_state(d: WholesaleDeal) -> str:
    """not_closed | payment_pending | fee_collected.

    A closing date that has gone by is NOT payment. Only a typed collected
    figure moves a deal to `fee_collected`, which is also the only state the
    Command Center counts as revenue.
    """
    if not d.closed_at:
        return "not_closed"
    return "fee_collected" if d.wholesale_fee_collected is not None \
        else "payment_pending"


# What has to exist before a closing is financially complete. This does not
# BLOCK anything — legitimate deals close with a piece missing and somebody
# chasing it — it just refuses to let the gap be invisible.
CLOSING_REQUIREMENTS = (
    ("title_company", "Title company"),
    ("title_file_number", "Escrow / file number"),
    ("closing_date", "Closing date"),
    ("contract_price", "Contract price"),
    ("buyer_price", "Buyer price"),
    ("assignment_fee", "Assignment fee"),
)


def closing_checklist(d: WholesaleDeal) -> Dict[str, Any]:
    missing = [label for field, label in CLOSING_REQUIREMENTS
               if getattr(d, field, None) in (None, "")]
    return {
        "missing": missing,
        "complete": not missing,
        # Said separately because a deal can be complete on paper and still
        # have no money in the account.
        "fee_collected": d.wholesale_fee_collected is not None,
    }


def _parse_datetime_date(raw):
    """A YYYY-MM-DD from a date input, as a datetime at midnight.

    Returns None for anything unparseable rather than raising: a bad date on a
    payment record should not lose the payment.
    """
    if not raw:
        return None
    try:
        return datetime.combine(date.fromisoformat(str(raw)[:10]),
                                datetime.min.time())
    except (ValueError, TypeError):
        return None


def deal_json(d: WholesaleDeal, settings: WholesaleSettings) -> Dict[str, Any]:
    return {
        "id": d.id, "property_id": d.property_id,
        "seller_lead_id": d.seller_lead_id, "assigned_to_id": d.assigned_to_id,
        "stage": d.stage, "stage_label": pipeline.stage_label(settings, d.stage),
        "stage_changed_at": (d.stage_changed_at.isoformat()
                             if d.stage_changed_at else None),
        "previous_stage": d.previous_stage, "lost_reason": d.lost_reason,
        "arv": _num(d.arv), "arv_source": d.arv_source, "arv_method": d.arv_method,
        "repair_estimate": _num(d.repair_estimate),
        "repair_estimate_source": d.repair_estimate_source,
        "repair_notes": d.repair_notes,
        "investor_percentage_used": _num(d.investor_percentage_used),
        "transaction_costs": _num(d.transaction_costs),
        "desired_wholesale_fee": _num(d.desired_wholesale_fee),
        "max_allowable_offer": _num(d.max_allowable_offer),
        "proposed_offer": _num(d.proposed_offer),
        "analysis_notes": d.analysis_notes,
        "contract_price": _num(d.contract_price),
        "contract_status": d.contract_status,
        "contract_signed_at": (d.contract_signed_at.isoformat()
                               if d.contract_signed_at else None),
        "inspection_deadline": (d.inspection_deadline.isoformat()
                                if d.inspection_deadline else None),
        "close_of_escrow_target": (d.close_of_escrow_target.isoformat()
                                   if d.close_of_escrow_target else None),
        "assigned_buyer_id": d.assigned_buyer_id,
        "buyer_price": _num(d.buyer_price),
        "assignment_fee": _num(d.assignment_fee),
        "assignment_status": d.assignment_status,
        "title_company": d.title_company, "title_contact": d.title_contact,
        "title_status": d.title_status,
        "closing_date": d.closing_date.isoformat() if d.closing_date else None,
        "closed_at": d.closed_at.isoformat() if d.closed_at else None,
        "wholesale_fee_collected": _num(d.wholesale_fee_collected),
        "deal_result": d.deal_result,
        "lost_reason": d.lost_reason,
        "lost_reason_detail": d.lost_reason_detail,
        # Phase 3. Contract dates, title detail, closing detail and the
        # economics lock. The deal summary header reads every one of these,
        # so a payload that omitted them would send the header back for a
        # second request to render its own first screen.
        "contract_date": d.contract_date.isoformat() if d.contract_date else None,
        "seller_signed_at": d.seller_signed_at.isoformat() if d.seller_signed_at else None,
        "buyer_signed_at": d.buyer_signed_at.isoformat() if d.buyer_signed_at else None,
        "effective_date": d.effective_date.isoformat() if d.effective_date else None,
        "earnest_money": _num(d.earnest_money),
        "earnest_money_due": d.earnest_money_due.isoformat() if d.earnest_money_due else None,
        "earnest_money_received_at": (d.earnest_money_received_at.isoformat()
                                      if d.earnest_money_received_at else None),
        "option_fee": _num(d.option_fee),
        "closing_deadline": d.closing_deadline.isoformat() if d.closing_deadline else None,
        "title_escrow_officer": d.title_escrow_officer,
        "title_phone": d.title_phone,
        "title_email": d.title_email,
        "title_commitment_received_at": (d.title_commitment_received_at.isoformat()
                                         if d.title_commitment_received_at else None),
        "title_issues": d.title_issues,
        "closing_time": d.closing_time,
        "closing_location": d.closing_location,
        "closing_status": d.closing_status,
        "other_costs": _num(d.other_costs),
        "buyer_selected_at": d.buyer_selected_at.isoformat() if d.buyer_selected_at else None,
        "buyer_selected_by_id": d.buyer_selected_by_id,
        "economics_locked": bool(d.economics_locked),
        # ── Phase 5. Closed and paid are two different facts. ───────────────
        "title_file_number": d.title_file_number,
        "funding_status": d.funding_status,
        "funded_at": d.funded_at.isoformat() if d.funded_at else None,
        "fee_collected_at": (d.fee_collected_at.isoformat()
                             if d.fee_collected_at else None),
        "fee_payment_method": d.fee_payment_method,
        "fee_payment_reference": d.fee_payment_reference,
        "fee_recorded_by_id": d.fee_recorded_by_id,
        "fee_variance_note": d.fee_variance_note,
        "payment_state": payment_state(d),
        "closing_checklist": closing_checklist(d),
        "is_test": bool(d.is_test),
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def _enum_text(value: Any) -> str:
    """An Enum's VALUE, never its repr.

    `str(SomeEnum.HOT)` is "SomeEnum.HOT", and that string went to the browser
    and was drawn on the seller conversation as REPLYCLASSIFICATION.HOT — the
    same class of defect as printing `single_family` at a person, one layer
    further down. Anything that is not an Enum passes through unchanged.
    """
    if value is None:
        return ""
    inner = getattr(value, "value", None)
    if isinstance(inner, str):
        return inner
    text = str(value)
    # Defensive: an Enum whose value is not a string still must not arrive as
    # "ClassName.MEMBER".
    if "." in text and type(value).__name__ and text.startswith(
            type(value).__name__ + "."):
        return text.split(".", 1)[1]
    return text


def comp_json(c: WholesaleComp, photo: Any = None) -> Dict[str, Any]:
    # $/sqft is derived here rather than in the browser so the table, the
    # comparison panel and the ARV working can never disagree about it.
    psf = None
    if c.sale_price is not None and c.square_feet:
        try:
            if int(c.square_feet) > 0:
                psf = round(float(c.sale_price) / float(int(c.square_feet)), 2)
        except (TypeError, ValueError, ZeroDivisionError):
            psf = None
    return {
        "id": c.id, "street_address": c.street_address, "city": c.city,
        "state": c.state, "zip_code": c.zip_code,
        "sale_price": _num(c.sale_price),
        "sale_date": c.sale_date.isoformat() if c.sale_date else None,
        "square_feet": c.square_feet, "bedrooms": _num(c.bedrooms),
        "bathrooms": _num(c.bathrooms), "distance_miles": _num(c.distance_miles),
        "year_built": c.year_built,
        "price_per_sqft": psf,
        "property_type": c.property_type, "source": c.source,
        "notes": c.notes, "included": bool(c.included),
        # The photo is a reference, never a public URL: /wholesale/files/{id}
        # re-checks the organization on every read.
        "photo": _photo_json(photo) if photo is not None else None,
    }


def approval_json(a: WholesaleApproval) -> Dict[str, Any]:
    return {
        "id": a.id, "kind": a.kind, "status": a.status, "amount": _num(a.amount),
        "recommendation": a.recommendation, "reasoning": a.reasoning,
        "inputs": _jsonl(a.inputs), "requested_by_id": a.requested_by_id,
        "requested_by_actor": a.requested_by_actor, "approver_id": a.approver_id,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "comments": a.comments,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def document_json(d: WholesaleDocument, stored: Any = None) -> Dict[str, Any]:
    """One document row, and whether a FILE actually exists behind it.

    Phase 4 fixed a real confusion here. `file_name` has meant, since Phase 1,
    "the name of a file the operator keeps somewhere else" — a reference. Phase 3
    added genuine uploads, which set `file_id`, but this payload never carried
    it, so a document with real stored bytes was drawn by the screen exactly
    like one that was only ever a filename. The operator could not tell whether
    the contract was in the system or in somebody's Downloads folder.

    `stored` is the WholesaleFile row when one exists. When it is None there is
    no file — and that is now a fact the screen can read rather than infer.
    """
    return {
        "id": d.id, "doc_type": d.doc_type, "title": d.title, "status": d.status,
        # Phase 5 lifecycle. `status` is what is STORED, including the
        # pre-lifecycle vocabulary rows still carry; `status_key` is that
        # value read through `wholesale_esign.normalise`, and the screen
        # draws from the key so no client re-implements the mapping.
        "status_key": esign.normalise(d.status),
        "status_label": esign.STATUS_LABEL[esign.normalise(d.status)],
        "allowed_next": esign.allowed_next(d.status),
        "signature_status": d.signature_status,
        "signature_provider": d.signature_provider, "external_ref": d.external_ref,
        "parties": _jsonl(d.parties), "file_name": d.file_name,
        "file_id": getattr(d, "file_id", None),
        # Present only when bytes are genuinely held. Never a public URL: it is
        # the authenticated route, which re-checks the organization on read.
        "stored_file": ({
            "id": stored.id,
            "url": "/wholesale/files/%s" % stored.id,
            "original_filename": stored.original_filename,
            "content_type": stored.content_type,
            "byte_size": stored.byte_size,
            "uploaded_at": (stored.created_at.isoformat()
                            if stored.created_at else None),
        } if stored is not None else None),
        # Phase 5. Who this document has been published to. Absent from the
        # buyer and seller payloads entirely — this is the operator's view of
        # the boundary, not a hint to an outside reader.
        "buyer_visible": bool(getattr(d, "buyer_visible", False)),
        "seller_visible": bool(getattr(d, "seller_visible", False)),
        "viewed_at": (d.viewed_at.isoformat()
                      if getattr(d, "viewed_at", None) else None),
        "file_url": d.file_url, "uploaded_by_id": d.uploaded_by_id,
        "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
        "executed_at": d.executed_at.isoformat() if d.executed_at else None,
        "notes": d.notes,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def event_json(e: WholesaleEvent) -> Dict[str, Any]:
    return {
        "id": e.id, "action": e.action, "actor_type": e.actor_type,
        "actor_user_id": e.actor_user_id, "actor_label": e.actor_label,
        "summary": e.summary, "before": _jsonl(e.before_state),
        "after": _jsonl(e.after_state), "details": _jsonl(e.details),
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


def enrichment_json(e: WholesaleEnrichmentRequest) -> Dict[str, Any]:
    return {
        "id": e.id, "property_id": e.property_id, "lead_id": e.lead_id,
        "provider": e.provider, "status": e.status, "error": e.error,
        "inputs": _jsonl(e.inputs), "result": _jsonl(e.result),
        "confidence": e.confidence, "billable": bool(e.billable),
        "requested_by_actor": e.requested_by_actor,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "completed_at": e.completed_at.isoformat() if e.completed_at else None,
    }


# ── Settings ────────────────────────────────────────────────────────────────

class SettingsPatch(BaseModel):
    investor_percentage: Optional[float] = None
    default_wholesale_fee: Optional[float] = None
    transaction_cost_percent: Optional[float] = None
    transaction_cost_flat: Optional[float] = None
    min_buyer_margin: Optional[float] = None
    high_threshold: Optional[int] = None
    medium_threshold: Optional[int] = None
    review_below_completeness: Optional[int] = None
    markets: Optional[List[str]] = None
    target_states: Optional[List[str]] = None
    target_counties: Optional[List[str]] = None
    target_cities: Optional[List[str]] = None
    target_zips: Optional[List[str]] = None
    pipeline_stages: Optional[List[Dict[str, Any]]] = None
    enrichment_provider: Optional[str] = None
    enrichment_auto: Optional[bool] = None
    property_data_provider: Optional[str] = None
    comps_provider: Optional[str] = None
    esign_provider: Optional[str] = None
    enrichment_daily_cap: Optional[int] = None
    enrichment_monthly_cap: Optional[int] = None
    enrichment_max_records_per_run: Optional[int] = None
    enrichment_requires_approval: Optional[bool] = None
    auto_enrich_on_import: Optional[bool] = None
    auto_stage_on_enrichment: Optional[bool] = None
    auto_qualify_on_reply: Optional[bool] = None
    auto_analysis_on_qualified: Optional[bool] = None
    auto_match_on_contract: Optional[bool] = None
    require_offer_approval: Optional[bool] = None
    require_contract_approval: Optional[bool] = None
    require_assignment_approval: Optional[bool] = None
    ai_qualification_enabled: Optional[bool] = None
    ai_persona_name: Optional[str] = None
    ai_direction: Optional[str] = None
    ai_tone: Optional[str] = None


_JSON_SETTINGS = ("markets", "target_states", "target_counties", "target_cities",
                  "target_zips", "pipeline_stages")


def settings_json(s: WholesaleSettings) -> Dict[str, Any]:
    out = {
        "investor_percentage": _num(s.investor_percentage),
        "default_wholesale_fee": _num(s.default_wholesale_fee),
        "transaction_cost_percent": _num(s.transaction_cost_percent),
        "transaction_cost_flat": _num(s.transaction_cost_flat),
        "min_buyer_margin": _num(s.min_buyer_margin),
        "high_threshold": s.high_threshold,
        "medium_threshold": s.medium_threshold,
        "review_below_completeness": s.review_below_completeness,
        "enrichment_provider": s.enrichment_provider,
        "enrichment_auto": bool(s.enrichment_auto),
        "property_data_provider": s.property_data_provider,
        "comps_provider": s.comps_provider,
        "esign_provider": s.esign_provider,
        "enrichment_daily_cap": s.enrichment_daily_cap,
        "enrichment_monthly_cap": s.enrichment_monthly_cap,
        "enrichment_max_records_per_run": s.enrichment_max_records_per_run,
        "enrichment_requires_approval": bool(s.enrichment_requires_approval),
        "auto_enrich_on_import": bool(s.auto_enrich_on_import),
        "auto_stage_on_enrichment": bool(s.auto_stage_on_enrichment),
        "auto_qualify_on_reply": bool(s.auto_qualify_on_reply),
        "auto_analysis_on_qualified": bool(s.auto_analysis_on_qualified),
        "auto_match_on_contract": bool(s.auto_match_on_contract),
        "require_offer_approval": bool(s.require_offer_approval),
        "require_contract_approval": bool(s.require_contract_approval),
        "require_assignment_approval": bool(s.require_assignment_approval),
        "ai_qualification_enabled": bool(s.ai_qualification_enabled),
        "ai_persona_name": s.ai_persona_name,
        "ai_direction": s.ai_direction,
        # Phase 6. Steers how the assistant PHRASES ITS OWN SUMMARY back to the
        # operator, and nothing else — see the model comment and
        # `wholesale_ai.TONES`.
        "ai_tone": getattr(s, "ai_tone", None),
        "ai_tones": list(wholesale_ai.TONES),
    }
    for field in _JSON_SETTINGS:
        out[field] = _jsonl(getattr(s, field))
    out["pipeline_stages_effective"] = pipeline.resolve_stages(s)
    # THE PROVIDER STATUS INDICATOR. "If credentials are unavailable, create a
    # configuration screen, a status indicator, a manual/import fallback and a
    # clear 'provider not connected' state." This is that status, computed from
    # the environment rather than from a stored flag that could be stale.
    out["providers"] = {
        "enrichment": enrichment.provider_report(),
        "comps": [{"key": "manual", "label": "Manual comp entry", "configured": True,
                   "billable": False, "missing_env": [], "status": "ready"}],
        # Phase 5. Was an inline literal claiming one hard-coded provider.
        # It now comes from the registry in `wholesale_esign.py`, so a vendor
        # added there appears here without this line being touched — and, more
        # to the point, so this list cannot say a provider exists that the
        # signature path would not actually use.
        "esign": esign.provider_report(),
    }
    out["signature_capability"] = esign.capability()
    return out


@router.get("/settings")
def get_settings(db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return settings_json(svc.resolve_settings(db, org_id))


@router.patch("/settings")
def patch_settings(payload: SettingsPatch, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    s = svc.resolve_settings(db, org_id, commit=False)
    data = payload.model_dump(exclude_unset=True)
    before = settings_json(s)

    if "pipeline_stages" in data and data["pipeline_stages"] is not None:
        _validate_stage_payload(data["pipeline_stages"])
    if "enrichment_provider" in data and data["enrichment_provider"] is not None:
        if data["enrichment_provider"] not in enrichment.PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail="Unknown enrichment provider %r. Available: %s"
                       % (data["enrichment_provider"],
                          ", ".join(sorted(enrichment.PROVIDERS))))
    if data.get("high_threshold") is not None and data.get("medium_threshold") is not None \
            and data["high_threshold"] <= data["medium_threshold"]:
        raise HTTPException(status_code=400,
                            detail="The HIGH threshold must be above the MEDIUM "
                                   "threshold, or no seller can ever be HIGH.")

    for key, value in data.items():
        if key in _JSON_SETTINGS:
            setattr(s, key, json.dumps(value) if value is not None else None)
        else:
            setattr(s, key, value)
    db.flush()
    svc.log_event(db, org_id, "settings.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, summary="Wholesale settings changed",
                  before=before, after=settings_json(s))
    db.commit()
    db.refresh(s)
    return settings_json(s)


def _validate_stage_payload(stages: List[Dict[str, Any]]) -> None:
    """A stage list has to be usable. Refuse the two shapes that are not."""
    if not stages:
        raise HTTPException(status_code=400,
                            detail="A pipeline needs at least one stage.")
    keys = [(s.get("key") or "").strip() for s in stages]
    if any(not k for k in keys):
        raise HTTPException(status_code=400, detail="Every stage needs a key.")
    if len(set(keys)) != len(keys):
        raise HTTPException(status_code=400, detail="Stage keys must be unique.")


# ── Command Center ──────────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db),
              user: User = Depends(require_tenant_or_observer),
              stage: Optional[str] = None,
              state: Optional[str] = None,
              county: Optional[str] = None,
              city: Optional[str] = None,
              zip_code: Optional[str] = None,
              market: Optional[str] = None,
              assigned_to_id: Optional[str] = None,
              source: Optional[str] = None,
              since: Optional[str] = None,
              until: Optional[str] = None,
              include_test: bool = False):
    """Every figure comes from this tenant's rows. Nothing here is a constant."""
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    filters = {"stage": stage, "state": state, "county": county, "city": city,
               "zip_code": zip_code, "market": market,
               "assigned_to_id": assigned_to_id, "source": source,
               "since": _parse_dt(since), "until": _parse_dt(until)}
    return svc.dashboard(db, org_id, filters, include_test=include_test)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400,
                            detail="Dates must be ISO-8601, e.g. 2026-09-01.")


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise HTTPException(status_code=400,
                            detail="Dates must be ISO-8601, e.g. 2026-09-01.")


# ── Properties ──────────────────────────────────────────────────────────────

class PropertyIn(BaseModel):
    street_address: Optional[str] = None
    unit: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    county: Optional[str] = None
    market: Optional[str] = None
    parcel_apn: Optional[str] = None
    property_type: Optional[str] = None
    bedrooms: Optional[float] = None
    bathrooms: Optional[float] = None
    square_feet: Optional[int] = None
    lot_size_sqft: Optional[int] = None
    year_built: Optional[int] = None
    estimated_value: Optional[float] = None
    estimated_value_source: Optional[str] = None
    mortgage_balance: Optional[float] = None
    mortgage_source: Optional[str] = None
    liens_note: Optional[str] = None
    ownership_type: Optional[str] = None
    owner_name: Optional[str] = None
    owner_mailing_street: Optional[str] = None
    owner_mailing_city: Optional[str] = None
    owner_mailing_state: Optional[str] = None
    owner_mailing_zip: Optional[str] = None
    occupancy_status: Optional[str] = None
    acquisition_source: Optional[str] = None
    source_detail: Optional[str] = None
    tags: Optional[List[str]] = None
    notes: Optional[str] = None
    assigned_to_id: Optional[str] = None
    is_test: bool = False
    test_note: Optional[str] = None


@router.get("/properties")
def list_properties(db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer),
                    q: Optional[str] = None,
                    state: Optional[str] = None,
                    city: Optional[str] = None,
                    zip_code: Optional[str] = None,
                    county: Optional[str] = None,
                    stage: Optional[str] = None,
                    band: Optional[str] = None,
                    include_test: bool = False,
                    with_next_action: bool = False,
                    limit: int = Query(100, le=500), offset: int = 0):
    """The property list, with enough on each row to work a pipeline from.

    `with_next_action` is OPT-IN and clamps the page size: working out what a
    deal is waiting on costs a query or two per deal, and a list endpoint that
    did it for five hundred rows without being asked would be a slow screen
    nobody could explain. Cover photos and last activity are batched and always
    included, because those are one query each for the whole page.
    """
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    settings = svc.resolve_settings(db, org_id)
    query = db.query(WholesaleProperty).filter(
        WholesaleProperty.organization_id == org_id)
    if not include_test:
        query = query.filter(WholesaleProperty.is_test.isnot(True))
    for field, value in (("state", state), ("city", city),
                         ("zip_code", zip_code), ("county", county)):
        if value:
            query = query.filter(getattr(WholesaleProperty, field) == value)
    if q:
        like = "%%%s%%" % q.strip()
        query = query.filter(
            (WholesaleProperty.street_address.ilike(like)) |
            (WholesaleProperty.owner_name.ilike(like)) |
            (WholesaleProperty.parcel_apn.ilike(like)))
    # Stage and band filter on the DEAL, so they are applied as a subquery on
    # the property ids rather than by fetching everything and dropping rows —
    # which would make the total, and the paging, lie.
    if stage:
        query = query.filter(WholesaleProperty.id.in_(
            db.query(WholesaleDeal.property_id).filter(
                WholesaleDeal.organization_id == org_id,
                WholesaleDeal.stage == stage)))
    if band:
        query = query.filter(WholesaleProperty.id.in_(
            db.query(WholesaleDeal.property_id).filter(
                WholesaleDeal.organization_id == org_id,
                WholesaleDeal.seller_profile_id.in_(
                    db.query(WholesaleSellerProfile.id).filter(
                        WholesaleSellerProfile.organization_id == org_id,
                        WholesaleSellerProfile.qualification_band == band)))))
    if with_next_action and limit > 50:
        limit = 50
    total = query.count()
    rows = (query.order_by(WholesaleProperty.created_at.desc())
            .offset(offset).limit(limit).all())

    prop_ids = [r.id for r in rows] or [""]
    deals = {d.property_id: d for d in db.query(WholesaleDeal).filter(
        WholesaleDeal.organization_id == org_id,
        WholesaleDeal.property_id.in_(prop_ids)).all()}

    covers: Dict[str, str] = {}
    for f in (db.query(WholesaleFile)
              .filter(WholesaleFile.organization_id == org_id,
                      WholesaleFile.kind == "property_photo",
                      WholesaleFile.is_primary.is_(True),
                      WholesaleFile.property_id.in_(prop_ids)).all()):
        covers[f.property_id] = "/wholesale/files/%s" % f.id

    profiles: Dict[str, Any] = {}
    leads: Dict[str, Any] = {}
    seller_ids = [d.seller_profile_id for d in deals.values() if d.seller_profile_id]
    if seller_ids:
        for pr in db.query(WholesaleSellerProfile).filter(
                WholesaleSellerProfile.organization_id == org_id,
                WholesaleSellerProfile.id.in_(seller_ids)).all():
            profiles[pr.id] = pr
    lead_ids = [d.seller_lead_id for d in deals.values() if d.seller_lead_id]
    if lead_ids:
        for ld in db.query(Lead).filter(Lead.id.in_(lead_ids)).all():
            leads[ld.id] = ld

    last_seen: Dict[str, Any] = {}
    deal_ids = [d.id for d in deals.values()]
    if deal_ids:
        from sqlalchemy import func as _func
        for row_deal_id, when in (
                db.query(WholesaleEvent.deal_id,
                         _func.max(WholesaleEvent.created_at))
                .filter(WholesaleEvent.organization_id == org_id,
                        WholesaleEvent.deal_id.in_(deal_ids))
                .group_by(WholesaleEvent.deal_id).all()):
            last_seen[row_deal_id] = when

    out = []
    for p in rows:
        item = property_json(p)
        deal = deals.get(p.id)
        item["deal"] = deal_json(deal, settings) if deal else None
        item["photo_url"] = covers.get(p.id)
        if deal is not None:
            profile = profiles.get(deal.seller_profile_id)
            item["seller"] = seller_json(profile, leads.get(deal.seller_lead_id))
            when = last_seen.get(deal.id)
            item["last_activity_at"] = when.isoformat() if when else None
            # The same function the deal room header and the Command Center
            # read, so three screens cannot disagree about one deal.
            item["next_action"] = (
                svc.next_action(db, org_id, deal, profile,
                                leads.get(deal.seller_lead_id))
                if with_next_action else None)
        else:
            item["seller"] = None
            item["last_activity_at"] = None
            item["next_action"] = None
        out.append(item)
    return {"total": total, "limit": limit, "offset": offset, "properties": out}


@router.post("/properties")
def create_property(payload: PropertyIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    data = payload.model_dump(exclude_unset=True)
    if not any(data.get(f) for f in ("street_address", "parcel_apn", "owner_name")):
        raise HTTPException(
            status_code=400,
            detail="A property needs at least a street address, a parcel/APN or an "
                   "owner name — something to identify it by. Everything else can "
                   "be filled in later.")
    data.setdefault("acquisition_source", "manual")
    prop = svc.create_property(db, org_id, user, data)
    db.commit()
    db.refresh(prop)
    settings = svc.resolve_settings(db, org_id)
    deal = svc.deal_for_property(db, org_id, prop.id)
    out = property_json(prop)
    out["deal"] = deal_json(deal, settings) if deal else None
    return out


@router.patch("/properties/{property_id}")
def update_property(property_id: str, payload: PropertyIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    prop = svc.get_property(db, org_id, property_id)
    before = property_json(prop)
    data = payload.model_dump(exclude_unset=True)
    # This is an EDIT: a field the user emptied must actually empty. Create
    # keeps the old behaviour, where a null simply means "not supplied".
    svc._apply_property_fields(prop, data, allow_clear=True)
    if "assigned_to_id" in data:
        prop.assigned_to_id = data["assigned_to_id"]
    db.flush()
    svc.log_event(db, org_id, "property.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, property_id=prop.id,
                  summary="Property updated", before=before, after=property_json(prop))
    db.commit()
    db.refresh(prop)
    return property_json(prop)


# ── CSV import ──────────────────────────────────────────────────────────────
#
# A first-class path, not a fallback. With no property-data provider connected
# this and manual entry ARE the intake, and they write exactly the rows an API
# would. Unknown columns are kept in `notes` rather than dropped, because a
# column somebody bothered to export is usually a column that means something.

PROPERTY_CSV_ALIASES = {
    "address": "street_address", "street": "street_address",
    "property address": "street_address", "street_address": "street_address",
    "unit": "unit", "apt": "unit",
    "city": "city", "state": "state", "st": "state",
    "zip": "zip_code", "zipcode": "zip_code", "zip_code": "zip_code",
    "postal code": "zip_code",
    "county": "county", "market": "market",
    "apn": "parcel_apn", "parcel": "parcel_apn", "parcel_apn": "parcel_apn",
    "type": "property_type", "property type": "property_type",
    "property_type": "property_type",
    "beds": "bedrooms", "bedrooms": "bedrooms", "br": "bedrooms",
    "baths": "bathrooms", "bathrooms": "bathrooms", "ba": "bathrooms",
    "sqft": "square_feet", "square feet": "square_feet", "sq ft": "square_feet",
    "building sqft": "square_feet", "square_feet": "square_feet",
    "lot": "lot_size_sqft", "lot size": "lot_size_sqft", "lot_size_sqft": "lot_size_sqft",
    "year built": "year_built", "year_built": "year_built", "yearbuilt": "year_built",
    "value": "estimated_value", "estimated value": "estimated_value",
    "estimated_value": "estimated_value", "avm": "estimated_value",
    "mortgage": "mortgage_balance", "mortgage balance": "mortgage_balance",
    "owner": "owner_name", "owner name": "owner_name", "owner_name": "owner_name",
    "ownership": "ownership_type", "ownership_type": "ownership_type",
    "mailing address": "owner_mailing_street",
    "owner_mailing_street": "owner_mailing_street",
    "mailing city": "owner_mailing_city", "owner_mailing_city": "owner_mailing_city",
    "mailing state": "owner_mailing_state", "owner_mailing_state": "owner_mailing_state",
    "mailing zip": "owner_mailing_zip", "owner_mailing_zip": "owner_mailing_zip",
    "occupancy": "occupancy_status", "occupancy_status": "occupancy_status",
    "notes": "notes",
    # Contact columns, which create the seller as well as the property.
    "phone": "_phone", "phone number": "_phone", "owner phone": "_phone",
    "email": "_email", "owner email": "_email",
    "first name": "_first_name", "first_name": "_first_name",
    "last name": "_last_name", "last_name": "_last_name",
}

_NUMERIC = {"bedrooms", "bathrooms", "square_feet", "lot_size_sqft", "year_built",
            "estimated_value", "mortgage_balance"}
_INTEGER = {"square_feet", "lot_size_sqft", "year_built"}


@router.post("/properties/import")
async def import_properties(request: Request,
                            file: UploadFile = File(...),
                            list_name: Optional[str] = Query(None),
                            is_test: bool = Query(False),
                            db: Session = Depends(get_db),
                            user: User = Depends(require_tenant_user),
                            _guard: User = Depends(require_not_observation)):
    """Import a CSV of properties, and the owners' contact details when present.

    Reports every row it could not use, with the row number and the reason. An
    importer that silently drops rows is how a list of 500 becomes a list of 412
    and nobody finds out until a month later.
    """
    org_id = svc.write_org_id(db, user)
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That file is larger than 10MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception:
            raise HTTPException(status_code=400,
                                detail="That file is not readable as text.")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="That file has no header row.")

    mapping = {}
    unmapped = []
    for header in reader.fieldnames:
        key = (header or "").strip().lower()
        if key in PROPERTY_CSV_ALIASES:
            mapping[header] = PROPERTY_CSV_ALIASES[key]
        else:
            unmapped.append(header)

    source = "csv:%s" % (list_name or file.filename or "import")
    created, skipped = 0, []
    settings = svc.resolve_settings(db, org_id, commit=False)

    # ONE COUNT FOR THE WHOLE FILE. A seller is a Lead and therefore occupies a
    # seat in the customer's package; calling the per-row check would re-count
    # the lead table for every line of a ten-thousand-row spreadsheet, which is
    # the exact case `plan_limits.CapacityCounter` exists for.
    #
    # RUNNING OUT OF ROOM DOES NOT ABORT THE IMPORT. The properties are still
    # created — they are not leads and cost nothing — and the owners that did
    # not fit are reported by row number with the reason. Throwing here would
    # lose the whole file's work to a limit that only ever stops the NEXT
    # addition, which is the rule plan_limits states for itself.
    from app.services import plan_limits
    capacity = plan_limits.counter_for_org_id(db, org_id, plan_limits.LIMIT_LEADS)
    capacity_blocked = []

    for i, row in enumerate(reader, start=2):
        data: Dict[str, Any] = {"acquisition_source": source,
                                "source_detail": list_name or file.filename,
                                "is_test": is_test}
        contact: Dict[str, Any] = {}
        extras = []
        for header, value in row.items():
            if value is None or str(value).strip() == "":
                continue
            value = str(value).strip()
            field = mapping.get(header)
            if field is None:
                extras.append("%s: %s" % (header, value))
                continue
            if field.startswith("_"):
                contact[field[1:]] = value
                continue
            if field in _NUMERIC:
                parsed = analysis.money(value)
                if parsed is None:
                    continue
                data[field] = int(parsed) if field in _INTEGER else float(parsed)
            else:
                data[field] = value
        if extras:
            data["notes"] = ((data.get("notes") or "") + "\n" +
                             "\n".join(extras)).strip()

        if not any(data.get(f) for f in ("street_address", "parcel_apn", "owner_name")):
            skipped.append({"row": i, "reason": "no address, parcel or owner name"})
            continue

        prop = svc.create_property(db, org_id, user, data,
                                   actor_type=ACTOR_USER)
        created += 1

        if (contact.get("phone") or contact.get("email")) and capacity.has_room(1):
            contact.setdefault("last_name", data.get("owner_name") or "")
            svc.attach_seller(db, org_id, user, prop, contact, capacity=capacity)
            deal = svc.deal_for_property(db, org_id, prop.id)
            if deal is not None and settings.auto_stage_on_enrichment:
                svc._set_stage_unchecked(db, deal, "ready_for_outreach",
                                         actor_type=ACTOR_AUTOMATION)
        elif contact.get("phone") or contact.get("email"):
            # The property is kept; the owner is not created. Reported, never
            # silently dropped — a row that vanished without a reason is how an
            # import of 500 becomes 412 and nobody finds out for a month.
            capacity_blocked.append({"row": i, "property_id": prop.id})
            deal = svc.deal_for_property(db, org_id, prop.id)
            if deal is not None:
                svc._set_stage_unchecked(db, deal, "enrichment_needed",
                                         actor_type=ACTOR_AUTOMATION)
        else:
            deal = svc.deal_for_property(db, org_id, prop.id)
            if deal is not None:
                svc._set_stage_unchecked(db, deal, "enrichment_needed",
                                         actor_type=ACTOR_AUTOMATION)

    db.commit()
    return {
        "created": created,
        "skipped": skipped,
        "unmapped_columns": unmapped,
        "unmapped_note": ("These columns were not recognised and were kept in each "
                          "property's notes rather than discarded."
                          if unmapped else None),
        "source": source,
        "is_test": is_test,
        "capacity_blocked": capacity_blocked,
        "capacity_note": (
            "%d row(s) carried contact details but this plan's active-lead "
            "allowance is full, so the owner was not created. The properties "
            "were kept and are sitting in ENRICHMENT NEEDED — raise the plan or "
            "clear some leads and add the owners from there."
            % len(capacity_blocked)) if capacity_blocked else None,
    }


# ── Sellers ─────────────────────────────────────────────────────────────────

class SellerIn(BaseModel):
    lead_id: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    owner_status: Optional[str] = None
    relationship_note: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    is_available: Optional[bool] = None
    considering_selling: Optional[bool] = None
    asking_price: Optional[float] = None
    timeline: Optional[str] = None
    motivation: Optional[str] = None
    reason_for_selling: Optional[str] = None
    property_condition: Optional[str] = None
    major_repairs: Optional[str] = None
    occupancy: Optional[str] = None
    mortgage_note: Optional[str] = None
    decision_makers: Optional[str] = None
    best_callback_time: Optional[str] = None
    appointment_status: Optional[str] = None
    notes: Optional[str] = None


@router.post("/properties/{property_id}/seller")
def attach_seller(property_id: str, payload: SellerIn, request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user),
                  _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    prop = svc.get_property(db, org_id, property_id)
    data = payload.model_dump(exclude_unset=True)
    if not data.get("lead_id") and not any(
            data.get(f) for f in ("first_name", "last_name", "phone", "email")):
        raise HTTPException(
            status_code=400,
            detail="An owner needs a name or a way to reach them. If you have "
                   "neither yet, run enrichment or import the contact details.")
    profile = svc.attach_seller(db, org_id, user, prop, data)
    db.commit()
    db.refresh(profile)
    lead = db.query(Lead).filter(Lead.id == profile.lead_id).first()
    return seller_json(profile, lead)


@router.patch("/sellers/{profile_id}")
def update_seller(profile_id: str, payload: SellerIn, request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user),
                  _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    profile = (db.query(WholesaleSellerProfile)
               .filter(WholesaleSellerProfile.id == profile_id,
                       WholesaleSellerProfile.organization_id == org_id).first())
    if profile is None:
        raise HTTPException(status_code=404, detail="Seller not found")
    lead = db.query(Lead).filter(Lead.id == profile.lead_id).first()
    before = seller_json(profile, lead)
    data = payload.model_dump(exclude_unset=True)
    svc.apply_seller_fields(profile, data)

    # Contact details belong to the LEAD, not to this profile. Writing them here
    # would create a second copy of a phone number that every compliance check
    # reads from the lead — which is exactly the split this module exists to
    # avoid. See the models docstring.
    if lead is not None:
        if data.get("phone"):
            from app.services.dedup_service import normalize_phone
            lead.phone_raw = data["phone"]
            lead.phone = normalize_phone(data["phone"]) or data["phone"]
        if data.get("email"):
            lead.email = data["email"]
        if data.get("first_name"):
            lead.first_name = data["first_name"]
        if data.get("last_name"):
            lead.last_name = data["last_name"]

    deal = (db.query(WholesaleDeal)
            .filter(WholesaleDeal.seller_profile_id == profile.id,
                    WholesaleDeal.organization_id == org_id).first())
    settings = svc.resolve_settings(db, org_id, commit=False)
    qual = analysis.qualify_seller(profile, deal, settings, lead)
    profile.qualification_band = qual["band"]
    profile.qualification_score = qual["score"]
    profile.completeness = qual["completeness"]
    profile.qualification_reasons = json.dumps(qual["reasons"])

    db.flush()
    svc.log_event(db, org_id, "seller.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=getattr(deal, "id", None),
                  summary="Seller details updated", before=before,
                  after=seller_json(profile, lead))
    db.commit()
    db.refresh(profile)
    return seller_json(profile, lead)


class OutreachIn(BaseModel):
    message: str
    channel: str = "sms"


@router.post("/deals/{deal_id}/outreach")
def start_outreach(deal_id: str, payload: OutreachIn, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    """Send the first message to a seller, through the platform's OWN send path.

    THIS MODULE DOES NOT HAVE A SENDER OF ITS OWN, and that is the decision this
    endpoint exists to express. `sms_service.send_sms` owns Twilio credential
    resolution, the suppression check, the consent record, the message row and
    the delivery receipt. A second sender here would be a second copy of all of
    that, and the two would drift — which is how somebody eventually gets a
    message the compliance system thought it had stopped.

    Three refusals, each with the real reason rather than a code:

        sandbox record   deliberately blocked. `test_records.is_outreach_eligible`
                         is the same gate every other outreach path in this
                         platform uses, and a rehearsal that could text a real
                         phone would not be a rehearsal.
        DNC / opt-out    refused by the same gate, before Twilio is touched.
        provider failure the error is returned as-is and recorded. Nothing
                         pretends a send happened.
    """
    from app.services import test_records

    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    if payload.channel != "sms":
        raise HTTPException(
            status_code=400,
            detail="Only SMS is wired through this endpoint today. Email outreach "
                   "goes through the existing email queue on the lead.")
    if not deal.seller_lead_id:
        raise HTTPException(status_code=400,
                            detail="This deal has no owner attached yet.")
    lead = (db.query(Lead)
            .filter(Lead.id == deal.seller_lead_id, Lead.organization_id == org_id)
            .first())
    if lead is None:
        raise HTTPException(status_code=404, detail="Seller contact not found")

    if not test_records.is_outreach_eligible(lead):
        reason = test_records.blocked_reason(lead) or "this contact cannot be messaged"
        svc.log_event(db, org_id, "outreach.blocked", actor_type=ACTOR_USER,
                      actor_user_id=user.id, deal_id=deal.id,
                      summary="Outreach refused: %s" % reason)
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="Nothing was sent — %s. Every other part of this deal keeps "
                   "working; only the send is blocked." % reason)
    if not lead.phone:
        raise HTTPException(status_code=400,
                            detail="No phone number on this owner. Add one, or run "
                                   "enrichment.")

    try:
        from app.services import sms_service
        message = sms_service.send_sms(db, user, lead, payload.message,
                                       include_booking_link=False)
    except Exception as exc:                                    # noqa: BLE001
        # A provider failure is recorded and reported. It never looks like success.
        svc.log_event(db, org_id, "outreach.failed", actor_type=ACTOR_USER,
                      actor_user_id=user.id, deal_id=deal.id,
                      summary="Send failed: %s" % str(exc)[:180])
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="Nothing was sent. The messaging service refused: %s"
                   % str(exc)[:300])

    svc._set_stage_unchecked(db, deal, "outreach_active",
                             actor_type=ACTOR_USER, actor_user_id=user.id)
    svc.log_event(db, org_id, "outreach.sent", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="First SMS sent to the owner",
                  after={"message_id": getattr(message, "id", None)})
    db.commit()
    return {"sent": True, "message_id": getattr(message, "id", None),
            "stage": deal.stage}


class CadenceIn(BaseModel):
    action: str
    reason: Optional[str] = None


@router.get("/deals/{deal_id}/cadence")
def get_cadence(deal_id: str, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer)):
    """Where this seller is in the multi-touch sequence, and what can be done next."""
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    deal = svc.get_deal(db, org_id, deal_id)
    return svc.cadence_status(db, org_id, deal)


@router.post("/deals/{deal_id}/cadence")
def control_cadence(deal_id: str, payload: CadenceIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    """start / pause / resume / stop the seller's multi-touch sequence.

    THIS IS A CONTROL SURFACE OVER THE PLATFORM'S OWN CADENCE ENGINE, not a
    second one. `cadence_service` owns the schedule, the touches, the templates
    and the cron; the seller is a Lead, so all of that already applies to them.
    What this adds is the wholesale-specific refusals — a sandbox deal, a closed
    or dead deal — on top of the engine's own (DNC, duplicate, email-only, plan
    capacity hold).

    A cadence also stops on its own when the deal closes or dies and when the
    owner opts out. Those are in `wholesale_service`, not here, because they
    must happen whether or not anybody visits this endpoint.
    """
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    if payload.action not in ("start", "pause", "resume", "stop"):
        raise HTTPException(status_code=400,
                            detail="action must be start, pause, resume or stop")
    result = svc.control_cadence(db, org_id, deal, payload.action, user, payload.reason)
    db.commit()
    return result


class ReplyIn(BaseModel):
    message: str
    mode: str = "manual"


@router.post("/deals/{deal_id}/seller-reply")
def seller_reply(deal_id: str, payload: ReplyIn, request: Request,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    """Feed a seller's message through qualification.

    `mode` is the AI gateway's mode. "manual" means a signed-in person asked for
    this one reading, which is the normal case from the deal room; the automated
    conversation path calls the service directly in "background" mode, where the
    platform's spend caps and circuit breaker apply.
    """
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    if payload.mode not in ("manual", "background"):
        raise HTTPException(status_code=400, detail="mode must be manual or background")
    # A person typing what the owner said is the ONLY way those words enter the
    # system in a deployment with no inbound channel, so the manual path puts
    # the message on the conversation record. The background path does not: the
    # conversation engine already wrote the inbound message it is reacting to,
    # and writing a second one would double every reply on the thread.
    result = svc.apply_seller_reply(db, org_id, deal, payload.message,
                                    user=user, mode=payload.mode,
                                    record_inbound=(payload.mode == "manual"))
    db.commit()
    return result


# ── Enrichment ──────────────────────────────────────────────────────────────

class EnrichIn(BaseModel):
    property_ids: List[str]


class ManualContactIn(BaseModel):
    phone: Optional[str] = None
    email: Optional[str] = None
    owner_name: Optional[str] = None
    mailing_street: Optional[str] = None
    mailing_city: Optional[str] = None
    mailing_state: Optional[str] = None
    mailing_zip: Optional[str] = None
    note: Optional[str] = None


@router.get("/enrichment/providers")
def enrichment_providers(db: Session = Depends(get_db),
                         user: User = Depends(require_tenant_or_observer)):
    """Which providers exist, and which can actually run right now."""
    return {"providers": enrichment.provider_report()}


@router.post("/enrichment/run")
def run_enrichment(payload: EnrichIn, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    """Attempt enrichment on selected properties.

    With no provider connected this returns `status="manual"` per property and a
    clear message — it does NOT fail, and it does not invent a phone number. The
    property stays exactly where it was and a person can type the details in.
    """
    org_id = svc.write_org_id(db, user)
    settings = svc.resolve_settings(db, org_id, commit=False)
    provider = enrichment.get_provider(settings.enrichment_provider)

    refusal = enrichment.admit(db, org_id, provider, settings,
                               requested=len(payload.property_ids))
    if refusal:
        raise HTTPException(status_code=429, detail=refusal)

    results = []
    for property_id in payload.property_ids[:200]:
        prop = (db.query(WholesaleProperty)
                .filter(WholesaleProperty.id == property_id,
                        WholesaleProperty.organization_id == org_id).first())
        if prop is None:
            results.append({"property_id": property_id, "status": "not_found"})
            continue

        data = enrichment.EnrichmentInput(
            street_address=prop.street_address, city=prop.city, state=prop.state,
            zip_code=prop.zip_code, county=prop.county, parcel_apn=prop.parcel_apn,
            owner_name=prop.owner_name,
            business_name=prop.owner_name if prop.ownership_type == "llc" else None,
            mailing_street=prop.owner_mailing_street,
            mailing_city=prop.owner_mailing_city,
            mailing_state=prop.owner_mailing_state,
            mailing_zip=prop.owner_mailing_zip)

        record = WholesaleEnrichmentRequest(
            organization_id=org_id, property_id=prop.id, provider=provider.key,
            requested_by_id=user.id, requested_by_actor=ACTOR_USER,
            inputs=data.to_json(), billable=provider.billable)
        db.add(record)
        db.flush()

        try:
            result = provider.lookup(data)
        except Exception as exc:                                # noqa: BLE001
            # A provider that raises is a provider that failed. The workflow is
            # not destroyed: the row records the failure and a person can retry
            # or enter the details by hand.
            log.warning("wholesale enrichment provider %s raised: %s", provider.key, exc)
            result = enrichment.EnrichmentResult(
                status=enrichment.STATUS_FAILED, provider=provider.key,
                message="%s: %s" % (type(exc).__name__, str(exc)[:200]))

        record.status = result.status
        record.result = result.to_json()
        record.confidence = result.confidence
        record.error = result.message if result.status in (
            enrichment.STATUS_FAILED, enrichment.STATUS_NOT_CONFIGURED) else None
        record.billable = bool(result.billable)
        record.cost_cents = result.cost_cents
        record.completed_at = datetime.utcnow()

        deal = svc.deal_for_property(db, org_id, prop.id)
        if result.found_anything:
            applied = _apply_enrichment(db, org_id, user, prop, result)
            if deal is not None and settings.auto_stage_on_enrichment:
                svc._set_stage_unchecked(db, deal, "ready_for_outreach",
                                         actor_type=ACTOR_AUTOMATION)
            record.lead_id = applied
        elif deal is not None and deal.stage in ("new_property", "owner_identified"):
            svc._set_stage_unchecked(db, deal, "enrichment_needed",
                                     actor_type=ACTOR_AUTOMATION)

        svc.log_event(db, org_id, "enrichment.returned",
                      actor_type=ACTOR_USER, actor_user_id=user.id,
                      property_id=prop.id, deal_id=getattr(deal, "id", None),
                      summary="%s: %s" % (provider.key, result.status),
                      after={"status": result.status,
                             "phones": len(result.phones),
                             "emails": len(result.emails)})
        results.append({"property_id": prop.id, "status": result.status,
                        "message": result.message,
                        "phones": [p.number for p in result.phones],
                        "emails": list(result.emails)})

    db.commit()
    return {"provider": provider.key, "provider_label": provider.label,
            "configured": provider.is_configured(), "results": results}


def _apply_enrichment(db: Session, org_id: str, user: User,
                      prop: WholesaleProperty,
                      result: enrichment.EnrichmentResult) -> Optional[str]:
    """Write a provider's findings onto the seller record, never over a person.

    An existing phone or email entered by a human is NOT overwritten by a
    provider response. A skip trace is evidence, not authority, and silently
    replacing a number somebody confirmed on a call is how outreach starts going
    to the wrong phone.
    """
    profile = (db.query(WholesaleSellerProfile)
               .filter(WholesaleSellerProfile.property_id == prop.id,
                       WholesaleSellerProfile.organization_id == org_id).first())
    phone = result.phones[0].number if result.phones else None
    email = result.emails[0] if result.emails else None

    if profile is None:
        data = {"phone": phone, "email": email}
        name = (result.owner_name or prop.owner_name or "").strip()
        if name:
            parts = name.split()
            data["first_name"] = parts[0] if len(parts) > 1 else None
            data["last_name"] = parts[-1]
        profile = svc.attach_seller(db, org_id, user, prop, data)
        return profile.lead_id

    lead = db.query(Lead).filter(Lead.id == profile.lead_id).first()
    if lead is not None:
        if phone and not lead.phone:
            from app.services.dedup_service import normalize_phone
            lead.phone = normalize_phone(phone) or phone
            lead.phone_raw = phone
            lead.contact_channel = "sms"
        if email and not lead.email:
            lead.email = email
    return profile.lead_id


@router.post("/properties/{property_id}/manual-contact")
def manual_contact(property_id: str, payload: ManualContactIn, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    """The manual fallback, recorded as a first-class enrichment result.

    It writes the same row shape a provider would, with `provider="manual"`, so
    the enrichment history is complete and the success-rate metric counts what
    actually happened rather than only what an API did.
    """
    org_id = svc.write_org_id(db, user)
    prop = svc.get_property(db, org_id, property_id)
    if not payload.phone and not payload.email:
        raise HTTPException(status_code=400,
                            detail="Enter a phone number or an email address.")

    result = enrichment.EnrichmentResult(
        status=enrichment.STATUS_SUCCEEDED, provider="manual",
        phones=([enrichment.EnrichmentPhone(number=payload.phone, source="manual")]
                if payload.phone else []),
        emails=[payload.email] if payload.email else [],
        owner_name=payload.owner_name,
        mailing_street=payload.mailing_street, mailing_city=payload.mailing_city,
        mailing_state=payload.mailing_state, mailing_zip=payload.mailing_zip,
        message=payload.note or "Entered by a person.")

    record = WholesaleEnrichmentRequest(
        organization_id=org_id, property_id=prop.id, provider="manual",
        requested_by_id=user.id, requested_by_actor=ACTOR_USER,
        status=enrichment.STATUS_SUCCEEDED, result=result.to_json(),
        billable=False, completed_at=datetime.utcnow())
    db.add(record)

    if payload.owner_name and not prop.owner_name:
        prop.owner_name = payload.owner_name
    for field, value in (("owner_mailing_street", payload.mailing_street),
                         ("owner_mailing_city", payload.mailing_city),
                         ("owner_mailing_state", payload.mailing_state),
                         ("owner_mailing_zip", payload.mailing_zip)):
        if value and not getattr(prop, field):
            setattr(prop, field, value)

    lead_id = _apply_enrichment(db, org_id, user, prop, result)
    record.lead_id = lead_id

    settings = svc.resolve_settings(db, org_id, commit=False)
    deal = svc.deal_for_property(db, org_id, prop.id)
    if deal is not None and settings.auto_stage_on_enrichment:
        svc._set_stage_unchecked(db, deal, "ready_for_outreach",
                                 actor_type=ACTOR_AUTOMATION)

    svc.log_event(db, org_id, "enrichment.manual", actor_type=ACTOR_USER,
                  actor_user_id=user.id, property_id=prop.id,
                  deal_id=getattr(deal, "id", None),
                  summary="Contact details entered by hand")
    db.commit()
    return {"status": "succeeded", "lead_id": lead_id,
            "stage": getattr(deal, "stage", None)}


@router.get("/properties/{property_id}/enrichment")
def property_enrichment_history(property_id: str, db: Session = Depends(get_db),
                                user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    # The query below is org-scoped, so nothing of anybody else's could come
    # back — but WITHOUT this the endpoint answered 200 and an empty list for
    # any id at all, including another tenant's, which is an existence oracle
    # and is not how the other 31 id-bearing endpoints in this module behave.
    # Found by tests/test_wholesale_cross_tenant.py.
    svc.get_property(db, org_id, property_id)
    rows = (db.query(WholesaleEnrichmentRequest)
            .filter(WholesaleEnrichmentRequest.organization_id == org_id,
                    WholesaleEnrichmentRequest.property_id == property_id)
            .order_by(WholesaleEnrichmentRequest.created_at.desc()).all())
    return {"requests": [enrichment_json(r) for r in rows]}


# ── Deals and the deal room ─────────────────────────────────────────────────

@router.get("/deals")
def list_deals(db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer),
               stage: Optional[str] = None,
               assigned_to_id: Optional[str] = None,
               band: Optional[str] = None,
               q: Optional[str] = None,
               include_test: bool = False,
               with_next_action: bool = False,
               limit: int = Query(100, le=500), offset: int = 0):
    """The deal list, with enough on each row to work from.

    `with_next_action` is OPT-IN and clamps the page size, because working out
    what a deal is waiting on costs a query or two per deal. The list screen
    asks for it on a page of fifty; a caller that only wants the rows does not
    pay for it. That is a deliberate trade rather than a silently slow endpoint.
    """
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    settings = svc.resolve_settings(db, org_id)
    query = db.query(WholesaleDeal).filter(WholesaleDeal.organization_id == org_id)
    if not include_test:
        query = query.filter(WholesaleDeal.is_test.isnot(True))
    if stage:
        query = query.filter(WholesaleDeal.stage == stage)
    if assigned_to_id:
        query = query.filter(WholesaleDeal.assigned_to_id == assigned_to_id)
    if q:
        # Address search. Scoped to this organization's properties by the join
        # condition, not merely by the outer filter.
        needle = "%%%s%%" % q.strip()
        query = query.filter(WholesaleDeal.property_id.in_(
            db.query(WholesaleProperty.id).filter(
                WholesaleProperty.organization_id == org_id,
                WholesaleProperty.street_address.ilike(needle))))
    if with_next_action and limit > 50:
        limit = 50
    total = query.count()
    rows = (query.order_by(WholesaleDeal.updated_at.desc())
            .offset(offset).limit(limit).all())

    props = {p.id: p for p in db.query(WholesaleProperty).filter(
        WholesaleProperty.id.in_([r.property_id for r in rows] or [""])).all()}
    profiles = {p.id: p for p in db.query(WholesaleSellerProfile).filter(
        WholesaleSellerProfile.id.in_(
            [r.seller_profile_id for r in rows if r.seller_profile_id] or [""])).all()}
    leads = {l.id: l for l in db.query(Lead).filter(
        Lead.id.in_([r.seller_lead_id for r in rows if r.seller_lead_id] or [""])).all()}

    # Cover photos and last activity, batched for the whole page rather than
    # fetched per row — a list screen that issues two queries per line is the
    # reason lists get capped at ten items and then called "fast".
    covers: Dict[str, str] = {}
    prop_ids = [r.property_id for r in rows if r.property_id]
    if prop_ids:
        for f in (db.query(WholesaleFile)
                  .filter(WholesaleFile.organization_id == org_id,
                          WholesaleFile.kind == "property_photo",
                          WholesaleFile.is_primary.is_(True),
                          WholesaleFile.property_id.in_(prop_ids)).all()):
            covers[f.property_id] = "/wholesale/files/%s" % f.id

    last_seen: Dict[str, Any] = {}
    deal_ids = [r.id for r in rows]
    if deal_ids:
        from sqlalchemy import func as _func
        for deal_id_value, when in (
                db.query(WholesaleEvent.deal_id,
                         _func.max(WholesaleEvent.created_at))
                .filter(WholesaleEvent.organization_id == org_id,
                        WholesaleEvent.deal_id.in_(deal_ids))
                .group_by(WholesaleEvent.deal_id).all()):
            last_seen[deal_id_value] = when

    out = []
    for d in rows:
        profile = profiles.get(d.seller_profile_id)
        if band and getattr(profile, "qualification_band", None) != band:
            continue
        lead = leads.get(d.seller_lead_id)
        item = deal_json(d, settings)
        item["property"] = property_json(props[d.property_id]) if d.property_id in props else None
        item["seller"] = seller_json(profile, lead)
        item["photo_url"] = covers.get(d.property_id)
        when = last_seen.get(d.id)
        item["last_activity_at"] = when.isoformat() if when else None
        # Computed by the same function the deal room header and the Command
        # Center use, so three screens cannot disagree about one deal.
        item["next_action"] = (svc.next_action(db, org_id, d, profile, lead)
                               if with_next_action else None)
        out.append(item)
    return {"total": total, "limit": limit, "offset": offset, "deals": out}


@router.get("/deals/{deal_id}")
def deal_room(deal_id: str, db: Session = Depends(get_db),
              user: User = Depends(require_tenant_or_observer)):
    """EVERYTHING about one deal, in one response.

    The whole point of the deal room: property, seller, communications,
    qualification, analysis, comps, offer, approvals, contracts, buyer matches,
    buyer communications, assignment, title, closing, financial result and the
    audit history — one request, one screen, no hunting.
    """
    from app.models.models import Message, Reply
    from app.models.wholesale_models import (WholesaleBuyer, WholesaleBuyerMatch,
                                             WholesaleBuyerOutreach)

    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    settings = svc.resolve_settings(db, org_id)
    deal = svc.get_deal(db, org_id, deal_id)
    prop = db.query(WholesaleProperty).filter(
        WholesaleProperty.id == deal.property_id).first()
    profile = (db.query(WholesaleSellerProfile)
               .filter(WholesaleSellerProfile.id == deal.seller_profile_id).first()
               if deal.seller_profile_id else None)
    lead = (db.query(Lead).filter(Lead.id == deal.seller_lead_id).first()
            if deal.seller_lead_id else None)

    comps = (db.query(WholesaleComp)
             .filter(WholesaleComp.deal_id == deal.id,
                     WholesaleComp.organization_id == org_id)
             .order_by(WholesaleComp.created_at.desc()).all())
    approvals = (db.query(WholesaleApproval)
                 .filter(WholesaleApproval.deal_id == deal.id,
                         WholesaleApproval.organization_id == org_id)
                 .order_by(WholesaleApproval.created_at.desc()).all())
    documents = (db.query(WholesaleDocument)
                 .filter(WholesaleDocument.deal_id == deal.id,
                         WholesaleDocument.organization_id == org_id)
                 .order_by(WholesaleDocument.created_at.desc()).all())
    matches = (db.query(WholesaleBuyerMatch)
               .filter(WholesaleBuyerMatch.deal_id == deal.id,
                       WholesaleBuyerMatch.organization_id == org_id)
               .order_by(WholesaleBuyerMatch.score.desc()).all())
    outreach = (db.query(WholesaleBuyerOutreach)
                .filter(WholesaleBuyerOutreach.deal_id == deal.id,
                        WholesaleBuyerOutreach.organization_id == org_id)
                .order_by(WholesaleBuyerOutreach.created_at.desc()).all())
    buyers = {b.id: b for b in db.query(WholesaleBuyer).filter(
        WholesaleBuyer.organization_id == org_id).all()}
    events = (db.query(WholesaleEvent)
              .filter(WholesaleEvent.organization_id == org_id,
                      WholesaleEvent.deal_id == deal.id)
              .order_by(WholesaleEvent.created_at.desc()).limit(200).all())

    # The seller conversation comes from the platform's existing message tables,
    # not from a wholesale copy of them. One conversation, one place.
    messages, replies = [], []
    if lead is not None:
        # `Message.sent_at` and `Reply.received_at`, not `created_at`: these two
        # tables name their timestamp after the event rather than after the row,
        # and reading them as `created_at` is an AttributeError at request time
        # rather than a wrong answer — which is how this was caught.
        messages = (db.query(Message).filter(Message.lead_id == lead.id)
                    .order_by(Message.sent_at.desc()).limit(100).all())
        replies = (db.query(Reply).filter(Reply.lead_id == lead.id)
                   .order_by(Reply.received_at.desc()).limit(100).all())

    summary = analysis.deal_summary(deal, settings)
    arv_calc = analysis.arv_from_comps(comps, getattr(prop, "square_feet", None))
    comp_photos = _comp_photos(db, org_id, [c.id for c in comps])
    # The stored file behind each document, in one query for the whole drawer.
    doc_file_ids = [getattr(d, "file_id", None) for d in documents]
    doc_file_ids = [i for i in doc_file_ids if i]
    doc_files = ({f.id: f for f in db.query(WholesaleFile).filter(
        WholesaleFile.organization_id == org_id,
        WholesaleFile.id.in_(doc_file_ids)).all()} if doc_file_ids else {})

    # Phase 4: what the match list needs to be decided from. Two batched
    # queries for the whole list, not two per row.
    from app.models.wholesale_models import WholesaleBuyBox
    from app.routers.wholesale_buyers_router import _buyer_activity
    matched_ids = [m.buyer_id for m in matches if m.buyer_id]
    buy_boxes: Dict[str, List[Any]] = {}
    if matched_ids:
        for box in db.query(WholesaleBuyBox).filter(
                WholesaleBuyBox.organization_id == org_id,
                WholesaleBuyBox.buyer_id.in_(matched_ids)).all():
            buy_boxes.setdefault(box.buyer_id, []).append(box)
    match_activity = _buyer_activity(db, org_id, matched_ids) if matched_ids else {}

    return {
        "deal": deal_json(deal, settings),
        "property": property_json(prop) if prop else None,
        "seller": seller_json(profile, lead),
        "analysis": summary,
        "arv_calculation": arv_calc,
        "comps": [comp_json(c, comp_photos.get(c.id)) for c in comps],
        # The comp set described as numbers — median AND average $/sqft, the
        # spread, and where the subject sits in it. Computed in the analysis
        # service so it is testable without a database and cannot drift from
        # the ARV working shown beside it.
        "comp_statistics": analysis.comp_statistics(
            comps, getattr(prop, "square_feet", None), deal.arv),
        "approvals": [approval_json(a) for a in approvals],
        "documents": [document_json(d, doc_files.get(getattr(d, "file_id", None)))
                      for d in documents],
        # Phase 4: the match list carried a score, a name and a wall of
        # criterion-by-criterion reasoning, and nothing an operator decides on.
        # Deciding who to send a deal sheet to is a judgement about whether the
        # buyer can actually take it — where they buy, how fast they close,
        # whether they have funds on file, what they have done here before. All
        # of that already existed; none of it reached this screen.
        "buyer_matches": [{
            "id": m.id, "buyer_id": m.buyer_id, "score": m.score,
            "factors": _jsonl(m.factors), "disqualified": bool(m.disqualified),
            "disqualified_reason": m.disqualified_reason,
            "buyer_name": (getattr(buyers.get(m.buyer_id), "company_name", None)
                           or getattr(buyers.get(m.buyer_id), "contact_name", None)),
            "contact_name": getattr(buyers.get(m.buyer_id), "contact_name", None),
            "geography": _buy_box_geography(buy_boxes.get(m.buyer_id, [])),
            "max_price": _buy_box_ceiling(buy_boxes.get(m.buyer_id, [])),
            "buy_box_count": len(buy_boxes.get(m.buyer_id, [])),
            "typical_close_days": getattr(buyers.get(m.buyer_id),
                                          "typical_close_days", None),
            "reliability_rating": getattr(buyers.get(m.buyer_id),
                                          "reliability_rating", None),
            "proof_of_funds_on_file": bool(getattr(
                buyers.get(m.buyer_id), "proof_of_funds_on_file", False)),
            "do_not_contact": bool(getattr(buyers.get(m.buyer_id),
                                           "do_not_contact", False)),
            "activity": match_activity.get(m.buyer_id),
            "computed_at": m.computed_at.isoformat() if m.computed_at else None,
        } for m in matches],
        "buyer_outreach": [{
            "id": o.id, "buyer_id": o.buyer_id,
            "buyer_name": (getattr(buyers.get(o.buyer_id), "company_name", None)
                           or getattr(buyers.get(o.buyer_id), "contact_name", None)),
            "channel": o.channel, "status": o.status, "subject": o.subject,
            "body": o.body, "asking_price": _num(o.asking_price),
            "offer_amount": _num(o.offer_amount),
            "response_note": o.response_note, "blocked_reason": o.blocked_reason,
            "sent_at": o.sent_at.isoformat() if o.sent_at else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        } for o in outreach],
        "communications": {
            "outbound": [{"id": m.id, "body": getattr(m, "body", None),
                          "status": getattr(m, "delivery_status", None),
                          "sent_at": m.sent_at.isoformat() if m.sent_at else None}
                         for m in messages],
            "inbound": [{"id": r.id, "body": getattr(r, "body", None),
                         "classification": _enum_text(
                             getattr(r, "classification", None)),
                         # "manual" means somebody typed what they were told.
                         # The thread says so rather than letting it read as a
                         # message that arrived on its own.
                         "source": getattr(r, "source", None),
                         "received_at": (r.received_at.isoformat()
                                         if r.received_at else None)}
                        for r in replies],
        },
        "events": [event_json(e) for e in events],
        # Phase 5. Which approval is genuinely next, and what each of the other
        # two is still missing. The screen makes the valid one primary and
        # explains the others rather than offering three identical buttons.
        "approval_readiness": svc.approval_readiness(db, org_id, deal),
        "stages": pipeline.resolve_stages(settings),
        # Phase 2: the seller's place in the multi-touch sequence, and whether
        # this deployment can actually send it. In the deal room payload rather
        # than behind its own request so the screen never renders a Start button
        # for a cadence that is already running.
        "cadence": svc.cadence_status(db, org_id, deal),
        # Phase 2: whether a deal sheet can actually leave this deployment, so
        # the disposition tab can say so before somebody presses Send rather
        # than after.
        "disposition_channels": _disposition_channels(),
        # Phase 2: this module records the document SLOT — type, parties,
        # signature state and the file name as the operator stored it. It does
        # not host files, and it does not need to: the platform already has one
        # file capability, `mobile_storage`, whose `store_upload` accepts PDFs
        # and refuses with a plain reason until MEDIA_STORAGE_BACKEND=s3 and the
        # bucket credentials are set. Reporting that state here is how the
        # documents tab says whether attaching the actual signed file is
        # possible in THIS deployment, instead of letting a person assume the
        # file is somewhere it is not. No second storage layer was built.
        "document_storage": _document_storage(),
        # Phase 3. The header answers "what is this deal" without the reader
        # opening a single tab, so everything it needs arrives with the room.
        "photos": [_photo_json(f) for f in _deal_photos(db, org_id, prop)],
        "offers": [offer_json(o) for o in (
            db.query(WholesaleOffer)
            .filter(WholesaleOffer.organization_id == org_id,
                    WholesaleOffer.deal_id == deal.id)
            .order_by(WholesaleOffer.created_at.asc()).all())],
        # Computed in the service, not here, so this and the Command Center's
        # active-deal list can never disagree about what a deal is waiting on.
        "next_action": svc.next_action(db, org_id, deal, profile, lead),
        "file_storage": _file_capability(),
    }


def _photo_json(row: WholesaleFile) -> Dict[str, Any]:
    return {"id": row.id, "url": "/wholesale/files/%s" % row.id,
            "caption": row.caption, "is_primary": bool(row.is_primary),
            "content_type": row.content_type, "byte_size": row.byte_size,
            "original_filename": row.original_filename,
            "created_at": row.created_at.isoformat() if row.created_at else None}


def _buy_box_geography(boxes: List[Any]) -> Optional[str]:
    """Where a buyer buys, in the words their own buy boxes use.

    Nothing is looked up, mapped or inferred — a market this code has never
    heard of still reads correctly, because it is simply the value they entered.
    An empty result means the buyer constrained no geography at all, which is a
    real answer and not a missing one.
    """
    seen, parts = set(), []
    for box in boxes or []:
        for field in ("markets", "counties", "cities", "states"):
            for value in (_jsonl(getattr(box, field, None)) or []):
                text = str(value).strip()
                if text and text.lower() not in seen:
                    seen.add(text.lower())
                    parts.append(text)
    return ", ".join(parts[:6]) if parts else None


def _buy_box_ceiling(boxes: List[Any]) -> Optional[float]:
    """The highest price any of this buyer's boxes will go to."""
    ceilings = [_num(getattr(b, "max_price", None)) for b in (boxes or [])]
    ceilings = [c for c in ceilings if c is not None]
    return max(ceilings) if ceilings else None


def _comp_photos(db: Session, org_id: str,
                 comp_ids: List[str]) -> Dict[str, WholesaleFile]:
    """One photo per comp, in one query. A comp keeps its most recent photo."""
    if not comp_ids:
        return {}
    rows = (db.query(WholesaleFile)
            .filter(WholesaleFile.organization_id == org_id,
                    WholesaleFile.kind == "comp_photo",
                    WholesaleFile.comp_id.in_(comp_ids))
            .order_by(WholesaleFile.created_at.asc()).all())
    return {r.comp_id: r for r in rows}


def _deal_photos(db: Session, org_id: str, prop) -> List[WholesaleFile]:
    if prop is None:
        return []
    return (db.query(WholesaleFile)
            .filter(WholesaleFile.organization_id == org_id,
                    WholesaleFile.property_id == prop.id,
                    WholesaleFile.kind == "property_photo")
            .order_by(WholesaleFile.is_primary.desc(),
                      WholesaleFile.sort_order.asc()).all())


def _file_capability() -> Dict[str, Any]:
    from app.services import wholesale_files
    return wholesale_files.capability()


def _document_storage():
    """The platform's own file capability, reported, not reimplemented."""
    try:
        from app.services import mobile_storage
        cap = mobile_storage.capability()
    except Exception:  # pragma: no cover - the module is optional to this one
        return {"uploads_enabled": False, "reason": None, "env": None}
    return {
        "uploads_enabled": bool(cap.get("uploads_enabled")),
        "max_bytes": cap.get("max_bytes"),
        "allowed_types": cap.get("allowed_types"),
        "reason": cap.get("reason"),
        "env": None if cap.get("uploads_enabled") else "MEDIA_STORAGE_BACKEND",
    }


def _disposition_channels():
    from app.services import wholesale_disposition as disp
    return disp.channel_status()


class StageIn(BaseModel):
    stage: str
    note: Optional[str] = None


@router.post("/deals/{deal_id}/stage")
def move_stage(deal_id: str, payload: StageIn, request: Request,
               db: Session = Depends(get_db),
               user: User = Depends(require_tenant_user),
               _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    svc.set_stage(db, org_id, deal, payload.stage, user, payload.note)
    db.commit()
    db.refresh(deal)
    return deal_json(deal, svc.resolve_settings(db, org_id))


# ── Comps and analysis ──────────────────────────────────────────────────────

class CompIn(BaseModel):
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    sale_price: Optional[float] = None
    sale_date: Optional[str] = None
    square_feet: Optional[int] = None
    bedrooms: Optional[float] = None
    bathrooms: Optional[float] = None
    distance_miles: Optional[float] = None
    year_built: Optional[int] = None
    property_type: Optional[str] = None
    notes: Optional[str] = None
    included: bool = True


@router.post("/deals/{deal_id}/comps")
def add_comp(deal_id: str, payload: CompIn, request: Request,
             db: Session = Depends(get_db),
             user: User = Depends(require_tenant_user),
             _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    data = payload.model_dump(exclude_unset=True)
    comp = WholesaleComp(
        organization_id=org_id, deal_id=deal.id, source=VALUE_MANUAL,
        sale_date=_parse_date(data.pop("sale_date", None)),
        **{k: v for k, v in data.items() if k != "sale_date"})
    db.add(comp)
    db.flush()
    svc.log_event(db, org_id, "comp.added", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="Comp added: %s" % (comp.street_address or "unnamed"))
    summary = svc.recalculate_analysis(db, org_id, deal, user)
    db.commit()
    return {"comp": comp_json(comp), "analysis": summary}


@router.patch("/comps/{comp_id}")
def update_comp(comp_id: str, payload: CompIn, request: Request,
                db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    comp = (db.query(WholesaleComp)
            .filter(WholesaleComp.id == comp_id,
                    WholesaleComp.organization_id == org_id).first())
    if comp is None:
        raise HTTPException(status_code=404, detail="Comp not found")
    data = payload.model_dump(exclude_unset=True)
    was_included = bool(comp.included)
    if "sale_date" in data:
        comp.sale_date = _parse_date(data.pop("sale_date"))
    for key, value in data.items():
        setattr(comp, key, value)
    deal = svc.get_deal(db, org_id, comp.deal_id)
    label = comp.street_address or "unnamed"
    if "included" in data and bool(data["included"]) != was_included:
        svc.log_event(
            db, org_id,
            "comp.included" if comp.included else "comp.excluded",
            actor_type=ACTOR_USER, actor_user_id=user.id, deal_id=deal.id,
            summary=("Comp %s in the ARV: %s"
                     % ("included" if comp.included else "excluded", label)))
    if [k for k in data if k != "included"]:
        svc.log_event(db, org_id, "comp.updated", actor_type=ACTOR_USER,
                      actor_user_id=user.id, deal_id=deal.id,
                      summary="Comp edited: %s" % label)
    summary = svc.recalculate_analysis(db, org_id, deal, user)
    db.commit()
    return {"comp": comp_json(comp), "analysis": summary}


@router.delete("/comps/{comp_id}")
def delete_comp(comp_id: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    comp = (db.query(WholesaleComp)
            .filter(WholesaleComp.id == comp_id,
                    WholesaleComp.organization_id == org_id).first())
    if comp is None:
        raise HTTPException(status_code=404, detail="Comp not found")
    deal_id = comp.deal_id
    gone = comp.street_address or "unnamed"
    db.delete(comp)
    db.flush()
    deal = svc.get_deal(db, org_id, deal_id)
    summary = svc.recalculate_analysis(db, org_id, deal, user)
    svc.log_event(db, org_id, "comp.removed", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal_id,
                  summary="Comp deleted: %s" % gone)
    db.commit()
    return {"ok": True, "analysis": summary}


class AnalysisIn(BaseModel):
    arv: Optional[float] = None
    arv_source: Optional[str] = None
    repair_estimate: Optional[float] = None
    repair_estimate_source: Optional[str] = None
    repair_notes: Optional[str] = None
    investor_percentage_used: Optional[float] = None
    desired_wholesale_fee: Optional[float] = None
    proposed_offer: Optional[float] = None
    analysis_notes: Optional[str] = None


@router.patch("/deals/{deal_id}/analysis")
def update_analysis(deal_id: str, payload: AnalysisIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    """Edit the assumptions. Every one of them is editable, and labelled."""
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    data = payload.model_dump(exclude_unset=True)

    for field in ("arv_source", "repair_estimate_source"):
        if data.get(field) and data[field] not in VALUE_SOURCES:
            raise HTTPException(status_code=400,
                                detail="%s must be one of: %s"
                                       % (field, ", ".join(VALUE_SOURCES)))
    # A number a person typed is MANUAL unless they said it was verified. It is
    # never left unlabelled, because an unlabelled figure on this screen reads
    # as fact.
    if "arv" in data and data["arv"] is not None and not data.get("arv_source"):
        data["arv_source"] = VALUE_MANUAL
        deal.arv_method = "manual"
    if "repair_estimate" in data and data["repair_estimate"] is not None \
            and not data.get("repair_estimate_source"):
        data["repair_estimate_source"] = VALUE_MANUAL

    before = {k: _num(getattr(deal, k, None))
              for k in ("arv", "repair_estimate", "proposed_offer")}
    for key, value in data.items():
        setattr(deal, key, value)

    summary = svc.recalculate_analysis(db, org_id, deal, user,
                                       recompute_arv_from_comps="arv" not in data)
    if deal.stage in ("qualified", "seller_engaged", "qualifying"):
        svc._set_stage_unchecked(db, deal, "analysis", actor_type=ACTOR_USER,
                                 actor_user_id=user.id)
    svc.log_event(db, org_id, "analysis.edited", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="Analysis assumptions edited", before=before,
                  after={k: _num(getattr(deal, k, None))
                         for k in ("arv", "repair_estimate", "proposed_offer")})
    db.commit()
    return summary


@router.post("/deals/{deal_id}/analysis/recalculate")
def recalculate(deal_id: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    summary = svc.recalculate_analysis(db, org_id, deal, user)
    db.commit()
    return summary


# ── Approvals ───────────────────────────────────────────────────────────────

class ApprovalRequestIn(BaseModel):
    kind: str
    amount: Optional[float] = None
    recommendation: Optional[str] = None
    reasoning: Optional[str] = None
    # Phase 5. Deliberately skipping a prerequisite, rather than accidentally.
    acknowledge_missing: bool = False


class ApprovalDecisionIn(BaseModel):
    approve: bool
    comments: Optional[str] = None


APPROVAL_KINDS = ("offer", "contract", "assignment", "stage")


@router.post("/deals/{deal_id}/approvals")
def request_approval(deal_id: str, payload: ApprovalRequestIn, request: Request,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user),
                     _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    if payload.kind not in APPROVAL_KINDS:
        raise HTTPException(status_code=400,
                            detail="kind must be one of: %s" % ", ".join(APPROVAL_KINDS))

    # Phase 5. Asking somebody to approve an assignment fee on a deal with no
    # buyer and no buyer price is asking them to approve a blank. The request
    # is refused with the gaps NAMED — and can still be made deliberately, by
    # acknowledging them, because a deal that ran out of order is still a deal.
    readiness = svc.approval_readiness(db, org_id, deal)
    gaps = readiness["kinds"].get(payload.kind, {}).get("missing") or []
    if (gaps and payload.kind in svc.APPROVAL_BLOCKING_KINDS
            and not payload.acknowledge_missing):
        raise HTTPException(
            status_code=409,
            detail=("This deal does not have %s yet, so there is nothing to "
                    "approve. Add it first, or send the request anyway and it "
                    "will be recorded as incomplete."
                    % ", ".join(g.lower() for g in gaps)))

    reasoning = payload.reasoning
    if gaps:
        # The gap goes ON the approval, so the person deciding it sees what was
        # missing when it was asked for rather than having to work it out.
        note = "Requested before: %s." % ", ".join(g.lower() for g in gaps)
        reasoning = ("%s\n%s" % (reasoning, note)).strip() if reasoning else note
    approval = svc.request_approval(
        db, org_id, deal, payload.kind, amount=payload.amount,
        recommendation=payload.recommendation, reasoning=reasoning, user=user)
    if payload.kind == "offer" and deal.stage in ("analysis", "qualified"):
        svc._set_stage_unchecked(db, deal, "offer_review", actor_type=ACTOR_USER,
                                 actor_user_id=user.id)
    db.commit()
    db.refresh(approval)
    return approval_json(approval)


@router.get("/approvals")
def list_approvals(db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer),
                   status_filter: Optional[str] = Query(None, alias="status"),
                   limit: int = Query(100, le=500)):
    """The Approval Center queue."""
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    query = db.query(WholesaleApproval).filter(
        WholesaleApproval.organization_id == org_id)
    if status_filter:
        query = query.filter(WholesaleApproval.status == status_filter)
    rows = query.order_by(WholesaleApproval.created_at.desc()).limit(limit).all()

    deals = {d.id: d for d in db.query(WholesaleDeal).filter(
        WholesaleDeal.id.in_([r.deal_id for r in rows] or [""])).all()}
    props = {p.id: p for p in db.query(WholesaleProperty).filter(
        WholesaleProperty.id.in_([d.property_id for d in deals.values()] or [""])).all()}

    out = []
    for a in rows:
        item = approval_json(a)
        deal = deals.get(a.deal_id)
        item["deal_id"] = a.deal_id
        item["deal_stage"] = getattr(deal, "stage", None)
        item["property_address"] = svc.address_line(
            props.get(getattr(deal, "property_id", None)))
        out.append(item)
    return {"approvals": out}


@router.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, payload: ApprovalDecisionIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    approval = (db.query(WholesaleApproval)
                .filter(WholesaleApproval.id == approval_id,
                        WholesaleApproval.organization_id == org_id).first())
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    svc.decide_approval(db, org_id, approval, payload.approve, user, payload.comments)
    db.commit()
    db.refresh(approval)
    return approval_json(approval)


# ── Contract, title, closing ────────────────────────────────────────────────

class ContractIn(BaseModel):
    contract_price: Optional[float] = None
    contract_status: Optional[str] = None
    inspection_deadline: Optional[str] = None
    close_of_escrow_target: Optional[str] = None
    # Phase 3. These columns existed on the model with no way to write them,
    # which is a field on a screen that silently never saves.
    contract_date: Optional[str] = None
    seller_signed_at: Optional[str] = None
    buyer_signed_at: Optional[str] = None
    effective_date: Optional[str] = None
    earnest_money: Optional[float] = None
    earnest_money_due: Optional[str] = None
    earnest_money_received_at: Optional[str] = None
    option_fee: Optional[float] = None
    closing_deadline: Optional[str] = None


@router.patch("/deals/{deal_id}/contract")
def update_contract(deal_id: str, payload: ContractIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    data = payload.model_dump(exclude_unset=True)
    # Closing locks the money. A price change after the fact is a correction and
    # goes through its own endpoint, with a reason and a before/after.
    if deal.economics_locked and "contract_price" in data:
        raise HTTPException(
            status_code=409,
            detail="This deal is closed and its economics are locked. Use the "
                   "economics correction, which records a reason.")
    for field in CONTRACT_DATE_FIELDS:
        if field in data:
            setattr(deal, field, _parse_date(data.pop(field)))
    for key, value in data.items():
        setattr(deal, key, value)
    if deal.contract_status == "signed" and deal.contract_signed_at is None:
        deal.contract_signed_at = datetime.utcnow()
    svc.log_event(db, org_id, "contract.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="Contract details updated",
                  after={"status": deal.contract_status,
                         "price": _num(deal.contract_price)})
    db.commit()
    return deal_json(deal, svc.resolve_settings(db, org_id))


# Every date on the contract, parsed the same way in one place rather than
# field by field, so adding one cannot quietly skip the parse and store a string.
CONTRACT_DATE_FIELDS = (
    "inspection_deadline", "close_of_escrow_target", "contract_date",
    "seller_signed_at", "buyer_signed_at", "effective_date",
    "earnest_money_due", "earnest_money_received_at", "closing_deadline",
)

TITLE_DATE_FIELDS = ("closing_date", "title_commitment_received_at")

# The Phase 3 vocabulary, plus the two Phase 1 spellings, exactly as DOC_TYPES
# keeps its old names: rows written before Phase 3 say `clear` and `issue`, and
# a validator that rejected them would refuse to save a deal whose only crime is
# being older than the list.
TITLE_STATUS_VALUES = TITLE_STATUSES + ("clear", "issue")


class TitleIn(BaseModel):
    title_company: Optional[str] = None
    title_contact: Optional[str] = None
    title_status: Optional[str] = None
    closing_date: Optional[str] = None
    # Phase 3 — what a title company actually asks for, and what a closing
    # actually needs. Same story: on the model, previously unwritable.
    title_escrow_officer: Optional[str] = None
    title_phone: Optional[str] = None
    title_email: Optional[str] = None
    title_commitment_received_at: Optional[str] = None
    title_issues: Optional[str] = None
    closing_time: Optional[str] = None
    closing_location: Optional[str] = None
    closing_status: Optional[str] = None
    # Phase 5. The escrow file number is the reference every phone call to the
    # title company starts with, and it was on no screen.
    title_file_number: Optional[str] = None
    # not_funded | funding_scheduled | funded. Typed, never inferred from a
    # date that has gone by.
    funding_status: Optional[str] = None


@router.patch("/deals/{deal_id}/title")
def update_title(deal_id: str, payload: TitleIn, request: Request,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    data = payload.model_dump(exclude_unset=True)
    # The vocabulary is checked rather than trusted: an unknown status here
    # would show as a raw key on every screen that reads it.
    for field, allowed in (("title_status", TITLE_STATUS_VALUES),
                           ("closing_status", TITLE_STATUS_VALUES)):
        value = data.get(field)
        if value and value not in allowed:
            raise HTTPException(
                status_code=400,
                detail="%s must be one of: %s." % (field, ", ".join(allowed)))
    for field in TITLE_DATE_FIELDS:
        if field in data:
            setattr(deal, field, _parse_date(data.pop(field)))
    for key, value in data.items():
        setattr(deal, key, value)
    if deal.title_status in ("opened", "clear", "title_search",
                             "clear_to_close") and deal.title_opened_at is None:
        deal.title_opened_at = datetime.utcnow()
    if deal.stage in ("assignment_pending", "buyer_identified"):
        svc._set_stage_unchecked(db, deal, "title_closing", actor_type=ACTOR_USER,
                                 actor_user_id=user.id)
    svc.log_event(db, org_id, "title.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="Title / closing updated",
                  after={"title_status": deal.title_status,
                         "closing_date": str(deal.closing_date or "")})
    db.commit()
    return deal_json(deal, svc.resolve_settings(db, org_id))


class CloseIn(BaseModel):
    # OPTIONAL SINCE PHASE 5. A deal closes on the day it closes; the wire
    # arrives when it arrives. Requiring a number here forced a guess, and a
    # guessed fee is indistinguishable in the database from a real one.
    wholesale_fee_collected: Optional[float] = None
    closing_date: Optional[str] = None
    deal_result: str = "closed_won"
    note: Optional[str] = None
    # Only meaningful when a fee is supplied at the same time.
    fee_payment_method: Optional[str] = None
    fee_payment_reference: Optional[str] = None


class FeeCollectedIn(BaseModel):
    """The money that actually landed. Typed by a person, after the fact."""
    amount: float
    collected_date: Optional[str] = None
    method: Optional[str] = None
    reference: Optional[str] = None
    note: Optional[str] = None


@router.post("/deals/{deal_id}/close")
def close_deal(deal_id: str, payload: CloseIn, request: Request,
               db: Session = Depends(get_db),
               user: User = Depends(require_tenant_user),
               _guard: User = Depends(require_not_observation)):
    """Record the closing and the fee. A PERSON does this, always.

    Nothing computes the fee that was actually collected. It is the one number
    in the module that is a fact about money that moved, and a platform that
    derived it would be reporting its own arithmetic as revenue.
    """
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    fee = analysis.money(payload.wholesale_fee_collected)
    if fee is not None:
        deal.wholesale_fee_collected = fee
        deal.fee_collected_at = datetime.utcnow()
        deal.fee_recorded_by_id = user.id
        deal.fee_payment_method = payload.fee_payment_method
        deal.fee_payment_reference = payload.fee_payment_reference
        deal.funding_status = "funded"
        deal.funded_at = deal.funded_at or datetime.utcnow()
    deal.deal_result = payload.deal_result
    deal.closed_at = datetime.utcnow()
    if payload.closing_date:
        deal.closing_date = _parse_date(payload.closing_date)
    # Phase 3: the economics stop being casually editable here. A closed deal
    # whose contract price can still be changed by a stray keystroke is a
    # revenue figure nobody can stand behind. Corrections go through
    # /economics-correction, which demands a reason and writes its own event.
    deal.economics_locked = True
    deal.closing_status = "closed"
    svc._set_stage_unchecked(db, deal, pipeline.STAGE_CLOSED,
                             actor_type=ACTOR_USER, actor_user_id=user.id)
    svc.log_event(db, org_id, "deal.closed", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary=("Closed — fee %s recorded" % float(fee)) if fee is not None
                          else "Closed — payment pending",
                  after={"fee": (float(fee) if fee is not None else None),
                         "result": payload.deal_result, "note": payload.note})
    db.commit()
    return deal_json(deal, svc.resolve_settings(db, org_id))


@router.post("/deals/{deal_id}/fee-collected")
def record_fee_collected(deal_id: str, payload: FeeCollectedIn, request: Request,
                         db: Session = Depends(get_db),
                         user: User = Depends(require_tenant_user),
                         _guard: User = Depends(require_not_observation)):
    """Record the assignment fee that actually landed.

    THIS IS THE ONLY PATH THAT SETS COLLECTED REVENUE, AND IT ALWAYS NAMES A
    PERSON. Nothing derives it, nothing infers it from a closing date, and the
    economics lock does not block it — locking the deal is what makes this the
    deliberate act it should be, not a reason to make the money unrecordable.

    A collected figure that differs from the expected one is kept as a
    difference rather than quietly replacing it, because the gap between the
    two is the only number anybody argues about after a closing.
    """
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    amount = analysis.money(payload.amount)
    if amount is None:
        raise HTTPException(status_code=400,
                            detail="Enter the amount that was collected.")

    expected = analysis.money(deal.assignment_fee)
    before = {"wholesale_fee_collected": _num(deal.wholesale_fee_collected)}

    deal.wholesale_fee_collected = amount
    deal.fee_collected_at = (_parse_datetime_date(payload.collected_date)
                             or datetime.utcnow())
    deal.fee_recorded_by_id = user.id
    deal.fee_payment_method = payload.method
    deal.fee_payment_reference = payload.reference
    deal.funding_status = "funded"
    deal.funded_at = deal.funded_at or deal.fee_collected_at
    if payload.note:
        deal.fee_variance_note = payload.note

    variance = (float(amount) - float(expected)) if expected is not None else None
    svc.log_event(db, org_id, "deal.fee_collected", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="Fee collected: %s" % float(amount),
                  before=before,
                  after={"wholesale_fee_collected": float(amount),
                         "expected": (float(expected) if expected is not None
                                      else None),
                         "variance": variance,
                         "method": payload.method,
                         "reference": payload.reference})
    db.commit()
    return deal_json(deal, svc.resolve_settings(db, org_id))


# ── Documents ───────────────────────────────────────────────────────────────

# Phase 3 widened this list to what a deal drawer actually holds. The Phase 1
# names are kept so existing rows keep resolving; the new ones are the ones a
# wholesaler asked for by name. THESE ARE FILING CATEGORIES, NOT LEGAL
# TEMPLATES — this module still ships no state-specific form and generates none.
DOC_TYPES = ("purchase_contract", "assignment_agreement", "seller_disclosure",
             "addendum", "inspection", "repair_estimate", "proof_of_funds",
             "title_document", "closing_statement", "buyer_doc",
             "property_photo", "other",
             # Retained Phase 1 spellings.
             "amendment", "disclosure", "title", "closing")


class DocumentIn(BaseModel):
    doc_type: str
    title: Optional[str] = None
    status: Optional[str] = None
    signature_status: Optional[str] = None
    signature_provider: Optional[str] = None
    external_ref: Optional[str] = None
    parties: Optional[List[Dict[str, Any]]] = None
    file_name: Optional[str] = None
    file_url: Optional[str] = None
    notes: Optional[str] = None
    # Phase 5 publication boundary. Both default to absent rather than False so
    # a PATCH that does not mention them cannot silently unpublish a document.
    # `update_document` setattr's whatever arrives, so nothing else changes.
    buyer_visible: Optional[bool] = None
    seller_visible: Optional[bool] = None


@router.post("/deals/{deal_id}/documents")
def add_document(deal_id: str, payload: DocumentIn, request: Request,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    """Create a document slot, or record one that already exists.

    THIS DOES NOT GENERATE A CONTRACT. No legal form is produced anywhere in
    this module — see the WholesaleDocument docstring. What this creates is the
    slot, its parties and its state, so the workflow can track a document
    somebody else drafted.
    """
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    if payload.doc_type not in DOC_TYPES:
        raise HTTPException(status_code=400,
                            detail="doc_type must be one of: %s" % ", ".join(DOC_TYPES))
    data = payload.model_dump(exclude_unset=True)
    doc = WholesaleDocument(
        organization_id=org_id, deal_id=deal.id, doc_type=data.pop("doc_type"),
        parties=json.dumps(data.pop("parties")) if data.get("parties") else None,
        **{k: v for k, v in data.items() if k != "parties"})
    if doc.file_name or doc.file_url:
        doc.status = doc.status or "uploaded"
        doc.uploaded_by_id = user.id
        doc.uploaded_at = datetime.utcnow()
    db.add(doc)
    db.flush()
    svc.log_event(db, org_id, "document.added", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="%s added" % doc.doc_type.replace("_", " "))
    db.commit()
    db.refresh(doc)
    return document_json(doc)


@router.patch("/documents/{document_id}")
def update_document(document_id: str, payload: DocumentIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    doc = (db.query(WholesaleDocument)
           .filter(WholesaleDocument.id == document_id,
                   WholesaleDocument.organization_id == org_id).first())
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    data = payload.model_dump(exclude_unset=True)
    before = document_json(doc)
    if "parties" in data:
        doc.parties = json.dumps(data.pop("parties")) if data["parties"] else None
    for key, value in data.items():
        setattr(doc, key, value)
    if doc.signature_status == "signed" and doc.executed_at is None:
        doc.executed_at = datetime.utcnow()
        doc.status = "executed"
    if (doc.file_name or doc.file_url) and doc.uploaded_at is None:
        doc.uploaded_by_id = user.id
        doc.uploaded_at = datetime.utcnow()
    svc.log_event(db, org_id, "document.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=doc.deal_id,
                  summary="%s updated" % doc.doc_type.replace("_", " "),
                  before=before, after=document_json(doc))
    db.commit()
    db.refresh(doc)
    return document_json(doc)


# ── Events ──────────────────────────────────────────────────────────────────

@router.get("/events")
def list_events(db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer),
                deal_id: Optional[str] = None,
                action: Optional[str] = None,
                limit: int = Query(200, le=1000)):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    query = db.query(WholesaleEvent).filter(WholesaleEvent.organization_id == org_id)
    if deal_id:
        query = query.filter(WholesaleEvent.deal_id == deal_id)
    if action:
        query = query.filter(WholesaleEvent.action == action)
    rows = query.order_by(WholesaleEvent.created_at.desc()).limit(limit).all()
    return {"events": [event_json(e) for e in rows]}


# ══════════════════════════════════════════════════════════════════════════
# PHASE 3 — the negotiation, the loss, and the correction
# ══════════════════════════════════════════════════════════════════════════

def offer_json(row) -> Dict[str, Any]:
    return {
        "id": row.id,
        "direction": row.direction,
        "amount": _num(row.amount),
        "status": row.status,
        "mao_at_time": _num(row.mao_at_time),
        "notes": row.notes,
        "approval_id": row.approval_id,
        "created_by_id": row.created_by_id,
        "created_by_actor": row.created_by_actor,
        "presented_at": row.presented_at.isoformat() if row.presented_at else None,
        "responded_at": row.responded_at.isoformat() if row.responded_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class OfferIn(BaseModel):
    amount: float
    direction: str = "us"          # "us" or "seller"
    notes: Optional[str] = None
    status: Optional[str] = None


@router.get("/deals/{deal_id}/offers")
def list_offers(deal_id: str, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    svc.get_deal(db, org_id, deal_id)
    rows = (db.query(WholesaleOffer)
            .filter(WholesaleOffer.organization_id == org_id,
                    WholesaleOffer.deal_id == deal_id)
            .order_by(WholesaleOffer.created_at.asc()).all())
    return {"offers": [offer_json(r) for r in rows]}


@router.post("/deals/{deal_id}/offers")
def record_offer(deal_id: str, payload: OfferIn, request: Request,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    """Write down one move in the negotiation.

    RECORDING IS NOT APPROVING, AND THIS ENDPOINT DOES NOT SEND ANYTHING.
    A number written here is history. The approval gate on `offer_sent` is
    untouched: a deal still cannot move to that stage without an approved
    offer, and the response says plainly whether this number is inside the
    maximum allowable offer in force right now.
    """
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    amount = analysis.money(payload.amount)
    if amount is None:
        raise HTTPException(status_code=400, detail="Enter an amount.")

    direction = (payload.direction or OFFER_FROM_US).strip().lower()
    if direction not in (OFFER_FROM_US, OFFER_FROM_SELLER):
        raise HTTPException(status_code=400,
                            detail="Direction must be 'us' or 'seller'.")

    mao = analysis.money(deal.max_allowable_offer)
    row = WholesaleOffer(
        organization_id=org_id, deal_id=deal_id, direction=direction,
        amount=amount, mao_at_time=mao, notes=payload.notes,
        created_by_id=user.id, created_by_actor=ACTOR_USER,
        status=(payload.status
                or ("countered" if direction == OFFER_FROM_SELLER else "draft")),
    )
    if direction == OFFER_FROM_SELLER:
        row.responded_at = datetime.utcnow()
    db.add(row)
    db.flush()

    # What we are asking for becomes the deal's current proposed number. What
    # the SELLER said does not — their counter is their position, not ours.
    if direction == OFFER_FROM_US:
        deal.proposed_offer = amount

    over_mao = bool(mao is not None and amount > mao)
    svc.log_event(
        db, org_id,
        "offer.recorded" if direction == OFFER_FROM_US else "offer.countered",
        actor_type=ACTOR_USER, actor_user_id=user.id, deal_id=deal_id,
        summary=("We offered %s" if direction == OFFER_FROM_US
                 else "Seller countered %s") % float(amount),
        after={"amount": float(amount), "mao": float(mao) if mao else None,
               "over_mao": over_mao})
    db.commit()
    return {
        "offer": offer_json(row),
        "over_mao": over_mao,
        # Said out loud rather than left for the screen to infer, because a
        # number above the MAO is exactly the one a person should look at twice.
        "note": ("This is above the maximum allowable offer of %s. It still "
                 "needs an approval before the deal can move to Offer Sent."
                 % _num(mao)) if over_mao else None,
    }


class OfferPatch(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


@router.patch("/offers/{offer_id}")
def update_offer(offer_id: str, payload: OfferPatch, request: Request,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    row = (db.query(WholesaleOffer)
           .filter(WholesaleOffer.id == offer_id,
                   WholesaleOffer.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Offer not found")
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"]:
        status_value = data["status"].strip().lower()
        if status_value not in OFFER_STATUSES:
            raise HTTPException(status_code=400,
                                detail="Unknown offer status %r." % status_value)
        row.status = status_value
        if status_value == "presented" and row.presented_at is None:
            row.presented_at = datetime.utcnow()
        if status_value in ("accepted", "rejected", "countered", "expired"):
            row.responded_at = datetime.utcnow()
    if "notes" in data:
        row.notes = data["notes"]
    svc.log_event(db, org_id, "offer.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=row.deal_id,
                  summary="Offer marked %s" % row.status)
    db.commit()
    return offer_json(row)


class LostIn(BaseModel):
    reason: str
    detail: Optional[str] = None


@router.post("/deals/{deal_id}/lost")
def mark_lost(deal_id: str, payload: LostIn, request: Request,
              db: Session = Depends(get_db),
              user: User = Depends(require_tenant_user),
              _guard: User = Depends(require_not_observation)):
    """Mark a deal dead, WITH a reason from a fixed list.

    Free text loses the ability to ask "how many did we lose on price", which
    is the question that changes what a wholesaler does next week. The stage
    move goes through the ordinary path, so the cadence stops exactly as it
    already does — this endpoint adds the reason, it does not add a second way
    to kill a deal.
    """
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    reason = (payload.reason or "").strip().lower()
    if reason not in LOST_REASONS:
        raise HTTPException(
            status_code=400,
            detail="Pick a reason from the list: %s." % ", ".join(LOST_REASONS))

    deal.lost_reason = reason
    deal.lost_reason_detail = payload.detail
    deal.deal_result = "closed_lost"
    svc._set_stage_unchecked(db, deal, pipeline.STAGE_DEAD,
                             actor_type=ACTOR_USER, actor_user_id=user.id)
    svc.log_event(db, org_id, "deal.lost", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="Marked lost: %s" % reason.replace("_", " "),
                  after={"reason": reason, "detail": payload.detail})
    db.commit()
    return deal_json(deal, svc.resolve_settings(db, org_id))


class CorrectionIn(BaseModel):
    reason: str
    contract_price: Optional[float] = None
    buyer_price: Optional[float] = None
    wholesale_fee_collected: Optional[float] = None
    other_costs: Optional[float] = None


@router.post("/deals/{deal_id}/economics-correction")
def correct_economics(deal_id: str, payload: CorrectionIn, request: Request,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user),
                      _guard: User = Depends(require_not_observation)):
    """The ONLY way to change the money on a closed deal.

    Closing locks the economics. That is not obstruction: a fee figure that can
    be altered by a stray keystroke is one nobody can stand behind at the end of
    a quarter. A genuine correction is a deliberate act with a reason attached
    and its own audit event carrying the before and after — which is what makes
    it defensible rather than invisible.
    """
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    reason = (payload.reason or "").strip()
    if len(reason) < 8:
        raise HTTPException(
            status_code=400,
            detail="Say what is being corrected and why, in a sentence.")

    before = {"contract_price": _num(deal.contract_price),
              "buyer_price": _num(deal.buyer_price),
              "assignment_fee": _num(deal.assignment_fee),
              "wholesale_fee_collected": _num(deal.wholesale_fee_collected),
              "other_costs": _num(deal.other_costs)}

    data = payload.model_dump(exclude_unset=True)
    for field in ("contract_price", "buyer_price", "wholesale_fee_collected",
                  "other_costs"):
        if field in data and data[field] is not None:
            setattr(deal, field, analysis.money(data[field]))

    # The assignment fee is arithmetic, not an opinion, so it follows.
    contract = analysis.money(deal.contract_price)
    buyer = analysis.money(deal.buyer_price)
    if contract is not None and buyer is not None:
        deal.assignment_fee = buyer - contract

    after = {"contract_price": _num(deal.contract_price),
             "buyer_price": _num(deal.buyer_price),
             "assignment_fee": _num(deal.assignment_fee),
             "wholesale_fee_collected": _num(deal.wholesale_fee_collected),
             "other_costs": _num(deal.other_costs)}

    svc.log_event(db, org_id, "economics.corrected", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="Economics corrected: %s" % reason[:160],
                  before=before, after=after)
    db.commit()
    return deal_json(deal, svc.resolve_settings(db, org_id))


@router.get("/operating-board")
def operating_board(db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer),
                    include_test: bool = False,
                    limit: int = Query(12, le=50)):
    """The Command Center's working half: what to do today.

    Separate from /dashboard on purpose. That endpoint answers "how big is
    this operation"; this one answers "what is waiting on me", and they are
    read by different parts of the same screen. Keeping them apart means the
    heavy counting query does not have to run every time somebody dismisses a
    row, and the two can be cached differently later without a rewrite.
    """
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409,
                            detail="No customer organization selected.")
    board = svc.operating_board(db, org_id, include_test=include_test, limit=limit)
    board["pipeline"] = svc.pipeline_value_by_stage(db, org_id,
                                                    include_test=include_test)
    board["recent_activity"] = svc.recent_activity(db, org_id)
    board["include_test"] = include_test
    board["as_of"] = datetime.utcnow().isoformat()
    return board


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    """Delete a document row, and the stored file with it.

    The screen confirms first and names what it is deleting. Both go, because
    a drawer that keeps orphaned bytes after the record is gone is a drawer
    nobody can answer a data request about.
    """
    org_id = svc.write_org_id(db, user)
    doc = (db.query(WholesaleDocument)
           .filter(WholesaleDocument.id == document_id,
                   WholesaleDocument.organization_id == org_id).first())
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    label = doc.title or doc.doc_type
    stored = None
    if doc.file_id:
        stored = (db.query(WholesaleFile)
                  .filter(WholesaleFile.id == doc.file_id,
                          WholesaleFile.organization_id == org_id).first())

    deal_id = doc.deal_id
    db.delete(doc)
    if stored is not None:
        backend, key = stored.storage_backend, stored.storage_key
        db.delete(stored)
    svc.log_event(db, org_id, "document.deleted", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal_id,
                  summary="Document deleted: %s" % label)
    db.commit()

    if stored is not None:
        from app.services import wholesale_files
        wholesale_files.remove(backend, key)
    return {"deleted": True, "id": document_id}
