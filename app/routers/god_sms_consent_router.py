"""SMS Consent Center - read-only operator view. God-only. Sends nothing.

    GET /god/sms-consent/overview   programs (sender, wording version, open or
                                    pending), campaign registry (what each
                                    campaign may carry right now)
    GET /god/sms-consent/records    the consent ledger: program, sender,
                                    wording + version, server timestamp, source
                                    page, IP, STOP status. Filter by program,
                                    phone, organization.
    GET /god/sms-consent/sci-check  why an SCI text to a number would or would
                                    not be allowed (the real gate, dry)
    POST /god/sms-consent/sci/reconcile
                                    owner-attested consent for the EXISTING SCI
                                    contacts (dry run unless apply). Skips
                                    suppressed / DNC / ever-opted-out numbers.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import User
from app.models.sms_consent_models import SmsConsentRecord
from app.services import sms_campaigns, sms_programs, wholesale_sms

router = APIRouter(prefix="/god/sms-consent", tags=["god-sms-consent"])


@router.get("/overview")
def overview(_god: User = Depends(require_god)):
    return {"programs": sms_programs.programs_view(),
            "campaigns": sms_campaigns.public_view(),
            "sci_send_enabled": sms_programs._truthy(sms_programs.SCI_SEND_ENV)}


@router.get("/records")
def records(program: Optional[str] = Query(None, description="general | wholesale | sci"),
            phone: Optional[str] = None, organization_id: Optional[str] = None,
            limit: int = Query(100, ge=1, le=500),
            db: Session = Depends(get_db), _god: User = Depends(require_god)):
    q = db.query(SmsConsentRecord)
    if program:
        p = sms_programs.get(program)
        q = q.filter(SmsConsentRecord.program == (p.consent_program if p else program))
    if phone:
        q = q.filter(SmsConsentRecord.phone_normalized == (wholesale_sms.normalize_e164(phone) or "-"))
    if organization_id:
        q = q.filter(SmsConsentRecord.organization_id == organization_id)
    rows = q.order_by(SmsConsentRecord.consented_at.desc()).limit(limit).all()
    return {"count": len(rows), "records": [sms_programs.record_view(r) for r in rows]}


@router.get("/sci-check")
def sci_check(phone: str, from_number: Optional[str] = None,
              db: Session = Depends(get_db), _god: User = Depends(require_god)):
    org_id = sms_programs.sci_org_id(db)
    if not org_id:
        return {"eligible": False, "reasons": ["program_org_not_configured"]}
    return sms_programs.sci_check(db, org_id, phone,
                                  from_number=from_number or sms_programs.sci_sender_number())


class ReconcileIn(BaseModel):
    attested_by: str
    evidence_reference: str
    apply: bool = False


@router.post("/sci/reconcile")
def sci_reconcile(body: ReconcileIn, db: Session = Depends(get_db),
                  god: User = Depends(require_god)):
    org_id = sms_programs.sci_org_id(db)
    if not org_id or not sms_programs.is_sci_org(db, org_id):
        raise HTTPException(status_code=409, detail="The SCI organization could not be resolved.")
    try:
        out = sms_programs.reconcile_existing(db, org_id, attested_by=body.attested_by,
                                              evidence_reference=body.evidence_reference,
                                              apply=body.apply)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    out["requested_by"] = god.email
    return out
