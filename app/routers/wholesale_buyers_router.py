"""Wholesale Real Estate — disposition side.

The cash-buyer CRM, structured buy boxes, buyer import, the matching engine and
buyer outreach. Same prefix and the same three gates as `wholesale_router.py`;
split into its own file only because one file holding both sides of a wholesale
deal would be unreadable.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
No scraping, no public-record harvesting, no third-party buyer-list purchase.
"Do not scrape websites tonight unless an existing lawful integration already
supports it. Build the intake architecture first." So what ships is the intake:
manual entry, CSV import, and `source` on every buyer so a future provider drops
in as another value rather than another table.
"""

import csv
import io
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Depends, File, HTTPException, Query, Request,
                     UploadFile)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import (get_db, require_not_observation, require_tenant_or_observer,
                      require_tenant_user)
from app.models.models import User
from app.models.wholesale_models import (
    ACTOR_USER, BUYER_RESPONSE_STATUSES, POF_STATUSES, WholesaleBuyBox,
    WholesaleBuyer, WholesaleBuyerMatch, WholesaleBuyerOutreach, WholesaleDeal,
    WholesaleProperty,
)
from app.services import wholesale_analysis as analysis
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

log = logging.getLogger(__name__)

router = APIRouter(prefix="/wholesale", tags=["wholesale-buyers"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])

STRATEGIES = ("flip", "rental", "brrrr", "land", "other")
REHAB_LEVELS = ("light", "moderate", "heavy", "full_gut")


def _num(value: Any) -> Optional[float]:
    d = analysis.money(value)
    return None if d is None else float(d)


def _jsonl(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def buy_box_json(b: WholesaleBuyBox) -> Dict[str, Any]:
    return {
        "id": b.id, "buyer_id": b.buyer_id, "label": b.label,
        "is_active": bool(b.is_active),
        "markets": _jsonl(b.markets), "states": _jsonl(b.states),
        "counties": _jsonl(b.counties), "cities": _jsonl(b.cities),
        "zips": _jsonl(b.zips), "property_types": _jsonl(b.property_types),
        "strategies": _jsonl(b.strategies),
        "min_price": _num(b.min_price), "max_price": _num(b.max_price),
        "min_beds": _num(b.min_beds), "max_beds": _num(b.max_beds),
        "min_baths": _num(b.min_baths),
        "min_sqft": b.min_sqft, "max_sqft": b.max_sqft,
        "min_year_built": b.min_year_built, "max_year_built": b.max_year_built,
        "rehab_tolerance": b.rehab_tolerance, "min_spread": _num(b.min_spread),
        "notes": b.notes,
    }


def _buyer_activity(db: Session, org_id: str,
                    buyer_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """What each buyer has actually done here, in one query for the page.

    THIS IS NOT A SCORE AND IT IS NOT A RANKING. It is a count of rows that
    exist: deal sheets sent, replies received, offers made, deals they were
    chosen for. A buyer with no history reads as no history rather than as a
    bad buyer — a new buyer and a bad buyer are not the same thing, and a CRM
    that conflated them would quietly bury everybody's new contacts.
    """
    if not buyer_ids:
        return {}
    blank = {"sheets_sent": 0, "responded": 0, "offers_made": 0,
             "best_offer": None, "selected_count": 0,
             "last_contacted_at": None, "last_response_at": None,
             # Phase 6. The rest of the funnel, still just counts of rows that
             # exist. `opened` is only as good as what the deployment can
             # actually observe — see the note below — so it is reported as a
             # separate figure rather than folded into "responded", which
             # would make an unobservable signal look like engagement.
             "opened": 0, "interested": 0, "passed": 0,
             "deals_closed": 0, "avg_close_days": None}
    out = {bid: dict(blank) for bid in buyer_ids}

    responded_statuses = ("replied", "interested", "needs_info", "offer_submitted",
                          "selected", "passed", "rejected", "accepted",
                          "requested_info")
    for r in (db.query(WholesaleBuyerOutreach)
              .filter(WholesaleBuyerOutreach.organization_id == org_id,
                      WholesaleBuyerOutreach.buyer_id.in_(buyer_ids)).all()):
        item = out.get(r.buyer_id)
        if item is None:
            continue
        if r.sent_at is not None:
            item["sheets_sent"] += 1
            if (item["last_contacted_at"] is None
                    or r.sent_at > item["last_contacted_at"]):
                item["last_contacted_at"] = r.sent_at
        replied_at = getattr(r, "replied_at", None)
        if r.status in responded_statuses or replied_at is not None:
            item["responded"] += 1
            if replied_at is not None and (item["last_response_at"] is None
                                           or replied_at > item["last_response_at"]):
                item["last_response_at"] = replied_at
        amount = analysis.money(r.offer_amount)
        if amount is not None:
            item["offers_made"] += 1
            if item["best_offer"] is None or float(amount) > item["best_offer"]:
                item["best_offer"] = float(amount)
        if bool(getattr(r, "is_selected", False)):
            item["selected_count"] += 1
        # OPENED IS NOT INFERRED. `opened_at` is written when something this
        # deployment can actually observe happens — a share link fetched, a
        # tracked email opened. With no such signal it stays NULL and this
        # count stays 0, which is the honest answer, rather than being
        # back-filled from "we sent it so they probably read it".
        if getattr(r, "opened_at", None) is not None:
            item["opened"] += 1
        if r.status == "interested":
            item["interested"] += 1
        if r.status in ("passed", "rejected"):
            item["passed"] += 1

    # Deals this buyer was actually assigned AND that actually closed, with how
    # long the closing took from selection. Read off the deal rows, so a buyer
    # who was selected on a deal that then died is not credited with a close.
    closed = (db.query(WholesaleDeal)
              .filter(WholesaleDeal.organization_id == org_id,
                      WholesaleDeal.assigned_buyer_id.in_(buyer_ids),
                      WholesaleDeal.closed_at.isnot(None)).all())
    spans: Dict[str, List[int]] = {}
    for deal in closed:
        item = out.get(deal.assigned_buyer_id)
        if item is None:
            continue
        item["deals_closed"] += 1
        start = deal.buyer_selected_at or deal.contract_signed_at
        if start is not None and deal.closed_at is not None:
            days = (deal.closed_at - start).days
            if days >= 0:
                spans.setdefault(deal.assigned_buyer_id, []).append(days)
    for bid, days_list in spans.items():
        if days_list:
            out[bid]["avg_close_days"] = round(sum(days_list) / len(days_list))

    for item in out.values():
        for key in ("last_contacted_at", "last_response_at"):
            if item[key] is not None:
                item[key] = item[key].isoformat()
    return out


def buyer_json(b: WholesaleBuyer, boxes: Optional[List[WholesaleBuyBox]] = None,
               activity: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "id": b.id, "entity_type": b.entity_type,
        "company_name": b.company_name, "contact_name": b.contact_name,
        "display_name": b.company_name or b.contact_name or "(unnamed buyer)",
        "phone": b.phone, "email": b.email,
        "preferred_channel": b.preferred_channel,
        "cash_verified": bool(b.cash_verified),
        "proof_of_funds_on_file": bool(b.proof_of_funds_on_file),
        "proof_of_funds_expires": (b.proof_of_funds_expires.isoformat()
                                   if b.proof_of_funds_expires else None),
        "typical_close_days": b.typical_close_days,
        "past_deals_count": b.past_deals_count,
        "reliability_rating": b.reliability_rating,
        "source": b.source, "source_detail": b.source_detail, "notes": b.notes,
        "is_active": bool(b.is_active),
        "do_not_contact": bool(b.do_not_contact),
        "do_not_contact_reason": b.do_not_contact_reason,
        "is_test": bool(b.is_test),
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "buy_boxes": [buy_box_json(x) for x in (boxes if boxes is not None
                                                else (b.buy_boxes or []))],
        # The track record, when the caller asked for it. Computed from what
        # actually happened on this organization's deals, never confused with
        # the `past_deals_count` a person typed when they added the buyer —
        # both are shown, and the screen says which is which.
        "activity": activity,
    }


# ── Buyers ──────────────────────────────────────────────────────────────────

class BuyerIn(BaseModel):
    entity_type: Optional[str] = None
    company_name: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    preferred_channel: Optional[str] = None
    cash_verified: Optional[bool] = None
    proof_of_funds_on_file: Optional[bool] = None
    proof_of_funds_expires: Optional[str] = None
    typical_close_days: Optional[int] = None
    past_deals_count: Optional[int] = None
    reliability_rating: Optional[int] = None
    source: Optional[str] = None
    source_detail: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    do_not_contact: Optional[bool] = None
    do_not_contact_reason: Optional[str] = None
    is_test: Optional[bool] = None


@router.get("/buyers")
def list_buyers(db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer),
                q: Optional[str] = None,
                active_only: bool = True,
                include_test: bool = False,
                with_activity: bool = False,
                limit: int = Query(200, le=1000), offset: int = 0):
    """The buyer list.

    `with_activity` is opt-in: it adds one query over this page's outreach rows
    to say what each buyer has actually done here. The CRM screen asks for it;
    a caller that only wants names does not pay for it.
    """
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    query = db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id)
    if active_only:
        query = query.filter(WholesaleBuyer.is_active.is_(True))
    if not include_test:
        query = query.filter(WholesaleBuyer.is_test.isnot(True))
    if q:
        like = "%%%s%%" % q.strip()
        query = query.filter((WholesaleBuyer.company_name.ilike(like)) |
                             (WholesaleBuyer.contact_name.ilike(like)) |
                             (WholesaleBuyer.email.ilike(like)))
    total = query.count()
    rows = (query.order_by(WholesaleBuyer.created_at.desc())
            .offset(offset).limit(limit).all())
    boxes: Dict[str, List[WholesaleBuyBox]] = {}
    for box in db.query(WholesaleBuyBox).filter(
            WholesaleBuyBox.organization_id == org_id,
            WholesaleBuyBox.buyer_id.in_([r.id for r in rows] or [""])).all():
        boxes.setdefault(box.buyer_id, []).append(box)
    activity = (_buyer_activity(db, org_id, [r.id for r in rows])
                if with_activity else {})
    return {"total": total, "limit": limit, "offset": offset,
            "buyers": [buyer_json(b, boxes.get(b.id, []), activity.get(b.id))
                       for b in rows]}


@router.post("/buyers")
def create_buyer(payload: BuyerIn, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    data = payload.model_dump(exclude_unset=True)
    if not (data.get("company_name") or data.get("contact_name")):
        raise HTTPException(status_code=400,
                            detail="A buyer needs a company name or a contact name.")
    if not (data.get("email") or data.get("phone")):
        raise HTTPException(
            status_code=400,
            detail="A buyer needs an email or a phone number — without one there "
                   "is no way to send them a deal.")
    if "proof_of_funds_expires" in data:
        from app.routers.wholesale_router import _parse_date
        data["proof_of_funds_expires"] = _parse_date(data["proof_of_funds_expires"])
    buyer = WholesaleBuyer(organization_id=org_id, **data)
    buyer.source = buyer.source or "manual"
    db.add(buyer)
    db.flush()
    svc.log_event(db, org_id, "buyer.created", actor_type=ACTOR_USER,
                  actor_user_id=user.id,
                  summary="Buyer added: %s" % (buyer.company_name or buyer.contact_name),
                  after={"source": buyer.source, "is_test": bool(buyer.is_test)})
    db.commit()
    db.refresh(buyer)
    return buyer_json(buyer, [])


@router.delete("/buyers/{buyer_id}")
def delete_buyer(buyer_id: str, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    """Delete a buyer who has no history. Refuse, with a reason, if they have.

    DELETING IS NOT DEACTIVATING, AND THE DIFFERENCE MATTERS MOST HERE. A buyer
    who has been sent deal sheets is attached to outreach rows, buyer matches
    and, possibly, a closed deal's assignment — and a wholesaler answering "who
    did we sell 1418 Cedar to" a year later needs that name to still exist.

    So: a buyer nobody has ever contacted is a mistake in the list and is
    deleted outright, with their buy boxes. A buyer with history is refused,
    and the refusal says how much history and what to do instead. The screen
    never has to guess which case it is, because the server says.
    """
    org_id = svc.write_org_id(db, user)
    buyer = _get_buyer(db, org_id, buyer_id)

    outreach = (db.query(WholesaleBuyerOutreach)
                .filter(WholesaleBuyerOutreach.organization_id == org_id,
                        WholesaleBuyerOutreach.buyer_id == buyer_id).count())
    matches = (db.query(WholesaleBuyerMatch)
               .filter(WholesaleBuyerMatch.organization_id == org_id,
                       WholesaleBuyerMatch.buyer_id == buyer_id).count())
    assigned = (db.query(WholesaleDeal)
                .filter(WholesaleDeal.organization_id == org_id,
                        WholesaleDeal.assigned_buyer_id == buyer_id).count())
    if outreach or matches or assigned:
        parts = []
        if outreach:
            parts.append("%d deal sheet%s" % (outreach, "" if outreach == 1 else "s"))
        if matches:
            parts.append("%d match%s" % (matches, "" if matches == 1 else "es"))
        if assigned:
            parts.append("%d assigned deal%s" % (assigned, "" if assigned == 1 else "s"))
        raise HTTPException(
            status_code=409,
            detail=("This buyer is attached to %s. Deleting them would remove a "
                    "name the deal history still refers to. Mark them inactive "
                    "instead — they stop appearing in matching and nothing is "
                    "lost." % ", ".join(parts)))

    name = buyer.company_name or buyer.contact_name or buyer_id
    boxes = (db.query(WholesaleBuyBox)
             .filter(WholesaleBuyBox.organization_id == org_id,
                     WholesaleBuyBox.buyer_id == buyer_id).all())
    for box in boxes:
        db.delete(box)
    db.delete(buyer)
    db.flush()
    svc.log_event(db, org_id, "buyer.deleted", actor_type=ACTOR_USER,
                  actor_user_id=user.id,
                  summary="Buyer deleted: %s" % name,
                  before={"buyer_id": buyer_id, "name": name,
                          "buy_boxes": len(boxes)})
    db.commit()
    return {"ok": True, "deleted": buyer_id, "buy_boxes_deleted": len(boxes)}


@router.patch("/buyers/{buyer_id}")
def update_buyer(buyer_id: str, payload: BuyerIn, request: Request,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    buyer = _get_buyer(db, org_id, buyer_id)
    before = buyer_json(buyer, [])
    data = payload.model_dump(exclude_unset=True)
    if "proof_of_funds_expires" in data:
        from app.routers.wholesale_router import _parse_date
        data["proof_of_funds_expires"] = _parse_date(data["proof_of_funds_expires"])
    for key, value in data.items():
        setattr(buyer, key, value)
    db.flush()
    svc.log_event(db, org_id, "buyer.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, summary="Buyer updated",
                  before=before, after=buyer_json(buyer, []))
    db.commit()
    db.refresh(buyer)
    return buyer_json(buyer)


def _get_buyer(db: Session, org_id: str, buyer_id: str) -> WholesaleBuyer:
    buyer = (db.query(WholesaleBuyer)
             .filter(WholesaleBuyer.id == buyer_id,
                     WholesaleBuyer.organization_id == org_id).first())
    if buyer is None:
        raise HTTPException(status_code=404, detail="Buyer not found")
    return buyer


# ── Buy boxes ───────────────────────────────────────────────────────────────

class BuyBoxIn(BaseModel):
    label: Optional[str] = None
    is_active: Optional[bool] = None
    markets: Optional[List[str]] = None
    states: Optional[List[str]] = None
    counties: Optional[List[str]] = None
    cities: Optional[List[str]] = None
    zips: Optional[List[str]] = None
    property_types: Optional[List[str]] = None
    strategies: Optional[List[str]] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    min_beds: Optional[float] = None
    max_beds: Optional[float] = None
    min_baths: Optional[float] = None
    min_sqft: Optional[int] = None
    max_sqft: Optional[int] = None
    min_year_built: Optional[int] = None
    max_year_built: Optional[int] = None
    rehab_tolerance: Optional[str] = None
    min_spread: Optional[float] = None
    notes: Optional[str] = None


_BOX_JSON_FIELDS = ("markets", "states", "counties", "cities", "zips",
                    "property_types", "strategies")


def _apply_box(box: WholesaleBuyBox, data: Dict[str, Any]) -> None:
    for key, value in data.items():
        if key in _BOX_JSON_FIELDS:
            # Lowercased on the way in, because the matcher compares lowercase
            # and a buy box that says "Dallas" must match a property in "dallas".
            setattr(box, key, json.dumps([str(v).strip().lower() for v in value])
                    if value else None)
        else:
            setattr(box, key, value)


def _validate_box(data: Dict[str, Any]) -> None:
    if data.get("strategies"):
        bad = [s for s in data["strategies"] if str(s).lower() not in STRATEGIES]
        if bad:
            raise HTTPException(status_code=400,
                                detail="Unknown strategy: %s. Use one of: %s"
                                       % (", ".join(bad), ", ".join(STRATEGIES)))
    if data.get("rehab_tolerance") and data["rehab_tolerance"] not in REHAB_LEVELS:
        raise HTTPException(status_code=400,
                            detail="rehab_tolerance must be one of: %s"
                                   % ", ".join(REHAB_LEVELS))
    lo, hi = data.get("min_price"), data.get("max_price")
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(status_code=400,
                            detail="The minimum price is above the maximum price, so "
                                   "nothing could ever match this buy box.")


@router.post("/buyers/{buyer_id}/buy-boxes")
def create_buy_box(buyer_id: str, payload: BuyBoxIn, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    buyer = _get_buyer(db, org_id, buyer_id)
    data = payload.model_dump(exclude_unset=True)
    _validate_box(data)
    box = WholesaleBuyBox(organization_id=org_id, buyer_id=buyer.id)
    _apply_box(box, data)
    db.add(box)
    db.flush()
    svc.log_event(db, org_id, "buy_box.created", actor_type=ACTOR_USER,
                  actor_user_id=user.id,
                  summary="Buy box added for %s"
                          % (buyer.company_name or buyer.contact_name))
    db.commit()
    db.refresh(box)
    return buy_box_json(box)


@router.patch("/buy-boxes/{box_id}")
def update_buy_box(box_id: str, payload: BuyBoxIn, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    box = (db.query(WholesaleBuyBox)
           .filter(WholesaleBuyBox.id == box_id,
                   WholesaleBuyBox.organization_id == org_id).first())
    if box is None:
        raise HTTPException(status_code=404, detail="Buy box not found")
    data = payload.model_dump(exclude_unset=True)
    _validate_box(data)
    before = buy_box_json(box)
    _apply_box(box, data)
    db.flush()
    svc.log_event(db, org_id, "buy_box.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, summary="Buy box updated",
                  before=before, after=buy_box_json(box))
    db.commit()
    db.refresh(box)
    return buy_box_json(box)


@router.delete("/buy-boxes/{box_id}")
def delete_buy_box(box_id: str, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    box = (db.query(WholesaleBuyBox)
           .filter(WholesaleBuyBox.id == box_id,
                   WholesaleBuyBox.organization_id == org_id).first())
    if box is None:
        raise HTTPException(status_code=404, detail="Buy box not found")
    db.delete(box)
    svc.log_event(db, org_id, "buy_box.removed", actor_type=ACTOR_USER,
                  actor_user_id=user.id, summary="Buy box removed")
    db.commit()
    return {"ok": True}


# ── Buyer import ────────────────────────────────────────────────────────────

BUYER_CSV_ALIASES = {
    "company": "company_name", "company name": "company_name",
    "company_name": "company_name", "business": "company_name",
    "name": "contact_name", "contact": "contact_name",
    "contact name": "contact_name", "contact_name": "contact_name",
    "buyer": "contact_name",
    "phone": "phone", "phone number": "phone", "mobile": "phone",
    "email": "email", "email address": "email",
    "close days": "typical_close_days", "typical_close_days": "typical_close_days",
    "deals": "past_deals_count", "past deals": "past_deals_count",
    "rating": "reliability_rating", "reliability": "reliability_rating",
    "notes": "notes", "source": "source",
    # Buy box columns. A buyer row that carries these gets a buy box created
    # from them, because a buyer list without a buy box cannot be matched and
    # importing one that way would produce a CRM nobody can use.
    "states": "_states", "state": "_states",
    "counties": "_counties", "county": "_counties",
    "cities": "_cities", "city": "_cities",
    "zips": "_zips", "zip": "_zips", "zip codes": "_zips",
    "markets": "_markets", "market": "_markets",
    "property types": "_property_types", "property_types": "_property_types",
    "type": "_property_types",
    "strategy": "_strategies", "strategies": "_strategies",
    "min price": "_min_price", "min_price": "_min_price",
    "max price": "_max_price", "max_price": "_max_price",
    "min beds": "_min_beds", "min_beds": "_min_beds",
    "min sqft": "_min_sqft", "min_sqft": "_min_sqft",
    "max sqft": "_max_sqft", "max_sqft": "_max_sqft",
    "rehab": "_rehab_tolerance", "rehab tolerance": "_rehab_tolerance",
}

_BUYER_INTS = {"typical_close_days", "past_deals_count", "reliability_rating"}


@router.post("/buyers/import")
async def import_buyers(request: Request, file: UploadFile = File(...),
                        list_name: Optional[str] = Query(None),
                        is_test: bool = Query(False),
                        db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user),
                        _guard: User = Depends(require_not_observation)):
    """Import a buyer list, building each buyer's buy box from the same row.

    Every rejected row comes back with its number and the reason. A buyer with
    no email and no phone is rejected rather than stored, because a buyer nobody
    can contact is a row that makes the CRM look fuller than it is.
    """
    org_id = svc.write_org_id(db, user)
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That file is larger than 10MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="That file has no header row.")

    mapping, unmapped = {}, []
    for header in reader.fieldnames:
        key = (header or "").strip().lower()
        if key in BUYER_CSV_ALIASES:
            mapping[header] = BUYER_CSV_ALIASES[key]
        else:
            unmapped.append(header)

    source = "csv:%s" % (list_name or file.filename or "import")
    created, boxes_created, skipped = 0, 0, []

    for i, row in enumerate(reader, start=2):
        data: Dict[str, Any] = {"source": source,
                                "source_detail": list_name or file.filename,
                                "is_test": is_test}
        box: Dict[str, Any] = {}
        extras = []
        for header, value in row.items():
            if value is None or str(value).strip() == "":
                continue
            value = str(value).strip()
            field = mapping.get(header)
            if field is None:
                extras.append("%s: %s" % (header, value))
                continue
            if field.startswith("_"):
                box[field[1:]] = value
                continue
            if field in _BUYER_INTS:
                parsed = analysis.money(value)
                if parsed is None:
                    continue
                data[field] = int(parsed)
            else:
                data[field] = value
        if extras:
            data["notes"] = ((data.get("notes") or "") + "\n" + "\n".join(extras)).strip()

        if not (data.get("company_name") or data.get("contact_name")):
            skipped.append({"row": i, "reason": "no company or contact name"})
            continue
        if not (data.get("email") or data.get("phone")):
            skipped.append({"row": i, "reason": "no email and no phone — this buyer "
                                                "could not be sent a deal"})
            continue

        buyer = WholesaleBuyer(organization_id=org_id, **data)
        db.add(buyer)
        db.flush()
        created += 1

        if box:
            box_row = WholesaleBuyBox(organization_id=org_id, buyer_id=buyer.id,
                                      label="Imported")
            payload: Dict[str, Any] = {}
            for key in ("states", "counties", "cities", "zips", "markets",
                        "property_types", "strategies"):
                if box.get(key):
                    payload[key] = [p.strip() for p in str(box[key]).split(",") if p.strip()]
            for key in ("min_price", "max_price", "min_beds"):
                if box.get(key) is not None:
                    parsed = analysis.money(box[key])
                    if parsed is not None:
                        payload[key] = float(parsed)
            for key in ("min_sqft", "max_sqft"):
                if box.get(key) is not None:
                    parsed = analysis.money(box[key])
                    if parsed is not None:
                        payload[key] = int(parsed)
            if box.get("rehab_tolerance") and \
                    str(box["rehab_tolerance"]).lower() in REHAB_LEVELS:
                payload["rehab_tolerance"] = str(box["rehab_tolerance"]).lower()
            if payload:
                _apply_box(box_row, payload)
                db.add(box_row)
                boxes_created += 1

    svc.log_event(db, org_id, "buyers.imported", actor_type=ACTOR_USER,
                  actor_user_id=user.id,
                  summary="%d buyer(s) imported, %d buy box(es) created"
                          % (created, boxes_created),
                  after={"source": source, "skipped": len(skipped)})
    db.commit()
    return {"created": created, "buy_boxes_created": boxes_created,
            "skipped": skipped, "unmapped_columns": unmapped, "source": source}


# ── Matching ────────────────────────────────────────────────────────────────

@router.post("/deals/{deal_id}/match-buyers")
def match_buyers(deal_id: str, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    """Score every buyer against this deal and store the result with its reasons."""
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    rows = svc.recompute_matches(db, org_id, deal, user)
    db.commit()
    buyers = {b.id: b for b in db.query(WholesaleBuyer).filter(
        WholesaleBuyer.organization_id == org_id).all()}
    return {"matches": [{
        "id": m.id, "buyer_id": m.buyer_id,
        "buyer_name": (getattr(buyers.get(m.buyer_id), "company_name", None)
                       or getattr(buyers.get(m.buyer_id), "contact_name", None)),
        "buyer_email": getattr(buyers.get(m.buyer_id), "email", None),
        "buyer_phone": getattr(buyers.get(m.buyer_id), "phone", None),
        "score": m.score, "factors": _jsonl(m.factors),
        "disqualified": bool(m.disqualified),
        "disqualified_reason": m.disqualified_reason,
    } for m in sorted(rows, key=lambda r: (not r.disqualified, r.score), reverse=True)]}


@router.get("/deals/{deal_id}/matches")
def list_matches(deal_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    svc.get_deal(db, org_id, deal_id)
    rows = (db.query(WholesaleBuyerMatch)
            .filter(WholesaleBuyerMatch.deal_id == deal_id,
                    WholesaleBuyerMatch.organization_id == org_id)
            .order_by(WholesaleBuyerMatch.score.desc()).all())
    buyers = {b.id: b for b in db.query(WholesaleBuyer).filter(
        WholesaleBuyer.organization_id == org_id).all()}
    return {"matches": [{
        "id": m.id, "buyer_id": m.buyer_id,
        "buyer_name": (getattr(buyers.get(m.buyer_id), "company_name", None)
                       or getattr(buyers.get(m.buyer_id), "contact_name", None)),
        "buyer_email": getattr(buyers.get(m.buyer_id), "email", None),
        "score": m.score, "factors": _jsonl(m.factors),
        "disqualified": bool(m.disqualified),
        "disqualified_reason": m.disqualified_reason,
        "computed_at": m.computed_at.isoformat() if m.computed_at else None,
    } for m in rows]}


# ── Disposition ─────────────────────────────────────────────────────────────

class DispositionIn(BaseModel):
    buyer_ids: List[str]
    asking_price: Optional[float] = None
    channel: str = "email"


@router.post("/deals/{deal_id}/disposition/preview")
def preview_disposition(deal_id: str, payload: DispositionIn,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user)):
    """The exact deal sheet that would go out — read it before you send it.

    A preview exists because the one thing nobody should discover after the fact
    is what was said about a property to a list of buyers.
    """
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    deal = svc.get_deal(db, org_id, deal_id)
    prop = db.query(WholesaleProperty).filter(
        WholesaleProperty.id == deal.property_id).first()
    settings = svc.resolve_settings(db, org_id)
    composed = svc.build_buyer_message(deal, prop, settings, payload.asking_price)
    return {**composed,
            "recipients": len(payload.buyer_ids),
            "note": ("No seller name, phone, email or motivation appears in this "
                     "message. The composer does not read those fields.")}


@router.get("/disposition/channels")
def disposition_channels(db: Session = Depends(get_db),
                         user: User = Depends(require_tenant_or_observer)):
    """Can this deployment actually send a deal sheet, and on which channels?

    Read from the environment on every call rather than stored, so it cannot go
    stale, and it names the variable so an operator who sees "not enabled" knows
    what to do about it.
    """
    from app.services import wholesale_disposition as disp
    return {"channels": disp.channel_status()}


@router.post("/deals/{deal_id}/disposition")
def send_disposition(deal_id: str, payload: DispositionIn, request: Request,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user),
                     _guard: User = Depends(require_not_observation)):
    """Send the deal sheet to the buyers a person selected. Deliberate, never bulk.

    THIS NOW SENDS. Phase 1 recorded an intention; this hands the composed sheet
    to the platform's existing providers through
    `app/services/wholesale_disposition.py`, which runs six refusals first and
    writes whatever the provider answered onto the row.

    NOTHING HERE IS AUTOMATIC. A human picked these buyers on the deal room,
    read the preview, and pressed Send. There is no scheduler and no automation
    that reaches this endpoint, which is what keeps "do not blast every matched
    buyer" true by construction rather than by policy.

    The response is per buyer and honest about each one: what was sent, what was
    refused and why, in the words a person can act on.
    """
    from app.services import wholesale_disposition as disp

    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    if not payload.buyer_ids:
        raise HTTPException(status_code=400, detail="Select at least one buyer.")
    if payload.channel not in ("email", "sms"):
        raise HTTPException(
            status_code=400,
            detail="channel must be email or sms. A phone call is not something "
                   "this platform places on your behalf.")
    if len(payload.buyer_ids) > 100:
        raise HTTPException(
            status_code=400,
            detail="That is more than 100 buyers in one action. Disposition is "
                   "meant to be a deliberate choice of who sees a deal; split it "
                   "if you really mean to send this widely.")

    rows = svc.queue_buyer_outreach(db, org_id, deal, payload.buyer_ids, user,
                                    payload.asking_price, payload.channel)

    buyers = {b.id: b for b in db.query(WholesaleBuyer).filter(
        WholesaleBuyer.organization_id == org_id,
        WholesaleBuyer.id.in_([r.buyer_id for r in rows] or [""])).all()}

    results = []
    for row in rows:
        buyer = buyers.get(row.buyer_id)
        if buyer is None:
            continue
        outcome = disp.send_to_buyer(db, org_id, deal, buyer, row, user)
        svc.log_event(
            db, org_id,
            "buyer_outreach.sent" if outcome["sent"] else "buyer_outreach.blocked",
            actor_type=ACTOR_USER, actor_user_id=user.id, deal_id=deal.id,
            summary=("Deal sheet sent to %s on %s"
                     % (buyer.company_name or buyer.contact_name, row.channel))
                    if outcome["sent"] else
                    ("Not sent to %s: %s"
                     % (buyer.company_name or buyer.contact_name, outcome["reason"]))[:250],
            after={"buyer_id": buyer.id, "channel": row.channel,
                   "status": outcome["status"], "code": outcome["code"],
                   "provider_message_id": outcome["provider_message_id"]})
        results.append({
            "buyer_id": buyer.id,
            "buyer_name": buyer.company_name or buyer.contact_name,
            "outreach_id": row.id,
            "sent": outcome["sent"],
            "status": outcome["status"],
            "code": outcome["code"],
            "reason": outcome["reason"],
            "provider_message_id": outcome["provider_message_id"],
        })

    db.commit()
    sent = [r for r in results if r["sent"]]
    return {"sent": len(sent), "failed": len(results) - len(sent),
            "channel": payload.channel, "results": results,
            "blocked": [{"buyer_id": r["buyer_id"], "reason": r["reason"]}
                        for r in results if not r["sent"]]}


@router.post("/outreach/{outreach_id}/resend")
def resend_outreach(outreach_id: str, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    """Send this buyer the same deal sheet again, on purpose.

    The ONLY way past the already-sent guard, and it exists because "the first
    one bounced" and "they asked me to resend it" are real, while an accidental
    second send is not something a person should be able to do by double-clicking.
    Every other refusal still applies.
    """
    from app.services import wholesale_disposition as disp

    org_id = svc.write_org_id(db, user)
    row = (db.query(WholesaleBuyerOutreach)
           .filter(WholesaleBuyerOutreach.id == outreach_id,
                   WholesaleBuyerOutreach.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Outreach record not found")
    deal = svc.get_deal(db, org_id, row.deal_id)
    buyer = _get_buyer(db, org_id, row.buyer_id)

    outcome = disp.send_to_buyer(db, org_id, deal, buyer, row, user,
                                 force_resend=True)
    svc.log_event(db, org_id,
                  "buyer_outreach.resent" if outcome["sent"]
                  else "buyer_outreach.blocked",
                  actor_type=ACTOR_USER, actor_user_id=user.id, deal_id=deal.id,
                  summary=("Resent to %s" % (buyer.company_name or buyer.contact_name))
                          if outcome["sent"] else
                          ("Resend refused: %s" % outcome["reason"])[:250],
                  after={"attempts": row.attempts, "status": outcome["status"]})
    db.commit()
    return {**outcome, "attempts": row.attempts}


class OutreachUpdateIn(BaseModel):
    status: str
    response_note: Optional[str] = None
    offer_amount: Optional[float] = None


OUTREACH_STATUSES = ("queued", "sent", "delivered", "failed", "replied",
                     "interested", "passed", "requested_info", "offer_submitted",
                     "accepted")


@router.patch("/outreach/{outreach_id}")
def update_outreach(outreach_id: str, payload: OutreachUpdateIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    """Record what a buyer did — sent, replied, interested, passed, offered."""
    org_id = svc.write_org_id(db, user)
    row = (db.query(WholesaleBuyerOutreach)
           .filter(WholesaleBuyerOutreach.id == outreach_id,
                   WholesaleBuyerOutreach.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Outreach record not found")
    if payload.status not in OUTREACH_STATUSES:
        raise HTTPException(status_code=400,
                            detail="status must be one of: %s" % ", ".join(OUTREACH_STATUSES))
    before = row.status
    row.status = payload.status
    if payload.response_note is not None:
        row.response_note = payload.response_note
    if payload.offer_amount is not None:
        row.offer_amount = analysis.money(payload.offer_amount)
    if payload.status == "sent" and row.sent_at is None:
        row.sent_at = datetime.utcnow()
    if payload.status in ("replied", "interested", "passed", "requested_info",
                          "offer_submitted", "accepted") and row.replied_at is None:
        row.replied_at = datetime.utcnow()

    deal = svc.get_deal(db, org_id, row.deal_id)
    if payload.status in ("interested", "offer_submitted") and \
            deal.stage == "disposition":
        svc._set_stage_unchecked(db, deal, "buyer_identified",
                                 actor_type=ACTOR_USER, actor_user_id=user.id)

    svc.log_event(db, org_id, "buyer_outreach.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=row.deal_id,
                  summary="Buyer response: %s" % payload.status,
                  before={"status": before}, after={"status": row.status})
    db.commit()
    return {"id": row.id, "status": row.status,
            "offer_amount": _num(row.offer_amount)}


# ── Assignment ──────────────────────────────────────────────────────────────

class AssignmentIn(BaseModel):
    buyer_id: str
    buyer_price: float
    assignment_fee: Optional[float] = None
    assignment_status: Optional[str] = None


@router.post("/deals/{deal_id}/assign")
def assign_deal(deal_id: str, payload: AssignmentIn, request: Request,
                db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _guard: User = Depends(require_not_observation)):
    """Select the buyer and set the assignment terms.

    THIS DOES NOT SIGN ANYTHING. It records who the deal is going to and at what
    price; the assignment agreement itself is a document in the deal's document
    workspace, executed by people. Moving the deal into ASSIGNMENT PENDING goes
    through `set_stage`, which refuses without an approved assignment while this
    organization's assignment gate is on.
    """
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)
    buyer = _get_buyer(db, org_id, payload.buyer_id)
    price = analysis.money(payload.buyer_price)
    if price is None:
        raise HTTPException(status_code=400, detail="Enter the buyer's price.")

    deal.assigned_buyer_id = buyer.id
    deal.buyer_price = price
    if payload.assignment_fee is not None:
        deal.assignment_fee = analysis.money(payload.assignment_fee)
    else:
        # The fee the wholesaler earns is the spread, when both ends are known.
        # It is stored as a DERIVED figure and is overwritten by anything the
        # user types; the fee actually COLLECTED is a separate column that only
        # a person ever writes, at close.
        acquisition = (analysis.money(deal.contract_price)
                       or analysis.money(deal.proposed_offer))
        if acquisition is not None:
            deal.assignment_fee = price - acquisition
    deal.assignment_status = payload.assignment_status or "pending"

    svc.log_event(db, org_id, "assignment.set", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal.id,
                  summary="Assigned to %s at %s"
                          % (buyer.company_name or buyer.contact_name, float(price)),
                  after={"buyer_id": buyer.id, "buyer_price": float(price),
                         "assignment_fee": _num(deal.assignment_fee)})
    db.commit()
    from app.routers.wholesale_router import deal_json
    return deal_json(deal, svc.resolve_settings(db, org_id))


# ══════════════════════════════════════════════════════════════════════════
# PHASE 3 — the disposition desk
# ══════════════════════════════════════════════════════════════════════════

class PofIn(BaseModel):
    status: str
    note: Optional[str] = None


@router.post("/outreach/{outreach_id}/pof-status")
def set_pof_status(outreach_id: str, payload: PofIn, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_user),
                   _guard: User = Depends(require_not_observation)):
    """Where this buyer's proof of funds stands on THIS deal.

    `verified` is a PERSON's judgement. No provider in this module verifies a
    bank letter and none is claimed to: a status that says "verified" because
    software looked at a PDF would be the most dangerous lie in the product.
    """
    org_id = svc.write_org_id(db, user)
    row = (db.query(WholesaleBuyerOutreach)
           .filter(WholesaleBuyerOutreach.id == outreach_id,
                   WholesaleBuyerOutreach.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Outreach not found")

    value = (payload.status or "").strip().lower()
    if value not in POF_STATUSES:
        raise HTTPException(status_code=400,
                            detail="Status must be one of: %s." % ", ".join(POF_STATUSES))
    row.pof_status = value

    # The buyer-level flag follows a per-deal verification, because "this buyer
    # has proof of funds on file" is what the buyer list needs to show.
    if value in ("received", "verified"):
        buyer = (db.query(WholesaleBuyer)
                 .filter(WholesaleBuyer.id == row.buyer_id,
                         WholesaleBuyer.organization_id == org_id).first())
        if buyer is not None:
            buyer.proof_of_funds_on_file = True

    svc.log_event(db, org_id, "buyer.pof_status", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=row.deal_id,
                  summary="Proof of funds: %s" % value.replace("_", " "),
                  after={"pof_status": value, "note": payload.note})
    db.commit()
    return {"outreach_id": row.id, "pof_status": row.pof_status}


@router.get("/deals/{deal_id}/buyer-board")
def buyer_board(deal_id: str, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_or_observer)):
    """Every buyer on this deal, side by side, with what each one is offering.

    The disposition decision is a COMPARISON — offer against spread against
    proof of funds against how fast they actually close — and a person cannot
    make it from a list of separate rows they have to hold in their head.
    Nothing here ranks or recommends: it lays the facts out and a human picks.
    """
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    deal = svc.get_deal(db, org_id, deal_id)

    rows = (db.query(WholesaleBuyerOutreach)
            .filter(WholesaleBuyerOutreach.organization_id == org_id,
                    WholesaleBuyerOutreach.deal_id == deal_id)
            .order_by(WholesaleBuyerOutreach.created_at.asc()).all())
    buyers = {b.id: b for b in db.query(WholesaleBuyer).filter(
        WholesaleBuyer.organization_id == org_id,
        WholesaleBuyer.id.in_([r.buyer_id for r in rows] or ["-"])).all()}
    matches = {m.buyer_id: m for m in db.query(WholesaleBuyerMatch).filter(
        WholesaleBuyerMatch.organization_id == org_id,
        WholesaleBuyerMatch.deal_id == deal_id).all()}

    contract = analysis.money(deal.contract_price)
    out = []
    for r in rows:
        b = buyers.get(r.buyer_id)
        offer = analysis.money(r.offer_amount)
        # The spread is THIS buyer's offer against what we are paying — the
        # number the decision actually turns on.
        spread = (offer - contract) if (offer is not None and contract is not None) else None
        m = matches.get(r.buyer_id)
        out.append({
            "outreach_id": r.id,
            "buyer_id": r.buyer_id,
            "buyer_name": getattr(b, "company_name", None) or getattr(b, "contact_name", None),
            "contact_name": getattr(b, "contact_name", None),
            "email": getattr(b, "email", None),
            "phone": getattr(b, "phone", None),
            "do_not_contact": bool(getattr(b, "do_not_contact", False)),
            "match_score": getattr(m, "score", None),
            "channel": r.channel,
            "status": r.status,
            "blocked_reason": r.blocked_reason,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            "delivered_at": r.delivered_at.isoformat() if r.delivered_at else None,
            "replied_at": r.replied_at.isoformat() if r.replied_at else None,
            "response_note": r.response_note,
            "offer_amount": analysis.money(r.offer_amount) and float(offer),
            "spread": float(spread) if spread is not None else None,
            "pof_status": r.pof_status or "not_requested",
            "pof_file_id": r.pof_file_id,
            "typical_close_days": getattr(b, "typical_close_days", None),
            "reliability_rating": getattr(b, "reliability_rating", None),
            "target_close_date": str(r.target_close_date or "") or None,
            # Phase 6. What the investor said when they answered from their own
            # link. `offer_financing` and the respondent block are the rest of
            # the offer — an amount with no funding and nobody's name on it is
            # not something an operator can act on, and the board was showing
            # exactly that.
            "offer_financing": r.offer_financing,
            "respondent_name": r.respondent_name,
            "respondent_email": r.respondent_email,
            "respondent_phone": r.respondent_phone,
            # Track record, from this workspace's own records.
            "past_deals_count": getattr(b, "past_deals_count", 0) or 0,
            "proof_of_funds_on_file": bool(getattr(b, "proof_of_funds_on_file", False)),
            "is_selected": bool(r.is_selected),
        })
    return {"deal_id": deal_id,
            "contract_price": float(contract) if contract is not None else None,
            "selected_buyer_id": deal.assigned_buyer_id,
            "buyers": out}


class SelectBuyerIn(BaseModel):
    outreach_id: str
    note: Optional[str] = None


@router.post("/deals/{deal_id}/select-buyer")
def select_buyer(deal_id: str, payload: SelectBuyerIn, request: Request,
                 db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user),
                 _guard: User = Depends(require_not_observation)):
    """A person chooses the buyer. Nothing here picks the highest number.

    "Do not automatically select based only on highest offer." The highest
    offer from somebody with no proof of funds who has never closed is not the
    best buyer, and a product that decided otherwise would lose deals on behalf
    of the person using it. This records a decision that was already made, with
    the name of whoever made it.

    Selecting does NOT bind anything: the assignment still needs its own
    approval, exactly as before.
    """
    org_id = svc.write_org_id(db, user)
    deal = svc.get_deal(db, org_id, deal_id)

    row = (db.query(WholesaleBuyerOutreach)
           .filter(WholesaleBuyerOutreach.id == payload.outreach_id,
                   WholesaleBuyerOutreach.organization_id == org_id,
                   WholesaleBuyerOutreach.deal_id == deal_id).first())
    if row is None:
        raise HTTPException(status_code=404,
                            detail="That buyer is not on this deal.")

    buyer = (db.query(WholesaleBuyer)
             .filter(WholesaleBuyer.id == row.buyer_id,
                     WholesaleBuyer.organization_id == org_id).first())
    if buyer is not None and buyer.do_not_contact:
        raise HTTPException(
            status_code=409,
            detail="That buyer has opted out. Selecting them would mean "
                   "working a deal with somebody who asked not to be contacted.")

    # One selected buyer per deal.
    (db.query(WholesaleBuyerOutreach)
     .filter(WholesaleBuyerOutreach.organization_id == org_id,
             WholesaleBuyerOutreach.deal_id == deal_id,
             WholesaleBuyerOutreach.id != row.id)
     .update({"is_selected": False}, synchronize_session=False))

    row.is_selected = True
    row.status = "selected"
    deal.assigned_buyer_id = row.buyer_id
    deal.buyer_selected_at = datetime.utcnow()
    deal.buyer_selected_by_id = user.id

    offer = analysis.money(row.offer_amount)
    contract = analysis.money(deal.contract_price)
    if offer is not None:
        deal.buyer_price = offer
        if contract is not None:
            deal.assignment_fee = offer - contract

    svc.log_event(db, org_id, "buyer.selected", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=deal_id,
                  summary="Buyer selected: %s" % (
                      getattr(buyer, "company_name", None) or row.buyer_id),
                  after={"buyer_id": row.buyer_id,
                         "buyer_price": float(offer) if offer else None,
                         "assignment_fee": (float(deal.assignment_fee)
                                            if deal.assignment_fee else None),
                         "note": payload.note})
    db.commit()
    return {"deal_id": deal_id, "selected_buyer_id": row.buyer_id,
            "buyer_price": float(offer) if offer is not None else None,
            "assignment_fee": (float(deal.assignment_fee)
                               if deal.assignment_fee is not None else None)}


class ResponseIn(BaseModel):
    status: Optional[str] = None
    offer_amount: Optional[float] = None
    response_note: Optional[str] = None
    target_close_date: Optional[str] = None
    delivered: Optional[bool] = None


@router.post("/outreach/{outreach_id}/response")
def record_response(outreach_id: str, payload: ResponseIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_tenant_user),
                    _guard: User = Depends(require_not_observation)):
    """What a buyer said back — typed by whoever heard it.

    Most of this arrives by phone. A disposition tool that can only record a
    response which came through its own email integration is a tool that loses
    the response that actually mattered, so this endpoint takes a human's word
    for it and stamps who recorded it.
    """
    org_id = svc.write_org_id(db, user)
    row = (db.query(WholesaleBuyerOutreach)
           .filter(WholesaleBuyerOutreach.id == outreach_id,
                   WholesaleBuyerOutreach.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Outreach not found")

    data = payload.model_dump(exclude_unset=True)

    if data.get("status"):
        value = data["status"].strip().lower()
        if value not in BUYER_RESPONSE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail="Status must be one of: %s." % ", ".join(BUYER_RESPONSE_STATUSES))
        row.status = value
        if value in ("replied", "interested", "needs_info", "offer_submitted",
                     "passed", "rejected") and row.replied_at is None:
            row.replied_at = datetime.utcnow()

    if "offer_amount" in data:
        amount = analysis.money(data["offer_amount"])
        row.offer_amount = amount
        # An amount IS a response. Recording one without moving the status
        # leaves a board that shows an offer nobody has noticed.
        #
        # `failed` and `blocked` are in this list deliberately. A buyer whose
        # deal sheet never went out - because the deployment switch is off, or
        # because they were suppressed - and who then phoned in an offer is the
        # exact case this endpoint exists for. Leaving them on "failed" would
        # hide a live offer behind a delivery problem.
        if amount is not None and row.status in ("queued", "sent", "delivered",
                                                 "opened", "replied", "failed",
                                                 "blocked", "not_contacted"):
            row.status = "offer_submitted"
            row.replied_at = row.replied_at or datetime.utcnow()

    if "response_note" in data:
        row.response_note = data["response_note"]
    if data.get("target_close_date"):
        try:
            row.target_close_date = datetime.strptime(
                data["target_close_date"][:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400,
                                detail="Use YYYY-MM-DD for the target close date.")
    if data.get("delivered") and row.delivered_at is None:
        row.delivered_at = datetime.utcnow()

    svc.log_event(db, org_id, "buyer.response_recorded", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=row.deal_id,
                  summary="Buyer response: %s%s" % (
                      row.status,
                      " — %s" % float(row.offer_amount) if row.offer_amount else ""),
                  after={"status": row.status,
                         "offer_amount": (float(row.offer_amount)
                                          if row.offer_amount is not None else None)})
    db.commit()
    return {"outreach_id": row.id, "status": row.status,
            "offer_amount": (float(row.offer_amount)
                             if row.offer_amount is not None else None)}
