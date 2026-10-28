"""God-granted demo previews (deal_preview_grants).

God side - grant, list, revoke. require_god on every route; every change is
written to the audit log.

Grantee side - GET /program-access/previews/{grant_id}. A signed-in route
that re-checks the grant for the caller on every open, writes an audit entry,
does NOT count a prospect view, and returns the demo page exactly as it is
stored ({title, html}) for the same sandboxed frame the public demo page uses.
It never returns the demo's token or public address, and it reaches nothing
else of the deal's brand.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_god
from app.models.demo_site_models import DealPreviewGrant
from app.models.models import User
from app.routers.audit_log_router import log_action
from app.services import preview_grants as pg

router = APIRouter(tags=["preview-grants"])

# One indistinguishable answer for every "not yours / not any more" case, so
# the open route cannot be used to learn which grant ids exist.
_GONE = "This preview is not available."


class GrantIn(BaseModel):
    opportunity_id: str
    # The person: by id, or by sign-in email (exact, case-insensitive).
    grantee_user_id: Optional[str] = None
    grantee_email: Optional[str] = None
    # The brand they work under: by id, or by its platform slug.
    platform_id: Optional[str] = None
    platform_slug: Optional[str] = None
    reason: str = Field(..., min_length=1, max_length=2000)
    # Either a fixed end date, or a number of days from now. Neither = until
    # revoked (the preview still closes by itself with the deal or its demo).
    expires_at: Optional[datetime] = None
    expires_in_days: Optional[int] = Field(None, ge=1, le=365)
    # Check everything and show what would be created, without writing.
    dry_run: bool = False


class RevokeIn(BaseModel):
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/god/preview-grants")
def create_grant(body: GrantIn, user: User = Depends(require_god),
                 db: Session = Depends(get_db)):
    now = datetime.utcnow()
    expires = body.expires_at
    if expires is not None and expires.tzinfo is not None:
        expires = expires.replace(tzinfo=None) - (expires.utcoffset() or timedelta(0))
    if body.expires_in_days:
        expires = now + timedelta(days=body.expires_in_days)
    grantee_id = body.grantee_user_id
    if not grantee_id and body.grantee_email:
        from sqlalchemy import func
        hit = db.query(User.id).filter(
            func.lower(User.email) == body.grantee_email.strip().lower()).first()
        grantee_id = hit[0] if hit else None
    platform_id = body.platform_id
    if not platform_id and body.platform_slug:
        from app.models.models import Platform
        hit = db.query(Platform.id).filter(Platform.slug == body.platform_slug.strip().lower()).first()
        platform_id = hit[0] if hit else None
    if not grantee_id or not platform_id:
        raise HTTPException(status_code=422, detail="Name the person and the brand.")
    try:
        row = pg.create(db, user, opportunity_id=body.opportunity_id,
                        grantee_user_id=grantee_id,
                        platform_id=platform_id, reason=body.reason,
                        expires_at=expires, now=now)
    except pg.GrantError as e:
        db.rollback()
        raise HTTPException(status_code=e.status, detail=e.message)
    if body.dry_run:
        preview = pg.out(db, row)
        db.rollback()
        preview["id"] = None
        return {"dry_run": True, "would_create": preview}
    log_action(db, None, user.id, action="preview.grant",
               target_type="opportunity", target_id=row.opportunity_id,
               platform_id=row.grantee_platform_id,
               after={"grant_id": row.id, "grantee_user_id": row.grantee_user_id,
                      "expires_at": row.expires_at.isoformat() if row.expires_at else None},
               note=row.reason, commit=False)
    db.commit()
    return {"grant": pg.out(db, row)}


@router.get("/god/preview-grants")
def list_grants(opportunity_id: Optional[str] = Query(None),
                grantee_user_id: Optional[str] = Query(None),
                user: User = Depends(require_god), db: Session = Depends(get_db)):
    q = db.query(DealPreviewGrant)
    if opportunity_id:
        q = q.filter(DealPreviewGrant.opportunity_id == opportunity_id)
    if grantee_user_id:
        q = q.filter(DealPreviewGrant.grantee_user_id == grantee_user_id)
    rows = q.order_by(DealPreviewGrant.created_at.desc()).limit(500).all()
    return {"grants": [pg.out(db, g) for g in rows]}


@router.post("/god/preview-grants/{grant_id}/revoke")
def revoke_grant(grant_id: str, body: Optional[RevokeIn] = None,
                 user: User = Depends(require_god), db: Session = Depends(get_db)):
    g = db.query(DealPreviewGrant).filter(DealPreviewGrant.id == grant_id).first()
    if g is None:
        raise HTTPException(status_code=404, detail="Grant not found")
    if g.revoked_at is not None:
        return {"grant": pg.out(db, g), "already_revoked": True}
    pg.revoke(db, g, user)
    log_action(db, None, user.id, action="preview.revoke",
               target_type="opportunity", target_id=g.opportunity_id,
               platform_id=g.grantee_platform_id,
               after={"grant_id": g.id, "grantee_user_id": g.grantee_user_id},
               note=(body.note if body else None), commit=False)
    db.commit()
    return {"grant": pg.out(db, g), "already_revoked": False}


@router.get("/program-access/previews/{grant_id}")
def open_preview(grant_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    g = db.query(DealPreviewGrant).filter(DealPreviewGrant.id == grant_id).first()
    ok = pg.usable(db, g, user)
    if ok is None:
        raise HTTPException(status_code=404, detail=_GONE)
    opp, demo = ok
    # Read straight from the row: no view is recorded (this is staff looking,
    # not the prospect), and the stored page is returned exactly as stored.
    # organization_id None: this is the deal brand's record, never written
    # into the grantee's own tenant audit view.
    log_action(db, None, user.id,
               action="preview.opened", target_type="opportunity",
               target_id=opp.id, platform_id=g.grantee_platform_id,
               details={"grant_id": g.id, "demo_slot": demo.slot or "platform"},
               commit=False)
    db.commit()
    return {"title": demo.title, "html": demo.html, "label": pg.PREVIEW_LABEL}
