"""Owner-only: send any document for signature from one of the platform's
organizations (e.g. a VOLT Marketing agreement), and watch / remind / void it.

Only the platform owner (God) can use these routes. The document is frozen at
send time (HTML + SHA-256); signers can only fill the blanks the sender marked
for them, sign or decline. Everything else - links, one-time codes, consent,
order, sealing and the emailed signed PDF - is evosys_esign.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.esign_models import EsignEnvelope
from app.models.models import AuditLogEntry, Organization, User
from app.services import evosys_esign as ev

log = logging.getLogger(__name__)
router = APIRouter(prefix="/esign/owner", tags=["esign-owner"])


class FieldIn(BaseModel):
    key: str = Field(..., max_length=40)
    label: str = Field(..., max_length=120)


class SignerIn(BaseModel):
    role: str = Field(..., max_length=60)
    name: Optional[str] = Field(None, max_length=120)
    email: str = Field(..., max_length=254)
    fields: List[FieldIn] = []


class SendIn(BaseModel):
    title: str = Field(..., min_length=3, max_length=250)
    document_html: str = Field(..., min_length=50, max_length=400_000)
    signers: List[SignerIn] = Field(..., min_length=1, max_length=10)
    cc: List[str] = Field(default_factory=list, max_length=10)
    message: Optional[str] = Field(None, max_length=2000)
    sender_name: Optional[str] = Field(None, max_length=120)
    kind: Optional[str] = Field(None, max_length=60)


def _audit(db: Session, god: User, action: str, env: EsignEnvelope, detail: Dict) -> None:
    try:
        db.add(AuditLogEntry(actor_user_id=god.id, organization_id=env.organization_id, action=action,
                             target_type="esign_envelope", target_id=env.id, details=json.dumps(detail)))
    except Exception:  # noqa: BLE001 - the audit row never blocks the send
        log.warning("esign owner audit row failed", exc_info=True)


def _env(db: Session, envelope_id: str) -> EsignEnvelope:
    env = db.query(EsignEnvelope).filter(EsignEnvelope.id == envelope_id).first()
    if not env:
        raise HTTPException(404, "No such envelope.")
    return env


def _out(db: Session, env: EsignEnvelope) -> Dict:
    d = ev.summary(db, env)
    d["cc"] = ev.cc_list(env)
    d["document_sha256"] = env.document_sha256
    d["final_sha256"] = env.final_sha256
    return d


@router.post("/orgs/{org_id}/envelopes")
def send(org_id: str, body: SendIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    if not db.query(Organization).filter(Organization.id == org_id).first():
        raise HTTPException(404, "No such organization.")
    try:
        env, delivered = ev.create_envelope(
            db, org_id=org_id, title=body.title, document_html=body.document_html,
            signers=[s.model_dump() for s in body.signers], kind=body.kind or "owner_document",
            message=body.message, sender=god, cc=body.cc, sender_name=body.sender_name)
    except ev.EsignError as e:
        raise HTTPException(e.status, e.message)
    _audit(db, god, "esign_sent", env, {"title": env.title, "signers": [s.email for s in body.signers],
                                         "cc": ev.cc_list(env), "document_sha256": env.document_sha256})
    db.commit()
    out = _out(db, env)
    out["first_email_delivered"] = delivered
    return out


@router.get("/envelopes/{envelope_id}")
def status(envelope_id: str, db: Session = Depends(get_db), _god: User = Depends(require_god)):
    return _out(db, _env(db, envelope_id))


@router.post("/envelopes/{envelope_id}/remind")
def remind(envelope_id: str, db: Session = Depends(get_db), _god: User = Depends(require_god)):
    try:
        r = ev.remind(db, _env(db, envelope_id))
    except ev.EsignError as e:
        raise HTTPException(e.status, e.message)
    db.commit()
    return r


class VoidIn(BaseModel):
    reason: Optional[str] = Field(None, max_length=300)


@router.post("/envelopes/{envelope_id}/void")
def void(envelope_id: str, body: VoidIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    try:
        r = ev.void(db, _env(db, envelope_id), body.reason, user=god)
    except ev.EsignError as e:
        raise HTTPException(e.status, e.message)
    db.commit()
    return r


@router.get("/envelopes/{envelope_id}/signed.pdf")
def signed_pdf(envelope_id: str, db: Session = Depends(get_db), _god: User = Depends(require_god)):
    env = _env(db, envelope_id)
    if env.status != "completed" or not env.final_pdf:
        raise HTTPException(409, "The signed copy is ready once everyone has signed.")
    return Response(content=env.final_pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % ev._filename(env.title),
                             "Cache-Control": "private, no-store"})


class SignerEmailIn(BaseModel):
    role: str = Field(..., max_length=60)
    email: str = Field(..., max_length=254)


@router.put("/envelopes/{envelope_id}/signer-email")
def change_signer_email(envelope_id: str, body: SignerEmailIn, db: Session = Depends(get_db),
                        god: User = Depends(require_god)):
    env = _env(db, envelope_id)
    try:
        r = ev.change_signer_email(db, env, body.role, body.email, user=god)
    except ev.EsignError as e:
        raise HTTPException(e.status, e.message)
    _audit(db, god, "esign_signer_email_changed", env, {"role": body.role, "email": body.email})
    db.commit()
    return r


class CcIn(BaseModel):
    cc: List[str] = Field(default_factory=list, max_length=10)


@router.put("/envelopes/{envelope_id}/cc")
def change_cc(envelope_id: str, body: CcIn, db: Session = Depends(get_db), god: User = Depends(require_god)):
    env = _env(db, envelope_id)
    try:
        r = ev.set_cc(db, env, body.cc, user=god)
    except ev.EsignError as e:
        raise HTTPException(e.status, e.message)
    _audit(db, god, "esign_cc_changed", env, {"cc": r["cc"]})
    db.commit()
    return r
