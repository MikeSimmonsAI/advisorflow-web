"""The staff side of the private Deal Room (#415) - inside Wholesale, behind the
same gates as every Wholesale route (tenant user, feature, no observer writes;
field logins are fenced off by wholesale_team.FIELD_BLOCKED_PREFIXES).

    invites       GET/POST /wholesale/deal-room/invites ; POST .../{id}/resend ; POST .../{id}/revoke
    investors     GET /wholesale/deal-room/buyers/{buyer_id} ; POST .../accounts/{id}/status
    listings      GET /wholesale/deal-room/listings ; POST /wholesale/deal-room/deals/{deal_id}/listing
                  PATCH /wholesale/deal-room/listings/{id} ; POST .../{id}/{approve|publish|withdraw|close|redraft}
                  GET .../{id}/audience
    holds         GET /wholesale/deal-room/holds ; POST /wholesale/deal-room/holds/{id}
    inbox         GET /wholesale/deal-room/inbox ; GET .../threads/{id} ; POST .../threads/{id}/(reply|note|takeover|return-to-ai)
    files         GET /wholesale/deal-room/buyers/{buyer_id}/files ; GET /wholesale/deal-room/files/{id}
                  POST /wholesale/deal-room/files/{id}/review ; POST /wholesale/deal-room/buyers/{buyer_id}/files (share)
    signatures    POST /wholesale/deal-room/envelopes/{id}/staff-session (sign your own part)
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.models.models import User
from app.models.wholesale_investor_models import DealListing, ExclusiveHold, InvestorInvite
from app.models.wholesale_models import WholesaleBuyer, WholesaleFile
from app.services import wholesale_deal_room as DR
from app.services import wholesale_deal_room_chat as CH
from app.services import wholesale_deal_room_manage as MG
from app.services import wholesale_deal_room_setup as SET
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale/deal-room", tags=["deal-room-staff"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _read(db, user) -> str:
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return org_id


class InviteIn(BaseModel):
    name: str
    email: str
    company: Optional[str] = None
    buyer_id: Optional[str] = None
    owner_user_id: Optional[str] = None
    is_test: bool = False


@router.get("/invites")
def invites(include_test: bool = False, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    DR._expire_invites(db, org_id)
    rows = db.query(InvestorInvite).filter(InvestorInvite.organization_id == org_id).order_by(
        InvestorInvite.created_at.desc()).limit(300).all()
    db.commit()
    return {"invites": [DR.invite_json(i) for i in rows if include_test or not i.is_test]}


@router.post("/invites")
def invite(payload: InviteIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
           _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    if payload.owner_user_id:
        owner = db.query(User).filter(User.id == payload.owner_user_id, User.organization_id == org_id).first()
        if owner is None:
            raise HTTPException(status_code=400, detail="The invitation owner must be on this team.")
    inv, token = DR.create_invite(db, org_id, user, name=payload.name, email=payload.email, company=payload.company,
                                  buyer_id=payload.buyer_id, owner_user_id=payload.owner_user_id,
                                  is_test=payload.is_test)
    db.commit()
    # The link is shown ONCE so the sender can deliver it themselves when email did not go out.
    return {**DR.invite_json(inv), "link": "%s/investors/invite/%s" % (DR.public_base(), token)}


@router.post("/invites/{invite_id}/resend")
def resend(invite_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
           _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    inv, token = DR.resend_invite(db, org_id, invite_id, user)
    db.commit()
    return {**DR.invite_json(inv), "link": "%s/investors/invite/%s" % (DR.public_base(), token)}


@router.post("/invites/{invite_id}/revoke")
def revoke(invite_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
           _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    inv = DR.revoke_invite(db, org_id, invite_id, user)
    db.commit()
    return DR.invite_json(inv)


@router.get("/buyers/{buyer_id}")
def buyer_room(buyer_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Everything the Deal Room holds for one buyer: logins, entities, threads, files, signatures, interest."""
    from app.models.esign_models import EsignEnvelope, EsignSigner
    from app.models.wholesale_buyer_engine_models import WholesaleBuyerConversation
    from app.models.wholesale_models import WholesaleBuyerOutreach
    org_id = _read(db, user)
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id, WholesaleBuyer.organization_id == org_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="Buyer not found")
    accts = DR.accounts_for_buyer(db, org_id, b.id)
    emails = [a["email"].lower() for a in accts if a.get("email")]
    sigs = (db.query(EsignSigner, EsignEnvelope).join(EsignEnvelope, EsignEnvelope.id == EsignSigner.envelope_id)
            .filter(EsignSigner.organization_id == org_id, EsignSigner.email.in_(emails or [""])).all())
    threads = db.query(WholesaleBuyerConversation).filter(WholesaleBuyerConversation.organization_id == org_id,
                                                          WholesaleBuyerConversation.buyer_id == b.id,
                                                          WholesaleBuyerConversation.kind.in_(CH.PORTAL_KINDS)).all()
    files = db.query(WholesaleFile).filter(WholesaleFile.organization_id == org_id,
                                           WholesaleFile.buyer_id == b.id).order_by(WholesaleFile.created_at.desc()).all()
    outreach = db.query(WholesaleBuyerOutreach).filter(WholesaleBuyerOutreach.organization_id == org_id,
                                                       WholesaleBuyerOutreach.buyer_id == b.id,
                                                       WholesaleBuyerOutreach.channel == "deal_room").all()
    invites = db.query(InvestorInvite).filter(InvestorInvite.organization_id == org_id,
                                              InvestorInvite.buyer_id == b.id).all()
    return {"buyer_id": b.id, "accounts": accts, "invites": [DR.invite_json(i) for i in invites],
            "threads": [{"id": c.id, "kind": c.kind, "status": c.status, "ai_enabled": bool(c.ai_enabled),
                         "unread": c.staff_unread or 0, "last_message_at": DR._iso(c.last_message_at)} for c in threads],
            "files": [DR.file_json(f) for f in files],
            "signatures": [{"envelope_id": e.id, "title": e.title, "status": e.status, "signer": s.role,
                            "signer_status": s.status, "signed_at": DR._iso(s.signed_at)} for s, e in sigs],
            "interest": [{"deal_id": o.deal_id, "status": o.status,
                          "offer": float(o.offer_amount) if o.offer_amount is not None else None,
                          "at": DR._iso(o.replied_at)} for o in outreach]}


@router.post("/accounts/{account_id}/status")
def account_status(account_id: str, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    a = DR.set_account_status(db, org_id, account_id, payload.get("status"), user)
    db.commit()
    return {"id": a.id, "status": a.status}


# ── listings ───────────────────────────────────────────────────────────────

@router.get("/listings")
def listings(include_test: bool = False, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    DR.sweep(db, org_id)
    rows = db.query(DealListing).filter(DealListing.organization_id == org_id).order_by(
        DealListing.updated_at.desc()).limit(200).all()
    out = [DR.listing_json(db, x) for x in rows if include_test or not x.is_test]
    # deals whose investor package is approved and published but not yet in the Deal Room
    from app.models.wholesale_models import WholesaleDeal, WholesaleProperty
    listed = {x.deal_id for x in rows}
    ready = (db.query(WholesaleDeal, WholesaleProperty).outerjoin(WholesaleProperty, WholesaleProperty.id == WholesaleDeal.property_id)
             .filter(WholesaleDeal.organization_id == org_id, WholesaleDeal.buyer_room_published.is_(True))
             .order_by(WholesaleDeal.updated_at.desc()).limit(100).all())
    available = [{"deal_id": d.id, "address": ", ".join(x for x in (getattr(p, "street_address", None), getattr(p, "city", None)) if x),
                  "asking_price": float(d.buyer_room_asking_price) if d.buyer_room_asking_price is not None else None,
                  "is_test": bool(getattr(d, "is_test", False) or getattr(p, "is_test", False))}
                 for d, p in ready if d.id not in listed]
    available = [a for a in available if include_test or not a["is_test"]]
    db.commit()
    return {"listings": out, "available": available, "states": list(DR.LISTING_STATES),
            "hold_presets": list(DR.HOLD_PRESETS), "hold_notice": DR.HOLD_NOTICE}


@router.post("/deals/{deal_id}/listing")
def create_listing(deal_id: str, payload: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    """Add a deal to the room. Optional in the same step: audience, selected
    buyers, availability window and which published documents the listing shows."""
    org_id = svc.write_org_id(db, user)
    svc.get_deal(db, org_id, deal_id)
    lst = DR.listing_for_deal(db, org_id, deal_id, create=True, user=user)
    opts = {k: payload[k] for k in ("audience", "buyer_ids", "min_match_score", "publish_at", "expires_at")
            if k in payload}
    if opts:
        DR.update_listing(db, org_id, lst, opts, user)
    if isinstance(payload.get("visible_document_ids"), list):
        MG.set_hidden_documents(db, org_id, lst, payload["visible_document_ids"])
    db.commit()
    return DR.listing_json(db, lst)


def _listing(db, org_id, listing_id) -> DealListing:
    lst = db.query(DealListing).filter(DealListing.id == listing_id, DealListing.organization_id == org_id).first()
    if lst is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return lst


@router.patch("/listings/{listing_id}")
def edit_listing(listing_id: str, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    lst = DR.update_listing(db, org_id, _listing(db, org_id, listing_id), payload, user)
    db.commit()
    return DR.listing_json(db, lst)


def listing_action(listing_id: str, action: str, db: Session, user: User):
    org_id = svc.write_org_id(db, user)
    lst = DR.transition(db, org_id, _listing(db, org_id, listing_id), action, user)
    if action == "publish" and lst.notify_on_publish:
        n = 0
        for a in DR.audience_preview(db, org_id, lst):
            n += 1 if a["eligible"] else 0
        DR.log(db, org_id, "deal_room.listing_audience", "%d investor(s) can now see this listing" % n, user,
               deal_id=lst.deal_id)
    db.commit()
    return DR.listing_json(db, lst)


def _listing_route(action: str):
    def handler(listing_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                _g: User = Depends(require_not_observation)):
        return listing_action(listing_id, action, db, user)
    handler.__name__ = "listing_%s" % action
    router.add_api_route("/listings/{listing_id}/%s" % action, handler, methods=["POST"])


for _action in DR.TRANSITIONS:          # approve / publish / withdraw / close / redraft - one route each
    _listing_route(_action)


@router.get("/listings/{listing_id}/audience")
def audience(listing_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    return {"investors": DR.audience_preview(db, org_id, _listing(db, org_id, listing_id))}


@router.get("/holds")
def holds(status: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    DR.sweep(db, org_id)
    q = db.query(ExclusiveHold).filter(ExclusiveHold.organization_id == org_id)
    if status:
        q = q.filter(ExclusiveHold.status == status)
    out = [DR.hold_json(db, h) for h in q.order_by(ExclusiveHold.requested_at.desc()).limit(200).all()]
    db.commit()
    return {"holds": out, "presets": list(DR.HOLD_PRESETS), "notice": DR.HOLD_NOTICE}


class HoldIn(BaseModel):
    decision: str
    preset: Optional[str] = None
    minutes: Optional[int] = None
    note: Optional[str] = None


@router.post("/holds/{hold_id}")
def decide_hold(hold_id: str, payload: HoldIn, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    h = DR.decide_hold(db, org_id, hold_id, user, decision=payload.decision, preset=payload.preset,
                       minutes=payload.minutes, note=payload.note)
    db.commit()
    return DR.hold_json(db, h)


# ── inbox ──────────────────────────────────────────────────────────────────

@router.get("/inbox")
def inbox(include_test: bool = False, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return {"threads": CH.inbox(db, _read(db, user), include_test=include_test)}


@router.get("/threads/{conv_id}")
def thread(conv_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    c = CH.staff_read(db, org_id, conv_id)
    db.commit()
    return CH.thread_json(db, c, for_buyer=False)


class TextIn(BaseModel):
    text: Optional[str] = None


def _thread_action(fn):
    def handler(conv_id: str, payload: TextIn = Body(default=TextIn()), db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
        org_id = svc.write_org_id(db, user)
        c = fn(db, org_id, conv_id, payload.text, user)
        db.commit()
        return CH.thread_json(db, c, for_buyer=False)
    return handler


router.post("/threads/{conv_id}/reply")(_thread_action(lambda db, o, c, t, u: CH.staff_reply(db, o, c, t, u)))
router.post("/threads/{conv_id}/note")(_thread_action(lambda db, o, c, t, u: CH.internal_note(db, o, c, t, u)))
router.post("/threads/{conv_id}/takeover")(_thread_action(lambda db, o, c, t, u: CH.takeover(db, o, c, u)))
router.post("/threads/{conv_id}/return-to-ai")(_thread_action(lambda db, o, c, t, u: CH.return_to_ai(db, o, c, u)))


# ── files ──────────────────────────────────────────────────────────────────

def _file(db, org_id, file_id) -> WholesaleFile:
    f = db.query(WholesaleFile).filter(WholesaleFile.id == file_id, WholesaleFile.organization_id == org_id,
                                       WholesaleFile.buyer_id.isnot(None)).first()
    if f is None:
        raise HTTPException(status_code=404, detail="File not found")
    return f


@router.get("/files/{file_id}")
def staff_download(file_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.services import wholesale_files as F
    org_id = _read(db, user)
    f = _file(db, org_id, file_id)
    DR.log(db, org_id, "deal_room.file_downloaded", "Staff downloaded %s" % f.original_filename, user,
           details={"file_id": f.id})
    db.commit()
    return Response(F.fetch(f.storage_backend, f.storage_key, db=db), media_type=f.content_type,
                    headers={"Content-Disposition": 'attachment; filename="%s"' % (f.original_filename or "file"),
                             "Cache-Control": "private, no-store"})


@router.post("/files/{file_id}/review")
def review(file_id: str, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
           user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    f = _file(db, org_id, file_id)
    st = payload.get("status")
    if st not in ("accepted", "rejected"):
        raise HTTPException(status_code=400, detail="status must be accepted or rejected")
    f.review_status = st
    if st == "accepted" and f.category == "proof_of_funds" and payload.get("pof_expires"):
        from datetime import datetime as _dt
        b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == f.buyer_id).first()
        try:
            b.proof_of_funds_expires = _dt.strptime(str(payload["pof_expires"])[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="pof_expires must be YYYY-MM-DD")
        b.proof_of_funds_on_file = True                  # DOCUMENT VERIFIED by a person
    DR.log(db, org_id, "deal_room.file_reviewed", "%s %s" % (f.original_filename, st), user,
           details={"file_id": f.id, "status": st})
    db.commit()
    return DR.file_json(f)


@router.get("/buyers/{buyer_id}/files")
def buyer_files(buyer_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    if db.query(WholesaleBuyer.id).filter(WholesaleBuyer.id == buyer_id,
                                          WholesaleBuyer.organization_id == org_id).first() is None:
        raise HTTPException(status_code=404, detail="Buyer not found")
    rows = db.query(WholesaleFile).filter(WholesaleFile.organization_id == org_id, WholesaleFile.buyer_id == buyer_id
                                          ).order_by(WholesaleFile.created_at.desc()).all()
    return {"files": [DR.file_json(f) for f in rows]}


@router.post("/buyers/{buyer_id}/files")
async def share_file(buyer_id: str, file: UploadFile = File(...), category: str = Form("other"),
                     db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                     _g: User = Depends(require_not_observation)):
    from app.services import wholesale_files as F
    org_id = svc.write_org_id(db, user)
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id, WholesaleBuyer.organization_id == org_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="Buyer not found")
    data, ctype, name = await F.read_upload(file)
    f = DR.store_upload(db, org_id, buyer=b, data=data, content_type=ctype, filename=name, kind="investor_shared",
                        category=category, deal_id=None, by_account=None, by_user=user, investor_visible=True)
    DR.log(db, org_id, "deal_room.file_shared", "Shared %s with %s" % (name, b.company_name or b.contact_name), user,
           details={"file_id": f.id})
    db.commit()
    return DR.file_json(f)


@router.post("/envelopes/{envelope_id}/staff-session")
def staff_signing_session(envelope_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                          _g: User = Depends(require_not_observation)):
    """Sign YOUR OWN part of a document from inside the app (you are signed in
    as the address the envelope names)."""
    from app.models.esign_models import EsignEnvelope, EsignSigner
    from app.services import evosys_esign as EV
    org_id = svc.write_org_id(db, user)
    env = db.query(EsignEnvelope).filter(EsignEnvelope.id == envelope_id, EsignEnvelope.organization_id == org_id).first()
    s = db.query(EsignSigner).filter(EsignSigner.envelope_id == envelope_id,
                                     EsignSigner.email == (user.email or "").lower()).first() if env else None
    if s is None:
        raise HTTPException(status_code=404, detail="You are not a signer on this document.")
    try:
        token = EV.session_link(db, env, s, verified_by="staff sign-in (%s)" % user.email)
    except EV.EsignError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    db.commit()
    return {"url": "/sign/%s" % token, "token": token}


# ── setup and management (owner) ───────────────────────────────────────────

@router.get("/settings")
def get_settings(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return SET.view(db, _read(db, user), user)


@router.put("/settings")
def put_settings(payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    out = SET.save(db, org_id, payload, user)
    db.commit()
    return out


@router.get("/readiness")
def get_readiness(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return SET.readiness(db, _read(db, user), user)


@router.post("/activate")
def activate(db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
             _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    out = SET.activate(db, org_id, user)
    db.commit()
    return out


@router.post("/pause")
def pause(db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
          _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    out = SET.pause(db, org_id, user)
    db.commit()
    return out


@router.get("/overview")
def overview(include_test: bool = False, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    out = MG.overview(db, org_id, include_test)
    db.commit()
    return out


@router.get("/investors")
def investors(include_test: bool = False, q: Optional[str] = None, db: Session = Depends(get_db),
              user: User = Depends(require_tenant_or_observer)):
    return {"investors": MG.investors(db, _read(db, user), include_test, q)}


@router.get("/team")
def team(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return {"people": SET.staff_people(db, _read(db, user))}


@router.get("/candidates")
def candidates(q: Optional[str] = None, include_test: bool = False, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    return {"deals": MG.candidates(db, _read(db, user), q, include_test)}


@router.get("/listings/{listing_id}/preview")
def listing_preview(listing_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    return MG.preview(db, org_id, _listing(db, org_id, listing_id))


class DocsIn(BaseModel):
    visible_document_ids: list = []


@router.put("/listings/{listing_id}/documents")
def listing_documents(listing_id: str, payload: DocsIn, db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    lst = MG.set_hidden_documents(db, org_id, _listing(db, org_id, listing_id), payload.visible_document_ids)
    DR.log(db, org_id, "deal_room.listing_documents", "Listing documents updated", user, deal_id=lst.deal_id)
    db.commit()
    return MG.preview(db, org_id, lst)


@router.get("/documents")
def documents(include_test: bool = False, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    return MG.documents(db, _read(db, user), include_test)


@router.get("/activity")
def activity(limit: int = Query(80, ge=1, le=300), db: Session = Depends(get_db),
             user: User = Depends(require_tenant_or_observer)):
    return {"events": MG.activity(db, _read(db, user), limit)}


class AssignIn(BaseModel):
    user_id: Optional[str] = None


@router.post("/threads/{conv_id}/assign")
def assign(conv_id: str, payload: AssignIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
           _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    c = MG.assign_thread(db, org_id, conv_id, payload.user_id, user)
    db.commit()
    return CH.thread_json(db, c, for_buyer=False)


@router.get("/portal-preview")
def portal_preview(include_test: bool = True, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer)):
    """What an investor's portal shows, for the owner to look at (read-only):
    the room's welcome and every live listing as the whitelist builds it."""
    org_id = _read(db, user)
    DR.sweep(db, org_id)
    rows = (db.query(DealListing).filter(DealListing.organization_id == org_id,
                                         DealListing.state.in_(("published", "exclusive_review")))
            .order_by(DealListing.published_at.desc()).all())
    cards = []
    for lst in rows:
        if lst.is_test and not include_test:
            continue
        pv = MG.preview(db, org_id, lst)
        if not pv["serving"]:
            continue
        room = pv["room"]
        cards.append({"id": lst.id, "state": lst.state, "address": room.get("property"), "category": pv["category"],
                      "facts": pv["facts"], "asking_price": room.get("asking_price"), "summary": room.get("summary"),
                      "arv": room.get("arv"), "estimated_repairs": room.get("estimated_repairs"),
                      "photos": [p.get("id") for p in room.get("photos") or []],
                      "published_at": DR._iso(lst.published_at), "is_test": bool(lst.is_test)})
    db.commit()
    return {"room": SET.investor_view(db, org_id), "opportunities": cards,
            "notice": "Preview: what investors see. Match scores, saved deals and messages are per investor."}


@router.get("/listings/{listing_id}/photos/{file_id}")
def listing_photo(listing_id: str, file_id: str, db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_or_observer)):
    """Staff preview of a listing's investor photo (only photos the package publishes)."""
    from app.models.wholesale_models import WholesaleDeal
    from app.services import wholesale_files as F
    from app.services import wholesale_publication as P
    org_id = _read(db, user)
    lst = _listing(db, org_id, listing_id)
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == lst.deal_id).first()
    if file_id not in {p["id"] for p in P.buyer_photos(db, deal)}:
        raise HTTPException(status_code=404, detail="Photo not found")
    f = db.query(WholesaleFile).filter(WholesaleFile.id == file_id, WholesaleFile.organization_id == org_id).first()
    if f is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    return Response(F.fetch(f.storage_backend, f.storage_key, db=db), media_type=f.content_type,
                    headers={"Cache-Control": "private, max-age=300"})


@router.get("/dashboard")
def owner_dashboard(include_test: bool = False, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    out = MG.dashboard(db, org_id, user, include_test)
    db.commit()
    return out


@router.get("/feed")
def owner_feed(kind: str = Query("activity"), include_test: bool = False, limit: int = Query(12, ge=1, le=50),
               db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    if kind not in ("activity", "holds", "signups", "messages"):
        raise HTTPException(status_code=400, detail="kind must be activity, holds, signups or messages")
    return {"rows": MG.feed(db, _read(db, user), kind, include_test, limit)}
