"""The investor side of the private Deal Room (#415).

    PUBLIC (no login - the emailed token is the authorisation)
        GET  /investor/invite/{token}             who invited you, and whether to register or sign in
        POST /investor/invite/{token}/accept      register (or link an existing investor login) and sign in
        POST /investor/auth/login                 investor sign-in (rate-limited like /auth/login)
    INVESTOR (role "investor", fenced to /investor/* in deps.get_current_user)
        GET  /investor/me ; PATCH /investor/me/identity ; POST /investor/me/entities
        POST /investor/me/buy-boxes ; PUT /investor/me/buy-boxes/{id}
        GET  /investor/dashboard ; GET /investor/opportunities ; GET /investor/opportunities/{id}
        GET  /investor/opportunities/{id}/photos/{file_id} ; .../documents/{doc_id}
        POST /investor/opportunities/{id}/interest ; POST /investor/opportunities/{id}/exclusive-review
        GET/POST /investor/threads ; GET /investor/threads/{id} ; POST /investor/threads/{id}/messages
        GET/POST /investor/files ; GET /investor/files/{id}
        GET  /investor/signatures ; POST /investor/signatures/{envelope_id}/session

Every investor endpoint resolves the caller's OWN account and buyer from the
session. No endpoint takes a buyer, account or organization id from the request.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.limiter import limiter
from app.models.models import User
from app.services import wholesale_deal_room as DR
from app.services import wholesale_deal_room_chat as CH

public_router = APIRouter(prefix="/investor", tags=["deal-room-public"])
router = APIRouter(prefix="/investor", tags=["deal-room-investor"])


class AcceptIn(BaseModel):
    password: str
    full_name: Optional[str] = None
    kind: Optional[str] = None


@public_router.get("/invite/{token}")
@limiter.limit("30/minute")
def invite_preview(request: Request, token: str, db: Session = Depends(get_db)):
    return DR.invite_preview(db, token)


@public_router.post("/invite/{token}/accept")
@limiter.limit("10/minute")
def invite_accept(request: Request, token: str, payload: AcceptIn, db: Session = Depends(get_db)):
    from app.services.auth_service import create_access_token
    user, acct = DR.accept_invite(db, token, password=payload.password, full_name=payload.full_name,
                                  kind=payload.kind)
    db.commit()
    return {"access_token": create_access_token(user, db, request=request), "token_type": "bearer",
            "role": "investor", "full_name": user.full_name, "account_id": acct.id}


class LoginIn(BaseModel):
    email: str
    password: str


@public_router.post("/auth/login")
@limiter.limit("10/minute")
def investor_login(request: Request, payload: LoginIn, db: Session = Depends(get_db)):
    from app.routers.auth_router import _login_clear_failures, _login_record_failure, _login_throttle_check
    from app.services.auth_service import authenticate_user, create_access_token
    email = (payload.email or "").strip().lower()
    _login_throttle_check(request, email, db)
    user = authenticate_user(db, email, payload.password)
    if user is None or user.role != "investor" or not DR.investor_accounts(db, user):
        _login_record_failure(request, email, db)
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    _login_clear_failures(request, email, db)
    return {"access_token": create_access_token(user, db, request=request), "token_type": "bearer",
            "role": "investor", "full_name": user.full_name}


def investor(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db),
             x_investor_account: Optional[str] = Header(None)):
    return DR.resolve_account(db, user, x_investor_account)


@router.get("/me")
def me(acct=Depends(investor), db: Session = Depends(get_db)):
    out = DR.profile(db, acct)
    db.commit()
    return out


@router.patch("/me/identity")
def set_identity(payload: Dict[str, Any] = Body(...), acct=Depends(investor), db: Session = Depends(get_db)):
    DR.set_identity(db, acct, payload)
    db.commit()
    return DR.profile(db, acct)


@router.post("/me/entities")
def add_entity(payload: Dict[str, Any] = Body(...), acct=Depends(investor), db: Session = Depends(get_db)):
    r = DR.add_entity(db, acct, payload)
    db.commit()
    return {**r, "profile": DR.profile(db, acct)}


@router.post("/me/buy-boxes")
def add_box(payload: Dict[str, Any] = Body(...), acct=Depends(investor), db: Session = Depends(get_db)):
    r = DR.save_box(db, acct, payload)
    db.commit()
    return r


@router.put("/me/buy-boxes/{box_id}")
def edit_box(box_id: str, payload: Dict[str, Any] = Body(...), acct=Depends(investor),
             db: Session = Depends(get_db)):
    r = DR.save_box(db, acct, payload, box_id=box_id)
    db.commit()
    return r


@router.get("/dashboard")
def dashboard(acct=Depends(investor), db: Session = Depends(get_db)):
    rows = DR.visible_listings(db, acct)
    cards = [DR.opportunity_card(db, acct, *r) for r in rows]
    cards.sort(key=lambda c: (c["hold"] is None, -(c["match"]["score"] or 0)))
    ths = CH.threads(db, acct)
    sigs = DR.investor_documents(db, acct)
    prof = DR.profile(db, acct)
    db.commit()
    return {"welcome": {"name": prof["account"]["preferred_name"] or (prof["account"]["legal_name"] or "").split(" ")[0],
                        "organization": prof["organization"]},
            "counts": {"opportunities": len(cards), "in_review": sum(1 for c in cards if c["hold"]),
                       "to_sign": sum(1 for s in sigs if s["your_turn"]),
                       "unread": sum(t.buyer_unread or 0 for t in ths)},
            "opportunities": cards,
            "onboarding": {"identity": prof["account"]["identity_complete"],
                           "buy_box": any(b["confirmed_at"] for b in prof["buy_boxes"]),
                           "proof_of_funds": prof["funding"]["documented"],
                           "welcome": prof["account"]["welcome_done"]},
            "is_test": prof["account"]["is_test"]}


@router.get("/opportunities")
def opportunities(acct=Depends(investor), db: Session = Depends(get_db)):
    out = [DR.opportunity_card(db, acct, *r) for r in DR.visible_listings(db, acct)]
    db.commit()
    return {"items": out}


@router.get("/opportunities/{listing_id}")
def opportunity(listing_id: str, acct=Depends(investor), db: Session = Depends(get_db)):
    out = DR.opportunity_detail(db, acct, listing_id)
    db.commit()
    return out


@router.get("/opportunities/{listing_id}/photos/{file_id}")
def opportunity_photo(listing_id: str, file_id: str, acct=Depends(investor), db: Session = Depends(get_db)):
    from app.models.wholesale_models import WholesaleFile
    from app.services import wholesale_files as F
    from app.services import wholesale_publication as P
    lst, deal, _m, _h = DR.get_visible(db, acct, listing_id)
    if file_id not in {p["id"] for p in P.buyer_photos(db, deal)}:
        raise HTTPException(status_code=404, detail="Photo not found")
    f = db.query(WholesaleFile).filter(WholesaleFile.id == file_id).first()
    return Response(F.fetch(f.storage_backend, f.storage_key, db=db), media_type=f.content_type,
                    headers={"Cache-Control": "private, max-age=300"})


@router.get("/opportunities/{listing_id}/documents/{doc_id}")
def opportunity_document(listing_id: str, doc_id: str, acct=Depends(investor), db: Session = Depends(get_db)):
    from app.models.wholesale_models import WholesaleDocument, WholesaleFile
    from app.services import wholesale_files as F
    lst, deal, _m, _h = DR.get_visible(db, acct, listing_id)
    d = db.query(WholesaleDocument).filter(WholesaleDocument.id == doc_id, WholesaleDocument.deal_id == deal.id,
                                           WholesaleDocument.buyer_visible.is_(True)).first()
    f = db.query(WholesaleFile).filter(WholesaleFile.id == getattr(d, "file_id", None)).first() if d else None
    if f is None:
        raise HTTPException(status_code=404, detail="Document not found")
    DR.log(db, acct.organization_id, "deal_room.document_downloaded", "Investor downloaded %s" % (d.title or "a document"),
           deal_id=deal.id, details={"document_id": d.id, "account_id": acct.id})
    db.commit()
    return Response(F.fetch(f.storage_backend, f.storage_key, db=db), media_type=f.content_type,
                    headers={"Content-Disposition": 'attachment; filename="%s"' % (f.original_filename or "document")})


@router.post("/opportunities/{listing_id}/interest")
def interest(listing_id: str, payload: Dict[str, Any] = Body(...), acct=Depends(investor),
             db: Session = Depends(get_db)):
    r = DR.submit_interest(db, acct, listing_id, payload)
    db.commit()
    return r


@router.post("/opportunities/{listing_id}/exclusive-review")
def exclusive_review(listing_id: str, payload: Dict[str, Any] = Body(default={}), acct=Depends(investor),
                     db: Session = Depends(get_db)):
    r = DR.request_hold(db, acct, listing_id, payload.get("note"))
    db.commit()
    return r


# ── conversations ──────────────────────────────────────────────────────────

@router.get("/threads")
def threads(acct=Depends(investor), db: Session = Depends(get_db)):
    if not CH.threads(db, acct):
        CH.open_thread(db, acct, "concierge")
    out = [CH.thread_json(db, c, for_buyer=True) for c in CH.threads(db, acct)]
    db.commit()
    return {"threads": out}


class ThreadIn(BaseModel):
    kind: str
    listing_id: Optional[str] = None


@router.post("/threads")
def new_thread(payload: ThreadIn, acct=Depends(investor), db: Session = Depends(get_db)):
    c = CH.open_thread(db, acct, payload.kind, payload.listing_id)
    db.commit()
    return CH.thread_json(db, c, for_buyer=True)


@router.get("/threads/{conv_id}")
def thread(conv_id: str, acct=Depends(investor), db: Session = Depends(get_db)):
    c = CH.get_thread(db, acct, conv_id)
    CH.mark_read(db, acct, c)
    db.commit()
    return CH.thread_json(db, c, for_buyer=True)


class MessageIn(BaseModel):
    text: str
    attachment_ids: Optional[List[str]] = None


@router.post("/threads/{conv_id}/messages")
def post_message(conv_id: str, payload: MessageIn, acct=Depends(investor), db: Session = Depends(get_db)):
    r = CH.post(db, acct, conv_id, payload.text, payload.attachment_ids)
    db.commit()
    return r


# ── files ──────────────────────────────────────────────────────────────────

@router.get("/files")
def files(acct=Depends(investor), db: Session = Depends(get_db)):
    from app.services import wholesale_files as F
    return {"files": [DR.file_json(f) for f in DR.investor_files(db, acct)],
            "limits": {"max_mb": DR.max_file_bytes() // 1048576, "quota_mb": DR.buyer_quota_bytes() // 1048576,
                       "types": sorted(set(F.CONTENT_TYPES.values()) - {".doc", ".heic"})},
            "categories": list(DR.CATEGORIES), "capability": F.capability()}


@router.post("/files")
async def upload(file: UploadFile = File(...), category: str = Form("other"), listing_id: Optional[str] = Form(None),
                 acct=Depends(investor), db: Session = Depends(get_db)):
    from app.services import wholesale_exceptions as EX
    from app.services import wholesale_files as F
    deal_id = None
    if listing_id:
        _lst, deal, _m, _h = DR.get_visible(db, acct, listing_id)
        deal_id = deal.id
    data, ctype, name = await F.read_upload(file)
    b = DR.buyer_of(db, acct)
    f = DR.store_upload(db, acct.organization_id, buyer=b, data=data, content_type=ctype, filename=name,
                        kind="investor_upload", category=category, deal_id=deal_id, by_account=acct)
    EX.raise_exception(db, acct.organization_id, kind="deal_room_upload_review", subject_type="buyer",
                       subject_id=b.id, title="Review %s's upload: %s" % (b.company_name or b.contact_name, name),
                       detail="%s, %.1f MB" % (category, len(data) / 1048576.0), priority=35, source="deal_room",
                       is_test=bool(b.is_test), assigned_to_id=acct.assigned_user_id)
    DR.log(db, acct.organization_id, "deal_room.file_uploaded", "Investor uploaded %s (%s)" % (name, category),
           deal_id=deal_id, details={"file_id": f.id, "buyer_id": b.id, "bytes": len(data)})
    DR.notify_staff(db, acct.organization_id, "Deal Room upload from %s: %s" % (b.company_name or b.contact_name, name),
                    "/wholesale/buyers?buyer=%s" % b.id, assignee_id=acct.assigned_user_id)
    db.commit()
    return DR.file_json(f)


@router.get("/files/{file_id}")
def download(file_id: str, acct=Depends(investor), db: Session = Depends(get_db)):
    from app.services import wholesale_files as F
    f = DR.investor_file(db, acct, file_id)
    return Response(F.fetch(f.storage_backend, f.storage_key, db=db), media_type=f.content_type,
                    headers={"Content-Disposition": 'attachment; filename="%s"' % (f.original_filename or "file"),
                             "Cache-Control": "private, no-store"})


# ── signatures ─────────────────────────────────────────────────────────────

@router.get("/signatures")
def signatures(acct=Depends(investor), db: Session = Depends(get_db)):
    return {"documents": DR.investor_documents(db, acct)}


@router.post("/signatures/{envelope_id}/session")
def signing_session(envelope_id: str, acct=Depends(investor), db: Session = Depends(get_db)):
    r = DR.signing_session(db, acct, envelope_id)
    db.commit()
    return r
