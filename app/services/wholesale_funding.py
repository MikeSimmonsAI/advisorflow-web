"""Funding / capital partners: who says they fund what, and what actually happened.

Need Funding -> Find Appropriate Funding Options -> Prepare Deal Packet -> Route/Track.

WHAT THIS IS NOT. EvoSys is not a lender and does not approve, price, commit or
guarantee financing. A partner is a third party; every figure here is either
what the partner STATED (labelled as such) or what they DID on a submitted
deal (recorded from their answer). Nothing is inferred into a promise.

REUSE, NOT A SECOND CRM. The partner as a person or company lives in the
contact database: `create_partner` captures them through Universal Intake with
the built-in "partner" classification (record class PARTNER, never a Lead), so
dedupe, source attribution and rollback are the canonical ones. This module
owns only the lending facts (`WholesaleFundingPartner`) and the per-deal
submissions (`WholesaleFundingSubmission`), both organization-scoped.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.wholesale_models import (ACTOR_USER, FUNDING_PRODUCTS,
                                         FUNDING_SUBMISSION_STATUSES, WholesaleFundingPartner,
                                         WholesaleFundingSubmission)
from app.services import wholesale_service as svc
from app.utils.time_fmt import iso_utc  # S19: explicit-UTC timestamps

PRODUCT_LABELS = {
    "dscr": "DSCR rental loan", "fix_flip": "Fix & flip", "hard_money": "Hard money",
    "bridge": "Bridge", "ground_up": "Ground-up construction",
    "land_development": "Land / development", "private_capital": "Private capital",
    "transactional": "Transactional funding",
}
DISCLAIMER = ("Funding partners are independent third parties. EvoSys does not lend, approve, "
              "price or guarantee financing; criteria shown are what each partner stated.")

LIST_FIELDS = ("products", "states", "markets", "property_types")
DECIDED = ("approved", "declined", "funded", "withdrawn")


def _jl(v) -> List[str]:
    if not v:
        return []
    try:
        out = json.loads(v) if isinstance(v, str) else v
    except (TypeError, ValueError):
        return []
    return [str(x).strip() for x in out if str(x).strip()] if isinstance(out, list) else []


def _num(v) -> Optional[float]:
    return float(v) if v is not None else None


def _clean_lists(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    for k in LIST_FIELDS:
        if k in out:
            vals = [str(x).strip() for x in (out[k] or []) if str(x).strip()]
            if k == "products":
                bad = [x for x in vals if x not in FUNDING_PRODUCTS]
                if bad:
                    raise HTTPException(422, "Unknown funding product(s): %s. Known: %s"
                                        % (", ".join(bad), ", ".join(FUNDING_PRODUCTS)))
            if k == "states":
                vals = [x.upper() for x in vals]
            out[k] = json.dumps(vals)
    for lo, hi in (("min_loan", "max_loan"),):
        if out.get(lo) is not None and out.get(hi) is not None and float(out[lo]) > float(out[hi]):
            raise HTTPException(422, "The minimum loan is above the maximum.")
    return out


def get_partner(db: Session, org_id: str, partner_id: str) -> WholesaleFundingPartner:
    p = (db.query(WholesaleFundingPartner)
         .filter(WholesaleFundingPartner.id == partner_id,
                 WholesaleFundingPartner.organization_id == org_id).first())
    if p is None:
        raise HTTPException(404, "Funding partner not found")
    return p


def create_partner(db: Session, org, user, data: Dict[str, Any]) -> WholesaleFundingPartner:
    """Capture the partner as a contact (Universal Intake, 'partner' - no Lead),
    then attach the lending facts. COMMITS (intake commits as it goes)."""
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "A funding partner needs a name.")
    if not (data.get("email") or data.get("phone")):
        raise HTTPException(422, "A funding partner needs an email or a phone number.")
    contact_id = None
    try:
        from app.services.intake import capture as CAP
        person = (data.get("contact_person") or "").strip()
        first, _, last = person.partition(" ")
        res = CAP.capture_one(
            db, org, {"first_name": first or None, "last_name": last or None,
                      "company": name, "email": data.get("email"), "phone": data.get("phone"),
                      "notes": "Funding partner"},
            source="manual", source_detail="Wholesale funding partner", list_name="Funding partners",
            classification="partner", actor_label="Funding partner", external=False,
            explicit=True, user=user)
        contact_id = res.contact_id
    except Exception:  # noqa: BLE001 - the partner is still recorded; the event says why
        db.rollback()
        svc.log_event(db, org.id, "funding.partner_intake_error", actor_type=ACTOR_USER,
                      actor_user_id=getattr(user, "id", None),
                      summary="Funding partner kept without a contact-database link (intake error)")
    fields = {k: v for k, v in data.items() if hasattr(WholesaleFundingPartner, k)
              and k not in ("id", "organization_id", "org_contact_id", "verified", "verified_at",
                            "verified_by_id", "created_by_id")}
    p = WholesaleFundingPartner(organization_id=org.id, org_contact_id=contact_id,
                                created_by_id=getattr(user, "id", None), **_clean_lists(fields))
    if data.get("verified"):
        _mark_verified(p, user)
    db.add(p)
    db.flush()
    svc.log_event(db, org.id, "funding.partner_created", actor_type=ACTOR_USER,
                  actor_user_id=getattr(user, "id", None), summary="Funding partner added: %s" % p.name,
                  after={"products": _jl(p.products), "states": _jl(p.states),
                         "org_contact_id": contact_id})
    return p


def _mark_verified(p, user):
    p.verified = True
    p.verified_at = datetime.utcnow()
    p.verified_by_id = getattr(user, "id", None)


def update_partner(db: Session, org_id: str, p: WholesaleFundingPartner, user,
                   data: Dict[str, Any]) -> WholesaleFundingPartner:
    before = partner_json(p)
    if "verified" in data:
        if data.pop("verified"):
            _mark_verified(p, user)
        else:
            p.verified, p.verified_at, p.verified_by_id = False, None, None
    for k, v in _clean_lists({k: v for k, v in data.items() if hasattr(WholesaleFundingPartner, k)
                              and k not in ("id", "organization_id", "org_contact_id",
                                            "verified_at", "verified_by_id", "created_by_id")}).items():
        setattr(p, k, v)
    svc.log_event(db, org_id, "funding.partner_updated", actor_type=ACTOR_USER,
                  actor_user_id=getattr(user, "id", None), summary="Funding partner updated: %s" % p.name,
                  before={k: before.get(k) for k in data}, after={k: partner_json(p).get(k) for k in data})
    return p


def partner_stats(db: Session, org_id: str, partner_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """What each partner actually DID with deals sent to them here."""
    out = {pid: {"deals_submitted": 0, "approvals": 0, "funded": 0, "declines": 0, "open": 0,
                 "avg_response_hours": None, "last_submitted_at": None} for pid in partner_ids}
    if not partner_ids:
        return out
    waits: Dict[str, List[float]] = {}
    for s in (db.query(WholesaleFundingSubmission)
              .filter(WholesaleFundingSubmission.organization_id == org_id,
                      WholesaleFundingSubmission.partner_id.in_(partner_ids)).all()):
        o = out[s.partner_id]
        o["deals_submitted"] += 1
        if s.status in ("approved", "funded"):
            o["approvals"] += 1
        if s.status == "funded":
            o["funded"] += 1
        if s.status == "declined":
            o["declines"] += 1
        if s.status not in DECIDED:
            o["open"] += 1
        if s.submitted_at and (o["last_submitted_at"] is None or s.submitted_at > o["last_submitted_at"]):
            o["last_submitted_at"] = s.submitted_at
        if s.submitted_at and s.responded_at and s.responded_at >= s.submitted_at:
            waits.setdefault(s.partner_id, []).append((s.responded_at - s.submitted_at).total_seconds() / 3600)
    for pid, w in waits.items():
        out[pid]["avg_response_hours"] = round(sum(w) / len(w), 1)
    for o in out.values():
        if o["last_submitted_at"] is not None:
            o["last_submitted_at"] = o["last_submitted_at"].isoformat()
    return out


def partner_json(p: WholesaleFundingPartner, stats: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "id": p.id, "name": p.name, "contact_person": p.contact_person, "email": p.email,
        "phone": p.phone, "org_contact_id": p.org_contact_id,
        "products": _jl(p.products), "product_labels": [PRODUCT_LABELS.get(x, x) for x in _jl(p.products)],
        "states": _jl(p.states), "markets": _jl(p.markets), "property_types": _jl(p.property_types),
        "min_loan": _num(p.min_loan), "max_loan": _num(p.max_loan),
        "max_ltv_pct": _num(p.max_ltv_pct), "max_ltc_pct": _num(p.max_ltc_pct),
        "min_credit_score": p.min_credit_score, "typical_close_days": p.typical_close_days,
        "referral_relationship": p.referral_relationship,
        "verified": bool(p.verified), "verified_at": iso_utc(p.verified_at),
        "criteria_basis": "verified by a person" if p.verified else "stated by the partner, not verified",
        "last_contact_at": iso_utc(p.last_contact_at),
        "notes": p.notes, "is_active": bool(p.is_active), "is_test": bool(p.is_test),
        "track_record": stats,
    }


def match_partners(partners: List[WholesaleFundingPartner], *, product: Optional[str] = None,
                   state: Optional[str] = None, amount: Optional[float] = None,
                   property_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Which partners fit a funding need, ON THEIR STATED CRITERIA, with every
    reason shown. A criterion the partner never stated is 'not stated' - neither
    a fit nor a miss. A stated mismatch excludes, with its reason."""
    out = []
    for p in partners:
        if not p.is_active:
            continue
        reasons, misses = [], []

        def check(label, stated, fits):
            if not stated:
                reasons.append({"criterion": label, "fit": None, "detail": "not stated"})
            elif fits:
                reasons.append({"criterion": label, "fit": True, "detail": "fits"})
            else:
                reasons.append({"criterion": label, "fit": False, "detail": "outside stated criteria"})
                misses.append(label)

        prods, states, ptypes = _jl(p.products), _jl(p.states), _jl(p.property_types)
        if product:
            check("product", prods, product in prods)
        if state:
            check("state", states, state.upper() in states)
        if property_type:
            check("property type", ptypes, property_type in ptypes)
        if amount is not None:
            lo, hi = _num(p.min_loan), _num(p.max_loan)
            check("loan amount", lo is not None or hi is not None,
                  (lo is None or amount >= lo) and (hi is None or amount <= hi))
        fits = sum(1 for r in reasons if r["fit"] is True)
        out.append({"partner": p, "eligible": not misses, "excluded_because": misses,
                    "fits": fits, "reasons": reasons})
    out.sort(key=lambda r: (r["eligible"], bool(r["partner"].verified), r["fits"]), reverse=True)
    return out


def submit(db: Session, org_id: str, deal_id: str, partner: WholesaleFundingPartner, user,
           *, product: Optional[str], amount: Optional[float], notes: Optional[str]) -> WholesaleFundingSubmission:
    """Record that this deal was sent to this partner. Sending the packet itself
    stays a human act through the platform's existing communications."""
    deal = svc.get_deal(db, org_id, deal_id)
    if product and product not in FUNDING_PRODUCTS:
        raise HTTPException(422, "Unknown funding product.")
    now = datetime.utcnow()
    s = WholesaleFundingSubmission(organization_id=org_id, deal_id=deal.id, partner_id=partner.id,
                                   product=product, amount_requested=Decimal(str(amount)) if amount is not None else None,
                                   status="submitted", submitted_at=now, notes=notes,
                                   created_by_id=getattr(user, "id", None))
    partner.last_contact_at = now
    db.add(s)
    db.flush()
    svc.log_event(db, org_id, "funding.submitted", actor_type=ACTOR_USER,
                  actor_user_id=getattr(user, "id", None), deal_id=deal.id,
                  summary="Deal submitted to funding partner %s" % partner.name,
                  after={"product": product, "amount_requested": amount, "submission_id": s.id})
    return s


def record_response(db: Session, org_id: str, s: WholesaleFundingSubmission, user,
                    *, status: str, approved_amount: Optional[float] = None,
                    decline_reason: Optional[str] = None, notes: Optional[str] = None):
    if status not in FUNDING_SUBMISSION_STATUSES:
        raise HTTPException(422, "Unknown status. Known: %s" % ", ".join(FUNDING_SUBMISSION_STATUSES))
    if status == "funded" and s.status not in ("approved", "term_sheet", "funded"):
        raise HTTPException(409, "A submission is marked funded only after it was approved.")
    before = s.status
    now = datetime.utcnow()
    s.status = status
    if s.responded_at is None and status not in ("submitted", "no_response"):
        s.responded_at = now
    if status in DECIDED:
        s.decided_at = s.decided_at or now
    if status == "funded":
        s.funded_at = now
    if approved_amount is not None:
        s.approved_amount = Decimal(str(approved_amount))
    if decline_reason is not None:
        s.decline_reason = decline_reason[:255]
    if notes:
        s.notes = ((s.notes + "\n") if s.notes else "") + notes
    svc.log_event(db, org_id, "funding.response", actor_type=ACTOR_USER,
                  actor_user_id=getattr(user, "id", None), deal_id=s.deal_id,
                  summary="Funding partner answer recorded: %s -> %s" % (before, status),
                  before={"status": before}, after={"status": status, "approved_amount": approved_amount})
    return s


def submission_json(s: WholesaleFundingSubmission, partner_name: Optional[str] = None) -> Dict[str, Any]:
    iso = lambda d: iso_utc(d)  # noqa: E731
    return {"id": s.id, "deal_id": s.deal_id, "partner_id": s.partner_id, "partner_name": partner_name,
            "product": s.product, "product_label": PRODUCT_LABELS.get(s.product or "", s.product),
            "amount_requested": _num(s.amount_requested), "status": s.status,
            "submitted_at": iso(s.submitted_at), "responded_at": iso(s.responded_at),
            "decided_at": iso(s.decided_at), "funded_at": iso(s.funded_at),
            "approved_amount": _num(s.approved_amount), "decline_reason": s.decline_reason,
            "notes": s.notes}
