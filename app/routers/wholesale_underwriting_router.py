"""/wholesale/deals/{id}/underwriting - the Deal Intelligence Engine.

    GET  /wholesale/deals/{id}/underwriting            the analysis (last run's assumptions) + history
    POST /wholesale/deals/{id}/underwriting            "Analyze deal" with these assumptions; kept in history
    POST /wholesale/deals/{id}/repairs/itemized        an itemized repair estimate (lines a person priced,
                                                       or a field visit's walkthrough) -> MANUAL estimate
    POST /wholesale/deals/{id}/comps/import            closed comparable sales from a CSV (each with a source)

Nothing here sends anything or calls an outside service. Comparable sales are
only what a person entered or imported with a source; an estimate is never a
sale. See app/services/wholesale_underwriting.py.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.models.models import User
from app.models.wholesale_models import WholesaleComp, WholesaleEvent
from app.services import wholesale_service as svc
from app.services import wholesale_underwriting as UW
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale", tags=["wholesale-underwriting"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


def _last_assumptions(db: Session, org_id: str, deal_id: str) -> Dict[str, Any]:
    e = (db.query(WholesaleEvent).filter(WholesaleEvent.organization_id == org_id,
                                         WholesaleEvent.deal_id == deal_id,
                                         WholesaleEvent.action == "underwriting.run")
         .order_by(WholesaleEvent.created_at.desc()).first())
    if e is None:
        return {}
    try:
        d = json.loads(e.details) if isinstance(e.details, str) else (e.details or {})
    except (TypeError, ValueError):
        return {}
    return d.get("changed_assumptions") or {}


@router.get("/deals/{deal_id}/underwriting")
def get_underwriting(deal_id: str, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    try:
        out = UW.analyze(db, org_id, deal, _last_assumptions(db, org_id, deal.id))
    except ValueError:
        out = UW.analyze(db, org_id, deal, {})
    out["history"] = UW.history(db, org_id, deal.id)
    return out


class AnalyzeIn(BaseModel):
    assumptions: Optional[Dict[str, Any]] = None


@router.post("/deals/{deal_id}/underwriting", dependencies=[Depends(require_not_observation)])
def run_underwriting(deal_id: str, payload: AnalyzeIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user)):
    """ANALYZE DEAL. Recomputes ARV and MAO from the comps and repairs on file
    (the deal's own calculation), then builds the analysis with these
    assumptions and keeps the run in the history."""
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    base = _last_assumptions(db, org_id, deal.id)
    base.update(payload.assumptions or {})
    svc.recalculate_analysis(db, org_id, deal, user)
    try:
        out = UW.analyze(db, org_id, deal, base)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    UW.record_run(db, org_id, deal, out, user)
    db.commit()
    out["history"] = UW.history(db, org_id, deal.id)
    return out


class RepairLine(BaseModel):
    category: Optional[str] = None
    item: str
    room: Optional[str] = None
    quantity: float = 1
    unit: Optional[str] = "each"
    unit_cost: float
    note: Optional[str] = None


class ItemizedIn(BaseModel):
    items: Optional[List[RepairLine]] = None
    from_visit: Optional[str] = None          # an appointment id: use its walkthrough repairs
    contingency_pct: float = 15
    quality: str = "standard"
    notes: Optional[str] = None


@router.post("/deals/{deal_id}/repairs/itemized", dependencies=[Depends(require_not_observation)])
def itemized_repairs(deal_id: str, payload: ItemizedIn, db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user)):
    """An itemized estimate becomes the deal's MANUAL repair estimate (low /
    base / high), with every line kept in its provenance."""
    from app.services import wholesale_repairs as REP
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    items = [i.model_dump() for i in (payload.items or [])]
    source = "itemized estimate"
    if payload.from_visit:
        items = _visit_lines(db, org_id, user, deal, payload.from_visit)
        source = "field visit walkthrough"
    try:
        est = UW.itemized(items, payload.contingency_pct, payload.quality)
        REP.record(db, org_id, deal, status=REP.MANUAL_ESTIMATE, amount=est["base"], low=est["low"],
                   high=est["high"], source=source, user=user, confidence="medium",
                   notes=payload.notes or est["basis"], provenance={"itemized": est})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    svc.log_event(db, org_id, "repairs.itemized", actor_type="user", actor_user_id=user.id, deal_id=deal.id,
                  summary="Itemized repairs: $%s base ($%s-$%s), %s lines" % (
                      format(int(est["base"]), ","), format(int(est["low"]), ","), format(int(est["high"]), ","),
                      len(est["lines"])),
                  details={"source": source, "subtotal": est["subtotal"], "contingency_pct": est["contingency_pct"],
                           "quality": est["quality"]})
    svc.recalculate_analysis(db, org_id, deal, user)
    db.commit()
    return {"estimate": est, "analysis": UW.analyze(db, org_id, deal, _last_assumptions(db, org_id, deal.id))}


def _visit_lines(db: Session, org_id: str, user: User, deal, appt_id: str) -> List[Dict[str, Any]]:
    from app.services import wholesale_appointments as A
    from app.services import wholesale_field_visits as V
    appt = A.get_visible(db, org_id, user, appt_id)
    if appt.deal_id and appt.deal_id != deal.id:
        raise HTTPException(status_code=422, detail="That visit belongs to another deal.")
    repairs = (V.visit_json(db, org_id, appt) or {}).get("repairs") or {}
    lines = [{"category": item, "item": item, "quantity": 1, "unit": "lump sum", "unit_cost": line.get("amount"),
              "note": line.get("note")}
             for item, line in (repairs.get("items") or {}).items() if line.get("amount") is not None]
    if not lines:
        raise HTTPException(status_code=422, detail="That visit has no priced repair lines yet.")
    return lines


# ── comparable sales from a CSV ─────────────────────────────────────────────

_COLS = {"address": "street_address", "street_address": "street_address", "street": "street_address",
         "city": "city", "state": "state", "zip": "zip_code", "zip_code": "zip_code",
         "sale_price": "sale_price", "sold_price": "sale_price", "price": "sale_price",
         "sale_date": "sale_date", "sold_date": "sale_date", "close_date": "sale_date",
         "sqft": "square_feet", "square_feet": "square_feet", "living_area": "square_feet",
         "beds": "bedrooms", "bedrooms": "bedrooms", "baths": "bathrooms", "bathrooms": "bathrooms",
         "year_built": "year_built", "distance": "distance_miles", "distance_miles": "distance_miles",
         "lot_size_sqft": "lot_size_sqft", "property_type": "property_type", "sale_type": "sale_type",
         "latitude": "latitude", "longitude": "longitude",
         "source": "source_reference", "source_reference": "source_reference", "mls": "source_reference",
         "mls_number": "source_reference", "notes": "notes"}
_NUMS = {"sale_price": float, "square_feet": int, "bedrooms": float, "bathrooms": float, "year_built": int,
         "distance_miles": float, "lot_size_sqft": int, "latitude": float, "longitude": float}
MAX_ROWS = 200


def _parse_date(v: str) -> Optional[date]:
    from datetime import datetime as _dt
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return _dt.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


@router.post("/deals/{deal_id}/comps/import", dependencies=[Depends(require_not_observation)])
async def import_comps(deal_id: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                       user: User = Depends(require_tenant_user)):
    """Closed sales from an MLS / agent / title export. Every row needs an
    address, a sale price, a sale date and a SOURCE (MLS number or document);
    rows without one are refused, never guessed. Imported comps are MANUAL in
    origin; a person can verify them afterwards. The same sale is never added
    twice."""
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    raw = await file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That file is over 2 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="The file has no header row.")
    cols = {f: _COLS.get(f.strip().lower().replace(" ", "_")) for f in reader.fieldnames}
    need = {"street_address", "sale_price", "sale_date", "source_reference"}
    have = {v for v in cols.values() if v}
    if need - have:
        raise HTTPException(status_code=422, detail="Missing columns: %s. Every comp needs an address, sale price, "
                                                    "sale date and source (MLS number or document)."
                            % ", ".join(sorted(need - have)))
    existing = {(c.street_address or "").strip().upper() + "|" + str(c.sale_date) + "|" + str(int(c.sale_price or 0))
                for c in db.query(WholesaleComp).filter(WholesaleComp.organization_id == org_id,
                                                        WholesaleComp.deal_id == deal.id).all()}
    added, refused = [], []
    for n, row in enumerate(reader, 2):
        if n - 1 > MAX_ROWS:
            refused.append({"row": n, "why": "only the first %s rows are read" % MAX_ROWS})
            break
        data: Dict[str, Any] = {}
        for f, key in cols.items():
            if key and (row.get(f) or "").strip():
                data[key] = row[f].strip()
        why = [k for k in need if not data.get(k)]
        if why:
            refused.append({"row": n, "why": "missing " + ", ".join(sorted(why))})
            continue
        try:
            for k, t in _NUMS.items():
                if k in data:
                    data[k] = t(float(str(data[k]).replace("$", "").replace(",", "")))
        except ValueError:
            refused.append({"row": n, "why": "a number could not be read"})
            continue
        sd = _parse_date(data.pop("sale_date"))
        if sd is None or sd > date.today():
            refused.append({"row": n, "why": "sale date missing, unreadable or in the future"})
            continue
        if data["sale_price"] <= 0:
            refused.append({"row": n, "why": "sale price must be above 0"})
            continue
        key = data["street_address"].upper() + "|" + str(sd) + "|" + str(int(data["sale_price"]))
        if key in existing:
            refused.append({"row": n, "why": "already on this deal"})
            continue
        existing.add(key)
        comp = WholesaleComp(organization_id=org_id, deal_id=deal.id, source=svc.VALUE_MANUAL,
                             verification_state="manual", entered_by_id=user.id, sale_date=sd,
                             is_test=bool(getattr(deal, "is_test", False)), included=True,
                             provenance=json.dumps({"imported_from": (file.filename or "csv")[:120], "row": n}),
                             **{k: v for k, v in data.items() if hasattr(WholesaleComp, k)})
        if comp.sale_type:
            comp.sale_type = comp.sale_type.strip().lower().replace(" ", "_")
        db.add(comp)
        added.append(data["street_address"])
    db.flush()
    if added:
        svc.log_event(db, org_id, "comps.imported", actor_type="user", actor_user_id=user.id, deal_id=deal.id,
                      summary="%s comparable sale%s imported from %s" % (len(added), "" if len(added) == 1 else "s",
                                                                         file.filename or "a CSV"),
                      details={"added": len(added), "refused": refused[:50]})
    svc.recalculate_analysis(db, org_id, deal, user)
    db.commit()
    return {"added": len(added), "refused": refused,
            "analysis": UW.analyze(db, org_id, deal, _last_assumptions(db, org_id, deal.id))}
