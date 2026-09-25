"""Wholesale file handling: property photos, comp photos, deal documents.

ONE UPLOAD PATH, ONE SERVE PATH, ONE DELETE PATH.
-------------------------------------------------
"Do not create separate upload implementations for every tab." Every stored
object in the module — a photo of a roof, a signed purchase contract, a buyer's
proof of funds — is a `WholesaleFile` row written by `wholesale_files.py` and
read back through ONE endpoint here.

WHY BYTES ARE SERVED AND NOT LINKED
------------------------------------
There is no public URL anywhere in this module. `GET /wholesale/files/{id}`
loads the row, re-checks the organization, and streams the bytes. An
unguessable link is not authorization: the whole point of a document drawer
holding a purchase contract is that only this customer can open it.

Same three gates as the rest of the module — tenant, feature, and (for writes)
not-an-observer — plus a fourth that is specific to files: the parent the file
is being attached to must belong to the caller's organization, checked BEFORE
a byte is read off the wire.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     Response, UploadFile)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import (get_db, require_not_observation, require_tenant_or_observer,
                      require_tenant_user)
from app.models.models import User
from app.models.wholesale_models import (
    ACTOR_USER, WholesaleBuyer, WholesaleComp, WholesaleDeal, WholesaleDocument,
    WholesaleFile, WholesaleProperty,
)
from app.services import wholesale_files as files
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

log = logging.getLogger(__name__)

FEATURE = "wholesale_real_estate"

router = APIRouter(prefix="/wholesale", tags=["wholesale"],
                   dependencies=[Depends(require_feature(FEATURE))])

KIND_PROPERTY_PHOTO = "property_photo"
KIND_COMP_PHOTO = "comp_photo"
KIND_DOCUMENT = "document"


def file_json(row: WholesaleFile) -> Dict[str, Any]:
    """What a screen is allowed to know about a stored file.

    `storage_key` and `storage_backend` are NOT here. The client never needs
    them and a payload that carries them is one screenshot away from being the
    thing somebody pastes into a bug report.
    """
    return {
        "id": row.id,
        "kind": row.kind,
        "url": "/wholesale/files/%s" % row.id,     # authenticated, not public
        "original_filename": row.original_filename,
        "content_type": row.content_type,
        "byte_size": row.byte_size,
        "caption": row.caption,
        "is_primary": bool(row.is_primary),
        "sort_order": row.sort_order,
        # Phase 5. What the picture is of, and whether a person has published
        # it to investors. `buyer_visible` is read by the publication boundary,
        # so it is shown here rather than being an invisible server-side flag.
        "category": row.category,
        "buyer_visible": bool(row.buyer_visible),
        # Phase 6. The owner's own page is a separate decision from the
        # investor gallery, so it is a separate flag and a separate checkbox.
        "seller_visible": bool(row.seller_visible),
        "is_image": (row.content_type or "").startswith("image/"),
        "uploaded_by_id": row.uploaded_by_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _own_property(db: Session, org_id: str, property_id: str) -> WholesaleProperty:
    row = (db.query(WholesaleProperty)
           .filter(WholesaleProperty.id == property_id,
                   WholesaleProperty.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return row


def _own_deal(db: Session, org_id: str, deal_id: str) -> WholesaleDeal:
    row = (db.query(WholesaleDeal)
           .filter(WholesaleDeal.id == deal_id,
                   WholesaleDeal.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    return row


def _own_file(db: Session, org_id: str, file_id: str) -> WholesaleFile:
    row = (db.query(WholesaleFile)
           .filter(WholesaleFile.id == file_id,
                   WholesaleFile.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    return row


@router.get("/files/capability")
def storage_capability(user: User = Depends(require_tenant_or_observer)):
    """What this deployment can store, in words a person can act on.

    Asked by every screen that shows an upload control, so a button is never
    offered that cannot work — and so the reason is the deployment's actual
    configuration rather than a generic failure after the fact.
    """
    return files.capability()


@router.get("/files/{file_id}")
def serve_file(file_id: str, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    """The bytes — to a signed-in member of the organization that owns them.

    The org check happens on the ROW, not on the key, which is why a file id
    from another tenant 404s exactly like one that does not exist.
    """
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409,
                            detail="No customer organization selected.")
    row = _own_file(db, org_id, file_id)
    data = files.fetch(row.storage_backend, row.storage_key)
    # `inline` for images so a thumbnail renders; the filename is quoted and
    # stripped of quotes so a crafted upload name cannot break the header.
    safe_name = (row.original_filename or "file").replace('"', "").replace("\n", "")
    disposition = "inline" if (row.content_type or "").startswith("image/") else "attachment"
    return Response(
        content=data,
        media_type=row.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": '%s; filename="%s"' % (disposition, safe_name),
            # A deal document is never a shared cache entry.
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/properties/{property_id}/photos")
async def upload_property_photo(
        property_id: str, request: Request,
        file: UploadFile = File(...),
        caption: Optional[str] = Form(None),
        db: Session = Depends(get_db),
        user: User = Depends(require_tenant_user),
        _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    prop = _own_property(db, org_id, property_id)

    data, content_type, original = await files.read_upload(file, images_only=True)
    stored = files.put(org_id, KIND_PROPERTY_PHOTO, data, content_type)

    existing = (db.query(WholesaleFile)
                .filter(WholesaleFile.organization_id == org_id,
                        WholesaleFile.property_id == property_id,
                        WholesaleFile.kind == KIND_PROPERTY_PHOTO).count())

    row = WholesaleFile(
        organization_id=org_id, property_id=property_id,
        kind=KIND_PROPERTY_PHOTO, original_filename=original,
        caption=(caption or None), uploaded_by_id=user.id,
        # The first photo is the cover, because a gallery whose first upload is
        # not the cover makes everybody set one by hand for no reason.
        is_primary=(existing == 0), sort_order=existing, **stored)
    db.add(row)
    db.flush()
    svc.log_event(db, org_id, "property.photo_uploaded", actor_type=ACTOR_USER,
                  actor_user_id=user.id, property_id=property_id,
                  summary="Photo uploaded: %s" % original)
    db.commit()
    return file_json(row)


@router.get("/properties/{property_id}/photos")
def list_property_photos(property_id: str, db: Session = Depends(get_db),
                         user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409,
                            detail="No customer organization selected.")
    _own_property(db, org_id, property_id)
    rows = (db.query(WholesaleFile)
            .filter(WholesaleFile.organization_id == org_id,
                    WholesaleFile.property_id == property_id,
                    WholesaleFile.kind == KIND_PROPERTY_PHOTO)
            .order_by(WholesaleFile.is_primary.desc(),
                      WholesaleFile.sort_order.asc(),
                      WholesaleFile.created_at.asc()).all())
    return {"photos": [file_json(r) for r in rows]}


class PhotoPatch(BaseModel):
    caption: Optional[str] = None
    is_primary: Optional[bool] = None
    sort_order: Optional[int] = None
    # Phase 5.
    category: Optional[str] = None
    buyer_visible: Optional[bool] = None
    # Phase 6. Default None, not False, for the same reason the document flags
    # are: a PATCH that does not mention an audience must not unpublish it.
    seller_visible: Optional[bool] = None


@router.patch("/files/{file_id}")
def update_file(file_id: str, payload: PhotoPatch, request: Request,
                db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _guard: User = Depends(require_not_observation)):
    """Caption, cover image, order. Not the bytes — those are immutable."""
    org_id = svc.write_org_id(db, user)
    row = _own_file(db, org_id, file_id)
    data = payload.model_dump(exclude_unset=True)

    if data.get("is_primary"):
        # Exactly one cover per property. Demoting the old one here rather than
        # trusting the screen to send two requests keeps the invariant true
        # even if the second request never arrives.
        (db.query(WholesaleFile)
         .filter(WholesaleFile.organization_id == org_id,
                 WholesaleFile.property_id == row.property_id,
                 WholesaleFile.kind == row.kind,
                 WholesaleFile.id != row.id)
         .update({"is_primary": False}, synchronize_session=False))
        row.is_primary = True
    elif "is_primary" in data:
        row.is_primary = bool(data["is_primary"])

    if "caption" in data:
        row.caption = data["caption"] or None
    if "sort_order" in data and data["sort_order"] is not None:
        row.sort_order = int(data["sort_order"])
    if "category" in data:
        # Stored as typed. An organization that invents its own category is not
        # punished for it, and the screen renders an unknown value as itself.
        row.category = (data["category"] or None)
    if "buyer_visible" in data:
        # This is a publication decision, so it gets its own audit line rather
        # than disappearing into a generic "file updated".
        row.buyer_visible = bool(data["buyer_visible"])
        svc.log_event(
            db, org_id,
            "property.photo_published" if row.buyer_visible
            else "property.photo_unpublished",
            actor_type=ACTOR_USER, actor_user_id=user.id,
            property_id=row.property_id, deal_id=row.deal_id,
            summary=("Photo shown to investors: %s" if row.buyer_visible
                     else "Photo hidden from investors: %s")
                    % (row.original_filename or row.id))
    if "seller_visible" in data:
        # Phase 6. Publishing to the OWNER is its own decision and its own
        # audit line. A single "published" event covering both audiences would
        # make the log unable to answer "who did this photo go to".
        row.seller_visible = bool(data["seller_visible"])
        svc.log_event(
            db, org_id,
            "property.photo_published_owner" if row.seller_visible
            else "property.photo_unpublished_owner",
            actor_type=ACTOR_USER, actor_user_id=user.id,
            property_id=row.property_id, deal_id=row.deal_id,
            summary=("Photo shown to the owner: %s" if row.seller_visible
                     else "Photo hidden from the owner: %s")
                    % (row.original_filename or row.id))

    svc.log_event(db, org_id, "file.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, property_id=row.property_id,
                  deal_id=row.deal_id,
                  summary="File updated: %s" % (row.original_filename or row.id))
    db.commit()
    return file_json(row)


@router.delete("/files/{file_id}")
def delete_file(file_id: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _guard: User = Depends(require_not_observation)):
    """Delete the row and the object. The screen confirms first.

    If this was the cover photo, the next one is promoted — a property whose
    gallery silently has no cover after a delete looks broken.
    """
    org_id = svc.write_org_id(db, user)
    row = _own_file(db, org_id, file_id)
    was_primary, prop_id, kind = bool(row.is_primary), row.property_id, row.kind
    backend, key, name = row.storage_backend, row.storage_key, row.original_filename

    # A document row pointing at this file keeps its typed metadata and loses
    # only the attachment — deleting the file must not silently delete the
    # operator's record that the document exists.
    (db.query(WholesaleDocument)
     .filter(WholesaleDocument.organization_id == org_id,
             WholesaleDocument.file_id == file_id)
     .update({"file_id": None}, synchronize_session=False))

    db.delete(row)
    db.flush()

    if was_primary and prop_id:
        nxt = (db.query(WholesaleFile)
               .filter(WholesaleFile.organization_id == org_id,
                       WholesaleFile.property_id == prop_id,
                       WholesaleFile.kind == kind)
               .order_by(WholesaleFile.sort_order.asc(),
                         WholesaleFile.created_at.asc()).first())
        if nxt is not None:
            nxt.is_primary = True

    svc.log_event(db, org_id, "file.deleted", actor_type=ACTOR_USER,
                  actor_user_id=user.id, property_id=prop_id,
                  summary="File deleted: %s" % (name or file_id))
    db.commit()
    # The object goes last: a failed delete here leaves an orphan in the bucket,
    # which is a cleanup job. The reverse order leaves a row pointing at nothing,
    # which is a broken screen.
    files.remove(backend, key)
    return {"deleted": True, "id": file_id}


@router.post("/deals/{deal_id}/documents/upload")
async def upload_deal_document(
        deal_id: str, request: Request,
        file: UploadFile = File(...),
        doc_type: str = Form("other"),
        title: Optional[str] = Form(None),
        document_id: Optional[str] = Form(None),
        notes: Optional[str] = Form(None),
        db: Session = Depends(get_db),
        user: User = Depends(require_tenant_user),
        _guard: User = Depends(require_not_observation)):
    """Attach a real file to a document slot, or create the slot and fill it.

    `document_id` attaches to an EXISTING slot — the one a person created when
    they knew a signed contract was coming before it arrived. Without it a new
    row is created. Either way there is one document record, not two.
    """
    org_id = svc.write_org_id(db, user)
    _own_deal(db, org_id, deal_id)

    data, content_type, original = await files.read_upload(file)
    stored = files.put(org_id, KIND_DOCUMENT, data, content_type)

    row = WholesaleFile(organization_id=org_id, deal_id=deal_id,
                        kind=KIND_DOCUMENT, original_filename=original,
                        uploaded_by_id=user.id, **stored)
    db.add(row)
    db.flush()

    doc = None
    if document_id:
        doc = (db.query(WholesaleDocument)
               .filter(WholesaleDocument.id == document_id,
                       WholesaleDocument.organization_id == org_id,
                       WholesaleDocument.deal_id == deal_id).first())
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
    if doc is None:
        doc = WholesaleDocument(organization_id=org_id, deal_id=deal_id,
                                doc_type=doc_type or "other")
        db.add(doc)

    doc.file_id = row.id
    doc.file_name = original
    doc.status = "uploaded"
    doc.uploaded_by_id = user.id
    doc.uploaded_at = row.created_at
    if title:
        doc.title = title
    if notes:
        doc.notes = notes
    db.flush()

    svc.log_event(db, org_id, "document.uploaded", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal_id,
                  summary="%s uploaded: %s" % (doc.doc_type, original))
    db.commit()
    return {"document_id": doc.id, "file": file_json(row)}


@router.post("/comps/{comp_id}/photo")
async def upload_comp_photo(
        comp_id: str, request: Request,
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        user: User = Depends(require_tenant_user),
        _guard: User = Depends(require_not_observation)):
    """One photo per comp — a comp sheet is a row of pictures, not an album."""
    org_id = svc.write_org_id(db, user)
    comp = (db.query(WholesaleComp)
            .filter(WholesaleComp.id == comp_id,
                    WholesaleComp.organization_id == org_id).first())
    if comp is None:
        raise HTTPException(status_code=404, detail="Comp not found")

    data, content_type, original = await files.read_upload(file, images_only=True)
    stored = files.put(org_id, KIND_COMP_PHOTO, data, content_type)

    previous = (db.query(WholesaleFile)
                .filter(WholesaleFile.organization_id == org_id,
                        WholesaleFile.comp_id == comp_id,
                        WholesaleFile.kind == KIND_COMP_PHOTO).all())

    row = WholesaleFile(organization_id=org_id, comp_id=comp_id,
                        deal_id=comp.deal_id, kind=KIND_COMP_PHOTO,
                        original_filename=original, uploaded_by_id=user.id,
                        is_primary=True, **stored)
    db.add(row)
    db.flush()

    for old in previous:
        files.remove(old.storage_backend, old.storage_key)
        db.delete(old)

    svc.log_event(db, org_id, "comp.photo_uploaded", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=comp.deal_id,
                  summary="Comp photo: %s" % (comp.street_address or comp_id))
    db.commit()
    return file_json(row)


@router.post("/outreach/{outreach_id}/proof-of-funds")
async def upload_proof_of_funds(
        outreach_id: str, request: Request,
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        user: User = Depends(require_tenant_user),
        _guard: User = Depends(require_not_observation)):
    """A buyer's proof of funds, against the deal they are bidding on.

    Stored as a document of type `proof_of_funds` so it appears in the deal's
    drawer like everything else, AND pointed at from the outreach row so the
    disposition board can show POF status per buyer without a second lookup.

    The status it sets is `received` — a file arriving is not verification, and
    this module has no provider that can verify one. `verified` is a person's
    decision, made on the buyer board.
    """
    org_id = svc.write_org_id(db, user)
    from app.models.wholesale_models import WholesaleBuyerOutreach

    row = (db.query(WholesaleBuyerOutreach)
           .filter(WholesaleBuyerOutreach.id == outreach_id,
                   WholesaleBuyerOutreach.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Outreach not found")

    data, content_type, original = await files.read_upload(file)
    stored = files.put(org_id, KIND_DOCUMENT, data, content_type)

    stored_file = WholesaleFile(organization_id=org_id, deal_id=row.deal_id,
                                buyer_id=row.buyer_id, kind=KIND_DOCUMENT,
                                original_filename=original,
                                uploaded_by_id=user.id, **stored)
    db.add(stored_file)
    db.flush()

    doc = WholesaleDocument(organization_id=org_id, deal_id=row.deal_id,
                            buyer_id=row.buyer_id, doc_type="proof_of_funds",
                            title="Proof of funds", status="uploaded",
                            file_id=stored_file.id, file_name=original,
                            uploaded_by_id=user.id,
                            uploaded_at=stored_file.created_at)
    db.add(doc)

    row.pof_file_id = stored_file.id
    row.pof_status = "received"

    svc.log_event(db, org_id, "buyer.pof_uploaded", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=row.deal_id,
                  summary="Proof of funds received: %s" % original)
    db.commit()
    return {"outreach_id": row.id, "pof_status": row.pof_status,
            "file": file_json(stored_file)}


# ── Phase 5. Many photos, in one go, and in an order somebody chose ─────────
#
# A wholesale property is not one picture. It is the front, the back, the
# kitchen, the roof, the foundation crack and the thing in the garage, and a
# one-at-a-time uploader turns a twenty-photo walkthrough into twenty round
# trips. This endpoint takes the whole set in one request and reports each
# file's own outcome, because a batch that fails as a unit because file 14 was
# a HEIC the browser mislabelled is worse than no batch at all.

@router.post("/properties/{property_id}/photos/batch")
async def upload_property_photos(
        property_id: str, request: Request,
        files_in: List[UploadFile] = File(..., alias="files"),
        category: Optional[str] = Form(None),
        db: Session = Depends(get_db),
        user: User = Depends(require_tenant_user),
        _guard: User = Depends(require_not_observation)):
    """Upload several photos at once. Per-file success, per-file reason."""
    org_id = svc.write_org_id(db, user)
    _own_property(db, org_id, property_id)

    existing = (db.query(WholesaleFile)
                .filter(WholesaleFile.organization_id == org_id,
                        WholesaleFile.property_id == property_id,
                        WholesaleFile.kind == KIND_PROPERTY_PHOTO).count())

    saved, rejected = [], []
    order = existing
    for upload in files_in:
        try:
            data, content_type, original = await files.read_upload(
                upload, images_only=True)
        except HTTPException as exc:
            rejected.append({"filename": upload.filename,
                             "reason": exc.detail})
            continue
        stored = files.put(org_id, KIND_PROPERTY_PHOTO, data, content_type)
        row = WholesaleFile(
            organization_id=org_id, property_id=property_id,
            kind=KIND_PROPERTY_PHOTO, original_filename=original,
            category=(category or None), uploaded_by_id=user.id,
            is_primary=(order == 0), sort_order=order, **stored)
        db.add(row)
        db.flush()
        saved.append(file_json(row))
        order += 1

    if saved:
        svc.log_event(db, org_id, "property.photo_uploaded",
                      actor_type=ACTOR_USER, actor_user_id=user.id,
                      property_id=property_id,
                      summary="%d photo(s) uploaded" % len(saved))
    db.commit()
    # 200 even when some were refused: the ones that landed did land, and the
    # caller is told exactly which did not and why.
    return {"uploaded": saved, "rejected": rejected,
            "uploaded_count": len(saved), "rejected_count": len(rejected)}


class PhotoOrder(BaseModel):
    """The ids, in the order a person dragged them into."""
    file_ids: List[str]


@router.post("/properties/{property_id}/photos/reorder")
def reorder_property_photos(
        property_id: str, payload: PhotoOrder, request: Request,
        db: Session = Depends(get_db),
        user: User = Depends(require_tenant_user),
        _guard: User = Depends(require_not_observation)):
    """Write the gallery order. Ids not on this property are ignored, not an
    error: a stale browser tab should not be able to reorder somebody else's
    photos, and it must not be able to learn that an id exists either."""
    org_id = svc.write_org_id(db, user)
    _own_property(db, org_id, property_id)

    rows = {r.id: r for r in
            db.query(WholesaleFile)
            .filter(WholesaleFile.organization_id == org_id,
                    WholesaleFile.property_id == property_id,
                    WholesaleFile.kind == KIND_PROPERTY_PHOTO).all()}
    position = 0
    for file_id in payload.file_ids:
        row = rows.get(file_id)
        if row is None:
            continue
        row.sort_order = position
        position += 1

    svc.log_event(db, org_id, "property.photos_reordered", actor_type=ACTOR_USER,
                  actor_user_id=user.id, property_id=property_id,
                  summary="Gallery reordered")
    db.commit()
    return {"ordered": position}
