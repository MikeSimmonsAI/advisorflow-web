"""Portfolio Acquisitions - one seller's portfolio, evaluated property by property,
bought in packages the owner compares side by side.

NO SECOND ENGINE. Every number a portfolio shows for a property comes from the
property's own deal, through the code the deal room uses:

    ARV, its source and confidence      deal.arv / arv_source / arv_confidence (arv_engine)
    repairs and their status            deal.repair_estimate / repair_status (wholesale_repairs)
    maximum allowable offer             wholesale_service.gated_summary -> wholesale_analysis
                                        .calculate_offer, with the MAO gate applied
    our target offer                    deal.proposed_offer (the deal's own offer field)
    flip / rental scenarios             wholesale_underwriting.analyze

Two figures are worked here, both written out on every screen that shows them:

    transaction costs   = ARV x the workspace's transaction cost % + its flat cost
                          (the same term calculate_offer subtracts)
    estimated profit    = ARV - offer - repairs - transaction costs
    (at an offer)         shown only when all four are known

NOTHING IS INVENTED. A property with no ARV says "Research needed". A total over
properties that are not all valued says how many are missing and is labelled
partial; a package profit is shown only when every property in it is valued.
The seller's price and rent are what the seller wrote and are labelled so.

ACCESS reuses the custom Wholesale access engine (wholesale_access): the
"portfolios" module gates the routes, and record visibility (assigned / team /
workspace) is applied to the properties inside a portfolio. A restricted login
sees only the properties it may see, and no package that contains one it may
not (a package total would otherwise reveal the hidden property's numbers).
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.wholesale_models import (ACTOR_USER, VALUE_MANUAL, VALUE_SOURCES, WholesaleComp, WholesaleDeal,
                                         WholesaleEvent, WholesaleOffer, WholesaleProperty)
from app.models.wholesale_portfolio_models import (CONFIDENCE, DISCOUNT_MODES, PORTFOLIO_STATUSES, REVIEW_STATUSES,
                                                   SOURCE_KINDS, WholesalePortfolio, WholesalePortfolioItem,
                                                   WholesalePortfolioPackage, WholesalePortfolioPackageItem,
                                                   WholesalePortfolioSource)
from app.services import wholesale_service as svc

MAX_ROWS = 2000
ESTIMATE_NOTICE = ("Internal investment estimate prepared from the figures on file. Not an appraisal, "
                   "not a broker price opinion and not an offer to purchase.")
STATUS_LABELS = {"research_needed": "Research needed", "repairs_needed": "Repairs needed",
                 "offer_needed": "Offer not set", "evaluated": "Evaluated"}
PORTFOLIO_STATUS_LABELS = {"researching": "Researching", "evaluating": "Evaluating", "negotiating": "Negotiating",
                           "offer_made": "Offer made", "under_contract": "Under contract", "closed": "Closed",
                           "passed": "Passed"}


# ── numbers ─────────────────────────────────────────────────────────────────

def _d(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return v
    s = str(v).strip().replace(",", "").replace("$", "")
    if s.lower() in ("", "n/a", "na", "none", "-", "--", "tbd", "?"):
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _f(v: Optional[Decimal]) -> Optional[float]:
    if v is None:
        return None
    return float(Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _sum(values: Iterable[Optional[Decimal]]) -> Tuple[Decimal, int]:
    """(total of the known values, how many were unknown)."""
    total, missing = Decimal(0), 0
    for v in values:
        if v is None:
            missing += 1
        else:
            total += v
    return total, missing


def _now() -> datetime:
    return datetime.utcnow()


def _iso(v: Optional[datetime]) -> Optional[str]:
    from app.utils.time_fmt import iso_utc
    return iso_utc(v) if v else None


def _jl(raw: Optional[str], default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


# ── access ──────────────────────────────────────────────────────────────────

def visible_ids(db: Session, org_id: str, user: User) -> Optional[Set[str]]:
    """Whose properties this person may see; None = every property here."""
    from app.services import wholesale_access as WACC
    return WACC.visible_user_ids(db, org_id, user)


def _can_see(prop: WholesaleProperty, visible: Optional[Set[str]]) -> bool:
    return visible is None or prop.assigned_to_id in visible


# ── portfolios ──────────────────────────────────────────────────────────────

def get_portfolio(db: Session, org_id: str, pid: str) -> WholesalePortfolio:
    p = (db.query(WholesalePortfolio)
         .filter(WholesalePortfolio.id == pid, WholesalePortfolio.organization_id == org_id).first())
    if p is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return p


def _clean_status(status: Optional[str]) -> str:
    s = (status or "researching").strip().lower().replace(" ", "_")
    if s not in PORTFOLIO_STATUSES:
        raise HTTPException(status_code=422, detail="Status must be one of: %s" % ", ".join(PORTFOLIO_STATUSES))
    return s


def _seller_lead(db: Session, org_id: str, lead_id: Optional[str]) -> Optional[str]:
    if not lead_id:
        return None
    from app.models.models import Lead
    row = db.query(Lead.id).filter(Lead.id == lead_id, Lead.organization_id == org_id).first()
    if row is None:
        raise HTTPException(status_code=422, detail="That seller is not in this workspace.")
    return lead_id


def create_portfolio(db: Session, org_id: str, user: User, data: Dict[str, Any]) -> WholesalePortfolio:
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the portfolio a name.")
    number = (db.query(func.max(WholesalePortfolio.number))
              .filter(WholesalePortfolio.organization_id == org_id).scalar() or 0) + 1
    p = WholesalePortfolio(organization_id=org_id, number=number, name=name[:200],
                           seller_lead_id=_seller_lead(db, org_id, data.get("seller_lead_id")),
                           seller_name=(data.get("seller_name") or "").strip()[:200] or None,
                           source=(data.get("source") or "").strip()[:200] or None,
                           status=_clean_status(data.get("status")),
                           notes=(data.get("notes") or "").strip() or None,
                           target_offer_total=_d(data.get("target_offer_total")),
                           is_test=bool(data.get("is_test")), created_by_id=user.id)
    db.add(p)
    db.flush()
    svc.log_event(db, org_id, "portfolio.created", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Seller portfolio #%03d created: %s" % (number, name),
                  after={"portfolio_id": p.id, "name": name, "is_test": p.is_test})
    return p


def update_portfolio(db: Session, org_id: str, user: User, p: WholesalePortfolio, data: Dict[str, Any]) -> None:
    before = portfolio_head(p)
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="The portfolio needs a name.")
        p.name = name[:200]
    if "seller_name" in data:
        p.seller_name = (data.get("seller_name") or "").strip()[:200] or None
    if "seller_lead_id" in data:
        p.seller_lead_id = _seller_lead(db, org_id, data.get("seller_lead_id"))
    if "source" in data:
        p.source = (data.get("source") or "").strip()[:200] or None
    if "status" in data:
        p.status = _clean_status(data.get("status"))
    if "notes" in data:
        p.notes = (data.get("notes") or "").strip() or None
    if "target_offer_total" in data:
        p.target_offer_total = _d(data.get("target_offer_total"))
    p.updated_at = _now()
    svc.log_event(db, org_id, "portfolio.updated", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Seller portfolio #%03d updated" % p.number, before=before, after=portfolio_head(p))


def archive_portfolio(db: Session, org_id: str, user: User, p: WholesalePortfolio, archived: bool) -> None:
    p.archived_at = _now() if archived else None
    p.archived_by_id = user.id if archived else None
    svc.log_event(db, org_id, "portfolio.archived" if archived else "portfolio.restored", actor_type=ACTOR_USER,
                  actor_user_id=user.id, summary="Seller portfolio #%03d %s" % (
                      p.number, "archived" if archived else "restored"))


def delete_portfolio(db: Session, org_id: str, user: User, p: WholesalePortfolio) -> None:
    """Only an EMPTY portfolio can be deleted. One with properties is archived:
    its scenarios and the seller's list are the record of a negotiation."""
    live = (db.query(func.count(WholesalePortfolioItem.id))
            .filter(WholesalePortfolioItem.portfolio_id == p.id, WholesalePortfolioItem.organization_id == org_id)
            .scalar())
    if live:
        raise HTTPException(status_code=409, detail="This portfolio has properties on it. Archive it instead; "
                                                    "deleting would lose the seller's list and every scenario.")
    db.query(WholesalePortfolioSource).filter(WholesalePortfolioSource.portfolio_id == p.id,
                                              WholesalePortfolioSource.organization_id == org_id).delete()
    db.query(WholesalePortfolioPackage).filter(WholesalePortfolioPackage.portfolio_id == p.id,
                                               WholesalePortfolioPackage.organization_id == org_id).delete()
    svc.log_event(db, org_id, "portfolio.deleted", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Empty seller portfolio #%03d deleted: %s" % (p.number, p.name))
    db.delete(p)


def portfolio_head(p: WholesalePortfolio) -> Dict[str, Any]:
    return {"id": p.id, "number": p.number, "label": "Seller Portfolio #%03d" % p.number, "name": p.name,
            "seller_name": p.seller_name, "seller_lead_id": p.seller_lead_id, "source": p.source,
            "status": p.status, "status_label": PORTFOLIO_STATUS_LABELS.get(p.status, p.status),
            "notes": p.notes, "target_offer_total": _f(p.target_offer_total), "is_test": bool(p.is_test),
            "archived": p.archived_at is not None, "archived_at": _iso(p.archived_at),
            "created_at": _iso(p.created_at), "updated_at": _iso(p.updated_at)}


def list_portfolios(db: Session, org_id: str, user: User, include_archived: bool = False) -> List[Dict[str, Any]]:
    q = db.query(WholesalePortfolio).filter(WholesalePortfolio.organization_id == org_id)
    if not include_archived:
        q = q.filter(WholesalePortfolio.archived_at.is_(None))
    out = []
    visible = visible_ids(db, org_id, user)
    settings = svc.resolve_settings(db, org_id, commit=False)
    for p in q.order_by(WholesalePortfolio.number.desc()).all():
        rows = item_rows(db, org_id, p, visible, settings)
        if visible is not None and not rows and p.created_by_id != user.id:
            continue                          # nothing in it this login may see
        head = portfolio_head(p)
        head["summary"] = summarize(rows)
        head["packages"] = (db.query(func.count(WholesalePortfolioPackage.id))
                            .filter(WholesalePortfolioPackage.portfolio_id == p.id,
                                    WholesalePortfolioPackage.organization_id == org_id,
                                    WholesalePortfolioPackage.status == "draft").scalar() or 0)
        out.append(head)
    return out


# ── one property's row ──────────────────────────────────────────────────────

def transaction_costs(arv: Optional[Decimal], settings) -> Optional[Decimal]:
    """The workspace's transaction costs at this ARV: the same term the MAO
    formula subtracts (wholesale_analysis.calculate_offer)."""
    if arv is None:
        return None
    pct = _d(getattr(settings, "transaction_cost_percent", 0)) or Decimal(0)
    flat = _d(getattr(settings, "transaction_cost_flat", 0)) or Decimal(0)
    return arv * pct / Decimal(100) + flat


def profit_at(arv, offer, repairs, costs) -> Optional[Decimal]:
    if None in (arv, offer, repairs, costs):
        return None
    return arv - offer - repairs - costs


def _status(arv, repairs, target) -> str:
    if arv is None:
        return "research_needed"
    if repairs is None:
        return "repairs_needed"
    if target is None:
        return "offer_needed"
    return "evaluated"


def row_for(item: WholesalePortfolioItem, prop: WholesaleProperty, deal: Optional[WholesaleDeal],
            settings) -> Dict[str, Any]:
    from app.services import wholesale_repairs as REP
    arv = _d(getattr(deal, "arv", None))
    rst = REP.status_of(deal) if deal is not None else REP.UNKNOWN
    repairs = _d(getattr(deal, "repair_estimate", None)) if rst != REP.UNKNOWN else None
    target = _d(getattr(deal, "proposed_offer", None))
    asking = _d(item.seller_price)
    rent = _d(item.seller_rent)
    mao, mao_note = None, None
    if deal is not None:
        summ = svc.gated_summary(deal, settings)
        mao = _d(summ.get("max_allowable_offer"))
        if mao is None:
            mao_note = (summ.get("mao_blocked") or summ.get("warnings") or ["Not calculated"])[0]
    costs = transaction_costs(arv, settings)
    profit = profit_at(arv, target, repairs, costs)
    as_is = _d(prop.estimated_value)
    return {
        "item_id": item.id, "property_id": prop.id, "deal_id": getattr(deal, "id", None),
        "address": svc.address_line(prop) or item.seller_address_text or "(no address)",
        "seller_address_text": item.seller_address_text,
        "city": prop.city, "state": prop.state, "zip_code": prop.zip_code, "parcel_apn": prop.parcel_apn,
        "property_type": "land" if item.is_land else prop.property_type, "seller_type": item.seller_type,
        "is_land": bool(item.is_land), "occupancy_status": prop.occupancy_status or "unknown",
        "bedrooms": _f(_d(prop.bedrooms)), "bathrooms": _f(_d(prop.bathrooms)), "square_feet": prop.square_feet,
        "assigned_to_id": prop.assigned_to_id,
        "source_row": item.source_row, "source_ref": item.source_ref,
        "extraction_confidence": item.extraction_confidence, "review_status": item.review_status,
        "seller_price": _f(asking), "seller_rent": _f(rent),
        "arv": _f(arv), "arv_source": getattr(deal, "arv_source", None),
        "arv_confidence": getattr(deal, "arv_confidence", None) or ("insufficient" if arv is None else None),
        "valuation_at": _iso(getattr(deal, "arv_calculated_at", None) or getattr(deal, "analysis_updated_at", None)),
        "as_is_value": _f(as_is), "as_is_value_source": prop.estimated_value_source,
        "repairs": _f(repairs), "repair_status": rst, "repair_status_label": REP.LABELS.get(rst, rst),
        "transaction_costs": _f(costs),
        "mao": _f(mao), "mao_note": mao_note,
        "target_offer": _f(target),
        "estimated_profit": _f(profit),
        "discount_from_asking": _f(asking - target) if asking is not None and target is not None else None,
        "gross_rent_yield_pct": _f(rent * 12 / target * 100) if rent is not None and target else None,
        "repair_ratio_pct": _f(repairs / arv * 100) if repairs is not None and arv else None,
        "status": _status(arv, repairs, target),
        "status_label": STATUS_LABELS[_status(arv, repairs, target)],
        "is_test": bool(prop.is_test),
    }


def _items(db: Session, org_id: str, portfolio_id: str, include_removed: bool = False):
    q = (db.query(WholesalePortfolioItem, WholesaleProperty)
         .join(WholesaleProperty, WholesaleProperty.id == WholesalePortfolioItem.property_id)
         .filter(WholesalePortfolioItem.organization_id == org_id, WholesaleProperty.organization_id == org_id,
                 WholesalePortfolioItem.portfolio_id == portfolio_id))
    if not include_removed:
        q = q.filter(WholesalePortfolioItem.removed_at.is_(None))
    return q.order_by(WholesalePortfolioItem.source_row.asc().nullslast(), WholesalePortfolioItem.created_at).all()


def _deals_by_property(db: Session, org_id: str, prop_ids: List[str]) -> Dict[str, WholesaleDeal]:
    out: Dict[str, WholesaleDeal] = {}
    if not prop_ids:
        return out
    for d in (db.query(WholesaleDeal).filter(WholesaleDeal.organization_id == org_id,
                                             WholesaleDeal.property_id.in_(prop_ids))
              .order_by(WholesaleDeal.created_at.asc()).all()):
        out[d.property_id] = d                # the newest deal wins (svc.deal_for_property's rule)
    return out


def item_rows(db: Session, org_id: str, p: WholesalePortfolio, visible: Optional[Set[str]], settings,
              ) -> List[Dict[str, Any]]:
    pairs = [(i, pr) for i, pr in _items(db, org_id, p.id) if _can_see(pr, visible)]
    deals = _deals_by_property(db, org_id, [pr.id for _, pr in pairs])
    return [row_for(i, pr, deals.get(pr.id), settings) for i, pr in pairs]


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Portfolio cards. Each total says how many properties it covers."""
    def tot(key):
        vals = [_d(r.get(key)) for r in rows]
        t, missing = _sum(vals)
        return {"total": _f(t) if len(rows) - missing else None, "counted": len(rows) - missing,
                "missing": missing, "partial": bool(missing) and missing < len(rows)}
    evaluated = [r for r in rows if r["status"] == "evaluated"]
    valued = [r for r in rows if r.get("arv") is not None]
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    spread_vals = [_d(r["estimated_profit"]) for r in evaluated]
    return {
        "properties": len(rows), "land": sum(1 for r in rows if r["is_land"]),
        "evaluated": len(evaluated), "valued": len(valued), "awaiting_valuation": len(rows) - len(valued),
        "progress_pct": round(len(evaluated) * 100 / len(rows)) if rows else 0,
        "by_status": by_status,
        "seller_asking": tot("seller_price"), "seller_rent_monthly": tot("seller_rent"),
        "estimated_value": tot("arv"), "repairs": tot("repairs"), "target_offers": tot("target_offer"),
        # The spread is summed over EVALUATED properties only (it needs every term).
        "estimated_spread": {"total": _f(_sum(spread_vals)[0]) if evaluated else None,
                             "counted": len(evaluated), "missing": len(rows) - len(evaluated),
                             "partial": len(evaluated) < len(rows) and bool(evaluated)},
        "labels": {"seller_asking": "Seller stated, not verified",
                   "seller_rent_monthly": "Seller stated scheduled rent, not collected rent",
                   "estimated_value": "ARV on file (comparable sales or a labelled entry)",
                   "estimated_spread": "ARV - target offer - repairs - transaction costs, evaluated properties only"},
    }


# ── items ───────────────────────────────────────────────────────────────────

def get_item(db: Session, org_id: str, p: WholesalePortfolio, item_id: str, user: User,
             ) -> Tuple[WholesalePortfolioItem, WholesaleProperty]:
    row = (db.query(WholesalePortfolioItem, WholesaleProperty)
           .join(WholesaleProperty, WholesaleProperty.id == WholesalePortfolioItem.property_id)
           .filter(WholesalePortfolioItem.id == item_id, WholesalePortfolioItem.portfolio_id == p.id,
                   WholesalePortfolioItem.organization_id == org_id, WholesaleProperty.organization_id == org_id)
           .first())
    if row is None or not _can_see(row[1], visible_ids(db, org_id, user)):
        raise HTTPException(status_code=404, detail="Property not found in this portfolio")
    return row


def _price_entry(price: Optional[Decimal], user: User, note: str) -> Dict[str, Any]:
    return {"price": _f(price), "at": _now().isoformat() + "Z", "by": user.id, "note": note}


def link_item(db: Session, org_id: str, user: User, p: WholesalePortfolio, prop: WholesaleProperty,
              fields: Dict[str, Any], source: Optional[WholesalePortfolioSource] = None,
              ) -> Tuple[WholesalePortfolioItem, str]:
    """Add a property to a portfolio: 'created', 'reactivated' or 'existing'."""
    item = (db.query(WholesalePortfolioItem)
            .filter(WholesalePortfolioItem.portfolio_id == p.id, WholesalePortfolioItem.property_id == prop.id,
                    WholesalePortfolioItem.organization_id == org_id).first())
    if item is not None and item.removed_at is None:
        return item, "existing"
    outcome = "reactivated" if item is not None else "created"
    if item is None:
        item = WholesalePortfolioItem(organization_id=org_id, portfolio_id=p.id, property_id=prop.id,
                                      created_by_id=user.id)
        db.add(item)
    item.removed_at = None
    item.removed_by_id = None
    item.source_id = source.id if source is not None else item.source_id
    for k in ("source_row", "source_ref", "seller_address_text", "seller_type", "source_notes"):
        if fields.get(k) not in (None, ""):
            setattr(item, k, fields[k])
    price = _d(fields.get("seller_price"))
    if price is not None or item.seller_price is None:
        if price != _d(item.seller_price):
            hist = _jl(item.price_history, [])
            hist.append(_price_entry(price, user, "from the seller's list" if source else "added"))
            item.price_history = json.dumps(hist)
        item.seller_price = price
    if fields.get("seller_rent") not in (None, ""):
        item.seller_rent = _d(fields.get("seller_rent"))
    item.is_land = bool(fields.get("is_land")) or (prop.property_type == "land")
    conf = fields.get("extraction_confidence")
    item.extraction_confidence = conf if conf in CONFIDENCE else item.extraction_confidence
    item.review_status = fields.get("review_status") if fields.get("review_status") in REVIEW_STATUSES \
        else (item.review_status or "needs_review")
    db.flush()
    return item, outcome


def add_existing(db: Session, org_id: str, user: User, p: WholesalePortfolio, property_ids: List[str],
                 ) -> Dict[str, Any]:
    visible = visible_ids(db, org_id, user)
    added, already, refused = [], [], []
    for pid in property_ids or []:
        prop = (db.query(WholesaleProperty)
                .filter(WholesaleProperty.id == pid, WholesaleProperty.organization_id == org_id).first())
        if prop is None or not _can_see(prop, visible):
            refused.append(pid)
            continue
        item, outcome = link_item(db, org_id, user, p, prop, {"review_status": "reviewed",
                                                              "extraction_confidence": None})
        (already if outcome == "existing" else added).append(item.id)
    svc.log_event(db, org_id, "portfolio.properties_linked", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="%d existing propert%s linked to portfolio #%03d" % (
                      len(added), "y" if len(added) == 1 else "ies", p.number),
                  details={"portfolio_id": p.id, "items": added})
    return {"added": len(added), "already_in_portfolio": len(already), "not_found": len(refused)}


ITEM_FIELDS = ("seller_address_text", "seller_price", "seller_rent", "seller_type", "is_land", "source_notes",
               "review_status", "extraction_confidence", "source_row", "source_ref")


def update_item(db: Session, org_id: str, user: User, p: WholesalePortfolio, item: WholesalePortfolioItem,
                data: Dict[str, Any]) -> None:
    before = {k: (_f(_d(getattr(item, k))) if k in ("seller_price", "seller_rent") else getattr(item, k))
              for k in ITEM_FIELDS}
    if "seller_price" in data:
        price = _d(data.get("seller_price"))
        if price != _d(item.seller_price):
            hist = _jl(item.price_history, [])
            hist.append(_price_entry(price, user, (data.get("price_note") or "asking price changed").strip()[:200]))
            item.price_history = json.dumps(hist)
            item.seller_price = price
    if "seller_rent" in data:
        item.seller_rent = _d(data.get("seller_rent"))
    for k in ("seller_address_text", "seller_type", "source_notes", "source_ref"):
        if k in data:
            item.__setattr__(k, (str(data.get(k) or "").strip() or None))
    if "is_land" in data:
        item.is_land = bool(data.get("is_land"))
    if "source_row" in data:
        item.source_row = int(data["source_row"]) if str(data.get("source_row") or "").strip().isdigit() else None
    if "review_status" in data:
        if data["review_status"] not in REVIEW_STATUSES:
            raise HTTPException(status_code=422, detail="Review status must be one of: %s" % ", ".join(REVIEW_STATUSES))
        item.review_status = data["review_status"]
    if "extraction_confidence" in data:
        if data["extraction_confidence"] not in CONFIDENCE + (None,):
            raise HTTPException(status_code=422, detail="Confidence must be high, medium or low.")
        item.extraction_confidence = data["extraction_confidence"]
    item.updated_at = _now()
    after = {k: (_f(_d(getattr(item, k))) if k in ("seller_price", "seller_rent") else getattr(item, k))
             for k in ITEM_FIELDS}
    svc.log_event(db, org_id, "portfolio.item_updated", actor_type=ACTOR_USER, actor_user_id=user.id,
                  property_id=item.property_id, summary="Seller's listing edited (portfolio #%03d)" % p.number,
                  before=before, after=after)


def _active_packages_with(db: Session, org_id: str, item_ids: List[str]) -> List[WholesalePortfolioPackage]:
    if not item_ids:
        return []
    return (db.query(WholesalePortfolioPackage)
            .join(WholesalePortfolioPackageItem, WholesalePortfolioPackageItem.package_id == WholesalePortfolioPackage.id)
            .filter(WholesalePortfolioPackageItem.item_id.in_(item_ids),
                    WholesalePortfolioPackage.organization_id == org_id,
                    WholesalePortfolioPackage.status == "draft").distinct().all())


def remove_items(db: Session, org_id: str, user: User, p: WholesalePortfolio, item_ids: List[str],
                 also_from_packages: bool = False) -> Dict[str, Any]:
    visible = visible_ids(db, org_id, user)
    items = []
    for iid in item_ids or []:
        row = (db.query(WholesalePortfolioItem, WholesaleProperty)
               .join(WholesaleProperty, WholesaleProperty.id == WholesalePortfolioItem.property_id)
               .filter(WholesalePortfolioItem.id == iid, WholesalePortfolioItem.portfolio_id == p.id,
                       WholesalePortfolioItem.organization_id == org_id).first())
        if row is not None and row[0].removed_at is None and _can_see(row[1], visible):
            items.append(row[0])
    pkgs = _active_packages_with(db, org_id, [i.id for i in items])
    if pkgs and not also_from_packages:
        raise HTTPException(status_code=409, detail="In draft package%s: %s. Remove from %s too, or take the "
                            "propert%s out of %s first." % (
                                "s" if len(pkgs) > 1 else "", ", ".join(k.name for k in pkgs),
                                "them" if len(pkgs) > 1 else "it", "ies" if len(items) > 1 else "y",
                                "those packages" if len(pkgs) > 1 else "that package"))
    if pkgs:
        (db.query(WholesalePortfolioPackageItem)
         .filter(WholesalePortfolioPackageItem.item_id.in_([i.id for i in items]),
                 WholesalePortfolioPackageItem.package_id.in_([k.id for k in pkgs]))
         .delete(synchronize_session=False))
    for i in items:
        i.removed_at = _now()
        i.removed_by_id = user.id
    svc.log_event(db, org_id, "portfolio.properties_removed", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="%d propert%s removed from portfolio #%03d (the property records are kept)" % (
                      len(items), "y" if len(items) == 1 else "ies", p.number),
                  details={"portfolio_id": p.id, "items": [i.id for i in items],
                           "packages": [k.id for k in pkgs]})
    return {"removed": len(items), "packages_updated": len(pkgs)}


def assign_items(db: Session, org_id: str, user: User, p: WholesalePortfolio, item_ids: List[str],
                 assignee_id: str) -> Dict[str, Any]:
    """Hand the research on these properties to a person on this team. The
    property and its deal are assigned (the existing assignment the access
    engine's 'assigned only' scope reads); nothing else changes."""
    from app.services import wholesale_access as WACC
    from app.services import wholesale_team as T
    target = T.in_workspace(db, org_id, assignee_id)
    if target is None:
        raise HTTPException(status_code=422, detail="That person is not on this workspace's team.")
    eff = WACC.effective(db, target, org_id)
    if eff.get("mode") == "custom" and not WACC.allows(eff, "portfolios", "view") \
            and not WACC.allows(eff, "properties", "view"):
        raise HTTPException(status_code=422, detail="%s's access doesn't include Portfolio Acquisitions or "
                                                    "Properties. Give them access first." % (
                                                        target.full_name or target.email))
    visible = visible_ids(db, org_id, user)
    changed = []
    for iid in item_ids or []:
        row = (db.query(WholesalePortfolioItem, WholesaleProperty)
               .join(WholesaleProperty, WholesaleProperty.id == WholesalePortfolioItem.property_id)
               .filter(WholesalePortfolioItem.id == iid, WholesalePortfolioItem.portfolio_id == p.id,
                       WholesalePortfolioItem.organization_id == org_id,
                       WholesalePortfolioItem.removed_at.is_(None)).first())
        if row is None or not _can_see(row[1], visible):
            continue
        prop = row[1]
        prop.assigned_to_id = target.id
        deal = svc.deal_for_property(db, org_id, prop.id)
        if deal is not None:
            deal.assigned_to_id = target.id
        changed.append(prop.id)
    svc.log_event(db, org_id, "portfolio.assigned", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="%d portfolio propert%s assigned to %s for research" % (
                      len(changed), "y" if len(changed) == 1 else "ies", target.full_name or target.email),
                  details={"portfolio_id": p.id, "properties": changed, "assignee": target.id})
    return {"assigned": len(changed), "to": {"id": target.id, "name": target.full_name or target.email}}


# ── evaluation (the existing underwriting, with the seller's row beside it) ─

def evaluation(db: Session, org_id: str, user: User, p: WholesalePortfolio, item: WholesalePortfolioItem,
               prop: WholesaleProperty, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from app.services import wholesale_repairs as REP
    from app.services import wholesale_underwriting as WU
    settings = svc.resolve_settings(db, org_id, commit=False)
    deal = svc.deal_for_property(db, org_id, prop.id)
    row = row_for(item, prop, deal, settings)
    ov = dict(overrides or {})
    rent_from_seller = False
    if ov.get("rent_monthly") is None and item.seller_rent is not None:
        ov["rent_monthly"] = float(item.seller_rent)
        rent_from_seller = True
    analysis = WU.analyze(db, org_id, deal, ov) if deal is not None else None
    events = (db.query(WholesaleEvent)
              .filter(WholesaleEvent.organization_id == org_id,
                      ((WholesaleEvent.property_id == prop.id) |
                       (WholesaleEvent.deal_id == getattr(deal, "id", "-"))),
                      WholesaleEvent.action.in_(("analysis.edited", "repairs.estimated", "arv.calculated",
                                                 "portfolio.evaluation_override", "portfolio.item_updated",
                                                 "property.updated")))
              .order_by(WholesaleEvent.created_at.desc()).limit(25).all())
    repairs = REP.history(db, org_id, deal.id) if deal is not None else []
    return {
        "portfolio": portfolio_head(p), "row": row,
        "property": {"address": row["address"], "parcel_apn": prop.parcel_apn, "parcel_verified": False,
                     "property_type": row["property_type"], "bedrooms": row["bedrooms"],
                     "bathrooms": row["bathrooms"], "square_feet": prop.square_feet,
                     "lot_size_sqft": prop.lot_size_sqft, "year_built": prop.year_built,
                     "occupancy_status": row["occupancy_status"],
                     "facts_source": prop.acquisition_source, "facts_detail": prop.source_detail},
        "seller": {"stated_price": row["seller_price"], "stated_rent": row["seller_rent"],
                   "address_as_listed": item.seller_address_text, "type_as_listed": item.seller_type,
                   "notes": item.source_notes, "source_row": item.source_row, "source_ref": item.source_ref,
                   "source_date": _iso(item.created_at), "confidence": item.extraction_confidence,
                   "review_status": item.review_status, "price_history": _jl(item.price_history, []),
                   "label": "As the seller listed it. Not verified."},
        "analysis": analysis,
        "rent_note": ("The rental scenario uses the seller's STATED rent ($%s/month). It is not verified "
                      "collected rent; enter a verified figure to replace it." % format(int(item.seller_rent), ","))
        if rent_from_seller else None,
        "repair_history": [{"status": r.status, "label": REP.LABELS.get(r.status, r.status),
                            "amount": _f(_d(r.amount)), "low": _f(_d(r.low)), "high": _f(_d(r.high)),
                            "source": r.source, "notes": r.notes, "at": _iso(r.created_at)}
                           for r in repairs][-10:],
        "history": [{"action": e.action, "summary": e.summary, "at": _iso(e.created_at),
                     "before": _jl(e.before_state, None), "after": _jl(e.after_state, None),
                     "note": (_jl(e.details, {}) or {}).get("note") if e.details else None} for e in events],
        "notice": ESTIMATE_NOTICE,
    }


OVERRIDE_FIELDS = ("arv", "repairs", "target_offer", "as_is_value", "occupancy_status")


def override(db: Session, org_id: str, user: User, p: WholesalePortfolio, item: WholesalePortfolioItem,
             prop: WholesaleProperty, data: Dict[str, Any]) -> Dict[str, Any]:
    """A person's number replaces an estimate, WITH its reason.

    Goes through the same pieces as the deal room's own analysis edit: the ARV
    is labelled MANUAL (or VERIFIED when the person says so), repairs go into
    the repair history, the MAO is recalculated. The value before the change is
    kept in the event, and in the repair history, never overwritten silently.
    """
    from app.services import wholesale_repairs as REP
    note = (data.get("note") or "").strip()
    changing = [k for k in OVERRIDE_FIELDS if k in data]
    if not changing:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    if any(k in data for k in ("arv", "repairs", "as_is_value")) and not note:
        raise HTTPException(status_code=422, detail="Say where the number comes from (a source or a reason). "
                                                    "It is kept with the change.")
    src = (data.get("source") or VALUE_MANUAL).strip().lower()
    if src not in (VALUE_MANUAL, "verified"):
        raise HTTPException(status_code=422, detail="Source must be manual or verified.")
    deal = svc.deal_for_property(db, org_id, prop.id)
    if deal is None:
        raise HTTPException(status_code=409, detail="This property has no deal record to evaluate.")
    if deal.economics_locked:
        raise HTTPException(status_code=409, detail="This deal has closed; its economics are locked.")
    before = {"arv": _f(_d(deal.arv)), "arv_source": deal.arv_source, "repairs": _f(_d(deal.repair_estimate)),
              "repair_status": deal.repair_status, "target_offer": _f(_d(deal.proposed_offer)),
              "as_is_value": _f(_d(prop.estimated_value)), "as_is_value_source": prop.estimated_value_source,
              "occupancy_status": prop.occupancy_status}
    if "arv" in data:
        v = _d(data.get("arv"))
        if v is not None and v <= 0:
            raise HTTPException(status_code=422, detail="ARV must be above zero.")
        deal.arv = v
        deal.arv_source = src if v is not None else None
        deal.arv_method = "manual" if v is not None else None
    if "repairs" in data:
        v = _d(data.get("repairs"))
        if v is None:
            deal.repair_estimate = None
            deal.repair_status = REP.UNKNOWN
            deal.repair_low = deal.repair_high = None
            deal.repair_estimate_source = None
        else:
            if v < 0:
                raise HTTPException(status_code=422, detail="Repairs cannot be negative.")
            REP.record(db, org_id, deal, status=REP.VERIFIED if src == "verified" else REP.MANUAL_ESTIMATE,
                       amount=v, user=user, source="portfolio evaluation", notes=note[:500] or None)
            deal.repair_estimate_source = src
    if "target_offer" in data:
        v = _d(data.get("target_offer"))
        if v is not None and v <= 0:
            raise HTTPException(status_code=422, detail="The target offer must be above zero.")
        deal.proposed_offer = v
    if "as_is_value" in data:
        v = _d(data.get("as_is_value"))
        prop.estimated_value = v
        prop.estimated_value_source = src if v is not None else None
    if "occupancy_status" in data:
        occ = (data.get("occupancy_status") or "unknown").strip().lower()
        if occ not in ("owner_occupied", "tenant", "vacant", "unknown"):
            raise HTTPException(status_code=422, detail="Occupancy must be owner_occupied, tenant, vacant or unknown.")
        prop.occupancy_status = None if occ == "unknown" else occ
    svc.recalculate_analysis(db, org_id, deal, user, recompute_arv_from_comps="arv" not in data)
    after = {"arv": _f(_d(deal.arv)), "arv_source": deal.arv_source, "repairs": _f(_d(deal.repair_estimate)),
             "repair_status": deal.repair_status, "target_offer": _f(_d(deal.proposed_offer)),
             "as_is_value": _f(_d(prop.estimated_value)), "as_is_value_source": prop.estimated_value_source,
             "occupancy_status": prop.occupancy_status}
    svc.log_event(db, org_id, "portfolio.evaluation_override", actor_type=ACTOR_USER, actor_user_id=user.id,
                  deal_id=deal.id, property_id=prop.id,
                  summary="Evaluation edited from portfolio #%03d: %s" % (p.number, ", ".join(changing)),
                  before=before, after=after, details={"note": note or None, "source": src})
    return after


# ── imports: stage, review, commit, undo ────────────────────────────────────

COLUMN_ALIASES = {
    "row": "source_row", "#": "source_row", "no": "source_row", "no.": "source_row", "entry": "source_row",
    "item": "source_row", "number": "source_row", "line": "source_row",
    "address": "address", "property address": "address", "street": "address", "street address": "address",
    "property": "address", "location": "address",
    "unit": "unit", "apt": "unit", "city": "city", "state": "state", "st": "state",
    "zip": "zip_code", "zipcode": "zip_code", "zip code": "zip_code", "postal code": "zip_code",
    "county": "county", "apn": "parcel_apn", "parcel": "parcel_apn", "parcel number": "parcel_apn",
    "price": "seller_price", "asking": "seller_price", "asking price": "seller_price", "sale price": "seller_price",
    "list price": "seller_price", "sales price": "seller_price", "seller price": "seller_price",
    "rent": "seller_rent", "monthly rent": "seller_rent", "rent amount": "seller_rent", "rents": "seller_rent",
    "rental": "seller_rent",
    "type": "seller_type", "property type": "seller_type", "kind": "seller_type",
    "beds": "bedrooms", "bedrooms": "bedrooms", "baths": "bathrooms", "bathrooms": "bathrooms",
    "sqft": "square_feet", "sq ft": "square_feet", "square feet": "square_feet",
    "notes": "source_notes", "note": "source_notes", "comments": "source_notes",
    "confidence": "extraction_confidence", "source": "source_ref", "sheet": "source_ref", "image": "source_ref",
}
EDITABLE_ROW_FIELDS = ("address", "unit", "city", "state", "zip_code", "county", "parcel_apn", "seller_price",
                       "seller_rent", "seller_type", "bedrooms", "bathrooms", "square_feet", "source_notes",
                       "source_row", "source_ref", "extraction_confidence")
# Personal details are not property facts. A column that looks like a tenant
# or a phone number is dropped on read and the preview says it was.
_PERSONAL = re.compile(r"tenant|occupant|phone|cell|mobile|email|ssn|lease holder|renter name|contact",
                       re.IGNORECASE)
_SUFFIX = {"street": "st", "str": "st", "avenue": "ave", "av": "ave", "road": "rd", "drive": "dr", "lane": "ln",
           "boulevard": "blvd", "court": "ct", "circle": "cir", "place": "pl", "parkway": "pkwy", "highway": "hwy",
           "terrace": "ter", "trail": "trl", "north": "n", "south": "s", "east": "e", "west": "w",
           "apartment": "apt", "suite": "ste", "#": "unit"}


def norm_address(street: Optional[str], unit: Optional[str] = None) -> str:
    s = (street or "").lower()
    s = re.sub(r"[^\w\s#]", " ", s)
    words = [_SUFFIX.get(w, w) for w in s.split()]
    if unit:
        words += ["unit"] + [w for w in re.sub(r"[^\w]", " ", str(unit).lower()).split()]
    return " ".join(words)


def _is_land(text_: Optional[str]) -> bool:
    return bool(re.search(r"\b(land|lot|lots|acre|acres|acreage|vacant land)\b", (text_ or ""), re.IGNORECASE))


def _read_table(raw: bytes, file_name: str) -> Tuple[List[str], List[List[Any]]]:
    name = (file_name or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception:                                       # noqa: BLE001
            raise HTTPException(status_code=400, detail="That spreadsheet could not be read. Save it as .xlsx or .csv.")
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        while rows and not any(c not in (None, "") for c in rows[0]):
            rows.pop(0)
        if not rows:
            raise HTTPException(status_code=400, detail="The first sheet is empty.")
        return [str(c or "").strip() for c in rows[0]], rows[1:]
    if not name.endswith((".csv", ".txt")):
        raise HTTPException(status_code=400, detail="Use a .csv or .xlsx file.")
    try:
        text_ = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text_ = raw.decode("latin-1")
    rows = list(csv.reader(io.StringIO(text_)))
    while rows and not any((c or "").strip() for c in rows[0]):
        rows.pop(0)
    if not rows:
        raise HTTPException(status_code=400, detail="That file has no header row.")
    return [(c or "").strip() for c in rows[0]], rows[1:]


def _row_from_mapping(header: List[str], values: List[Any]) -> Tuple[Dict[str, Any], List[str]]:
    out: Dict[str, Any] = {}
    extras, dropped = [], []
    for h, v in zip(header, values):
        if v is None or str(v).strip() == "":
            continue
        key = COLUMN_ALIASES.get(h.strip().lower())
        if key is None:
            if _PERSONAL.search(h):
                dropped.append(h)
                continue
            extras.append("%s: %s" % (h, str(v).strip()))
            continue
        out[key] = str(v).strip() if not isinstance(v, (int, float, Decimal)) else v
    if extras:
        out["source_notes"] = "; ".join(filter(None, [str(out.get("source_notes") or "")] + extras)).strip("; ")
    return out, dropped


def _stage_row(n: int, data: Dict[str, Any]) -> Dict[str, Any]:
    r = {"n": n, "original": {k: (str(v) if v is not None else None) for k, v in data.items()}}
    for k in EDITABLE_ROW_FIELDS:
        v = data.get(k)
        r[k] = (str(v).strip() if v is not None else None) or None
    if r["source_row"] is None:
        r["source_row"] = str(n)
    conf = (r.get("extraction_confidence") or "").lower() or None
    r["extraction_confidence"] = conf if conf in CONFIDENCE else None
    r["decision"] = None
    r["link_property_id"] = None
    return r


def _analyze_rows(db: Session, org_id: str, p: WholesalePortfolio, rows: List[Dict[str, Any]], kind: str) -> None:
    """Fill each row's findings and default decision. Re-run after every edit."""
    seen: Dict[str, int] = {}
    in_portfolio = {pr.id for _, pr in _items(db, org_id, p.id)}
    for r in rows:
        issues: List[Dict[str, str]] = []
        land = _is_land(r.get("seller_type")) or _is_land(r.get("address"))
        r["is_land"] = land
        price, rent = r.get("seller_price"), r.get("seller_rent")
        for k, v in (("seller_price", price), ("seller_rent", rent)):
            if v not in (None, "") and _d(v) is None:
                issues.append({"code": "bad_number", "text": "%s '%s' is not a number" % (
                    "Price" if k == "seller_price" else "Rent", v)})
        if land and rent:
            issues.append({"code": "land_rent", "text": "Listed as land but has a rent; check the row."})
        addr = (r.get("address") or "").strip()
        r["matches"] = []
        if not addr and not r.get("parcel_apn"):
            issues.append({"code": "missing_address", "text": "No address or parcel number."})
        elif addr:
            if not re.match(r"^\s*\d", addr) and not land:
                issues.append({"code": "ambiguous_address", "text": "No house number."})
            if not r.get("city") and not r.get("zip_code"):
                issues.append({"code": "ambiguous_address", "text": "No city or ZIP."})
            key = norm_address(addr, r.get("unit"))
            if key in seen:
                issues.append({"code": "repeat_in_list", "text": "Same address as row %s on this list (possible "
                                                                  "duplicate, or a second unit/parcel)." % seen[key]})
            else:
                seen[key] = r["source_row"]
            num = addr.split()[0] if addr.split() else ""
            if num:
                cands = (db.query(WholesaleProperty)
                         .filter(WholesaleProperty.organization_id == org_id,
                                 WholesaleProperty.street_address.ilike(num + " %")).limit(200).all())
                for c in cands:
                    if norm_address(c.street_address, c.unit) != key:
                        continue
                    if r.get("zip_code") and c.zip_code and str(c.zip_code)[:5] != str(r["zip_code"])[:5]:
                        continue
                    r["matches"].append({"property_id": c.id, "address": svc.address_line(c),
                                         "in_this_portfolio": c.id in in_portfolio, "is_test": bool(c.is_test)})
            if r["matches"]:
                where = "already in this portfolio" if any(m["in_this_portfolio"] for m in r["matches"]) \
                    else "already in this workspace"
                issues.append({"code": "possible_duplicate", "text": "Possible duplicate: %s %s." % (
                    r["matches"][0]["address"], where)})
        r["issues"] = issues
        codes = {i["code"] for i in issues}
        if r.get("decision") in ("create", "link", "skip"):
            pass                                                     # a person already decided
        elif "missing_address" in codes or "bad_number" in codes:
            r["decision"] = "fix"
        elif "possible_duplicate" in codes or "repeat_in_list" in codes:
            r["decision"] = "decide"                                 # a person has to choose
        else:
            r["decision"] = "create"
        if r.get("extraction_confidence") is None and kind in ("photo", "manual"):
            r["extraction_confidence"] = "low"


def _source_json(s: WholesalePortfolioSource) -> Dict[str, Any]:
    rows = _jl(s.rows, [])
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r.get("decision") or "?"] = counts.get(r.get("decision") or "?", 0) + 1
    created = _jl(s.created, {})
    total_price = _sum(_d(r.get("seller_price")) for r in rows if r.get("decision") != "skip")
    total_rent = _sum(_d(r.get("seller_rent")) for r in rows if r.get("decision") != "skip")
    return {"id": s.id, "portfolio_id": s.portfolio_id, "kind": s.kind, "file_name": s.file_name,
            "reference": s.reference, "note": s.note, "status": s.status, "rows": rows,
            "counts": counts, "row_count": len(rows),
            "ready": s.status == "preview" and rows and not counts.get("fix") and not counts.get("decide"),
            "stated_totals": {"seller_price": _f(total_price[0]), "seller_price_missing": total_price[1],
                              "seller_rent": _f(total_rent[0]), "seller_rent_missing": total_rent[1],
                              "label": "Seller stated, as listed - not verified"},
            "dropped_columns": created.get("dropped_columns") if s.status == "preview" else None,
            "created": created if s.status != "preview" else None,
            "created_at": _iso(s.created_at), "committed_at": _iso(s.committed_at), "undone_at": _iso(s.undone_at)}


def stage_file(db: Session, org_id: str, user: User, p: WholesalePortfolio, raw: bytes, file_name: str,
               reference: Optional[str], note: Optional[str]) -> WholesalePortfolioSource:
    header, values = _read_table(raw, file_name)
    rows, dropped = [], set()
    for i, vals in enumerate(values, start=1):
        if not any(v not in (None, "") and str(v).strip() for v in vals):
            continue
        data, drop = _row_from_mapping(header, list(vals))
        dropped.update(drop)
        rows.append(_stage_row(i, data))
        if len(rows) > MAX_ROWS:
            raise HTTPException(status_code=413, detail="More than %d rows. Split the file." % MAX_ROWS)
    if not rows:
        raise HTTPException(status_code=400, detail="No rows found under the header.")
    kind = "xlsx" if file_name.lower().endswith((".xlsx", ".xlsm")) else "csv"
    return _new_source(db, org_id, user, p, kind, rows, file_name, reference, note,
                       {"dropped_columns": sorted(dropped)})


def stage_rows(db: Session, org_id: str, user: User, p: WholesalePortfolio, kind: str, rows_in: List[Dict[str, Any]],
               reference: Optional[str], note: Optional[str]) -> WholesalePortfolioSource:
    """Rows typed in, or read off a photographed list by a person. They start
    at LOW confidence and needing review: a transcription is not a verified
    record until someone checks it against the original."""
    if kind not in ("photo", "manual"):
        raise HTTPException(status_code=422, detail="Typed rows are 'photo' (read off an image) or 'manual'.")
    if kind == "photo" and not (reference or "").strip():
        raise HTTPException(status_code=422, detail="Say which image these rows were read from.")
    rows = []
    for i, data in enumerate(rows_in or [], start=1):
        clean = {k: data.get(k) for k in EDITABLE_ROW_FIELDS if k in data}
        if _PERSONAL.search(" ".join(str(k) for k in data.keys() if k not in EDITABLE_ROW_FIELDS)):
            pass                                                     # personal fields are never read
        rows.append(_stage_row(i, clean))
    if not rows:
        raise HTTPException(status_code=422, detail="No rows.")
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=413, detail="More than %d rows." % MAX_ROWS)
    return _new_source(db, org_id, user, p, kind, rows, None, reference, note, {})


def _new_source(db, org_id, user, p, kind, rows, file_name, reference, note, created):
    if kind not in SOURCE_KINDS:
        raise HTTPException(status_code=422, detail="Unknown source kind.")
    _analyze_rows(db, org_id, p, rows, kind)
    s = WholesalePortfolioSource(organization_id=org_id, portfolio_id=p.id, kind=kind,
                                 file_name=(file_name or None) and file_name[:200],
                                 reference=(reference or "").strip()[:300] or None,
                                 note=(note or "").strip() or None, status="preview", rows=json.dumps(rows),
                                 created=json.dumps(created), created_by_id=user.id)
    db.add(s)
    db.flush()
    return s


def get_source(db: Session, org_id: str, p: WholesalePortfolio, sid: str) -> WholesalePortfolioSource:
    s = (db.query(WholesalePortfolioSource)
         .filter(WholesalePortfolioSource.id == sid, WholesalePortfolioSource.portfolio_id == p.id,
                 WholesalePortfolioSource.organization_id == org_id).first())
    if s is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return s


def edit_row(db: Session, org_id: str, p: WholesalePortfolio, s: WholesalePortfolioSource, n: int,
             data: Dict[str, Any]) -> None:
    if s.status != "preview":
        raise HTTPException(status_code=409, detail="This import is already %s." % s.status)
    rows = _jl(s.rows, [])
    row = next((r for r in rows if r.get("n") == n), None)
    if row is None:
        raise HTTPException(status_code=404, detail="Row not found")
    for k in EDITABLE_ROW_FIELDS:
        if k in data:
            row[k] = (str(data[k]).strip() if data[k] is not None else None) or None
    if "decision" in data:
        dec = data.get("decision")
        if dec not in ("create", "link", "skip", None):
            raise HTTPException(status_code=422, detail="Decision must be create, link or skip.")
        if dec == "link":
            pid = data.get("link_property_id") or (row.get("matches") or [{}])[0].get("property_id")
            if not pid or not (db.query(WholesaleProperty.id)
                               .filter(WholesaleProperty.id == pid, WholesaleProperty.organization_id == org_id).first()):
                raise HTTPException(status_code=422, detail="Choose the existing property to link to.")
            row["link_property_id"] = pid
        else:
            row["link_property_id"] = None
        row["decision"] = dec
    elif any(k in data for k in EDITABLE_ROW_FIELDS) and row.get("decision") in ("fix", "decide"):
        row["decision"] = None                       # re-check the corrected row
    _analyze_rows(db, org_id, p, rows, s.kind)
    s.rows = json.dumps(rows)


def commit_source(db: Session, org_id: str, user: User, p: WholesalePortfolio, s: WholesalePortfolioSource,
                  ) -> Dict[str, Any]:
    if s.status != "preview":
        raise HTTPException(status_code=409, detail="This import is already %s." % s.status)
    rows = _jl(s.rows, [])
    _analyze_rows(db, org_id, p, rows, s.kind)
    blocked = [r["source_row"] for r in rows if r.get("decision") in ("fix", "decide")]
    if blocked:
        raise HTTPException(status_code=409, detail="Rows %s still need a decision or a correction." % ", ".join(
            str(b) for b in blocked[:20]))
    created_items, reactivated, created_props, linked = [], [], [], []
    ref_base = s.reference or s.file_name or s.kind
    for r in rows:
        dec = r.get("decision")
        if dec == "skip":
            continue
        fields = {"source_row": int(r["source_row"]) if str(r.get("source_row") or "").isdigit() else None,
                  "source_ref": (r.get("source_ref") or ref_base)[:300],
                  "seller_address_text": " ".join(filter(None, [r.get("address"), r.get("unit"), r.get("city"),
                                                                r.get("state"), r.get("zip_code")]))[:300] or None,
                  "seller_price": r.get("seller_price"), "seller_rent": None if r.get("is_land") else r.get("seller_rent"),
                  "seller_type": r.get("seller_type"), "is_land": r.get("is_land"),
                  "source_notes": r.get("source_notes"),
                  "extraction_confidence": r.get("extraction_confidence") or ("high" if s.kind in ("csv", "xlsx")
                                                                              else "low"),
                  "review_status": "needs_review" if s.kind in ("photo", "manual") or r.get("issues") else "reviewed"}
        if dec == "link":
            prop = (db.query(WholesaleProperty).filter(WholesaleProperty.id == r.get("link_property_id"),
                                                       WholesaleProperty.organization_id == org_id).first())
            if prop is None:
                raise HTTPException(status_code=409, detail="Row %s: the property to link no longer exists."
                                    % r["source_row"])
            linked.append(prop.id)
        else:
            data = {"street_address": r.get("address"), "unit": r.get("unit"), "city": r.get("city"),
                    "state": r.get("state"), "zip_code": r.get("zip_code"), "county": r.get("county"),
                    "parcel_apn": r.get("parcel_apn"),
                    "property_type": "land" if r.get("is_land") else None,
                    "acquisition_source": "portfolio:#%03d" % p.number,
                    "source_detail": ("%s, row %s" % (ref_base, r["source_row"]))[:250],
                    "is_test": bool(p.is_test),
                    "notes": "Added from seller portfolio #%03d (%s). Seller's listing is unverified." % (
                        p.number, p.name)}
            if not r.get("is_land"):
                for k in ("bedrooms", "bathrooms", "square_feet"):
                    v = _d(r.get(k))
                    if v is not None:
                        data[k] = int(v) if k == "square_feet" else float(v)
            prop = svc.create_property(db, org_id, user, data, actor_type=ACTOR_USER)
            created_props.append(prop.id)
        item, outcome = link_item(db, org_id, user, p, prop, fields, s)
        if outcome == "created":
            created_items.append(item.id)
        elif outcome == "reactivated":
            reactivated.append(item.id)
    s.status = "committed"
    s.committed_at = _now()
    s.committed_by_id = user.id
    s.rows = json.dumps(rows)
    s.created = json.dumps({"items": created_items, "reactivated": reactivated, "properties": created_props,
                            "linked_properties": linked})
    svc.log_event(db, org_id, "portfolio.import_committed", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Seller list imported into portfolio #%03d: %d new properties, %d linked" % (
                      p.number, len(created_props), len(linked)),
                  details={"portfolio_id": p.id, "source_id": s.id, "items": len(created_items) + len(reactivated)})
    return {"properties_created": len(created_props), "properties_linked": len(linked),
            "items_added": len(created_items) + len(reactivated),
            "skipped": sum(1 for r in rows if r.get("decision") == "skip")}


def _pristine(db: Session, org_id: str, prop_id: str, portfolio_items_ok: Set[str]) -> bool:
    """True when nothing has happened to this property since an import made it."""
    from app.models.wholesale_models import WholesaleFile, WholesaleRepairEstimate, WholesaleSellerProfile
    others = (db.query(WholesalePortfolioItem.id)
              .filter(WholesalePortfolioItem.property_id == prop_id, WholesalePortfolioItem.organization_id == org_id)
              .all())
    if any(o[0] not in portfolio_items_ok for o in others):
        return False
    deals = db.query(WholesaleDeal).filter(WholesaleDeal.property_id == prop_id,
                                           WholesaleDeal.organization_id == org_id).all()
    deal_ids = [d.id for d in deals]
    for d in deals:
        if d.arv is not None or d.proposed_offer is not None or d.repair_estimate is not None \
                or d.seller_lead_id or d.contract_price is not None:
            return False
    if deal_ids:
        for model in (WholesaleComp, WholesaleOffer, WholesaleRepairEstimate):
            if db.query(model.id).filter(model.deal_id.in_(deal_ids)).first():
                return False
    if db.query(WholesaleFile.id).filter(WholesaleFile.property_id == prop_id).first():
        return False
    if db.query(WholesaleSellerProfile.id).filter(WholesaleSellerProfile.property_id == prop_id).first():
        return False
    acts = (db.query(WholesaleEvent.action)
            .filter(WholesaleEvent.organization_id == org_id,
                    (WholesaleEvent.property_id == prop_id) | (WholesaleEvent.deal_id.in_(deal_ids or ["-"]))).all())
    return all(a[0] in ("property.created", "deal.stage_changed") for a in acts)


def undo_source(db: Session, org_id: str, user: User, p: WholesalePortfolio, s: WholesalePortfolioSource,
                ) -> Dict[str, Any]:
    """Take a committed import back out: its portfolio links, and the property
    records it created that nobody has worked since. Anything somebody has
    recorded work on is KEPT, and listed."""
    if s.status != "committed":
        raise HTTPException(status_code=409, detail="Only a committed import can be undone.")
    created = _jl(s.created, {})
    items = list(created.get("items") or []) + list(created.get("reactivated") or [])
    pkgs = _active_packages_with(db, org_id, items)
    if pkgs:
        raise HTTPException(status_code=409, detail="Properties from this import are in draft packages (%s). "
                            "Take them out of those packages first." % ", ".join(k.name for k in pkgs))
    new_items = set(created.get("items") or [])
    for it in db.query(WholesalePortfolioItem).filter(WholesalePortfolioItem.id.in_(items or ["-"]),
                                                      WholesalePortfolioItem.organization_id == org_id).all():
        if it.id in new_items:
            db.delete(it)
        else:
            it.removed_at = _now()
            it.removed_by_id = user.id
    db.flush()
    deleted, kept = [], []
    for pid in created.get("properties") or []:
        prop = (db.query(WholesaleProperty)
                .filter(WholesaleProperty.id == pid, WholesaleProperty.organization_id == org_id).first())
        if prop is None:
            continue
        if not _pristine(db, org_id, pid, set()):
            kept.append(svc.address_line(prop) or pid)
            continue
        deal_ids = [d[0] for d in db.query(WholesaleDeal.id).filter(WholesaleDeal.property_id == pid).all()]
        (db.query(WholesaleEvent).filter(WholesaleEvent.organization_id == org_id,
                                         (WholesaleEvent.property_id == pid) |
                                         (WholesaleEvent.deal_id.in_(deal_ids or ["-"])))
         .delete(synchronize_session=False))
        db.query(WholesaleDeal).filter(WholesaleDeal.property_id == pid).delete(synchronize_session=False)
        deleted.append(svc.address_line(prop) or pid)
        db.delete(prop)
    db.flush()
    s.status = "undone"
    s.undone_at = _now()
    s.undone_by_id = user.id
    svc.log_event(db, org_id, "portfolio.import_undone", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Import undone on portfolio #%03d: %d properties removed, %d kept" % (
                      p.number, len(deleted), len(kept)),
                  details={"portfolio_id": p.id, "source_id": s.id, "deleted": deleted, "kept": kept})
    return {"links_removed": len(items), "properties_deleted": len(deleted), "properties_kept": kept}


# ── packages ────────────────────────────────────────────────────────────────

SNAPSHOT_KEYS = ("seller_price", "arv", "repairs", "transaction_costs", "as_is_value", "mao", "target_offer")
STALE_KEYS = ("seller_price", "arv", "repairs", "transaction_costs", "as_is_value")


def get_package(db: Session, org_id: str, p: WholesalePortfolio, pkg_id: str) -> WholesalePortfolioPackage:
    k = (db.query(WholesalePortfolioPackage)
         .filter(WholesalePortfolioPackage.id == pkg_id, WholesalePortfolioPackage.portfolio_id == p.id,
                 WholesalePortfolioPackage.organization_id == org_id).first())
    if k is None:
        raise HTTPException(status_code=404, detail="Package not found")
    return k


def _snap(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: row.get(k) for k in SNAPSHOT_KEYS}


def _rows_by_item(db: Session, org_id: str, p: WholesalePortfolio, settings) -> Dict[str, Dict[str, Any]]:
    return {r["item_id"]: r for r in item_rows(db, org_id, p, None, settings)}


def _add_lines(db: Session, org_id: str, k: WholesalePortfolioPackage, item_ids: List[str],
               rows: Dict[str, Dict[str, Any]], visible: Optional[Set[str]]) -> int:
    have = {x[0] for x in db.query(WholesalePortfolioPackageItem.item_id)
            .filter(WholesalePortfolioPackageItem.package_id == k.id).all()}
    n = 0
    for iid in item_ids or []:
        r = rows.get(iid)
        if r is None or iid in have:
            continue
        if visible is not None and r["assigned_to_id"] not in visible:
            continue
        db.add(WholesalePortfolioPackageItem(organization_id=org_id, package_id=k.id, item_id=iid,
                                             offer_amount=_d(r.get("target_offer")),
                                             snapshot=json.dumps(_snap(r)), snapshot_at=_now()))
        have.add(iid)
        n += 1
    db.flush()
    return n


def _discount(mode: Optional[str], value: Any) -> Tuple[str, Optional[Decimal]]:
    mode = (mode or "none").strip().lower()
    if mode not in DISCOUNT_MODES:
        raise HTTPException(status_code=422, detail="Discount must be none, amount, percent or total.")
    v = _d(value)
    if mode == "none":
        return mode, None
    if v is None or v < 0:
        raise HTTPException(status_code=422, detail="Enter the discount as a positive number.")
    if mode == "percent" and v >= 100:
        raise HTTPException(status_code=422, detail="A percentage discount must be below 100.")
    return mode, v


def create_package(db: Session, org_id: str, user: User, p: WholesalePortfolio, data: Dict[str, Any],
                   ) -> WholesalePortfolioPackage:
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name the package.")
    mode, value = _discount(data.get("discount_mode"), data.get("discount_value"))
    k = WholesalePortfolioPackage(organization_id=org_id, portfolio_id=p.id, name=name[:120], status="draft",
                                  discount_mode=mode, discount_value=value,
                                  notes=(data.get("notes") or "").strip() or None, created_by_id=user.id)
    db.add(k)
    db.flush()
    settings = svc.resolve_settings(db, org_id, commit=False)
    ids = data.get("item_ids") or []
    if data.get("all"):
        ids = [i.id for i, _ in _items(db, org_id, p.id)]
    _add_lines(db, org_id, k, ids, _rows_by_item(db, org_id, p, settings), visible_ids(db, org_id, user))
    svc.log_event(db, org_id, "portfolio.package_created", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Package '%s' created on portfolio #%03d" % (k.name, p.number),
                  details={"package_id": k.id})
    return k


def update_package(db: Session, org_id: str, user: User, p: WholesalePortfolio, k: WholesalePortfolioPackage,
                   data: Dict[str, Any]) -> None:
    if k.status == "archived" and data.get("status") != "draft":
        raise HTTPException(status_code=409, detail="This package is archived. Restore it to change it.")
    settings = svc.resolve_settings(db, org_id, commit=False)
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Name the package.")
        k.name = name[:120]
    if "notes" in data:
        k.notes = (data.get("notes") or "").strip() or None
    if "discount_mode" in data or "discount_value" in data:
        k.discount_mode, k.discount_value = _discount(data.get("discount_mode", k.discount_mode),
                                                      data.get("discount_value", k.discount_value))
    if "status" in data:
        if data["status"] not in ("draft", "archived"):
            raise HTTPException(status_code=422, detail="Status must be draft or archived.")
        k.status = data["status"]
        k.archived_at = _now() if k.status == "archived" else None
    visible = visible_ids(db, org_id, user)
    if data.get("add_item_ids"):
        _add_lines(db, org_id, k, data["add_item_ids"], _rows_by_item(db, org_id, p, settings), visible)
    if data.get("remove_item_ids"):
        (db.query(WholesalePortfolioPackageItem)
         .filter(WholesalePortfolioPackageItem.package_id == k.id,
                 WholesalePortfolioPackageItem.item_id.in_(data["remove_item_ids"]))
         .delete(synchronize_session=False))
    for change in data.get("offers") or []:
        line = (db.query(WholesalePortfolioPackageItem)
                .filter(WholesalePortfolioPackageItem.package_id == k.id,
                        WholesalePortfolioPackageItem.item_id == change.get("item_id")).first())
        if line is None:
            continue
        v = _d(change.get("offer_amount"))
        if v is not None and v <= 0:
            raise HTTPException(status_code=422, detail="An offer must be above zero.")
        line.offer_amount = v
        line.updated_at = _now()
    if data.get("refresh_snapshots"):
        rows = _rows_by_item(db, org_id, p, settings)
        for line in db.query(WholesalePortfolioPackageItem).filter(WholesalePortfolioPackageItem.package_id == k.id):
            r = rows.get(line.item_id)
            if r is not None:
                line.snapshot = json.dumps(_snap(r))
                line.snapshot_at = _now()
    k.updated_at = _now()
    svc.log_event(db, org_id, "portfolio.package_updated", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Package '%s' updated (portfolio #%03d)" % (k.name, p.number),
                  details={"package_id": k.id, "changes": sorted(data.keys())})


def duplicate_package(db: Session, org_id: str, user: User, p: WholesalePortfolio, k: WholesalePortfolioPackage,
                      name: Optional[str]) -> WholesalePortfolioPackage:
    copy = WholesalePortfolioPackage(organization_id=org_id, portfolio_id=p.id,
                                     name=((name or "").strip() or ("%s (copy)" % k.name))[:120], status="draft",
                                     discount_mode=k.discount_mode, discount_value=k.discount_value, notes=k.notes,
                                     copied_from_id=k.id, created_by_id=user.id)
    db.add(copy)
    db.flush()
    for line in db.query(WholesalePortfolioPackageItem).filter(WholesalePortfolioPackageItem.package_id == k.id):
        db.add(WholesalePortfolioPackageItem(organization_id=org_id, package_id=copy.id, item_id=line.item_id,
                                             offer_amount=line.offer_amount, snapshot=line.snapshot,
                                             snapshot_at=line.snapshot_at))
    db.flush()
    svc.log_event(db, org_id, "portfolio.package_duplicated", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Package '%s' copied to '%s'" % (k.name, copy.name),
                  details={"package_id": copy.id, "from": k.id})
    return copy


def package_view(db: Session, org_id: str, p: WholesalePortfolio, k: WholesalePortfolioPackage,
                 rows: Dict[str, Dict[str, Any]], visible: Optional[Set[str]]) -> Optional[Dict[str, Any]]:
    """The package with its lines and totals. None when it contains a property
    this login may not see (its totals would reveal that property)."""
    lines_db = (db.query(WholesalePortfolioPackageItem)
                .filter(WholesalePortfolioPackageItem.package_id == k.id,
                        WholesalePortfolioPackageItem.organization_id == org_id).all())
    lines = []
    for line in lines_db:
        r = rows.get(line.item_id)
        if r is None:
            continue                                   # removed from the portfolio since
        if visible is not None and r["assigned_to_id"] not in visible:
            return None
        snap = _jl(line.snapshot, {})
        changed = [key for key in STALE_KEYS if snap.get(key) != r.get(key)]
        offer = _d(line.offer_amount)
        arv, repairs, costs, mao = _d(r["arv"]), _d(r["repairs"]), _d(r["transaction_costs"]), _d(r["mao"])
        lines.append({"item_id": line.item_id, "property_id": r["property_id"], "deal_id": r["deal_id"],
                      "address": r["address"], "is_land": r["is_land"], "status": r["status"],
                      "status_label": r["status_label"],
                      "seller_price": r["seller_price"], "seller_rent": r["seller_rent"], "arv": r["arv"],
                      "arv_confidence": r["arv_confidence"], "repairs": r["repairs"],
                      "transaction_costs": r["transaction_costs"], "mao": r["mao"],
                      "target_offer": r["target_offer"], "offer": _f(offer),
                      "offer_over_mao": bool(offer is not None and mao is not None and offer > mao),
                      "estimated_profit": _f(profit_at(arv, offer, repairs, costs)),
                      "stale": changed, "stale_detail": {key: {"was": snap.get(key), "now": r.get(key)}
                                                         for key in changed},
                      "target_changed": snap.get("target_offer") != r.get("target_offer"),
                      "snapshot_at": _iso(line.snapshot_at)})
    lines.sort(key=lambda x: (rows[x["item_id"]].get("source_row") or 10 ** 9, x["address"]))
    return {"id": k.id, "portfolio_id": p.id, "name": k.name, "status": k.status, "notes": k.notes,
            "discount_mode": k.discount_mode, "discount_value": _f(k.discount_value),
            "copied_from_id": k.copied_from_id, "created_at": _iso(k.created_at), "updated_at": _iso(k.updated_at),
            "lines": lines, "totals": package_totals(lines, k.discount_mode, _d(k.discount_value))}


def package_totals(lines: List[Dict[str, Any]], mode: str, value: Optional[Decimal]) -> Dict[str, Any]:
    """Every package figure, computed in ONE place for the screen and the exports."""
    n = len(lines)
    asking, a_miss = _sum(_d(x["seller_price"]) for x in lines)
    arv, v_miss = _sum(_d(x["arv"]) for x in lines)
    rep, r_miss = _sum(_d(x["repairs"]) for x in lines)
    costs, c_miss = _sum(_d(x["transaction_costs"]) for x in lines)
    offers, o_miss = _sum(_d(x["offer"]) for x in lines)
    mao, m_miss = _sum(_d(x["mao"]) for x in lines)
    # Land carries no rent; it is not "missing" a rent.
    rent, rent_miss = _sum(_d(x["seller_rent"]) for x in lines if not x["is_land"])
    valued = n and not (v_miss or r_miss or c_miss)
    steps: List[str] = []
    package_offer, basis = None, None
    if mode == "none":
        package_offer = offers if n and not o_miss else None
        basis = "sum of this package's property offers"
    elif mode == "total":
        package_offer = value
        basis = "negotiated package total"
    elif n and not a_miss:
        if mode == "amount":
            package_offer = asking - value
            basis = "seller asking total $%s - $%s" % (format(int(asking), ","), format(int(value), ","))
        else:
            package_offer = asking * (Decimal(1) - value / Decimal(100))
            basis = "seller asking total $%s - %s%%" % (format(int(asking), ","), format(value.normalize(), "f"))
    if package_offer is not None and package_offer <= 0:
        steps.append("The package offer is at or below zero; check the discount.")
    profit_individual = (arv - offers - rep - costs) if valued and not o_miss else None
    profit_package = (arv - package_offer - rep - costs) if valued and package_offer is not None else None
    if not n:
        why_no_profit = "No properties in this package."
    elif not valued:
        why_no_profit = "%d of %d properties still need an ARV or repairs." % (
            max(v_miss, r_miss, c_miss), n)
    else:
        why_no_profit = None
    if valued:
        steps.append("Estimated profit = value $%s - offer - repairs $%s - transaction costs $%s" % (
            format(int(arv), ","), format(int(rep), ","), format(int(costs), ",")))
    return {
        "properties": n, "land": sum(1 for x in lines if x["is_land"]),
        "seller_asking": _f(asking) if n - a_miss else None, "seller_asking_missing": a_miss,
        "seller_rent_monthly": _f(rent) if n - rent_miss - sum(1 for x in lines if x["is_land"]) else None,
        "seller_rent_missing": rent_miss,
        "estimated_value": _f(arv) if n - v_miss else None, "estimated_value_missing": v_miss,
        "repairs": _f(rep) if n - r_miss else None, "repairs_missing": r_miss,
        "transaction_costs": _f(costs) if n - c_miss else None,
        "mao_total": _f(mao) if n - m_miss else None, "mao_missing": m_miss,
        "individual_offers": _f(offers) if n - o_miss else None, "offers_missing": o_miss,
        "package_offer": _f(package_offer), "package_offer_basis": basis,
        "discount_from_asking": _f(asking - package_offer) if package_offer is not None and n and not a_miss else None,
        "discount_from_asking_pct": _f((asking - package_offer) / asking * 100)
        if package_offer is not None and n and not a_miss and asking else None,
        "profit_at_individual_offers": _f(profit_individual),
        "profit_at_package_offer": _f(profit_package),
        "package_vs_individual": _f(profit_package - profit_individual)
        if profit_package is not None and profit_individual is not None else None,
        "gross_rent_yield_pct": _f(rent * 12 / package_offer * 100)
        if package_offer and rent and not rent_miss else None,
        "complete": bool(valued), "why_no_profit": why_no_profit,
        "stale_lines": sum(1 for x in lines if x["stale"]),
        "offers_over_mao": sum(1 for x in lines if x["offer_over_mao"]),
        "steps": steps,
        "labels": {"seller_asking": "Seller stated, not verified",
                   "seller_rent_monthly": "Seller stated scheduled rent - gross, before any expense",
                   "gross_rent_yield_pct": "Seller-stated rent x 12 / package offer. Gross, not a return."},
    }


def packages(db: Session, org_id: str, user: User, p: WholesalePortfolio, include_archived: bool = True,
             ) -> List[Dict[str, Any]]:
    settings = svc.resolve_settings(db, org_id, commit=False)
    rows = _rows_by_item(db, org_id, p, settings)
    visible = visible_ids(db, org_id, user)
    q = db.query(WholesalePortfolioPackage).filter(WholesalePortfolioPackage.portfolio_id == p.id,
                                                   WholesalePortfolioPackage.organization_id == org_id)
    if not include_archived:
        q = q.filter(WholesalePortfolioPackage.status == "draft")
    out = []
    for k in q.order_by(WholesalePortfolioPackage.created_at.asc()).all():
        v = package_view(db, org_id, p, k, rows, visible)
        if v is not None:
            out.append(v)
    return out


COMPARE_ROWS = [("properties", "Properties", "n"), ("seller_asking", "Seller asking total", "$"),
                ("individual_offers", "Individual offers", "$"), ("package_offer", "Package offer", "$"),
                ("discount_from_asking", "Discount from asking", "$"),
                ("discount_from_asking_pct", "Discount from asking %", "%"),
                ("estimated_value", "Estimated value (ARV)", "$"), ("repairs", "Repairs", "$"),
                ("transaction_costs", "Transaction costs", "$"), ("mao_total", "Maximum allowable offers", "$"),
                ("profit_at_individual_offers", "Est. profit at individual offers", "$"),
                ("profit_at_package_offer", "Est. profit at package offer", "$"),
                ("package_vs_individual", "Package vs individual offers", "$"),
                ("seller_rent_monthly", "Seller-stated rent / month", "$"),
                ("gross_rent_yield_pct", "Gross rent yield at package offer", "%")]


PARTIAL_OF = {"seller_asking": "seller_asking_missing", "individual_offers": "offers_missing",
              "estimated_value": "estimated_value_missing", "repairs": "repairs_missing",
              "transaction_costs": "estimated_value_missing", "mao_total": "mao_missing",
              "seller_rent_monthly": "seller_rent_missing"}


def compare(pkgs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Side by side, with no recommendation: the larger discount is not
    called 'better'. Each row is labelled; profit is shown only where every
    property in that package is valued."""
    drafts = [k for k in pkgs if k["status"] == "draft"]
    return {"packages": [{"id": k["id"], "name": k["name"], "complete": k["totals"]["complete"],
                          "why_no_profit": k["totals"]["why_no_profit"], "stale_lines": k["totals"]["stale_lines"]}
                         for k in drafts],
            "rows": [{"key": key, "label": label, "kind": kind,
                      "values": [k["totals"].get(key) for k in drafts],
                      # a sum over only some of the package's properties is marked partial
                      "partial": [bool(PARTIAL_OF.get(key) and k["totals"].get(PARTIAL_OF[key])
                                       and k["totals"].get(key) is not None) for k in drafts]}
                     for key, label, kind in COMPARE_ROWS],
            "note": "Packages are compared on the same figures. A bigger discount is not ranked higher; "
                    "estimated profit is shown only when every property in a package is valued."}


# ── exports ─────────────────────────────────────────────────────────────────

def _csv(rows: List[List[Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return buf.getvalue()


INVENTORY_COLS = [("source_row", "Row"), ("address", "Address"), ("property_type", "Type"),
                  ("occupancy_status", "Occupancy"), ("seller_price", "Seller asking (stated)"),
                  ("seller_rent", "Seller rent / month (stated)"), ("arv", "Estimated value (ARV)"),
                  ("arv_source", "Value source"), ("arv_confidence", "Value confidence"),
                  ("valuation_at", "Valuation date"), ("as_is_value", "As-is value (reference)"),
                  ("as_is_value_source", "As-is source"), ("repairs", "Estimated repairs"),
                  ("repair_status_label", "Repair status"), ("transaction_costs", "Transaction costs"),
                  ("mao", "Maximum allowable offer"), ("target_offer", "Our target offer"),
                  ("estimated_profit", "Est. profit at target"), ("discount_from_asking", "Discount from asking"),
                  ("status_label", "Evaluation status"), ("review_status", "Listing review"),
                  ("extraction_confidence", "Listing confidence"), ("source_ref", "Source")]


def export_inventory(p: WholesalePortfolio, rows: List[Dict[str, Any]]) -> str:
    out = [["Seller Portfolio #%03d - %s" % (p.number, p.name)], [ESTIMATE_NOTICE],
           ["Seller figures are as the seller stated them and are not verified."], [],
           [label for _, label in INVENTORY_COLS]]
    out += [[r.get(k) for k, _ in INVENTORY_COLS] for r in rows]
    return _csv(out)


def export_item(ev: Dict[str, Any]) -> str:
    r = ev["row"]
    a = ev.get("analysis") or {}
    out = [["Property evaluation - %s" % r["address"]], [ESTIMATE_NOTICE], []]
    out += [[label, r.get(k)] for k, label in INVENTORY_COLS]
    out += [[], ["Assumptions (editable starting points, not market data)"]]
    for k, v in sorted((a.get("assumptions") or {}).items()):
        out.append([(a.get("assumption_labels") or {}).get(k, k), v])
    for st in ((a.get("offer") or {}).get("steps") or []):
        out.append(["MAO step: %s" % st.get("label"), st.get("value"), st.get("note")])
    if ev.get("rent_note"):
        out += [[], [ev["rent_note"]]]
    return _csv(out)


PACKAGE_COLS = [("address", "Address"), ("status_label", "Evaluation status"), ("seller_price", "Seller asking (stated)"),
                ("seller_rent", "Seller rent / month (stated)"), ("arv", "Estimated value (ARV)"),
                ("arv_confidence", "Value confidence"), ("repairs", "Repairs"),
                ("transaction_costs", "Transaction costs"), ("mao", "Maximum allowable offer"),
                ("offer", "This package's offer"), ("estimated_profit", "Est. profit at this offer"),
                ("snapshot_at", "Figures as of")]


def export_package(p: WholesalePortfolio, k: Dict[str, Any]) -> str:
    t = k["totals"]
    out = [["Package '%s' - Seller Portfolio #%03d" % (k["name"], p.number)], [ESTIMATE_NOTICE], [],
           [label for _, label in PACKAGE_COLS]]
    out += [[x.get(key) for key, _ in PACKAGE_COLS] + (["Built on older figures: " + ", ".join(x["stale"])]
                                                       if x["stale"] else []) for x in k["lines"]]
    out += [[]] + [[label, t.get(key)] for key, label, _ in COMPARE_ROWS]
    out += [["Package offer basis", t.get("package_offer_basis")]]
    if t.get("why_no_profit"):
        out.append(["Profit not shown", t["why_no_profit"]])
    return _csv(out)


def export_compare(p: WholesalePortfolio, cmp: Dict[str, Any]) -> str:
    out = [["Package comparison - Seller Portfolio #%03d - %s" % (p.number, p.name)], [ESTIMATE_NOTICE], [cmp["note"]],
           [], [""] + [k["name"] for k in cmp["packages"]]]
    out += [[r["label"]] + ["%s (partial)" % v if part else v for v, part in zip(r["values"], r["partial"])]
            for r in cmp["rows"]]
    return _csv(out)
