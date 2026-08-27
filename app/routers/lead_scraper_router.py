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
"""

import logging
import os
import uuid
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_god
from app.models.models import Lead, Organization, User
from app.utils.crypto import decrypt_value

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scraper", tags=["lead-scraper"])


# -- Request / Response Models ------------------------------------------------

class ScrapeSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=200,
                       description='Full text search, e.g. "funeral homes near Dallas TX"')
    location: Optional[str] = Field(None, max_length=200,
                                    description="Optional location bias, e.g. 'Dallas, TX'")
    radius_meters: int = Field(default=8000, ge=500, le=50000)
    max_results: int = Field(default=20, ge=1, le=60)


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


@router.post("/search")
async def scrape_search(
    req: ScrapeSearchRequest,
    current_user: User = Depends(require_god),
):
    """Search Google Places for businesses matching the query + optional location."""
    api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Google Places API not configured. Add GOOGLE_PLACES_API_KEY to Render env vars.",
        )

    places = await _google_places_search(
        req.query.strip(), req.location, req.radius_meters, req.max_results, api_key
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

    full_query = f"{req.query.strip()} near {req.location}" if req.location else req.query.strip()
    return {"results": businesses, "total": len(businesses), "query": full_query}


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
    """Import scraped businesses as leads. Deduplicates by phone within the org."""
    org_id = _resolve_target_org(req.target_org_id or req.organization_id, current_user, db)
    list_name = req.list_name or "Lead Scraper Import"
    imported = 0
    skipped = 0

    for biz in req.leads:
        if biz.get("phone") if isinstance(biz, dict) else biz.phone:
            phone = biz["phone"] if isinstance(biz, dict) else biz.phone
            existing = db.query(Lead).filter(
                Lead.organization_id == org_id,
                Lead.phone == phone,
            ).first()
            if existing:
                skipped += 1
                continue
        else:
            phone = None

        name = biz["name"] if isinstance(biz, dict) else biz.name
        address = biz.get("address") if isinstance(biz, dict) else biz.address
        website = biz.get("website") if isinstance(biz, dict) else biz.website
        phone_type = biz.get("phone_type") if isinstance(biz, dict) else biz.phone_type
        channel = biz.get("channel") if isinstance(biz, dict) else biz.channel
        rating = biz.get("rating") if isinstance(biz, dict) else biz.rating
        reviews = biz.get("reviews_count") if isinstance(biz, dict) else getattr(biz, "reviews_count", None)

        name_parts = (name or "Unknown Business").split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        effective_channel = channel or ("sms" if phone_type == "mobile" else "voice")
        source_map = {"sms": "google_places_sms", "email": "google_places_email", "voice": "google_places_voice"}
        source = source_map.get(effective_channel, "google_places")

        notes_parts = [f"Business: {name}"]
        if address:
            notes_parts.append(f"Address: {address}")
        if website:
            notes_parts.append(f"Website: {website}")
        if rating:
            notes_parts.append(f"Rating: {rating}/5" + (f" ({reviews} reviews)" if reviews else ""))
        if phone_type:
            notes_parts.append(f"Phone type: {phone_type}")

        lead = Lead(
            id=str(uuid.uuid4()),
            organization_id=org_id,
            user_id=current_user.id,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=None,
            source=source,
            status="new",
            tier="new_inquiry",
            message_track="new_inquiry_intro",
            import_list_name=list_name,
            notes="\n".join(notes_parts),
        )
        db.add(lead)
        imported += 1

    db.commit()
    logger.info(
        "scraper_import: org=%s list='%s' imported=%d skipped=%d",
        org_id, list_name, imported, skipped,
    )
    return {"success": True, "imported": imported, "skipped": skipped, "list_name": list_name}
