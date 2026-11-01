"""P2 - business discovery for the managed acquisition pilot (Issue #452).

Reuses the EXISTING Google Places calls the God lead scraper already makes
(app/routers/lead_scraper_router._google_places_search / _google_place_details);
no second provider, no Harvey daemon, no independent CRM.

Every run is:
  * PLANNED first (no calls): the query, the request count and the cost from
    CONFIGURED unit prices - never an invented price;
  * SMALL: at most MAX_RESULTS_PER_RUN businesses, MAX_RUNS_PER_DAY runs;
  * SCOPED: the client's own business types and cities only, one org only;
  * GATED: ACQUISITION_DISCOVERY_ENABLED=1, a Places key and both unit prices
    must be configured, and while the pilot is paused only PRACTICE discovery
    runs (practice rows can never be contacted or shown to the client);
  * METERED: every search and details request is counted on the run row.

Facts are kept apart from inferences: what the listing itself says is
recorded as verified-at-source evidence; the business type, which comes
from the search category, is recorded as an UNVERIFIED inference.
Nothing here contacts anyone.
"""
import math
import os
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionDiscoveryRun, AcquisitionOpportunity
from app.models.models import Organization, User
from app.services.acquisition import common as C
from app.services.acquisition import engine as E
from app.services.acquisition import harvey_adapted as H

MAX_RESULTS_PER_RUN = 20
MAX_RUNS_PER_DAY = 5
PAGE_SIZE = 20          # Places text search returns up to 20 per page

# What we search for per business type. Plain category words; the city is
# appended. These are search terms, never claims about a business.
SEGMENT_QUERIES = {
    "office": "office building",
    "medical_dental": "dental office",
    "apartments": "apartment complex",
    "property_management": "property management company",
    "short_term_rental": "vacation rental management company",
}


def _flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _price(name: str) -> Optional[float]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if v >= 0 else None


def unit_prices() -> Dict[str, Optional[float]]:
    """USD per request, from configuration only (Render env). Check the
    provider's current price list before setting them."""
    return {"search": _price("ACQUISITION_PLACES_SEARCH_USD"),
            "details": _price("ACQUISITION_PLACES_DETAILS_USD")}


def _runs_today(db: Session, org_id: str) -> int:
    since = datetime.utcnow() - timedelta(hours=24)
    return (db.query(AcquisitionDiscoveryRun)
            .filter(AcquisitionDiscoveryRun.organization_id == org_id,
                    AcquisitionDiscoveryRun.created_at >= since).count())


def plan(db: Session, org: Organization, segment: str, city: str, max_results: int,
         practice: bool) -> Dict[str, Any]:
    """What a run WOULD do, and every reason it would be refused. No calls."""
    p = E.get_profile(db, org.id)
    reasons: List[str] = []
    if p is None:
        raise HTTPException(409, "Run setup first")
    if segment not in SEGMENT_QUERIES:
        raise HTTPException(422, "Unknown business type")
    city = (city or "").strip()
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        raise HTTPException(422, "max_results must be a number")
    if not 1 <= max_results <= MAX_RESULTS_PER_RUN:
        raise HTTPException(422, "A run finds 1 to %d businesses" % MAX_RESULTS_PER_RUN)
    if segment not in C.jload(p.segments, []):
        reasons.append("The client does not want this business type")
    areas = [a.lower() for a in C.jload(p.service_area, [])]
    if city.lower() not in areas:
        reasons.append("%s is not in the client's service area" % (city or "That city"))
    query = "%s in %s, TX" % (SEGMENT_QUERIES[segment], city)
    search_req = math.ceil(max_results / PAGE_SIZE)
    details_req = max_results          # one details lookup per business, at most
    prices = unit_prices()
    cost = None
    if prices["search"] is not None and prices["details"] is not None:
        cost = round(search_req * prices["search"] + details_req * prices["details"], 4)
    else:
        reasons.append("Provider unit prices are not configured, so the cost can't be stated")
    if not _flag("ACQUISITION_DISCOVERY_ENABLED"):
        reasons.append("Discovery is switched off on this server (ACQUISITION_DISCOVERY_ENABLED)")
    if not os.getenv("GOOGLE_PLACES_API_KEY"):
        reasons.append("No Google Places key is configured")
    if bool(p.paused) and not practice:
        reasons.append("The pilot is paused: only a practice run is allowed")
    used = _runs_today(db, org.id)
    if used >= MAX_RUNS_PER_DAY:
        reasons.append("Daily limit reached (%d runs in 24 hours)" % MAX_RUNS_PER_DAY)
    return {"segment": segment, "city": city, "query": query, "max_results": max_results,
            "is_practice": bool(practice), "requests": {"search": search_req, "details_max": details_req},
            "unit_prices_usd": prices, "max_cost_usd": cost, "runs_last_24h": used,
            "limits": {"max_results_per_run": MAX_RESULTS_PER_RUN, "max_runs_per_day": MAX_RUNS_PER_DAY},
            "can_run": not reasons, "refused_because": reasons}


# ── scoring: deterministic, explainable, no AI ─────────────────────────────
def score(segment_match: bool, in_area: bool, has_site: bool, has_phone: bool,
          reviews: Optional[int]) -> Dict[str, Any]:
    pts, why = 0, []
    if segment_match:
        pts += 35; why.append("+35 business type the client wants (from search category, unverified)")
    if in_area:
        pts += 25; why.append("+25 in the client's service area (listing address)")
    if has_site:
        pts += 15; why.append("+15 has a website to research")
    if has_phone:
        pts += 5; why.append("+5 listed business phone")
    if reviews:
        add = 20 if reviews >= 100 else 12 if reviews >= 25 else 5
        pts += add; why.append("+%d established (%d public reviews)" % (add, reviews))
    return {"score": min(pts, 100), "reasons": why}


def _city_from_address(addr: str) -> Optional[str]:
    # "123 Main St, Dallas, TX 75201, USA" -> "Dallas"
    parts = [x.strip() for x in (addr or "").split(",")]
    for i, part in enumerate(parts):
        if part.startswith("TX") and i > 0:
            return parts[i - 1]
    return None


Searcher = Callable[[str, int], Awaitable[List[dict]]]
Detailer = Callable[[str], Awaitable[dict]]


def _places_provider():
    from app.routers.lead_scraper_router import _google_place_details, _google_places_search
    key = os.getenv("GOOGLE_PLACES_API_KEY", "")

    async def search(query: str, max_results: int) -> List[dict]:
        return await _google_places_search(query, None, 40000, max_results, key)

    async def details(place_id: str) -> dict:
        return await _google_place_details(place_id, key)
    return search, details


async def run(db: Session, org: Organization, actor: User, segment: str, city: str, max_results: int,
              practice: bool, search: Optional[Searcher] = None, details: Optional[Detailer] = None) -> Dict[str, Any]:
    pl = plan(db, org, segment, city, max_results, practice)
    if not pl["can_run"]:
        raise HTTPException(409, {"message": "Discovery refused", "refused_because": pl["refused_because"]})
    if search is None or details is None:
        search, details = _places_provider()
    r = AcquisitionDiscoveryRun(organization_id=org.id, requested_by_id=actor.id, segment=segment, city=pl["city"],
                                query=pl["query"], max_results=pl["max_results"], is_practice=bool(practice),
                                status="running")
    db.add(r)
    db.commit()
    prices = pl["unit_prices_usd"]
    try:
        places = await search(pl["query"], pl["max_results"])
        r.search_requests = max(1, math.ceil(len(places) / PAGE_SIZE)) if places else 1
        places = places[:pl["max_results"]]
        r.found = len(places)
        p = E.get_profile(db, org.id)
        excl = C.jload(p.exclusions, [])
        areas = [a.lower() for a in C.jload(p.service_area, [])]
        for place in places:
            name = (place.get("name") or "").strip()
            pid = place.get("place_id") or ""
            if not name:
                r.junk += 1
                continue
            det = await details(pid) if pid else {}
            if pid:
                r.detail_requests += 1
            website = det.get("website") or None
            domain = H.normalize_domain(website) if website else ""
            phone = det.get("formatted_phone_number") or None
            if H.is_junk(name, domain):
                r.junk += 1
                continue
            key = H.dedupe_key(name, domain, phone or "")
            if (db.query(AcquisitionOpportunity.id)
                    .filter(AcquisitionOpportunity.organization_id == org.id,
                            AcquisitionOpportunity.dedupe_key == key).first()) is not None:
                r.duplicates += 1
                continue
            if domain and H.do_not_contact_domain_match(domain, excl):
                r.excluded += 1
                continue
            addr = place.get("formatted_address") or ""
            pcity = _city_from_address(addr)
            listing_url = "https://www.google.com/maps/place/?q=place_id:%s" % pid if pid else None
            opp = E.add_prospect(db, org, actor, {
                "business_name": name, "segment": segment, "website": website, "phone": phone,
                "street_address": addr.split(",")[0] if addr else None, "city": pcity, "state": "TX",
                "source": "google_places", "source_url": listing_url, "is_practice": bool(practice)})
            reviews = det.get("user_ratings_total") or place.get("user_ratings_total")
            sc = score(True, bool(pcity and pcity.lower() in areas), bool(website), bool(phone), reviews)
            opp.score, opp.score_reasons = sc["score"], C.jdump(sc["reasons"])
            # Facts the listing states (verified at source) vs our inference.
            if addr:
                E.add_evidence(db, org.id, opp, None, {"kind": "listing", "value": "Address: " + addr,
                                                       "source_url": listing_url, "confidence": 0.95,
                                                       "verified": True}, commit=False)
            if website:
                E.add_evidence(db, org.id, opp, None, {"kind": "website", "value": website, "source_url": listing_url,
                                                       "confidence": 0.9, "verified": True}, commit=False)
            if phone:
                E.add_evidence(db, org.id, opp, None, {"kind": "contact_phone", "value": "Business line: " + phone,
                                                       "source_url": listing_url, "confidence": 0.9,
                                                       "verified": True}, commit=False)
            E.add_evidence(db, org.id, opp, None, {"kind": "segment", "value": C.SEGMENT_LABEL[segment] +
                                                   " (inferred from the search category - not verified)",
                                                   "source_url": listing_url, "confidence": 0.6,
                                                   "verified": False}, commit=False)
            r.added += 1
            db.commit()
        r.status = "done"
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - recorded on the run, never swallowed silently
        db.rollback()
        r.status, r.error = "failed", str(exc)[:500]
    if prices["search"] is not None and prices["details"] is not None:
        r.cost_usd = round(r.search_requests * prices["search"] + r.detail_requests * prices["details"], 4)
    r.finished_at = datetime.utcnow()
    db.commit()
    return run_json(r)


def run_json(r: AcquisitionDiscoveryRun) -> Dict[str, Any]:
    return {"id": r.id, "segment": r.segment, "segment_label": C.SEGMENT_LABEL.get(r.segment), "city": r.city,
            "query": r.query, "max_results": r.max_results, "is_practice": bool(r.is_practice), "status": r.status,
            "search_requests": r.search_requests, "detail_requests": r.detail_requests, "cost_usd": r.cost_usd,
            "found": r.found, "added": r.added, "duplicates": r.duplicates, "excluded": r.excluded,
            "junk": r.junk, "error": r.error, "at": C.iso(r.created_at)}


def runs(db: Session, org_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    rows = (db.query(AcquisitionDiscoveryRun).filter(AcquisitionDiscoveryRun.organization_id == org_id)
            .order_by(AcquisitionDiscoveryRun.created_at.desc()).limit(limit).all())
    return [run_json(r) for r in rows]
