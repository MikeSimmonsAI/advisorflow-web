"""Investor deal package (R3).

Operator (login):
    GET  /wholesale/deals/{id}/package               status, content, what is missing, history
    POST /wholesale/deals/{id}/package               create (or re-create) the package - Draft
    POST /wholesale/deals/{id}/package/submit        send to the Approval Center - Review
    POST /wholesale/deals/{id}/package/publish       approved content only - Published
    POST /wholesale/deals/{id}/package/withdraw      take the investor page down - Withdrawn
    GET  /wholesale/deals/{id}/package.pdf           the PDF; watermarked unless the content is approved
Investor (the existing share link, only while published):
    GET  /wholesale-rooms/buyer/{token}/package.pdf  same whitelisted content, download recorded as a view
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.limiter import limiter
from app.models.models import User
from app.services import wholesale_deal_package as PK
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale", tags=["wholesale-package"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])
public_router = APIRouter(prefix="/wholesale-rooms", tags=["wholesale-rooms"])


def _read(db, user, deal_id):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return org_id, svc.get_deal(db, org_id, deal_id)


def _write(db, user, deal_id):
    org_id = svc.write_org_id(db, user)
    return org_id, svc.get_deal(db, org_id, deal_id)


@router.get("/deals/{deal_id}/package")
def get_package(deal_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id, deal = _read(db, user, deal_id)
    return PK.status(db, org_id, deal)


@router.post("/deals/{deal_id}/package")
def create_package(deal_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                   _g: User = Depends(require_not_observation)):
    org_id, deal = _write(db, user, deal_id)
    out = PK.create(db, org_id, deal, user)
    db.commit()
    return out


@router.post("/deals/{deal_id}/package/submit")
def submit_package(deal_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                   _g: User = Depends(require_not_observation)):
    org_id, deal = _write(db, user, deal_id)
    out = PK.submit(db, org_id, deal, user)
    db.commit()
    return out


@router.post("/deals/{deal_id}/package/publish")
def publish_package(deal_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                    _g: User = Depends(require_not_observation)):
    org_id, deal = _write(db, user, deal_id)
    out = PK.publish(db, org_id, deal, user)
    db.commit()
    return out


class WithdrawIn(BaseModel):
    reason: Optional[str] = None


@router.post("/deals/{deal_id}/package/withdraw")
def withdraw_package(deal_id: str, payload: WithdrawIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id, deal = _write(db, user, deal_id)
    out = PK.withdraw(db, org_id, deal, user, (payload.reason or "").strip()[:200] or None)
    db.commit()
    return out


def _pdf(data: bytes, name: str) -> Response:
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="%s"' % name,
                             "Cache-Control": "no-store"})


@router.get("/deals/{deal_id}/package.pdf")
def package_pdf(deal_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id, deal = _read(db, user, deal_id)
    st = PK.status(db, org_id, deal)
    ok = st["approved_hash"] is not None and not st["changed_since_approval"]
    return _pdf(PK.pdf_bytes(st["content"], watermark=None if ok else "DRAFT - NOT APPROVED"), "deal-package.pdf")


@public_router.get("/buyer/{token}/package.pdf")
@limiter.limit("20/minute")
def investor_package_pdf(token: str, request: Request, db: Session = Depends(get_db)):
    from app.routers.wholesale_rooms_router import AUDIENCE_BUYER, _record_view, _resolve
    link, deal = _resolve(db, token, AUDIENCE_BUYER)        # same refusal as the page: unpublished = not found
    data = PK.pdf_bytes(PK.content(db, deal))
    _record_view(db, link, deal, request, action="pdf_downloaded")
    db.commit()
    return _pdf(data, "deal-package.pdf")
