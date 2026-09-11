"""CUSTOM COMMERCIAL AGREEMENTS — the HTTP surface.

THE ACCESS RULE, STATED ONCE, AND IT IS THE LAUNCH ENGINE'S
===========================================================
A CUSTOMER ROUTE NEVER TAKES AN ORGANIZATION ID. Not in the path, not in the
body, not in a query string. The org comes from the authenticated session via
`lead_scope.active_workspace_org_id`, exactly as `/launch/me` resolves it.

That rule matters more here than anywhere else in the platform, because what
is behind these routes is the commercial terms of a deal — percentages, fees,
who gets paid what. An `/commercial/{org_id}` route would be a contract-terms
enumeration endpoint with a UUID for a lock.

A STAFF ROUTE takes ids and is scoped twice: the actor's platform scope decides
which organizations and agreements EXIST for them (404, never 403, so the route
cannot be used to discover ids), and `services.commercial.authority` decides
what they may then DO. Reading is not approving; approving is not activating.

WHAT THESE ROUTES DO NOT DO
===========================
No route here charges anybody, invoices anybody, provisions anything, activates
an AI employee or sends a message to a customer. The one route that sounds like
it moves money — `settlement/distribute` — exists to refuse and to leave a
record that somebody asked.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.commercial_models import (
    AGREEMENT_STATUS_LABELS, AGREEMENT_STATUSES, AGREEMENT_TYPE_LABELS,
    AGREEMENT_TYPES, ATTRIBUTION_RULES, COLLECTION_SOURCES,
    CommercialAgreement, IMPLEMENTED_COLLECTION_SOURCES, OVERRIDE_ITEM_KINDS,
    OVERRIDE_MODE_LABELS, OVERRIDE_MODES, PARTY_TYPES, SHARE_BEARING_TYPES,
)
from app.models.implementation_models import Implementation
from app.models.models import AuditLogEntry, Organization, User
from app.models.sales_models import BrandSalesOrg, Opportunity
from app.services.commercial import agreements as ag
from app.services.commercial import authority as auth
from app.services.commercial import onboarding as flow
from app.services.commercial import overrides as ov
from app.services.commercial import questions as q
from app.services.commercial import settlement as settle
from app.services.commercial import t2_link
from app.services.commercial import terms as t
from app.services.sales_access import is_god, sales_memberships

log = logging.getLogger("commercial_router")

router = APIRouter(prefix="/commercial", tags=["Commercial Agreements"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                           detail="Agreement not found.")


# ── scope ───────────────────────────────────────────────────────────────────

def _actor_platform_ids(db: Session, actor: User) -> set:
    """Platforms this staff actor may see. Empty set for a customer user.

    A brand-sales identity has no organization; its reach comes from the brand
    sales orgs it is a member of, each of which names a platform. A platform
    admin carries `platform_id` directly.
    """
    ids = set()
    pid = getattr(actor, "platform_id", None)
    if pid:
        ids.add(pid)
    scope_ids = [m.scope_id for m in sales_memberships(actor, db)]
    if scope_ids:
        rows = (db.query(BrandSalesOrg)
                .filter(BrandSalesOrg.id.in_(scope_ids)).all())
        ids.update(r.platform_id for r in rows if r.platform_id)
    return ids


def _staff_agreement(db: Session, actor: User, agreement_id: str) -> CommercialAgreement:
    """Load an agreement the actor may SEE, else 404.

    Existence is scoped before authority is consulted, so a manager of another
    brand cannot distinguish "not yours" from "does not exist".
    """
    row = (db.query(CommercialAgreement)
           .filter(CommercialAgreement.id == agreement_id).first())
    if row is None:
        raise _NOT_FOUND
    if is_god(actor):
        return row
    if row.platform_id and row.platform_id in _actor_platform_ids(db, actor):
        return row
    raise _NOT_FOUND


def _staff_org(db: Session, actor: User, organization_id: str) -> Organization:
    org = (db.query(Organization)
           .filter(Organization.id == organization_id).first())
    if org is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    if is_god(actor):
        return org
    if org.platform_id and org.platform_id in _actor_platform_ids(db, actor):
        return org
    raise HTTPException(status_code=404, detail="Customer not found.")


def _staff_impl(db: Session, actor: User, organization_id: str) -> Implementation:
    org = _staff_org(db, actor, organization_id)
    impl = (db.query(Implementation)
            .filter(Implementation.organization_id == org.id).first())
    if impl is None:
        raise HTTPException(
            status_code=404,
            detail="This customer has no onboarding record yet.")
    return impl


def _require_view(db: Session, actor: User, agreement: CommercialAgreement) -> None:
    auth.assert_can_on(db, actor, auth.CAP_VIEW_INTERNAL, agreement)


# ── customer-session resolution ─────────────────────────────────────────────

def _caller_org_id(user: User, db: Session, request: Request) -> str:
    from app.services.lead_scope import active_workspace_org_id
    org_id = active_workspace_org_id(user, db, request)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No customer workspace is selected for this session.")
    return org_id


# ════════════════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════════════════


@router.get("/config")
def config(_user: User = Depends(get_current_user)) -> dict:
    """The vocabulary, so no screen hard-codes a list of commercial models."""
    return {
        "agreement_types": [{"value": v, "label": AGREEMENT_TYPE_LABELS[v],
                             "share_bearing": v in SHARE_BEARING_TYPES}
                            for v in AGREEMENT_TYPES],
        "agreement_statuses": [{"value": v, "label": AGREEMENT_STATUS_LABELS[v]}
                               for v in AGREEMENT_STATUSES],
        "party_types": list(PARTY_TYPES),
        "collection_sources": [
            {"value": v, "implemented": v in IMPLEMENTED_COLLECTION_SOURCES}
            for v in COLLECTION_SOURCES],
        "attribution_rules": list(ATTRIBUTION_RULES),
        "override_modes": [{"value": v, "label": OVERRIDE_MODE_LABELS[v]}
                           for v in OVERRIDE_MODES],
        "override_item_kinds": list(OVERRIDE_ITEM_KINDS),
        "onboarding_steps": [
            {"key": s["key"], "n": s["n"], "label": s["label"],
             "description": s["description"],
             "customer_visible": s["customer_visible"]}
            for s in flow.STEP_SCHEMA],
        "step_statuses": [{"value": v, "label": flow.STATUS_LABELS[v]}
                          for v in flow.STEP_STATUSES],
        "blockable_actions": list(t.BLOCKABLE_ACTIONS),
        "payout_execution_supported": settle.PAYOUT_EXECUTION_SUPPORTED,
    }


# ════════════════════════════════════════════════════════════════════════════
# CUSTOMER SURFACE  — no org id, ever
# ════════════════════════════════════════════════════════════════════════════


class CustomerAnswer(BaseModel):
    value: Any = None
    note: Optional[str] = None
    revision: Optional[int] = None


@router.get("/me")
def my_commercial(request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> dict:
    """What the customer sees: their arrangement, in their own language."""
    org_id = _caller_org_id(user, db, request)
    agreement = ag.current_for_organization(db, org_id)
    if agreement is None:
        return {
            "has_agreement": False,
            "headline": None,
            "status_line": "Your commercial arrangement has not been set up "
                           "here yet. Your account manager will confirm it.",
            "questions": [],
        }
    return {"has_agreement": True, **ag.customer_view(db, agreement)}


@router.put("/me/questions/{key}")
def answer_my_question(key: str, body: CustomerAnswer, request: Request,
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> dict:
    """The customer answers one of their own business questions.

    Three refusals live in this one handler, and each is load-bearing:

      the question must be one the CUSTOMER is asked — an internal question
      (a percentage, an attribution rule) is not answerable here even by an
      authenticated customer admin;
      the caller must be an administrator of THIS organization;
      the answer must survive validation, or nothing is written.
    """
    org_id = _caller_org_id(user, db, request)
    agreement = ag.current_for_organization(db, org_id)
    if agreement is None:
        raise HTTPException(status_code=404,
                            detail="There is no agreement to answer questions on.")

    auth.assert_can(db, user, auth.CAP_ANSWER_CUSTOMER_Q,
                    brand_sales_org_id=agreement.brand_sales_org_id,
                    organization_id=org_id)

    defn = q.definition(db, agreement.platform_id, key)
    if defn is None:
        raise HTTPException(status_code=404,
                            detail="No question named '%s'." % key)
    if defn["audience"] == q.AUDIENCE_INTERNAL:
        # 404 rather than 403: the customer has no business knowing which
        # internal questions exist about their own deal.
        raise HTTPException(status_code=404,
                            detail="No question named '%s'." % key)

    t.set_term(db, agreement, user, key, body.value,
               source="customer_onboarding", note=body.note,
               expected_revision=body.revision)
    ag.refresh_status(db, agreement, user)
    db.commit()
    return ag.customer_view(db, agreement)


@router.get("/me/onboarding")
def my_onboarding(request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> dict:
    """The customer's own progress. Resumable by construction: it is computed
    from stored rows every time, so leaving and coming back loses nothing."""
    org_id = _caller_org_id(user, db, request)
    impl = (db.query(Implementation)
            .filter(Implementation.organization_id == org_id).first())
    if impl is None:
        raise HTTPException(
            status_code=404,
            detail="Your onboarding has not been set up yet.")

    prog = flow.progress(db, impl)
    visible = [s for s in prog["steps"] if s["customer_visible"]]
    settled = [s for s in visible if s["settled"]]
    return {
        "implementation_status": prog["implementation_status"],
        "steps": [{k: v for k, v in s.items()
                   if k not in ("customer_visible",)} for s in visible],
        "step_total": len(visible),
        "step_settled": len(settled),
        "overall_pct": (int(round(100 * len(settled) / len(visible)))
                        if visible else 0),
    }


# ════════════════════════════════════════════════════════════════════════════
# STAFF — AGREEMENTS
# ════════════════════════════════════════════════════════════════════════════


class AgreementNew(BaseModel):
    platform_id: str
    agreement_type: str
    brand_sales_org_id: Optional[str] = None
    organization_id: Optional[str] = None
    opportunity_id: Optional[str] = None
    implementation_id: Optional[str] = None
    name: Optional[str] = None
    reference: Optional[str] = None
    currency: str = "usd"
    effective_date: Optional[date] = None
    notes: Optional[str] = None
    references_t2_subscription: bool = False
    t2_note: Optional[str] = None
    as_draft: bool = False


class AgreementPatch(BaseModel):
    name: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    currency: Optional[str] = None
    effective_date: Optional[date] = None
    end_date: Optional[date] = None
    references_t2_subscription: Optional[bool] = None
    t2_note: Optional[str] = None
    document_reference: Optional[str] = None
    document_note: Optional[str] = None
    expected_version: Optional[int] = None


class TypeBody(BaseModel):
    agreement_type: str


class BindBody(BaseModel):
    organization_id: str
    implementation_id: Optional[str] = None


class DecisionBody(BaseModel):
    note: Optional[str] = None


class ReasonBody(BaseModel):
    reason: str
    end_date: Optional[date] = None


class PartyNew(BaseModel):
    party_key: str
    display_name: str
    party_type: str
    organization_id: Optional[str] = None
    brand_sales_org_id: Optional[str] = None
    external_reference: Optional[str] = None
    is_payee: bool = True
    note: Optional[str] = None


class PartyPatch(BaseModel):
    display_name: Optional[str] = None
    party_type: Optional[str] = None
    organization_id: Optional[str] = None
    brand_sales_org_id: Optional[str] = None
    external_reference: Optional[str] = None
    is_payee: Optional[bool] = None
    note: Optional[str] = None


class AllocationBody(BaseModel):
    percent: Optional[float] = None
    fixed_amount_cents: Optional[int] = None
    tier_json: Optional[Any] = None
    note: Optional[str] = None
    clear_percent: bool = False


class TermBody(BaseModel):
    value: Any = None
    note: Optional[str] = None
    not_applicable: bool = False
    revision: Optional[int] = None


class DocumentBody(BaseModel):
    file_id: Optional[str] = None
    reference: Optional[str] = None
    note: Optional[str] = None


@router.get("/agreements")
def list_agreements(organization_id: Optional[str] = Query(None),
                    opportunity_id: Optional[str] = Query(None),
                    platform_id: Optional[str] = Query(None),
                    limit: int = Query(50, ge=1, le=200),
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> dict:
    scope = _actor_platform_ids(db, user)
    if not is_god(user) and not scope:
        return {"agreements": [], "count": 0}

    query = db.query(CommercialAgreement)
    if not is_god(user):
        query = query.filter(CommercialAgreement.platform_id.in_(sorted(scope)))
    if platform_id:
        query = query.filter(CommercialAgreement.platform_id == platform_id)
    if organization_id:
        query = query.filter(CommercialAgreement.organization_id == organization_id)
    if opportunity_id:
        query = query.filter(CommercialAgreement.opportunity_id == opportunity_id)

    rows = (query.order_by(CommercialAgreement.created_at.desc())
            .limit(limit).all())
    out = []
    for r in rows:
        if not auth.can_on(db, user, auth.CAP_VIEW_INTERNAL, r):
            continue
        comp = t.completeness(db, r)
        out.append({
            "id": r.id,
            "agreement_type": r.agreement_type,
            "agreement_type_label": AGREEMENT_TYPE_LABELS.get(r.agreement_type,
                                                              r.agreement_type),
            "status": r.status,
            "status_label": AGREEMENT_STATUS_LABELS.get(r.status, r.status),
            "name": r.name,
            "organization_id": r.organization_id,
            "opportunity_id": r.opportunity_id,
            "platform_id": r.platform_id,
            "effective_date": r.effective_date,
            "currency": r.currency,
            "required_missing": comp["required_missing"],
            "terms_complete": comp["terms_complete"],
            "created_at": r.created_at,
        })
    return {"agreements": out, "count": len(out)}


@router.post("/agreements", status_code=status.HTTP_201_CREATED)
def create_agreement(body: AgreementNew,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> dict:
    """Create an arrangement whose terms may be entirely unknown.

    The customer exists, onboarding starts, and the terms are chased through
    the questions. Nothing about this call requires a price.
    """
    if not is_god(user) and body.platform_id not in _actor_platform_ids(db, user):
        raise HTTPException(status_code=404, detail="Brand not found.")
    auth.assert_can(db, user, auth.CAP_CREATE,
                    brand_sales_org_id=body.brand_sales_org_id,
                    organization_id=body.organization_id)

    if body.organization_id:
        _staff_org(db, user, body.organization_id)

    row = ag.create(db, user,
                    platform_id=body.platform_id,
                    agreement_type=body.agreement_type,
                    brand_sales_org_id=body.brand_sales_org_id,
                    organization_id=body.organization_id,
                    opportunity_id=body.opportunity_id,
                    implementation_id=body.implementation_id,
                    name=body.name, reference=body.reference,
                    currency=body.currency,
                    effective_date=body.effective_date,
                    notes=body.notes,
                    references_t2_subscription=body.references_t2_subscription,
                    t2_note=body.t2_note,
                    as_draft=body.as_draft)
    db.commit()
    return ag.internal_view(db, row)


@router.get("/agreements/{agreement_id}")
def read_agreement(agreement_id: str,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    _require_view(db, user, row)
    view = ag.internal_view(db, row)
    view["t2"] = t2_link.state(db, row.organization_id)
    view["capabilities"] = sorted(auth.capabilities(
        db, user, brand_sales_org_id=row.brand_sales_org_id,
        organization_id=row.organization_id))
    return view


@router.get("/agreements/{agreement_id}/questions")
def agreement_questions(agreement_id: str,
                        audience: Optional[str] = Query(None),
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    _require_view(db, user, row)
    return {"questions": t.ordered_terms(db, row, audience=audience)}


@router.patch("/agreements/{agreement_id}")
def patch_agreement(agreement_id: str, body: AgreementPatch,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_EDIT_TERMS, row)
    fields = body.model_dump(exclude_unset=True)
    expected = fields.pop("expected_version", None)
    ag.update(db, row, user, expected_version=expected, **fields)
    db.commit()
    return ag.internal_view(db, row)


@router.post("/agreements/{agreement_id}/type")
def change_type(agreement_id: str, body: TypeBody,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_EDIT_ECONOMICS, row)
    ag.set_type(db, row, user, body.agreement_type)
    db.commit()
    return ag.internal_view(db, row)


@router.post("/agreements/{agreement_id}/bind")
def bind_agreement(agreement_id: str, body: BindBody,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_EDIT_TERMS, row)
    _staff_org(db, user, body.organization_id)
    ag.bind_to_customer(db, row, user,
                        organization_id=body.organization_id,
                        implementation_id=body.implementation_id)
    db.commit()
    return ag.internal_view(db, row)


# ── terms ───────────────────────────────────────────────────────────────────


@router.put("/agreements/{agreement_id}/terms/{key}")
def set_term(agreement_id: str, key: str, body: TermBody,
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)) -> dict:
    """Answer an internal term.

    An answer that changes the economics — a fee, a quoted figure — needs the
    economics capability; the four business questions a customer is also asked
    need only the terms capability, which a rep has.
    """
    row = _staff_agreement(db, user, agreement_id)
    defn = q.definition(db, row.platform_id, key)
    if defn is None:
        raise HTTPException(status_code=404,
                            detail="No question named '%s'." % key)
    needed = (auth.CAP_EDIT_ECONOMICS if defn["kind"] in (q.KIND_MONEY, q.KIND_PERCENT)
              else auth.CAP_EDIT_TERMS)
    auth.assert_can_on(db, user, needed, row)

    result = t.set_term(db, row, user, key, body.value,
                        source="internal", note=body.note,
                        not_applicable=body.not_applicable,
                        expected_revision=body.revision)
    ag.refresh_status(db, row, user)
    db.commit()
    return {"term": result, "agreement": ag.internal_view(db, row)}


# ── parties and allocation ──────────────────────────────────────────────────


@router.post("/agreements/{agreement_id}/parties",
             status_code=status.HTTP_201_CREATED)
def add_party(agreement_id: str, body: PartyNew,
              db: Session = Depends(get_db),
              user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_EDIT_ECONOMICS, row)
    ag.add_party(db, row, user, party_key=body.party_key,
                 display_name=body.display_name, party_type=body.party_type,
                 organization_id=body.organization_id,
                 brand_sales_org_id=body.brand_sales_org_id,
                 external_reference=body.external_reference,
                 is_payee=body.is_payee, note=body.note)
    db.commit()
    return ag.internal_view(db, row)


@router.patch("/agreements/{agreement_id}/parties/{party_id}")
def patch_party(agreement_id: str, party_id: str, body: PartyPatch,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_EDIT_ECONOMICS, row)
    ag.update_party(db, row, user, party_id, **body.model_dump(exclude_unset=True))
    db.commit()
    return ag.internal_view(db, row)


@router.delete("/agreements/{agreement_id}/parties/{party_id}")
def delete_party(agreement_id: str, party_id: str,
                 db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_EDIT_ECONOMICS, row)
    ag.remove_party(db, row, user, party_id)
    db.commit()
    return ag.internal_view(db, row)


@router.put("/agreements/{agreement_id}/parties/{party_id}/allocation")
def set_allocation(agreement_id: str, party_id: str, body: AllocationBody,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_EDIT_ECONOMICS, row)
    ag.set_allocation(db, row, user, party_id,
                      percent=body.percent,
                      fixed_amount_cents=body.fixed_amount_cents,
                      tier_json=body.tier_json, note=body.note,
                      clear_percent=body.clear_percent)
    db.commit()
    return ag.internal_view(db, row)


@router.post("/agreements/{agreement_id}/document")
def link_document(agreement_id: str, body: DocumentBody,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> dict:
    """Point at the paper. It approves nothing — see the service docstring."""
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_EDIT_TERMS, row)
    ag.link_document(db, row, user, file_id=body.file_id,
                     reference=body.reference, note=body.note)
    db.commit()
    return ag.internal_view(db, row)


# ── the three human decisions ───────────────────────────────────────────────


@router.post("/agreements/{agreement_id}/approve")
def approve(agreement_id: str, body: DecisionBody,
            db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_APPROVE, row)
    ag.approve(db, row, user, note=body.note)
    db.commit()
    return ag.internal_view(db, row)


@router.post("/agreements/{agreement_id}/activate")
def activate(agreement_id: str, body: DecisionBody,
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_ACTIVATE, row)
    ag.activate(db, row, user, note=body.note)
    db.commit()
    return ag.internal_view(db, row)


@router.post("/agreements/{agreement_id}/suspend")
def suspend(agreement_id: str, body: ReasonBody,
            db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_SUSPEND, row)
    ag.suspend(db, row, user, body.reason)
    db.commit()
    return ag.internal_view(db, row)


@router.post("/agreements/{agreement_id}/resume")
def resume(agreement_id: str, body: DecisionBody,
           db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_SUSPEND, row)
    ag.resume(db, row, user, note=body.note)
    db.commit()
    return ag.internal_view(db, row)


@router.post("/agreements/{agreement_id}/end")
def end(agreement_id: str, body: ReasonBody,
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_END, row)
    ag.end(db, row, user, body.reason, end_date=body.end_date)
    db.commit()
    return ag.internal_view(db, row)


# ── audit ───────────────────────────────────────────────────────────────────

_COMMERCIAL_TARGETS = ("commercial_agreement", "commercial_term",
                       "commercial_party", "commercial_allocation",
                       "commercial_collection_record", "commercial_settlement",
                       "onboarding_milestone_override")


@router.get("/agreements/{agreement_id}/audit")
def agreement_audit(agreement_id: str,
                    limit: int = Query(100, ge=1, le=500),
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> dict:
    """Everything that has happened to this agreement, from the platform's own
    audit log. No second activity store."""
    row = _staff_agreement(db, user, agreement_id)
    _require_view(db, user, row)

    child_ids = {row.id}

    from app.models.commercial_models import (
        CommercialAllocation, CommercialCollectionRecord, CommercialParty,
        CommercialSettlement, CommercialTerm,
    )
    for model in (CommercialTerm, CommercialParty, CommercialAllocation,
                  CommercialCollectionRecord, CommercialSettlement):
        child_ids.update(r[0] for r in
                         db.query(model.id)
                         .filter(model.agreement_id == row.id).all())

    if row.implementation_id or row.organization_id:
        from app.models.commercial_models import OnboardingMilestoneOverride
        oq = db.query(OnboardingMilestoneOverride.id)
        if row.implementation_id:
            oq = oq.filter(
                OnboardingMilestoneOverride.implementation_id == row.implementation_id)
        else:
            oq = oq.filter(
                OnboardingMilestoneOverride.organization_id == row.organization_id)
        child_ids.update(r[0] for r in oq.all())

    rows = (db.query(AuditLogEntry)
            .filter(AuditLogEntry.target_type.in_(_COMMERCIAL_TARGETS),
                    AuditLogEntry.target_id.in_(sorted(child_ids)))
            .order_by(AuditLogEntry.created_at.desc())
            .limit(limit).all())

    actors = {}
    for r in rows:
        if r.actor_user_id and r.actor_user_id not in actors:
            u = db.query(User).filter(User.id == r.actor_user_id).first()
            actors[r.actor_user_id] = (getattr(u, "full_name", None)
                                       or getattr(u, "email", None))
    return {"entries": [{
        "id": r.id, "action": r.action, "target_type": r.target_type,
        "target_id": r.target_id, "actor_user_id": r.actor_user_id,
        "actor_name": actors.get(r.actor_user_id),
        "before": r.before_state, "after": r.after_state,
        "details": r.details, "note": r.note, "created_at": r.created_at,
    } for r in rows], "count": len(rows)}


# ════════════════════════════════════════════════════════════════════════════
# COLLECTIONS AND SETTLEMENT
# ════════════════════════════════════════════════════════════════════════════


class CollectionNew(BaseModel):
    period_start: date
    period_end: date
    source: str
    gross_cents: int
    adjustments_cents: Optional[int] = None
    adjustments_note: Optional[str] = None
    attribution_state: str = "unknown_review_required"
    source_reference: Optional[str] = None
    note: Optional[str] = None
    submit: bool = True


class StripeImportBody(BaseModel):
    period_start: date
    period_end: date
    attribution_state: str = "unknown_review_required"
    note: Optional[str] = None


class CollectionDecision(BaseModel):
    attribution_state: Optional[str] = None
    note: Optional[str] = None


class PeriodBody(BaseModel):
    period_start: date
    period_end: date
    persist: bool = True


def _collection_public(r) -> dict:
    return {
        "id": r.id, "period_start": r.period_start, "period_end": r.period_end,
        "source": r.source, "source_reference": r.source_reference,
        "currency": r.currency, "gross_cents": r.gross_cents,
        "adjustments_cents": r.adjustments_cents,
        "adjustments_known": r.adjustments_cents is not None,
        "adjustments_note": r.adjustments_note,
        "attribution_state": r.attribution_state,
        "status": r.status, "note": r.note,
        "approved_by_user_id": r.approved_by_user_id,
        "approved_at": r.approved_at,
        "rejected_reason": r.rejected_reason,
        "created_at": r.created_at,
    }


@router.get("/agreements/{agreement_id}/collections")
def list_collections(agreement_id: str,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    _require_view(db, user, row)
    rows = settle.records(db, row)
    return {"records": [_collection_public(r) for r in rows],
            "count": len(rows)}


@router.post("/agreements/{agreement_id}/collections",
             status_code=status.HTTP_201_CREATED)
def add_collection(agreement_id: str, body: CollectionNew,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_RECORD_COLLECTIONS, row)
    rec = settle.record_collection(
        db, row, user,
        period_start=body.period_start, period_end=body.period_end,
        source=body.source, gross_cents=body.gross_cents,
        adjustments_cents=body.adjustments_cents,
        adjustments_note=body.adjustments_note,
        attribution_state=body.attribution_state,
        source_reference=body.source_reference,
        note=body.note, submit=body.submit)
    db.commit()
    return _collection_public(rec)


@router.post("/agreements/{agreement_id}/collections/from-stripe",
             status_code=status.HTTP_201_CREATED)
def import_stripe_collections(agreement_id: str, body: StripeImportBody,
                              db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)) -> dict:
    """Read payments T2 already recorded. Creates a SUBMITTED record, not an
    approved one — a machine summing a table is not a person accepting it."""
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_RECORD_COLLECTIONS, row)
    rec = settle.import_from_stripe(
        db, row, user, period_start=body.period_start,
        period_end=body.period_end,
        attribution_state=body.attribution_state, note=body.note)
    db.commit()
    return _collection_public(rec)


@router.post("/agreements/{agreement_id}/collections/{record_id}/approve")
def approve_collection(agreement_id: str, record_id: str,
                       body: CollectionDecision,
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_APPROVE_COLLECTIONS, row)
    rec = settle.approve_collection(db, row, user, record_id,
                                    attribution_state=body.attribution_state,
                                    note=body.note)
    db.commit()
    return _collection_public(rec)


@router.post("/agreements/{agreement_id}/collections/{record_id}/reject")
def reject_collection(agreement_id: str, record_id: str, body: ReasonBody,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_APPROVE_COLLECTIONS, row)
    rec = settle.reject_collection(db, row, user, record_id, body.reason)
    db.commit()
    return _collection_public(rec)


@router.get("/agreements/{agreement_id}/settlement/readiness")
def settlement_readiness(agreement_id: str,
                         period_start: Optional[date] = Query(None),
                         period_end: Optional[date] = Query(None),
                         db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    _require_view(db, user, row)
    return settle.readiness(db, row, period_start, period_end)


@router.post("/agreements/{agreement_id}/settlement/preview")
def settlement_preview(agreement_id: str, body: PeriodBody,
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> dict:
    """Calculate what each party WOULD be owed. Never a payout."""
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_CALCULATE_SETTLEMENT, row)
    result = settle.preview(db, row, user,
                            period_start=body.period_start,
                            period_end=body.period_end,
                            persist=body.persist)
    db.commit()
    return result


@router.post("/agreements/{agreement_id}/settlement/{settlement_id}/approve")
def approve_settlement(agreement_id: str, settlement_id: str,
                       body: DecisionBody,
                       db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)) -> dict:
    row = _staff_agreement(db, user, agreement_id)
    auth.assert_can_on(db, user, auth.CAP_APPROVE_SETTLEMENT, row)
    s = settle.approve_settlement(db, row, user, settlement_id, note=body.note)
    db.commit()
    return {"id": s.id, "status": s.status, "basis_cents": s.basis_cents,
            "approved_at": s.approved_at, "moves_money": False}


@router.post("/agreements/{agreement_id}/settlement/{settlement_id}/distribute")
def distribute(agreement_id: str, settlement_id: str,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> dict:
    """Refuses. Every time. And records that somebody asked.

    Gated on READING the agreement rather than on settlement authority, and
    that is deliberate. A 403 here would say "you personally may not move this
    money", which invites the next question — who may? Nobody may. Everyone
    who can see the agreement gets the same 409 and the same sentence, and the
    attempt is audited whoever made it.
    """
    row = _staff_agreement(db, user, agreement_id)
    _require_view(db, user, row)
    settle.distribute(db, row, user, settlement_id)
    db.commit()                                    # pragma: no cover
    return {}                                      # pragma: no cover


# ════════════════════════════════════════════════════════════════════════════
# ONBOARDING — staff view and overrides
# ════════════════════════════════════════════════════════════════════════════


class OverrideBody(BaseModel):
    item_kind: str
    item_key: str
    mode: str
    reason: str
    note: Optional[str] = None
    item_label: Optional[str] = None
    previously_completed_on: Optional[date] = None
    previously_completed_date_known: bool = False


@router.get("/onboarding/{organization_id}")
def staff_onboarding(organization_id: str,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> dict:
    impl = _staff_impl(db, user, organization_id)
    prog = flow.progress(db, impl)
    agreement = ag.current_for_organization(db, organization_id)
    prog["agreement"] = (ag.internal_view(db, agreement)
                         if agreement is not None
                         and auth.can_on(db, user, auth.CAP_VIEW_INTERNAL, agreement)
                         else None)
    prog["overrides"] = [ov.public(r) for r in ov.list_for(db, impl.id)]
    prog["capabilities"] = sorted(auth.capabilities(
        db, user,
        brand_sales_org_id=impl.brand_sales_org_id,
        organization_id=organization_id))
    return prog


@router.get("/onboarding/{organization_id}/overrides")
def list_overrides(organization_id: str,
                   include_superseded: bool = Query(False),
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    impl = _staff_impl(db, user, organization_id)
    rows = ov.list_for(db, impl.id, active_only=not include_superseded)
    return {"overrides": [ov.public(r) for r in rows], "count": len(rows)}


@router.post("/onboarding/{organization_id}/overrides",
             status_code=status.HTTP_201_CREATED)
def create_override(organization_id: str, body: OverrideBody,
                    db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)) -> dict:
    """Mark a step satisfied other than by doing it now.

    Every one of these carries a named decider, the moment of the decision, and
    a reason — and a prior completion date only where somebody actually knows
    it.
    """
    impl = _staff_impl(db, user, organization_id)
    auth.assert_can(db, user, auth.CAP_OVERRIDE_MILESTONE,
                    brand_sales_org_id=impl.brand_sales_org_id,
                    organization_id=organization_id)
    row = ov.apply(db, impl, user,
                   item_kind=body.item_kind, item_key=body.item_key,
                   mode=body.mode, reason=body.reason, note=body.note,
                   item_label=body.item_label,
                   previously_completed_on=body.previously_completed_on,
                   previously_completed_date_known=body.previously_completed_date_known)
    db.commit()
    return ov.public(row)


@router.delete("/onboarding/{organization_id}/overrides/{override_id}")
def withdraw_override(organization_id: str, override_id: str,
                      reason: str = Query(..., min_length=1),
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)) -> dict:
    impl = _staff_impl(db, user, organization_id)
    auth.assert_can(db, user, auth.CAP_OVERRIDE_MILESTONE,
                    brand_sales_org_id=impl.brand_sales_org_id,
                    organization_id=organization_id)
    row = ov.remove(db, impl, user, override_id, reason)
    db.commit()
    return ov.public(row)


# ════════════════════════════════════════════════════════════════════════════
# QUESTION DEFINITIONS — configuration, not a deploy
# ════════════════════════════════════════════════════════════════════════════


class QuestionDefBody(BaseModel):
    key: str
    label: str
    kind: str = "select"
    description: Optional[str] = None
    help_text: Optional[str] = None
    applies_to_types: Optional[List[str]] = None
    audience: str = "both"
    required_for_activation: bool = False
    allowed_values: Optional[List[Dict[str, Any]]] = None
    validation: Optional[Dict[str, Any]] = None
    default_value: Optional[Dict[str, Any]] = None
    display_order: int = 0
    is_active: bool = True
    platform_id: Optional[str] = None


@router.get("/question-definitions")
def list_question_definitions(platform_id: Optional[str] = Query(None),
                              agreement_type: Optional[str] = Query(None),
                              db: Session = Depends(get_db),
                              user: User = Depends(get_current_user)) -> dict:
    if platform_id and not is_god(user) and platform_id not in _actor_platform_ids(db, user):
        raise HTTPException(status_code=404, detail="Brand not found.")
    rows = q.for_agreement(db, platform_id, agreement_type)
    return {"definitions": rows, "count": len(rows)}


@router.put("/question-definitions/{key}")
def upsert_question_definition(key: str, body: QuestionDefBody,
                               db: Session = Depends(get_db),
                               user: User = Depends(get_current_user)) -> dict:
    """Add or override a commercial question without a deploy.

    God only. A question definition decides what a customer is asked about
    their own contract and what blocks activation — it is platform policy, not
    a per-deal setting.
    """
    auth.assert_can(db, user, auth.CAP_MANAGE_QUESTIONS)

    from app.models.commercial_models import CommercialQuestionDefinition
    from app.services.commercial import audit as caudit

    if body.kind not in q.KINDS:
        raise HTTPException(status_code=400,
                            detail="Unsupported answer type '%s'." % body.kind)
    if body.audience not in q.AUDIENCES:
        raise HTTPException(status_code=400,
                            detail="Unknown audience '%s'." % body.audience)
    for value in (body.applies_to_types or []):
        if value not in AGREEMENT_TYPES:
            raise HTTPException(status_code=400,
                                detail="Unknown commercial model '%s'." % value)

    scope_clause = (CommercialQuestionDefinition.platform_id.is_(None)
                    if body.platform_id is None
                    else CommercialQuestionDefinition.platform_id == body.platform_id)
    row = (db.query(CommercialQuestionDefinition)
           .filter(scope_clause, CommercialQuestionDefinition.key == key)
           .first())
    before = None
    if row is None:
        row = CommercialQuestionDefinition(platform_id=body.platform_id, key=key,
                                           version=0,
                                           created_by_user_id=user.id)
        db.add(row)
    else:
        before = {"label": row.label, "required_for_activation":
                  row.required_for_activation, "is_active": row.is_active,
                  "version": row.version}

    row.label = body.label
    row.description = body.description
    row.help_text = body.help_text
    row.kind = body.kind
    row.applies_to_types = body.applies_to_types
    row.audience = body.audience
    row.required_for_activation = bool(body.required_for_activation)
    row.allowed_values = body.allowed_values
    row.validation = body.validation
    row.default_value = body.default_value
    row.display_order = int(body.display_order or 0)
    row.is_active = bool(body.is_active)
    row.version = int(row.version or 0) + 1
    db.flush()

    caudit.record(db, None, user, caudit.A_QUESTION_DEFINED,
                  target_type="commercial_question_definition", target_id=row.id,
                  platform_id=body.platform_id,
                  before=before,
                  after={"label": row.label,
                         "required_for_activation": row.required_for_activation,
                         "is_active": row.is_active, "version": row.version})
    db.commit()
    return {"key": row.key, "version": row.version, "is_active": row.is_active}
