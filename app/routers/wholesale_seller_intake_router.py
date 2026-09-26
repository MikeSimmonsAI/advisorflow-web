"""Public seller inquiries, and the operator's view of seller SMS consent.

TWO ROUTERS IN ONE FILE, ON PURPOSE - they are the two ends of one record.

`public_router`  POST /site-intake/wholesale/{intake_key}/seller-inquiry
    Server-to-server from a public seller page (the page's PHP posts it; the
    browser never sees this URL or the key). No authentication for the seller.
    Rate limited on the shared public-intake budget. The organization comes
    ONLY from the key; any organization field in the payload is ignored.

`ops_router`     /wholesale/sms/...
    Signed-in, organization-scoped, behind the Wholesale entitlement. Every
    query filters on the caller's own workspace, so another tenant cannot read
    this organization's consent records, and a consent in one organization is
    never evidence for a send in another.

NO `from __future__ import annotations` - see site_intake_router.py: the
rate-limit decorator needs real annotation objects.
"""
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.deps import (get_db, require_not_observation, require_tenant_or_observer,
                      require_tenant_user)
from app.limiter import limiter
from app.models.models import Lead, User
from app.models.sms_consent_models import SmsConsentRecord
from app.models.wholesale_models import WholesaleDeal
from app.services import public_intake
from app.services import wholesale_seller_intake as intake
from app.services import wholesale_service as svc
from app.services import wholesale_sms as ws
from app.services.entitlements import require_feature

log = logging.getLogger(__name__)

_REFUSED = "We can't accept this submission right now. Please call or email us."

public_router = APIRouter(prefix="/site-intake/wholesale", tags=["site-intake"])
ops_router = APIRouter(prefix="/wholesale/sms", tags=["wholesale"],
                       dependencies=[Depends(require_feature(intake.FEATURE))])


class SellerInquiryPayload(BaseModel):
    """Only these fields are read. Anything else - including any attempt to
    name an organization or a consent timestamp - is ignored."""
    model_config = ConfigDict(extra="ignore")

    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    property_condition: Optional[str] = None
    timeline: Optional[str] = None
    reason_for_selling: Optional[str] = None
    preferred_contact_method: Optional[str] = None
    notes: Optional[str] = None
    sms_consent: Optional[Any] = None
    disclosure_text: Optional[str] = None
    disclosure_version: Optional[str] = None
    form_version: Optional[str] = None
    source_url: Optional[str] = None
    submission_id: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None


@public_router.post("/{intake_key}/seller-inquiry", status_code=201)
@limiter.shared_limit(public_intake.PUBLIC_INTAKE_LIMIT,
                      scope=public_intake.PUBLIC_INTAKE_SCOPE)
def seller_inquiry(intake_key: str, payload: SellerInquiryPayload, request: Request,
                   db: Session = Depends(get_db)):
    try:
        org, settings = intake.resolve_destination(db, intake_key)
    except intake.IntakeRefused as exc:
        log.error("seller-inquiry refused: %s", exc.reason)
        raise HTTPException(status_code=503, detail=_REFUSED)
    data = intake.validate(payload.model_dump())
    try:
        result = intake.submit(db, org, settings, data)
    except intake.IntakeRefused as exc:
        log.error("seller-inquiry refused for org %s: %s", org.id, exc.reason)
        raise HTTPException(status_code=503, detail=_REFUSED)
    # The seller gets a reference and whether THEIR consent was recorded -
    # nothing about scores, deals, buyers or internal workflow.
    return {"success": True, "reference": result["reference"],
            "sms_consent_recorded": result["sms_consent_recorded"]}


# ── Operator side ───────────────────────────────────────────────────────────

def _org(db: Session, user: User) -> str:
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return str(org_id)


def _lead_in_org(db: Session, org_id: str, lead_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@ops_router.get("/status")
def sms_status(db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    return ws.program_status(db, _org(db, user))


@ops_router.get("/consents")
def list_consents(lead_id: Optional[str] = None, deal_id: Optional[str] = None,
                  phone: Optional[str] = None, limit: int = 50,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_or_observer)):
    org_id = _org(db, user)
    q = db.query(SmsConsentRecord).filter(SmsConsentRecord.organization_id == org_id)
    if deal_id:
        deal = (db.query(WholesaleDeal)
                .filter(WholesaleDeal.id == deal_id,
                        WholesaleDeal.organization_id == org_id).first())
        if deal is None:
            raise HTTPException(status_code=404, detail="Deal not found")
        lead_id = lead_id or deal.seller_lead_id
        if not lead_id:
            return {"consents": [], "sms_consent": False, "status": None}
    current = None
    if lead_id:
        lead = _lead_in_org(db, org_id, lead_id)
        e164 = ws.normalize_e164(lead.phone)
        current = e164
        q = q.filter((SmsConsentRecord.lead_id == lead.id)
                     | (SmsConsentRecord.phone_normalized == (e164 or "-")))
    if phone:
        current = ws.normalize_e164(phone)
        q = q.filter(SmsConsentRecord.phone_normalized == (current or "-"))
    rows = (q.order_by(SmsConsentRecord.consented_at.desc())
            .limit(max(1, min(limit, 200))).all())
    # CONSENT IS ABOUT A NUMBER. The headline answer is for the lead's CURRENT
    # number; a record for a number they used to have is history, listed but
    # never reported as "consent on file" (a seller whose phone was corrected
    # has no consent for the new one until they give it).
    for_current = [r for r in rows if current and r.phone_normalized == current] if current else rows
    latest = for_current[0] if for_current else None
    return {"consents": [ws.consent_json(r) for r in rows],
            "sms_consent": bool(latest and latest.status == "opted_in"),
            "status": latest.status if latest else None,
            "current_phone": current,
            "has_consent_for_other_numbers": any(current and r.phone_normalized != current for r in rows)}


@ops_router.get("/eligibility")
def eligibility(lead_id: Optional[str] = None, phone: Optional[str] = None,
                db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer)):
    """Would the seller SMS program send to this lead/number right now, and if
    not, exactly why. Read-only; sends nothing."""
    org_id = _org(db, user)
    lead = _lead_in_org(db, org_id, lead_id) if lead_id else None
    target = getattr(lead, "phone", None) if lead is not None else phone
    if not target:
        raise HTTPException(status_code=400, detail="Give a lead_id or a phone.")
    return ws.check_eligibility(db, org_id, target, lead=lead)


class OptOutPayload(BaseModel):
    phone: str
    reason: Optional[str] = None


@ops_router.post("/opt-out")
def manual_opt_out(payload: OptOutPayload, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    """Record an opt-out a person told us about another way (a call, an email)."""
    org_id = svc.write_org_id(db, user)
    e164 = ws.normalize_e164(payload.phone)
    if not e164:
        raise HTTPException(status_code=422, detail="That is not a usable US number.")
    reason = (payload.reason or "Opt-out recorded by an operator")[:200]
    n = ws.record_opt_out(db, org_id, e164, keyword=None, reason=reason, source="manual")
    db.commit()
    from app.services.compliance_service import add_suppression_entry
    add_suppression_entry(db, org_id, e164, reason=reason)
    from app.models.wholesale_models import ACTOR_USER
    svc.log_event(db, org_id, "sms.opt_out", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="SMS opt-out recorded by an operator",
                  details={"consents_withdrawn": n})
    db.commit()
    return {"phone": e164, "consents_withdrawn": n, "suppressed": True}
