"""
Lead Scraper -- TIER 3
----------------------
Search for local businesses using Google Places Text Search API,
validate phone numbers via Twilio Lookup v2, then import as leads.

Required env vars:
  GOOGLE_PLACES_API_KEY   -- from Google Cloud Console (Places API enabled)

Twilio Lookup uses the org's own Twilio creds (falls back to global).

Endpoints:
  POST /scraper/search     -- search Google Places, returns raw results
  POST /scraper/validate   -- run Twilio Lookup on a batch of phones
  POST /scraper/import     -- create Lead records from validated results
  POST /scraper/exists     -- check which phones already exist in org leads
  POST /scraper/stage      -- put results into the Lead Intelligence prospect pool
                              (normalize -> dedupe -> suppression -> qualification);
                              routed to an organization only by an explicit
                              POST /god/lead-intelligence/route

Sep 28 2026 (Lead Intelligence): `industry` maps to query terms, an intended
`destination_org_id` is recorded on a LeadIntelScrapeJob (never auto-routed),
and /import now runs normalize -> dedupe -> suppression -> qualification
before it writes, reusing app/services/qualification.py.
"""

import logging
import os
import uuid
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_god
from app.models.models import Lead, Organization, User
from app.utils.crypto import decrypt_value
from app.services import master_contacts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scraper", tags=["lead-scraper"])


# -- Request / Response Models ------------------------------------------------

class ScrapeSearchRequest(BaseModel):
    # Either a query or an industry (or both). The industry maps to search
    # terms via lead_routing.INDUSTRY_TERMS.
    query: Optional[str] = Field(None, min_length=2, max_length=200,
                                 description='Full text search, e.g. "funeral homes near Dallas TX"')
    industry: Optional[str] = Field(None, max_length=80)
    # INTENT ONLY - stored on the job, never routed to automatically.
    destination_org_id: Optional[str] = None
    location: Optional[str] = Field(None, max_length=200,
                                    description="Optional location bias, e.g. 'Dallas, TX'")
    radius_meters: int = Field(default=8000, ge=500, le=50000)
    max_results: int = Field(default=20, ge=1, le=60)

    @model_validator(mode="after")
    def _needs_query_or_industry(self):
        if not (self.query and self.query.strip()) and not (self.industry and self.industry.strip()):
            raise ValueError("Provide a search query or an industry.")
        return self


class ScrapedBusiness(BaseModel):
    place_id: str
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[float] = None
    reviews_count: Optional[int] = None
    phone_type: Optional[str] = None   # "mobile" | "landline" | "voip" | "unknown"
    channel: Optional[str] = None      # "sms" | "email" | "voice"


class ValidateRequest(BaseModel):
    phones: List[str] = Field(..., max_items=60)


class ValidatedPhone(BaseModel):
    phone: str
    phone_type: str
    channel: str


class ExistsRequest(BaseModel):
    phones: List[str] = Field(..., max_items=200)
    # Dedup must be checked against the SAME org the leads will import into,
    # otherwise the duplicate check is meaningless.
    target_org_id: Optional[str] = None
    organization_id: Optional[str] = None  # accepted synonym


class ImportRequest(BaseModel):
    leads: List[ScrapedBusiness]
    list_name: Optional[str] = None
    # Which organization these scraped leads belong to. God mode scrapes on
    # behalf of a client org, so the target must be explicit. Falls back to the
    # org selected via the X-Org-Override switcher; never silently defaults to
    # the god platform org.
    target_org_id: Optional[str] = None
    organization_id: Optional[str] = None  # accepted synonym
    job_id: Optional[str] = None


class StageRequest(BaseModel):
    """Results into the Lead Intelligence prospect pool - no organization is
    written to. `destination_org_id` is recorded as intent only."""
    results: List[ScrapedBusiness] = Field(..., max_length=60)
    job_id: Optional[str] = None
    industry: Optional[str] = None
    destination_org_id: Optional[str] = None


# -- Helpers ------------------------------------------------------------------

async def _google_places_search(
    query: str, location: Optional[str], radius_m: int, max_results: int, api_key: str
) -> List[dict]:
    full_query = f"{query} near {location}" if location else query
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": full_query, "radius": radius_m, "key": api_key}
    results = []
    next_page_token = None

    async with httpx.AsyncClient(timeout=15) as client:
        while len(results) < max_results:
            if next_page_token:
                import asyncio
                await asyncio.sleep(2)
                params = {"pagetoken": next_page_token, "key": api_key}
            resp = await client.get(url, params=params)
            data = resp.json()
            status = data.get("status")
            if status == "ZERO_RESULTS":
                break
            if status != "OK":
                logger.warning("Google Places error: %s — %s", status, data.get("error_message", ""))
                break
            for place in data.get("results", []):
                results.append(place)
                if len(results) >= max_results:
                    break
            next_page_token = data.get("next_page_token")
            if not next_page_token:
                break
    return results


async def _google_place_details(place_id: str, api_key: str) -> dict:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "fields": "formatted_phone_number,website,user_ratings_total",
        "key": api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            return resp.json().get("result", {})
    except Exception as exc:
        logger.warning("Place Details error for %s: %s", place_id, exc)
        return {}


def _twilio_lookup(phone: str, account_sid: str, auth_token: str) -> str:
    import requests as _req
    url = f"https://lookups.twilio.com/v2/PhoneNumbers/{phone}?Fields=line_type_intelligence"
    try:
        r = _req.get(url, auth=(account_sid, auth_token), timeout=8)
        if r.status_code == 200:
            lti = r.json().get("line_type_intelligence") or {}
            t = (lti.get("type") or "unknown").lower()
            if "mobile" in t or "cellular" in t:
                return "mobile"
            if "landline" in t or "fixed" in t:
                return "landline"
            if "voip" in t:
                return "voip"
    except Exception as exc:
        logger.warning("Twilio Lookup error for %s: %s", phone, exc)
    return "unknown"


def _channel_for_type(phone_type: str, has_website: bool) -> str:
    if phone_type == "mobile":
        return "sms"
    if has_website:
        return "email"
    return "voice"


def _get_twilio_creds(current_user: User, db: Session) -> tuple:
    """The caller's OWN Twilio credentials, or a refusal. Never anyone else's.

    Two bugs lived here, and the second hid the first.

    THE ORG BRANCH WAS DEAD CODE. It read `org.twilio_account_sid` and
    `org.twilio_auth_token_encrypted`. Those attributes do not exist on
    Organization - the columns are `org_twilio_account_sid` and
    `org_twilio_auth_token_encrypted`. `getattr(..., None)` swallowed the
    mistake silently, so the branch never once returned an organization's
    credentials.

    THE FALLBACK THEN SENT AS SOMEBODY ELSE. Because the org branch never
    matched, every organization fell through to the platform's own
    TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN. One shared account, used on behalf
    of any tenant that asked - a lookup billed to the platform, from a number
    that belongs to no one customer, in a codebase whose entire premise is that
    tenants do not share credentials. Nothing failed, so nothing was noticed.

    Both are fixed together, because fixing only the names would have left the
    fallback as the quiet default for every org that has not configured Twilio.
    An unconfigured organization now gets a refusal naming what to do, which is
    the only honest answer: there is no correct account to use.
    """
    if current_user.twilio_account_sid and getattr(current_user, "twilio_auth_token_encrypted", None):
        return (current_user.twilio_account_sid, decrypt_value(current_user.twilio_auth_token_encrypted))

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    sid = getattr(org, "org_twilio_account_sid", None) if org else None
    tok = getattr(org, "org_twilio_auth_token_encrypted", None) if org else None
    if sid and tok:
        return (sid, decrypt_value(tok))

    raise HTTPException(
        status_code=409,
        detail=(
            "No Twilio credentials are configured for this organization, and this "
            "will not fall back to the platform's shared account — a lookup billed "
            "to the platform from a number belonging to no customer is not a "
            "sensible default. Add org-level Twilio under the customer's "
            "Communications settings, or set them on your own user."
        ),
    )


# -- Endpoints ----------------------------------------------------------------

GOD_PLATFORM_ORG_ID = "org-god-platform"


def _resolve_target_org(requested_org_id, current_user: User, db: Session) -> str:
    """Work out which organization scraped leads belong to.

    God mode scrapes on behalf of a client org. Getting this wrong means leads
    silently land in the god platform org and look like they vanished, so this
    refuses to guess: either the request names an org, or the god admin has one
    selected in the org switcher (X-Org-Override).
    """
    org_id = requested_org_id or current_user.organization_id

    if getattr(current_user, "_god_all_orgs", False) and not requested_org_id:
        raise HTTPException(
            status_code=400,
            detail="No organization selected. Pick a client org in the switcher, "
                   "or pass organization_id, before importing scraped leads.",
        )

    if not org_id or org_id == GOD_PLATFORM_ORG_ID:
        raise HTTPException(
            status_code=400,
            detail="Scraped leads must be imported into a client organization, "
                   "not the god platform org. Pass organization_id.",
        )

    if not db.query(Organization).filter(Organization.id == org_id).first():
        raise HTTPException(status_code=404, detail="Organization %s not found." % org_id)

    return org_id


def _check_destination(dest: Optional[str], db: Session) -> Optional[str]:
    """An intended destination must be a real client org. Validated, never acted on."""
    if not dest:
        return None
    if dest == GOD_PLATFORM_ORG_ID:
        raise HTTPException(status_code=400,
                            detail="The destination must be a client organization.")
    if not db.query(Organization.id).filter(Organization.id == dest).first():
        raise HTTPException(status_code=404, detail="Organization %s not found." % dest)
    return dest


def _record_job(db: Session, current_user: User, req: "ScrapeSearchRequest",
                effective_query: str, dest: Optional[str], result_count: int) -> Optional[str]:
    """Record the search. A failure here (e.g. an unmigrated table) must not
    cost the operator their results, so it is logged and swallowed."""
    try:
        from app.models.lead_intel_models import LeadIntelScrapeJob
        job = LeadIntelScrapeJob(created_by_id=current_user.id, provider="google_places",
                                 industry=req.industry or None, query=req.query or None,
                                 effective_query=effective_query, location=req.location,
                                 radius_meters=req.radius_meters, max_results=req.max_results,
                                 destination_org_id=dest, status="searched",
                                 result_count=result_count)
        db.add(job)
        db.commit()
        return job.id
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.warning("scraper: could not record the search job", exc_info=True)
        return None


@router.post("/search")
async def scrape_search(
    req: ScrapeSearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """Search Google Places for businesses matching the query/industry + optional location."""
    from app.services.lead_routing import industry_query
    dest = _check_destination(req.destination_org_id, db)
    api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Google Places API not configured. Add GOOGLE_PLACES_API_KEY to Render env vars.",
        )

    effective = industry_query(req.industry, req.query)
    places = await _google_places_search(
        effective, req.location, req.radius_meters, req.max_results, api_key
    )

    businesses = []
    for place in places:
        place_id = place.get("place_id", "")
        name = place.get("name", "Unknown")
        address = place.get("formatted_address") or place.get("vicinity") or ""
        rating = place.get("rating")
        reviews_count = place.get("user_ratings_total")

        details = {}
        if place_id:
            details = await _google_place_details(place_id, api_key)

        businesses.append({
            "place_id": place_id,
            "name": name,
            "phone": details.get("formatted_phone_number"),
            "address": address,
            "website": details.get("website"),
            "rating": rating,
            "reviews_count": details.get("user_ratings_total") or reviews_count,
            "phone_type": None,
            "channel": None,
        })

    full_query = f"{effective} near {req.location}" if req.location else effective
    job_id = _record_job(db, current_user, req, full_query, dest, len(businesses))
    return {"results": businesses, "total": len(businesses), "query": full_query,
            "industry": req.industry, "destination_org_id": dest, "job_id": job_id}


@router.post("/stage", status_code=201)
def scrape_stage(
    req: StageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """Stage results into the Lead Intelligence prospect pool.

    normalize -> dedupe -> suppression -> qualification, via
    app/services/lead_routing.py. Writes ONLY lead_intel_* tables: no Lead, no
    contact, no consent, no message. Routing into an organization is a separate
    explicit action (POST /god/lead-intelligence/route).
    """
    from app.models.lead_intel_models import LeadIntelScrapeJob
    from app.services import lead_routing
    dest = _check_destination(req.destination_org_id, db)
    job = None
    if req.job_id:
        job = db.query(LeadIntelScrapeJob).filter(LeadIntelScrapeJob.id == req.job_id).first()
        if job is None:
            raise HTTPException(status_code=404, detail="Scrape job not found.")
    out = lead_routing.ingest(db, req.results, job=job, industry=req.industry,
                              destination_org_id=dest)
    db.commit()
    names = {}
    if dest or (job and job.destination_org_id):
        oid = dest or job.destination_org_id
        o = db.query(Organization).filter(Organization.id == oid).first()
        names = {oid: o.name} if o else {}
    return {"job_id": job.id if job else None, "counts": out["counts"],
            "prospects": [lead_routing.prospect_dict(p, names) for p in out["prospects"]],
            "note": "Staged in the Lead Intelligence pool. Nothing was sent to any organization."}


@router.post("/validate")
def scrape_validate(
    req: ValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """Run Twilio Lookup v2 on a list of phone numbers."""
    account_sid, auth_token = _get_twilio_creds(current_user, db)
    if not account_sid or not auth_token:
        raise HTTPException(
            status_code=503,
            detail="Twilio not configured — cannot validate phone types.",
        )

    results = []
    for phone in req.phones:
        phone_type = _twilio_lookup(phone, account_sid, auth_token) if phone else "unknown"
        channel = _channel_for_type(phone_type, False)
        results.append({"phone": phone, "phone_type": phone_type, "channel": channel})

    return {"results": results}


@router.post("/exists")
def scrape_exists(
    req: ExistsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """Return the set of phone numbers that already exist as leads in this org."""
    org_id = _resolve_target_org(req.target_org_id or req.organization_id, current_user, db)
    phones = [p for p in req.phones if p]
    if not phones:
        return {"existing_phones": []}

    existing = (
        db.query(Lead.phone)
        .filter(Lead.organization_id == org_id, Lead.phone.in_(phones))
        .all()
    )
    return {"existing_phones": [row.phone for row in existing]}


@router.post("/import", status_code=201)
def scrape_import(
    req: ImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_god),
):
    """RETIRED (#470 Stage D). Kept so old clients get a clear answer instead of a 404."""
    raise HTTPException(status_code=410, detail='Direct scraper import is retired. Stage results with POST /scraper/stage (Lead Intelligence), then route them; nothing is written to an organization from here.')
