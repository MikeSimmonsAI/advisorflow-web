"""Explicit import destinations and the business-import routes (#470 Stage B).

GET  /intake/destinations                  where THIS person may import, server-decided
     /intake/sales/{brand_sales_org_id}/...  EvoSys Sales (Opportunity)
     /intake/acquisition/{org_id}/...        one cleaning customer's Acquisition pipeline

The target is in the URL of every call and is re-checked against the actor
each time - never taken from the browser's last-open customer. Upload -> map
-> analyze -> review -> commit -> report/rollback. Nothing is sent.
"""
from typing import Dict, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.import_models import ImportBatch, ImportStagedRow
from app.models.models import Organization, User
from app.services.intake import acquisition_import as AQ
from app.services.intake import business_import as BI
from app.services.intake import sales_import as SI

router = APIRouter(prefix="/intake", tags=["intake"])


def _audit(db, org_id, user, action, batch_id, details=None):
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, org_id, user.id, action, "import_batch", batch_id, details=details)
    except Exception:  # noqa: BLE001 - an audit hiccup never undoes the import step
        pass


# ── where may I import? ─────────────────────────────────────────────────────
@router.get("/destinations")
def destinations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.services.platform_owner import is_platform_owner, is_platform_pseudo_org
    god = is_platform_owner(user)
    out = []
    # 1. A customer's own contacts and leads (the guided importer).
    if god:
        orgs = (db.query(Organization.id, Organization.name).order_by(Organization.name).limit(1000).all())
        tenant = [{"id": i, "name": n} for i, n in orgs if not is_platform_pseudo_org(i)]
    else:
        from app.services.lead_scope import active_workspace_org_id
        oid = active_workspace_org_id(user, db)
        o = db.query(Organization).filter(Organization.id == oid).first() if oid and not is_platform_pseudo_org(oid) else None
        tenant = [{"id": o.id, "name": o.name}] if o else []
    out.append({"key": "tenant_contacts", "label": "A customer's contacts and leads",
                "description": "People and leads for one customer's own CRM.", "targets": tenant,
                "path": "/imports/new"})
    # 2. EvoSys Sales - companies EvoSys sells its software to.
    out.append({"key": SI.DESTINATION, "label": "EvoSys Sales prospects",
                "description": "Companies EvoSys sells its own software to (for example cleaning companies for the Growth Pilot).",
                "targets": SI.targets(db, user), "profiles": [{"key": k, "label": v["label"]} for k, v in SI.PROFILES.items()],
                "path": "/imports/sales"})
    # 3. One cleaning customer's Acquisition pipeline.
    out.append({"key": AQ.DESTINATION, "label": "A cleaning customer's Acquisition prospects",
                "description": "Businesses that might buy cleaning from ONE cleaning customer.",
                "targets": AQ.targets(db, user), "path": "/imports/acquisition"})
    # 4. The owner's master pool (PR #473) - God only.
    if god:
        out.append({"key": "platform_acquisition_pool", "label": "Master pool (owner only)",
                    "description": "Businesses that buy cleaning, held for the owner to assign to customers later.",
                    "targets": [{"id": "platform", "name": "EvoSys master pool"}], "path": "/acquisition-ops/import"})
    # 5. SCI funeral program - only for that workspace's managers.
    try:
        from app.routers.program_router import _sci_importer
        prog, org = _sci_importer(db, user)
        out.append({"key": "sci", "label": "SCI program families", "description": "SCI funeral program records.",
                    "targets": [{"id": prog.organization_id, "name": getattr(org, "name", None) or "SCI"}],
                    "path": "/program/import/new"})
    except Exception:  # noqa: BLE001 - not an SCI manager here
        pass
    return {"items": [d for d in out if d["targets"]], "is_platform_owner": god}


# ── shared step routes ──────────────────────────────────────────────────────
def _routes(prefix: str, adapter: BI.Adapter, can_import, target_name, batch_org):
    def guard(db, user, target):
        if not can_import(db, user, target):
            # Same answer for "doesn't exist" and "not yours".
            raise HTTPException(403, "You can't import into this destination.")

    def load(db, user, target, batch_id):
        guard(db, user, target)
        try:
            return BI.load(db, adapter, batch_id, target)
        except BI.BusinessImportError as exc:
            raise HTTPException(404, str(exc))

    @router.get(prefix + "/batches")
    def list_batches(target: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        guard(db, user, target)
        rows = (db.query(ImportBatch).filter(ImportBatch.pipeline == adapter.pipeline,
                                             ImportBatch.source_detail == "%s:%s" % (adapter.destination, target))
                .order_by(ImportBatch.created_at.desc()).limit(50).all())
        return {"items": [BI.batch_payload(b) for b in rows]}

    @router.post(prefix + "/batches")
    async def upload(target: str, file: UploadFile = File(...), source_system: str = Form(...),
                     display_name: Optional[str] = Form(None), profile: Optional[str] = Form(None),
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        from app.services.intake.engine import MAX_UPLOAD_BYTES
        guard(db, user, target)
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, "The file is too large")
        try:
            b = BI.create_batch(db, user, adapter, target_id=target, target_name=target_name(db, target),
                                content=content, filename=file.filename or "upload.csv", source_system=source_system,
                                batch_org_id=batch_org(target), display_name=display_name, profile=profile)
        except BI.BusinessImportError as exc:
            db.rollback()
            raise HTTPException(400, str(exc))
        _audit(db, batch_org(target), user, adapter.pipeline + ".uploaded", b.id,
               {"rows": b.total_rows, "source": b.source_system, "target": target})
        db.commit()
        return BI.batch_payload(b)

    @router.get(prefix + "/batches/{batch_id}")
    def get_batch(target: str, batch_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        return BI.batch_payload(load(db, user, target, batch_id))

    @router.put(prefix + "/batches/{batch_id}/mapping")
    def put_mapping(target: str, batch_id: str, mapping: Dict[str, Optional[str]] = Body(...),
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        b = load(db, user, target, batch_id)
        try:
            BI.save_mapping(db, b, mapping)
        except BI.BusinessImportError as exc:
            raise HTTPException(422, str(exc))
        return BI.batch_payload(b)

    @router.post(prefix + "/batches/{batch_id}/analyze")
    def analyze(target: str, batch_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        b = load(db, user, target, batch_id)
        try:
            counts = BI.analyze(db, b, adapter)
        except BI.BusinessImportError as exc:
            raise HTTPException(409, str(exc))
        _audit(db, batch_org(target), user, adapter.pipeline + ".analyzed", b.id, counts)
        db.commit()
        return BI.batch_payload(b)

    @router.get(prefix + "/batches/{batch_id}/rows")
    def rows(target: str, batch_id: str, outcome: Optional[str] = Query(None), limit: int = Query(200, le=1000),
             offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        b = load(db, user, target, batch_id)
        items = [BI.row_payload(r) for r in db.query(ImportStagedRow).filter(ImportStagedRow.batch_id == b.id)
                 .order_by(ImportStagedRow.row_number).all()]
        if outcome:
            items = [i for i in items if i["outcome"] == outcome]
        return {"total": len(items), "items": items[offset:offset + limit]}

    @router.post(prefix + "/batches/{batch_id}/review")
    def review(target: str, batch_id: str, payload: dict = Body(...), db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
        b = load(db, user, target, batch_id)
        try:
            n = BI.review(db, b, user, payload.get("row_ids") or [], payload.get("decision") or "", payload.get("note"))
        except BI.BusinessImportError as exc:
            raise HTTPException(409, str(exc))
        return {"updated": n}

    @router.post(prefix + "/batches/{batch_id}/commit")
    def commit(target: str, batch_id: str, payload: Optional[dict] = Body(None), db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
        b = load(db, user, target, batch_id)
        confirm = (payload or {}).get("confirm_target_id")
        if confirm != target:
            raise HTTPException(409, "Confirm the destination: send confirm_target_id matching the destination shown.")
        try:
            report = BI.commit(db, b, user, adapter)
        except BI.BusinessImportError as exc:
            db.rollback()
            raise HTTPException(409, str(exc))
        _audit(db, batch_org(target), user, adapter.pipeline + ".committed", b.id,
               {k: v for k, v in report.items() if k != "record_ids"})
        db.commit()
        return report

    @router.post(prefix + "/batches/{batch_id}/rollback")
    def rollback(target: str, batch_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        b = load(db, user, target, batch_id)
        try:
            report = BI.rollback(db, b, user, adapter)
        except BI.BusinessImportError as exc:
            raise HTTPException(409, str(exc))
        _audit(db, batch_org(target), user, adapter.pipeline + ".rolled_back", b.id, report)
        db.commit()
        return report

    @router.get(prefix + "/batches/{batch_id}/report.csv")
    def report(target: str, batch_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        b = load(db, user, target, batch_id)
        return Response(BI.report_csv(db, b), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="%s-results.csv"' % (b.batch_code or "import")})

    return load


def _sales_name(db, bid):
    from app.models.sales_models import BrandSalesOrg
    o = db.query(BrandSalesOrg).filter(BrandSalesOrg.id == bid).first()
    return o.name if o else bid


def _org_name(db, oid):
    o = db.query(Organization).filter(Organization.id == oid).first()
    return o.name if o else oid


_sales_load = _routes("/sales/{target}", SI.ADAPTER, SI.can_import, _sales_name, lambda t: BI.PLATFORM_ORG_ID)
_routes("/acquisition/{target}", AQ.ADAPTER, AQ.can_import, _org_name, lambda t: t)


# ── Sales only: matched prospects and the source-id backfill ───────────────
@router.post("/sales/{target}/batches/{batch_id}/attach-matched")
def attach_matched(target: str, batch_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    b = _sales_load(db, user, target, batch_id)
    out = SI.attach_to_matched(db, b, user)
    _audit(db, BI.PLATFORM_ORG_ID, user, "evosys_sales.attached_matched", b.id, out)
    db.commit()
    return out


@router.get("/sales/{target}/source-ids/preview")
def backfill_preview(target: str, prefix: Optional[str] = Query(None), db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    if not SI.can_import(db, user, target):
        raise HTTPException(403, "You can't import into this destination.")
    return SI.backfill_preview(db, target, prefix)


@router.post("/sales/{target}/source-ids/apply")
def backfill_apply(target: str, payload: Optional[dict] = Body(None), db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    if not SI.can_import(db, user, target):
        raise HTTPException(403, "You can't import into this destination.")
    out = SI.backfill_apply(db, target, user, (payload or {}).get("prefix"))
    _audit(db, BI.PLATFORM_ORG_ID, user, "evosys_sales.source_ids_backfilled", target,
           {"filled": out["filled"], "conflicts": len(out["conflicts"])})
    db.commit()
    return out
