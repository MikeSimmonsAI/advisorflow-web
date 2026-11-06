"""VOLT document drawers - private uploads through the platform's approved storage.

Storage is app/services/wholesale_files (the database by default, S3 when the
deployment configures it). The bytes are written in the SAME transaction as the
volt_documents row, so an interrupted upload leaves neither half behind. A
retried upload carrying the same `upload_key` returns the first row instead of
storing a second copy.

Every read goes through an authenticated endpoint that re-checks ownership
(the applicant) or staff authority for the tenant; no storage URL ever leaves
the server. File contents and dates of birth are never logged.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.volt_models import VoltApplication, VoltDocument, VoltProgram, VoltProject, VoltRep
from app.services import wholesale_files as storage
from app.services.volt import common as C

MAX_BYTES = 10 * 1024 * 1024
ACCEPTED = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic", "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def _requirement(db, prog: VoltProgram, app: Optional[VoltApplication], doc_key: str):
    if app is None:
        reqs = C.loads(prog.profile_required_docs, [])
    else:
        project = db.get(VoltProject, app.project_id)
        reqs = C.loads(project.required_docs if project else None, [])
    for r in reqs:
        if str(r.get("key")) == doc_key:
            return r
    raise HTTPException(status_code=422, detail="That document is not requested here.")


async def upload(db: Session, prog: VoltProgram, rep: VoltRep, file: UploadFile, doc_key: str,
                 application_id: Optional[str], upload_key: str) -> VoltDocument:
    upload_key = (upload_key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", upload_key):
        raise HTTPException(status_code=422, detail="Upload key missing. Refresh the page and try again.")
    prior = (db.query(VoltDocument).filter(VoltDocument.rep_serial == rep.serial,
                                           VoltDocument.upload_key == upload_key).first())
    if prior is not None:
        return prior                                     # a retry of a finished upload
    app = None
    if application_id:
        app = db.get(VoltApplication, application_id)
        if app is None or app.rep_serial != rep.serial:
            raise HTTPException(status_code=404, detail="Application not found.")
        if app.status not in ("draft", "needs_update"):
            raise HTTPException(status_code=409, detail="This application is with our team. Uploads reopen if they ask for a change.")
    req = _requirement(db, prog, app, doc_key)
    data, content_type, filename = await storage.read_upload(file)
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="That file is too large. The limit is 10 MB.")
    if content_type not in ACCEPTED:
        raise HTTPException(status_code=400, detail="Upload a PDF, Word document or photo (JPG, PNG, WEBP, HEIC).")
    scope = (VoltDocument.application_id == app.id) if app else VoltDocument.application_id.is_(None)
    latest = (db.query(func.max(VoltDocument.version)).filter(VoltDocument.rep_serial == rep.serial, scope,
                                                              VoltDocument.doc_key == doc_key).scalar()) or 0
    try:
        with db.begin_nested():
            stored = storage.put(prog.organization_id, "volt", data, content_type, db=db)
            d = VoltDocument(organization_id=prog.organization_id, rep_serial=rep.serial,
                             application_id=app.id if app else None, doc_key=doc_key,
                             label=req.get("label") or doc_key, version=latest + 1, status="under_review",
                             storage_backend=stored["storage_backend"], storage_key=stored["storage_key"],
                             filename=filename, content_type=content_type, size_bytes=stored["byte_size"],
                             sha256=stored["checksum"], upload_key=upload_key)
            db.add(d)
            db.flush()
    except IntegrityError:
        prior = (db.query(VoltDocument).filter(VoltDocument.rep_serial == rep.serial,
                                               VoltDocument.upload_key == upload_key).first())
        if prior is None:
            raise
        return prior
    C.audit(db, prog.organization_id, None, "document", d.id, "uploaded", field=doc_key, new="v%d" % d.version)
    return d


def fetch_bytes(db: Session, d: VoltDocument) -> bytes:
    data = storage.fetch(d.storage_backend, d.storage_key, db=db)
    if data is None:
        raise HTTPException(status_code=404, detail="File not found.")
    return data


def review(db: Session, prog: VoltProgram, d: VoltDocument, status: str, reason: Optional[str], actor) -> VoltDocument:
    status = (status or "").strip().lower()
    if status not in ("accepted", "replace"):
        raise HTTPException(status_code=422, detail="Choose Accept or Request a replacement.")
    if status == "replace" and not (reason or "").strip():
        raise HTTPException(status_code=422, detail={"message": "Say what the replacement needs.",
                                                     "fields": {"reason": "Say what the replacement needs."}})
    newer = (db.query(VoltDocument).filter(VoltDocument.rep_serial == d.rep_serial,
                                           VoltDocument.doc_key == d.doc_key,
                                           VoltDocument.application_id.is_(None) if d.application_id is None
                                           else VoltDocument.application_id == d.application_id,
                                           VoltDocument.version > d.version).first())
    if newer is not None:
        raise HTTPException(status_code=409, detail="A newer version (v%d) was uploaded. Review that one." % newer.version)
    old = d.status
    d.status = status
    d.reason = reason.strip()[:2000] if status == "replace" else None
    d.reviewed_by = actor.id
    d.reviewed_at = C.now()
    C.audit(db, prog.organization_id, actor, "document", d.id, "reviewed", field="v%d" % d.version,
            old=old, new=status, reason=d.reason)
    return d
