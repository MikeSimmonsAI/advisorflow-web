"""MY TEAM + SALES ONBOARDING — the EvoSys Pro sales team, run from Back Office.

Every rule lives in app/services/sales_team.py and sales_onboarding.py; this
file is the HTTP surface. Each route re-checks the caller on the server.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import User
from app.models.sales_team_models import SalesOnboarding, SalesOnboardingDocument
from app.services import sales_onboarding as onb
from app.services import sales_team as team

router = APIRouter(prefix="/sales-team", tags=["sales-team"])
_NO_STORE = {"Cache-Control": "no-store, private"}


def _brand(db: Session):
    b = team.program_brand(db)
    if b is None:
        raise HTTPException(status_code=503, detail="The EvoSys Pro sales organization is not set up.")
    return b


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    brand = team.program_brand(db)
    rights = team.actor_rights(db, user)
    rec = onb.record(db, user.id, brand.id) if brand else None
    return {"rights": rights,
            "onboarding": (onb.checklist(db, rec, with_file_names=True) if rec else None),
            "approved": bool(rec and rec.status == "approved")}


@router.get("/roster")
def roster(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return Response(content=__import__("json").dumps(team.roster(db, user)),
                    media_type="application/json", headers=_NO_STORE)


class InviteIn(BaseModel):
    email: str
    full_name: str
    seat: str
    manager_id: Optional[str] = None
    base_url: Optional[str] = None


@router.get("/members/{user_id}/profile")
def member_profile(user_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A salesperson's deals, activity and demo work, inside the caller's team scope."""
    return team.profile(db, user, user_id)


@router.post("/invite")
def invite(body: InviteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return team.invite(db, user, email=body.email, full_name=body.full_name, seat_key=body.seat,
                       manager_id=body.manager_id, base_url=body.base_url)


@router.post("/members/{user_id}/suspend")
def suspend(user_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    team.set_suspended(db, user, user_id, True)
    return {"ok": True}


@router.post("/members/{user_id}/reactivate")
def reactivate(user_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    team.set_suspended(db, user, user_id, False)
    return {"ok": True}


class MoveIn(BaseModel):
    manager_id: str


@router.post("/members/{user_id}/move")
def move(user_id: str, body: MoveIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    team.move(db, user, user_id, body.manager_id)
    return {"ok": True}


class DepartIn(BaseModel):
    departed_at: Optional[str] = None
    note: Optional[str] = None


@router.post("/members/{user_id}/depart")
def depart(user_id: str, body: DepartIn, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    when = None
    if body.departed_at:
        try:
            when = datetime.fromisoformat(body.departed_at[:19])
        except ValueError:
            raise HTTPException(status_code=422, detail="Departure date must be a date.")
    team.record_departure(db, user, user_id, when, body.note)
    return {"ok": True}


# ── onboarding: the person's own ─────────────────────────────────────────────

def _own(db: Session, user: User) -> SalesOnboarding:
    brand = _brand(db)
    rec = onb.record(db, user.id, brand.id)
    if rec is None:
        if team.seat(db, user, brand) in (None, team.SEAT_GOD, team.SEAT_OWNER):
            raise HTTPException(status_code=404, detail="You don't have a sales onboarding to complete.")
        rec = onb.open_for(db, user, brand.id)
        db.commit()
    return rec


@router.get("/onboarding/me")
def my_onboarding(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _own(db, user)
    return Response(content=__import__("json").dumps(
        {"application": onb.application(rec), "checklist": onb.checklist(db, rec, with_file_names=True),
         "fields": onb.APPLICATION_LABELS,
         "journey": __import__("app.services.sales_contractor", fromlist=["x"]).journey(db, rec, user),
         "documents": __import__("app.services.sales_contractor", fromlist=["x"]).documents(db, rec, with_files=False)}),
        media_type="application/json", headers=_NO_STORE)


@router.put("/onboarding/me/application")
def save_application(body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _own(db, user)
    onb.save_application(db, rec, body or {})
    db.commit()
    return {"application": onb.application(rec), "checklist": onb.checklist(db, rec, with_file_names=True)}


@router.post("/onboarding/me/documents/{kind}")
async def upload_document(kind: str, file: UploadFile = File(...), user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    rec = _own(db, user)
    await onb.upload(db, rec, kind, file, user)
    db.commit()
    return {"checklist": onb.checklist(db, rec, with_file_names=True)}


@router.post("/onboarding/me/submit")
def submit(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _own(db, user)
    onb.submit(db, rec)
    db.commit()
    return {"checklist": onb.checklist(db, rec, with_file_names=True)}


# ── onboarding: owners and God ───────────────────────────────────────────────

def _admin_record(db: Session, user: User, onboarding_id: str) -> SalesOnboarding:
    if not team.actor_rights(db, user)["can_approve"]:
        raise HTTPException(status_code=403, detail="Only an owner can do that.")
    rec = db.query(SalesOnboarding).filter(SalesOnboarding.id == onboarding_id).first()
    if rec is None or rec.brand_sales_org_id != _brand(db).id:
        raise HTTPException(status_code=404, detail="Onboarding not found.")
    return rec


@router.get("/onboarding/{onboarding_id}")
def onboarding_detail(onboarding_id: str, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    rec = _admin_record(db, user, onboarding_id)
    return Response(content=__import__("json").dumps(
        {"application": onb.application(rec), "checklist": onb.checklist(db, rec, with_file_names=True)}),
        media_type="application/json", headers=_NO_STORE)


@router.post("/onboarding/{onboarding_id}/approve")
def approve(onboarding_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _admin_record(db, user, onboarding_id)
    sc.before_approval(db, rec, user)
    onb.approve(db, rec, user)
    db.commit()
    return {"ok": True}


class SendBackIn(BaseModel):
    note: Optional[str] = None


@router.post("/onboarding/{onboarding_id}/send-back")
def send_back(onboarding_id: str, body: SendBackIn, request: Request, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _admin_record(db, user, onboarding_id)
    onb.send_back(db, rec, user, body.note or "")
    db.flush()
    emailed = sc.notify_corrections(db, user, rec, _base_url(request))
    db.commit()
    return {"ok": True, "email_sent": emailed, "corrections": sc.corrections(db, rec)}


@router.get("/onboarding/{onboarding_id}/documents/{document_id}")
def download(onboarding_id: str, document_id: str, inline: bool = False,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rec = _admin_record(db, user, onboarding_id)
    doc = (db.query(SalesOnboardingDocument)
           .filter(SalesOnboardingDocument.id == document_id,
                   SalesOnboardingDocument.onboarding_id == rec.id).first())
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    data = onb.read_document(db, doc, rec, user, "viewed" if inline else "downloaded")
    db.commit()
    disp = "inline" if inline else "attachment"
    return Response(content=data, media_type=doc.content_type,
                    headers={**_NO_STORE, "Content-Disposition": '%s; filename="%s"' % (disp, doc.filename),
                             "X-Content-Type-Options": "nosniff"})


# ── unclaimed commission: owners and God only ────────────────────────────────

@router.get("/unclaimed")
def unclaimed(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Commission no one was paid because the seat was empty or the person had
    left the company, plus refunds with commission still standing. EvoSys keeps
    unclaimed money; it never raises anyone else's pay."""
    if not team.actor_rights(db, user)["is_admin"]:
        raise HTTPException(status_code=403, detail="Only the owners can see unclaimed commission.")
    from app.services import sales_program_comp
    return sales_program_comp.unclaimed_report(db)


# ── Pricing & Plans (Back Office): what the rates are, and who changes them ──

@router.get("/pricing")
def pricing(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The EvoSys Pro Sales launch commission rules (versioned) and the
    customer package prices, for God Mode, the owners and anyone granted
    compensation view/manage on the brand. Read-only here: the commission
    rules change only as a new version, and package prices are edited in God
    Mode, which stays owner-only."""
    from app.models.sales_models import BrandPackage
    from app.services import deal_builder_commission as dbc
    from app.services.sales_access import is_god
    brand = team.program_brand(db)
    if brand is None:
        raise HTTPException(status_code=404, detail="EvoSys Pro Sales is not set up.")
    rights = team.actor_rights(db, user)
    allowed = rights["is_admin"] or team._has(db, user, "sales_comp_view", brand.id) \
        or team._has(db, user, "sales_comp_manage", brand.id)
    if not allowed:
        raise HTTPException(status_code=403,
                            detail="Pricing & Plans is for the owners and people allowed to see team compensation.")
    pkgs = (db.query(BrandPackage).filter(BrandPackage.platform_id == brand.platform_id)
            .order_by(BrandPackage.setup_fee.is_(None), BrandPackage.setup_fee).all())
    plans = []
    for key, p in dbc.PLANS.items():
        plans.append({"key": key, "name": p["name"], "kind": p["kind"],
                      "setup": float(p.get("setup") or 0) if p.get("setup") is not None else None,
                      "monthly": float(p["month"]) if p.get("month") is not None else None,
                      "payout": {k: float(v) for k, v in (p.get("payout") or {}).items()}})
    return {
        "brand": {"id": brand.id, "name": brand.name},
        "rules_version": dbc.RULES_VERSION,
        "commission": {"plans": plans,
                       "package_monthly_pct": {k: float(v) for k, v in dbc.PACKAGE_MONTHLY_PCT.items()},
                       "saas_pct": {k: float(v) for k, v in dbc.SAAS_PCT.items()},
                       "holdback_days": 14},
        "packages": [{"key": k.key, "name": k.name,
                      "setup_fee": float(k.setup_fee) if k.setup_fee is not None else None,
                      "monthly_price": float(k.monthly_price) if k.monthly_price is not None else None}
                     for k in pkgs],
        "can_edit_in_god_mode": bool(is_god(user)),
        "how_to_change": ("Package prices are edited in God Mode → Pricing. Commission rates change "
                          "only as a new rules version, so every saved quote and earned record keeps "
                          "the version it was made under."),
    }

# ── contractor onboarding: invites, review, agreement signing ────────────────

def _base_url(request: Optional[Request]) -> Optional[str]:
    """Where links in onboarding emails point: the EvoSys Pro app (the people
    who get them open them in the app, never on the API host)."""
    import os
    return (os.environ.get("EVOSYS_APP_URL") or "https://app.evosyspro.live").rstrip("/")


def _ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() or (request.client.host if request.client else None)) or None


@router.post("/members/{user_id}/resend-invite")
def resend_invite(user_id: str, request: Request, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    return sc.resend_invite(db, user, user_id, _base_url(request))


class ReviewIn(BaseModel):
    status: str
    note: Optional[str] = None


@router.post("/onboarding/{onboarding_id}/documents/{document_id}/review")
def review_document(onboarding_id: str, document_id: str, body: ReviewIn,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _admin_record(db, user, onboarding_id)
    return sc.review_document(db, user, rec, document_id, body.status, body.note)


class TemplateIn(BaseModel):
    title: str
    version_label: str
    exhibit_version: str
    body_text: str
    requires_countersign: bool = False


class ApproveTemplateIn(BaseModel):
    approved_for_signing: bool = False


@router.get("/agreement-templates")
def list_templates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    return {"templates": sc.list_templates(db, user)}


@router.post("/agreement-templates")
def create_template(body: TemplateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    return sc.create_template(db, user, title=body.title, version_label=body.version_label,
                              exhibit_version=body.exhibit_version, body_text=body.body_text,
                              requires_countersign=body.requires_countersign)


@router.post("/agreement-templates/{template_id}/approve")
def approve_template(template_id: str, body: ApproveTemplateIn, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    return sc.approve_template(db, user, template_id, body.approved_for_signing)


@router.post("/agreement-templates/{template_id}/retire")
def retire_template(template_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    return sc.retire_template(db, user, template_id)


class SendAgreementIn(BaseModel):
    template_id: str


@router.post("/onboarding/{onboarding_id}/send-agreement")
def send_agreement(onboarding_id: str, body: SendAgreementIn, request: Request,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _admin_record(db, user, onboarding_id)
    return sc.send_agreement(db, user, rec, body.template_id, _base_url(request))


class NoteIn(BaseModel):
    note: str = ""


class SignIn(BaseModel):
    typed_name: str = ""
    consent: bool = False


@router.post("/onboarding/{onboarding_id}/agreement-correction")
def owner_agreement_correction(onboarding_id: str, body: NoteIn, user: User = Depends(get_current_user),
                               db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _admin_record(db, user, onboarding_id)
    return sc.request_agreement_correction(db, user, rec, body.note)


@router.post("/onboarding/{onboarding_id}/agreement-countersign")
def countersign(onboarding_id: str, body: SignIn, request: Request, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _admin_record(db, user, onboarding_id)
    return sc.countersign(db, user, rec, body.typed_name, _ip(request))


@router.get("/onboarding/{onboarding_id}/signed-agreement")
def owner_signed_copy(onboarding_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _admin_record(db, user, onboarding_id)
    data = sc.signed_copy(db, user, rec)
    return Response(content=data, media_type="application/pdf",
                    headers={**_NO_STORE, "Content-Disposition": 'attachment; filename="signed-agreement.pdf"',
                             "X-Content-Type-Options": "nosniff"})


@router.get("/onboarding/{onboarding_id}/journey")
def owner_journey(onboarding_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _admin_record(db, user, onboarding_id)
    person = db.query(User).filter(User.id == rec.user_id).first()
    return Response(content=__import__("json").dumps({
        **sc.journey(db, rec, person), "documents": sc.documents(db, rec, with_files=True),
        "application": onb.application(rec), "onboarding_id": rec.id,
        "account": sc.account_status(db, person)}),
        media_type="application/json", headers=_NO_STORE)


@router.get("/onboarding/me/agreement")
def my_agreement(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _own(db, user)
    env = sc.current_envelope(db, rec)
    if env is not None and env.status == "sent":
        sc.mark_viewed(db, rec, env)
    from app.services.evosys_esign import CONSENT_TEXT
    return Response(content=__import__("json").dumps({"agreement": sc.envelope_out(env, with_html=True),
                                                      "consent_text": CONSENT_TEXT,
                                                      "legal_name": rec.legal_name}),
                    media_type="application/json", headers=_NO_STORE)


@router.post("/onboarding/me/agreement/sign")
def my_sign(body: SignIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _own(db, user)
    return sc.sign(db, user, rec, typed_name=body.typed_name, consent=body.consent,
                   ip=_ip(request), ua=request.headers.get("user-agent"))


@router.post("/onboarding/me/agreement/correction")
def my_agreement_correction(body: NoteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _own(db, user)
    return sc.request_agreement_correction(db, user, rec, body.note)


@router.get("/onboarding/me/agreement/document")
def my_agreement_document(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The agreement as sent (before signing), to read offline or keep."""
    from app.services import sales_contractor as sc
    rec = _own(db, user)
    env = sc.current_envelope(db, rec)
    if env is None:
        raise HTTPException(status_code=404, detail="No agreement has been sent to you yet.")
    return Response(content="<!doctype html><meta charset=utf-8><title>%s</title>%s" % (env.title, env.document_html),
                    media_type="text/html",
                    headers={**_NO_STORE, "Content-Disposition": 'attachment; filename="contractor-agreement.html"',
                             "X-Content-Type-Options": "nosniff"})


@router.get("/onboarding/me/agreement/signed-copy")
def my_signed_copy(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services import sales_contractor as sc
    rec = _own(db, user)
    data = sc.signed_copy(db, user, rec)
    db.commit()
    return Response(content=data, media_type="application/pdf",
                    headers={**_NO_STORE, "Content-Disposition": 'attachment; filename="signed-agreement.pdf"',
                             "X-Content-Type-Options": "nosniff"})



@router.get("/getting-started")
def getting_started(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """This salesperson's own path from approval to their first paid sale."""
    from app.services import sales_getting_started as gs
    return gs.steps_for(db, user)
