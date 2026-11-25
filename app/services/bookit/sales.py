"""Structured sales (directive s17-18, s42): line items, server-side money in
integer cents, one contract counted once, attribution that reassignment never
erases, and every material correction audited with a reason.

Closed contract value is NOT cash collected and NOT recognized revenue; this
module records contracted amounts only. Pending opportunities never count as
closed. A legacy "Sold? Yes" with no amount is flagged `amount_missing` and
kept out of verified totals.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.bookit_models import (ACQUISITION_SOURCES, DEFAULT_CATEGORIES, SALE_STATUSES, BookitCategory,
                                      BookitSale, BookitSaleLine)
from app.services.bookit.core import BookitError, Who, audit, notify, MANAGEMENT

MAX_CENTS = 100_000_000_00                               # $100M - anything above is a typo


def cents(value, label: str = "Amount") -> int:
    """'$4,500.00' / 4500 / '4500.5' -> 450000 / 450050. Rejects nonsense."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise BookitError("%s isn't a number." % label)
    s = re.sub(r"[,$\s]", "", str(value))
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise BookitError("%s isn't a number." % label)
    if d.is_nan() or d.is_infinite():
        raise BookitError("%s isn't a number." % label)
    c = int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if c < 0:
        raise BookitError("%s can't be negative." % label)
    if c > MAX_CENTS:
        raise BookitError("%s is too large - please check it." % label)
    return c


def categories(db: Session, org: str) -> List[str]:
    rows = (db.query(BookitCategory).filter(BookitCategory.organization_id == org)
            .order_by(BookitCategory.position).all())
    if not rows:
        for i, name in enumerate(DEFAULT_CATEGORIES):
            db.add(BookitCategory(organization_id=org, name=name, position=i))
        db.flush()
        return list(DEFAULT_CATEGORIES)
    return [r.name for r in rows if r.active]


def compute(lines: List[Dict[str, Any]], contract_discount=0, adjustment=0) -> Dict[str, Any]:
    """The authoritative arithmetic. Each line: quantity x unit = gross;
    minus its own discount = net. Contract discount is taken once from the
    total (never also from the lines). Adjustment is signed via 'adjustment'
    (positive adds, e.g. a fee) - stored separately so it's never mistaken
    for a discount."""
    out, gross, line_disc = [], 0, 0
    for i, ln in enumerate(lines or []):
        try:
            qty = int(ln.get("quantity", 1) or 0)
        except (TypeError, ValueError):
            raise BookitError("Line %d: quantity must be a whole number." % (i + 1))
        if qty < 1 or qty > 1000:
            raise BookitError("Line %d: quantity must be between 1 and 1000." % (i + 1))
        unit = cents(ln.get("unit_price"), "Line %d unit price" % (i + 1))
        g = qty * unit
        dsc = cents(ln.get("discount"), "Line %d discount" % (i + 1))
        if dsc > g:
            raise BookitError("Line %d: the discount is more than the line." % (i + 1))
        cat = (ln.get("category") or "").strip()
        if not cat:
            raise BookitError("Line %d: pick a product category." % (i + 1))
        out.append({"position": i, "category": cat[:120], "description": (ln.get("description") or "").strip()[:300],
                    "quantity": qty, "unit_cents": unit, "gross_cents": g, "discount_cents": dsc,
                    "net_cents": g - dsc})
        gross += g
        line_disc += dsc
    cdisc = cents(contract_discount, "Contract discount")
    if line_disc + cdisc > gross:
        raise BookitError("The discounts are more than the contract.")
    try:
        adj = int((Decimal(re.sub(r"[,$\s]", "", str(adjustment or 0))) * 100).quantize(Decimal("1"),
                                                                                        rounding=ROUND_HALF_UP))
    except InvalidOperation:
        raise BookitError("Adjustment isn't a number.")
    net = gross - line_disc - cdisc + adj
    if net < 0:
        raise BookitError("The contract can't come to less than $0.")
    return {"lines": out, "gross_cents": gross, "discount_cents": line_disc + cdisc, "adjustment_cents": adj,
            "net_cents": net}


def _date(v) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        raise BookitError("Sale date must be a date.")


def to_dict(s: BookitSale, lines: Optional[List[BookitSaleLine]] = None) -> Dict[str, Any]:
    d = {k: getattr(s, k) for k in ("id", "lead_id", "appointment_id", "follow_up_id", "acquisition_source",
                                    "booking_source", "campaign_key", "referral_batch_id", "referral_contact_id",
                                    "add_case_id", "referring_advisor", "assigned_advisor", "closing_advisor",
                                    "contract_ref", "gross_cents", "discount_cents", "adjustment_cents",
                                    "net_cents", "currency", "status", "amount_missing", "notes")}
    d["sale_date"] = s.sale_date.isoformat() if s.sale_date else None
    d["recorded_at"] = s.created_at.isoformat() + "Z" if s.created_at else None
    if lines is not None:
        d["lines"] = [{k: getattr(x, k) for k in ("id", "position", "category", "description", "quantity",
                                                  "unit_cents", "gross_cents", "discount_cents", "net_cents")}
                      for x in sorted(lines, key=lambda x: x.position)]
    return d


ATTR_FIELDS = ("lead_id", "appointment_id", "follow_up_id", "acquisition_source", "booking_source", "campaign_key",
               "referral_batch_id", "referral_contact_id", "add_case_id", "referring_advisor", "assigned_advisor")


def record(db: Session, org: str, w: Who, body: Dict[str, Any], attribution: Dict[str, Any]) -> Dict[str, Any]:
    """Create a sale (or return the one this idempotency key already made).
    `attribution` is decided by the server from the appointment/family, not
    taken from the body."""
    key = (body.get("idempotency_key") or "").strip()
    if not key or len(key) > 120:
        raise BookitError("Missing idempotency key.")
    have = db.query(BookitSale).filter(BookitSale.organization_id == org, BookitSale.idempotency_key == key).first()
    if have is not None:
        return {"sale": to_dict(have, db.query(BookitSaleLine).filter_by(sale_id=have.id).all()), "duplicate": True}
    status = body.get("status") or "closed"
    if status not in ("closed", "pending"):
        raise BookitError("A new sale is closed or pending.")
    calc = compute(body.get("lines") or [], body.get("contract_discount"), body.get("adjustment"))
    if status == "closed" and not calc["lines"]:
        raise BookitError("A closed sale needs at least one product or service with its amount.")
    if attribution.get("acquisition_source") not in ACQUISITION_SOURCES:
        attribution["acquisition_source"] = "unknown"
    s = BookitSale(organization_id=org, idempotency_key=key, status=status,
                   closing_advisor=(body.get("closing_advisor") or w.name or w.user_name),
                   contract_ref=(body.get("contract_ref") or "").strip()[:120] or None,
                   sale_date=_date(body.get("sale_date")) or (date.today() if status == "closed" else None),
                   notes=(body.get("notes") or "").strip()[:4000] or None,
                   gross_cents=calc["gross_cents"], discount_cents=calc["discount_cents"],
                   adjustment_cents=calc["adjustment_cents"], net_cents=calc["net_cents"],
                   created_by=w.user_id, updated_by=w.user_id,
                   **{k: attribution.get(k) for k in ATTR_FIELDS})
    if not w.manager:
        s.closing_advisor = w.name                       # an advisor records their own sales
    db.add(s)
    db.flush()
    for ln in calc["lines"]:
        db.add(BookitSaleLine(sale_id=s.id, **ln))
    audit(db, org, w, "sale", s.id, "create", "net_cents", None, s.net_cents)
    if status == "closed":
        notify(db, org, MANAGEMENT, "sale_closed", "Closed sale recorded",
               body="%s recorded a closed contract." % (s.closing_advisor or "An advisor"),
               ref_type="sale", ref_id=s.id)
    db.flush()
    return {"sale": to_dict(s, db.query(BookitSaleLine).filter_by(sale_id=s.id).all()), "duplicate": False}


MATERIAL = ("lines", "contract_discount", "adjustment", "status", "contract_ref", "acquisition_source",
            "closing_advisor", "referring_advisor", "sale_date")


def correct(db: Session, org: str, w: Who, sale_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Change a sale. Advisors may finish their own PENDING sale (add the
    amounts, close it). Anything that changes a closed sale's money,
    attribution or status is management's, and needs a reason."""
    s = db.query(BookitSale).filter(BookitSale.organization_id == org, BookitSale.id == sale_id).first()
    if s is None or not w.sees(s.closing_advisor, s.assigned_advisor):
        raise BookitError("Not found.", 404)
    material = [k for k in MATERIAL if k in body]
    if s.status != "pending" and material and not w.manager:
        raise BookitError("Only management can change a closed sale.", 403)
    if not w.manager and any(k in body for k in ("acquisition_source", "referring_advisor", "closing_advisor")):
        raise BookitError("Only management can change attribution.", 403)
    reason = (body.get("reason") or "").strip()
    if s.status != "pending" and material and not reason:
        raise BookitError("Please give a reason for the correction.")
    if "status" in body and body["status"] not in SALE_STATUSES:
        raise BookitError("Status must be closed, pending or void.")
    old = to_dict(s)
    if "lines" in body or "contract_discount" in body or "adjustment" in body:
        lines = body.get("lines")
        if lines is None:
            lines = [{"category": x.category, "description": x.description, "quantity": x.quantity,
                      "unit_price": Decimal(x.unit_cents) / 100, "discount": Decimal(x.discount_cents) / 100}
                     for x in db.query(BookitSaleLine).filter_by(sale_id=s.id).order_by(BookitSaleLine.position)]
        line_disc = sum(cents(l.get("discount")) for l in lines)
        cdisc = body.get("contract_discount")
        if cdisc is None:
            cdisc = Decimal(max(s.discount_cents - line_disc, 0)) / 100
        calc = compute(lines, cdisc, body.get("adjustment", Decimal(s.adjustment_cents) / 100))
        db.query(BookitSaleLine).filter_by(sale_id=s.id).delete()
        for ln in calc["lines"]:
            db.add(BookitSaleLine(sale_id=s.id, **ln))
        s.gross_cents, s.discount_cents = calc["gross_cents"], calc["discount_cents"]
        s.adjustment_cents, s.net_cents = calc["adjustment_cents"], calc["net_cents"]
        s.amount_missing = False
    for k in ("status", "contract_ref", "acquisition_source", "closing_advisor", "referring_advisor", "notes"):
        if k in body:
            setattr(s, k, (body[k] or None) if k != "status" else body[k])
    if "sale_date" in body:
        s.sale_date = _date(body["sale_date"])
    if s.status == "closed" and s.net_cents <= 0 and not s.amount_missing:
        raise BookitError("A closed sale needs its amounts.")
    s.updated_by = w.user_id
    new = to_dict(s)
    for k in new:
        if old.get(k) != new.get(k) and k not in ("recorded_at",):
            audit(db, org, w, "sale", s.id, "void" if k == "status" and new[k] == "void" else "update",
                  k, old.get(k), new.get(k), reason)
    if material and s.status != "pending":
        notify(db, org, MANAGEMENT, "sale_corrected", "Sale corrected", body=reason[:200], ref_type="sale",
               ref_id=s.id, dedupe="sale_corrected:%s:%s" % (s.id, datetime.utcnow().isoformat()))
    db.flush()
    return {"sale": to_dict(s, db.query(BookitSaleLine).filter_by(sale_id=s.id).all())}


def visible(db: Session, org: str, w: Who, status: Optional[str] = None):
    q = db.query(BookitSale).filter(BookitSale.organization_id == org)
    if status:
        q = q.filter(BookitSale.status == status)
    rows = q.order_by(BookitSale.created_at.desc()).all()
    return [s for s in rows if w.sees(s.closing_advisor, s.assigned_advisor)]
