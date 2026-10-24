"""Buyer network: reviewable imports of the operator's buyer file, and the DFW segment.

    POST   /wholesale/buyers/imports                     upload CSV / XLSX -> batch with suggested mapping
    GET    /wholesale/buyers/imports                     import history
    GET    /wholesale/buyers/imports/{id}                batch + counts
    PUT    /wholesale/buyers/imports/{id}/mapping        change the column mapping
    POST   /wholesale/buyers/imports/{id}/analyze        validate + duplicate check (nothing written to buyers)
    GET    /wholesale/buyers/imports/{id}/rows?state=    review rows (new / update / conflict / error / ...)
    PATCH  /wholesale/buyers/imports/{id}/rows/{row}     decide a conflict, fix a bad row
    POST   /wholesale/buyers/imports/{id}/commit         apply (again = retry the rows fixed since)
    POST   /wholesale/buyers/imports/{id}/rollback       undo the batch, row by row, with a report
    GET    /wholesale/buyers/imports/{id}/errors.csv     the rows that did not go in, and why
    GET    /wholesale/buyers/network?segment=dfw&status= the network with status, DFW flags, evidence

Field logins are already fenced off /wholesale/buyers (wholesale_team.FIELD_BLOCKED_PREFIXES).
The old one-shot POST /wholesale/buyers/import keeps working unchanged.
"""
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.models.models import User
from app.services import wholesale_buyer_network as NET
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale", tags=["wholesale-buyer-network"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _read(db, user):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    return org_id


def _log(db, org_id, user, action, summary, after=None):
    svc.log_event(db, org_id, action, actor_type=svc.ACTOR_USER, actor_user_id=user.id, summary=summary[:250],
                  after=after)


@router.post("/buyers/imports")
async def upload(file: UploadFile = File(...), list_name: Optional[str] = Form(None),
                 is_test: bool = Form(False), db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    content = await file.read()
    b = NET.create_batch(db, org_id, user, content, file.filename, list_name, is_test)
    _log(db, org_id, user, "buyers.import_uploaded", "Buyer file uploaded: %s (%d rows)" % (b.filename, b.row_count),
         {"batch_id": b.id})
    db.commit()
    out = NET.batch_json(b)
    out["sample"] = [NET.row_json(r) for r in NET._rows(db, b).limit(10).all()]
    return out


@router.get("/buyers/imports")
def history(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer),
            limit: int = Query(50, le=200)):
    from app.models.wholesale_buyer_import_models import WholesaleBuyerImportBatch
    org_id = _read(db, user)
    rows = (db.query(WholesaleBuyerImportBatch).filter(WholesaleBuyerImportBatch.organization_id == org_id)
            .order_by(WholesaleBuyerImportBatch.created_at.desc()).limit(limit).all())
    return {"imports": [NET.batch_json(b) for b in rows]}


@router.get("/buyers/imports/{batch_id}")
def get_import(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    b = NET.get_batch(db, org_id, batch_id)
    out = NET.batch_json(b)
    out["sample"] = [NET.row_json(r) for r in NET._rows(db, b).limit(10).all()]
    return out


class MappingIn(BaseModel):
    mapping: Dict[str, Optional[str]]


@router.put("/buyers/imports/{batch_id}/mapping")
def put_mapping(batch_id: str, payload: MappingIn, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    b = NET.get_batch(db, org_id, batch_id)
    NET.set_mapping(db, b, payload.mapping)
    db.commit()
    return NET.batch_json(b)


@router.post("/buyers/imports/{batch_id}/analyze")
def analyze(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
            _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    b = NET.get_batch(db, org_id, batch_id)
    counts = NET.analyze(db, b)
    db.commit()
    return {**NET.batch_json(b), "counts": counts}


@router.get("/buyers/imports/{batch_id}/rows")
def rows(batch_id: str, state: Optional[str] = None, limit: int = Query(100, le=1000), offset: int = 0,
         db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    b = NET.get_batch(db, org_id, batch_id)
    q = NET._rows(db, b, state)
    return {"total": q.count(), "rows": [NET.row_json(r) for r in q.offset(offset).limit(limit).all()]}


class DecideIn(BaseModel):
    decision: Optional[str] = None          # create | merge | skip
    buyer_id: Optional[str] = None          # merge target
    fix: Optional[Dict[str, str]] = None    # {header: corrected cell}


@router.patch("/buyers/imports/{batch_id}/rows/{row_id}")
def decide(batch_id: str, row_id: str, payload: DecideIn, db: Session = Depends(get_db),
           user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    b = NET.get_batch(db, org_id, batch_id)
    r = NET.decide(db, b, row_id, user, payload.decision, payload.buyer_id, payload.fix)
    db.commit()
    return NET.row_json(r)


@router.post("/buyers/imports/{batch_id}/commit")
def commit(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
           _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    b = NET.get_batch(db, org_id, batch_id)
    out = NET.commit(db, b, user)
    _log(db, org_id, user, "buyers.import_committed",
         "Buyer import %s: %d created, %d updated, %d skipped, %d waiting, %d errors" % (
             b.filename, out["created"], out["updated"], out["skipped"], out["waiting"], out["errors"]),
         {"batch_id": b.id, **out})
    db.commit()
    return {**NET.batch_json(b), "result": out}


@router.post("/buyers/imports/{batch_id}/rollback")
def rollback(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
             _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    b = NET.get_batch(db, org_id, batch_id)
    rep = NET.rollback(db, b, user)
    _log(db, org_id, user, "buyers.import_rolled_back",
         "Buyer import %s rolled back: %d removed, %d turned off, %d restored" % (
             b.filename, len(rep["removed"]), len(rep["deactivated"]), len(rep["restored"])),
         {"batch_id": b.id, "removed": len(rep["removed"]), "deactivated": len(rep["deactivated"]),
          "restored": len(rep["restored"])})
    db.commit()
    return {**NET.batch_json(b), "rollback": rep}


@router.get("/buyers/imports/{batch_id}/errors.csv")
def errors_csv(batch_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.utils.content_disposition import content_disposition
    org_id = _read(db, user)
    b = NET.get_batch(db, org_id, batch_id)
    return Response(content=NET.error_report_csv(db, b), media_type="text/csv",
                    headers={"Content-Disposition": content_disposition("buyer-import-problems.csv")})


@router.get("/buyers/network")
def network(segment: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None,
            state: Optional[str] = None, county: Optional[str] = None, include_test: bool = False,
            limit: int = Query(100, le=500), offset: int = 0,
            db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = _read(db, user)
    return NET.network(db, org_id, segment=segment, status=status, q=q, state=state, county=county,
                       include_test=include_test, limit=limit, offset=offset)
