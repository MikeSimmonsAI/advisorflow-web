"""FREE business discovery from OpenStreetMap (#452, Harvey workforce).

Finds NEW businesses that BUY a client's service - offices, property
managers, shopping centres, hotels... - inside the client's territory, from
OpenStreetMap's public data (ODbL). No account, no fee, no scraping:

  * Nominatim (one geocode per city, cached on the run) turns "Arlington, TX"
    into a bounding box - the usage policy allows single place lookups;
  * ONE Overpass query per run reads named places with the buyer tags inside
    that box - modest volume, no parallelism (Overpass etiquette, the same
    service Harvey's own discovery defaults to).

Every business found is recorded in the EXISTING master prospect pool
(lead_intel_prospects) in its own discovery batch, with the OSM element URL
as its source, the collection time, the tags it was found by, a
qualification reason, a confidence and a duplicate fingerprint. Businesses
already in the pool are skipped. Nothing is assigned to a client and nobody
is contacted. A website from OSM is a CLAIM until research confirms the site
names the business.
"""
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionDiscoveryRun
from app.services.acquisition import common as C
from app.services.acquisition import harvey_adapted as H

log = logging.getLogger("acquisition.discovery_osm")
PROVIDER = "openstreetmap"
UA = "EvoSysPro-Acquisition/1.0 (business discovery; +https://evosyspro.live)"
OVERPASS_MIRROR = "https://overpass.kumi.systems/api/interpreter"
OVERPASS = "https://overpass-api.de/api/interpreter"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
MAX_NEW_PER_RUN = 25

# Which OSM tags mark a BUYER of each segment. (tag, value regex or None).
SEGMENT_TAGS: Dict[str, List[Tuple[str, Optional[str]]]] = {
    "office": [("office", "^(company|it|lawyer|accountant|insurance|financial|consulting|architect|engineer|"
                         "advertising_agency|employment_agency|tax_advisor|telecommunication|coworking)$")],
    "property_management": [("office", "^property_management$")],
    "retail": [("shop", "^(mall|department_store|supermarket|furniture|car|electronics|clothes|hardware|"
                       "doityourself|sports|jewelry|optician|beauty|hairdresser|bakery|gift|books|pet)$"),
               ("landuse", "^retail$")],
    "restaurants": [("tourism", "^(hotel|motel)$"), ("amenity", "^(restaurant|cafe|fast_food|bar|pub)$")],
    "medical_dental": [("amenity", "^(clinic|dentist|doctors)$"), ("healthcare", None)],
    "apartments": [("building", "^apartments$")],
    "industrial": [("building", "^(warehouse|industrial)$"), ("landuse", "^industrial$")],
    "homeowners": [],
    "hoa": [],
    "short_term_rental": [],
}
# Tagged like a business but never a buyer of building services: agents
# selling other people's buildings, service desks inside a venue.
NOT_A_BUYER = {"office": {"estate_agent", "guest_relations", "government"}}


def not_a_buyer(tags: Dict[str, str]) -> Optional[str]:
    for k, bad in NOT_A_BUYER.items():
        if tags.get(k) in bad:
            return "%s=%s is not a buyer of building services" % (k, tags[k])
    return None


SEGMENT_REASON = {
    "office": "Office / commercial business premises",
    "property_management": "Property manager - buys services for the buildings it runs",
    "retail": "Retail storefront or shopping centre",
    "restaurants": "Hospitality / food-service premises",
    "medical_dental": "Medical or dental practice premises",
    "apartments": "Apartment community",
    "industrial": "Warehouse / industrial premises",
}
# Never a buyer: the client's own competitors, by trade words in the name.
COMPETITOR_WORDS = {
    "window_washing": ("window wash", "window clean", "window cleaning"),
    "cleaning": ("janitorial", "cleaning service", "commercial cleaning", "maid"),
    "residential_cleaning": ("maid", "house cleaning", "cleaning service"),
    "pest_control": ("pest", "exterminat", "termite"),
    "landscaping": ("landscap", "lawn", "tree service"),
    "pressure_washing": ("pressure wash", "power wash"),
}


def segments_for(org, profile) -> List[str]:
    """The buyer types this client sells to: its profile's segments, else the
    buyer segments of its category's services."""
    segs = [s for s in (C.jload(getattr(profile, "segments", None), []) or []) if s in SEGMENT_TAGS]
    if segs:
        return segs
    cat = (getattr(org, "industry", None) or "").lower()
    return sorted({seg for _, _, seg in C.services_for(cat) if seg in SEGMENT_TAGS})


def build_query(bbox: Tuple[float, float, float, float], segments: List[str]) -> str:
    s, w, n, e = bbox
    parts = []
    for seg in segments:
        for tag, rx in SEGMENT_TAGS.get(seg, []):
            cond = '["%s"~"%s"]' % (tag, rx) if rx else '["%s"]' % tag
            parts.append('nwr["name"]%s(%.5f,%.5f,%.5f,%.5f);' % (cond, s, w, n, e))
    return '[out:json][timeout:25];(%s);out center tags 200;' % "".join(parts)


def build_tag_query(bbox: Tuple[float, float, float, float], tag: str, rx: Optional[str]) -> str:
    """ONE tag condition per request - small enough for the free servers."""
    s, w, n, e = bbox
    cond = '["%s"~"%s"]' % (tag, rx) if rx else '["%s"]' % tag
    return '[out:json][timeout:25];nwr["name"]%s(%.5f,%.5f,%.5f,%.5f);out center tags 150;' % (cond, s, w, n, e)


def requalify(db: Session) -> int:
    """Take earlier OpenStreetMap finds that today's rules say are not
    buyers out of the pool (suppressed, with the reason; never deleted)."""
    from app.models.lead_intel_models import LeadIntelProspect
    n = 0
    for p in (db.query(LeadIntelProspect).filter(LeadIntelProspect.source == PROVIDER,
                                                 LeadIntelProspect.suppressed_reason.is_(None)).all()):
        q = ((C.jload(p.evidence_json, {}) or {}).get("discovery") or {}).get("qualification") or ""
        for k, bad in NOT_A_BUYER.items():
            hit = next((v for v in bad if ("%s=%s" % (k, v)) in q), None)
            if hit:
                p.suppressed_reason = "Not a buyer: %s=%s (discovery re-check)" % (k, hit)
                n += 1
                break
    if n:
        db.commit()
    return n


def classify(tags: Dict[str, str], segments: List[str]) -> Optional[str]:
    for seg in segments:
        for tag, rx in SEGMENT_TAGS.get(seg, []):
            v = tags.get(tag)
            if v is not None and (rx is None or re.search(rx, v)):
                return seg
    return None


def _fingerprint(name: str, website: str, phone: str, city: str) -> str:
    key = H.dedupe_key(name, website, phone)
    if key.startswith("n:"):
        key += ":" + re.sub(r"[^a-z]", "", (city or "").lower())
    return key


def _existing_keys(db: Session) -> set:
    from app.models.lead_intel_models import LeadIntelProspect
    keys = set()
    for name, site, phone, city in db.query(LeadIntelProspect.name, LeadIntelProspect.website,
                                            LeadIntelProspect.phone_raw, LeadIntelProspect.city).all():
        keys.add(_fingerprint(name or "", site or "", phone or "", city or ""))
        keys.add("name:" + re.sub(r"[^a-z0-9]", "", (name or "").lower()) + ":" +
                 re.sub(r"[^a-z]", "", (city or "").lower()))
    return keys


def default_geocode(city: str, state: str) -> Optional[Tuple[float, float, float, float]]:
    import httpx
    r = httpx.get(NOMINATIM, timeout=15, headers={"User-Agent": UA},
                  params={"city": city, "state": state, "country": "USA", "format": "jsonv2", "limit": 1})
    r.raise_for_status()
    rows = r.json() or []
    if not rows or not rows[0].get("boundingbox"):
        return None
    s, n, w, e = (float(x) for x in rows[0]["boundingbox"])
    return (s, w, n, e)


def default_overpass(query: str) -> List[Dict[str, Any]]:
    """One small Overpass query. The main public server first; if it is busy
    (429/5xx/timeout) ONE try at the public mirror - both free, no key."""
    import httpx
    last = None
    for url in (OVERPASS, OVERPASS_MIRROR):
        time.sleep(1.1)                   # never back-to-back requests
        try:
            r = httpx.post(url, data={"data": query}, timeout=45, headers={"User-Agent": UA})
            if r.status_code == 429 or r.status_code >= 500:
                last = "%s answered %d" % (url.split("/")[2], r.status_code)
                continue
            r.raise_for_status()
            return (r.json() or {}).get("elements") or []
        except httpx.HTTPError as exc:
            last = "%s: %s" % (url.split("/")[2], type(exc).__name__)
    raise RuntimeError("OpenStreetMap search servers are busy (%s)" % last)


def _interleave(groups: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Take one from each buyer type in turn, so one big type can't crowd out the rest."""
    out, i = [], 0
    while any(i < len(g) for g in groups):
        for g in groups:
            if i < len(g):
                out.append(g[i])
        i += 1
    return out


def run(db: Session, org, profile, *, city: str, state: str = "TX", max_new: int = MAX_NEW_PER_RUN,
        is_practice: bool = True, geocode=None, overpass=None, actor_id: Optional[str] = None) -> Dict[str, Any]:
    """One bounded free discovery run for one client's territory and buyers."""
    from app.models.lead_intel_models import LeadIntelProspect
    segments = segments_for(org, profile)
    batch = "osm:%s-%s:%s:%s" % (re.sub(r"[^a-z]", "", city.lower()), state.lower(),
                                 (getattr(org, "industry", None) or "service"), datetime.utcnow().strftime("%Y%m%d%H%M"))
    runrow = AcquisitionDiscoveryRun(organization_id=org.id, requested_by_id=actor_id, provider=PROVIDER,
                                     segment=",".join(segments)[:40] or "none", city=city[:80],
                                     query=("OpenStreetMap buyers in %s, %s" % (city, state))[:300],
                                     max_results=max_new, is_practice=is_practice, status="running", cost_usd=0.0)
    db.add(runrow)
    db.commit()
    out = {"batch": batch, "segments": segments, "found": 0, "added": 0, "duplicates": 0, "excluded": 0,
           "unqualified": 0, "run_id": runrow.id, "provider": PROVIDER, "cost_usd": 0.0}
    try:
        if not segments:
            raise ValueError("this client has no buyer types to search for")
        bbox = (geocode or default_geocode)(city, state)
        if not bbox:
            raise ValueError("OpenStreetMap could not locate %s, %s" % (city, state))
        out["requalified"] = requalify(db)
        groups, seg_errors, asked = [], [], 0
        for seg in segments:                     # one small query per buyer-type tag
            for tag, rx in SEGMENT_TAGS.get(seg, []):
                asked += 1
                try:
                    groups.append((overpass or default_overpass)(build_tag_query(bbox, tag, rx)) or [])
                except Exception as exc:          # noqa: BLE001
                    seg_errors.append("%s/%s: %s" % (seg, tag, str(exc)[:120]))
        runrow.search_requests = 1 + asked
        if seg_errors and len(seg_errors) == asked:
            raise RuntimeError("; ".join(seg_errors))
        if seg_errors:
            out["segment_errors"] = seg_errors
        elements = _interleave(groups)
        seen = _existing_keys(db)
        comp = COMPETITOR_WORDS.get((getattr(org, "industry", None) or "").lower(), ())
        excl = C.jload(getattr(profile, "exclusions", None), []) or []
        now = datetime.utcnow()
        this_run = set()
        for el in elements:
            tags = el.get("tags") or {}
            name = (tags.get("name") or "").strip()
            oid = "%s/%s" % (el.get("type"), el.get("id"))
            if not name or oid in this_run:       # same place matched two buyer types
                continue
            this_run.add(oid)
            out["found"] += 1
            seg = classify(tags, segments)
            if seg is None or not_a_buyer(tags):
                out["unqualified"] += 1
                continue
            tag_city = (tags.get("addr:city") or "").strip()
            if tag_city and tag_city.lower() != city.strip().lower():
                out["outside_city"] = out.get("outside_city", 0) + 1     # found again when that city is run
                continue
            low = name.lower()
            site = (tags.get("website") or tags.get("contact:website") or tags.get("url") or "").strip()
            phone = (tags.get("phone") or tags.get("contact:phone") or "").strip()
            el_city = (tags.get("addr:city") or city).strip()
            if any(w in low for w in comp) or H.is_junk(name, H.normalize_domain(site)) or \
                    (site and H.do_not_contact_domain_match(H.normalize_domain(site), excl)):
                out["excluded"] += 1
                continue
            fp = _fingerprint(name, site, phone, el_city)
            nk = "name:" + re.sub(r"[^a-z0-9]", "", low) + ":" + re.sub(r"[^a-z]", "", el_city.lower())
            if fp in seen or nk in seen:
                out["duplicates"] += 1
                continue
            if out["added"] >= max_new:
                break
            seen.update({fp, nk})
            osm_id = "%s/%s" % (el.get("type"), el.get("id"))
            addr = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x) or None
            has_premises = bool(addr or el.get("center") or el.get("lat"))
            confidence = 0.6 + (0.2 if site else 0) + (0.1 if addr else 0)
            reason = "%s in %s (OpenStreetMap: %s)" % (SEGMENT_REASON.get(seg, seg), el_city,
                                                      ", ".join("%s=%s" % (k, tags[k]) for k in
                                                                ("office", "shop", "tourism", "amenity", "building",
                                                                 "landuse", "healthcare") if k in tags))
            ev = {"discovery": {"provider": PROVIDER, "batch": batch, "osm": osm_id, "collected_at": now.isoformat(),
                                "segment": seg, "qualification": reason, "confidence": round(confidence, 2),
                                "fingerprint": fp, "website_claimed": site or None,
                                "website_verified": False, "premises": has_premises},
                  "segment": seg, "geo_status": "eligible" if (tags.get("addr:state") or state).upper() == state.upper()
                  else "review"}
            db.add(LeadIntelProspect(source=PROVIDER, external_id=osm_id, industry=seg, name=name[:200],
                                     phone_raw=phone or None, website=site or None, address=addr,
                                     city=el_city, state=(tags.get("addr:state") or state).upper()[:2],
                                     zip_code=tags.get("addr:postcode"), stage="qualified",
                                     source_url="https://www.openstreetmap.org/%s" % osm_id,
                                     import_batch_id=None, source_record_id=osm_id,
                                     evidence_json=json.dumps(ev), raw_json=json.dumps(tags)[:8000]))
            out["added"] += 1
        runrow.found, runrow.added, runrow.duplicates = out["found"], out["added"], out["duplicates"]
        runrow.excluded, runrow.junk = out["excluded"], out["unqualified"]
        runrow.status, runrow.finished_at = "done", datetime.utcnow()
        db.commit()
    except Exception as exc:                                     # noqa: BLE001
        db.rollback()
        runrow = db.query(AcquisitionDiscoveryRun).filter(AcquisitionDiscoveryRun.id == runrow.id).one()
        runrow.status, runrow.error, runrow.finished_at = "failed", ("%s: %s" % (type(exc).__name__, exc))[:500], datetime.utcnow()
        db.commit()
        out["error"] = runrow.error
    return out
