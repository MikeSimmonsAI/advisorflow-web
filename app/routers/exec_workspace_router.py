"""
Executive Workspace API — deal-room content scoped to customer organizations.

AUTHORITY MODEL
───────────────
Every endpoint calls require_brand_executive, then resolves the authorized
organization set via executive_authority.authorized_org_ids().  A per-item
endpoint additionally calls exec_auth.may_view_org() before touching the row.
A False from may_view_org answers 404, identical to a missing item, so a
probing caller learns nothing about whether the organization id is real.

FILE SECURITY
─────────────
File serve is authenticated + org-gated.  The blob is returned with the
stored content_type and a Content-Disposition: attachment header.  The
preview endpoint adds a strict CSP so HTML renders sandboxed.

SNAPSHOT SEMANTICS
──────────────────
A PATCH that changes ANY of title / status / working_notes writes ONE
pre-change snapshot before applying the update.  Multiple changed fields still
produce exactly one snapshot.  A PATCH that changes none of those three fields
writes no snapshot.

PUBLIC-FACING COPY RULE
────────────────────────
No response body or frontend label may contain "god", "god_admin",
"God Mode", "God Admin", "God Operations", or similar internal terms.
"""

from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_brand_executive
from app.models.exec_workspace_models import (
    ExecWorkspaceItem, ExecWorkspaceFile, ExecWorkspaceVersion,
)
from app.routers.audit_log_router import log_action
from app.services import executive_authority as exec_auth

router = APIRouter(prefix="/executive/workspace", tags=["executive-workspace"])

VALID_STATUSES = {"Draft", "Partner Review", "Approved", "Final"}
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ItemCreate(BaseModel):
    organization_id: str
    title: str
    status: str = "Draft"
    working_notes: Optional[str] = None


class ItemPatch(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    working_notes: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_item_or_404(db: Session, item_id: str) -> ExecWorkspaceItem:
    item = db.query(ExecWorkspaceItem).filter(ExecWorkspaceItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Workspace item not found")
    return item


def _gate_item(db: Session, user, platform, item: ExecWorkspaceItem):
    """Raise 404 if the caller cannot see this item's organization."""
    if not exec_auth.may_view_org(db, user, platform.id, item.organization_id):
        raise HTTPException(status_code=404, detail="Workspace item not found")


def _serialize_item(item: ExecWorkspaceItem) -> dict:
    return {
        "id": item.id,
        "organization_id": item.organization_id,
        "platform_id": item.platform_id,
        "title": item.title,
        "status": item.status,
        "working_notes": item.working_notes,
        "created_by": item.created_by,
        "updated_by": item.updated_by,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _serialize_file_meta(f: ExecWorkspaceFile) -> dict:
    return {
        "id": f.id,
        "item_id": f.item_id,
        "filename": f.filename,
        "content_type": f.content_type,
        "file_size": f.file_size,
        "is_current": f.is_current,
        "replaces_file_id": f.replaces_file_id,
        "uploaded_by": f.uploaded_by,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
    }


def _serialize_version(v: ExecWorkspaceVersion) -> dict:
    return {
        "id": v.id,
        "item_id": v.item_id,
        "snapshot_title": v.snapshot_title,
        "snapshot_notes": v.snapshot_notes,
        "snapshot_status": v.snapshot_status,
        "trigger": v.trigger,
        "saved_by": v.saved_by,
        "saved_at": v.saved_at.isoformat() if v.saved_at else None,
    }


def _snapshot(db: Session, item: ExecWorkspaceItem, actor_id: str, trigger: str = "patch"):
    """Write one pre-change snapshot.  Caller is responsible for committing."""
    snap = ExecWorkspaceVersion(
        item_id=item.id,
        organization_id=item.organization_id,
        platform_id=item.platform_id,
        snapshot_title=item.title,
        snapshot_notes=item.working_notes,
        snapshot_status=item.status,
        trigger=trigger,
        saved_by=actor_id,
    )
    db.add(snap)


# ── List items ────────────────────────────────────────────────────────────────

@router.get("/")
def list_items(
    organization_id: Optional[str] = None,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive
    org_ids = exec_auth.authorized_org_ids(db, user, platform.id)
    if not org_ids:
        return {"items": []}

    q = db.query(ExecWorkspaceItem).filter(
        ExecWorkspaceItem.platform_id == platform.id,
        ExecWorkspaceItem.organization_id.in_(org_ids),
    )
    if organization_id:
        if organization_id not in org_ids:
            raise HTTPException(status_code=404, detail="Organization not found")
        q = q.filter(ExecWorkspaceItem.organization_id == organization_id)

    items = q.order_by(ExecWorkspaceItem.updated_at.desc()).all()
    return {"items": [_serialize_item(i) for i in items]}


# ── Create item ───────────────────────────────────────────────────────────────

@router.post("/", status_code=201)
def create_item(
    body: ItemCreate,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive

    if body.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(VALID_STATUSES)}")

    if not exec_auth.may_view_org(db, user, platform.id, body.organization_id):
        raise HTTPException(status_code=404, detail="Organization not found")

    item = ExecWorkspaceItem(
        organization_id=body.organization_id,
        platform_id=platform.id,
        title=body.title,
        status=body.status,
        working_notes=body.working_notes,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(item)
    db.flush()

    log_action(
        db,
        organization_id=item.organization_id,
        actor_user_id=user.id,
        action="exec_workspace_item.created",
        target_type="exec_workspace_item",
        target_id=item.id,
        platform_id=platform.id,
        after={"title": item.title, "status": item.status},
    )
    db.commit()
    return _serialize_item(item)


# ── Get item ──────────────────────────────────────────────────────────────────

@router.get("/{item_id}")
def get_item(
    item_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive
    item = _get_item_or_404(db, item_id)
    _gate_item(db, user, platform, item)
    return _serialize_item(item)


# ── Patch item ────────────────────────────────────────────────────────────────

@router.patch("/{item_id}")
def patch_item(
    item_id: str,
    body: ItemPatch,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive
    item = _get_item_or_404(db, item_id)
    _gate_item(db, user, platform, item)

    if body.status is not None and body.status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(VALID_STATUSES)}")

    # Snapshot: ONE pre-change snapshot if ANY tracked field actually changed.
    changed = (
        (body.title is not None and body.title != item.title) or
        (body.status is not None and body.status != item.status) or
        (body.working_notes is not None and body.working_notes != item.working_notes)
    )
    before_state = {"title": item.title, "status": item.status, "working_notes": item.working_notes}

    if changed:
        _snapshot(db, item, user.id, trigger="patch")

    if body.title is not None:
        item.title = body.title
    if body.status is not None:
        item.status = body.status
    if body.working_notes is not None:
        item.working_notes = body.working_notes

    item.updated_by = user.id
    db.flush()

    after_state = {"title": item.title, "status": item.status, "working_notes": item.working_notes}
    log_action(
        db,
        organization_id=item.organization_id,
        actor_user_id=user.id,
        action="exec_workspace_item.updated",
        target_type="exec_workspace_item",
        target_id=item.id,
        platform_id=platform.id,
        before=before_state if changed else None,
        after=after_state,
    )
    db.commit()
    return _serialize_item(item)


# ── Versions ──────────────────────────────────────────────────────────────────

@router.get("/{item_id}/versions")
def list_versions(
    item_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive
    item = _get_item_or_404(db, item_id)
    _gate_item(db, user, platform, item)

    versions = (
        db.query(ExecWorkspaceVersion)
        .filter(ExecWorkspaceVersion.item_id == item_id)
        .order_by(ExecWorkspaceVersion.saved_at.desc())
        .all()
    )
    return {"versions": [_serialize_version(v) for v in versions]}


@router.get("/{item_id}/versions/{version_id}")
def get_version(
    item_id: str,
    version_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive
    item = _get_item_or_404(db, item_id)
    _gate_item(db, user, platform, item)

    v = (
        db.query(ExecWorkspaceVersion)
        .filter(
            ExecWorkspaceVersion.id == version_id,
            ExecWorkspaceVersion.item_id == item_id,
        )
        .first()
    )
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    return _serialize_version(v)


# ── Files: upload ─────────────────────────────────────────────────────────────

@router.post("/{item_id}/files", status_code=201)
async def upload_file(
    item_id: str,
    file: UploadFile = File(...),
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive
    item = _get_item_or_404(db, item_id)
    _gate_item(db, user, platform, item)

    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 20 MB limit")

    wf = ExecWorkspaceFile(
        item_id=item.id,
        organization_id=item.organization_id,
        platform_id=item.platform_id,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        file_size=len(data),
        file_data=data,
        is_current=True,
        uploaded_by=user.id,
    )
    db.add(wf)
    db.flush()

    log_action(
        db,
        organization_id=item.organization_id,
        actor_user_id=user.id,
        action="exec_workspace_file.uploaded",
        target_type="exec_workspace_file",
        target_id=wf.id,
        platform_id=platform.id,
        after={"filename": wf.filename, "size": wf.file_size},
    )
    db.commit()
    return _serialize_file_meta(wf)


# ── Files: list ───────────────────────────────────────────────────────────────

@router.get("/{item_id}/files")
def list_files(
    item_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive
    item = _get_item_or_404(db, item_id)
    _gate_item(db, user, platform, item)

    files = (
        db.query(ExecWorkspaceFile)
        .filter(
            ExecWorkspaceFile.item_id == item_id,
            ExecWorkspaceFile.is_current.is_(True),
        )
        .order_by(ExecWorkspaceFile.uploaded_at.desc())
        .all()
    )
    return {"files": [_serialize_file_meta(f) for f in files]}


# ── Files: serve ──────────────────────────────────────────────────────────────

@router.get("/{item_id}/files/{file_id}/serve")
def serve_file(
    item_id: str,
    file_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive
    item = _get_item_or_404(db, item_id)
    _gate_item(db, user, platform, item)

    wf = (
        db.query(ExecWorkspaceFile)
        .filter(
            ExecWorkspaceFile.id == file_id,
            ExecWorkspaceFile.item_id == item_id,
        )
        .first()
    )
    if not wf:
        raise HTTPException(status_code=404, detail="File not found")

    headers = {
        "Content-Disposition": f'attachment; filename="{wf.filename}"',
    }
    if wf.content_type and "html" in wf.content_type.lower():
        headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "img-src data: blob:; frame-ancestors 'self'"
        )

    return StreamingResponse(
        io.BytesIO(wf.file_data),
        media_type=wf.content_type,
        headers=headers,
    )


# ── Files: replace (PUT = new version, marks old as not current) ──────────────

@router.put("/{item_id}/files/{file_id}", status_code=201)
async def replace_file(
    item_id: str,
    file_id: str,
    file: UploadFile = File(...),
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive
    item = _get_item_or_404(db, item_id)
    _gate_item(db, user, platform, item)

    old = (
        db.query(ExecWorkspaceFile)
        .filter(
            ExecWorkspaceFile.id == file_id,
            ExecWorkspaceFile.item_id == item_id,
        )
        .first()
    )
    if not old:
        raise HTTPException(status_code=404, detail="File not found")

    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 20 MB limit")

    # Mark old as superseded
    old.is_current = False

    new_f = ExecWorkspaceFile(
        item_id=item.id,
        organization_id=item.organization_id,
        platform_id=item.platform_id,
        filename=file.filename or old.filename,
        content_type=file.content_type or old.content_type,
        file_size=len(data),
        file_data=data,
        is_current=True,
        replaces_file_id=old.id,
        uploaded_by=user.id,
    )
    db.add(new_f)
    db.flush()

    log_action(
        db,
        organization_id=item.organization_id,
        actor_user_id=user.id,
        action="exec_workspace_file.replaced",
        target_type="exec_workspace_file",
        target_id=new_f.id,
        platform_id=platform.id,
        before={"file_id": old.id, "filename": old.filename},
        after={"file_id": new_f.id, "filename": new_f.filename},
    )
    db.commit()
    return _serialize_file_meta(new_f)


# ── Files: version chain ──────────────────────────────────────────────────────

@router.get("/{item_id}/files/{file_id}/history")
def file_history(
    item_id: str,
    file_id: str,
    executive=Depends(require_brand_executive),
    db: Session = Depends(get_db),
):
    user, mem, platform = executive
    item = _get_item_or_404(db, item_id)
    _gate_item(db, user, platform, item)

    # Walk the replaces_file_id chain: start from the given file_id and find
    # everything in the item with the same lineage root.
    all_files = (
        db.query(ExecWorkspaceFile)
        .filter(ExecWorkspaceFile.item_id == item_id)
        .order_by(ExecWorkspaceFile.uploaded_at.asc())
        .all()
    )

    # Determine which files are in this file's lineage by walking backward.
    id_set: set[str] = set()
    # Find the root of the chain for the requested file_id.
    by_id = {f.id: f for f in all_files}
    target = by_id.get(file_id)
    if not target:
        raise HTTPException(status_code=404, detail="File not found")

    # Walk back to root.
    cur = target
    while cur:
        id_set.add(cur.id)
        cur = by_id.get(cur.replaces_file_id) if cur.replaces_file_id else None

    # Also walk forward (files that replaced this one).
    # Build a replaces→new map.
    replaced_by = {f.replaces_file_id: f for f in all_files if f.replaces_file_id}
    cur = target
    while cur:
        id_set.add(cur.id)
        cur = replaced_by.get(cur.id)

    chain = [f for f in all_files if f.id in id_set]
    return {"history": [_serialize_file_meta(f) for f in chain]}
