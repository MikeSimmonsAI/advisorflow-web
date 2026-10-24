"""AUTOMATIC COMPARABLE SALES - "Find comps" for a deal, from every source actually
connected, ranked with reasons, and honest about Texas.

TEXAS IS A NON-DISCLOSURE STATE. Deeds are public; prices are not. So the free
public sources can say WHICH similar houses nearby changed hands and WHEN, but
never for how much. This module therefore separates four kinds of evidence and
never lets one pass for another:

    CLOSED_SALE        a closed sale price from an authorized source (MLS, a
                       provider that passed the sold-comps truth gate) or typed by
                       a person with a source reference           -> can feed the ARV
    TRANSFER_NO_PRICE  a recorded deed: date known, price NOT public -> a lead for a
                       price (ask an agent / MLS), never an ARV input by itself
    LIST               an asking price on a live listing          -> reference only
    LISTING_REMOVED    a listing taken down - sold or withdrawn, unknown -> reference only

Assessed / tax values and AVM estimates are not comparables at all here; they
stay in "other valuations" exactly as the ARV engine already labels them.

ADAPTERS (provider-independent - add one by writing a class with `available` and
`fetch`; nothing in underwriting changes):
    county_transfers   houses this workspace already holds from the county rolls,
                       same ZIP / within the radius, deed recorded in the window  FREE
    johnson_subdivision Johnson County roll: same platted subdivision, recent deed FREE
    rentcast_listings  RentCast's nearby listings (inside the hard free monthly
                       budget the platform already enforces)                     FREE TIER
    reapi_comps        RealEstateAPI PropertyComps - closed comps, but BLOCKED until
                       the sold-comps truth gate passes and the owner approves     PAID

Candidates are a shortlist. A person adds one to the deal; a transfer needs its
price and where the price came from (MLS number, closing statement) before it
can count. The ARV engine (unchanged) decides what counts, and refuses to give an
ARV without enough closed sales.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

CLOSED_SALE = "CLOSED_SALE"
TRANSFER_NO_PRICE = "TRANSFER_NO_PRICE"
LIST = "LIST"
LISTING_REMOVED = "LISTING_REMOVED"
KIND_LABELS = {
    CLOSED_SALE: "Closed sale price",
    TRANSFER_NO_PRICE: "Deed recorded - price not public in Texas",
    LIST: "Asking price on a listing (not a sale)",
    LISTING_REMOVED: "Listing removed - sold or withdrawn, price not confirmed",
}
DEFAULTS = {"radius_miles": 1.0, "months": 12, "sqft_tolerance_pct": 35, "year_tolerance": 25, "max_results": 25}


# ── subject ─────────────────────────────────────────────────────────────────

def subject_of(db, org_id: str, deal) -> Dict[str, Any]:
    from app.models.evosense_models import EvoSenseProperty
    from app.models.wholesale_models import WholesaleProperty
    wp = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first()
    evo = (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                             EvoSenseProperty.promoted_deal_id == deal.id).first())

    def pick(*vals):
        for v in vals:
            if v not in (None, ""):
                return v
        return None
    return {
        "street_address": pick(getattr(wp, "street_address", None), getattr(evo, "street_address", None)),
        "city": pick(getattr(wp, "city", None), getattr(evo, "city", None)),
        "zip_code": pick(getattr(wp, "zip_code", None), getattr(evo, "zip_code", None)),
        "county": pick(getattr(wp, "county", None), getattr(evo, "county", None)),
        "property_type": pick(getattr(wp, "property_type", None), getattr(evo, "property_type", None)),
        "square_feet": pick(getattr(wp, "square_feet", None), getattr(evo, "square_feet", None)),
        "bedrooms": pick(getattr(wp, "bedrooms", None), getattr(evo, "bedrooms", None)),
        "bathrooms": pick(getattr(wp, "bathrooms", None), getattr(evo, "bathrooms", None)),
        "year_built": pick(getattr(wp, "year_built", None), getattr(evo, "year_built", None)),
        "latitude": _f(pick(getattr(wp, "latitude", None), getattr(evo, "latitude", None))),
        "longitude": _f(pick(getattr(wp, "longitude", None), getattr(evo, "longitude", None))),
        "is_test": bool(deal.is_test), "evo": evo,
    }


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def miles(a_lat, a_lon, b_lat, b_lon) -> Optional[float]:
    if None in (a_lat, a_lon, b_lat, b_lon):
        return None
    r = 3958.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = math.radians(b_lat - a_lat), math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(h)), 2)


def _norm_addr(s: Optional[str]) -> str:
    return " ".join(str(s or "").upper().replace(".", "").replace(",", " ").split())


# ── adapters ────────────────────────────────────────────────────────────────

class Adapter:
    key = ""
    label = ""
    cost = "free"

    def available(self, db, org_id: str, subject: Dict[str, Any]) -> Tuple[bool, str]:
        return True, ""

    def fetch(self, db, org_id: str, subject: Dict[str, Any], opts: Dict[str, Any]) -> List[Dict[str, Any]]:
        raise NotImplementedError


class CountyTransfers(Adapter):
    key = "county_transfers"
    label = "County deed records already in this workspace"
    cost = "free"

    def available(self, db, org_id, subject):
        if not (subject.get("zip_code") or subject.get("latitude") is not None):
            return False, "The property needs a ZIP code or map position."
        return True, ""

    def fetch(self, db, org_id, subject, opts):
        from app.models.evosense_models import EvoSenseProperty
        since = date.today() - timedelta(days=int(opts["months"] * 30.5))
        q = (db.query(EvoSenseProperty)
             .filter(EvoSenseProperty.organization_id == org_id,
                     EvoSenseProperty.last_deed_transfer_date.isnot(None),
                     EvoSenseProperty.last_deed_transfer_date >= since,
                     EvoSenseProperty.is_test.is_(bool(subject["is_test"]))))
        if subject.get("zip_code"):
            q = q.filter(EvoSenseProperty.zip_code == subject["zip_code"])
        out = []
        evo_id = getattr(subject.get("evo"), "id", None)
        for p in q.limit(500).all():
            if p.id == evo_id or _norm_addr(p.street_address) == _norm_addr(subject.get("street_address")):
                continue
            out.append({
                "address": p.street_address, "city": p.city, "zip_code": p.zip_code,
                "distance_miles": miles(subject.get("latitude"), subject.get("longitude"), _f(p.latitude), _f(p.longitude)),
                "latitude": _f(p.latitude), "longitude": _f(p.longitude),
                "square_feet": p.square_feet, "bedrooms": _f(p.bedrooms), "bathrooms": _f(p.bathrooms),
                "year_built": p.year_built, "property_type": p.property_type,
                "date": p.last_deed_transfer_date.isoformat(), "date_kind": "deed recorded",
                "price": None, "price_kind": TRANSFER_NO_PRICE,
                "source": "County appraisal roll (deed date)", "source_reference": "evosense:%s" % p.id,
                "last_verified": p.updated_at.isoformat() if getattr(p, "updated_at", None) else None,
                "confidence": "high for the date; no price",
            })
        return out


class JohnsonSubdivision(Adapter):
    key = "johnson_subdivision"
    label = "Johnson County roll - same subdivision, recent deeds"
    cost = "free"

    def available(self, db, org_id, subject):
        if (subject.get("county") or "").strip().lower().replace(" county", "") != "johnson":
            return False, "Only for Johnson County properties."
        if subject.get("evo") is None:
            return False, "The deal has no county record linked."
        from app.services.evosense import fc_match as M
        legal = (M.identity(db, subject["evo"]) or {}).get("legal")
        if not legal:
            return False, "No legal description on file yet - look the house up in the Johnson County roll first."
        subject["_legal"] = legal
        return True, ""

    def fetch(self, db, org_id, subject, opts):
        from app.services.evosense.sources import johnson as J
        legal = subject["_legal"]
        idx = J.load_index()
        since = date.today() - timedelta(days=int(opts["months"] * 30.5))
        out = []
        for r in idx.same_subdivision(legal["subdivision"], limit=400):
            try:
                d = datetime.strptime(r.get("RECD DATE") or "", "%m/%d/%Y").date()
            except ValueError:
                continue
            if d < since:
                continue
            addr = " ".join(x for x in (r.get("SITUS STREET NUM"), r.get("SITUS STREET NAME")) if x)
            if _norm_addr(addr) == _norm_addr(subject.get("street_address")):
                continue
            out.append({
                "address": addr, "city": (r.get("SITUS CITY") or "").title() or None, "zip_code": None,
                "distance_miles": None, "same_subdivision": True,
                "square_feet": J._int(r.get("BLDG HEAT AREA")), "bedrooms": None, "bathrooms": None,
                "year_built": J._int(r.get("YEAR_BUILT")),
                "property_type": "single_family" if (r.get("PARCEL USE CD") or "").startswith("A") else None,
                "date": d.isoformat(), "date_kind": "deed recorded",
                "price": None, "price_kind": TRANSFER_NO_PRICE,
                "source": "Johnson County appraisal roll (JCAD certified)",
                "source_reference": "JCAD:%s instrument %s" % (r.get("GEO ACCOUNT NUM"), r.get("INSTRUMENT NUM") or "?"),
                "legal": r.get("LEGAL"), "last_verified": (idx.meta or {}).get("built_at"),
                "confidence": "high for the date and lot; no price",
            })
        return out


class RentcastListings(Adapter):
    key = "rentcast_listings"
    label = "RentCast nearby listings"
    cost = "free tier (hard monthly cap)"

    def available(self, db, org_id, subject):
        from app.services.evosense.sources import rentcast as RC
        if subject.get("evo") is not None and subject["evo"].valuation_detail:
            return True, ""
        if not RC.configured():
            return False, "RentCast is not connected (RENTCAST_API_KEY is not set)."
        return True, ""

    def fetch(self, db, org_id, subject, opts):
        from app.services.evosense import common as C
        evo = subject.get("evo")
        detail = C.jload(evo.valuation_detail, {}) if evo is not None and evo.valuation_detail else None
        if detail is None and evo is not None and opts.get("allow_rentcast_call"):
            from app.services.evosense import value_lookup as VL
            res = VL.value_property(db, evo, user=opts.get("user"))
            detail = res.get("detail") if res.get("ok") else None
            if detail is None:
                opts.setdefault("_notes", []).append(res.get("reason") or "RentCast gave no answer.")
        out = []
        for c in (detail or {}).get("comparables") or []:
            if "rent" in (c.get("label") or "").lower():
                continue
            removed = bool(c.get("removed"))
            out.append({
                "address": (c.get("address") or "").split(",")[0], "city": None, "zip_code": None,
                "full_address": c.get("address"),
                "distance_miles": _f(c.get("distance_miles")), "square_feet": c.get("square_feet"),
                "bedrooms": _f(c.get("bedrooms")), "bathrooms": _f(c.get("bathrooms")),
                "year_built": c.get("year_built"), "property_type": subject.get("property_type"),
                "date": c.get("removed") or c.get("listed"), "date_kind": "listing removed" if removed else "listed",
                "price": _f(c.get("price")), "price_kind": LISTING_REMOVED if removed else LIST,
                "source": "RentCast (listing data)", "source_reference": "rentcast:%s" % (c.get("address") or ""),
                "last_verified": None, "confidence": "listing price only",
            })
        return out


class ReapiComps(Adapter):
    key = "reapi_comps"
    label = "RealEstateAPI closed comps"
    cost = "paid - about $0.10 per subject (vendor quote, unconfirmed)"

    def available(self, db, org_id, subject):
        from app.services.evosense import providers as PV
        from app.services.evosense import truth_gate as TG
        p = PV.PROVIDERS.get("reapi_comps")
        if p is None:
            return False, "Adapter not installed."
        why = []
        if p.missing_config():
            why.append("REAPI_API_KEY is not set")
        if getattr(p, "evaluation_only", False):
            why.append("the vendor is evaluation-only until the owner approves it")
        if not TG.passes(db, org_id, "reapi_comps"):
            why.append("the sold-comps truth gate has not passed (Texas prices must be shown to be closed "
                       "sales, with storage and display rights)")
        if why:
            return False, "Not used: " + "; ".join(why) + "."
        return True, ""

    def fetch(self, db, org_id, subject, opts):      # pragma: no cover - blocked in production today
        return []


ADAPTERS: List[Adapter] = [CountyTransfers(), JohnsonSubdivision(), RentcastListings(), ReapiComps()]


# ── ranking ─────────────────────────────────────────────────────────────────

def rank(subject: Dict[str, Any], c: Dict[str, Any], opts: Dict[str, Any], today: Optional[date] = None
         ) -> Dict[str, Any]:
    """Score 0-100 with a sentence per point, and exclusions that say why."""
    today = today or date.today()
    pts, why, exclude = 0, [], []
    d = c.get("distance_miles")
    if c.get("same_subdivision"):
        pts += 30
        why.append("+30 same platted subdivision")
    elif d is not None:
        if d > opts["radius_miles"] * 2:
            exclude.append("%.2f miles away (limit %.1f)" % (d, opts["radius_miles"] * 2))
        p = 30 if d <= 0.5 else 20 if d <= opts["radius_miles"] else 8
        pts += p
        why.append("+%d %.2f miles away" % (p, d))
    else:
        pts += 10
        why.append("+10 same ZIP code (exact distance unknown)")
    ss, cs = _f(subject.get("square_feet")), _f(c.get("square_feet"))
    if ss and cs:
        diff = abs(cs - ss) / ss * 100
        if diff > opts["sqft_tolerance_pct"]:
            exclude.append("living area differs %.0f%% (limit %d%%)" % (diff, opts["sqft_tolerance_pct"]))
        p = 25 if diff <= 10 else 15 if diff <= 20 else 5 if diff <= opts["sqft_tolerance_pct"] else 0
        pts += p
        why.append("+%d living area %.0f vs %.0f sq ft" % (p, cs, ss))
    else:
        why.append("+0 living area unknown")
    sy, cy = subject.get("year_built"), c.get("year_built")
    if sy and cy:
        dy = abs(int(cy) - int(sy))
        if dy > opts["year_tolerance"]:
            exclude.append("built %s vs %s" % (cy, sy))
        p = 15 if dy <= 10 else 8 if dy <= 20 else 0
        pts += p
        why.append("+%d built %s (subject %s)" % (p, cy, sy))
    if subject.get("bedrooms") and c.get("bedrooms"):
        if abs(float(subject["bedrooms"]) - float(c["bedrooms"])) <= 0.5:
            pts += 10
            why.append("+10 same bedrooms")
    st, ct = (subject.get("property_type") or "").lower(), (c.get("property_type") or "").lower()
    if st and ct and st != ct:
        exclude.append("%s, subject is %s" % (ct.replace("_", " "), st.replace("_", " ")))
    try:
        cd = date.fromisoformat(str(c.get("date"))[:10]) if c.get("date") else None
    except ValueError:
        cd = None
    if cd:
        months = (today.year - cd.year) * 12 + (today.month - cd.month)
        if months > opts["months"]:
            exclude.append("%d months old (limit %d)" % (months, opts["months"]))
        p = 20 if months <= 6 else 10 if months <= 12 else 0
        pts += p
        why.append("+%d %s %d months ago" % (p, c.get("date_kind") or "dated", months))
    usable = c["price_kind"] == CLOSED_SALE and not exclude
    return {"score": min(100, pts), "why": why, "excluded": exclude, "usable_for_arv": usable,
            "needs_price": c["price_kind"] == TRANSFER_NO_PRICE and not exclude}


# ── the request ─────────────────────────────────────────────────────────────

def find(db, org_id: str, deal, user=None, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    opts = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k in DEFAULTS and v is not None:
            opts[k] = type(DEFAULTS[k])(v)
    opts["allow_rentcast_call"] = bool((overrides or {}).get("allow_rentcast_call"))
    opts["user"] = user
    subject = subject_of(db, org_id, deal)
    sources, cands = [], []
    for a in ADAPTERS:
        ok, why = a.available(db, org_id, subject)
        row = {"key": a.key, "label": a.label, "cost": a.cost, "available": ok, "why": why or None, "found": 0,
               "error": None}
        if ok:
            try:
                got = a.fetch(db, org_id, subject, opts)
            except Exception as exc:  # noqa: BLE001 - one source failing must not hide the others
                row["error"] = "%s did not answer: %s" % (a.label, str(exc)[:160])
                got = []
            for c in got:
                c["adapter"] = a.key
                c["kind_label"] = KIND_LABELS[c["price_kind"]]
                c.update(rank(subject, c, opts))
                c["key"] = "%s|%s|%s" % (a.key, _norm_addr(c.get("address")), c.get("date"))
            row["found"] = len(got)
            cands += got
        sources.append(row)
    seen, uniq = set(), []
    for c in sorted(cands, key=lambda x: (bool(x["excluded"]), -x["score"])):
        k = (_norm_addr(c.get("address")), c["price_kind"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)
    usable = [c for c in uniq if c["usable_for_arv"]]
    need_price = [c for c in uniq if c["needs_price"]]
    listings = [c for c in uniq if c["price_kind"] in (LIST, LISTING_REMOVED) and not c["excluded"]]
    gap = None
    if len(usable) < 3:
        gap = ("No verified closed sale prices are available for this house from the connected sources, so no "
               "ARV is calculated from them. Texas does not publish sale prices. %s%s" % (
                   ("%d similar nearby house%s changed hands recently - add the sale price from the MLS or a "
                    "closing statement (with its reference) and they will count. " % (
                        len(need_price), "" if len(need_price) == 1 else "s")) if need_price else "",
                   "A closed-sales provider (RealEstateAPI PropertyComps, about $0.10 per lookup, needs "
                   "REAPI_API_KEY and the sold-comps truth gate) would fill this automatically once approved."))
    return {"deal_id": deal.id, "subject": {k: v for k, v in subject.items() if k not in ("evo", "_legal")},
            "options": {k: v for k, v in opts.items() if k in DEFAULTS},
            "sources": sources, "candidates": uniq[:opts["max_results"] * 2],
            "counts": {"closed_sales_usable": len(usable), "transfers_need_price": len(need_price),
                       "listings_reference_only": len(listings), "excluded": sum(1 for c in uniq if c["excluded"])},
            "gap": gap, "notes": opts.get("_notes") or [],
            "kinds": KIND_LABELS,
            "rule": "Only closed sale prices with a source count toward the ARV. Deed transfers need a price; "
                    "listings, tax values and estimates never count."}


def add_candidate(db, org_id: str, deal, user, cand: Dict[str, Any], sale_price: Optional[float],
                  source_reference: Optional[str]) -> Any:
    """Turn a shortlisted candidate into a comp on the deal. A transfer needs a price
    AND where it came from; a listing is refused - an asking price is not a sale."""
    from fastapi import HTTPException
    from app.models.wholesale_models import WholesaleComp
    from app.services import wholesale_service as svc
    kind = cand.get("price_kind")
    if kind in (LIST, LISTING_REMOVED):
        raise HTTPException(status_code=409, detail="A listing price is not a sale price, so it cannot be added "
                                                    "as a comparable sale.")
    if kind == TRANSFER_NO_PRICE:
        if not sale_price or sale_price <= 0:
            raise HTTPException(status_code=422, detail="Enter the sale price - Texas does not publish it.")
        if not (source_reference or "").strip() or len(source_reference.strip()) < 3:
            raise HTTPException(status_code=422, detail="Say where the price came from (MLS number, closing "
                                                        "statement, agent) - a price without a source is not used.")
    try:
        sd = date.fromisoformat(str(cand.get("date"))[:10])
    except (TypeError, ValueError):
        sd = None
    comp = WholesaleComp(
        organization_id=org_id, deal_id=deal.id, street_address=cand.get("address"), city=cand.get("city"),
        state="TX", zip_code=cand.get("zip_code"), sale_price=sale_price, sale_date=sd,
        square_feet=cand.get("square_feet"), bedrooms=cand.get("bedrooms"), bathrooms=cand.get("bathrooms"),
        year_built=cand.get("year_built"), distance_miles=cand.get("distance_miles"),
        property_type=cand.get("property_type"), latitude=cand.get("latitude"), longitude=cand.get("longitude"),
        source=svc.VALUE_MANUAL, source_reference=(source_reference or "").strip()[:200],
        verification_state="manual", entered_by_id=user.id, is_test=bool(deal.is_test),
        provenance=json.dumps({"found_by": cand.get("adapter"), "found_source": cand.get("source"),
                               "found_reference": cand.get("source_reference"), "evidence_kind": kind,
                               "price_entered_by_person": True, "rank": {"score": cand.get("score"),
                                                                         "why": cand.get("why")}}),
        notes="Found automatically (%s); sale price entered by a person from: %s" % (
            cand.get("source"), (source_reference or "").strip()[:120]))
    db.add(comp)
    db.flush()
    svc.log_event(db, org_id, "comp.added_from_search", actor_type=svc.ACTOR_USER, actor_user_id=user.id,
                  deal_id=deal.id, summary="Comp added from automatic search: %s" % (cand.get("address") or "")[:120],
                  after={"price": sale_price, "source_reference": source_reference, "found_by": cand.get("adapter")})
    return comp
