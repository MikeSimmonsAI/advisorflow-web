"""
Client Proposal Portal Router
──────────────────────────────
Two distinct surfaces with completely separate auth patterns:

ADMIN SURFACE  (requires org_admin or above, same JWT as rest of app)
  POST   /proposals/                        create proposal
  GET    /proposals/                        list all for org
  GET    /proposals/{id}                    detail
  PATCH  /proposals/{id}                    update metadata
  DELETE /proposals/{id}                    soft-delete
  POST   /proposals/{id}/publish            set status → published
  POST   /proposals/{id}/unpublish          set status → draft
  POST   /proposals/{id}/blocks             add a content block
  PATCH  /proposals/{id}/blocks/{block_id}  update block content/url
  DELETE /proposals/{id}/blocks/{block_id}  delete block
  POST   /proposals/{id}/blocks/reorder     reorder blocks by position list
  POST   /proposals/{id}/send               generate + email magic link
  GET    /proposals/{id}/analytics          view + download stats
  POST   /proposals/{id}/tokens/{tok_id}/revoke  revoke a sent link

CLIENT SURFACE  (NO internal JWT — magic-link token in URL)
  GET    /proposals/portal/resolve/{token}  validate token → return portal session payload
  POST   /proposals/portal/view/{view_id}/ping   heartbeat (scroll depth + duration)
  POST   /proposals/portal/view/{view_id}/close  session close (final duration + scroll)
  POST   /proposals/portal/view/{view_id}/download  mark download event

Branding note: this feature is EvoSys Pro only. No BookaBoost / AdvisorFlow
strings should appear in any response or email this router generates.
"""

import uuid
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.models.models import (
    User, Organization, Proposal, ProposalBlock, ProposalToken, ProposalView, ProposalFile,
)
from app.services.email_service import send_email
from app.services.platform_owner import tenant_write_org_id as _tenant_write_org_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/proposals", tags=["proposals"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _gen_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_proposal_or_404(db: Session, proposal_id: str, org_id: str) -> Proposal:
    p = (
        db.query(Proposal)
        .filter(
            Proposal.id == proposal_id,
            Proposal.organization_id == org_id,
            Proposal.deleted_at.is_(None),
        )
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return p


def _proposal_to_dict(p: Proposal) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "subtitle": p.subtitle,
        "client_name": p.client_name,
        "client_email": p.client_email,
        "client_company": p.client_company,
        "status": p.status,
        "branding_override": json.loads(p.branding_override) if p.branding_override else None,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "block_count": len(p.blocks),
    }


def _block_to_dict(b: ProposalBlock) -> dict:
    return {
        "id": b.id,
        "block_type": b.block_type,
        "position": b.position,
        "content": b.content,
        "file_url": b.file_url,
        "file_name": b.file_name,
        "file_size": b.file_size,
    }


# ── Request / Response schemas ─────────────────────────────────────────────────

class ProposalCreate(BaseModel):
    title: str
    subtitle: Optional[str] = None
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    client_company: Optional[str] = None


class ProposalUpdate(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    client_company: Optional[str] = None
    branding_override: Optional[dict] = None
    expires_at: Optional[str] = None  # ISO string


class BlockCreate(BaseModel):
    block_type: str   # text | image | pdf | video | divider | cta | website_url (live site embed)
    content: Optional[str] = None
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    position: Optional[int] = None  # if None, appended at end


class BlockUpdate(BaseModel):
    content: Optional[str] = None
    file_url: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None


class ReorderRequest(BaseModel):
    # List of block IDs in desired order (0-indexed after reorder)
    block_ids: List[str]


class SendInviteRequest(BaseModel):
    recipient_email: str
    recipient_name: Optional[str] = None
    # hours until magic link expires — default 72h
    expires_hours: int = 72
    # optional personal note appended to email body
    personal_note: Optional[str] = None
    # content protection: disables right-click, drag, download, text selection in portal
    protect_content: bool = False


class ViewPingRequest(BaseModel):
    scroll_pct: int  # 0–100
    elapsed_seconds: int


class ViewCloseRequest(BaseModel):
    scroll_pct: int
    elapsed_seconds: int


# ── Admin endpoints ────────────────────────────────────────────────────────────

@router.post("/")
def create_proposal(
    req: ProposalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = Proposal(
        id=_gen_id(),
        # Proposal.organization_id is NULLABLE, so a neutral owner does not
        # get a loud failure here - they get a proposal that belongs to
        # nobody and appears in no customer's list. tenant_write_org_id
        # turns that into a 409 naming the context to select.
        organization_id=_tenant_write_org_id(current_user),
        created_by_id=current_user.id,
        title=req.title,
        subtitle=req.subtitle,
        client_name=req.client_name,
        client_email=req.client_email,
        client_company=req.client_company,
        status="draft",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    logger.info("Proposal %s created by %s", p.id, current_user.id)
    return _proposal_to_dict(p)


@router.get("/")
def list_proposals(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    proposals = (
        db.query(Proposal)
        .filter(
            Proposal.organization_id == current_user.organization_id,
            Proposal.deleted_at.is_(None),
        )
        .order_by(Proposal.updated_at.desc())
        .all()
    )
    # Annotate each with view count and last opened
    results = []
    for p in proposals:
        d = _proposal_to_dict(p)
        d["view_count"] = len(p.views)
        d["last_viewed_at"] = (
            max((v.opened_at for v in p.views), default=None)
        )
        if d["last_viewed_at"]:
            d["last_viewed_at"] = d["last_viewed_at"].isoformat()
        results.append(d)
    return results


@router.get("/{proposal_id}")
def get_proposal(
    proposal_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    d = _proposal_to_dict(p)
    d["blocks"] = [_block_to_dict(b) for b in p.blocks]
    d["tokens"] = [
        {
            "id": t.id,
            "token": t.token,
            "recipient_email": t.recipient_email,
            "recipient_name": t.recipient_name,
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
            "first_redeemed_at": t.first_redeemed_at.isoformat() if t.first_redeemed_at else None,
            "revoked_at": t.revoked_at.isoformat() if t.revoked_at else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in p.tokens
    ]
    return d


@router.patch("/{proposal_id}")
def update_proposal(
    proposal_id: str,
    req: ProposalUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    if req.title is not None:
        p.title = req.title
    if req.subtitle is not None:
        p.subtitle = req.subtitle
    if req.client_name is not None:
        p.client_name = req.client_name
    if req.client_email is not None:
        p.client_email = req.client_email
    if req.client_company is not None:
        p.client_company = req.client_company
    if req.branding_override is not None:
        p.branding_override = json.dumps(req.branding_override)
    if req.expires_at is not None:
        try:
            p.expires_at = datetime.fromisoformat(req.expires_at)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid expires_at format")
    p.updated_at = _utcnow()
    db.commit()
    return _proposal_to_dict(p)


@router.delete("/{proposal_id}")
def delete_proposal(
    proposal_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    p.deleted_at = _utcnow()
    db.commit()
    return {"deleted": True}


@router.post("/{proposal_id}/publish")
def publish_proposal(
    proposal_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    if not p.blocks:
        raise HTTPException(status_code=422, detail="Cannot publish a proposal with no content blocks")
    p.status = "published"
    p.updated_at = _utcnow()
    db.commit()
    return {"status": p.status}


@router.post("/{proposal_id}/unpublish")
def unpublish_proposal(
    proposal_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    p.status = "draft"
    p.updated_at = _utcnow()
    db.commit()
    return {"status": p.status}


# ── Block management ───────────────────────────────────────────────────────────

VALID_BLOCK_TYPES = {"text", "image", "pdf", "video", "divider", "cta", "website_url"}


@router.post("/{proposal_id}/blocks")
def add_block(
    proposal_id: str,
    req: BlockCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if req.block_type not in VALID_BLOCK_TYPES:
        raise HTTPException(status_code=422, detail=f"block_type must be one of {VALID_BLOCK_TYPES}")
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)

    # Determine position
    if req.position is not None:
        position = req.position
        # Shift existing blocks at or after this position down
        for b in p.blocks:
            if b.position >= position:
                b.position += 1
    else:
        position = max((b.position for b in p.blocks), default=-1) + 1

    block = ProposalBlock(
        id=_gen_id(),
        proposal_id=p.id,
        block_type=req.block_type,
        position=position,
        content=req.content,
        file_url=req.file_url,
        file_name=req.file_name,
        file_size=req.file_size,
        created_at=_utcnow(),
    )
    db.add(block)
    p.updated_at = _utcnow()
    db.commit()
    db.refresh(block)
    return _block_to_dict(block)


@router.patch("/{proposal_id}/blocks/{block_id}")
def update_block(
    proposal_id: str,
    block_id: str,
    req: BlockUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    block = next((b for b in p.blocks if b.id == block_id), None)
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    if req.content is not None:
        block.content = req.content
    if req.file_url is not None:
        block.file_url = req.file_url
    if req.file_name is not None:
        block.file_name = req.file_name
    if req.file_size is not None:
        block.file_size = req.file_size
    p.updated_at = _utcnow()
    db.commit()
    return _block_to_dict(block)


@router.delete("/{proposal_id}/blocks/{block_id}")
def delete_block(
    proposal_id: str,
    block_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    block = next((b for b in p.blocks if b.id == block_id), None)
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    db.delete(block)
    # Compact positions so there are no gaps
    for i, b in enumerate(sorted([x for x in p.blocks if x.id != block_id], key=lambda x: x.position)):
        b.position = i
    p.updated_at = _utcnow()
    db.commit()
    return {"deleted": True}


@router.post("/{proposal_id}/blocks/reorder")
def reorder_blocks(
    proposal_id: str,
    req: ReorderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    block_map = {b.id: b for b in p.blocks}
    for i, block_id in enumerate(req.block_ids):
        if block_id not in block_map:
            raise HTTPException(status_code=422, detail=f"Block {block_id} not found in this proposal")
        block_map[block_id].position = i
    p.updated_at = _utcnow()
    db.commit()
    return {"reordered": True}


# ── Magic-link invite ──────────────────────────────────────────────────────────

@router.post("/{proposal_id}/send")
def send_proposal_invite(
    proposal_id: str,
    req: SendInviteRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    if p.status != "published":
        raise HTTPException(status_code=422, detail="Proposal must be published before sending")

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    org_name = org.name if org else "EvoSys Pro"

    # Create the token
    token_str = str(uuid.uuid4()).replace("-", "")
    expires_at = _utcnow() + timedelta(hours=req.expires_hours)
    portal_token = ProposalToken(
        id=_gen_id(),
        proposal_id=p.id,
        token=token_str,
        recipient_email=req.recipient_email,
        recipient_name=req.recipient_name,
        expires_at=expires_at,
        protect_content=req.protect_content,
        created_at=_utcnow(),
    )
    db.add(portal_token)
    db.commit()

    # Build portal URL — clients land on /portal/access/{token}
    # Frontend uses window.location.origin so the domain auto-matches
    portal_url = f"https://app.evosyspro.live/portal/access/{token_str}"

    greeting = f"Hi {req.recipient_name}," if req.recipient_name else "Hello,"
    personal_section = f"\n\n{req.personal_note}" if req.personal_note else ""

    email_body = f"""{greeting}

{current_user.full_name} from {org_name} has shared a proposal with you: **{p.title}**
{personal_section}

Click the secure link below to view your proposal:

{portal_url}

This link is private and intended only for you. It expires in {req.expires_hours} hours.

— The {org_name} Team
"""

    def _send():
        try:
            send_email(
                db=db,
                org_id=current_user.organization_id,
                to_email=req.recipient_email,
                to_name=req.recipient_name or req.recipient_email,
                subject=f"Your proposal from {org_name}: {p.title}",
                body=email_body,
            )
        except Exception as e:
            logger.error("Failed to send proposal invite email: %s", e)

    background_tasks.add_task(_send)

    return {
        "token_id": portal_token.id,
        "token": token_str,
        "portal_url": portal_url,
        "recipient_email": req.recipient_email,
        "expires_at": expires_at.isoformat(),
        "message": f"Invite sent to {req.recipient_email}",
    }


@router.post("/{proposal_id}/tokens/{token_id}/revoke")
def revoke_token(
    proposal_id: str,
    token_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    tok = next((t for t in p.tokens if t.id == token_id), None)
    if not tok:
        raise HTTPException(status_code=404, detail="Token not found")
    tok.revoked_at = _utcnow()
    db.commit()
    return {"revoked": True}


# ── Analytics ──────────────────────────────────────────────────────────────────

@router.get("/{proposal_id}/analytics")
def get_analytics(
    proposal_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    p = _get_proposal_or_404(db, proposal_id, current_user.organization_id)
    views = p.views
    total_opens = len(views)
    unique_tokens = len({v.token_id for v in views if v.token_id})
    downloads = sum(1 for v in views if v.downloaded)
    avg_duration = (
        sum(v.duration_seconds for v in views if v.duration_seconds)
        / max(1, sum(1 for v in views if v.duration_seconds))
        if views else 0
    )
    avg_scroll = (
        sum(v.max_scroll_pct for v in views) / len(views) if views else 0
    )

    token_activity = []
    for tok in p.tokens:
        tok_views = [v for v in views if v.token_id == tok.id]
        token_activity.append({
            "token_id": tok.id,
            "recipient_email": tok.recipient_email,
            "recipient_name": tok.recipient_name,
            "sent_at": tok.created_at.isoformat() if tok.created_at else None,
            "first_opened": tok.first_redeemed_at.isoformat() if tok.first_redeemed_at else None,
            "open_count": len(tok_views),
            "downloaded": any(v.downloaded for v in tok_views),
            "last_scroll_pct": max((v.max_scroll_pct for v in tok_views), default=0),
            "revoked": tok.revoked_at is not None,
        })

    view_timeline = [
        {
            "id": v.id,
            "opened_at": v.opened_at.isoformat() if v.opened_at else None,
            "duration_seconds": v.duration_seconds,
            "max_scroll_pct": v.max_scroll_pct,
            "downloaded": v.downloaded,
            "viewer_city": v.viewer_city,
        }
        for v in sorted(views, key=lambda x: x.opened_at or datetime.min, reverse=True)
    ]

    return {
        "proposal_id": p.id,
        "title": p.title,
        "total_opens": total_opens,
        "unique_recipients_opened": unique_tokens,
        "total_downloads": downloads,
        "avg_duration_seconds": round(avg_duration),
        "avg_scroll_pct": round(avg_scroll),
        "token_activity": token_activity,
        "view_timeline": view_timeline,
    }


# ── File upload / serve ────────────────────────────────────────────────────────

ALLOWED_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
    "application/pdf",
}
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB


@router.post("/{proposal_id}/upload")
async def upload_proposal_file(
    proposal_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Upload an image or PDF for use in a proposal block.
    Returns {file_id, file_url, filename, content_type, file_size}.
    The file_url is a path to the public serve endpoint.
    """
    p = db.query(Proposal).filter_by(id=proposal_id,
                                     organization_id=_tenant_write_org_id(current_user),
                                     deleted_at=None).first()
    if not p:
        raise HTTPException(404, "Proposal not found")

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported file type: {file.content_type}. Allowed: images and PDF.")

    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(400, f"File too large ({len(data) // 1024 // 1024} MB). Maximum is 20 MB.")

    pf = ProposalFile(
        # ProposalFile.organization_id is nullable, so a context-less owner
        # would create a file row owned by nobody rather than being refused.
        organization_id=_tenant_write_org_id(current_user),
        proposal_id=proposal_id,
        filename=file.filename or "upload",
        content_type=file.content_type,
        file_size=len(data),
        file_data=data,
    )
    db.add(pf)
    db.commit()
    db.refresh(pf)

    return {
        "file_id": pf.id,
        "file_url": f"/proposals/files/{pf.id}",
        "filename": pf.filename,
        "content_type": pf.content_type,
        "file_size": pf.file_size,
    }


@router.get("/files/{file_id}")
def serve_proposal_file(file_id: str, db: Session = Depends(get_db)):
    """
    Serves an uploaded proposal file. No auth required — client portal links
    must work without a session. Files are keyed by UUID so they're not guessable.
    """
    pf = db.query(ProposalFile).filter_by(id=file_id).first()
    if not pf:
        raise HTTPException(404, "File not found")
    return Response(
        content=pf.file_data,
        media_type=pf.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{pf.filename}"',
            "Cache-Control": "public, max-age=86400",
        },
    )


# ── Client portal surface (no internal JWT) ────────────────────────────────────

@router.get("/portal/resolve/{token}")
def resolve_portal_token(
    token: str,
    db: Session = Depends(get_db),
):
    """
    Called by the client portal on load.
    Validates the magic-link token, creates a ProposalView row, and returns
    the full proposal content (blocks) for rendering.
    No internal JWT required — the token IS the authentication.
    """
    now = _utcnow()

    portal_token = (
        db.query(ProposalToken)
        .filter(ProposalToken.token == token)
        .first()
    )

    if not portal_token:
        raise HTTPException(status_code=404, detail="This link is invalid or has expired.")

    if portal_token.revoked_at and portal_token.revoked_at <= now:
        raise HTTPException(status_code=403, detail="This link has been revoked.")

    if portal_token.expires_at and portal_token.expires_at < now:
        raise HTTPException(status_code=403, detail="This link has expired. Please contact your advisor for a new one.")

    p = (
        db.query(Proposal)
        .filter(
            Proposal.id == portal_token.proposal_id,
            Proposal.deleted_at.is_(None),
        )
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found.")

    if p.status != "published":
        raise HTTPException(status_code=403, detail="This proposal is not yet available.")

    if p.expires_at and p.expires_at < now:
        raise HTTPException(status_code=403, detail="This proposal has expired.")

    # Mark first redemption
    if not portal_token.first_redeemed_at:
        portal_token.first_redeemed_at = now

    # Open a new view session
    view = ProposalView(
        id=_gen_id(),
        proposal_id=p.id,
        token_id=portal_token.id,
        opened_at=now,
        max_scroll_pct=0,
        downloaded=False,
    )
    db.add(view)
    db.commit()
    db.refresh(view)

    # Branding. The brand's own public identity first (name, support number,
    # marketing site) so the document can sign itself the way the brand does
    # everywhere else, then any per-proposal override on top. Nothing internal
    # is added here: every key below is information the brand already prints on
    # its own website.
    branding = {}
    try:
        from app.services.appointment_invites import brand_identity_for_brand
        ident = brand_identity_for_brand(db, getattr(p, "brand_sales_org_id", None))
        for k in ("name", "support_phone", "website", "accent"):
            if ident.get(k):
                branding[k] = ident[k]
    except Exception:
        logger.exception("portal branding lookup failed for proposal %s", p.id)
    if p.branding_override:
        try:
            branding.update(json.loads(p.branding_override) or {})
        except Exception:
            logger.exception("bad branding_override on proposal %s", p.id)

    return {
        "view_id": view.id,
        "proposal": {
            "id": p.id,
            "title": p.title,
            "subtitle": p.subtitle,
            "client_name": portal_token.recipient_name or p.client_name,
            "client_company": p.client_company,
            # Document identity, so the cover can carry a reference and a date
            # instead of inventing one at render time.
            "proposal_number": getattr(p, "proposal_number", None),
            "version": getattr(p, "version", None),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            "blocks": [_block_to_dict(b) for b in p.blocks],
        },
        "branding": branding,
        # Tells the client whether to show download buttons etc.
        "permissions": {
            "can_download": not portal_token.protect_content,
            "protect_content": portal_token.protect_content,
        },
    }


@router.post("/portal/view/{view_id}/ping")
def ping_view(
    view_id: str,
    req: ViewPingRequest,
    db: Session = Depends(get_db),
):
    """Heartbeat — client sends scroll depth + elapsed time every 15s."""
    view = db.query(ProposalView).filter(ProposalView.id == view_id).first()
    if not view:
        raise HTTPException(status_code=404, detail="View session not found")
    if req.scroll_pct > view.max_scroll_pct:
        view.max_scroll_pct = min(req.scroll_pct, 100)
    db.commit()
    return {"ok": True}


@router.post("/portal/view/{view_id}/close")
def close_view(
    view_id: str,
    req: ViewCloseRequest,
    db: Session = Depends(get_db),
):
    """Client tab close / page unload — record final stats."""
    view = db.query(ProposalView).filter(ProposalView.id == view_id).first()
    if not view:
        raise HTTPException(status_code=404, detail="View session not found")
    view.closed_at = _utcnow()
    view.duration_seconds = req.elapsed_seconds
    if req.scroll_pct > view.max_scroll_pct:
        view.max_scroll_pct = min(req.scroll_pct, 100)
    db.commit()
    return {"ok": True}


@router.post("/portal/view/{view_id}/download")
def record_download(
    view_id: str,
    db: Session = Depends(get_db),
):
    """Client clicks a download button — flag the view row."""
    view = db.query(ProposalView).filter(ProposalView.id == view_id).first()
    if not view:
        raise HTTPException(status_code=404, detail="View session not found")
    view.downloaded = True
    db.commit()
    return {"ok": True}
