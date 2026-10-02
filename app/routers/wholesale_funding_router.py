"""/wholesale/funding — funding / capital partners and per-deal submissions.

    GET    /wholesale/funding/products                   the product vocabulary + disclaimer
    GET    /wholesale/funding/partners                   partners with their measured track record
    POST   /wholesale/funding/partners                   add (captured into the contact DB as a partner)
    PATCH  /wholesale/funding/partners/{id}              edit / verify
    GET    /wholesale/funding/deals/{deal_id}/options    partners that fit this deal's need, with reasons
    GET    /wholesale/funding/deals/{deal_id}/submissions
    POST   /wholesale/funding/deals/{deal_id}/submissions   record a deal sent to a partner
    POST   /wholesale/funding/submissions/{id}/response     record the partner's answer
    GET    /wholesale/funding/deals/{deal_id}/packet     the deal packet (json | html download) - never sent

EvoSys is not the lender - see app.services.wholesale_funding. Every route is
organization-scoped; another tenant's partner, deal or submission is a 404.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.models.models import Organization, User
from app.models.wholesale_models import (FUNDING_PRODUCTS, FUNDING_SUBMISSION_STATUSES,
                                         WholesaleFundingPartner, WholesaleFundingSubmission,
                                         WholesaleProperty)
from app.services import wholesale_funding as FD
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature
from app.utils.content_disposition import content_disposition

router = APIRouter(prefix="/wholesale/funding", tags=["wholesale-funding"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _read_org(db, user):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return org_id


class PartnerIn(BaseModel):
    name: Optional[str] = None
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    products: Optional[List[str]] = None
    states: Optional[List[str]] = None
    markets: Optional[List[str]] = None
    property_types: Optional[List[str]] = None
    min_loan: Optional[float] = None
    max_loan: Optional[float] = None
    max_ltv_pct: Optional[float] = None
    max_ltc_pct: Optional[float] = None
    min_credit_score: Optional[int] = None
    typical_close_days: Optional[int] = None
    referral_relationship: Optional[str] = None
    verified: Optional[bool] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    is_test: Optional[bool] = None


class SubmissionIn(BaseModel):
    partner_id: str
    product: Optional[str] = None
    amount_requested: Optional[float] = None
    notes: Optional[str] = None


class ResponseIn(BaseModel):
    status: str
    approved_amount: Optional[float] = None
    decline_reason: Optional[str] = None
    notes: Optional[str] = None


@router.get("/products")
def products(user: User = Depends(require_tenant_or_observer)):
    return {"products": [{"key": k, "label": FD.PRODUCT_LABELS[k]} for k in FUNDING_PRODUCTS],
            "submission_statuses": list(FUNDING_SUBMISSION_STATUSES), "disclaimer": FD.DISCLAIMER}


@router.get("/partners")
def list_partners(include_inactive: bool = False, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    q = db.query(WholesaleFundingPartner).filter(WholesaleFundingPartner.organization_id == org_id)
    if not include_inactive:
        q = q.filter(WholesaleFundingPartner.is_active.is_(True))
    rows = q.order_by(WholesaleFundingPartner.name).all()
    stats = FD.partner_stats(db, org_id, [p.id for p in rows])
    return {"partners": [FD.partner_json(p, stats.get(p.id)) for p in rows], "disclaimer": FD.DISCLAIMER}


@router.post("/partners")
def create_partner(payload: PartnerIn, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    org = db.query(Organization).filter(Organization.id == org_id).one()
    p = FD.create_partner(db, org, user, payload.model_dump(exclude_unset=True))
    db.commit()
    return FD.partner_json(p, FD.partner_stats(db, org_id, [p.id]).get(p.id))


@router.patch("/partners/{partner_id}")
def update_partner(partner_id: str, payload: PartnerIn, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = FD.get_partner(db, org_id, partner_id)
    FD.update_partner(db, org_id, p, user, payload.model_dump(exclude_unset=True))
    db.commit()
    return FD.partner_json(p, FD.partner_stats(db, org_id, [p.id]).get(p.id))


@router.get("/deals/{deal_id}/options")
def funding_options(deal_id: str, product: Optional[str] = None, amount: Optional[float] = None,
                    db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    prop = (db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id,
                                               WholesaleProperty.organization_id == org_id).first())
    if product and product not in FUNDING_PRODUCTS:
        raise HTTPException(422, "Unknown funding product.")
    partners = (db.query(WholesaleFundingPartner)
                .filter(WholesaleFundingPartner.organization_id == org_id,
                        WholesaleFundingPartner.is_active.is_(True),
                        WholesaleFundingPartner.is_test.is_(bool(deal.is_test))).all())
    matches = FD.match_partners(partners, product=product, state=getattr(prop, "state", None),
                                amount=amount, property_type=getattr(prop, "property_type", None))
    stats = FD.partner_stats(db, org_id, [m["partner"].id for m in matches])
    return {"need": {"product": product, "amount": amount, "state": getattr(prop, "state", None),
                     "property_type": getattr(prop, "property_type", None)},
            "options": [{"partner": FD.partner_json(m["partner"], stats.get(m["partner"].id)),
                         "eligible": m["eligible"], "excluded_because": m["excluded_because"],
                         "reasons": m["reasons"]} for m in matches],
            "disclaimer": FD.DISCLAIMER}


@router.get("/deals/{deal_id}/submissions")
def deal_submissions(deal_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_or_observer)):
    org_id = _read_org(db, user)
    svc.get_deal(db, org_id, deal_id)
    rows = (db.query(WholesaleFundingSubmission)
            .filter(WholesaleFundingSubmission.organization_id == org_id,
                    WholesaleFundingSubmission.deal_id == deal_id)
            .order_by(WholesaleFundingSubmission.created_at.desc()).all())
    names = {p.id: p.name for p in db.query(WholesaleFundingPartner).filter(
        WholesaleFundingPartner.organization_id == org_id).all()}
    return {"submissions": [FD.submission_json(s, names.get(s.partner_id)) for s in rows]}


@router.post("/deals/{deal_id}/submissions")
def create_submission(deal_id: str, payload: SubmissionIn, db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    partner = FD.get_partner(db, org_id, payload.partner_id)
    s = FD.submit(db, org_id, deal_id, partner, user, product=payload.product,
                  amount=payload.amount_requested, notes=payload.notes)
    db.commit()
    return FD.submission_json(s, partner.name)


@router.post("/submissions/{submission_id}/response")
def submission_response(submission_id: str, payload: ResponseIn, db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    s = (db.query(WholesaleFundingSubmission)
         .filter(WholesaleFundingSubmission.id == submission_id,
                 WholesaleFundingSubmission.organization_id == org_id).first())
    if s is None:
        raise HTTPException(404, "Submission not found")
    FD.record_response(db, org_id, s, user, status=payload.status, approved_amount=payload.approved_amount,
                       decline_reason=payload.decline_reason, notes=payload.notes)
    db.commit()
    return FD.submission_json(s)


@router.get("/deals/{deal_id}/packet")
def deal_packet(deal_id: str, format: str = "json", partner_id: Optional[str] = None,
                submission_id: Optional[str] = None, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user)):
    """Build the funding deal packet from this workspace's own data. It is a
    download for a person to review; nothing is sent to anyone."""
    from app.services import wholesale_funding_packet as PK
    org_id = svc.write_org_id(db, user)
    pk = PK.build(db, org_id, deal_id, partner_id=partner_id, submission_id=submission_id)
    PK.log_generated(db, org_id, pk, user)
    db.commit()
    if format == "html":
        safe = "".join(ch if ch.isalnum() else "-" for ch in pk["property"]["address"])[:60].strip("-")
        return HTMLResponse(PK.render_html(pk), headers={
            "Content-Disposition": content_disposition("funding-packet-%s.html" % (safe or "deal")),
            "Cache-Control": "no-store"})
    return pk
