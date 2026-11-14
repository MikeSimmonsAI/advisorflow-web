"""EVOSYS PRO SALES ONBOARDING — application, checklist, private files, approval.

Nobody on the team sells until an owner (or God Mode) approves them. Before
approval a person can sign in, fill in the application, upload the checklist
files and see their own checklist - nothing else in the sales program.

WHO CAN DO WHAT (enforced here and in app/routers/sales_team_router.py)
  the person      fill in their own application, upload their own files,
                  submit, see their own checklist (file names, not contents)
  Sr EVP          see done / missing for people in HIS team. No file
                  contents, no download, no approval.
  owners, God     open and download files, approve, send back for a fix.

FILES. Bytes go to the platform's private storage (app/services/wholesale_files:
the database by default, S3 when configured), never to a public bucket, a URL,
an email or a log. The backend has no Google Drive connection that can write
files; see `DRIVE_STATUS`. Every view and download is written to the audit log.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_team_models import (DOC_KINDS, DOC_LABELS, ONB_APPROVED,
                                          ONB_IN_PROGRESS, ONB_RETURNED,
                                          ONB_SUBMITTED, SalesOnboarding,
                                          SalesOnboardingDocument)
from app.routers.audit_log_router import log_action
from app.services import wholesale_files as storage

MAX_BYTES = 10 * 1024 * 1024
ACCEPTED = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic"}
APPLICATION_FIELDS = ("legal_name", "home_address", "phone", "email", "start_date")
APPLICATION_LABELS = {"legal_name": "Legal name", "home_address": "Home address",
                      "phone": "Phone", "email": "Email", "start_date": "Start date"}
STORAGE_KIND = "sales-onboarding"
DRIVE_STATUS = ("Files are kept in the platform's private storage. The backend has "
                "no Google Drive connection that can write files, so the "
                "'EvoSys Pro – Sales Onboarding' Drive folder is not filled "
                "automatically yet.")


def record(db: Session, user_id: str, brand_id: str) -> Optional[SalesOnboarding]:
    return (db.query(SalesOnboarding)
            .filter(SalesOnboarding.user_id == user_id,
                    SalesOnboarding.brand_sales_org_id == brand_id).first())


def is_cleared(db: Session, user_id: str, brand_id: str) -> bool:
    """Approved to sell. Everyone on the team goes through onboarding - there
    is no grandfathered selling team - so no record means not cleared."""
    r = record(db, user_id, brand_id)
    return bool(r is not None and r.status == ONB_APPROVED)


def open_for(db: Session, user: User, brand_id: str, invited_by: Optional[str] = None) -> SalesOnboarding:
    r = record(db, user.id, brand_id)
    if r is None:
        r = SalesOnboarding(brand_sales_org_id=brand_id, user_id=user.id,
                            status=ONB_IN_PROGRESS, email=user.email,
                            legal_name=user.full_name, invited_by=invited_by)
        db.add(r)
        db.flush()
    return r


def current_docs(db: Session, onb: SalesOnboarding) -> Dict[str, SalesOnboardingDocument]:
    rows = (db.query(SalesOnboardingDocument)
            .filter(SalesOnboardingDocument.onboarding_id == onb.id,
                    SalesOnboardingDocument.is_current.is_(True)).all())
    return {d.kind: d for d in rows}


def checklist(db: Session, onb: SalesOnboarding, *, with_file_names: bool) -> Dict[str, Any]:
    """Status of every item. `with_file_names=False` is the Sr EVP's version:
    done / missing only."""
    docs = current_docs(db, onb)
    app_missing = [APPLICATION_LABELS[f] for f in APPLICATION_FIELDS if not getattr(onb, f)]
    items = [{"key": "application", "label": "Application",
              "done": not app_missing, "missing_fields": app_missing}]
    for k in DOC_KINDS:
        d = docs.get(k)
        it = {"key": k, "label": DOC_LABELS[k], "done": d is not None}
        if with_file_names and d is not None:
            it.update({"document_id": d.id, "filename": d.filename,
                       "uploaded_at": d.uploaded_at.isoformat()})
        items.append(it)
    return {"status": onb.status, "items": items,
            "complete": all(i["done"] for i in items),
            "missing": [i["label"] for i in items if not i["done"]],
            "submitted_at": onb.submitted_at.isoformat() if onb.submitted_at else None,
            "approved_at": onb.approved_at.isoformat() if onb.approved_at else None,
            "returned_note": onb.returned_note if onb.status == ONB_RETURNED else None}


def application(onb: SalesOnboarding) -> Dict[str, Any]:
    return {f: (getattr(onb, f).isoformat() if isinstance(getattr(onb, f), date)
                else getattr(onb, f)) for f in APPLICATION_FIELDS}


def _editable(onb: SalesOnboarding) -> None:
    if onb.status in (ONB_SUBMITTED, ONB_APPROVED):
        raise HTTPException(status_code=409, detail="This onboarding has been submitted. "
                                                    "An owner can send it back for changes.")


def save_application(db: Session, onb: SalesOnboarding, data: Dict[str, Any]) -> None:
    _editable(onb)
    for f in APPLICATION_FIELDS:
        if f not in data:
            continue
        v = data[f]
        if f == "start_date":
            if v in (None, ""):
                onb.start_date = None
            else:
                try:
                    onb.start_date = date.fromisoformat(str(v)[:10])
                except ValueError:
                    raise HTTPException(status_code=422, detail="Start date must be a date.")
            continue
        v = (str(v).strip() if v is not None else "")[:500]
        if f == "email" and v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise HTTPException(status_code=422, detail="Enter a valid email address.")
        setattr(onb, f, v or None)


async def upload(db: Session, onb: SalesOnboarding, kind: str, file: UploadFile, actor: User) -> SalesOnboardingDocument:
    _editable(onb)
    if kind not in DOC_KINDS:
        raise HTTPException(status_code=422, detail="That document is not on the checklist.")
    data, content_type, filename = await storage.read_upload(file)
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="That file is too large. The limit is 10 MB.")
    if content_type not in ACCEPTED:
        raise HTTPException(status_code=400, detail="Upload a PDF or a photo (JPG, PNG, WEBP, HEIC).")
    prior = (db.query(SalesOnboardingDocument)
             .filter(SalesOnboardingDocument.onboarding_id == onb.id,
                     SalesOnboardingDocument.kind == kind).all())
    for p in prior:
        p.is_current = False
    stored = storage.put(onb.brand_sales_org_id, STORAGE_KIND, data, content_type, db=db)
    safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", (filename or kind))[:120] or kind
    d = SalesOnboardingDocument(
        onboarding_id=onb.id, kind=kind, version=len(prior) + 1, is_current=True,
        storage_backend=stored["storage_backend"], storage_key=stored["storage_key"],
        filename=safe_name, content_type=content_type, size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(), uploaded_by=actor.id)
    db.add(d)
    db.flush()
    log_action(db, None, actor.id, action="sales_onboarding_document_uploaded",
               target_type="user", target_id=onb.user_id,
               brand_sales_org_id=onb.brand_sales_org_id,
               after={"document_id": d.id, "kind": kind, "version": d.version},
               note="File contents are not recorded.", commit=False)
    return d


def submit(db: Session, onb: SalesOnboarding) -> None:
    _editable(onb)
    c = checklist(db, onb, with_file_names=False)
    if not c["complete"]:
        raise HTTPException(status_code=422, detail="Still missing: " + ", ".join(c["missing"]))
    onb.status = ONB_SUBMITTED
    onb.submitted_at = datetime.utcnow()
    onb.returned_note = None


def approve(db: Session, onb: SalesOnboarding, actor: User) -> None:
    c = checklist(db, onb, with_file_names=False)
    if not c["complete"]:
        raise HTTPException(status_code=422, detail="Cannot approve - still missing: " + ", ".join(c["missing"]))
    onb.status = ONB_APPROVED
    onb.approved_by = actor.id
    onb.approved_at = datetime.utcnow()
    log_action(db, None, actor.id, action="sales_onboarding_approved",
               target_type="user", target_id=onb.user_id,
               brand_sales_org_id=onb.brand_sales_org_id, commit=False)


def send_back(db: Session, onb: SalesOnboarding, actor: User, note: str) -> None:
    onb.status = ONB_RETURNED
    onb.returned_note = (note or "").strip()[:1000] or "Please review and resubmit."
    onb.approved_at = None
    onb.approved_by = None
    log_action(db, None, actor.id, action="sales_onboarding_returned",
               target_type="user", target_id=onb.user_id,
               brand_sales_org_id=onb.brand_sales_org_id, commit=False)


def read_document(db: Session, doc: SalesOnboardingDocument, onb: SalesOnboarding,
                  actor: User, action: str) -> bytes:
    data = storage.fetch(doc.storage_backend, doc.storage_key, db=db)
    log_action(db, None, actor.id, action="sales_onboarding_document_" + action,
               target_type="user", target_id=onb.user_id,
               brand_sales_org_id=onb.brand_sales_org_id,
               after={"document_id": doc.id, "kind": doc.kind, "version": doc.version},
               note="File contents are not recorded.", commit=False)
    return data
