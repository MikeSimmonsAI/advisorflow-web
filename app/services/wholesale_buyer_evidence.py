"""Buyer evidence and identity - recorded purchases, legal entities, and the
review of investors who MIGHT be the same group.

RULES (owner, #413)
  * One investor stays one buyer record however many lists they appear in.
  * Exact legal-name match (normalized) = the same buyer: evidence attaches to
    the existing record, nothing is duplicated.
  * Similar names (same name once LLC / INC / II ... are dropped) or a shared
    mailing address are NOT merged. They become a NEEDS REVIEW item; a person
    decides. A registered agent or an address is not an identity.
  * Linking another LLC to an investor never deletes a row: the second record
    is marked as linked and can be unlinked.
  * A recorded deed is a RECORDED TITLE TRANSFER - Texas deeds state no price,
    so it is never a verified or cash purchase. A deal the buyer closed with us
    is a VERIFIED PURCHASE. What a file or the buyer claims is CLAIMED.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.wholesale_buyer_engine_models import (CLAIMED_PURCHASE, ENTITY_CONFIRMED, ENTITY_REJECTED,
                                                      ENTITY_REVIEW, EVIDENCE_CLASSES, RECORDED_TITLE_TRANSFER,
                                                      REPORTED_INVESTOR_PURCHASE, VERIFIED_CASH_PURCHASE,
                                                      VERIFIED_PURCHASE, WholesaleBuyerAcquisition,
                                                      WholesaleBuyerEntity)
from app.models.wholesale_models import WholesaleBuyBox, WholesaleBuyer, WholesaleDeal, WholesaleProperty
from app.services import wholesale_buyer_finder as finder

SOURCE_FILE = {"dallas": "dcad", "tarrant": "tad"}
_SUFFIX = re.compile(r"\b(LLC|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LP|LTD|LLP|PLLC|PC|SERIES|"
                     r"I|II|III|IV|V|VI|1|2|3|4|5|THE)\b")


def norm(name: str) -> str:
    return finder.norm_name(name or "")


def stem(name: str) -> str:
    """The name with legal suffixes and series numbers removed. Two different
    names with the same stem MIGHT be one investor - a person decides."""
    return re.sub(r"\s+", " ", _SUFFIX.sub(" ", norm(name).replace("&", " AND "))).strip()


def _mail_key(street: Optional[str], zip5: Optional[str]) -> Optional[str]:
    st = re.sub(r"[^A-Z0-9 ]", "", (street or "").upper()).strip()
    z = (zip5 or "").strip()[:5]
    return "%s|%s" % (st, z) if st and z else None


def _buyer_names(b) -> List[str]:
    return [n for n in (b.company_name, b.contact_name) if n]


class Index:
    """Every name (buyer names + linked entity names) and mailing address in one
    organization, for exact matching and for review suggestions."""

    def __init__(self, db, org_id: str, is_test: bool = False):
        self.by_norm: Dict[str, str] = {}
        self.by_stem: Dict[str, List[str]] = {}
        self.by_mail: Dict[str, List[str]] = {}
        self.names: Dict[str, str] = {}
        for b in db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id).all():
            if bool(b.is_test) != bool(is_test):
                continue
            primary = b.linked_to_buyer_id or b.id
            self.names[b.id] = b.company_name or b.contact_name or ""
            for n in _buyer_names(b):
                self.add(n, primary)
            mk = _mail_from_notes(b.notes)
            if mk:
                self.by_mail.setdefault(mk, []).append(primary)
        for e in db.query(WholesaleBuyerEntity).filter(WholesaleBuyerEntity.organization_id == org_id,
                                                       WholesaleBuyerEntity.status == ENTITY_CONFIRMED).all():
            self.add(e.legal_name, e.buyer_id)

    def add(self, name: str, buyer_id: str):
        n = norm(name)
        if not n:
            return
        self.by_norm.setdefault(n, buyer_id)
        self.by_stem.setdefault(stem(name), [])
        if buyer_id not in self.by_stem[stem(name)]:
            self.by_stem[stem(name)].append(buyer_id)

    def exact(self, name: str) -> Optional[str]:
        return self.by_norm.get(norm(name))

    def similar(self, name: str, mail: Optional[str]) -> List[Dict[str, str]]:
        out, seen = [], set()
        for bid in self.by_stem.get(stem(name), []):
            if bid not in seen:
                seen.add(bid)
                out.append({"buyer_id": bid, "reason": "Same name once LLC / INC / series numbers are dropped "
                                                         "(%s vs %s)" % (name, self.names.get(bid, "?"))})
        for bid in (self.by_mail.get(mail) or []) if mail else []:
            if bid not in seen:
                seen.add(bid)
                out.append({"buyer_id": bid, "reason": "Shares a mailing address with %s - an address alone "
                                                         "does not make them the same investor" % self.names.get(bid, "?")})
        return out


_MAIL_RE = re.compile(r"Mailing address \(county record\): (.+)")


def _mail_from_notes(notes: Optional[str]) -> Optional[str]:
    m = _MAIL_RE.search(notes or "")
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split(",")]
    zip5 = re.findall(r"\b(\d{5})\b", parts[-1]) if parts else []
    return _mail_key(parts[0] if parts else None, zip5[-1] if zip5 else None)


# ── recorded acquisitions ──────────────────────────────────────────────────

def attach_acquisitions(db, org_id: str, buyer: WholesaleBuyer, county: str, owner: Dict[str, Any],
                        generated_at: Optional[str]) -> int:
    """Upsert one RECORDED_TITLE_TRANSFER per recent deed in the county scan.
    Idempotent: the same parcel and deed date is never recorded twice."""
    src = SOURCE_FILE.get(county, county)
    have = {(a.reference, a.transfer_date) for a in db.query(WholesaleBuyerAcquisition).filter(
        WholesaleBuyerAcquisition.organization_id == org_id, WholesaleBuyerAcquisition.buyer_id == buyer.id,
        WholesaleBuyerAcquisition.source == src).all()}
    added = 0
    for d in owner.get("acquisitions") or []:
        try:
            when = datetime.strptime(d["deed_date"], "%Y-%m-%d").date()
        except (KeyError, ValueError, TypeError):
            continue
        key = (d.get("account"), when)
        if key in have:
            continue
        have.add(key)
        db.add(WholesaleBuyerAcquisition(
            organization_id=org_id, buyer_id=buyer.id, evidence_class=RECORDED_TITLE_TRANSFER, source=src,
            reference=d.get("account"), grantee_name=owner.get("name"),
            property_kind=finder.KIND_SEGMENT.get(d.get("kind"), d.get("kind")),
            street=d.get("street"), city=d.get("city"), county=county.title(), state="TX", zip_code=d.get("zip"),
            transfer_date=when, price=None, confidence="high", source_file_date=generated_at,
            is_test=bool(buyer.is_test),
            note="Deed recorded to %s in the %s County appraisal file. Texas records state no sale price, so "
                 "this shows they took title - not the price or whether they paid cash." % (owner.get("name"),
                                                                                           county.title())))
        added += 1
    if added:
        db.flush()
    return added


def ensure_entity(db, org_id: str, buyer_id: str, name: str, *, status: str = ENTITY_CONFIRMED,
                  source: str = "county_roll", reason: Optional[str] = None,
                  linked_buyer_id: Optional[str] = None, user=None) -> WholesaleBuyerEntity:
    n = norm(name)
    e = db.query(WholesaleBuyerEntity).filter(WholesaleBuyerEntity.organization_id == org_id,
                                              WholesaleBuyerEntity.buyer_id == buyer_id,
                                              WholesaleBuyerEntity.norm_name == n).first()
    if e is None:
        e = WholesaleBuyerEntity(organization_id=org_id, buyer_id=buyer_id, legal_name=name.strip(), norm_name=n,
                                 status=status, source=source, reason=reason, linked_buyer_id=linked_buyer_id)
        db.add(e)
        db.flush()
    elif e.status == ENTITY_REJECTED and status == ENTITY_REVIEW:
        return e                                   # a person already said no - never re-ask
    else:
        if status == ENTITY_CONFIRMED:
            e.status = status
        e.linked_buyer_id = linked_buyer_id or e.linked_buyer_id
        e.reason = reason or e.reason
    if user is not None and status != ENTITY_REVIEW:
        e.decided_by_id, e.decided_at = user.id, datetime.utcnow()
    return e


def import_owner(db, org_id: str, county: str, owner: Dict[str, Any], *, generated_at: Optional[str],
                 recent_months: int, index: Index, user=None, is_test: bool = False) -> Dict[str, Any]:
    """Add one county investor - or attach their evidence to the buyer they already are."""
    existing = index.exact(owner["name"])
    if existing:
        b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == existing,
                                            WholesaleBuyer.organization_id == org_id).first()
        added = attach_acquisitions(db, org_id, b, county, owner, generated_at) if b else 0
        if b is not None:
            ensure_entity(db, org_id, b.id, owner["name"], source="county_roll:%s" % county)
        return {"result": "attached", "buyer_id": existing, "deeds_added": added}
    m = owner.get("mailing") or {}
    mailing = ", ".join(x for x in (m.get("street"), m.get("city"),
                                    " ".join(y for y in (m.get("state"), m.get("zip")) if y)) if x)
    kinds = [("house", "house"), ("multifamily", "multifamily"), ("lot", "lot"), ("land", "land parcel"),
             ("commercial", "commercial parcel"), ("industrial", "industrial parcel")]
    held = {"house": owner.get("houses", 0), "multifamily": owner.get("multifamily", 0), "lot": owner.get("lots", 0),
            "land": owner.get("land", 0), "commercial": owner.get("commercial", 0),
            "industrial": owner.get("industrial", 0)}
    what = ", ".join("%d %s%s" % (held[k], word, "" if held[k] == 1 or k == "multifamily" else "s")
                     for k, word in kinds if held.get(k))
    detail = "%s County records: owns %s; %d deed(s) to them in the last %s months" % (
        county.title(), what or "%d parcels" % owner.get("properties", 0), owner.get("bought_recently", 0),
        recent_months)
    notes = "\n".join(x for x in (
        "Mailing address (county record): %s" % mailing if mailing else None,
        "Last deed to them: %s" % owner["last_deed"] if owner.get("last_deed") else None,
        "No phone or email yet - look one up or mail them before sending deals.") if x)
    company = owner.get("kind") == "company"
    b = WholesaleBuyer(organization_id=org_id, entity_type="company" if company else "person",
                       company_name=owner["name"] if company else None,
                       contact_name=owner["name"] if not company else None,
                       preferred_channel="phone", source="county_roll:%s" % county, source_detail=detail[:250],
                       notes=notes, past_deals_count=0, is_test=is_test)
    db.add(b)
    db.flush()
    types = [t for t, n in (("single_family", held["house"]), ("multifamily", held["multifamily"]),
                            ("land", held["lot"] + held["land"]), ("commercial", held["commercial"]),
                            ("industrial", held["industrial"])) if n]
    import json as _json
    db.add(WholesaleBuyBox(organization_id=org_id, buyer_id=b.id, label="From county records",
                           source="county_records", counties=_json.dumps([county.title()]),
                           zips=_json.dumps(owner["zips"]) if owner.get("zips") else None,
                           property_types=_json.dumps(types) if types else None,
                           notes="Inferred from what they own in the county file - they have not stated it."))
    ensure_entity(db, org_id, b.id, owner["name"], source="county_roll:%s" % county)
    added = attach_acquisitions(db, org_id, b, county, owner, generated_at)
    mk = _mail_key(m.get("street"), m.get("zip"))
    similar = [s for s in index.similar(owner["name"], mk) if s["buyer_id"] != b.id]
    for s in similar:
        other = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == s["buyer_id"]).first()
        if other is None:
            continue
        ensure_entity(db, org_id, other.id, owner["name"], status=ENTITY_REVIEW, source="identity_check",
                      reason=s["reason"], linked_buyer_id=b.id)
    if similar:
        b.identity_status = "needs_review"
    index.add(owner["name"], b.id)
    if mk:
        index.by_mail.setdefault(mk, []).append(b.id)
    index.names[b.id] = owner["name"]
    return {"result": "created", "buyer_id": b.id, "deeds_added": added, "needs_review": len(similar)}


def refresh_county(db, org_id: str, county: str, res: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the recorded deeds from the latest scan to every buyer this
    workspace already has from that county (by exact legal name only)."""
    if not res or res.get("format", 1) < 2:
        return {"attached_buyers": 0, "deeds_added": 0, "needs_new_scan": True}
    by_norm = {norm(o["name"]): o for o in res.get("buyers") or []}
    idx = Index(db, org_id)
    touched, added = 0, 0
    for n, bid in list(idx.by_norm.items()):
        o = by_norm.get(n)
        if not o:
            continue
        b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == bid, WholesaleBuyer.organization_id == org_id).first()
        if b is None:
            continue
        k = attach_acquisitions(db, org_id, b, county, o, res.get("generated_at"))
        ensure_entity(db, org_id, b.id, o["name"], source="county_roll:%s" % county)
        touched += 1
        added += k
    return {"attached_buyers": touched, "deeds_added": added, "needs_new_scan": False,
            "scan_generated_at": res.get("generated_at"), "scan_owners": len(by_norm)}


def sync_closed_deals(db, org_id: str) -> int:
    """Deals a buyer closed WITH US are verified purchases (our own closing).
    Idempotent. Cash is not assumed."""
    added = 0
    deals = db.query(WholesaleDeal).filter(WholesaleDeal.organization_id == org_id,
                                           WholesaleDeal.assigned_buyer_id.isnot(None),
                                           WholesaleDeal.closed_at.isnot(None)).all()
    for d in deals:
        ref = "deal:%s" % d.id
        if db.query(WholesaleBuyerAcquisition).filter(WholesaleBuyerAcquisition.organization_id == org_id,
                                                      WholesaleBuyerAcquisition.buyer_id == d.assigned_buyer_id,
                                                      WholesaleBuyerAcquisition.reference == ref).first():
            continue
        p = db.query(WholesaleProperty).filter(WholesaleProperty.id == d.property_id).first()
        price = getattr(d, "buyer_price", None)
        db.add(WholesaleBuyerAcquisition(
            organization_id=org_id, buyer_id=d.assigned_buyer_id, evidence_class=VERIFIED_PURCHASE,
            source="deal", reference=ref, property_kind=getattr(p, "property_category", None) or getattr(p, "property_type", None),
            street=getattr(p, "street_address", None), city=getattr(p, "city", None), county=getattr(p, "county", None),
            zip_code=getattr(p, "zip_code", None), transfer_date=d.closed_at.date() if d.closed_at else None,
            price=int(price) if price is not None else None, confidence="high", is_test=bool(d.is_test),
            note="Closed with you on this platform's deal %s. Funding type not recorded." % d.id))
        added += 1
    if added:
        db.flush()
    return added


def add_evidence(db, org_id: str, buyer: WholesaleBuyer, data: Dict[str, Any], user) -> WholesaleBuyerAcquisition:
    """A person records a purchase they know of. The class must match the
    proof: a verified (cash) purchase needs a document reference."""
    cls = (data.get("evidence_class") or REPORTED_INVESTOR_PURCHASE).upper()
    if cls not in EVIDENCE_CLASSES or cls == RECORDED_TITLE_TRANSFER and not data.get("reference"):
        raise HTTPException(status_code=400, detail="Choose an evidence type; a recorded transfer needs the "
                                                    "record or document number.")
    if cls in (VERIFIED_PURCHASE, VERIFIED_CASH_PURCHASE) and not (data.get("reference") or "").strip():
        raise HTTPException(status_code=400, detail="A verified purchase needs the closing document or record "
                                                    "it was verified from (document number, title file).")
    when = None
    if data.get("date"):
        try:
            when = datetime.strptime(str(data["date"])[:10], "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD.")
    price = data.get("price")
    if price is not None and cls in (RECORDED_TITLE_TRANSFER, CLAIMED_PURCHASE) and not data.get("price_source"):
        price = None                                # no invented prices on deeds or claims
    row = WholesaleBuyerAcquisition(
        organization_id=org_id, buyer_id=buyer.id, evidence_class=cls,
        source="staff:%s" % (getattr(user, "id", "") or ""), reference=(data.get("reference") or None),
        grantee_name=data.get("grantee") or None, property_kind=data.get("kind") or None,
        street=data.get("street") or None, city=data.get("city") or None, county=data.get("county") or None,
        state=(data.get("state") or "TX"), zip_code=(data.get("zip") or None), transfer_date=when,
        price=int(price) if price not in (None, "") else None,
        confidence={VERIFIED_CASH_PURCHASE: "high", VERIFIED_PURCHASE: "high", RECORDED_TITLE_TRANSFER: "high",
                    REPORTED_INVESTOR_PURCHASE: "medium", CLAIMED_PURCHASE: "low"}[cls],
        note=data.get("note") or None, is_test=bool(buyer.is_test))
    db.add(row)
    db.flush()
    return row


# ── identity review ────────────────────────────────────────────────────────

def review_queue(db, org_id: str, include_test: bool = False) -> List[Dict[str, Any]]:
    rows = db.query(WholesaleBuyerEntity).filter(WholesaleBuyerEntity.organization_id == org_id,
                                                 WholesaleBuyerEntity.status == ENTITY_REVIEW).all()
    ids = {r.buyer_id for r in rows} | {r.linked_buyer_id for r in rows if r.linked_buyer_id}
    buyers = {b.id: b for b in db.query(WholesaleBuyer).filter(WholesaleBuyer.id.in_(list(ids) or [""])).all()}
    out = []
    for r in rows:
        a, c = buyers.get(r.buyer_id), buyers.get(r.linked_buyer_id)
        if a is None or (a.is_test and not include_test):
            continue
        out.append({"id": r.id, "buyer_id": r.buyer_id, "buyer_name": a.company_name or a.contact_name,
                    "candidate_name": r.legal_name, "candidate_buyer_id": r.linked_buyer_id,
                    "candidate_source": getattr(c, "source", None), "reason": r.reason,
                    "created_at": r.created_at.isoformat() if r.created_at else None})
    return out


def decide(db, org_id: str, entity_id: str, decision: str, user) -> Dict[str, Any]:
    """same -> the candidate becomes a confirmed entity of the buyer and its own
    record (if it has one) is linked, not deleted. different -> rejected, never
    suggested again."""
    e = db.query(WholesaleBuyerEntity).filter(WholesaleBuyerEntity.id == entity_id,
                                              WholesaleBuyerEntity.organization_id == org_id).first()
    if e is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    if decision not in ("same", "different"):
        raise HTTPException(status_code=400, detail="decision must be 'same' or 'different'")
    e.decided_by_id, e.decided_at = user.id, datetime.utcnow()
    linked = None
    if decision == "same":
        e.status = ENTITY_CONFIRMED
        if e.linked_buyer_id and e.linked_buyer_id != e.buyer_id:
            other = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == e.linked_buyer_id,
                                                    WholesaleBuyer.organization_id == org_id).first()
            if other is not None and not other.linked_to_buyer_id:
                other.linked_to_buyer_id = e.buyer_id
                other.identity_status = "linked"
                linked = other.id
    else:
        e.status = ENTITY_REJECTED
        if e.linked_buyer_id:
            other = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == e.linked_buyer_id,
                                                    WholesaleBuyer.organization_id == org_id).first()
            if other is not None and other.identity_status == "needs_review":
                still = db.query(WholesaleBuyerEntity).filter(
                    WholesaleBuyerEntity.organization_id == org_id, WholesaleBuyerEntity.linked_buyer_id == other.id,
                    WholesaleBuyerEntity.status == ENTITY_REVIEW, WholesaleBuyerEntity.id != e.id).count()
                if not still:
                    other.identity_status = None
    owner = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == e.buyer_id).first()
    if owner is not None and owner.identity_status == "needs_review":
        open_items = db.query(WholesaleBuyerEntity).filter(
            WholesaleBuyerEntity.organization_id == org_id, WholesaleBuyerEntity.buyer_id == owner.id,
            WholesaleBuyerEntity.status == ENTITY_REVIEW).count()
        if not open_items:
            owner.identity_status = None
    db.flush()
    return {"id": e.id, "status": e.status, "linked_buyer_id": linked}


def unlink(db, org_id: str, buyer_id: str, user) -> Dict[str, Any]:
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id, WholesaleBuyer.organization_id == org_id).first()
    if b is None or not b.linked_to_buyer_id:
        raise HTTPException(status_code=404, detail="This buyer is not linked to another investor.")
    primary = b.linked_to_buyer_id
    b.linked_to_buyer_id = None
    b.identity_status = None
    for e in db.query(WholesaleBuyerEntity).filter(WholesaleBuyerEntity.organization_id == org_id,
                                                   WholesaleBuyerEntity.buyer_id == primary,
                                                   WholesaleBuyerEntity.linked_buyer_id == b.id).all():
        e.status = ENTITY_REJECTED
        e.decided_by_id, e.decided_at = user.id, datetime.utcnow()
    db.flush()
    return {"buyer_id": b.id, "unlinked_from": primary}
