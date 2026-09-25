# -*- coding: utf-8 -*-
"""The investor room, the seller page, and the boundary between them.

TWO ROUTERS IN ONE FILE, AND THE SPLIT IS THE SECURITY MODEL.

    router         /wholesale/...        operators. Same three gates as every
                                         other wholesale route: tenant user,
                                         feature, and no writes from an
                                         observer.

    public_router  /wholesale-rooms/...  investors and sellers. NO login, NO
                                         organization context, NO feature gate
                                         to satisfy — the token in the URL is
                                         the entire authorisation, and every
                                         endpoint re-derives the organization
                                         FROM that token rather than trusting
                                         anything the caller sent.

They are deliberately not under one prefix. `/wholesale` is mounted with
`require_feature`, and a public route living inside that prefix would be one
refactor away from either inheriting a gate it cannot satisfy or, far worse,
having the gate quietly removed for everything else.

WHAT A TOKEN CAN AND CANNOT DO
------------------------------
It names ONE deal and ONE audience. There is no "list my deals", no org switch,
no id parameter that widens the view, and no write except the small set of
actions that audience is allowed to take. Every payload is built by
`wholesale_publication`, which is a whitelist — see that module for why.

WHAT IS NOT FAKED HERE
----------------------
Nothing in this file sends an email, a text message or a signature request. A
link is minted and shown to the operator to deliver however they already
deliver things. When outbound email for buyer disposition is switched on in a
deployment, the existing gated disposition path is what sends it; this file
does not grow a second outbound surface.
"""
import logging
import secrets
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import (get_db, require_not_observation, require_tenant_or_observer,
                      require_tenant_user)
from app.limiter import limiter
from app.models.models import User
from app.models.wholesale_models import (ACTOR_API, ACTOR_USER,
                                         WholesaleBuyer, WholesaleBuyerOutreach,
                                         WholesaleDeal, WholesaleDocument,
                                         WholesaleFile)
from app.models.wholesale_share_models import (AUDIENCE_BUYER, AUDIENCE_SELLER,
                                               AUDIENCES, BUYER_ACTIONS,
                                               WholesaleShareLink,
                                               WholesaleShareView)
from app.services import wholesale_files as files
from app.services import wholesale_publication as pub
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

log = logging.getLogger(__name__)

FEATURE = "wholesale_real_estate"

router = APIRouter(prefix="/wholesale", tags=["wholesale"],
                   dependencies=[Depends(require_feature(FEATURE))])

public_router = APIRouter(prefix="/wholesale-rooms", tags=["wholesale-rooms"])


# ── Helpers ────────────────────────────────────────────────────────────────

def _own_deal(db: Session, org_id: str, deal_id: str) -> WholesaleDeal:
    row = (db.query(WholesaleDeal)
           .filter(WholesaleDeal.id == deal_id,
                   WholesaleDeal.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    return row


def _new_token() -> str:
    """43 url-safe characters from the OS. Not a uuid, and never a row id.

    `secrets`, not `random`, for the same reason `appointment_invites` uses it:
    this string is the only thing standing between a stranger and somebody's
    transaction.
    """
    return secrets.token_urlsafe(32)


def link_json(link: WholesaleShareLink, base_path: str) -> Dict[str, Any]:
    """What the operator screen may know about a link.

    The token IS included, because the operator has to be able to copy the URL
    and send it. That is not a leak: they already have full access to the deal.
    """
    return {
        "id": link.id,
        "audience": link.audience,
        "buyer_id": link.buyer_id,
        "outreach_id": link.outreach_id,
        "recipient_name": link.recipient_name,
        "recipient_email": link.recipient_email,
        "url_path": "%s/%s" % (base_path, link.token),
        "token": link.token,
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
        "revoked_at": link.revoked_at.isoformat() if link.revoked_at else None,
        "first_viewed_at": (link.first_viewed_at.isoformat()
                            if link.first_viewed_at else None),
        "last_viewed_at": (link.last_viewed_at.isoformat()
                           if link.last_viewed_at else None),
        "view_count": link.view_count or 0,
        "active": link.revoked_at is None and (
            link.expires_at is None or link.expires_at > datetime.utcnow()),
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }


def _base_path(audience: str) -> str:
    return "/investor" if audience == AUDIENCE_BUYER else "/my-property"


def _resolve(db: Session, token: str, audience: str):
    """Token -> (link, deal). Every refusal says the same thing on purpose.

    A revoked link, an expired link, a link for the other audience and a link
    that never existed all produce ONE message. Telling a stranger which of
    those it was is telling them whether they guessed a real token.
    """
    now = datetime.utcnow()
    link = (db.query(WholesaleShareLink)
            .filter(WholesaleShareLink.token == token,
                    WholesaleShareLink.audience == audience).first())
    if link is None or link.revoked_at is not None or (
            link.expires_at is not None and link.expires_at <= now):
        raise HTTPException(status_code=404,
                            detail="This link is no longer available.")

    deal = (db.query(WholesaleDeal)
            .filter(WholesaleDeal.id == link.deal_id,
                    WholesaleDeal.organization_id == link.organization_id).first())
    if deal is None:
        raise HTTPException(status_code=404,
                            detail="This link is no longer available.")

    # Publication is checked here, not in the payload builder, so an
    # unpublished deal is indistinguishable from a bad token.
    published = (deal.buyer_room_published if audience == AUDIENCE_BUYER
                 else deal.seller_room_published)
    if not published:
        raise HTTPException(status_code=404,
                            detail="This link is no longer available.")
    return link, deal


def _record_view(db: Session, link: WholesaleShareLink, deal: WholesaleDeal,
                 request: Request, action: str = "view",
                 detail: Optional[str] = None, amount=None) -> None:
    now = datetime.utcnow()
    if link.first_viewed_at is None:
        link.first_viewed_at = now
    link.last_viewed_at = now
    link.view_count = (link.view_count or 0) + 1

    ip = getattr(getattr(request, "client", None), "host", None) or ""
    # First three octets only. Enough to tell two devices apart in a dispute,
    # short of keeping a full address for somebody who never signed up here.
    prefix = ".".join(ip.split(".")[:3]) if "." in ip else ip[:16]
    agent = (request.headers.get("user-agent") or "")[:255]

    db.add(WholesaleShareView(
        organization_id=link.organization_id, share_link_id=link.id,
        deal_id=deal.id, action=action, detail=detail, amount=amount,
        ip_prefix=prefix or None, user_agent=agent or None))


# ══════════════════════════════════════════════════════════════════════════
#  OPERATOR SURFACE — publication, links, and who has looked
# ══════════════════════════════════════════════════════════════════════════

class PublicationIn(BaseModel):
    """Everything a person can choose to publish. All optional, all opt-in."""
    buyer_room_summary: Optional[str] = None
    buyer_room_condition: Optional[str] = None
    buyer_room_asking_price: Optional[float] = None
    buyer_room_show_arv: Optional[bool] = None
    buyer_room_show_repairs: Optional[bool] = None
    buyer_room_show_comps: Optional[bool] = None
    seller_room_message: Optional[str] = None
    seller_room_contact_name: Optional[str] = None
    seller_room_contact_phone: Optional[str] = None
    seller_room_contact_email: Optional[str] = None


def _publication_json(db: Session, deal: WholesaleDeal) -> Dict[str, Any]:
    photos = (db.query(WholesaleFile)
              .filter(WholesaleFile.organization_id == deal.organization_id,
                      WholesaleFile.property_id == deal.property_id,
                      WholesaleFile.kind == "property_photo").all()
              if deal.property_id else [])
    docs = (db.query(WholesaleDocument)
            .filter(WholesaleDocument.organization_id == deal.organization_id,
                    WholesaleDocument.deal_id == deal.id).all())
    links = (db.query(WholesaleShareLink)
             .filter(WholesaleShareLink.organization_id == deal.organization_id,
                     WholesaleShareLink.deal_id == deal.id)
             .order_by(WholesaleShareLink.created_at.desc()).all())
    return {
        "buyer": {
            "published": bool(deal.buyer_room_published),
            "published_at": (deal.buyer_room_published_at.isoformat()
                             if deal.buyer_room_published_at else None),
            "summary": deal.buyer_room_summary,
            "condition": deal.buyer_room_condition,
            "asking_price": (float(deal.buyer_room_asking_price)
                             if deal.buyer_room_asking_price is not None else None),
            "show_arv": bool(deal.buyer_room_show_arv),
            "show_repairs": bool(deal.buyer_room_show_repairs),
            "show_comps": bool(deal.buyer_room_show_comps),
            "photo_count": sum(1 for p in photos if p.buyer_visible),
            "document_count": sum(1 for d in docs if d.buyer_visible),
        },
        "seller": {
            "published": bool(deal.seller_room_published),
            "published_at": (deal.seller_room_published_at.isoformat()
                             if deal.seller_room_published_at else None),
            "message": deal.seller_room_message,
            "contact_name": deal.seller_room_contact_name,
            "contact_phone": deal.seller_room_contact_phone,
            "contact_email": deal.seller_room_contact_email,
            "document_count": sum(1 for d in docs if d.seller_visible),
        },
        "photo_total": len(photos),
        "document_total": len(docs),
        "links": [link_json(x, _base_path(x.audience)) for x in links],
    }


@router.get("/deals/{deal_id}/publication")
def get_publication(deal_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    """What is currently published, to whom, and who has opened it."""
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409,
                            detail="No customer organization selected.")
    return _publication_json(db, _own_deal(db, org_id, deal_id))


@router.patch("/deals/{deal_id}/publication")
def set_publication(deal_id: str, payload: PublicationIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    """Edit what WOULD be published. Editing is not publishing."""
    org_id = svc.write_org_id(db, user)
    deal = _own_deal(db, org_id, deal_id)
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(deal, key, value)
    svc.log_event(db, org_id, "publication.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="Published content edited")
    db.commit()
    db.refresh(deal)
    return _publication_json(db, deal)


class PublishStateIn(BaseModel):
    """One decision, two parameters. See the note in the test guard for why
    this is a single route rather than publish/ and unpublish/ per audience."""
    audience: str
    published: bool


@router.post("/deals/{deal_id}/publication/state")
def set_publication_state(deal_id: str, payload: PublishStateIn, request: Request,
                          db: Session = Depends(get_db),
                          user: User = Depends(require_tenant_user),
                          _guard: User = Depends(require_not_observation)):
    """Turn a room on or off. Deliberate, audited, and reversible.

    Unpublishing does not revoke the links. It stops them resolving, which is
    the faster and safer of the two: a link can be turned back on for the same
    recipient without re-sending anything.
    """
    if payload.audience not in AUDIENCES:
        raise HTTPException(status_code=400, detail="Unknown audience.")
    org_id = svc.write_org_id(db, user)
    deal = _own_deal(db, org_id, deal_id)
    now = datetime.utcnow()

    if payload.audience == AUDIENCE_BUYER:
        deal.buyer_room_published = bool(payload.published)
        if payload.published:
            deal.buyer_room_published_at = now
            deal.buyer_room_published_by_id = user.id
    else:
        deal.seller_room_published = bool(payload.published)
        if payload.published:
            deal.seller_room_published_at = now
            deal.seller_room_published_by_id = user.id

    svc.log_event(db, org_id,
                  "publication.published" if payload.published
                  else "publication.unpublished",
                  actor_type=ACTOR_USER, actor_user_id=user.id, deal_id=deal.id,
                  summary="%s room %s" % (payload.audience.capitalize(),
                                          "published" if payload.published
                                          else "unpublished"))
    db.commit()
    db.refresh(deal)
    return _publication_json(db, deal)


class ShareLinkIn(BaseModel):
    audience: str = AUDIENCE_BUYER
    buyer_id: Optional[str] = None
    recipient_name: Optional[str] = None
    recipient_email: Optional[str] = None
    # None means it does not expire on its own. It can always be revoked.
    expires_in_days: Optional[int] = 30


@router.post("/deals/{deal_id}/share-links")
def create_share_link(deal_id: str, payload: ShareLinkIn, request: Request,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user),
                      _guard: User = Depends(require_not_observation)):
    """Mint one link. It is shown once here and stored; nothing is emailed."""
    if payload.audience not in AUDIENCES:
        raise HTTPException(status_code=400, detail="Unknown audience.")
    org_id = svc.write_org_id(db, user)
    deal = _own_deal(db, org_id, deal_id)

    outreach_id = None
    buyer = None
    if payload.audience == AUDIENCE_BUYER:
        if not payload.buyer_id:
            raise HTTPException(
                status_code=400,
                detail="An investor link has to name which buyer it is for.")
        buyer = (db.query(WholesaleBuyer)
                 .filter(WholesaleBuyer.id == payload.buyer_id,
                         WholesaleBuyer.organization_id == org_id).first())
        if buyer is None:
            raise HTTPException(status_code=404, detail="Buyer not found")
        # Their actions land on the outreach row that already pairs this deal
        # with this buyer, so an investor reply and an operator-recorded reply
        # are the same record rather than two versions of the truth.
        row = (db.query(WholesaleBuyerOutreach)
               .filter(WholesaleBuyerOutreach.organization_id == org_id,
                       WholesaleBuyerOutreach.deal_id == deal.id,
                       WholesaleBuyerOutreach.buyer_id == buyer.id)
               .order_by(WholesaleBuyerOutreach.created_at.desc()).first())
        outreach_id = row.id if row else None

    expires_at = None
    if payload.expires_in_days:
        expires_at = datetime.utcnow() + timedelta(days=int(payload.expires_in_days))

    link = WholesaleShareLink(
        organization_id=org_id, deal_id=deal.id, audience=payload.audience,
        buyer_id=payload.buyer_id if payload.audience == AUDIENCE_BUYER else None,
        outreach_id=outreach_id, token=_new_token(),
        recipient_name=payload.recipient_name or (buyer.company_name if buyer else None),
        recipient_email=payload.recipient_email,
        expires_at=expires_at, created_by_id=user.id)
    db.add(link)
    db.flush()
    svc.log_event(db, org_id, "share_link.created", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="%s link created for %s"
                          % (payload.audience.capitalize(),
                             link.recipient_name or "an external party"))
    db.commit()
    db.refresh(link)
    return link_json(link, _base_path(link.audience))


@router.post("/share-links/{link_id}/revoke")
def revoke_share_link(link_id: str, request: Request,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user),
                      _guard: User = Depends(require_not_observation)):
    """Kill a link. The next request on it 404s like any other dead token."""
    org_id = svc.write_org_id(db, user)
    link = (db.query(WholesaleShareLink)
            .filter(WholesaleShareLink.id == link_id,
                    WholesaleShareLink.organization_id == org_id).first())
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
    link.revoked_at = datetime.utcnow()
    link.revoked_by_id = user.id
    svc.log_event(db, org_id, "share_link.revoked", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=link.deal_id,
                  summary="Link revoked for %s"
                          % (link.recipient_name or "an external party"))
    db.commit()
    db.refresh(link)
    return link_json(link, _base_path(link.audience))


@router.get("/deals/{deal_id}/share-activity")
def share_activity(deal_id: str, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer)):
    """Did they open it, and what did they do — for this deal's links only."""
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409,
                            detail="No customer organization selected.")
    deal = _own_deal(db, org_id, deal_id)
    rows = (db.query(WholesaleShareView)
            .filter(WholesaleShareView.organization_id == org_id,
                    WholesaleShareView.deal_id == deal.id)
            .order_by(WholesaleShareView.created_at.desc()).limit(100).all())
    names = {x.id: (x.recipient_name or x.audience)
             for x in db.query(WholesaleShareLink)
             .filter(WholesaleShareLink.deal_id == deal.id).all()}
    return {"activity": [{
        "id": r.id,
        "who": names.get(r.share_link_id) or "external",
        "action": r.action,
        "detail": r.detail,
        "amount": float(r.amount) if r.amount is not None else None,
        "at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]}


# ══════════════════════════════════════════════════════════════════════════
#  INVESTOR SURFACE — no login, one deal, published content only
# ══════════════════════════════════════════════════════════════════════════

@public_router.get("/buyer/{token}")
@limiter.limit("60/minute")
def buyer_room(token: str, request: Request, db: Session = Depends(get_db)):
    link, deal = _resolve(db, token, AUDIENCE_BUYER)
    outreach = None
    if link.outreach_id:
        outreach = (db.query(WholesaleBuyerOutreach)
                    .filter(WholesaleBuyerOutreach.id == link.outreach_id).first())
    payload = pub.buyer_room_payload(db, deal, link=link, outreach=outreach)
    _record_view(db, link, deal, request)
    db.commit()
    return payload


class BuyerActionIn(BaseModel):
    action: str
    amount: Optional[float] = None
    message: Optional[str] = None
    # Phase 6. What an offer actually consists of. An amount with no closing
    # date and no idea how it is funded is not an offer an operator can act on,
    # and the old form collected exactly that.
    #
    # THREE OF THESE LAND ON COLUMNS THAT ALREADY EXISTED — `target_close_date`
    # and `pof_status` were both on the outreach row from Phase 3. Only
    # `financing` and the respondent's own contact details are new. Nothing
    # here builds a parallel record of an offer.
    closing_date: Optional[str] = None      # ISO date the buyer proposes
    financing: Optional[str] = None         # see FINANCING
    proof_of_funds: Optional[str] = None    # see POF_CLAIMS
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None


# What an investor may say about their money. Deliberately short, and every
# value is a CLAIM the investor made rather than anything this platform has
# checked — which is why the operator's own proof-of-funds vocabulary keeps a
# separate `verified` state that only a person can set.
FINANCING = ("cash", "hard_money", "conventional", "other")
POF_CLAIMS = ("on_file", "can_provide", "not_yet")

# The investor's claim, mapped onto the operator's existing vocabulary.
# `claimed` is not `verified`: somebody still has to look at the document.
_POF_FROM_CLAIM = {
    "on_file": "claimed",
    "can_provide": "requested",
    "not_yet": "none",
}


@public_router.post("/buyer/{token}/action")
@limiter.limit("20/minute")
def buyer_action(token: str, payload: BuyerActionIn, request: Request,
                 db: Session = Depends(get_db)):
    """An investor replies, and a REAL record changes.

    Every action below writes to the outreach row the operator already reads on
    the buyer board. There is no separate "portal response" table, because two
    tables holding the same answer is how an operator ends up looking at the
    stale one.
    """
    if payload.action not in BUYER_ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown action.")
    link, deal = _resolve(db, token, AUDIENCE_BUYER)

    outreach = None
    if link.outreach_id:
        outreach = (db.query(WholesaleBuyerOutreach)
                    .filter(WholesaleBuyerOutreach.id == link.outreach_id).first())
    if outreach is None and link.buyer_id:
        outreach = (db.query(WholesaleBuyerOutreach)
                    .filter(WholesaleBuyerOutreach.organization_id == link.organization_id,
                            WholesaleBuyerOutreach.deal_id == deal.id,
                            WholesaleBuyerOutreach.buyer_id == link.buyer_id)
                    .order_by(WholesaleBuyerOutreach.created_at.desc()).first())
    if outreach is None:
        # No deal sheet was ever sent to this buyer, so there is nothing to
        # answer. Refusing is honest; inventing an outreach row would put a
        # "sent" record on a board for something nobody sent.
        raise HTTPException(
            status_code=409,
            detail="This deal has not been sent to you, so there is nothing "
                   "to respond to yet.")

    now = datetime.utcnow()
    note = (payload.message or "").strip()[:2000] or None
    status_for = {
        "interested": "interested",
        "offer": "offer_submitted",
        "pass": "passed",
        "walkthrough": "requested_info",
        "question": "requested_info",
    }
    outreach.status = status_for[payload.action]
    outreach.replied_at = now
    if payload.action == "offer":
        if payload.amount is None:
            raise HTTPException(status_code=400,
                                detail="An offer needs an amount.")
        outreach.offer_amount = payload.amount

        # ── The rest of the offer, onto the columns that already hold it ──
        if payload.closing_date:
            try:
                outreach.target_close_date = date.fromisoformat(
                    str(payload.closing_date)[:10])
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="The closing date must be a date, e.g. 2026-11-08.")
        if payload.financing:
            if payload.financing not in FINANCING:
                raise HTTPException(status_code=400,
                                    detail="Unknown financing option.")
            outreach.offer_financing = payload.financing
        if payload.proof_of_funds:
            if payload.proof_of_funds not in POF_CLAIMS:
                raise HTTPException(status_code=400,
                                    detail="Unknown proof-of-funds option.")
            # NOT `verified`. The investor said it; nobody has checked it.
            outreach.pof_status = _POF_FROM_CLAIM[payload.proof_of_funds]

    # Who filled the form in. Recorded against THIS response and nowhere else:
    # a public page does not get to rewrite the contact details the operator
    # keeps on the buyer in their own CRM.
    for field, value in (("respondent_name", payload.contact_name),
                         ("respondent_email", payload.contact_email),
                         ("respondent_phone", payload.contact_phone)):
        clean = (value or "").strip()[:200]
        if clean:
            setattr(outreach, field, clean)
    if note:
        prefix = {"walkthrough": "Walkthrough requested: ",
                  "question": "Question: "}.get(payload.action, "")
        outreach.response_note = (prefix + note)[:2000]
    elif payload.action == "walkthrough":
        outreach.response_note = "Walkthrough requested."

    who = link.recipient_name or "an investor"
    summary = {
        "interested": "%s is interested" % who,
        "offer": "%s offered %s" % (who, payload.amount),
        "pass": "%s passed" % who,
        "walkthrough": "%s asked for a walkthrough" % who,
        "question": "%s asked a question" % who,
    }[payload.action]
    svc.log_event(db, link.organization_id, "buyer.responded",
                  actor_type=ACTOR_API, actor_label=who, deal_id=deal.id,
                  summary=summary)
    _record_view(db, link, deal, request, action=payload.action,
                 detail=note, amount=payload.amount)
    db.commit()
    return {"recorded": True, "status": outreach.status,
            "your_offer": (float(outreach.offer_amount)
                           if outreach.offer_amount is not None else None)}


@public_router.get("/buyer/{token}/photo/{file_id}")
@limiter.limit("240/minute")
def buyer_photo(token: str, file_id: str, request: Request,
                db: Session = Depends(get_db)):
    """One published photo. Three checks, all on the row, none on the URL."""
    link, deal = _resolve(db, token, AUDIENCE_BUYER)
    row = (db.query(WholesaleFile)
           .filter(WholesaleFile.id == file_id,
                   WholesaleFile.organization_id == link.organization_id,
                   WholesaleFile.property_id == deal.property_id,
                   WholesaleFile.kind == "property_photo",
                   WholesaleFile.buyer_visible.is_(True)).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _bytes(row)


@public_router.get("/buyer/{token}/document/{document_id}")
@limiter.limit("60/minute")
def buyer_document(token: str, document_id: str, request: Request,
                   db: Session = Depends(get_db)):
    link, deal = _resolve(db, token, AUDIENCE_BUYER)
    return _document_bytes(db, link, deal, document_id, "buyer_visible")


# ══════════════════════════════════════════════════════════════════════════
#  SELLER SURFACE — no login, their own transaction, nothing else
# ══════════════════════════════════════════════════════════════════════════

@public_router.get("/seller/{token}")
@limiter.limit("60/minute")
def seller_room(token: str, request: Request, db: Session = Depends(get_db)):
    link, deal = _resolve(db, token, AUDIENCE_SELLER)
    payload = pub.seller_room_payload(db, deal, link=link)
    _record_view(db, link, deal, request)
    db.commit()
    return payload


@public_router.get("/seller/{token}/photo/{file_id}")
@limiter.limit("240/minute")
def seller_photo(token: str, file_id: str, request: Request,
                 db: Session = Depends(get_db)):
    """One photo published to the OWNER. Same three checks as the buyer route.

    Note the filter: `seller_visible`, not `buyer_visible`. The two routes are
    written out separately rather than sharing a parameterised helper for
    exactly one reason — a single helper taking the column name is one wrong
    argument away from serving an investor gallery to a seller link, and this
    is the last place in the module where that mistake should be possible.
    """
    link, deal = _resolve(db, token, AUDIENCE_SELLER)
    row = (db.query(WholesaleFile)
           .filter(WholesaleFile.id == file_id,
                   WholesaleFile.organization_id == link.organization_id,
                   WholesaleFile.property_id == deal.property_id,
                   WholesaleFile.kind == "property_photo",
                   WholesaleFile.seller_visible.is_(True)).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _bytes(row)


@public_router.get("/seller/{token}/document/{document_id}")
@limiter.limit("60/minute")
def seller_document(token: str, document_id: str, request: Request,
                    db: Session = Depends(get_db)):
    link, deal = _resolve(db, token, AUDIENCE_SELLER)
    return _document_bytes(db, link, deal, document_id, "seller_visible")


# ── Byte serving, shared by both audiences ─────────────────────────────────

def _bytes(row: WholesaleFile) -> Response:
    data = files.fetch(row.storage_backend, row.storage_key)
    safe = (row.original_filename or "file").replace('"', "").replace("\n", "")
    inline = (row.content_type or "").startswith("image/")
    return Response(
        content=data,
        media_type=row.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": '%s; filename="%s"'
                                   % ("inline" if inline else "attachment", safe),
            # Never a shared cache entry: the URL is the credential.
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _document_bytes(db: Session, link: WholesaleShareLink, deal: WholesaleDeal,
                    document_id: str, visibility_field: str) -> Response:
    column = getattr(WholesaleDocument, visibility_field)
    doc = (db.query(WholesaleDocument)
           .filter(WholesaleDocument.id == document_id,
                   WholesaleDocument.organization_id == link.organization_id,
                   WholesaleDocument.deal_id == deal.id,
                   column.is_(True)).first())
    if doc is None or not doc.file_id:
        raise HTTPException(status_code=404, detail="Not found")
    row = (db.query(WholesaleFile)
           .filter(WholesaleFile.id == doc.file_id,
                   WholesaleFile.organization_id == link.organization_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    # An external party opening a document is a fact worth keeping: it is the
    # only "viewed" this deployment can honestly prove without a provider.
    if doc.viewed_at is None:
        doc.viewed_at = datetime.utcnow()
        db.commit()
    return _bytes(row)
