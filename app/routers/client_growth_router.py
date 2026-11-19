"""CLIENT GROWTH API - discovery questionnaires and proposal preparation.

/client-growth/*         God Mode, the EvoSys Pro sales program owners and holders
                         of the `client_growth` grant (services/client_growth/access).
/god/client-growth/*     God Mode only (existing require_god): who has access,
                         template switches, the full history, test clean-up.
/client-growth-public/*  the prospect's private link. No login; the token names
                         one invitation and nothing else. Never returns submitted
                         answers. Rate limited.

Nothing here sends an email, a text or any other message to a prospect.
"""
# NO `from __future__ import annotations` IN A ROUTER MODULE - see the note in
# god_access_router.py. Under a `@limiter.limit` wrapper FastAPI cannot resolve
# a stringified body annotation and the app refuses to import.
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.limiter import limiter
from app.models.client_growth_models import (ClientGrowthInvitation, ClientGrowthSubmission, ClientGrowthTask,
                                             ClientGrowthTemplate)
from app.models.models import User
from app.models.sales_models import Opportunity, OpportunityEvent
from app.services.client_growth import access as A
from app.services.client_growth import service as S
from app.services.client_growth import templates as T

router = APIRouter(prefix="/client-growth", tags=["client-growth"])
god_router = APIRouter(prefix="/god/client-growth", tags=["client-growth (owner)"])
public_router = APIRouter(prefix="/client-growth-public", tags=["client-growth-public"])

PUBLIC_LIMIT = "30/minute"
SUBMIT_LIMIT = "6/minute"


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Referrer-Policy"] = "no-referrer"


# ── staff ────────────────────────────────────────────────────────────────────

@router.get("/me")
def me(c: A.Ctx = Depends(A.require)):
    return {"access": c.level, "is_owner": c.is_owner, "brand": c.bso.name,
            "god_mode_path": "/god/client-growth" if c.is_owner else None}


@router.get("/overview")
def overview(c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    S.ensure_templates(db)
    db.commit()
    attention = (db.query(ClientGrowthSubmission)
                 .filter(ClientGrowthSubmission.brand_sales_org_id == c.bso.id,
                         ClientGrowthSubmission.status.in_((S.SUB_SUBMITTED, S.SUB_UNDER_REVIEW)))
                 .order_by(ClientGrowthSubmission.submitted_at.asc()).limit(20).all())
    recent = (db.query(ClientGrowthInvitation).filter(ClientGrowthInvitation.brand_sales_org_id == c.bso.id)
              .order_by(ClientGrowthInvitation.created_at.desc()).limit(8).all())
    return {"counts": S.counts(db, c.bso.id), "test_counts": S.counts(db, c.bso.id, include_test=True),
            "attention": [S.submission_row(db, s) for s in attention],
            "recent_invitations": [S.invitation_row(db, i) for i in recent]}


@router.get("/prospects")
def prospects(q: Optional[str] = None, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    qry = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == c.bso.id)
    if q and q.strip():
        like = "%" + q.strip().lower()[:80] + "%"
        qry = qry.filter(or_(func.lower(Opportunity.company_name).like(like),
                             func.lower(Opportunity.contact_name).like(like),
                             func.lower(Opportunity.email).like(like)))
    else:
        # Without a search: the prospects already in discovery here first.
        with_inv = db.query(ClientGrowthInvitation.opportunity_id).filter(
            ClientGrowthInvitation.brand_sales_org_id == c.bso.id)
        qry = qry.filter(or_(Opportunity.id.in_(with_inv), Opportunity.source_system.in_(
            (S.SOURCE_SYSTEM, S.TEST_SOURCE_SYSTEM))))
    rows = qry.order_by(Opportunity.created_at.desc()).limit(50).all()
    return {"prospects": [S.prospect_row(db, o) for o in rows]}


class ProspectIn(BaseModel):
    company: str
    contact: Optional[str] = ""
    email: Optional[str] = ""
    phone: Optional[str] = None
    industry: Optional[str] = None
    note: Optional[str] = None
    confirm_new: bool = False


@router.post("/prospects")
def add_prospect(body: ProspectIn, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    row = S.create_prospect(db, c.user, c.bso.id, company=body.company, contact=body.contact or "",
                            email=body.email or "", phone=body.phone, industry=body.industry,
                            force=body.confirm_new, note=body.note)
    db.commit()
    return {"prospect": row}


@router.get("/prospects/{opp_id}")
def prospect_detail(opp_id: str, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    o = S.opp_for(db, c.bso.id, opp_id)
    events = (db.query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == o.id)
              .order_by(OpportunityEvent.occurred_at.desc()).limit(60).all())
    invs = (db.query(ClientGrowthInvitation).filter(ClientGrowthInvitation.opportunity_id == o.id)
            .order_by(ClientGrowthInvitation.created_at.desc()).all())
    subs = (db.query(ClientGrowthSubmission).filter(ClientGrowthSubmission.opportunity_id == o.id)
            .order_by(ClientGrowthSubmission.submitted_at.desc()).all())
    return {"prospect": S.prospect_row(db, o),
            "activity": [{"type": e.event_type, "summary": e.summary, "detail": e.detail,
                          "at": S.iso(e.occurred_at)} for e in events],
            "invitations": [S.invitation_row(db, i) for i in invs],
            "submissions": [S.submission_row(db, s) for s in subs]}


@router.get("/templates")
def templates(c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    S.ensure_templates(db)
    db.commit()
    rows = db.query(ClientGrowthTemplate).order_by(ClientGrowthTemplate.key, ClientGrowthTemplate.version).all()
    uses = dict(db.query(ClientGrowthInvitation.template_id, func.count(ClientGrowthInvitation.id))
                .group_by(ClientGrowthInvitation.template_id).all())
    return {"templates": [S.template_out(t, uses.get(t.id, 0)) for t in rows]}


@router.get("/templates/{template_id}")
def template_detail(template_id: str, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    t = db.get(ClientGrowthTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Template not found.")
    return {"template": S.template_out(t), "definition": t.definition}


class InviteIn(BaseModel):
    prospect_id: str
    template_key: str = T.INSURANCE_KEY
    prepared_for: Optional[str] = None
    contact_name: Optional[str] = None
    business_name: Optional[str] = None
    email: Optional[str] = None
    expires_days: int = S.DEFAULT_EXPIRY_DAYS


@router.post("/invitations")
def create_invitation(body: InviteIn, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    row = S.create_invitation(db, c.user, c.bso.id, opp_id=body.prospect_id, template_key=body.template_key,
                              prepared_for=body.prepared_for,
                              prefill={"contact_name": body.contact_name, "business_name": body.business_name,
                                       "email": body.email}, expires_days=body.expires_days)
    db.commit()
    return {"invitation": row, "sent": False,
            "note": "Link created. Nothing was sent - copy it and send it yourself."}


@router.get("/invitations")
def invitations(c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    rows = (db.query(ClientGrowthInvitation).filter(ClientGrowthInvitation.brand_sales_org_id == c.bso.id)
            .order_by(ClientGrowthInvitation.created_at.desc()).limit(200).all())
    return {"invitations": [S.invitation_row(db, i) for i in rows]}


@router.post("/invitations/{inv_id}/copy")
def copy_link(inv_id: str, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    out = S.copy_link(db, c.user, S.invitation_for(db, c.bso.id, inv_id))
    db.commit()
    return out


class NoteIn(BaseModel):
    note: Optional[str] = None


@router.post("/invitations/{inv_id}/mark-sent")
def mark_sent(inv_id: str, body: NoteIn, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    row = S.mark_sent(db, c.user, S.invitation_for(db, c.bso.id, inv_id), body.note)
    db.commit()
    return {"invitation": row}


class RevokeIn(BaseModel):
    reason: str
    confirm: bool = False


@router.post("/invitations/{inv_id}/revoke")
def revoke(inv_id: str, body: RevokeIn, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    if not body.confirm:
        raise HTTPException(400, "Confirm that you want to revoke this link.")
    row = S.revoke(db, c.user, S.invitation_for(db, c.bso.id, inv_id), body.reason)
    db.commit()
    return {"invitation": row}


class ReissueIn(BaseModel):
    reason: Optional[str] = None
    expires_days: int = S.DEFAULT_EXPIRY_DAYS


@router.post("/invitations/{inv_id}/reissue")
def reissue(inv_id: str, body: ReissueIn, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    row = S.reissue(db, c.user, S.invitation_for(db, c.bso.id, inv_id), body.reason or "", body.expires_days)
    db.commit()
    return {"invitation": row, "sent": False}


@router.get("/submissions")
def submissions(c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    rows = (db.query(ClientGrowthSubmission).filter(ClientGrowthSubmission.brand_sales_org_id == c.bso.id)
            .order_by(ClientGrowthSubmission.submitted_at.desc()).limit(200).all())
    return {"submissions": [S.submission_row(db, s) for s in rows]}


@router.get("/submissions/{sub_id}")
def submission_detail(sub_id: str, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    return {"submission": S.submission_row(db, S.submission_for(db, c.bso.id, sub_id), full=True)}


@router.post("/submissions/{sub_id}/review")
def start_review(sub_id: str, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    row = S.start_review(db, c.user, S.submission_for(db, c.bso.id, sub_id))
    db.commit()
    return {"submission": row}


@router.post("/submissions/{sub_id}/proposal-prepared")
def proposal_prepared(sub_id: str, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    row = S.proposal_prepared(db, c.user, S.submission_for(db, c.bso.id, sub_id))
    db.commit()
    return {"submission": row}


@router.post("/submissions/{sub_id}/ai-summary")
def ai_summary(sub_id: str, c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    row = S.ai_summary(db, c.user, S.submission_for(db, c.bso.id, sub_id))
    db.commit()
    return {"submission": row}


@router.get("/tasks")
def tasks(c: A.Ctx = Depends(A.require), db: Session = Depends(get_db)):
    rows = (db.query(ClientGrowthTask).filter(ClientGrowthTask.brand_sales_org_id == c.bso.id)
            .order_by(ClientGrowthTask.status.desc(), ClientGrowthTask.created_at.desc()).limit(200).all())
    out = []
    for k in rows:
        r = S.task_row(k)
        o = db.get(Opportunity, k.opportunity_id)
        r["company"] = o.company_name if o else None
        out.append(r)
    return {"tasks": out}


# ── God Mode ─────────────────────────────────────────────────────────────────

def _bso(db: Session):
    b = A.brand(db)
    if b is None:
        raise HTTPException(409, "The EvoSys Pro sales team was not found.")
    return b


@god_router.get("/overview")
def god_overview(god: User = Depends(require_god), db: Session = Depends(get_db)):
    b = _bso(db)
    S.ensure_templates(db)
    db.commit()
    invs = (db.query(ClientGrowthInvitation).filter(ClientGrowthInvitation.brand_sales_org_id == b.id)
            .order_by(ClientGrowthInvitation.created_at.desc()).limit(300).all())
    subs = (db.query(ClientGrowthSubmission).filter(ClientGrowthSubmission.brand_sales_org_id == b.id)
            .order_by(ClientGrowthSubmission.submitted_at.desc()).limit(300).all())
    uses = dict(db.query(ClientGrowthInvitation.template_id, func.count(ClientGrowthInvitation.id))
                .group_by(ClientGrowthInvitation.template_id).all())
    tpls = db.query(ClientGrowthTemplate).order_by(ClientGrowthTemplate.key, ClientGrowthTemplate.version).all()
    return {"brand": b.name, "counts": S.counts(db, b.id), "test_counts": S.counts(db, b.id, include_test=True),
            "access": A.people_with_access(db), "templates": [S.template_out(t, uses.get(t.id, 0)) for t in tpls],
            "invitations": [S.invitation_row(db, i) for i in invs],
            "submissions": [S.submission_row(db, s) for s in subs], "workspace_path": A.PATH}


class AccessIn(BaseModel):
    email: str
    grant: bool = True


@god_router.post("/access")
def god_access(body: AccessIn, god: User = Depends(require_god), db: Session = Depends(get_db)):
    email = (body.email or "").strip().lower()
    matches = db.query(User).filter(func.lower(User.email) == email).all()
    if len(matches) != 1:
        raise HTTPException(404, "No single existing login has that email. Client Growth never creates logins.")
    target = matches[0]
    if not target.is_active:
        raise HTTPException(409, "That login is not active.")
    A.set_grant(db, god, target, body.grant)
    db.commit()
    return {"access": A.people_with_access(db)}


class TemplateSwitchIn(BaseModel):
    active: bool


@god_router.post("/templates/{template_id}")
def god_template(template_id: str, body: TemplateSwitchIn, god: User = Depends(require_god),
                 db: Session = Depends(get_db)):
    t = db.get(ClientGrowthTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Template not found.")
    before = bool(t.is_active)
    t.is_active = bool(body.active)
    from datetime import datetime as _dt
    t.updated_at = _dt.utcnow()
    S._audit(db, god, _bso(db).id, "client_growth.template_switched", "client_growth_template", t.id,
             before={"active": before}, after={"active": t.is_active})
    db.commit()
    return {"template": S.template_out(t)}


@god_router.post("/test-records/cleanup")
def god_cleanup(confirm: bool = Body(False, embed=True), god: User = Depends(require_god),
                db: Session = Depends(get_db)):
    if not confirm:
        raise HTTPException(400, "Confirm removal of synthetic test records.")
    out = S.cleanup_test_records(db, god, _bso(db).id)
    db.commit()
    return {"removed": out}


class TestProspectIn(BaseModel):
    company: str = "Synthetic Test Agency"
    contact: str = "Test Prospect"
    email: str = "growth-test@example.test"


@god_router.post("/test-prospect")
def god_test_prospect(body: TestProspectIn, god: User = Depends(require_god), db: Session = Depends(get_db)):
    """A SEPARATE synthetic prospect for production verification. Marked as a
    test everywhere and removable by the clean-up above. Its email must be an
    unroutable example.test address."""
    if not (body.email or "").lower().endswith("@example.test"):
        raise HTTPException(422, "A test prospect must use an @example.test address.")
    row = S.create_prospect(db, god, _bso(db).id, company=body.company, contact=body.contact, email=body.email,
                            phone=None, industry="insurance", force=True, is_test=True)
    db.commit()
    return {"prospect": row}


# ── public (the prospect's private link) ─────────────────────────────────────

@public_router.get("/invitations/{token}")
@limiter.limit(PUBLIC_LIMIT)
def public_get(request: Request, response: Response, token: str, db: Session = Depends(get_db)):
    _private(response)
    return S.public_view(db, token)


class SubmitIn(BaseModel):
    answers: Dict[str, Any]


@public_router.post("/invitations/{token}/submit")
@limiter.limit(SUBMIT_LIMIT)
def public_submit(request: Request, response: Response, token: str, body: SubmitIn, db: Session = Depends(get_db)):
    _private(response)
    return S.submit(db, token, {"answers": body.answers})
