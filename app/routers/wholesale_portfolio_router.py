"""Portfolio Acquisitions - /wholesale/portfolios.

    GET    /wholesale/portfolios                              list (with summary cards)
    POST   /wholesale/portfolios                              create SELLER PORTFOLIO #nnn
    GET    /wholesale/portfolios/{id}                         detail: head, summary, property rows
    PATCH  /wholesale/portfolios/{id}                         edit name / seller / status / notes / target
    POST   /wholesale/portfolios/{id}/archive                 archive or restore ({"archived": bool})
    DELETE /wholesale/portfolios/{id}                         only when it has no properties
    POST   /wholesale/portfolios/{id}/items/add               link existing properties
    POST   /wholesale/portfolios/{id}/items/remove            take properties out (records are kept)
    PATCH  /wholesale/portfolios/{id}/items/{item}            the seller's listing (price, rent, type...)
    GET    /wholesale/portfolios/{id}/items/{item}/evaluation the property evaluation (existing underwriting)
    POST   /wholesale/portfolios/{id}/items/{item}/evaluation a person's override, with its source
    POST   /wholesale/portfolios/{id}/assign                  hand properties to a researcher
    POST   /wholesale/portfolios/{id}/imports                 stage a CSV / XLSX (multipart)
    POST   /wholesale/portfolios/{id}/imports/rows            stage typed / photo-transcribed rows
    GET    /wholesale/portfolios/{id}/imports                 the import history
    GET    /wholesale/portfolios/{id}/imports/{src}           the preview
    PATCH  /wholesale/portfolios/{id}/imports/{src}/rows/{n}  correct a row / decide create-link-skip
    POST   /wholesale/portfolios/{id}/imports/{src}/commit    create and link, as reviewed
    POST   /wholesale/portfolios/{id}/imports/{src}/undo      take a committed import back out
    POST   /wholesale/portfolios/{id}/imports/{src}/discard   drop an uncommitted preview
    GET    /wholesale/portfolios/{id}/packages                scenarios + side-by-side comparison
    POST   /wholesale/portfolios/{id}/packages                create a scenario
    PATCH  /wholesale/portfolios/{id}/packages/{pkg}          rename, discount, add/remove, offers, archive
    POST   /wholesale/portfolios/{id}/packages/{pkg}/duplicate
    GET    .../export.csv  (inventory, one property, one package, the comparison)

Gated like every Wholesale route: tenant user, the wholesale_real_estate
feature, writes refused for observers, and the custom-access engine's
"portfolios" module (wholesale_access) for team logins with custom access.
Nothing here sends a message, calls a paid provider or publishes anything.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.models.models import User
from app.services import wholesale_portfolio as PF
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale", tags=["wholesale-portfolios"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _csv(body: str, name: str) -> Response:
    return Response(content=body, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % name,
                             "Cache-Control": "no-store"})


def _detail(db: Session, org_id: str, user: User, p) -> Dict[str, Any]:
    settings = svc.resolve_settings(db, org_id, commit=False)
    rows = PF.item_rows(db, org_id, p, PF.visible_ids(db, org_id, user), settings)
    return {"portfolio": PF.portfolio_head(p), "summary": PF.summarize(rows), "rows": rows,
            "notice": PF.ESTIMATE_NOTICE,
            "assumptions": {"transaction_cost_percent": float(getattr(settings, "transaction_cost_percent", 0) or 0),
                            "transaction_cost_flat": float(getattr(settings, "transaction_cost_flat", 0) or 0),
                            "investor_percentage": float(getattr(settings, "investor_percentage", 0) or 0)
                            if getattr(settings, "investor_percentage", None) is not None else None,
                            "default_wholesale_fee": float(getattr(settings, "default_wholesale_fee", 0) or 0)
                            if getattr(settings, "default_wholesale_fee", None) is not None else None,
                            "note": "This workspace's Wholesale Settings. The MAO formula and transaction costs "
                                    "use these; change them in Wholesale Settings."}}


# ── portfolios ──────────────────────────────────────────────────────────────

class PortfolioIn(BaseModel):
    name: Optional[str] = None
    seller_name: Optional[str] = None
    seller_lead_id: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    target_offer_total: Optional[float] = None
    is_test: Optional[bool] = None


@router.get("/portfolios")
def list_portfolios(include_archived: bool = Query(False), db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        return {"portfolios": []}
    return {"portfolios": PF.list_portfolios(db, org_id, user, include_archived), "notice": PF.ESTIMATE_NOTICE}


@router.post("/portfolios")
def create_portfolio(payload: PortfolioIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                     _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.create_portfolio(db, org_id, user, payload.model_dump(exclude_unset=True))
    db.commit()
    return _detail(db, org_id, user, p)


@router.get("/portfolios/assignees")
def assignees(db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    """Who research can be handed to: active people on this workspace's team."""
    from app.services import wholesale_team as T
    org_id = svc.read_org_id(db, user)
    if not org_id:
        return {"items": []}
    rows = T.people_q(db, org_id).filter(User.is_active.isnot(False)).order_by(User.full_name.asc()).all()
    return {"items": [{"id": u.id, "name": u.full_name or u.email} for u in rows]}


@router.get("/portfolios/{pid}")
def get_portfolio(pid: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    return _detail(db, org_id, user, p)


@router.patch("/portfolios/{pid}")
def update_portfolio(pid: str, payload: PortfolioIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    data = payload.model_dump(exclude_unset=True)
    data.pop("is_test", None)                # set at creation; never flipped afterwards
    PF.update_portfolio(db, org_id, user, p, data)
    db.commit()
    return _detail(db, org_id, user, p)


@router.post("/portfolios/{pid}/archive")
def archive_portfolio(pid: str, body: Dict[str, Any], db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    PF.archive_portfolio(db, org_id, user, p, bool((body or {}).get("archived", True)))
    db.commit()
    return PF.portfolio_head(p)


@router.delete("/portfolios/{pid}")
def delete_portfolio(pid: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                     _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    PF.delete_portfolio(db, org_id, user, p)
    db.commit()
    return {"ok": True}


# ── properties in the portfolio ─────────────────────────────────────────────

class IdsIn(BaseModel):
    item_ids: List[str] = []
    property_ids: List[str] = []
    also_from_packages: bool = False
    user_id: Optional[str] = None


@router.post("/portfolios/{pid}/items/add")
def add_items(pid: str, payload: IdsIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
              _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    out = PF.add_existing(db, org_id, user, p, payload.property_ids)
    db.commit()
    return {**out, **_detail(db, org_id, user, p)}


@router.post("/portfolios/{pid}/items/remove")
def remove_items(pid: str, payload: IdsIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                 _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    out = PF.remove_items(db, org_id, user, p, payload.item_ids, payload.also_from_packages)
    db.commit()
    return {**out, **_detail(db, org_id, user, p)}


@router.post("/portfolios/{pid}/assign")
def assign(pid: str, payload: IdsIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
           _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    if not payload.user_id:
        raise HTTPException(status_code=422, detail="Choose who to assign them to.")
    out = PF.assign_items(db, org_id, user, p, payload.item_ids, payload.user_id)
    db.commit()
    return out


@router.patch("/portfolios/{pid}/items/{item_id}")
def update_item(pid: str, item_id: str, body: Dict[str, Any], db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    item, prop = PF.get_item(db, org_id, p, item_id, user)
    PF.update_item(db, org_id, user, p, item, body or {})
    db.commit()
    settings = svc.resolve_settings(db, org_id, commit=False)
    return PF.row_for(item, prop, svc.deal_for_property(db, org_id, prop.id), settings)


@router.get("/portfolios/{pid}/items/{item_id}/evaluation")
def get_evaluation(pid: str, item_id: str, rent_monthly: Optional[float] = Query(None),
                   db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    item, prop = PF.get_item(db, org_id, p, item_id, user)
    return PF.evaluation(db, org_id, user, p, item, prop,
                         {"rent_monthly": rent_monthly} if rent_monthly is not None else None)


@router.post("/portfolios/{pid}/items/{item_id}/evaluation")
def override_evaluation(pid: str, item_id: str, body: Dict[str, Any], db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    item, prop = PF.get_item(db, org_id, p, item_id, user)
    PF.override(db, org_id, user, p, item, prop, body or {})
    db.commit()
    return PF.evaluation(db, org_id, user, p, item, prop)


# ── imports ─────────────────────────────────────────────────────────────────

@router.get("/portfolios/{pid}/imports")
def list_imports(pid: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    from app.models.wholesale_portfolio_models import WholesalePortfolioSource as S
    org_id = svc.read_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    rows = (db.query(S).filter(S.portfolio_id == p.id, S.organization_id == org_id)
            .order_by(S.created_at.desc()).all())
    out = []
    for s in rows:
        j = PF._source_json(s)
        j.pop("rows", None)
        out.append(j)
    return {"imports": out}


@router.post("/portfolios/{pid}/imports")
async def stage_import(pid: str, file: UploadFile = File(...), reference: Optional[str] = Form(None),
                       note: Optional[str] = Form(None), db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That file is larger than 10 MB.")
    s = PF.stage_file(db, org_id, user, p, raw, file.filename or "list.csv", reference, note)
    db.commit()
    return PF._source_json(s)


class RowsIn(BaseModel):
    kind: str = "manual"
    reference: Optional[str] = None
    note: Optional[str] = None
    rows: List[Dict[str, Any]] = []


@router.post("/portfolios/{pid}/imports/rows")
def stage_rows(pid: str, payload: RowsIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
               _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    s = PF.stage_rows(db, org_id, user, p, payload.kind, payload.rows, payload.reference, payload.note)
    db.commit()
    return PF._source_json(s)


@router.get("/portfolios/{pid}/imports/{sid}")
def get_import(pid: str, sid: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    return PF._source_json(PF.get_source(db, org_id, p, sid))


@router.patch("/portfolios/{pid}/imports/{sid}/rows/{n}")
def edit_import_row(pid: str, sid: str, n: int, body: Dict[str, Any], db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    s = PF.get_source(db, org_id, p, sid)
    PF.edit_row(db, org_id, p, s, n, body or {})
    db.commit()
    return PF._source_json(s)


@router.post("/portfolios/{pid}/imports/{sid}/commit")
def commit_import(pid: str, sid: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                  _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    s = PF.get_source(db, org_id, p, sid)
    out = PF.commit_source(db, org_id, user, p, s)
    db.commit()
    return {"result": out, "import": PF._source_json(s)}


@router.post("/portfolios/{pid}/imports/{sid}/undo")
def undo_import(pid: str, sid: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    s = PF.get_source(db, org_id, p, sid)
    out = PF.undo_source(db, org_id, user, p, s)
    db.commit()
    return {"result": out, "import": PF._source_json(s)}


@router.post("/portfolios/{pid}/imports/{sid}/discard")
def discard_import(pid: str, sid: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user),
                   _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    s = PF.get_source(db, org_id, p, sid)
    if s.status != "preview":
        raise HTTPException(status_code=409, detail="Only a preview can be discarded.")
    s.status = "discarded"
    db.commit()
    return {"ok": True}


# ── packages ────────────────────────────────────────────────────────────────

class PackageIn(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    discount_mode: Optional[str] = None
    discount_value: Optional[float] = None
    item_ids: List[str] = []
    all: bool = False
    status: Optional[str] = None
    add_item_ids: List[str] = []
    remove_item_ids: List[str] = []
    offers: List[Dict[str, Any]] = []
    refresh_snapshots: bool = False


def _packages(db: Session, org_id: str, user: User, p) -> Dict[str, Any]:
    pkgs = PF.packages(db, org_id, user, p)
    return {"packages": pkgs, "comparison": PF.compare(pkgs), "notice": PF.ESTIMATE_NOTICE}


@router.get("/portfolios/{pid}/packages")
def list_packages(pid: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    return _packages(db, org_id, user, p)


@router.post("/portfolios/{pid}/packages")
def create_package(pid: str, payload: PackageIn, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    k = PF.create_package(db, org_id, user, p, payload.model_dump(exclude_unset=True))
    db.commit()
    return {"created_id": k.id, **_packages(db, org_id, user, p)}


@router.patch("/portfolios/{pid}/packages/{pkg_id}")
def update_package(pid: str, pkg_id: str, payload: PackageIn, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    k = PF.get_package(db, org_id, p, pkg_id)
    if PF.package_view(db, org_id, p, k, PF._rows_by_item(db, org_id, p, svc.resolve_settings(db, org_id, commit=False)),
                       PF.visible_ids(db, org_id, user)) is None:
        raise HTTPException(status_code=404, detail="Package not found")
    PF.update_package(db, org_id, user, p, k, payload.model_dump(exclude_unset=True))
    db.commit()
    return _packages(db, org_id, user, p)


@router.post("/portfolios/{pid}/packages/{pkg_id}/duplicate")
def duplicate_package(pid: str, pkg_id: str, body: Optional[Dict[str, Any]] = None, db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    k = PF.get_package(db, org_id, p, pkg_id)
    if PF.package_view(db, org_id, p, k, PF._rows_by_item(db, org_id, p, svc.resolve_settings(db, org_id, commit=False)),
                       PF.visible_ids(db, org_id, user)) is None:
        raise HTTPException(status_code=404, detail="Package not found")
    copy = PF.duplicate_package(db, org_id, user, p, k, (body or {}).get("name"))
    db.commit()
    return {"created_id": copy.id, **_packages(db, org_id, user, p)}


# ── exports (gated as "export" by the access engine) ────────────────────────

@router.get("/portfolios/{pid}/export.csv")
def export_inventory(pid: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = svc.read_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    settings = svc.resolve_settings(db, org_id, commit=False)
    rows = PF.item_rows(db, org_id, p, PF.visible_ids(db, org_id, user), settings)
    return _csv(PF.export_inventory(p, rows), "portfolio-%03d-inventory.csv" % p.number)


@router.get("/portfolios/{pid}/items/{item_id}/export.csv")
def export_item(pid: str, item_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = svc.read_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    item, prop = PF.get_item(db, org_id, p, item_id, user)
    ev = PF.evaluation(db, org_id, user, p, item, prop)
    return _csv(PF.export_item(ev), "portfolio-%03d-property-%s.csv" % (p.number, item.source_row or item.id[:8]))


@router.get("/portfolios/{pid}/packages/compare/export.csv")
def export_compare(pid: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = svc.read_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    pkgs = PF.packages(db, org_id, user, p)
    return _csv(PF.export_compare(p, PF.compare(pkgs)), "portfolio-%03d-package-comparison.csv" % p.number)


@router.get("/portfolios/{pid}/packages/{pkg_id}/export.csv")
def export_package(pid: str, pkg_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    org_id = svc.read_org_id(db, user)
    p = PF.get_portfolio(db, org_id, pid)
    k = PF.get_package(db, org_id, p, pkg_id)
    v = PF.package_view(db, org_id, p, k, PF._rows_by_item(db, org_id, p, svc.resolve_settings(db, org_id, commit=False)),
                        PF.visible_ids(db, org_id, user))
    if v is None:
        raise HTTPException(status_code=404, detail="Package not found")
    return _csv(PF.export_package(p, v), "portfolio-%03d-package.csv" % p.number)
